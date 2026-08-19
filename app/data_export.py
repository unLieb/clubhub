"""Selektiver Struktur-Export/Import als JSON - Ergänzung zum vollständigen
Datenbank-Backup (siehe backup.py). Gedacht für den Fall, dass ClubHUB auf
einem neuen (weitgehend leeren) Server aufgesetzt wird und nur bestimmte
Kategorien (z.B. Gruppen/Bereiche/Aufgaben/Inventar) aus einer bestehenden
Instanz übernommen werden sollen, ohne z.B. Test-Nutzer mitzuschleppen.

Referenzen zwischen Kategorien laufen bewusst über Namen statt Datenbank-IDs
(die zwischen Quell- und Ziel-Instanz ohnehin nicht übereinstimmen), passend
zu den in models.py bereits als eindeutig markierten Namen (Group.name,
User.name). Der Import geht davon aus, dass die Ziel-Instanz für die
ausgewählten Kategorien im Wesentlichen leer ist: existiert ein Name schon,
wird die vorhandene Zeile wiederverwendet (Gruppen/Bereiche/Kanäle/Inventar)
bzw. der Import dieser Zeile übersprungen (Nutzer), statt eine vollwertige
Merge-/Konfliktauflösung für zwei bereits unabhängig befüllte Datenbanken zu
bauen - für den beschriebenen Rollout-Fall unnötiger Aufwand."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import models
from . import version

CATEGORIES = ["groups", "channels", "users", "rooms", "tasks", "inventory_items", "appointments"]

CATEGORY_LABELS = {
    "groups": "Gruppen",
    "channels": "Benachrichtigungskanäle",
    "users": "Nutzer",
    "rooms": "Bereiche",
    "tasks": "Aufgaben",
    "inventory_items": "Inventar",
    "appointments": "Termine",
}


def _aware(ts):
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def export_data_json(db: Session) -> dict:
    return {
        "meta": {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "app_version": version.VERSION,
        },
        "groups": [
            {"name": g.name, "work_start_hour": g.work_start_hour, "work_end_hour": g.work_end_hour}
            for g in db.query(models.Group).all()
        ],
        "channels": [
            {"name": c.name, "type": c.type, "target": c.target, "groups": [g.name for g in c.groups]}
            for c in db.query(models.NotificationChannel).all()
        ],
        "users": [
            {
                "name": u.name,
                "personnel_number": u.personnel_number,
                "password_hash": u.password_hash,
                "is_admin": u.is_admin,
                "is_shift_lead": u.is_shift_lead,
                "is_active": u.is_active,
                "hourly_wage": u.hourly_wage,
                "target_hours_per_month": u.target_hours_per_month,
                "avatar_url": u.avatar_url,
                "groups": [g.name for g in u.groups],
            }
            for u in db.query(models.User).all()
        ],
        "rooms": [
            {"name": r.name, "groups": [g.name for g in r.groups]}
            for r in db.query(models.Room).all()
        ],
        "tasks": [
            {"name": t.name, "room": t.room.name, "interval_hours": t.interval_hours, "warn_hours": t.warn_hours}
            for t in db.query(models.Task).all()
        ],
        "inventory_items": [
            {
                "name": i.name,
                "unit": i.unit,
                "unit_plural": i.unit_plural,
                "pack_size": i.pack_size,
                "pack_unit": i.pack_unit,
                "stock_current": i.stock_current,
                "stock_min": i.stock_min,
                "stock_critical": i.stock_critical,
                "category": i.category,
                "location": i.location,
                "reorder_url": i.reorder_url,
                "image_url": i.image_url,
                "group": i.group.name if i.group else None,
            }
            for i in db.query(models.InventoryItem).all()
        ],
        "appointments": [
            {
                "name": a.name,
                "date": _aware(a.date).isoformat(),
                "recurrence_days": a.recurrence_days,
                "notify_days_before": a.notify_days_before,
                "groups": [g.name for g in a.groups],
                "company_wide": a.is_company_wide,
                "created_by": a.user.name if a.user else None,
            }
            for a in db.query(models.Appointment).all()
        ],
    }


def _new_summary() -> dict:
    return {cat: {"created": 0, "matched": 0, "skipped": 0, "skip_reasons": []} for cat in CATEGORIES}


def import_data_json(db: Session, data: dict, selected: set, importing_user) -> dict:
    """Importiert die ausgewählten Kategorien in Abhängigkeitsreihenfolge
    (Gruppen -> Kanäle -> Nutzer -> Bereiche -> Aufgaben -> Inventar ->
    Termine). Referenzen (z.B. Aufgabe -> Bereich) werden per Name gegen das
    aufgelöst, was in der Ziel-Datenbank JETZT schon existiert - unabhängig
    davon, ob die referenzierte Kategorie selbst mit ausgewählt wurde. Lässt
    sich eine Pflicht-Referenz nicht auflösen, wird die betroffene Zeile
    übersprungen und im Ergebnis begründet."""
    summary = _new_summary()

    group_by_name = {g.name: g for g in db.query(models.Group).all()}
    if "groups" in selected:
        for row in data.get("groups", []):
            if row["name"] in group_by_name:
                summary["groups"]["matched"] += 1
                continue
            group = models.Group(
                name=row["name"],
                work_start_hour=row.get("work_start_hour"),
                work_end_hour=row.get("work_end_hour"),
            )
            db.add(group)
            db.flush()
            group_by_name[group.name] = group
            summary["groups"]["created"] += 1

    if "channels" in selected:
        existing_channels = {c.name for c in db.query(models.NotificationChannel).all()}
        for row in data.get("channels", []):
            if row["name"] in existing_channels:
                summary["channels"]["matched"] += 1
                continue
            channel = models.NotificationChannel(name=row["name"], type=row["type"], target=row.get("target"))
            db.add(channel)
            db.flush()
            for gname in row.get("groups", []):
                group = group_by_name.get(gname)
                if group:
                    channel.groups.append(group)
            existing_channels.add(channel.name)
            summary["channels"]["created"] += 1

    user_by_name = {u.name: u for u in db.query(models.User).all()}
    if "users" in selected:
        for row in data.get("users", []):
            if row["name"] in user_by_name:
                summary["users"]["skipped"] += 1
                summary["users"]["skip_reasons"].append(f"{row['name']}: Nutzername existiert bereits")
                continue
            user = models.User(
                name=row["name"],
                personnel_number=row.get("personnel_number"),
                password_hash=row["password_hash"],
                is_admin=row.get("is_admin", False),
                is_shift_lead=row.get("is_shift_lead", False),
                is_active=row.get("is_active", True),
                hourly_wage=row.get("hourly_wage"),
                target_hours_per_month=row.get("target_hours_per_month"),
                avatar_url=row.get("avatar_url"),
            )
            db.add(user)
            db.flush()
            for gname in row.get("groups", []):
                group = group_by_name.get(gname)
                if group:
                    user.groups.append(group)
            user_by_name[user.name] = user
            summary["users"]["created"] += 1

    room_by_name = {r.name: r for r in db.query(models.Room).all()}
    if "rooms" in selected:
        for row in data.get("rooms", []):
            if row["name"] in room_by_name:
                summary["rooms"]["matched"] += 1
                continue
            room = models.Room(name=row["name"])
            db.add(room)
            db.flush()
            for gname in row.get("groups", []):
                group = group_by_name.get(gname)
                if group:
                    room.groups.append(group)
            room_by_name[room.name] = room
            summary["rooms"]["created"] += 1

    if "tasks" in selected:
        existing_tasks = {(t.name, t.room_id) for t in db.query(models.Task).all()}
        for row in data.get("tasks", []):
            room = room_by_name.get(row["room"])
            if not room:
                summary["tasks"]["skipped"] += 1
                summary["tasks"]["skip_reasons"].append(f'{row["name"]}: Bereich "{row["room"]}" nicht gefunden')
                continue
            if (row["name"], room.id) in existing_tasks:
                summary["tasks"]["matched"] += 1
                continue
            db.add(models.Task(
                name=row["name"], room_id=room.id,
                interval_hours=row["interval_hours"], warn_hours=row.get("warn_hours", 5.0),
            ))
            existing_tasks.add((row["name"], room.id))
            summary["tasks"]["created"] += 1

    if "inventory_items" in selected:
        existing_items = {i.name for i in db.query(models.InventoryItem).all()}
        for row in data.get("inventory_items", []):
            if row["name"] in existing_items:
                summary["inventory_items"]["matched"] += 1
                continue
            group = group_by_name.get(row.get("group"))
            db.add(models.InventoryItem(
                name=row["name"], unit=row.get("unit"), unit_plural=row.get("unit_plural"),
                pack_size=row.get("pack_size"), pack_unit=row.get("pack_unit"),
                stock_current=row.get("stock_current", 0.0), stock_min=row.get("stock_min", 0.0),
                stock_critical=row.get("stock_critical"), category=row.get("category"),
                location=row.get("location"), reorder_url=row.get("reorder_url"), image_url=row.get("image_url"),
                group_id=group.id if group else None,
            ))
            existing_items.add(row["name"])
            summary["inventory_items"]["created"] += 1

    if "appointments" in selected:
        existing_appts = {(a.name, _aware(a.date)) for a in db.query(models.Appointment).all()}
        for row in data.get("appointments", []):
            date = datetime.fromisoformat(row["date"])
            if (row["name"], date) in existing_appts:
                summary["appointments"]["matched"] += 1
                continue
            # "groups" (Liste, Mehrfachauswahl) ist das aktuelle Format - "group"
            # (Einzelwert) als Fallback fuer aeltere Export-Dateien von vor der
            # Mehrfach-Gruppenauswahl.
            group_names = row.get("groups") or ([row["group"]] if row.get("group") else [])
            groups = [group_by_name[n] for n in group_names if n in group_by_name]
            creator = user_by_name.get(row.get("created_by")) or importing_user
            appt = models.Appointment(
                name=row["name"], date=date, recurrence_days=row.get("recurrence_days"),
                notify_days_before=row.get("notify_days_before", 1.0),
                is_company_wide=row.get("company_wide", False), user_id=creator.id,
            )
            appt.groups = groups
            db.add(appt)
            existing_appts.add((row["name"], date))
            summary["appointments"]["created"] += 1

    db.commit()
    return summary
