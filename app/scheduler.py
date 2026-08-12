import os
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .database import SessionLocal
from .models import Task, TaskGroupNotice, InventoryItem, Appointment
from .status import task_status, compute_inventory_status
from .notifications import notify_group, notify_user
from . import ntptime
from . import backup

logger = logging.getLogger("reinigungsplan.scheduler")

APP_TIMEZONE = ZoneInfo(os.environ.get("APP_TIMEZONE", "Europe/Berlin"))

# Uhrzeiten (lokal, kommagetrennt) für automatische Sicherungen sowie wie
# viele Tage diese aufbewahrt werden, bevor sie automatisch gelöscht werden.
BACKUP_SCHEDULE_HOURS = os.environ.get("BACKUP_SCHEDULE_HOURS", "0,6,12,18")
BACKUP_RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", "3"))

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
    db = SessionLocal()
    try:
        now_local = ntptime.now_utc().astimezone(APP_TIMEZONE)
        tasks = db.query(Task).all()
        for task in tasks:
            result = task_status(task)
            new_status = result["status"]

            # Explizit zugewiesene Gruppen benachrichtigen, falls gesetzt -
            # sonst wie bisher alle Gruppen des Bereichs (Rückwärtskompatibel
            # für Aufgaben ohne eigene Gruppen-Zuordnung).
            for group in (task.groups or task.room.groups):
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
                else:
                    title = f"Überfällig: {task.room.name}"
                    msg = f"„{task.name}“ ist überfällig und sollte erledigt werden."
                notify_group(group, title, msg, url=f"/room/{task.room_id}?focus=task-{task.id}")
                notice.last_status = new_status

        db.commit()
    except Exception:
        logger.exception("Fehler beim Prüfen der Aufgaben")
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
            # Nur ab "critical"/"empty" benachrichtigen, nicht schon bei "low"
            # (unter Soll-Bestand, aber noch über dem Mindestbestand) - sonst
            # nervt es bei jedem kleinen Abgang unterhalb des Zielwerts.
            if compute_inventory_status(item)["status"] not in ("critical", "empty"):
                continue
            if not _within_working_hours(item.group, now_local):
                continue  # wird beim nächsten Tick nachgeholt, sobald Arbeitszeit beginnt
            notify_group(
                item.group,
                f"Bestand kritisch: {item.name}",
                f"„{item.name}“ liegt bei {item.stock_current:g}"
                f"{' ' + item.unit if item.unit else ''} – Soll-Bestand ist {item.stock_min:g}.",
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
            if appt.group and not _within_working_hours(appt.group, now_local):
                continue  # wird beim nächsten Tick nachgeholt, sobald Arbeitszeit beginnt

            if days_until == 0:
                when = "heute"
            elif days_until == 1:
                when = "morgen"
            else:
                when = f"in {days_until} Tagen"
            title = f"Termin: {appt.name}"
            msg = f"{when} ({date_local.strftime('%d.%m.%Y')})"
            if appt.group:
                notify_group(appt.group, title, msg, url="/appointments")
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
