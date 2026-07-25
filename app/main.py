import json
import os
import re
import secrets
import signal
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import FastAPI, Request, Depends, Form, File, UploadFile, BackgroundTasks, HTTPException
from fastapi.responses import RedirectResponse, Response, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from .database import Base, engine, get_db, DB_PATH, SessionLocal
from . import models
from . import ntptime
from . import backup
from . import version
from . import push
from .auth import hash_pin, verify_pin, get_current_user, require_login, require_admin, require_admin_or_shift_lead
from .status import task_status, compute_inventory_status
from .scheduler import start_scheduler, APP_TIMEZONE, BACKUP_SCHEDULE_HOURS, BACKUP_RETENTION_DAYS
from .notifications import notify_group

Base.metadata.create_all(bind=engine)

app = FastAPI(title="ClubHUB")
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "change-me-in-production"))
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

# Foto-Uploads zu Meldungen liegen im persistenten Datenverzeichnis (nicht im
# Image), damit sie Container-Neustarts/-Updates überstehen.
UPLOADS_DIR = os.path.join(os.path.dirname(DB_PATH), "uploads")
REPORT_PHOTOS_DIR = os.path.join(UPLOADS_DIR, "reports")
os.makedirs(REPORT_PHOTOS_DIR, exist_ok=True)
INVENTORY_IMAGES_DIR = os.path.join(UPLOADS_DIR, "inventory")
os.makedirs(INVENTORY_IMAGES_DIR, exist_ok=True)
AVATAR_IMAGES_DIR = os.path.join(UPLOADS_DIR, "avatars")
os.makedirs(AVATAR_IMAGES_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
# Öffentlicher VAPID-Schlüssel fürs Web-Push-Abo (base.html), siehe push.py.
templates.env.globals["vapid_public_key"] = push.get_vapid_public_key


def _to_local(ts):
    """Für Templates, die einen DB-Zeitstempel (intern immer UTC, teils ohne
    tzinfo) direkt formatieren wollen - rechnet nach APP_TIMEZONE um, inkl.
    automatischem Sommer-/Winterzeit-Wechsel statt eines festen Offsets."""
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(APP_TIMEZONE)


templates.env.filters["localtime"] = _to_local


@app.get("/sw.js")
def service_worker():
    # Bewusst unter der Root-URL statt /static/sw.js ausgeliefert: der
    # Geltungsbereich (scope) eines Service Workers ist standardmäßig auf sein
    # eigenes Verzeichnis begrenzt - für Push/Klicks auf beliebigen Seiten
    # muss er auf Root-Ebene liegen.
    sw_path = os.path.join(os.path.dirname(__file__), "static", "sw.js")
    with open(sw_path, "r", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="application/javascript")
# App-Version + Git-Kurz-Hash (aus VERSION/BUILD_HASH, beim Docker-Build erzeugt)
# auf jeder Seite verfügbar (Sidebar-Fußzeile + Verwaltung/System im Detail).
templates.env.globals["app_version"] = version.VERSION
templates.env.globals["app_build_hash"] = version.BUILD_HASH

scheduler = None


def _ensure_column(db: Session, table: str, column: str, coltype: str):
    """Legt eine fehlende Spalte per ALTER TABLE an – create_all() legt nur
    fehlende Tabellen an, ändert aber bestehende Tabellen nicht."""
    existing = {row[1] for row in db.execute(text(f"PRAGMA table_info({table})")).fetchall()}
    if column not in existing:
        db.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
        db.commit()


def _migrate_task_warn_hours(db: Session):
    """warn_percent (% des Intervalls) -> warn_hours (Stunden vor Fälligkeit)."""
    _ensure_column(db, "tasks", "warn_hours", "FLOAT")
    try:
        rows = db.execute(
            text("SELECT id, interval_hours, warn_percent FROM tasks WHERE warn_hours IS NULL")
        ).fetchall()
    except OperationalError:
        return  # frisches Setup: warn_percent hat nie existiert
    for task_id, interval_hours, warn_percent in rows:
        warn_hours = interval_hours * (warn_percent or 20.0) / 100.0
        db.execute(text("UPDATE tasks SET warn_hours = :wh WHERE id = :id"), {"wh": warn_hours, "id": task_id})
    db.commit()


def _migrate_group_work_hours(db: Session):
    """Neue, optionale Arbeitszeit-Spalten pro Gruppe (kein Altdaten-Bezug)."""
    _ensure_column(db, "groups", "work_start_hour", "INTEGER")
    _ensure_column(db, "groups", "work_end_hour", "INTEGER")


def _migrate_user_shift_lead(db: Session):
    """Neue Rolle 'Schichtleiter' (kein Altdaten-Bezug, Standard: False)."""
    _ensure_column(db, "users", "is_shift_lead", "INTEGER DEFAULT 0")


def _migrate_report_priority_category(db: Session):
    """Neue Priorität/Kategorie-Spalten für Meldungen (kein Altdaten-Bezug)."""
    _ensure_column(db, "reports", "priority", "TEXT DEFAULT 'normal'")
    _ensure_column(db, "reports", "category", "TEXT DEFAULT 'sonstiges'")


def _migrate_report_assigned_group(db: Session):
    """Optionale Zuständigkeits-Gruppe je Meldung (kein Altdaten-Bezug)."""
    _ensure_column(db, "reports", "assigned_group_id", "INTEGER")


def _migrate_inventory_extras(db: Session):
    """Neue, frei vergebbare Kategorie/Lagerort- sowie Nachbestell-URL-Spalte
    pro Inventarartikel (kein Altdaten-Bezug)."""
    _ensure_column(db, "inventory_items", "category", "TEXT")
    _ensure_column(db, "inventory_items", "location", "TEXT")
    _ensure_column(db, "inventory_items", "reorder_url", "TEXT")
    _ensure_column(db, "inventory_items", "image_url", "TEXT")


def _migrate_inventory_pack_size(db: Session):
    """Optionale Gebindegröße (z.B. '10 Liter' je Kanister, '8 Rollen' je
    Packung), rein informativ zur Anzeige - kein Altdaten-Bezug."""
    _ensure_column(db, "inventory_items", "pack_size", "FLOAT")
    _ensure_column(db, "inventory_items", "pack_unit", "TEXT")


def _migrate_inventory_unit_plural(db: Session):
    """Optionale Mehrzahlform des Gebindes (z.B. 'Rollen' zu 'Rolle'), damit
    die Anzeige ab einer Menge von 2 grammatikalisch korrekt pluralisiert -
    kein Altdaten-Bezug."""
    _ensure_column(db, "inventory_items", "unit_plural", "TEXT")


def _migrate_inventory_critical_stock(db: Session):
    """Optionaler Mindestbestand (kritische Schwelle) je Artikel, getrennt
    vom Soll-Bestand - kein Altdaten-Bezug."""
    _ensure_column(db, "inventory_items", "stock_critical", "FLOAT")


def _migrate_user_time_tracking(db: Session):
    """Zeiterfassung: Stundensatz + Soll-Arbeitszeit/Monat pro Nutzer, beide
    optional (kein Altdaten-Bezug)."""
    _ensure_column(db, "users", "hourly_wage", "FLOAT")
    _ensure_column(db, "users", "target_hours_per_month", "FLOAT")


def _migrate_user_avatar(db: Session):
    """Optionales Profilbild pro Nutzer (kein Altdaten-Bezug)."""
    _ensure_column(db, "users", "avatar_url", "TEXT")


def _migrate_remove_timeclock_nfc_tags(db: Session):
    """Das Ein-/Ausstempeln lief anfangs über einen gemeinsamen NFC-Tag
    (/timeclock/scan), wurde aber durch ein autorisiertes Terminal ersetzt
    (Manipulationsschutz, da ein NFC-Tag beliebig kopierbar ist). Räumt evtl.
    zuvor angelegte Registry-Einträge für dieses inzwischen entfernte Ziel auf."""
    db.query(models.NfcTag).filter(models.NfcTag.target_type == "timeclock").delete()
    db.commit()


def _migrate_report_photos(db: Session):
    """Überführt das alte einzelne photo_filename je Meldung (vor der Umstellung
    auf mehrere Fotos pro Meldung) in je eine ReportPhoto-Zeile. Greift nur für
    Meldungen, die noch keine ReportPhoto-Zeilen haben."""
    reports = db.query(models.Report).filter(models.Report.photo_filename.isnot(None)).all()
    for report in reports:
        if db.query(models.ReportPhoto).filter(models.ReportPhoto.report_id == report.id).count() > 0:
            continue
        db.add(models.ReportPhoto(report_id=report.id, filename=report.photo_filename))
    db.commit()


def _migrate_legacy_group_channels(db: Session):
    """Überführt ntfy_topic/gotify_token aus alten Gruppen-Zeilen (vor der
    Trennung in eigene Benachrichtigungskanäle) in NotificationChannel-Objekte.
    Greift nur, wenn noch keine Kanäle existieren und die alten Spalten in der
    SQLite-Datei noch vorhanden sind (create_all() legt nur fehlende Tabellen
    an und ändert bestehende nicht, daher bleiben sie bei Upgrades einfach liegen)."""
    if db.query(models.NotificationChannel).count() > 0:
        return
    try:
        rows = db.execute(text("SELECT id, name, ntfy_topic, gotify_token FROM groups")).fetchall()
    except OperationalError:
        return  # frisches Setup: Spalten haben nie existiert

    for group_id, group_name, ntfy_topic, gotify_token in rows:
        group = db.query(models.Group).filter(models.Group.id == group_id).first()
        if not group:
            continue
        if ntfy_topic:
            channel = models.NotificationChannel(name=f"{group_name} (ntfy)", type="ntfy", target=ntfy_topic)
            db.add(channel)
            group.channels.append(channel)
        if gotify_token:
            channel = models.NotificationChannel(name=f"{group_name} (Gotify)", type="gotify", target=gotify_token)
            db.add(channel)
            group.channels.append(channel)
    db.commit()


@app.on_event("startup")
def _startup():
    global scheduler
    ntptime.sync()  # Erstsync synchron, damit die Zeit von Anfang an stimmt
    scheduler = start_scheduler()

    db = next(get_db())
    try:
        _migrate_task_warn_hours(db)
        _migrate_group_work_hours(db)
        _migrate_legacy_group_channels(db)
        _migrate_user_shift_lead(db)
        _migrate_report_priority_category(db)
        _migrate_report_assigned_group(db)
        _migrate_report_photos(db)
        _migrate_inventory_extras(db)
        _migrate_inventory_pack_size(db)
        _migrate_inventory_unit_plural(db)
        _migrate_inventory_critical_stock(db)
        _migrate_user_time_tracking(db)
        _migrate_remove_timeclock_nfc_tags(db)
        _migrate_user_avatar(db)
    finally:
        db.close()

    # Ersten Admin anlegen, falls die Datenbank noch leer ist
    db = next(get_db())
    try:
        if db.query(models.User).count() == 0:
            admin_name = os.environ.get("INITIAL_ADMIN_NAME", "Admin")
            admin_pin = os.environ.get("INITIAL_ADMIN_PIN", "0000")
            db.add(models.User(name=admin_name, pin_hash=hash_pin(admin_pin), is_admin=True))
            db.commit()
            print(f"[Setup] Erster Admin angelegt: '{admin_name}' mit PIN '{admin_pin}' "
                  f"– bitte nach dem ersten Login unter /admin ändern bzw. eigenen Nutzer anlegen.")
    finally:
        db.close()


def require_login_page(request: Request, db: Session):
    """Für normale Seitenaufrufe (GET): ohne Login direkt auf /login umleiten
    (mit next-Rücksprung), statt wie require_login() einen rohen 401 zu
    werfen - bessere UX für ganze Seiten statt einzelner Aktionen. Gibt
    (user, None) bei Login zurück, sonst (None, RedirectResponse)."""
    user = get_current_user(request, db)
    if user:
        return user, None
    return None, RedirectResponse(f"/login?next={request.url.path}", status_code=302)


# ---------- Dashboard ----------

STATUS_RANK = {"green": 0, "yellow": 1, "red": 2}

ROOM_ICON_RULES = [
    (("wc", "toilette", "klo"), "🚻"),
    (("küche", "kueche"), "🍳"),
    (("spüle", "spuele", "abwasch"), "🧽"),
    (("büro", "buero", "office"), "💼"),
    (("lager",), "📦"),
    (("flur", "treppenhaus", "eingang"), "🚪"),
    (("garten", "terrasse", "hof", "außen", "aussen"), "🌳"),
    (("keller",), "🗄️"),
    (("bar", "theke"), "🍸"),
    (("umkleide",), "👕"),
    (("müll", "muell", "abfall"), "🗑️"),
]


def guess_room_icon(name: str) -> str:
    lowered = name.lower()
    for keywords, icon in ROOM_ICON_RULES:
        if any(k in lowered for k in keywords):
            return icon
    return "🧹"


def format_duration_de(delta) -> str:
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes < 1:
        return "unter 1 Minute"
    if total_minutes < 60:
        return f"{total_minutes} Minute{'n' if total_minutes != 1 else ''}"
    total_hours = total_minutes // 60
    if total_hours < 24:
        rest_minutes = total_minutes % 60
        text = f"{total_hours} Stunde{'n' if total_hours != 1 else ''}"
        if rest_minutes:
            text += f" {rest_minutes} Min."
        return text
    days = total_hours // 24
    return f"{days} Tag{'e' if days != 1 else ''}"


def format_hours_de(hours: float) -> str:
    total_minutes = round((hours or 0.0) * 60)
    h, m = divmod(total_minutes, 60)
    if h and m:
        return f"{h} Std. {m} Min."
    if h:
        return f"{h} Std."
    return f"{m} Min."


templates.env.globals["format_hours_de"] = format_hours_de


def format_qty(value) -> str:
    """Zeigt Mengen (Bestand, Gebindegröße) ohne Nachkommastelle, wenn der
    Wert ganzzahlig ist (z.B. "1" statt "1.0"), sonst mit - so bleibt eine
    bewusst eingegebene Nachkommastelle (z.B. "1.5") weiterhin sichtbar."""
    if value is None:
        return "–"
    value = round(float(value), 2)
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


templates.env.filters["qty"] = format_qty


def unit_label(item, *quantities) -> str:
    """Gibt die Einzahl oder Mehrzahl des Gebindes zurück, je nachdem ob alle
    übergebenen Mengen genau 1 sind (Einzahl) oder nicht (Mehrzahl) - z.B.
    "1 Rolle" aber "10 Rollen". Ohne hinterlegte Mehrzahlform wird immer die
    Einzahl verwendet (z.B. bei invarianten Wörtern wie "Kanister"). Ohne
    übergebene Menge (z.B. reine Namensnennung ohne Bestandszahl daneben)
    wird die Einzahl als Grundform gezeigt."""
    if not item.unit:
        return ""
    plural = item.unit_plural or item.unit
    if not quantities:
        return item.unit
    is_singular = all(q is not None and round(float(q), 6) == 1 for q in quantities)
    return item.unit if is_singular else plural


templates.env.filters["unit_label"] = unit_label


def greeting_for_now(now) -> str:
    """Tageszeit-abhängige Begrüßung, nach lokaler Stunde (APP_TIMEZONE) -
    'Guten Abend' gilt bis in die Nacht hinein, da 'Gute Nacht' im
    normalen Sprachgebrauch keine Begrüßung, sondern ein Abschiedsgruß ist."""
    hour = now.astimezone(APP_TIMEZONE).hour
    if 5 <= hour < 11:
        return "Guten Morgen"
    if 11 <= hour < 18:
        return "Guten Tag"
    return "Guten Abend"


def compute_room_statuses(rooms, now):
    """Berechnet Ampel-Status pro Bereich sowie global überfällige/bald fällige
    Aufgaben. Von Dashboard und Bereiche-Übersicht gemeinsam genutzt."""
    room_status = {}
    overdue_tasks = []
    due_soon_count = 0
    overdue_count = 0

    for room in rooms:
        worst = None
        last_completed = None
        last_user = None
        for task in room.tasks:
            s = task_status(task, now)
            if worst is None or STATUS_RANK[s["status"]] > STATUS_RANK[worst["status"]]:
                worst = s
            if s["status"] == "yellow":
                due_soon_count += 1
            elif s["status"] == "red":
                overdue_count += 1
                overdue_tasks.append({
                    "task": task,
                    "duration_text": f"Überfällig seit {format_duration_de(now - s['due_at'])}",
                })
            if task.completions and (last_completed is None or task.completions[0].timestamp > last_completed):
                last_completed = task.completions[0].timestamp
                last_user = task.completions[0].user.name

        if worst is None:
            # Bereich ohne Aufgaben -> neutral als "erledigt" behandeln
            worst = {"status": "green", "due_at": now, "warn_at": now}

        if worst["status"] == "red":
            duration_text = f"seit {format_duration_de(now - worst['due_at'])}"
        elif worst["status"] == "yellow":
            duration_text = f"fällig in {format_duration_de(worst['due_at'] - now)}"
        else:
            duration_text = None

        room_status[room.id] = {
            "status": worst["status"],
            "duration_text": duration_text,
            "last_completed": last_completed,
            "last_user": last_user,
            "icon": guess_room_icon(room.name),
        }

    return room_status, overdue_tasks, due_soon_count, overdue_count


@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    rooms = db.query(models.Room).all()
    now = ntptime.now_utc()
    user = get_current_user(request, db)

    timeclock_open_entry = None
    if user:
        open_entry = (
            db.query(models.TimeEntry)
            .filter(models.TimeEntry.user_id == user.id, models.TimeEntry.clock_out.is_(None))
            .first()
        )
        if open_entry:
            timeclock_open_entry = _aware(open_entry.clock_in).astimezone(APP_TIMEZONE)

    currently_clocked_in = []
    if user and (user.is_admin or user.is_shift_lead):
        open_entries = (
            db.query(models.TimeEntry)
            .filter(models.TimeEntry.clock_out.is_(None))
            .order_by(models.TimeEntry.clock_in.asc())
            .all()
        )
        currently_clocked_in = [
            {"user": e.user, "since": _aware(e.clock_in).astimezone(APP_TIMEZONE)} for e in open_entries
        ]

    room_status, overdue_tasks, due_soon_count, overdue_count = compute_room_statuses(rooms, now)
    attention_rooms = [r for r in rooms if room_status[r.id]["status"] != "green"]

    today = now.date()
    done_today = (
        db.query(models.Completion)
        .filter(models.Completion.timestamp >= datetime(today.year, today.month, today.day, tzinfo=timezone.utc))
        .count()
    )

    recent_completions = (
        db.query(models.Completion).order_by(models.Completion.timestamp.desc()).limit(8).all()
    )

    all_reports = db.query(models.Report).all()
    open_reports = _sort_reports([r for r in all_reports if r.status != "done"])
    open_count = sum(1 for r in all_reports if r.status == "open")
    in_progress_count = sum(1 for r in all_reports if r.status == "in_progress")
    done_today_reports = sum(
        1 for r in all_reports
        if r.status == "done" and r.resolved_at and r.resolved_at.astimezone(timezone.utc).date() == today
    )

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "rooms": rooms,
        "attention_rooms": attention_rooms,
        "room_status": room_status,
        "stats": {"done_today": done_today, "due_soon": due_soon_count, "overdue": overdue_count},
        "recent_completions": recent_completions,
        "overdue_tasks": overdue_tasks[:6],
        "report_stats": {
            "open": open_count,
            "in_progress": in_progress_count,
            "done_today": done_today_reports,
            "needs_attention": open_count + in_progress_count,
        },
        "recent_reports": open_reports[:5],
        "greeting": greeting_for_now(now),
        "pending_tasks_count": due_soon_count + overdue_count,
        "now": now,
        "now_local": now.astimezone(APP_TIMEZONE),
        "device_authorized": device_is_authorized(request, db),
        "timeclock_open_entry": timeclock_open_entry,
        "currently_clocked_in": currently_clocked_in,
    })


