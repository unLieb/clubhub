"""Berechnung fuer den Wartungsplan (siehe MaintenancePlan in models.py):
naechste Faelligkeit, Ampel und "zu frueh"-Erkennung. Reine Logik ohne
DB-Zugriff, damit Seite und Scheduler dasselbe Ergebnis bekommen."""
import calendar
from datetime import date, timedelta

UNIT_LABELS = {"days": "Tage", "weeks": "Wochen", "months": "Monate", "years": "Jahre"}


def add_interval(d: date, value: int, unit: str) -> date:
    value = max(int(value or 1), 1)
    if unit == "days":
        return d + timedelta(days=value)
    if unit == "weeks":
        return d + timedelta(weeks=value)
    months = value * (12 if unit == "years" else 1)
    idx = d.year * 12 + (d.month - 1) + months
    year, month = divmod(idx, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def has_interval(plan) -> bool:
    return plan.interval_unit in UNIT_LABELS


def interval_label(plan) -> str:
    if not has_interval(plan):
        return "kein fester Turnus"
    return f"alle {plan.interval_value} {UNIT_LABELS[plan.interval_unit]}"


def plan_state(plan, today: date) -> dict:
    """status: green | yellow | red | grey (kein Startdatum/Besuch bzw. Plan
    ohne festen Turnus). Besuche nach heute gelten als angekuendigt
    ("upcoming") und fliessen noch nicht in Turnus/Durchschnitt ein."""
    past = [v for v in plan.visits if v.visit_date <= today]
    upcoming = [v for v in plan.visits if v.visit_date > today]
    regular = [v for v in past if v.kind == "regular"]
    last = regular[-1].visit_date if regular else None
    any_last = past[-1].visit_date if past else None
    tol = plan.tolerance_days or 0
    interval = has_interval(plan)

    # Tatsaechlicher Abstand der Regelwartungen (auch ohne festen Turnus, um
    # ihn aus der Praxis kennenzulernen)
    gaps = [(b.visit_date - a.visit_date).days for a, b in zip(regular, regular[1:])]
    avg_gap = round(sum(gaps) / len(gaps)) if gaps else None

    next_due = None
    early = {}
    if interval:
        next_due = add_interval(last, plan.interval_value, plan.interval_unit) if last else plan.start_date
        # Besuche, die vor Ablauf des Turnus (minus Toleranz) stattfanden
        for prev, cur in zip(regular, regular[1:]):
            expected = add_interval(prev.visit_date, plan.interval_value, plan.interval_unit)
            diff = (expected - cur.visit_date).days
            if diff > tol:
                early[cur.id] = {"days_early": diff, "since_last": (cur.visit_date - prev.visit_date).days}

    if next_due is None:
        status, days_left = "grey", None
    else:
        days_left = (next_due - today).days
        if days_left < 0:
            status = "red"
        elif days_left <= (plan.notify_days_before or 0):
            status = "yellow"
        else:
            status = "green"
    return {
        "last_regular": last, "last_any": any_last, "next_due": next_due, "days_left": days_left,
        "status": status, "early": early, "has_interval": interval,
        "avg_gap": avg_gap, "upcoming": upcoming,
        "earliest_ok": (next_due - timedelta(days=tol)) if next_due else None,
    }
