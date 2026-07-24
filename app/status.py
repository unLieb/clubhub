from datetime import timezone, timedelta

from . import ntptime


def task_status(task, now=None):
    """
    Berechnet Status ('green' | 'yellow' | 'red') sowie die relevanten
    Zeitpunkte für eine Aufgabe, basierend auf rollierendem Intervall.
    """
    now = now or ntptime.now_utc()

    last = task.completions[0].timestamp if task.completions else None
    if last is None:
        # nie erledigt -> sofort fällig (rot), Basis ist "Erstellung/jetzt"
        due_at = now
        warn_at = now
    else:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        interval = timedelta(hours=task.interval_hours)
        due_at = last + interval
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
    }


def inventory_critical_threshold(item):
    """Schwelle, unterhalb derer ein Artikel als 'critical' statt nur 'low'
    gilt. Nutzt den expliziten Mindestbestand (stock_critical), falls
    gesetzt, sonst die Hälfte des Soll-Bestands (stock_min) als sinnvoller
    Default ohne Konfigurationsaufwand. Geklemmt auf höchstens stock_min,
    damit eine versehentlich zu hoch gesetzte Schwelle 'low' nicht
    verschluckt."""
    if not item.stock_min:
        return None
    threshold = item.stock_critical if item.stock_critical is not None else item.stock_min / 2
    return min(threshold, item.stock_min)


def compute_inventory_status(item) -> dict:
    """Ampel-Status ('ok' | 'low' | 'critical' | 'empty') + Füllstand in
    Prozent (relativ zum Soll-Bestand, ab dem die Anzeige als voll gilt) für
    die farbige Bestandsanzeige. 'low' = unter Soll-Bestand aber noch über
    dem Mindestbestand (kann warten), 'critical' = darunter (dringend)."""
    critical_threshold = inventory_critical_threshold(item)
    if item.stock_current <= 0:
        status = "empty"
    elif critical_threshold is not None and item.stock_current < critical_threshold:
        status = "critical"
    elif item.stock_min and item.stock_current < item.stock_min:
        status = "low"
    else:
        status = "ok"

    if status == "empty":
        fill_pct = 0
    elif status == "ok" or not item.stock_min:
        fill_pct = 100
    else:
        fill_pct = max(0, min(100, round(item.stock_current / item.stock_min * 100)))

    return {"status": status, "fill_pct": fill_pct, "critical_threshold": critical_threshold}
