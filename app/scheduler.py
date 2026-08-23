import os
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .database import SessionLocal
from .models import Task, TaskGroupNotice, InventoryItem, Appointment, Group, RoomGroupThrottle
from .status import task_status, compute_inventory_status
from .notifications import notify_group, notify_groups, notify_user
from . import ntptime
from . import backup
from . import notification_batching

logger = logging.getLogger("reinigungsplan.scheduler")

APP_TIMEZONE = ZoneInfo(os.environ.get("APP_TIMEZONE", "Europe/Berlin"))

# Uhrzeiten (lokal, kommagetrennt) für automatische Sicherungen sowie wie
# viele Tage diese aufbewahrt werden, bevor sie automatisch gelöscht werden.
BACKUP_SCHEDULE_HOURS = os.environ.get("BACKUP_SCHEDULE_HOURS", "0,6,12,18")
BACKUP_RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", "3"))

# Drossel-Intervall je (Bereich, Gruppe) für die gebündelte "Überfällig"-
# Sammel-Push (siehe RoomGroupThrottle) - verhindert wiederholte Sammel-
# Pushes für dieselbe Bereich-Gruppen-Kombination, solange dort weiterhin
# (dieselben oder neue) Aufgaben überfällig sind. Pro Gruppe statt nur pro
# Bereich, damit eine Gruppe mit später öffnendem Arbeitszeit-Fenster nicht
# leer ausgeht, nur weil eine andere Gruppe desselben Bereichs bereits
# informiert wurde.
OVERDUE_BATCH_THROTTLE_HOURS = float(os.environ.get("OVERDUE_BATCH_THROTTLE_HOURS", "4"))

# Statuswechsel, bei denen wir aktiv informieren (green -> gelb/rot ist neu, rot bleibt still nach erster Meldung)
NOTIFY_ON = {"yellow", "red"}


def _aware(ts):
    """SQLite gibt Zeitstempel manchmal ohne tzinfo zurück, obwohl sie in UTC
    gespeichert wurden - hier konsistent nachrüsten, bevor mit ihnen gerechnet wird."""
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _within_working_hours(group, now_local) -> bool:
    """True, wenn die Gruppe keine Arbeitszeit gesetzt hat oder 'jetzt' innerhalb
    davon liegt. Unterstützt nur Fenster innerhalb desselben Tages (kein Wrap
    über Mitternacht, z.B. 22-6 Uhr)."""
    if group.work_start_hour is None or group.work_end_hour is None:
        return True
    return group.work_start_hour <= now_local.hour < group.work_end_hour