@app.get("/rooms")
def rooms_overview(request: Request, sort: str = "status", db: Session = Depends(get_db)):
    user, redirect = require_login_page(request, db)
    if redirect:
        return redirect
    rooms = db.query(models.Room).all()
    now = ntptime.now_utc()
    room_status, _, _, _ = compute_room_statuses(rooms, now)
    if sort == "name":
        rooms.sort(key=lambda r: r.name.lower())
    else:
        sort = "status"
        # Überfällig (rot) zuerst, dann bald fällig (gelb), erledigt (grün)
        # zuletzt; innerhalb desselben Status alphabetisch nach Name.
        rooms.sort(key=lambda r: (-STATUS_RANK[room_status[r.id]["status"]], r.name.lower()))
    return templates.TemplateResponse("rooms.html", {
        "request": request,
        "user": user,
        "rooms": rooms,
        "room_status": room_status,
        "sort": sort,
    })


# ---------- Raum / Scan ----------

@app.get("/room/{room_id}")
def room_view(room_id: int, request: Request, db: Session = Depends(get_db)):
    user, redirect = require_login_page(request, db)
    if redirect:
        return redirect
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if not room:
        return RedirectResponse("/", status_code=302)
    now = ntptime.now_utc()
    statuses = {}
    for t in room.tasks:
        s = task_status(t, now)
        if s["status"] == "red":
            s["duration_text"] = f"Überfällig seit {format_duration_de(now - s['due_at'])}"
        elif s["status"] == "yellow":
            s["duration_text"] = f"Fällig in {format_duration_de(s['due_at'] - now)}"
        else:
            s["duration_text"] = None
        statuses[t.id] = s
    return templates.TemplateResponse("room.html", {
        "request": request,
        "user": user,
        "room": room,
        "statuses": statuses,
    })


