import os
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from .database import SessionLocal
from .models import Task, TaskGroupNotice, InventoryItem
from .status import task_status
from .notifications import notify_group
from . import ntptime

logger = logging.getLogger("reinigungsplan.scheduler")

APP_TIMEZONE = ZoneInfo(os.environ.get("APP_TIMEZONE", "Europe/Berlin"))

# Statuswechsel, bei denen wir aktiv informieren (green -> gelb/rot ist neu, rot bleibt still nach erster Meldung)
NOTIFY_ON = {"yellow", "red"}


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

            for group in task.room.groups:
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
                notify_group(group, title, msg)
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
            if item.stock_current >= item.stock_min or item.notified:
                continue
            if not _within_working_hours(item.group, now_local):
                continue  # wird beim nächsten Tick nachgeholt, sobald Arbeitszeit beginnt
            notify_group(
                item.group,
                f"Bestand niedrig: {item.name}",
                f"„{item.name}“ liegt bei {item.stock_current:g}"
                f"{' ' + item.unit if item.unit else ''} – Mindestbestand ist {item.stock_min:g}.",
            )
            item.notified = True
        db.commit()
    except Exception:
        logger.exception("Fehler beim Prüfen des Inventars")
        db.rollback()
    finally:
        db.close()


def start_scheduler():
    scheduler = BackgroundScheduler()
    # alle 15 Minuten prüfen; ausreichend granular für Intervalle ab 1h aufwärts
    scheduler.add_job(check_tasks_job, "interval", minutes=15, id="check_tasks")
    scheduler.add_job(check_inventory_job, "interval", minutes=15, id="check_inventory")
    # NTP-Offset regelmäßig auffrischen (Erstsync passiert synchron beim App-Start)
    scheduler.add_job(ntptime.sync, "interval", minutes=30, id="ntp_sync")
    scheduler.start()
    return scheduler