def check_tasks_job():
    """"Bald fällig" (gelb) bleibt eine sofortige Einzel-Push je Aufgabe/
    Gruppe beim Statuswechsel (wie bisher) - "Überfällig" (rot) dagegen wird
    NICHT mehr direkt aus dieser Schleife heraus verschickt, sondern nur
    gesammelt (siehe overdue_by_room unten). Grund: bei mehreren gleichzeitig
    überfälligen Aufgaben in einem Bereich (oder mehreren Aufgaben, die
    nacheinander über die Zeit überfällig werden) würde sonst pro Aufgabe und
    Gruppe ein eigener Push rausgehen und den Kanal überfluten - hier deshalb
    stattdessen genau eine gebündelte Sammel-Push je Bereich und Gruppe
    (siehe unten, gedrosselt über RoomGroupThrottle)."""
    db = SessionLocal()
    try:
        now = ntptime.now_utc()
        now_local = now.astimezone(APP_TIMEZONE)
        tasks = db.query(Task).all()

        # room_id -> {"room": Room, "task_names": [...], "group_ids": {...}}
        overdue_by_room = {}

        for task in tasks:
            result = task_status(task)
            new_status = result["status"]
            task_groups = task.groups or task.room.groups

            # Explizit zugewiesene Gruppen benachrichtigen, falls gesetzt -
            # sonst wie bisher alle Gruppen des Bereichs (Rückwärtskompatibel
            # für Aufgaben ohne eigene Gruppen-Zuordnung).
            for group in task_groups:
                notice = (
                    db.query(TaskGroupNotice)
                    .filter_by(task_id=task.id, group_id=group.id)
                    .first()
                )
                if notice is None:
                    notice = TaskGroupNotice(task_id=task.id, group_id=group.id, last_status="green")
                    db.add(notice)

                if new_status == notice.last_status:
                    continue

                if new_status not in NOTIFY_ON:
                    # z.B. zurück auf grün - nur Buchführung nachziehen, nicht benachrichtigen
                    notice.last_status = new_status
                    continue

                if not _within_working_hours(group, now_local):
                    # Außerhalb der Arbeitszeit: nichts ändern, wird beim nächsten
                    # Tick nachgeholt sobald die Arbeitszeit beginnt
                    continue

                if new_status == "yellow":
                    title = f"Bald fällig: {task.room.name}"
                    msg = f"„{task.name}“ wird demnächst fällig."
                    notify_group(group, title, msg, url=f"/room/{task.room_id}?focus=task-{task.id}")
                notice.last_status = new_status

            if new_status == "red":
                entry = overdue_by_room.setdefault(
                    task.room_id, {"room": task.room, "task_names": [], "group_ids": set()}
                )
                entry["task_names"].append(task.name)
                entry["group_ids"].update(g.id for g in task_groups)

        db.commit()

        # Gebündelte "Überfällig"-Sammel-Push: pro Bereich mit aktuell
        # überfälligen Aufgaben höchstens eine Meldung je Gruppe, gedrosselt
        # auf OVERDUE_BATCH_THROTTLE_HOURS je (Bereich, Gruppe) statt bei
        # jedem 15-Minuten-Tick erneut zu feuern, solange die Überfälligkeit
        # anhält.
        for room_id, entry in overdue_by_room.items():
            room = entry["room"]
            all_groups = db.query(Group).filter(Group.id.in_(entry["group_ids"])).all()
            if not all_groups:
                continue
            # Nur Gruppen betrachten, die JETZT innerhalb ihrer EIGENEN
            # Arbeitszeit sind - bei mehreren Gruppen am selben Bereich (z.B.
            # Hausmeister mit festem Fenster + Toilettenbetreuung ganz ohne
            # Fenster) darf eine Gruppe ohne Einschraenkung nicht dazu fuehren,
            # dass eine ANDERE Gruppe ausserhalb ihres eigenen Fensters
            # trotzdem benachrichtigt wird.
            groups = [g for g in all_groups if _within_working_hours(g, now_local)]
            if not groups:
                continue  # wird beim nächsten Tick nachgeholt, sobald eine Arbeitszeit beginnt

            # Von den (aktuell im Dienst befindlichen) Gruppen wiederum nur
            # die, deren eigenes Drossel-Fenster fuer DIESEN Bereich bereits
            # abgelaufen ist - pro (Bereich, Gruppe) statt nur pro Bereich,
            # damit eine spaeter startende Schicht nicht leer ausgeht, nur
            # weil eine frueher startende Gruppe desselben Bereichs kuerzlich
            # schon informiert wurde.
            throttles = {
                t.group_id: t
                for t in db.query(RoomGroupThrottle).filter(
                    RoomGroupThrottle.room_id == room_id,
                    RoomGroupThrottle.group_id.in_([g.id for g in groups]),
                ).all()
            }
            due_groups = []
            for group in groups:
                throttle = throttles.get(group.id)
                last_notified = _aware(throttle.notified_at) if throttle else None
                if last_notified is not None and (now - last_notified).total_seconds() < OVERDUE_BATCH_THROTTLE_HOURS * 3600:
                    continue
                due_groups.append((group, throttle))
            if not due_groups:
                continue

            count = len(entry["task_names"])
            task_word = "Aufgabe" if count == 1 else "Aufgaben"
            verb = "ist" if count == 1 else "sind"
            examples = ", ".join(entry["task_names"][:3])
            if count > 3:
                examples += ", ..."
            title = f"Überfällig: {room.name}"
            msg = f"{count} {task_word} {verb} überfällig (z.B. {examples})."
            notify_groups([g for g, _ in due_groups], title, msg, url=f"/room/{room_id}")
            for group, throttle in due_groups:
                if throttle:
                    throttle.notified_at = now
                else:
                    db.add(RoomGroupThrottle(room_id=room_id, group_id=group.id, notified_at=now))

        db.commit()
    except Exception:
        logger.exception("Fehler beim Prüfen der Aufgaben")
        db.rollback()
    finally:
        db.close()