@app.post("/room/{room_id}/task/{task_id}/complete")
def complete_task(room_id: int, task_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    task = db.query(models.Task).filter(models.Task.id == task_id, models.Task.room_id == room_id).first()
    if task:
        completion = models.Completion(task_id=task.id, user_id=user.id)
        db.add(completion)
        db.query(models.TaskGroupNotice).filter(models.TaskGroupNotice.task_id == task.id).update(
            {"last_status": "green"}
        )
        db.commit()
    return RedirectResponse(f"/room/{room_id}", status_code=302)


# ---------- Zeiterfassung ----------

def _entry_hours(entry, now) -> float:
    end = entry.clock_out or now
    return max(0.0, (_aware(end) - _aware(entry.clock_in)).total_seconds() / 3600.0)


def compute_time_stats(user, db: Session, now) -> dict:
    """Aggregiert die Zeiterfassung eines Nutzers für heute und den laufenden
    Kalendermonat, jeweils inklusive einer eventuell noch offenen (laufenden)
    Buchung bis 'now'. Die Zuordnung zu Tag/Monat erfolgt über das lokale Datum
    von clock_in - Schichten über Mitternacht werden bewusst nicht aufgeteilt,
    das wäre für den gewünschten schlanken Umfang nicht nötig. Zeiten werden
    hier schon nach lokaler Zeit umgerechnet, damit das Template sie direkt
    anzeigen kann, ohne selbst mit tz-naiven SQLite-Werten hantieren zu müssen."""
    local_now = now.astimezone(APP_TIMEZONE)
    today = local_now.date()
    month_start = today.replace(day=1)

    entries = (
        db.query(models.TimeEntry)
        .filter(models.TimeEntry.user_id == user.id)
        .order_by(models.TimeEntry.clock_in.desc())
        .limit(200)
        .all()
    )

    open_entry = None
    today_hours = 0.0
    month_hours = 0.0
    history = []
    for e in entries:
        clock_in_local = _aware(e.clock_in).astimezone(APP_TIMEZONE)
        clock_out_local = _aware(e.clock_out).astimezone(APP_TIMEZONE) if e.clock_out else None
        hours = _entry_hours(e, now)
        entry_date = clock_in_local.date()
        if entry_date >= month_start:
            month_hours += hours
            if entry_date == today:
                today_hours += hours
        if e.clock_out is None:
            open_entry = {"clock_in": clock_in_local}
        if len(history) < 20:
            history.append({
                "clock_in": clock_in_local,
                "clock_out": clock_out_local,
                "hours": hours,
                "open": e.clock_out is None,
            })

    wage = user.hourly_wage or 0.0
    target = user.target_hours_per_month
    return {
        "open_entry": open_entry,
        "today_hours": today_hours,
        "today_earned": today_hours * wage,
        "month_hours": month_hours,
        "month_earned": month_hours * wage,
        "overtime_hours": (month_hours - target) if target is not None else None,
        "history": history,
        "has_wage": user.hourly_wage is not None,
        "has_target": target is not None,
    }


@app.get("/timeclock")
def timeclock_view(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    stats = compute_time_stats(user, db, ntptime.now_utc())
    return templates.TemplateResponse("timeclock.html", {
        "request": request,
        "user": user,
        "stats": stats,
    })


# ---------- Zeiterfassungs-Terminal (autorisiertes Gerät) ----------

DEVICE_COOKIE_NAME = "tc_device_token"
DEVICE_COOKIE_MAX_AGE = 10 * 365 * 24 * 3600  # ~10 Jahre, praktisch dauerhaft


def get_authorized_device(db: Session):
    return db.query(models.AuthorizedDevice).first()


def device_is_authorized(request: Request, db: Session) -> bool:
    device = get_authorized_device(db)
    if not device:
        return False
    cookie = request.cookies.get(DEVICE_COOKIE_NAME)
    return bool(cookie) and cookie == device.token


@app.get("/timeclock/kiosk")
def timeclock_kiosk(request: Request, db: Session = Depends(get_db)):
    if not device_is_authorized(request, db):
        return templates.TemplateResponse("timeclock_kiosk.html", {
            "request": request, "user": None, "authorized": False,
        })
    users = db.query(models.User).order_by(models.User.name).all()
    return templates.TemplateResponse("timeclock_kiosk.html", {
        "request": request, "user": None, "authorized": True,
        "users": users, "error": None, "result": None,
    })


@app.post("/timeclock/kiosk")
def timeclock_kiosk_punch(
    request: Request,
    user_id: int = Form(...),
    pin: str = Form(...),
    db: Session = Depends(get_db),
):
    if not device_is_authorized(request, db):
        return templates.TemplateResponse("timeclock_kiosk.html", {
            "request": request, "user": None, "authorized": False,
        })

    users = db.query(models.User).order_by(models.User.name).all()
    target_user = db.query(models.User).filter(models.User.id == user_id).first()
    if not target_user or not verify_pin(pin, target_user.pin_hash):
        return templates.TemplateResponse("timeclock_kiosk.html", {
            "request": request, "user": None, "authorized": True,
            "users": users, "error": "PIN ist falsch.", "result": None,
        })

    open_entry = (
        db.query(models.TimeEntry)
        .filter(models.TimeEntry.user_id == target_user.id, models.TimeEntry.clock_out.is_(None))
        .first()
    )
    if open_entry:
        open_entry.clock_out = ntptime.now_utc()
        action, punch_time = "out", open_entry.clock_out
    else:
        entry = models.TimeEntry(user_id=target_user.id, clock_in=ntptime.now_utc())
        db.add(entry)
        action, punch_time = "in", entry.clock_in
    db.commit()

    return templates.TemplateResponse("timeclock_kiosk.html", {
        "request": request, "user": None, "authorized": True,
        "users": users, "error": None,
        "result": {
            "name": target_user.name,
            "action": action,
            "time_local": _aware(punch_time).astimezone(APP_TIMEZONE),
        },
    })


@app.post("/timeclock/punch")
def timeclock_punch(request: Request, db: Session = Depends(get_db)):
    """Ein-/Ausstempel-Button direkt im Dashboard - für den eingeloggten
    Nutzer, aber nur wenn das aktuelle Gerät autorisiert ist. So bleibt die
    Buchung weiterhin an ein konkretes Gerät gebunden, ganz ohne den Umweg
    über das separate Kiosk-Terminal (/timeclock/kiosk), wenn man ohnehin
    schon auf dem autorisierten Gerät eingeloggt ist."""
    user = require_login(request, db)
    if not device_is_authorized(request, db):
        return RedirectResponse("/", status_code=302)

    open_entry = (
        db.query(models.TimeEntry)
        .filter(models.TimeEntry.user_id == user.id, models.TimeEntry.clock_out.is_(None))
        .first()
    )
    if open_entry:
        open_entry.clock_out = ntptime.now_utc()
    else:
        db.add(models.TimeEntry(user_id=user.id, clock_in=ntptime.now_utc()))
    db.commit()
    return RedirectResponse("/", status_code=302)


# ---------- NFC-Tags (Verwaltung) ----------

def tag_target_url(request: Request, tag) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/room/{tag.target_room_id}"


@app.get("/admin/nfc-tags")
def admin_nfc_tags_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    tags = db.query(models.NfcTag).order_by(models.NfcTag.created_at.desc()).all()
    rooms = db.query(models.Room).order_by(models.Room.name).all()
    base = str(request.base_url).rstrip("/")

    target_urls = {f"room:{r.id}": f"{base}/room/{r.id}" for r in rooms}

    return templates.TemplateResponse("admin_nfc_tags.html", {
        "request": request,
        "user": admin,
        "tags": [
            {
                "tag": t,
                "url": tag_target_url(request, t),
                "last_verified_local": _aware(t.last_verified_at).astimezone(APP_TIMEZONE) if t.last_verified_at else None,
            }
            for t in tags
        ],
        "rooms": rooms,
        "target_urls_json": json.dumps(target_urls),
        "is_secure": request.url.scheme == "https",
    })


@app.post("/admin/nfc-tags/add")
def admin_nfc_tags_add(
    request: Request,
    target: str = Form(...),
    label: str = Form(""),
    uid: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin_or_shift_lead(request, db)
    if not target.startswith("room:"):
        return RedirectResponse("/admin/nfc-tags", status_code=302)
    target_type, target_room_id = "room", int(target.split(":", 1)[1])
    db.add(models.NfcTag(
        uid=uid.strip() or None,
        label=label.strip() or None,
        target_type=target_type,
        target_room_id=target_room_id,
        last_verified_at=ntptime.now_utc() if uid.strip() else None,
    ))
    db.commit()
    return RedirectResponse("/admin/nfc-tags", status_code=302)


@app.post("/admin/nfc-tags/{tag_id}/rescan")
def admin_nfc_tags_rescan(tag_id: int, request: Request, uid: str = Form(...), db: Session = Depends(get_db)):
    require_admin_or_shift_lead(request, db)
    tag = db.query(models.NfcTag).filter(models.NfcTag.id == tag_id).first()
    if tag:
        tag.uid = uid.strip() or None
        tag.last_verified_at = ntptime.now_utc()
        db.commit()
    return RedirectResponse("/admin/nfc-tags", status_code=302)


@app.post("/admin/nfc-tags/{tag_id}/edit")
def admin_nfc_tags_edit(tag_id: int, request: Request, label: str = Form(""), db: Session = Depends(get_db)):
    require_admin_or_shift_lead(request, db)
    tag = db.query(models.NfcTag).filter(models.NfcTag.id == tag_id).first()
    if tag:
        tag.label = label.strip() or None
        db.commit()
    return RedirectResponse("/admin/nfc-tags", status_code=302)


@app.post("/admin/nfc-tags/{tag_id}/delete")
def admin_nfc_tags_delete(tag_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    tag = db.query(models.NfcTag).filter(models.NfcTag.id == tag_id).first()
    if tag:
        db.delete(tag)
        db.commit()
    return RedirectResponse("/admin/nfc-tags", status_code=302)


# ---------- Inventar ----------

def distinct_inventory_values(db: Session, column: str) -> list[str]:
    col = getattr(models.InventoryItem, column)
    return sorted({v for (v,) in db.query(col).distinct().all() if v})


def user_can_see_inventory_item(user, item) -> bool:
    """Nur Admin sieht das komplette Inventar; alle anderen (auch Schichtleiter)
    nur Artikel ohne Gruppe (gemeinsames Inventar) sowie Artikel der eigenen
    Gruppe(n) - so sieht z.B. die Küche nicht das Inventar der Gastronomie."""
    if user and user.is_admin:
        return True
    if item.group_id is None:
        return True
    if not user:
        return False
    return any(g.id == item.group_id for g in user.groups)


def filter_inventory_for_user(items, user):
    return [i for i in items if user_can_see_inventory_item(user, i)]


_OG_IMAGE_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']', re.I),
    re.compile(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']', re.I),
]


def fetch_product_image(reorder_url: str) -> str | None:
    """Best-Effort-Versuch, das Vorschaubild (Open-Graph/Twitter-Meta) einer
    Produktseite zu laden und lokal zu speichern. Gibt bei jedem Fehler (kein
    Treffer, Timeout, kein Bild, zu groß, ...) einfach None zurück, damit ein
    fehlgeschlagener Abruf das Speichern des Kauflinks nie blockiert."""
    if urlparse(reorder_url).scheme not in ("http", "https"):
        return None
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ClubHUB/1.0)"}
        with httpx.Client(timeout=8.0, follow_redirects=True, headers=headers) as client:
            page = client.get(reorder_url)
            if page.status_code != 200 or "text/html" not in page.headers.get("content-type", ""):
                return None
            html = page.text[:300_000]  # Meta-Tags stehen im <head>, mehr braucht es nicht

            image_src = None
            for pattern in _OG_IMAGE_PATTERNS:
                match = pattern.search(html)
                if match:
                    image_src = match.group(1)
                    break
            if not image_src:
                return None

            image_url = urljoin(str(page.url), image_src)
            if urlparse(image_url).scheme not in ("http", "https"):
                return None

            img = client.get(image_url)
            if img.status_code != 200 or not img.headers.get("content-type", "").startswith("image/"):
                return None
            data = img.content
            if not data or len(data) > 8 * 1024 * 1024:
                return None

            ext = os.path.splitext(urlparse(image_url).path)[1][:10] or ".jpg"
            filename = f"{uuid.uuid4().hex}{ext}"
            with open(os.path.join(INVENTORY_IMAGES_DIR, filename), "wb") as f:
                f.write(data)
            return f"/uploads/inventory/{filename}"
    except Exception:
        return None


def _with_img_fetch_hint(target: str, failed: bool) -> str:
    """Hängt einen Hinweis-Query-Parameter an, wenn der automatische
    Produktbild-Abruf fehlgeschlagen ist (z.B. Bot-Schutz des Shops) -
    damit das nicht als stilles Nichts-passiert wirkt."""
    target = target if target.startswith("/") else "/admin/inventory"
    if not failed:
        return target
    sep = "&" if "?" in target else "?"
    return f"{target}{sep}img_fetch_failed=1"


def nav_badges(request: Request) -> dict:
    """Kleine Zähler für die Navigation (offene Meldungen, Artikel unter
    Mindestbestand) - läuft als Jinja-Global mit eigener kurzlebiger DB-Session,
    damit jede Seite (nicht nur das Dashboard) den aktuellen Stand zeigen kann,
    ohne dass jede Route ihn einzeln in den Kontext geben muss."""
    db = SessionLocal()
    try:
        user = get_current_user(request, db)
        if not user:
            return {"reports": 0, "inventory": 0}
        reports_open = db.query(models.Report).filter(models.Report.status != "done").count()
        items = filter_inventory_for_user(db.query(models.InventoryItem).all(), user)
        inventory_critical = sum(
            1 for i in items if compute_inventory_status(i)["status"] in ("low", "critical", "empty")
        )
        return {"reports": reports_open, "inventory": inventory_critical}
    finally:
        db.close()


templates.env.globals["nav_badges"] = nav_badges


def compute_inventory_consumption(item, now, months: int = 6) -> list[dict]:
    """Verbrauch (Summe negativer Buchungen, als positive Zahl) je Kalendermonat
    der letzten `months` Monate - Basis für Sparkline/Verlaufs-Chart, damit sich
    gute/schlechte Monate auf einen Blick erkennen lassen."""
    sums = {}
    for m in item.movements:
        if m.delta >= 0:
            continue
        ts = m.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        key = (ts.year, ts.month)
        sums[key] = sums.get(key, 0.0) + (-m.delta)

    keys = []
    year, month = now.year, now.month
    for _ in range(months):
        keys.append((year, month))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    keys.reverse()

    return [{"label": f"{mo:02d}/{str(y)[2:]}", "value": sums.get((y, mo), 0.0)} for (y, mo) in keys]


def build_consumption_chart(series: list[dict], width: int = 300, height: int = 90, pad: float = 6.0) -> dict:
    """Baut ein einfaches Linien-/Flächendiagramm (nur Geradensegmente, keine
    Bezier-Glättung) aus der monatlichen Verbrauchsreihe - als fertige SVG-Pfad-
    Strings, damit das Template nur noch plattes Markup rendern muss."""
    values = [b["value"] for b in series]
    max_val = max(values) if max(values) > 0 else 1.0
    n = len(series)
    step = (width - 2 * pad) / (n - 1) if n > 1 else 0

    points = []
    for i, v in enumerate(values):
        x = pad + i * step
        y = height - pad - (v / max_val) * (height - 2 * pad)
        points.append((round(x, 1), round(y, 1)))

    line_path = "M" + " L".join(f"{x},{y}" for x, y in points)
    baseline = height - pad
    area_path = f"{line_path} L{points[-1][0]},{baseline} L{points[0][0]},{baseline} Z"

    return {
        "width": width,
        "height": height,
        "line_path": line_path,
        "area_path": area_path,
        "points": points,
        "max_val": round(max_val, 1) if max(values) > 0 else 0,
    }


@app.get("/inventory")
def inventory_overview(request: Request, img_fetch_failed: str = "", db: Session = Depends(get_db)):
    user, redirect = require_login_page(request, db)
    if redirect:
        return redirect
    items = db.query(models.InventoryItem).order_by(models.InventoryItem.name).all()
    items = filter_inventory_for_user(items, user)
    now = ntptime.now_utc()
    inventory_status = {item.id: compute_inventory_status(item) for item in items}
    inventory_consumption = {item.id: compute_inventory_consumption(item, now) for item in items}
    inventory_chart = {item.id: build_consumption_chart(inventory_consumption[item.id]) for item in items}
    return templates.TemplateResponse("inventory.html", {
        "request": request,
        "user": user,
        "items": items,
        "inventory_status": inventory_status,
        "inventory_consumption": inventory_consumption,
        "inventory_chart": inventory_chart,
        "groups": db.query(models.Group).all(),
        "categories": sorted({i.category for i in items if i.category}),
        "locations": sorted({i.location for i in items if i.location}),
        "units": sorted({i.unit for i in items if i.unit}),
        "pack_units": sorted({i.pack_unit for i in items if i.pack_unit}),
        "img_fetch_failed": bool(img_fetch_failed),
    })


@app.post("/inventory/{item_id}/adjust")
def inventory_adjust(
    item_id: int,
    request: Request,
    delta: float = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    item = db.query(models.InventoryItem).filter(models.InventoryItem.id == item_id).first()
    if item and user_can_see_inventory_item(user, item):
        item.stock_current += delta
        db.add(models.InventoryMovement(item_id=item.id, user_id=user.id, delta=delta, note=note or None))
        if item.stock_current >= item.stock_min:
            item.notified = False
        db.commit()
    return RedirectResponse("/inventory", status_code=302)


@app.post("/inventory/{item_id}/image")
async def inventory_set_image(
    item_id: int,
    request: Request,
    image_file: UploadFile | None = File(None),
    image_url_input: str = Form(""),
    db: Session = Depends(get_db),
):
    actor = require_admin_or_shift_lead(request, db)
    item = db.query(models.InventoryItem).filter(models.InventoryItem.id == item_id).first()
    if item and user_can_see_inventory_item(actor, item):
        if image_file is not None and image_file.filename:
            data = await image_file.read()
            if data and (image_file.content_type or "").startswith("image/"):
                ext = os.path.splitext(image_file.filename)[1][:10] or ".jpg"
                filename = f"{uuid.uuid4().hex}{ext}"
                with open(os.path.join(INVENTORY_IMAGES_DIR, filename), "wb") as f:
                    f.write(data)
                item.image_url = f"/uploads/inventory/{filename}"
        elif image_url_input.strip().startswith(("http://", "https://")):
            item.image_url = image_url_input.strip()
        db.commit()
    return RedirectResponse("/inventory", status_code=302)


# ---------- Meldungen ----------

REPORT_PRIORITIES = ("critical", "high", "normal", "low")
REPORT_PRIORITY_RANK = {p: i for i, p in enumerate(REPORT_PRIORITIES)}
REPORT_CATEGORIES = ("defekt", "material", "reinigung", "sonstiges")
REPORT_STATUSES = ("open", "in_progress", "done")


def _sort_reports(reports):
    """Neueste zuerst, aber innerhalb dessen nach Priorität (kritisch zuerst) -
    zweistufig stabil sortiert, damit beides gleichzeitig gilt."""
    by_recency = sorted(reports, key=lambda r: r.created_at, reverse=True)
    return sorted(by_recency, key=lambda r: REPORT_PRIORITY_RANK.get(r.priority, 2))


def _aware(ts):
    """SQLite gibt Zeitstempel manchmal ohne tzinfo zurück, obwohl sie in UTC
    gespeichert wurden - hier konsistent nachrüsten, bevor mit ihnen gerechnet wird."""
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def compute_report_meta(r, now) -> dict:
    """Relative Zeitangaben ('vor X') sowie den Zeitpunkt der letzten Änderung
    (neuestes von Erstellung/Erledigung/letztem Kommentar) für die Meldungs-
    Übersicht und den Verlauf-Tab."""
    created_at = _aware(r.created_at)
    resolved_at = _aware(r.resolved_at)

    last_modified = created_at
    if resolved_at and resolved_at > last_modified:
        last_modified = resolved_at
    if r.comments:
        latest_comment = _aware(r.comments[-1].created_at)
        if latest_comment > last_modified:
            last_modified = latest_comment
    return {
        "created_rel": format_duration_de(now - created_at),
        "resolved_rel": format_duration_de(now - resolved_at) if resolved_at else None,
        "last_modified": last_modified,
    }


@app.get("/reports")
def reports_list(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_login_page(request, db)
    if redirect:
        return redirect
    reports = db.query(models.Report).all()
    now = ntptime.now_utc()
    open_reports = _sort_reports([r for r in reports if r.status != "done"])
    done_reports = _sort_reports([r for r in reports if r.status == "done"])
    report_meta = {r.id: compute_report_meta(r, now) for r in reports}
    return templates.TemplateResponse("reports.html", {
        "request": request,
        "user": user,
        "open_reports": open_reports,
        "done_reports": done_reports,
        "report_meta": report_meta,
        "rooms": db.query(models.Room).all(),
        "groups": db.query(models.Group).all(),
    })


@app.post("/reports")
async def reports_create(
    request: Request,
    background_tasks: BackgroundTasks,
    room_id: int = Form(...),
    comment: str = Form(...),
    priority: str = Form("normal"),
    category: str = Form("sonstiges"),
    assigned_group_id: str = Form(""),
    photos: list[UploadFile] = File([]),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)

    if priority not in REPORT_PRIORITIES:
        priority = "normal"
    if category not in REPORT_CATEGORIES:
        category = "sonstiges"
    assigned_group_id = int(assigned_group_id) if assigned_group_id.strip() else None

    report = models.Report(
        room_id=room_id, user_id=user.id, comment=comment, priority=priority, category=category,
        assigned_group_id=assigned_group_id,
    )
    db.add(report)
    db.commit()

    for photo in photos:
        if not photo or not photo.filename:
            continue
        data = await photo.read()
        if data and (photo.content_type or "").startswith("image/"):
            ext = os.path.splitext(photo.filename)[1][:10] or ".jpg"
            filename = f"{uuid.uuid4().hex}{ext}"
            with open(os.path.join(REPORT_PHOTOS_DIR, filename), "wb") as f:
                f.write(data)
            db.add(models.ReportPhoto(report_id=report.id, filename=filename))
    db.commit()

    # Gruppen inkl. Kanäle + Mitglieder/Push-Abos hier bereits vollständig laden
    # (nicht erst lazy in notify_group) - die Session ist geschlossen, sobald
    # der Background-Task nach dem Response tatsächlich läuft, sonst
    # DetachedInstanceError.
    group_loaders = (
        joinedload(models.Group.channels),
        joinedload(models.Group.users).joinedload(models.User.push_subscriptions),
    )
    room = (
        db.query(models.Room)
        .options(joinedload(models.Room.groups).options(*group_loaders))
        .filter(models.Room.id == room_id)
        .first()
    )
    if room:
        title = f"Neue Meldung: {room.name}"
        msg = comment if len(comment) <= 200 else comment[:197] + "…"
        if assigned_group_id:
            # Explizite Zuständigkeit gewählt (z.B. "Technik") - nur diese
            # Gruppe benachrichtigen, unabhängig davon, welche Gruppen dem
            # Bereich zugeordnet sind.
            assigned_group = (
                db.query(models.Group)
                .options(*group_loaders)
                .filter(models.Group.id == assigned_group_id)
                .first()
            )
            if assigned_group:
                background_tasks.add_task(notify_group, assigned_group, title, msg, "default", "/reports")
        else:
            for group in room.groups:
                background_tasks.add_task(notify_group, group, title, msg, "default", "/reports")

    return RedirectResponse("/reports", status_code=302)


@app.post("/reports/{report_id}/status")
def reports_set_status(report_id: int, request: Request, status: str = Form(...), db: Session = Depends(get_db)):
    user = require_login(request, db)
    if status not in REPORT_STATUSES:
        return RedirectResponse("/reports", status_code=302)
    report = db.query(models.Report).filter(models.Report.id == report_id).first()
    if report and report.status != status:
        if status == "done":
            report.resolved_at = ntptime.now_utc()
            report.resolved_by_id = user.id
        elif report.status == "done":
            # Wieder geöffnet (offen/in Bearbeitung) - Erledigt-Angaben sind
            # dann nicht mehr gültig, sonst zeigt die Meldung fälschlich noch
            # ein "erledigt am"-Datum.
            report.resolved_at = None
            report.resolved_by_id = None
        report.status = status
        db.commit()
    return RedirectResponse("/reports", status_code=302)


@app.post("/reports/{report_id}/comment")
def reports_add_comment(report_id: int, request: Request, text: str = Form(...), db: Session = Depends(get_db)):
    user = require_login(request, db)
    report = db.query(models.Report).filter(models.Report.id == report_id).first()
    if report and text.strip():
        db.add(models.ReportComment(report_id=report.id, user_id=user.id, text=text.strip()))
        db.commit()
    return RedirectResponse("/reports", status_code=302)


@app.post("/reports/{report_id}/assign")
def reports_assign(
    report_id: int, request: Request, assigned_group_id: str = Form(""), db: Session = Depends(get_db)
):
    require_login(request, db)
    report = db.query(models.Report).filter(models.Report.id == report_id).first()
    if report:
        report.assigned_group_id = int(assigned_group_id) if assigned_group_id.strip() else None
        db.commit()
    return RedirectResponse("/reports", status_code=302)


@app.post("/reports/{report_id}/photos")
async def reports_add_photos(
    report_id: int,
    request: Request,
    photos: list[UploadFile] = File([]),
    db: Session = Depends(get_db),
):
    require_login(request, db)
    report = db.query(models.Report).filter(models.Report.id == report_id).first()
    if report:
        for photo in photos:
            if not photo or not photo.filename:
                continue
            data = await photo.read()
            if data and (photo.content_type or "").startswith("image/"):
                ext = os.path.splitext(photo.filename)[1][:10] or ".jpg"
                filename = f"{uuid.uuid4().hex}{ext}"
                with open(os.path.join(REPORT_PHOTOS_DIR, filename), "wb") as f:
                    f.write(data)
                db.add(models.ReportPhoto(report_id=report.id, filename=filename))
        db.commit()
    return RedirectResponse("/reports", status_code=302)


# ---------- Push (Web Push) ----------

class PushSubscribeIn(BaseModel):
    endpoint: str
    keys: dict


class PushUnsubscribeIn(BaseModel):
    endpoint: str


@app.post("/push/subscribe")
def push_subscribe(payload: PushSubscribeIn, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    p256dh = payload.keys.get("p256dh", "")
    auth = payload.keys.get("auth", "")
    if not p256dh or not auth:
        return {"ok": False}

    existing = db.query(models.PushSubscription).filter(
        models.PushSubscription.endpoint == payload.endpoint
    ).first()
    user_agent = request.headers.get("user-agent", "")[:255]
    if existing:
        existing.user_id = user.id
        existing.p256dh = p256dh
        existing.auth = auth
        existing.user_agent = user_agent
    else:
        db.add(models.PushSubscription(
            user_id=user.id, endpoint=payload.endpoint, p256dh=p256dh, auth=auth, user_agent=user_agent,
        ))
    db.commit()
    return {"ok": True}


@app.post("/push/unsubscribe")
def push_unsubscribe(payload: PushUnsubscribeIn, request: Request, db: Session = Depends(get_db)):
    require_login(request, db)
    db.query(models.PushSubscription).filter(models.PushSubscription.endpoint == payload.endpoint).delete()
    db.commit()
    return {"ok": True}


# ---------- Historie ----------

@app.get("/history")
def history(request: Request, db: Session = Depends(get_db)):
    user, redirect = require_login_page(request, db)
    if redirect:
        return redirect
    completions = db.query(models.Completion).order_by(models.Completion.timestamp.desc()).limit(200).all()
    return templates.TemplateResponse("history.html", {
        "request": request,
        "user": user,
        "completions": completions,
    })


@app.post("/history/{completion_id}/delete")
def delete_completion(completion_id: int, request: Request, db: Session = Depends(get_db)):
    """Zieht eine versehentlich als erledigt markierte Aufgabe zurück - nur
    Admin, da das den Fälligkeits-Status der Aufgabe direkt beeinflusst."""
    require_admin(request, db)
    completion = db.query(models.Completion).filter(models.Completion.id == completion_id).first()
    if completion:
        db.delete(completion)
        db.commit()
    return RedirectResponse("/history", status_code=302)


# ---------- Login / Logout ----------

@app.get("/login")
def login_form(request: Request, next: str = "/", db: Session = Depends(get_db)):
    users = db.query(models.User).order_by(models.User.name).all()
    return templates.TemplateResponse("login.html", {
        "request": request, "user": None, "users": users, "next": next, "error": None,
    })


@app.post("/login")
def login_submit(
    request: Request,
    user_id: int = Form(...),
    pin: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    users = db.query(models.User).order_by(models.User.name).all()
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or not verify_pin(pin, user.pin_hash):
        return templates.TemplateResponse("login.html", {
            "request": request, "user": None, "users": users, "next": next,
            "error": "PIN ist falsch.",
        })
    request.session["user_id"] = user.id
    return RedirectResponse(next or "/", status_code=302)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)


# ---------- Profil (Selbstverwaltung) ----------

@app.get("/profile")
def profile_view(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": user,
        "pin_error": None,
        "pin_success": False,
    })


@app.post("/profile/avatar")
async def profile_set_avatar(
    request: Request,
    avatar_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    data = await avatar_file.read()
    if data and (avatar_file.content_type or "").startswith("image/"):
        ext = os.path.splitext(avatar_file.filename)[1][:10] or ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(AVATAR_IMAGES_DIR, filename), "wb") as f:
            f.write(data)
        user.avatar_url = f"/uploads/avatars/{filename}"
        db.commit()
    return RedirectResponse("/profile", status_code=302)


@app.post("/profile/pin")
def profile_change_pin(
    request: Request,
    pin: str = Form(...),
    pin_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    error = None
    if not pin.strip():
        error = "PIN darf nicht leer sein."
    elif pin != pin_confirm:
        error = "Die PINs stimmen nicht überein."
    else:
        user.pin_hash = hash_pin(pin)
        db.commit()
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": user,
        "pin_error": error,
        "pin_success": error is None,
    })


@app.post("/profile/pay")
def profile_set_pay(
    request: Request,
    hourly_wage: str = Form(""),
    target_hours_per_month: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    user.hourly_wage = float(hourly_wage) if hourly_wage.strip() else None
    user.target_hours_per_month = float(target_hours_per_month) if target_hours_per_month.strip() else None
    db.commit()
    return RedirectResponse("/profile", status_code=302)


# ---------- Admin ----------

@app.get("/admin")
def admin_home(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    return templates.TemplateResponse("admin_index.html", {
        "request": request,
        "user": admin,
        "users_count": db.query(models.User).count(),
        "groups_count": db.query(models.Group).count(),
        "groups_with_hours_count": db.query(models.Group).filter(models.Group.work_start_hour.isnot(None)).count(),
        "rooms_count": db.query(models.Room).count(),
        "tasks_count": db.query(models.Task).count(),
        "inventory_count": db.query(models.InventoryItem).count(),
        "low_stock_count": db.query(models.InventoryItem)
            .filter(models.InventoryItem.stock_current < models.InventoryItem.stock_min).count(),
        "channels_count": db.query(models.NotificationChannel).count(),
        "open_time_entries_count": db.query(models.TimeEntry).filter(models.TimeEntry.clock_out.is_(None)).count(),
        "nfc_tags_count": db.query(models.NfcTag).count(),
        "nfc_tags_unscanned_count": db.query(models.NfcTag).filter(models.NfcTag.uid.is_(None)).count(),
        "ntp_status": ntptime.status(),
    })


@app.get("/admin/users")
def admin_users_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    return templates.TemplateResponse("admin_users.html", {
        "request": request,
        "user": admin,
        "users": db.query(models.User).order_by(models.User.name).all(),
        "groups": db.query(models.Group).order_by(models.Group.name).all(),
    })


@app.get("/admin/groups")
def admin_groups_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    return templates.TemplateResponse("admin_groups.html", {
        "request": request,
        "user": admin,
        "groups": db.query(models.Group).order_by(models.Group.name).all(),
        "channels": db.query(models.NotificationChannel).all(),
    })


@app.get("/admin/rooms")
def admin_rooms_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    return templates.TemplateResponse("admin_rooms.html", {
        "request": request,
        "user": admin,
        "rooms": db.query(models.Room).order_by(models.Room.name).all(),
        "groups": db.query(models.Group).order_by(models.Group.name).all(),
    })


@app.get("/admin/tasks")
def admin_tasks_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    tasks = db.query(models.Task).join(models.Room).order_by(models.Room.name, models.Task.name).all()
    return templates.TemplateResponse("admin_tasks.html", {
        "request": request,
        "user": admin,
        "tasks": tasks,
        "rooms": db.query(models.Room).order_by(models.Room.name).all(),
    })


_INVENTORY_STATUS_ORDER = {"empty": 0, "critical": 1, "low": 2, "ok": 3}


@app.get("/admin/inventory")
def admin_inventory_page(
    request: Request, img_fetch_failed: str = "", sort: str = "status", db: Session = Depends(get_db)
):
    admin = require_admin_or_shift_lead(request, db)
    items = filter_inventory_for_user(db.query(models.InventoryItem).all(), admin)
    if sort == "name":
        items.sort(key=lambda i: i.name.lower())
    elif sort == "category":
        items.sort(key=lambda i: (i.category is None, (i.category or "").lower(), i.name.lower()))
    else:
        sort = "status"
        items.sort(key=lambda i: (_INVENTORY_STATUS_ORDER[compute_inventory_status(i)["status"]], i.name.lower()))
    return templates.TemplateResponse("admin_inventory.html", {
        "request": request,
        "user": admin,
        "inventory_items": items,
        "sort": sort,
        "groups": db.query(models.Group).all(),
        "categories": sorted({i.category for i in items if i.category}),
        "locations": sorted({i.location for i in items if i.location}),
        "units": sorted({i.unit for i in items if i.unit}),
        "pack_units": sorted({i.pack_unit for i in items if i.pack_unit}),
        "img_fetch_failed": bool(img_fetch_failed),
    })


def _parse_local_dt(value: str):
    """Wandelt den Wert eines <input type=datetime-local> (als APP_TIMEZONE
    interpretiert, da Admins hier lokale Uhrzeiten nachtragen) in ein
    UTC-Datetime für die Speicherung um."""
    if not value:
        return None
    naive = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    return naive.replace(tzinfo=APP_TIMEZONE).astimezone(timezone.utc)


@app.get("/admin/timeclock")
def admin_timeclock_page(request: Request, db: Session = Depends(get_db)):
    # Nur Admin, nicht Schichtleiter: Korrekturen wirken sich direkt auf den
    # berechneten Verdienst eines Nutzers aus (dieselbe Einschränkung wie
    # beim Stundensatz/Sollzeit selbst).
    admin = require_admin(request, db)
    users = db.query(models.User).order_by(models.User.name).all()
    entries_by_user = {}
    for u in users:
        entries = (
            db.query(models.TimeEntry)
            .filter(models.TimeEntry.user_id == u.id)
            .order_by(models.TimeEntry.clock_in.desc())
            .limit(30)
            .all()
        )
        entries_by_user[u.id] = [
            {
                "id": e.id,
                "clock_in_local": _aware(e.clock_in).astimezone(APP_TIMEZONE),
                "clock_out_local": _aware(e.clock_out).astimezone(APP_TIMEZONE) if e.clock_out else None,
                "open": e.clock_out is None,
            }
            for e in entries
        ]
    device = get_authorized_device(db)
    return templates.TemplateResponse("admin_timeclock.html", {
        "request": request,
        "user": admin,
        "users": users,
        "entries_by_user": entries_by_user,
        "device": device,
        "device_authorized_local": _aware(device.authorized_at).astimezone(APP_TIMEZONE) if device else None,
        "kiosk_url": f"{str(request.base_url).rstrip('/')}/timeclock/kiosk",
    })


@app.post("/admin/timeclock/authorize-device")
def admin_authorize_device(request: Request, label: str = Form(""), db: Session = Depends(get_db)):
    """Autorisiert GENAU das Gerät, von dem aus dieser Request kommt, für das
    Zeiterfassungs-Terminal - ein bereits autorisiertes Gerät verliert dabei
    automatisch seine Berechtigung (nur ein Token wird je gespeichert)."""
    admin = require_admin(request, db)
    token = secrets.token_hex(32)
    device = get_authorized_device(db)
    if device:
        device.token = token
        device.label = label.strip() or None
        device.authorized_at = ntptime.now_utc()
        device.authorized_by_id = admin.id
    else:
        db.add(models.AuthorizedDevice(
            token=token, label=label.strip() or None, authorized_by_id=admin.id,
        ))
    db.commit()
    response = RedirectResponse("/admin/timeclock", status_code=302)
    response.set_cookie(
        DEVICE_COOKIE_NAME, token,
        max_age=DEVICE_COOKIE_MAX_AGE, httponly=True, samesite="lax",
    )
    return response


@app.post("/admin/timeclock/revoke-device")
def admin_revoke_device(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    db.query(models.AuthorizedDevice).delete()
    db.commit()
    return RedirectResponse("/admin/timeclock", status_code=302)


@app.post("/admin/timeclock/{user_id}/add")
def admin_timeclock_add(
    user_id: int,
    request: Request,
    clock_in: str = Form(...),
    clock_out: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user:
        db.add(models.TimeEntry(
            user_id=user.id,
            clock_in=_parse_local_dt(clock_in),
            clock_out=_parse_local_dt(clock_out),
        ))
        db.commit()
    return RedirectResponse("/admin/timeclock", status_code=302)


@app.post("/admin/timeclock/{entry_id}/edit")
def admin_timeclock_edit(
    entry_id: int,
    request: Request,
    clock_in: str = Form(...),
    clock_out: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    entry = db.query(models.TimeEntry).filter(models.TimeEntry.id == entry_id).first()
    if entry:
        entry.clock_in = _parse_local_dt(clock_in)
        entry.clock_out = _parse_local_dt(clock_out)
        db.commit()
    return RedirectResponse("/admin/timeclock", status_code=302)


@app.post("/admin/timeclock/{entry_id}/delete")
def admin_timeclock_delete(entry_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    entry = db.query(models.TimeEntry).filter(models.TimeEntry.id == entry_id).first()
    if entry:
        db.delete(entry)
        db.commit()
    return RedirectResponse("/admin/timeclock", status_code=302)


@app.get("/admin/notifications")
def admin_notifications_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    return templates.TemplateResponse("admin_notifications.html", {
        "request": request,
        "user": admin,
        "channels": db.query(models.NotificationChannel).all(),
    })


def _backup_health(scheduled_backups: list[dict]) -> bool:
    """True, wenn die letzte automatische Sicherung ungewöhnlich lange her ist
    (z.B. weil der Scheduler mal nicht gelaufen ist) - Basis für die
    Warnanzeige in der Systemverwaltung. Schwelle: größter Abstand zwischen
    zwei geplanten Uhrzeiten am Tag + 2 Stunden Toleranz für Jitter/Neustarts."""
    hours = sorted({int(h) for h in BACKUP_SCHEDULE_HOURS.split(",") if h.strip() != ""})
    if not hours:
        return False
    gaps = [hours[i + 1] - hours[i] for i in range(len(hours) - 1)]
    gaps.append(24 - hours[-1] + hours[0])
    threshold = timedelta(hours=max(gaps) + 2)
    if not scheduled_backups:
        return True
    return (ntptime.now_utc() - scheduled_backups[0]["timestamp"]) > threshold


def _admin_system_context(request: Request, admin, restore_error: str | None = None) -> dict:
    scheduled_backups = [
        {**b, "timestamp_local": b["timestamp"].astimezone(APP_TIMEZONE)}
        for b in backup.list_scheduled_backups()
    ]
    return {
        "request": request,
        "user": admin,
        "app_timezone": os.environ.get("APP_TIMEZONE", "Europe/Berlin"),
        "ntp_status": ntptime.status(),
        "scheduled_backups": scheduled_backups,
        "backup_schedule_hours": BACKUP_SCHEDULE_HOURS,
        "backup_retention_days": BACKUP_RETENTION_DAYS,
        "backup_stale": _backup_health(scheduled_backups),
        "restore_error": restore_error,
    }


@app.get("/admin/system")
def admin_system_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    return templates.TemplateResponse("admin_system.html", _admin_system_context(request, admin))


@app.post("/admin/system/resync-ntp")
def admin_resync_ntp(request: Request, db: Session = Depends(get_db)):
    require_admin_or_shift_lead(request, db)
    ntptime.sync()
    return RedirectResponse("/admin/system", status_code=302)


@app.get("/admin/system/backup")
def admin_download_backup(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = backup.create_backup_bytes()
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{backup.backup_filename()}"'},
    )


@app.get("/admin/system/scheduled-backup/{filename}/download")
def admin_download_scheduled_backup(filename: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    path = backup.scheduled_backup_path(filename)
    if not path:
        raise HTTPException(status_code=404)
    with open(path, "rb") as f:
        data = f.read()
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _trigger_restart():
    # SIGTERM statt hartem os._exit(), damit uvicorn sauber herunterfährt;
    # Docker startet den Container per restart-Policy automatisch neu und
    # durchläuft dabei die normalen Startup-Migrationen gegen die neue Datei.
    os.kill(os.getpid(), signal.SIGTERM)


def _restore_success_response() -> HTMLResponse:
    return HTMLResponse("""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="6;url=/admin/system">
<title>Wiederherstellung – ClubHUB</title></head>
<body style="font-family:ui-sans-serif,system-ui,sans-serif;background:#12161f;color:#e2e8f0;
             display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
<div style="text-align:center;max-width:24rem;padding:1.5rem;">
  <p>Datenbank wiederhergestellt. Die Anwendung startet neu…</p>
  <p style="opacity:.6;font-size:.85em;margin-top:.5rem;">Diese Seite lädt in wenigen Sekunden automatisch neu.</p>
</div>
</body></html>""")


@app.post("/admin/system/restore")
async def admin_restore_backup(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    admin = require_admin(request, db)
    data = await file.read()
    error = backup.restore_from_bytes(data)
    if error:
        return templates.TemplateResponse("admin_system.html", _admin_system_context(request, admin, error))
    background_tasks.add_task(_trigger_restart)
    return _restore_success_response()


@app.post("/admin/system/scheduled-backup/{filename}/restore")
def admin_restore_scheduled_backup(
    filename: str, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    admin = require_admin(request, db)
    path = backup.scheduled_backup_path(filename)
    if not path:
        raise HTTPException(status_code=404)
    error = backup.restore_from_path(path)
    if error:
        return templates.TemplateResponse("admin_system.html", _admin_system_context(request, admin, error))
    background_tasks.add_task(_trigger_restart)
    return _restore_success_response()


@app.post("/admin/groups")
def admin_add_group(
    request: Request,
    name: str = Form(...),
    channel_ids: list[int] = Form([]),
    work_start_hour: str = Form(""),
    work_end_hour: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin_or_shift_lead(request, db)
    channels = db.query(models.NotificationChannel).filter(models.NotificationChannel.id.in_(channel_ids)).all()
    db.add(models.Group(
        name=name,
        channels=channels,
        work_start_hour=int(work_start_hour) if work_start_hour else None,
        work_end_hour=int(work_end_hour) if work_end_hour else None,
    ))
    db.commit()
    return RedirectResponse("/admin/groups", status_code=302)


@app.post("/admin/groups/{group_id}/edit")
def admin_edit_group(
    group_id: int,
    request: Request,
    name: str = Form(...),
    channel_ids: list[int] = Form([]),
    work_start_hour: str = Form(""),
    work_end_hour: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin_or_shift_lead(request, db)
    group = db.query(models.Group).filter(models.Group.id == group_id).first()
    if group:
        group.name = name
        group.channels = db.query(models.NotificationChannel).filter(models.NotificationChannel.id.in_(channel_ids)).all()
        group.work_start_hour = int(work_start_hour) if work_start_hour else None
        group.work_end_hour = int(work_end_hour) if work_end_hour else None
        db.commit()
    return RedirectResponse("/admin/groups", status_code=302)


@app.post("/admin/groups/{group_id}/delete")
def admin_delete_group(group_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    group = db.query(models.Group).filter(models.Group.id == group_id).first()
    if group:
        db.delete(group)
        db.commit()
    return RedirectResponse("/admin/groups", status_code=302)


@app.post("/admin/channels")
def admin_add_channel(
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    target: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin_or_shift_lead(request, db)
    db.add(models.NotificationChannel(name=name, type=type, target=target or None))
    db.commit()
    return RedirectResponse("/admin/notifications", status_code=302)


@app.post("/admin/channels/{channel_id}/edit")
def admin_edit_channel(
    channel_id: int,
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    target: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin_or_shift_lead(request, db)
    channel = db.query(models.NotificationChannel).filter(models.NotificationChannel.id == channel_id).first()
    if channel:
        channel.name = name
        channel.type = type
        channel.target = target or None
        db.commit()
    return RedirectResponse("/admin/notifications", status_code=302)


@app.post("/admin/channels/{channel_id}/delete")
def admin_delete_channel(channel_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    channel = db.query(models.NotificationChannel).filter(models.NotificationChannel.id == channel_id).first()
    if channel:
        db.delete(channel)
        db.commit()
    return RedirectResponse("/admin/notifications", status_code=302)


@app.post("/admin/users")
def admin_add_user(
    request: Request,
    name: str = Form(...),
    pin: str = Form(...),
    group_id: str = Form(""),
    role: str = Form("mitarbeiter"),
    target_hours_per_month: str = Form(""),
    db: Session = Depends(get_db),
):
    actor = require_admin_or_shift_lead(request, db)
    groups = []
    if group_id:
        group = db.query(models.Group).filter(models.Group.id == int(group_id)).first()
        if group:
            groups = [group]
    # Nur Admins dürfen beim Anlegen direkt Admin-/Schichtleiter-Rechte vergeben;
    # ein Schichtleiter legt immer nur normale Mitarbeiter-Konten an, auch wenn
    # im Formular (z.B. per direktem POST) etwas anderes übermittelt wird.
    user = models.User(
        name=name,
        pin_hash=hash_pin(pin),
        is_admin=actor.is_admin and role == "admin",
        is_shift_lead=actor.is_admin and role == "schichtleiter",
        groups=groups,
    )
    # Hier nur beim Anlegen durch einen vollen Admin setzbar (Schichtleiter
    # legen nur einfache Konten an) - der Nutzer selbst kann sie danach jederzeit
    # im eigenen Profil anpassen, Stundensatz sowieso nur dort.
    if actor.is_admin:
        user.target_hours_per_month = float(target_hours_per_month) if target_hours_per_month else None
    db.add(user)
    db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@app.post("/admin/users/{user_id}/edit")
def admin_edit_user(
    user_id: int,
    request: Request,
    name: str = Form(...),
    pin: str = Form(""),
    group_id: str = Form(""),
    role: str = Form(""),
    target_hours_per_month: str = Form(""),
    db: Session = Depends(get_db),
):
    actor = require_admin_or_shift_lead(request, db)
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if target:
        # Schichtleiter dürfen Admin-Konten gar nicht anfassen - sonst könnten sie
        # sich per PIN-Reset eines Admin-Kontos faktisch selbst zum Admin machen.
        if target.is_admin and not actor.is_admin:
            return RedirectResponse("/admin/users", status_code=302)

        target.name = name
        if pin:
            target.pin_hash = hash_pin(pin)
        group = db.query(models.Group).filter(models.Group.id == int(group_id)).first() if group_id else None
        target.groups = [group] if group else []

        # Nur ein Admin darf Rollen ändern (Admin/Schichtleiter vergeben oder
        # entziehen); bei einem Schichtleiter als Akteur bleibt die Rolle des
        # bearbeiteten Nutzers unverändert, egal was im Formular ankommt.
        if actor.is_admin:
            desired_role = role if role in ("mitarbeiter", "schichtleiter", "admin") else "mitarbeiter"
            other_admins = db.query(models.User).filter(models.User.is_admin == True, models.User.id != user_id).count()
            if target.is_admin and desired_role != "admin" and other_admins == 0:
                desired_role = "admin"  # letzten Admin nicht versehentlich entmachten
            target.is_admin = desired_role == "admin"
            target.is_shift_lead = desired_role == "schichtleiter"
            # Hier nur von einem vollen Admin änderbar; der Nutzer selbst kann sie
            # zusätzlich im eigenen Profil anpassen - beide pflegen denselben Wert.
            target.target_hours_per_month = float(target_hours_per_month) if target_hours_per_month else None
        db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@app.post("/admin/users/{user_id}/delete")
def admin_delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if target and target.id != admin.id:
        other_admins = db.query(models.User).filter(models.User.is_admin == True, models.User.id != user_id).count()
        if not target.is_admin or other_admins > 0:
            db.delete(target)
            db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@app.post("/admin/rooms")
def admin_add_room(
    request: Request,
    name: str = Form(...),
    group_ids: list[int] = Form([]),
    db: Session = Depends(get_db),
):
    require_admin_or_shift_lead(request, db)
    groups = db.query(models.Group).filter(models.Group.id.in_(group_ids)).all()
    db.add(models.Room(name=name, groups=groups))
    db.commit()
    return RedirectResponse("/admin/rooms", status_code=302)


@app.post("/admin/rooms/{room_id}/edit")
def admin_edit_room(
    room_id: int,
    request: Request,
    name: str = Form(...),
    group_ids: list[int] = Form([]),
    db: Session = Depends(get_db),
):
    require_admin_or_shift_lead(request, db)
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if room:
        room.name = name
        room.groups = db.query(models.Group).filter(models.Group.id.in_(group_ids)).all()
        db.commit()
    return RedirectResponse("/admin/rooms", status_code=302)


@app.post("/admin/rooms/{room_id}/delete")
def admin_delete_room(room_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if room:
        db.delete(room)
        db.commit()
    return RedirectResponse("/admin/rooms", status_code=302)


@app.post("/admin/tasks")
def admin_add_task(
    request: Request,
    room_id: int = Form(...),
    name: str = Form(...),
    interval_hours: float = Form(...),
    warn_hours: float = Form(5.0),
    db: Session = Depends(get_db),
):
    require_admin_or_shift_lead(request, db)
    db.add(models.Task(room_id=room_id, name=name, interval_hours=interval_hours, warn_hours=warn_hours))
    db.commit()
    return RedirectResponse("/admin/tasks", status_code=302)


@app.post("/admin/tasks/{task_id}/edit")
def admin_edit_task(
    task_id: int,
    request: Request,
    room_id: int = Form(...),
    name: str = Form(...),
    interval_hours: float = Form(...),
    warn_hours: float = Form(5.0),
    db: Session = Depends(get_db),
):
    require_admin_or_shift_lead(request, db)
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if task:
        task.room_id = room_id
        task.name = name
        task.interval_hours = interval_hours
        task.warn_hours = warn_hours
        db.commit()
    return RedirectResponse("/admin/tasks", status_code=302)


@app.post("/admin/tasks/{task_id}/delete")
def admin_delete_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if task:
        db.delete(task)
        db.commit()
    return RedirectResponse("/admin/tasks", status_code=302)


@app.post("/admin/inventory")
def admin_add_inventory_item(
    request: Request,
    name: str = Form(...),
    unit: str = Form(""),
    unit_plural: str = Form(""),
    pack_size: str = Form(""),
    pack_unit: str = Form(""),
    stock_current: float = Form(0.0),
    stock_min: float = Form(0.0),
    stock_critical: str = Form(""),
    category: str = Form(""),
    location: str = Form(""),
    reorder_url: str = Form(""),
    group_id: str = Form(""),
    next: str = Form("/admin/inventory"),
    db: Session = Depends(get_db),
):
    require_admin_or_shift_lead(request, db)
    reorder_url = reorder_url or None
    # Kauflink direkt beim Anlegen gesetzt -> automatisch das Produktbild
    # der verlinkten Seite als Artikelbild übernehmen, falls auffindbar.
    image_url = fetch_product_image(reorder_url) if reorder_url else None
    image_fetch_failed = bool(reorder_url) and not image_url
    db.add(models.InventoryItem(
        name=name,
        unit=unit or None,
        unit_plural=unit_plural or None,
        pack_size=float(pack_size) if pack_size else None,
        pack_unit=pack_unit or None,
        stock_current=stock_current,
        stock_min=stock_min,
        stock_critical=float(stock_critical) if stock_critical else None,
        category=category or None,
        location=location or None,
        reorder_url=reorder_url,
        image_url=image_url,
        group_id=int(group_id) if group_id else None,
    ))
    db.commit()
    return RedirectResponse(_with_img_fetch_hint(next, image_fetch_failed), status_code=302)


@app.post("/admin/inventory/{item_id}/edit")
def admin_edit_inventory_item(
    item_id: int,
    request: Request,
    name: str = Form(...),
    unit: str = Form(""),
    unit_plural: str = Form(""),
    pack_size: str = Form(""),
    pack_unit: str = Form(""),
    stock_current: float = Form(0.0),
    stock_min: float = Form(0.0),
    stock_critical: str = Form(""),
    category: str = Form(""),
    location: str = Form(""),
    reorder_url: str = Form(""),
    group_id: str = Form(""),
    next: str = Form("/admin/inventory"),
    db: Session = Depends(get_db),
):
    actor = require_admin_or_shift_lead(request, db)
    item = db.query(models.InventoryItem).filter(models.InventoryItem.id == item_id).first()
    if item and not user_can_see_inventory_item(actor, item):
        item = None
    if item:
        item.name = name
        item.unit = unit or None
        item.unit_plural = unit_plural or None
        item.pack_size = float(pack_size) if pack_size else None
        item.pack_unit = pack_unit or None
        item.stock_current = stock_current
        item.stock_min = stock_min
        item.stock_critical = float(stock_critical) if stock_critical else None
        item.category = category or None
        item.location = location or None
        new_reorder_url = reorder_url or None
        # Nur beim tatsächlichen Ändern/Neuzuweisen des Kauflinks neu abrufen -
        # so wird ein manuell gesetztes Bild nicht bei jedem Speichern überschrieben.
        image_fetch_failed = False
        if new_reorder_url and new_reorder_url != item.reorder_url:
            fetched_image = fetch_product_image(new_reorder_url)
            if fetched_image:
                item.image_url = fetched_image
            else:
                image_fetch_failed = True
        item.reorder_url = new_reorder_url
        item.group_id = int(group_id) if group_id else None
        if item.stock_current >= item.stock_min:
            item.notified = False
        db.commit()
        return RedirectResponse(_with_img_fetch_hint(next, image_fetch_failed), status_code=302)
    return RedirectResponse(next if next.startswith("/") else "/admin/inventory", status_code=302)


@app.post("/admin/inventory/{item_id}/delete")
def admin_delete_inventory_item(item_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    item = db.query(models.InventoryItem).filter(models.InventoryItem.id == item_id).first()
    if item:
        db.delete(item)
        db.commit()
    return RedirectResponse("/admin/inventory", status_code=302)
