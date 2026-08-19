import os
from datetime import timezone, timedelta, datetime
from zoneinfo import ZoneInfo

from . import ntptime

# Eigene Zeitzonen-Instanz statt Import aus scheduler.py, da scheduler.py
# umgekehrt task_status() importiert (Zirkelimport).
_APP_TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "Europe/Berlin"))


def _parse_active_weekdays(raw):
    """CSV aus Wochentag-Zahlen (Montag=0 ... Sonntag=6), z.B. '0,1,2,3,4'
    für Mo-Fr. None/leer = keine Einschränkung, wie bisher jeder Tag aktiv."""
    if not raw:
        return None
    return {int(d) for d in raw.split(",") if d.strip() != ""}


def _advance_due_at(last, interval_hours, active_weekdays):
    """Nächste Fälligkeit ab der letzten Erledigung. Ohne Einschränkung wie
    bisher einfach +interval_hours. Mit aktiven Wochentagen wird das Intervall
    in Tage umgerechnet und dabei nur über aktive Tage weitergezählt, damit
    z.B. eine Mo-Fr-Aufgabe nicht durchs Wochenende hindurch fällig wird,
    sondern erst wieder am nächsten aktiven Tag zur selben Uhrzeit. Wochentage
    werden in der lokalen Geschäftszeitzone bestimmt, nicht in UTC."""
    if active_weekdays is None:
        return last + timedelta(hours=interval_hours)
    last_local = last.astimezone(_APP_TZ)
    interval_days = max(1, round(interval_hours / 24))
    cursor_date = last_local.date()
    remaining = interval_days
    while remaining > 0:
        cursor_date += timedelta(days=1)
        if cursor_date.weekday() in active_weekdays:
            remaining -= 1
    due_local = datetime.combine(cursor_date, last_local.timetz())
    return due_local.astimezone(timezone.utc)


def task_status(task, now=None):
    """
    Berechnet Status ('green' | 'yellow' | 'red') sowie die relevanten
    Zeitpunkte für eine Aufgabe, basierend auf rollierendem Intervall.
    interval_hours == 0 ist der Sonderwert "Nach Bedarf" (kein Datenbank-
    Schema-Wechsel nötig, da 0 als echtes Intervall ohnehin sinnlos wäre):
    solche Aufgaben werden nie automatisch gelb/rot und lösen dadurch auch
    keine Erinnerung aus (check_tasks_job benachrichtigt nur bei gelb/rot).
    """
    now = now or ntptime.now_utc()

    last = task.completions[0].timestamp if task.completions else None

    if task.interval_hours == 0:
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        # Nach-Bedarf-Aufgaben sollen nach dem Abhaken trotzdem aus der
        # "Offen"-Liste verschwinden (sonst wirkt der Button kaputt, weil er
        # anders als bei Intervall-Aufgaben nicht automatisch aus der Liste
        # faellt) - aber eben nur fuer den restlichen Tag, danach wieder
        # normal verwendbar. Lokaler Kalendertag statt rollierender 24h,
        # konsistent mit dem Rest der App (z.B. done_today im Dashboard).
        done_today = last is not None and last.astimezone(_APP_TZ).date() == now.astimezone(_APP_TZ).date()
        return {
            "status": "green",
            "last_completed": last,
            "due_at": None,
            "warn_at": None,
            "on_demand": True,
            "on_demand_done_today": done_today,
        }

    if last is None:
        # nie erledigt -> sofort fällig (rot), Basis ist "Erstellung/jetzt"
        due_at = now
        warn_at = now
    else:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        active_weekdays = _parse_active_weekdays(task.active_weekdays)
        due_at = _advance_due_at(last, task.interval_hours, active_weekdays)
        warn_at = due_at - timedelta(hours=task.warn_hours)

    if now >= due_at:
        status = "red"
    elif now >= warn_at:
        status = "yellow"
    else:
        status = "green"

    return {
        "status": status,
        "last_completed": last,
        "due_at": due_at,
        "warn_at": warn_at,
        "on_demand": False,
        "on_demand_done_today": False,
    }


def inventory_critical_threshold(item):
    """Mindestbestand-Schwelle für den 2-Stufen-Status. Nutzt den expliziten
    Wert (stock_critical), falls gesetzt, sonst die Hälfte des Soll-Bestands
    (stock_min) als sinnvoller Default ohne Konfigurationsaufwand. Geklemmt
    auf höchstens stock_min, damit ein versehentlich zu hoch gesetzter Wert
    nicht größer als der Zielbestand selbst wird."""
    if not item.stock_min:
        return None
    threshold = item.stock_critical if item.stock_critical is not None else item.stock_min / 2
    return min(threshold, item.stock_min)


def compute_inventory_status(item) -> dict:
    """Vereinfachtes 2-Stufen-Ampel-System (kein 'critical'/'empty' mit
    Prozent-Zwischenschwellen mehr): 'ok' (gruen, 'Im Soll') sobald der
    Ist-Bestand über dem Mindestbestand (stock_critical, mit Fallback über
    inventory_critical_threshold) liegt, sonst 'low' (rot, 'Niedriger
    Bestand') - eine einzige klare Schwelle statt mehrerer Ampelstufen.
    WICHTIG: die Schwelle ist der Mindestbestand, NICHT der Soll-Bestand
    (stock_min) - ein Artikel kann unter Soll aber über Mindestbestand
    liegen und gilt dann trotzdem als "Im Soll" (siehe Bug-Report: Artikel
    mit 3 Ist / 4 Soll / 2 Mindestbestand ist gruen, nicht rot). fill_pct
    bleibt nur als Füllstand für den Fortschrittsbalken erhalten (rein
    visuell, keine Status-Entscheidung mehr)."""
    critical_threshold = inventory_critical_threshold(item)
    if critical_threshold is not None and item.stock_current <= critical_threshold:
        status = "low"
    else:
        status = "ok"
    fill_pct = max(0, min(100, round(item.stock_current / item.stock_min * 100))) if item.stock_min else 100
    return {"status": status, "fill_pct": fill_pct, "critical_threshold": critical_threshold}