def _format_names_de(names) -> str:
    """'Sebastian' / 'Sebastian und Anna' / 'Sebastian, Anna und Max' -
    deutsche Aufzaehlung fuer die gesammelte Erledigt-Benachrichtigung."""
    names = sorted(names)
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} und {names[-1]}"


def check_completion_batches_job():
    """Verschickt die in notification_batching gesammelten Erledigt-
    Ereignisse: pro Bereich, dessen Sammel-Zeitfenster abgelaufen ist,
    genau eine zusammengefasste Push-Nachricht statt einer je Aufgabe (siehe
    queue_task_completion in main.py, wo jede Erledigung eingereiht wird,
    statt sofort zu benachrichtigen).

    Opt-in statt Opt-out (siehe User.notify_on_completion in models.py):
    standardmäßig aus, jeder aktiviert es sich bei Bedarf selbst im eigenen
    Profil. Geht deshalb bewusst NICHT über notify_group/notify_groups (die
    würden zusätzlich auch die geteilten Gruppen-Kanäle ntfy/Gotify/Signal
    bedienen, die keinen Opt-in je Empfänger kennen), sondern nur als
    persönlicher Web-Push an einzelne, explizit angemeldete Nutzer.

    Respektiert außerdem die Arbeitszeit der jeweiligen Gruppe (wie die
    anderen Job-Typen) - eine Gruppe, die gerade nicht im Dienst ist, wird
    für diesen Batch übersprungen (kein Nachholen für sie, anders als bei
    Überfällig/Bald fällig: ein einzelnes "wurde erledigt" ist rein
    informativ und nach Schichtende nicht mehr sinnvoll nachzureichen)."""
    db = SessionLocal()
    try:
        now = ntptime.now_utc()
        now_local = now.astimezone(APP_TIMEZONE)
        for room_id, entry in notification_batching.pop_due_batches(now):
            groups = db.query(Group).filter(Group.id.in_(entry["group_ids"])).all()
            if not groups:
                continue
            count = entry["count"]
            task_word = "Aufgabe" if count == 1 else "Aufgaben"
            verb = "wurde" if count == 1 else "wurden"
            who = _format_names_de(entry["user_names"])
            title = f"Erledigt: {entry['room_name']}"
            msg = f"{count} {task_word} {verb} von {who} erledigt."
            url = f"/room/{room_id}"

            seen_user_ids = set()
            for group in groups:
                if not _within_working_hours(group, now_local):
                    continue
                for member in group.users:
                    if member.id in seen_user_ids or not member.notify_on_completion:
                        continue
                    seen_user_ids.add(member.id)
                    notify_user(member, title, msg, url=url)
    except Exception:
        logger.exception("Fehler beim Verschicken gebündelter Erledigt-Benachrichtigungen")
        db.rollback()
    finally:
        db.close()


def check_inventory_job():
    db = SessionLocal()
    try:
        now_local = ntptime.now_utc().astimezone(APP_TIMEZONE)
        items = db.query(InventoryItem).filter(InventoryItem.group_id.isnot(None)).all()
        for item in items:
            if item.notified:
                continue
            # Vereinfachtes 2-Stufen-System (siehe status.compute_inventory_status):
            # "low" ist bereits die einzige "braucht Aufmerksamkeit"-Stufe -
            # die Schwelle dafuer ist der Mindestbestand, nicht der Soll-Bestand.
            item_status = compute_inventory_status(item)
            if item_status["status"] != "low":
                continue
            if not _within_working_hours(item.group, now_local):
                continue  # wird beim nächsten Tick nachgeholt, sobald Arbeitszeit beginnt
            notify_group(
                item.group,
                f"Niedriger Bestand: {item.name}",
                f"„{item.name}“ liegt bei {item.stock_current:g}"
                f"{' ' + item.unit if item.unit else ''} – Mindestbestand ist {item_status['critical_threshold']:g}.",
                url=f"/inventory?focus=item-{item.id}",
            )
            item.notified = True
        db.commit()
    except Exception:
        logger.exception("Fehler beim Prüfen des Inventars")
        db.rollback()
    finally:
        db.close()


