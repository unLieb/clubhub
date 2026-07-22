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