def check_appointments_job():
    """Erinnert vorab an anstehende Termine (z.B. Mülltonnen-Abholung) und
    lässt wiederkehrende Termine nach Ablauf automatisch auf den nächsten
    zukünftigen Termin weiterspringen (kein Kalender, nur eine einfache,
    sich selbst fortschreibende Liste)."""
    db = SessionLocal()
    try:
        now_local = ntptime.now_utc().astimezone(APP_TIMEZONE)
        today = now_local.date()
        for appt in db.query(Appointment).all():
            date_local = _aware(appt.date).astimezone(APP_TIMEZONE).date()
            days_until = (date_local - today).days

            if days_until < 0:
                # Termin liegt in der Vergangenheit - bei Wiederholung auf den
                # nächsten zukünftigen Termin weiterspringen, sonst unangetastet
                # als vergangener Termin stehen lassen.
                if appt.recurrence_days:
                    next_date = date_local
                    while (next_date - today).days < 0:
                        next_date += timedelta(days=appt.recurrence_days)
                    appt.date = datetime(
                        next_date.year, next_date.month, next_date.day, tzinfo=APP_TIMEZONE
                    ).astimezone(timezone.utc)
                    appt.notified = False
                continue

            if appt.notified or days_until > (appt.notify_days_before or 0):
                continue
            # Arbeitszeit-Fenster nur bei genau einer Zielgruppe pruefbar - bei
            # mehreren Gruppen oder "Alle (Betriebsweit)" koennten sich die
            # Fenster widersprechen, dort wird sofort benachrichtigt statt die
            # Logik pro Gruppe aufzusplitten (Termin bleibt eine einzelne,
            # gemeinsame Erinnerung).
            if len(appt.groups) == 1 and not appt.is_company_wide and not _within_working_hours(appt.groups[0], now_local):
                continue  # wird beim nächsten Tick nachgeholt, sobald Arbeitszeit beginnt

            if days_until == 0:
                when = "heute"
            elif days_until == 1:
                when = "morgen"
            else:
                when = f"in {days_until} Tagen"
            title = f"Termin: {appt.name}"
            msg = f"{when} ({date_local.strftime('%d.%m.%Y')})"
            if appt.is_company_wide:
                all_groups = db.query(Group).all()
                if all_groups:
                    notify_groups(all_groups, title, msg, url="/appointments")
            elif appt.groups:
                notify_groups(appt.groups, title, msg, url="/appointments")
            else:
                notify_user(appt.user, title, msg, url="/appointments")
            appt.notified = True

        db.commit()
    except Exception:
        logger.exception("Fehler beim Prüfen der Termine")
        db.rollback()
    finally:
        db.close()


def scheduled_backup_job():
    try:
        backup.create_scheduled_backup(BACKUP_RETENTION_DAYS)
    except Exception:
        logger.exception("Fehler beim automatischen Backup")


def start_scheduler():
    scheduler = BackgroundScheduler()
    # alle 15 Minuten prüfen; ausreichend granular für Intervalle ab 1h aufwärts
    scheduler.add_job(check_tasks_job, "interval", minutes=15, id="check_tasks")
    # Deutlich granularer als die anderen Jobs: das Batching-Zeitfenster
    # (siehe notification_batching.BATCH_WINDOW_SECONDS) ist auf Sekunden,
    # nicht Minuten ausgelegt - sonst wuerde die gesammelte Push-Nachricht
    # erst mit der naechsten 15-Minuten-Flanke rausgehen.
    scheduler.add_job(check_completion_batches_job, "interval", seconds=15, id="check_completion_batches")
    scheduler.add_job(check_inventory_job, "interval", minutes=15, id="check_inventory")
    scheduler.add_job(check_appointments_job, "interval", minutes=15, id="check_appointments")
    # NTP-Offset regelmäßig auffrischen (Erstsync passiert synchron beim App-Start)
    scheduler.add_job(ntptime.sync, "interval", minutes=30, id="ntp_sync")
    # Automatische Sicherungen zu festen lokalen Uhrzeiten, mit Rotation
    scheduler.add_job(
        scheduled_backup_job,
        CronTrigger(hour=BACKUP_SCHEDULE_HOURS, minute=0, timezone=APP_TIMEZONE),
        id="scheduled_backup",
    )
    scheduler.start()
    return scheduler
