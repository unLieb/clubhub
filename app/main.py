import hashlib
import json
import logging
import os
import re
import secrets
import signal
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, quote, urlencode

import httpx
from fastapi import FastAPI, Request, Depends, Form, File, UploadFile, BackgroundTasks, HTTPException, Query
from fastapi.responses import RedirectResponse, Response, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from sqlalchemy import text, or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from .database import Base, engine, get_db, DB_PATH, SessionLocal
from . import models
from . import ntptime
from . import backup
from . import data_export
from . import version
from . import push
from . import pdf_export
from .auth import (
    hash_password, verify_password, find_user_by_identifier, get_current_user, require_login,
    require_admin, require_admin_or_shift_lead, require_admin_or_developer, require_staff_or_developer,
)
from .status import task_status, compute_inventory_status
from .scheduler import start_scheduler, APP_TIMEZONE, BACKUP_SCHEDULE_HOURS, BACKUP_RETENTION_DAYS
from .notifications import notify_group, notify_user

Base.metadata.create_all(bind=engine)


class _HealthzLogFilter(logging.Filter):
    """Blendet den vom Docker-HEALTHCHECK alle 30s erzeugten Zugriff auf
    /healthz aus dem Access-Log aus, damit dieser nicht die echten Zugriffe
    darin ertränkt."""
    def filter(self, record: logging.LogRecord) -> bool:
        return "/healthz" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_HealthzLogFilter())

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
SETUP_PHOTOS_DIR = os.path.join(UPLOADS_DIR, "setups")
os.makedirs(SETUP_PHOTOS_DIR, exist_ok=True)
FEEDBACK_PHOTOS_DIR = os.path.join(UPLOADS_DIR, "feedback")
os.makedirs(FEEDBACK_PHOTOS_DIR, exist_ok=True)
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


def _relative_date_de(ts_local):
    """Formatiert ein bereits lokalisiertes Datum (siehe localtime-Filter)
    als 'heute'/'gestern', sonst als TT.MM.JJJJ."""
    if ts_local is None:
        return None
    today = ntptime.now_utc().astimezone(APP_TIMEZONE).date()
    d = ts_local.date()
    if d == today:
        return "heute"
    if d == today - timedelta(days=1):
        return "gestern"
    return ts_local.strftime("%d.%m.%Y")


templates.env.filters["reldate"] = _relative_date_de

_WEEKDAY_ABBR_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _weekday_label_de(raw):
    """Kurzform der aktiven Wochentage einer Aufgabe für Badges, z.B. 'Mo–Fr'
    oder 'Sa+So'. None (keine Einschränkung) -> kein Label."""
    if not raw:
        return None
    days = sorted(int(d) for d in raw.split(",") if d.strip() != "")
    if not days or len(days) == 7:
        return None
    if days == list(range(days[0], days[-1] + 1)) and len(days) > 1:
        return f"{_WEEKDAY_ABBR_DE[days[0]]}–{_WEEKDAY_ABBR_DE[days[-1]]}"
    return "+".join(_WEEKDAY_ABBR_DE[d] for d in days)


templates.env.filters["weekday_label"] = _weekday_label_de


@app.get("/healthz")
def healthz(db: Session = Depends(get_db)):
    """Für Docker HEALTHCHECK (siehe Dockerfile) - prüft neben dem laufenden
    Prozess auch, ob die Datenbank tatsächlich erreichbar ist, statt nur
    "der Uvicorn-Prozess lebt noch" zu bestätigen."""
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/changelog")
def changelog_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("changelog.html", {
        "request": request,
        "user": get_current_user(request, db),
        "entries": version.CHANGELOG,
    })


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


def _migrate_report_room_optional(db: Session):
    """Macht reports.room_id nullable (fuer die Kategorie "anschaffung" -
    Materialwunsch/Anschaffung, an keinen Bereich gebunden, siehe
    models.Report). SQLite kann eine NOT-NULL-Einschränkung nicht per
    ALTER TABLE entfernen, deshalb per Standard-SQLite-Rebuild: Tabelle
    umbenennen, anhand der (jetzt nullable) SQLAlchemy-Metadata neu anlegen,
    Daten 1:1 kopieren, alte Tabelle verwerfen. Greift nur, wenn die
    bestehende Spalte tatsächlich noch NOT NULL ist - bei einer frischen
    Installation legt create_all() die Tabelle bereits korrekt an."""
    cols = db.execute(text("PRAGMA table_info(reports)")).fetchall()
    room_col = next((c for c in cols if c[1] == "room_id"), None)
    if room_col is None or room_col[3] == 0:
        return
    db.execute(text("ALTER TABLE reports RENAME TO reports_pre_optional_room"))
    db.commit()
    models.Report.__table__.create(bind=engine)
    column_names = ", ".join(c.name for c in models.Report.__table__.columns)
    db.execute(text(
        f"INSERT INTO reports ({column_names}) SELECT {column_names} FROM reports_pre_optional_room"
    ))
    db.execute(text("DROP TABLE reports_pre_optional_room"))
    db.commit()


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


def _migrate_user_password_rename(db: Session):
    """pin_hash -> password_hash: Login wurde von einer kurzen PIN auf ein
    reguläres Passwort umgestellt, damit Mitarbeiter ihr bestehendes Passwort
    aus der betrieblich genutzten Zeiterfassung weiterverwenden können.
    Bestehende bcrypt-Hashes bleiben unverändert gültig, nur die Spalte
    heißt um (kein erzwungenes Zurücksetzen nötig)."""
    existing = {row[1] for row in db.execute(text("PRAGMA table_info(users)")).fetchall()}
    if "pin_hash" in existing and "password_hash" not in existing:
        db.execute(text("ALTER TABLE users RENAME COLUMN pin_hash TO password_hash"))
        db.commit()


def _migrate_user_developer_role(db: Session):
    """Neue Rolle 'Entwickler' (kein Altdaten-Bezug, Standard: False)."""
    _ensure_column(db, "users", "is_developer", "INTEGER DEFAULT 0")


def _migrate_task_note(db: Session):
    """Neue optionale Notiz je Aufgabe (kein Altdaten-Bezug)."""
    _ensure_column(db, "tasks", "note", "TEXT")


def _migrate_task_group_key(db: Session):
    """Neuer optionaler Gruppierungs-Schlüssel für baugleiche Aufgaben in
    mehreren Bereichen (kein Altdaten-Bezug)."""
    _ensure_column(db, "tasks", "group_key", "TEXT")


def _migrate_task_active_weekdays(db: Session):
    """Neue optionale Wochentag-Einschränkung je Aufgabe (kein Altdaten-Bezug,
    NULL = weiterhin an jedem Tag aktiv wie bisher)."""
    _ensure_column(db, "tasks", "active_weekdays", "TEXT")


def _migrate_detach_task_groups(db: Session):
    """Einmalige Migration: Aufgabenverwaltung ist jetzt raumzentriert
    (Kopieren statt Verlinken, siehe admin_rooms.html) statt über verlinkte
    baugleiche Aufgaben mehrerer Bereiche. Die einzelnen Bereichs-Instanzen
    waren schon vorher eigenständige Task-Zeilen mit eigener Erledigungs-
    Historie (group_key hat nie Zeilen zusammengelegt, nur die gemeinsame
    Bearbeitung gesteuert) - hier wird nur noch diese Verknüpfung gelöst,
    damit Bearbeiten/Löschen ab sofort ausschließlich die jeweilige
    Bereichs-Instanz betrifft. Kein Datenverlust, keine neuen Zeilen nötig."""
    db.query(models.Task).filter(models.Task.group_key.isnot(None)).update({"group_key": None})
    db.commit()


def _migrate_room_setup_events(db: Session):
    """Führt bestehende Aufbauten (früher: eigener Name direkt am RoomSetup,
    fest an genau einen Bereich gebunden) ins EventSetup-Modell über - ein
    Event kann jetzt mehrere Bereichs-Einträge mit je eigenen Fotos/Notizen
    haben. Für jeden bisherigen Aufbau ohne event_id wird ein eigenes Event
    mit demselben Namen angelegt (1:1), damit nichts verloren geht."""
    _ensure_column(db, "room_setups", "event_id", "INTEGER")
    # Über die ORM statt raw SQL lesen, damit created_at als echtes
    # datetime-Objekt kommt (raw SQL liefert hier nur den rohen SQLite-Text,
    # das crasht beim Insert ins neue DateTime-Feld von EventSetup).
    orphans = db.query(models.RoomSetup).filter(models.RoomSetup.event_id.is_(None)).all()
    for setup in orphans:
        event = models.EventSetup(name=setup.name, created_by_id=setup.created_by_id, created_at=setup.created_at)
        db.add(event)
        db.flush()
        setup.event_id = event.id
    db.commit()


def _migrate_user_flat_rate(db: Session):
    """Neues Pauschalkraft-Flag (kein Altdaten-Bezug, Standard: False)."""
    _ensure_column(db, "users", "is_flat_rate", "INTEGER DEFAULT 0")


def _migrate_time_entry_audit_hash(db: Session):
    """Neue Hash-Kette-Spalte auf dem Änderungsprotokoll (kein Altdaten-Bezug,
    bestehende Einträge ohne Hash werden von verify_audit_chain() einfach als
    Kette ab dort neu gestartet behandelt, siehe dort)."""
    _ensure_column(db, "time_entry_audits", "hash", "TEXT")


def _migrate_user_personnel_number(db: Session):
    """Optionale Personalnummer je Nutzer, zusätzlich zum Namen als Login-
    Kennung nutzbar. Eindeutigkeit über einen separaten Unique-Index statt
    Column(unique=True), da create_all() bestehende Tabellen nicht ändert -
    ein Index lässt sich dagegen bei jedem Start idempotent nachziehen
    (IF NOT EXISTS), auch für schon bestehende Installationen."""
    _ensure_column(db, "users", "personnel_number", "TEXT")
    db.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_personnel_number "
        "ON users(personnel_number)"
    ))
    db.commit()


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
        _migrate_report_room_optional(db)
        _migrate_report_photos(db)
        _migrate_inventory_extras(db)
        _migrate_inventory_pack_size(db)
        _migrate_inventory_unit_plural(db)
        _migrate_inventory_critical_stock(db)
        _migrate_user_time_tracking(db)
        _migrate_remove_timeclock_nfc_tags(db)
        _migrate_user_avatar(db)
        _migrate_user_password_rename(db)
        _migrate_user_personnel_number(db)
        _migrate_time_entry_audit_hash(db)
        _migrate_user_developer_role(db)
        _migrate_user_flat_rate(db)
        _migrate_room_setup_events(db)
        _migrate_task_note(db)
        _migrate_task_group_key(db)
        _migrate_task_active_weekdays(db)
        _migrate_detach_task_groups(db)
    finally:
        db.close()

    # Ersten Admin anlegen, falls die Datenbank noch leer ist
    db = next(get_db())
    try:
        if db.query(models.User).count() == 0:
            admin_name = os.environ.get("INITIAL_ADMIN_NAME", "Admin")
            admin_password = os.environ.get("INITIAL_ADMIN_PASSWORD", "0000")
            db.add(models.User(name=admin_name, password_hash=hash_password(admin_password), is_admin=True))
            db.commit()
            print(f"[Setup] Erster Admin angelegt: '{admin_name}' mit Passwort '{admin_password}' "
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


def device_label(user_agent: str | None) -> str:
    """Grobe, menschenlesbare Herkunft einer Push-Subscription aus dem User-Agent
    (z.B. für "Meine Geräte" in /profile) - hilft zu erkennen, welches Gerät
    mit welchem Account verknüpft ist, wenn man wie hier mit mehreren Geräten
    und Accounts parallel testet. Chromium-Browser wie Vivaldi identifizieren
    sich standardmäßig nicht eigenständig im User-Agent (erscheinen als
    "Chrome"), daher keine exakte Browsererkennung möglich."""
    if not user_agent:
        return "Unbekanntes Gerät"
    ua = user_agent
    if "Android" in ua:
        os_label = "Android"
    elif "iPhone" in ua:
        os_label = "iPhone"
    elif "iPad" in ua:
        os_label = "iPad"
    elif "Windows" in ua:
        os_label = "Windows"
    elif "Macintosh" in ua:
        os_label = "Mac"
    elif "Linux" in ua:
        os_label = "Linux"
    else:
        os_label = "Unbekanntes Gerät"

    if "Firefox" in ua:
        browser = "Firefox"
    elif "Edg/" in ua:
        browser = "Edge"
    elif "OPR/" in ua or "Opera" in ua:
        browser = "Opera"
    elif "Chrome" in ua:
        browser = "Chrome/Vivaldi"
    elif "Safari" in ua:
        browser = "Safari"
    else:
        browser = ""
    return f"{os_label} · {browser}" if browser else os_label


templates.env.globals["device_label"] = device_label


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
    # Bereiche mit mindestens einer fälligen/überfälligen TÄGLICHEN Aufgabe -
    # Basis für den "Tägliche erledigen"-Sammel-Button auf der Bereichs-Card,
    # der bewusst nur die Tagesroutine abhakt statt aller Turnusse (z.B. eine
    # überfällige monatliche Aufgabe soll dabei nicht versehentlich mit
    # abgehakt werden, siehe Nutzer-Feedback nach der ersten Version).
    daily_due_room_ids = set()
    daily_overdue_count = 0

    for room in rooms:
        worst = None
        last_completed = None
        last_user = None
        for task in room.tasks:
            s = task_status(task, now)
            if worst is None or STATUS_RANK[s["status"]] > STATUS_RANK[worst["status"]]:
                worst = s
            if task.interval_hours == 24 and s["status"] in ("yellow", "red"):
                daily_due_room_ids.add(room.id)
                if s["status"] == "red":
                    daily_overdue_count += 1
            if s["status"] == "yellow":
                due_soon_count += 1
            elif s["status"] == "red":
                overdue_count += 1
                # "Überfällig seit X" wäre bei nie erledigten Aufgaben irreführend -
                # due_at wird dort bei jeder Berechnung neu auf "jetzt" gesetzt, die
                # angezeigte Dauer bliebe für immer nahe null statt die tatsächliche
                # (unbekannte) Wartezeit widerzuspiegeln.
                if s["last_completed"] is None:
                    dt = "Noch nie erledigt"
                else:
                    dt = f"Überfällig seit {format_duration_de(now - s['due_at'])}"
                overdue_tasks.append({
                    "task": task,
                    "duration_text": dt,
                })
            if task.completions and (last_completed is None or task.completions[0].timestamp > last_completed):
                last_completed = task.completions[0].timestamp
                last_user = task.completions[0].user.name

        if worst is None:
            # Bereich ohne Aufgaben -> neutral als "erledigt" behandeln
            worst = {"status": "green", "due_at": now, "warn_at": now}

        if worst["status"] == "red" and worst["last_completed"] is None:
            duration_text = "noch nie erledigt"
        elif worst["status"] == "red":
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

    return room_status, overdue_tasks, due_soon_count, overdue_count, daily_due_room_ids, daily_overdue_count


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

    room_status, overdue_tasks, due_soon_count, overdue_count, daily_due_room_ids, daily_overdue_count = (
        compute_room_statuses(rooms, now)
    )
    attention_rooms = [r for r in rooms if room_status[r.id]["status"] != "green"]

    today = now.date()
    today_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    done_today = db.query(models.Completion).filter(models.Completion.timestamp >= today_start).count()

    recent_completions = (
        db.query(models.Completion).order_by(models.Completion.timestamp.desc()).limit(8).all()
    )

    now_local_date = now.astimezone(APP_TIMEZONE).date()
    all_upcoming_appointments = [
        a for a in db.query(models.Appointment).order_by(models.Appointment.date.asc()).all()
        if _aware(a.date).astimezone(APP_TIMEZONE).date() >= now_local_date and _is_appointment_visible(a, user)
    ]
    upcoming_appointments = all_upcoming_appointments[:5]
    # Fuer den mobilen "Heute anstehend"-Block (siehe dashboard.html) getrennt
    # nach heute (alle) und rein zukuenftig (Vorschau, gedeckelt) - vermeidet
    # doppelte Anzeige desselben Termins in beiden Bloecken.
    todays_appointments = [
        a for a in all_upcoming_appointments if _aware(a.date).astimezone(APP_TIMEZONE).date() == now_local_date
    ]
    future_appointments = [
        a for a in all_upcoming_appointments if _aware(a.date).astimezone(APP_TIMEZONE).date() != now_local_date
    ][:5]

    on_vacation_now = [
        v for v in db.query(models.Vacation).all()
        if _aware(v.start_date).astimezone(APP_TIMEZONE).date() <= now_local_date
        <= _aware(v.end_date).astimezone(APP_TIMEZONE).date()
    ]

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
        "daily_due_room_ids": daily_due_room_ids,
        "daily_overdue_count": daily_overdue_count,
        "stats": {
            "done_today": done_today,
            "due_soon": due_soon_count,
            "overdue": overdue_count,
            "group_count": db.query(models.Group).count(),
        },
        "recent_completions": recent_completions,
        "overdue_tasks": overdue_tasks[:6],
        "report_stats": {
            "open": open_count,
            "in_progress": in_progress_count,
            "done_today": done_today_reports,
            "needs_attention": open_count + in_progress_count,
        },
        "recent_reports": open_reports[:5],
        "upcoming_appointments": upcoming_appointments,
        "todays_appointments": todays_appointments,
        "future_appointments": future_appointments,
        "on_vacation_now": on_vacation_now,
        "greeting": greeting_for_now(now),
        "pending_tasks_count": due_soon_count + overdue_count,
        "now": now,
        "now_local": now.astimezone(APP_TIMEZONE),
        "device_authorized": device_is_authorized(request, db),
        "timeclock_user_mode": get_app_settings(db).timeclock_user_mode,
        "timeclock_open_entry": timeclock_open_entry,
        "currently_clocked_in": currently_clocked_in,
        "login_error": bool(request.query_params.get("login_error")),
    })


@app.get("/rooms")
def rooms_overview(request: Request, sort: str = "status", db: Session = Depends(get_db)):
    user, redirect = require_login_page(request, db)
    if redirect:
        return redirect
    rooms = db.query(models.Room).all()
    now = ntptime.now_utc()
    room_status, _, _, _, daily_due_room_ids, _ = compute_room_statuses(rooms, now)
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
        "daily_due_room_ids": daily_due_room_ids,
        "sort": sort,
    })


# ---------- Raum / Scan ----------

@app.get("/room/{room_id}")
def room_view(room_id: int, request: Request, done: str = "", db: Session = Depends(get_db)):
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
        if s["status"] == "red" and s["last_completed"] is None:
            s["duration_text"] = "Noch nie erledigt"
        elif s["status"] == "red":
            s["duration_text"] = f"Überfällig seit {format_duration_de(now - s['due_at'])}"
        elif s["status"] == "yellow":
            s["duration_text"] = f"Fällig in {format_duration_de(s['due_at'] - now)}"
        else:
            s["duration_text"] = None
        statuses[t.id] = s
    # Erledigte (grüne) Aufgaben aus der Hauptliste ausblenden, damit man beim
    # Abarbeiten nicht an bereits Erledigtem vorbeischeitzen muss - sie tauchen
    # erst beim nächsten Turnus-Alarm (gelb/rot) wieder auf. "Nach Bedarf"-
    # Aufgaben haben keinen Alarm und blieben früher deshalb immer sichtbar -
    # das wirkte nach dem Abhaken wie ein kaputter Button (siehe on_demand_done_today
    # in status.py). Sie verschwinden jetzt fuer den Rest des Tages ebenfalls
    # aus der Liste und tauchen am naechsten Tag automatisch wieder auf.
    pending_tasks = [
        t for t in room.tasks
        if (statuses[t.id]["on_demand"] and not statuses[t.id]["on_demand_done_today"])
        or (not statuses[t.id]["on_demand"] and statuses[t.id]["status"] != "green")
    ]
    # Turnus kurz -> lang (Tagesgeschäft zuerst, Grundreinigungen darunter),
    # "Nach Bedarf" (interval_hours=0) bewusst ans Ende statt an den Anfang -
    # numerisch waere 0 sonst der kuerzeste Turnus. Innerhalb desselben
    # Turnus alphabetisch nach Namen, damit die Liste eine feste Reihenfolge
    # behaelt statt bei jedem Aufruf in Einfuege-/ID-Reihenfolge zu springen.
    pending_tasks.sort(key=lambda t: (t.interval_hours or float("inf"), t.name.lower()))
    pending_task_ids = {t.id for t in pending_tasks}
    done_tasks = [t for t in room.tasks if t.id not in pending_task_ids]
    setups = (
        db.query(models.RoomSetup)
        .filter(models.RoomSetup.room_id == room_id)
        .join(models.EventSetup)
        .order_by(models.EventSetup.name)
        .all()
    )
    # Events, die diesen Bereich noch nicht als eigenen Eintrag haben - nur
    # die im "Zu bestehendem Aufbau hinzufügen"-Formular auswählbar, sonst
    # könnte man versehentlich zwei Einträge für denselben Bereich anlegen.
    attachable_events = [
        e for e in db.query(models.EventSetup).order_by(models.EventSetup.name).all()
        if not any(rs.room_id == room_id for rs in e.room_setups)
    ]
    just_done_task_id = int(done) if done.isdigit() else None
    just_done_task_name = next((t.name for t in room.tasks if t.id == just_done_task_id), None)
    return templates.TemplateResponse("room.html", {
        "request": request,
        "user": user,
        "room": room,
        "statuses": statuses,
        "pending_tasks": pending_tasks,
        "done_tasks": done_tasks,
        "setups": setups,
        "attachable_events": attachable_events,
        "just_done_task_name": just_done_task_name,
        # Für die Inline-Aufgabenverwaltung (Anlegen/Bearbeiten-Drawer),
        # sichtbar nur für Admins/Schichtleiter/Entwickler (siehe room.html).
        "groups": db.query(models.Group).order_by(models.Group.name).all(),
    })


def _with_toast(path: str, message: str, kind: str = "success") -> str:
    """Hängt eine Toast-Nachricht als Query-Param an eine Redirect-Zielseite an
    (siehe base.html, liest ?bulk_toast= aus und zeigt eine kurze Bestätigung
    an) - so bekommt man bei Sammel-Aktionen ohne JS/fetch trotzdem sofortiges
    Feedback, ohne von der bestehenden Server-Redirect-Architektur abzuweichen.
    kind="error" färbt den Toast rot statt grün (z.B. für einen fehlgeschlagenen
    Datei-Import) - Standard bleibt "success", damit bestehende Aufrufe ohne
    Änderung weiterlaufen."""
    sep = "&" if "?" in path else "?"
    target = f"{path}{sep}bulk_toast={quote(message)}"
    if kind != "success":
        target += f"&bulk_toast_kind={kind}"
    return target


def _complete_tasks(db: Session, tasks: list, user_id: int):
    """Markiert mehrere Aufgaben auf einmal als erledigt (gleiche Effekte wie
    complete_task pro Aufgabe: Completion anlegen, Gruppen-Benachrichtigungs-
    status zurücksetzen), für die Sammel-Buttons unten."""
    for task in tasks:
        db.add(models.Completion(task_id=task.id, user_id=user_id))
        db.query(models.TaskGroupNotice).filter(models.TaskGroupNotice.task_id == task.id).update(
            {"last_status": "green"}
        )
    db.commit()


@app.post("/room/{room_id}/task/{task_id}/complete")
def complete_task(room_id: int, task_id: int, request: Request, db: Session = Depends(get_db)):
    """Fuer JS-Clients (siehe room.html, fetch mit X-Requested-With: fetch)
    antwortet die Route mit JSON statt einem Redirect, damit der "Erledigt"-
    Button ohne Full-Page-Reload optimistisch reagieren kann (Karte animiert
    ausblenden, Toast mit "Rückgängig" - siehe undo_complete_task unten).
    Ohne diesen Header (klassischer Formular-POST, z.B. ohne JS) bleibt das
    bisherige Redirect-Verhalten unveraendert als Fallback erhalten."""
    user = require_login(request, db)
    is_fetch = request.headers.get("X-Requested-With") == "fetch"
    task = db.query(models.Task).filter(models.Task.id == task_id, models.Task.room_id == room_id).first()
    if not task:
        if is_fetch:
            raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
        return RedirectResponse(f"/room/{room_id}?done={task_id}", status_code=302)
    completion = models.Completion(task_id=task.id, user_id=user.id)
    db.add(completion)
    db.query(models.TaskGroupNotice).filter(models.TaskGroupNotice.task_id == task.id).update(
        {"last_status": "green"}
    )
    db.commit()
    if is_fetch:
        db.refresh(completion)
        return {"ok": True, "completion_id": completion.id, "task_id": task.id}
    # done=<task_id> im Redirect, damit die Seite die gerade erledigte Aufgabe
    # kurz sichtbar hervorheben kann - sonst ist die einzige Rückmeldung eine
    # kleine Textzeile, die leicht übersehen wird (siehe Nutzer-Feedback: ohne
    # sichtbare Bestätigung wurde "Erledigt" mehrfach hintereinander gedrückt).
    return RedirectResponse(f"/room/{room_id}?done={task_id}", status_code=302)


@app.post("/room/{room_id}/task/{task_id}/complete/{completion_id}/undo")
def undo_complete_task(room_id: int, task_id: int, completion_id: int, request: Request, db: Session = Depends(get_db)):
    """Ruecknahme direkt nach dem Abhaken (siehe "Rückgängig" im Toast in
    room.html) - bewusst NICHT ueber die admin-only /history/{id}/delete-
    Route, da hier jeder Nutzer seine eigene, gerade erst gesetzte Buchung
    selbst zurueckziehen koennen soll (typischer Anwendungsfall: versehentlicher
    Doppelklick). Eng eingegrenzt: nur die eigene Buchung, nur fuer die
    passende Aufgabe/den passenden Bereich, und nur innerhalb eines kurzen
    Zeitfensters - kein genereller Freibrief zum nachtraeglichen Loeschen
    beliebiger eigener Buchungen."""
    user = require_login(request, db)
    completion = (
        db.query(models.Completion)
        .filter(
            models.Completion.id == completion_id,
            models.Completion.task_id == task_id,
            models.Completion.user_id == user.id,
        )
        .first()
    )
    if not completion or not completion.task or completion.task.room_id != room_id:
        raise HTTPException(status_code=404, detail="Buchung nicht gefunden")
    if ntptime.now_utc() - _aware(completion.timestamp) > timedelta(minutes=5):
        raise HTTPException(status_code=400, detail="Rückgängig ist nur kurz nach dem Abhaken möglich")
    db.delete(completion)
    db.commit()
    return {"ok": True}


@app.post("/room/{room_id}/complete-due")
def complete_due_tasks(room_id: int, request: Request, next: str = Form("/"), db: Session = Depends(get_db)):
    """Sammel-Button auf der Bereichs-Card: markiert alle aktuell fälligen
    (gelb) oder überfälligen (rot) TÄGLICHEN Aufgaben dieses Bereichs auf
    einmal als erledigt - reduziert die Klickarbeit bei den täglichen
    Routine-Checks. Bewusst nur Turnus "Täglich" (interval_hours == 24),
    nicht sämtliche fälligen Aufgaben: seltenere Turnusse (wöchentlich,
    monatlich, ...) sollen dabei nicht versehentlich mit abgehakt werden,
    siehe Nutzer-Feedback nach der ersten Version. "Nach Bedarf"-Aufgaben und
    bereits erledigte (grüne) bleiben ohnehin unberührt."""
    user = require_login(request, db)
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if not room:
        return RedirectResponse("/", status_code=302)
    now = ntptime.now_utc()
    due_tasks = [
        t for t in room.tasks
        if t.interval_hours == 24 and task_status(t, now)["status"] in ("yellow", "red")
    ]
    _complete_tasks(db, due_tasks, user.id)
    count = len(due_tasks)
    msg = f"{count} tägliche {'Aufgabe' if count == 1 else 'Aufgaben'} für {room.name} als erledigt markiert"
    return RedirectResponse(_with_toast(next, msg), status_code=302)


@app.post("/tasks/complete-overdue")
def complete_all_overdue(request: Request, next: str = Form("/"), db: Session = Depends(get_db)):
    """Sekundärer Sammel-Button im "Überfällige Aufgaben"-Widget: markiert
    bereichsübergreifend alle aktuell überfälligen (roten) TÄGLICHEN Aufgaben
    als erledigt (gleiche Turnus-Einschränkung wie complete_due_tasks), nicht
    nur die im Widget sichtbaren obersten Einträge."""
    user = require_login(request, db)
    now = ntptime.now_utc()
    overdue = [
        t for t in db.query(models.Task).all()
        if t.interval_hours == 24 and task_status(t, now)["status"] == "red"
    ]
    _complete_tasks(db, overdue, user.id)
    count = len(overdue)
    msg = f"{count} überfällige tägliche {'Aufgabe' if count == 1 else 'Aufgaben'} als erledigt markiert"
    return RedirectResponse(_with_toast(next, msg), status_code=302)


@app.post("/room/{room_id}/setups")
async def room_setups_create(
    room_id: int,
    request: Request,
    name: str = Form(...),
    note: str = Form(""),
    photos: list[UploadFile] = File([]),
    db: Session = Depends(get_db),
):
    """Neuer Aufbau (siehe EventSetup) mit einem ersten Bereichs-Eintrag
    (siehe RoomSetup) - bewusst nicht für Pauschalkräfte, siehe User.is_flat_rate."""
    user = require_login(request, db)
    if user.is_flat_rate:
        return RedirectResponse(f"/room/{room_id}", status_code=302)

    event = models.EventSetup(name=name, created_by_id=user.id)
    db.add(event)
    db.commit()

    setup = models.RoomSetup(
        event_id=event.id, room_id=room_id, name=name, note=note.strip() or None, created_by_id=user.id,
    )
    db.add(setup)
    db.commit()

    for photo in photos:
        if not photo or not photo.filename:
            continue
        data = await photo.read()
        if data and (photo.content_type or "").startswith("image/"):
            ext = os.path.splitext(photo.filename)[1][:10] or ".jpg"
            filename = f"{uuid.uuid4().hex}{ext}"
            with open(os.path.join(SETUP_PHOTOS_DIR, filename), "wb") as f:
                f.write(data)
            db.add(models.RoomSetupPhoto(setup_id=setup.id, filename=filename))
    db.commit()
    return RedirectResponse(f"/room/{room_id}", status_code=302)


@app.post("/room/{room_id}/setups/attach")
async def room_setups_attach(
    room_id: int,
    request: Request,
    event_id: int = Form(...),
    note: str = Form(""),
    photos: list[UploadFile] = File([]),
    db: Session = Depends(get_db),
):
    """Fügt diesem Bereich einen eigenen Eintrag zu einem bereits bestehenden
    Aufbau (EventSetup) hinzu - z.B. wenn "Party 1" schon im Ausschank
    dokumentiert ist und jetzt auch der Club dazukommt. Eigene Fotos/Notiz,
    gemeinsamer Name mit den anderen Bereichs-Einträgen desselben Events."""
    user = require_login(request, db)
    if user.is_flat_rate:
        return RedirectResponse(f"/room/{room_id}", status_code=302)

    event = db.query(models.EventSetup).filter(models.EventSetup.id == event_id).first()
    if not event or any(rs.room_id == room_id for rs in event.room_setups):
        return RedirectResponse(f"/room/{room_id}", status_code=302)

    setup = models.RoomSetup(
        event_id=event.id, room_id=room_id, name=event.name, note=note.strip() or None, created_by_id=user.id,
    )
    db.add(setup)
    db.commit()

    for photo in photos:
        if not photo or not photo.filename:
            continue
        data = await photo.read()
        if data and (photo.content_type or "").startswith("image/"):
            ext = os.path.splitext(photo.filename)[1][:10] or ".jpg"
            filename = f"{uuid.uuid4().hex}{ext}"
            with open(os.path.join(SETUP_PHOTOS_DIR, filename), "wb") as f:
                f.write(data)
            db.add(models.RoomSetupPhoto(setup_id=setup.id, filename=filename))
    db.commit()
    return RedirectResponse(f"/room/{room_id}", status_code=302)


@app.post("/setups/{setup_id}/photos")
async def room_setup_add_photos(
    setup_id: int, request: Request, photos: list[UploadFile] = File([]), db: Session = Depends(get_db),
):
    user = require_login(request, db)
    setup = db.query(models.RoomSetup).filter(models.RoomSetup.id == setup_id).first()
    if setup and not user.is_flat_rate:
        for photo in photos:
            if not photo or not photo.filename:
                continue
            data = await photo.read()
            if data and (photo.content_type or "").startswith("image/"):
                ext = os.path.splitext(photo.filename)[1][:10] or ".jpg"
                filename = f"{uuid.uuid4().hex}{ext}"
                with open(os.path.join(SETUP_PHOTOS_DIR, filename), "wb") as f:
                    f.write(data)
                db.add(models.RoomSetupPhoto(setup_id=setup.id, filename=filename))
        db.commit()
    return RedirectResponse(f"/room/{setup.room_id}" if setup else "/", status_code=302)


@app.post("/setups/{setup_id}/edit")
def room_setup_edit(
    setup_id: int, request: Request, name: str = Form(...), note: str = Form(""), db: Session = Depends(get_db),
):
    """Der Name gehört dem EventSetup (gemeinsam für alle Bereichs-Einträge
    desselben Aufbaus), die Notiz gehört nur diesem einen Bereichs-Eintrag."""
    user = require_login(request, db)
    setup = db.query(models.RoomSetup).filter(models.RoomSetup.id == setup_id).first()
    if setup and (setup.created_by_id == user.id or user.is_admin or user.is_shift_lead or user.is_developer):
        if setup.event:
            setup.event.name = name
        setup.name = name
        setup.note = note.strip() or None
        db.commit()
        return RedirectResponse(f"/room/{setup.room_id}", status_code=302)
    return RedirectResponse("/", status_code=302)


@app.post("/setups/{setup_id}/delete")
def room_setup_delete(setup_id: int, request: Request, db: Session = Depends(get_db)):
    """Löscht nur den Bereichs-Eintrag; ist es der letzte Bereichs-Eintrag
    des Events, wird das EventSetup gleich mit aufgeräumt, damit keine leeren
    Aufbauten in der "Zu bestehendem Aufbau hinzufügen"-Auswahl übrig bleiben."""
    user = require_login(request, db)
    setup = db.query(models.RoomSetup).filter(models.RoomSetup.id == setup_id).first()
    if setup and (setup.created_by_id == user.id or user.is_admin or user.is_shift_lead or user.is_developer):
        room_id = setup.room_id
        event = setup.event
        for photo in setup.photos:
            try:
                os.remove(os.path.join(SETUP_PHOTOS_DIR, photo.filename))
            except OSError:
                pass
        db.delete(setup)
        db.commit()
        if event and len(event.room_setups) == 0:
            db.delete(event)
            db.commit()
        return RedirectResponse(f"/room/{room_id}", status_code=302)
    return RedirectResponse("/", status_code=302)


# ---------- Zeiterfassung ----------

def _entry_hours(entry, now) -> float:
    end = entry.clock_out or now
    return max(0.0, (_aware(end) - _aware(entry.clock_in)).total_seconds() / 3600.0)


def compute_time_stats(user, db: Session, now, range_start_utc, range_end_utc, include_overtime: bool) -> dict:
    """Aggregiert die Zeiterfassung eines Nutzers für heute (immer der echte
    aktuelle Kalendertag, unabhängig vom gewählten Zeitraum) sowie für den
    per range_start_utc/range_end_utc (exklusiv) übergebenen Zeitraum -
    Monat oder freier Von/Bis-Bereich, siehe _resolve_timeclock_self_period.
    include_overtime unterdrückt den Sollzeit-Vergleich außerhalb des
    Monats-Modus, da target_hours_per_month sich nur sinnvoll mit einem
    vollen Kalendermonat vergleichen lässt, nicht mit einem beliebigen
    Zeitraum. 'history' enthält jetzt den kompletten Zeitraum (nicht mehr
    nur die letzten 20 Buchungen), analog zur admin-seitigen Monatsliste."""
    local_now = now.astimezone(APP_TIMEZONE)
    today = local_now.date()
    today_start_utc = datetime(today.year, today.month, today.day, tzinfo=APP_TIMEZONE).astimezone(timezone.utc)
    today_end_utc = today_start_utc + timedelta(days=1)

    today_hours = sum(
        _entry_hours(e, now) for e in
        db.query(models.TimeEntry).filter(
            models.TimeEntry.user_id == user.id,
            models.TimeEntry.clock_in >= today_start_utc, models.TimeEntry.clock_in < today_end_utc,
        ).all()
    )

    range_entries = (
        db.query(models.TimeEntry)
        .filter(
            models.TimeEntry.user_id == user.id,
            models.TimeEntry.clock_in >= range_start_utc, models.TimeEntry.clock_in < range_end_utc,
        )
        .order_by(models.TimeEntry.clock_in.desc())
        .all()
    )

    range_hours = 0.0
    history = []
    for e in range_entries:
        clock_in_local = _aware(e.clock_in).astimezone(APP_TIMEZONE)
        clock_out_local = _aware(e.clock_out).astimezone(APP_TIMEZONE) if e.clock_out else None
        hours = _entry_hours(e, now)
        range_hours += hours
        history.append({
            "id": e.id,
            "date_label": f"{_WEEKDAY_ABBR_DE[clock_in_local.weekday()]}, {clock_in_local.strftime('%d.%m.%Y')}",
            "clock_in": clock_in_local,
            "clock_out": clock_out_local,
            "hours": hours,
            "open": e.clock_out is None,
        })

    # Offene Buchung unabhaengig vom gewaehlten Zeitraum ermitteln - man kann
    # sich z.B. einen vergangenen Monat ansehen, waehrend man gerade jetzt
    # eingestempelt ist.
    open_e = (
        db.query(models.TimeEntry)
        .filter(models.TimeEntry.user_id == user.id, models.TimeEntry.clock_out.is_(None))
        .first()
    )
    open_entry = {"clock_in": _aware(open_e.clock_in).astimezone(APP_TIMEZONE)} if open_e else None

    wage = user.hourly_wage or 0.0
    target = user.target_hours_per_month
    return {
        "open_entry": open_entry,
        "today_hours": today_hours,
        "today_earned": today_hours * wage,
        "range_hours": range_hours,
        "range_earned": range_hours * wage,
        "range_bookings": len(range_entries),
        "overtime_hours": (range_hours - target) if (include_overtime and target is not None) else None,
        "history": history,
        "has_wage": user.hourly_wage is not None,
        "has_target": target is not None,
    }


def get_app_settings(db: Session) -> models.AppSettings:
    """Holt die eine Einstellungs-Zeile, legt sie beim allerersten Zugriff an
    (anders als bei AuthorizedDevice bedeutet "keine Zeile" hier nicht "nicht
    konfiguriert", sondern muss immer einen Default liefern können)."""
    settings = db.query(models.AppSettings).first()
    if not settings:
        settings = models.AppSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _resolve_timeclock_period(mode: str, month: str, date_from: str, date_to: str, local_now) -> dict:
    """Loest die "Monat" (Pfeil-Navigation)/"Freier Zeitraum" (Von/Bis)-Auswahl
    zu UTC-Grenzen fuer die DB-Abfrage auf - gemeinsam genutzt von der
    Mitarbeiter- (timeclock.html) UND der Admin-Zeiterfassung
    (admin_timeclock.html), damit beide Ansichten exakt dieselbe Filter-Logik
    verwenden (siehe auch app/static/timeclock_period.js fuers Frontend-
    Gegenstueck). Liefert IMMER beide Wertesaetze (Monat + Von/Bis), damit
    beim Wechsel zwischen den Modi im Frontend sinnvolle Default-Werte
    vorausgefuellt werden koennen, auch wenn der jeweils andere Modus gerade
    aktiv ist."""
    today = local_now.date()
    month_start, next_month_start = _parse_month_param(month, local_now)
    month_value = f"{month_start.year:04d}-{month_start.month:02d}"
    if month_start.month == 1:
        prev_month_value = f"{month_start.year - 1:04d}-12"
    else:
        prev_month_value = f"{month_start.year:04d}-{month_start.month - 1:02d}"
    next_month_value = f"{next_month_start.year:04d}-{next_month_start.month:02d}"

    if mode == "range":
        try:
            start_date = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else month_start
        except ValueError:
            start_date = month_start
        try:
            end_date = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today
        except ValueError:
            end_date = today
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        range_start_local, range_end_local_exclusive = start_date, end_date + timedelta(days=1)
        date_from_value, date_to_value = start_date.isoformat(), end_date.isoformat()
    else:
        mode = "month"
        range_start_local, range_end_local_exclusive = month_start, next_month_start
        # Sinnvolle Default-Vorbelegung fuer die Von/Bis-Felder, falls direkt
        # danach in den freien Zeitraum umgeschaltet wird, ohne dass vorher
        # schon eigene Werte gesetzt wurden.
        date_from_value = month_start.isoformat()
        date_to_value = min(today, next_month_start - timedelta(days=1)).isoformat()

    range_start_utc = datetime(
        range_start_local.year, range_start_local.month, range_start_local.day, tzinfo=APP_TIMEZONE
    ).astimezone(timezone.utc)
    range_end_utc = datetime(
        range_end_local_exclusive.year, range_end_local_exclusive.month, range_end_local_exclusive.day, tzinfo=APP_TIMEZONE
    ).astimezone(timezone.utc)

    return {
        "mode": mode,
        "month_value": month_value,
        "prev_month_value": prev_month_value,
        "next_month_value": next_month_value,
        "date_from_value": date_from_value,
        "date_to_value": date_to_value,
        "range_start_utc": range_start_utc,
        "range_end_utc": range_end_utc,
    }


def _build_timeclock_self_context(request: Request, user, db: Session, mode: str, month: str, date_from: str, date_to: str) -> dict:
    """Kontext fuer timeclock.html - ausgelagert, damit GET /timeclock bei
    fetch()-Anfragen (Monats-/Zeitraum-/Moduswechsel ohne Full-Page-Reload,
    siehe timeclock.html) nur das aktualisierte Fragment zurueckgeben kann."""
    now = ntptime.now_utc()
    period = _resolve_timeclock_period(mode, month, date_from, date_to, now.astimezone(APP_TIMEZONE))
    stats = compute_time_stats(
        user, db, now, period["range_start_utc"], period["range_end_utc"], include_overtime=(period["mode"] == "month"),
    )
    settings = get_app_settings(db)
    return {
        "request": request,
        "user": user,
        "stats": stats,
        "timeclock_user_mode": settings.timeclock_user_mode,
        "mode": period["mode"],
        "month_value": period["month_value"],
        "prev_month_value": period["prev_month_value"],
        "next_month_value": period["next_month_value"],
        "date_from_value": period["date_from_value"],
        "date_to_value": period["date_to_value"],
    }


@app.get("/timeclock")
def timeclock_view(
    request: Request, mode: str = "month", month: str = "",
    date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to"),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    context = _build_timeclock_self_context(request, user, db, mode, month, date_from, date_to)
    # Monats-/Zeitraum-/Moduswechsel laeuft clientseitig ueber fetch() statt
    # einer echten Navigation (siehe timeclock.html, analog zu
    # admin_timeclock.html) - der Header markiert diese Anfragen, damit wir
    # nur das Fragment statt der kompletten Seite zurueckschicken.
    if request.headers.get("X-Requested-With") == "fetch":
        return templates.TemplateResponse("_timeclock_self_fragment.html", context)
    return templates.TemplateResponse("timeclock.html", context)


def _timeentry_pdf_rows(db: Session, user_id: int, range_start_utc=None, range_end_utc=None) -> list:
    """range_start_utc/range_end_utc (exklusiv) schraenken auf den aktuell
    aktiven Monats-/Zeitraum-Filter ein (siehe _resolve_timeclock_period) -
    None/None exportiert wie bisher die komplette Historie."""
    query = db.query(models.TimeEntry).filter(models.TimeEntry.user_id == user_id)
    if range_start_utc is not None:
        query = query.filter(models.TimeEntry.clock_in >= range_start_utc, models.TimeEntry.clock_in < range_end_utc)
    entries = query.order_by(models.TimeEntry.clock_in.asc()).all()
    return [
        {
            "clock_in_local": _aware(e.clock_in).astimezone(APP_TIMEZONE),
            "clock_out_local": _aware(e.clock_out).astimezone(APP_TIMEZONE) if e.clock_out else None,
        }
        for e in entries
    ]


def _timeentry_pdf_response(target_user, entries_local: list) -> Response:
    generated_at_local = ntptime.now_utc().astimezone(APP_TIMEZONE)
    pdf_bytes = pdf_export.generate_timeclock_pdf(
        target_user.name, target_user.personnel_number, entries_local, generated_at_local,
    )
    filename = f"zeiterfassung-{target_user.name}-{generated_at_local.strftime('%Y%m%d')}.pdf"
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/timeclock/export.pdf")
def timeclock_export_pdf(
    request: Request, mode: str = "month", month: str = "",
    date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to"),
    db: Session = Depends(get_db),
):
    """Exportiert strikt den aktuell aktiven Filter (Monat oder freier
    Zeitraum, siehe _resolve_timeclock_period) statt wie frueher immer die
    komplette Historie - die Query-Parameter kommen 1:1 vom PDF-Link in
    timeclock.html mit, der dem aktiven Filter folgt."""
    user = require_login(request, db)
    period = _resolve_timeclock_period(mode, month, date_from, date_to, ntptime.now_utc().astimezone(APP_TIMEZONE))
    return _timeentry_pdf_response(
        user, _timeentry_pdf_rows(db, user.id, period["range_start_utc"], period["range_end_utc"])
    )


def _timeclock_self_redirect(mode: str, month: str, date_from: str, date_to: str) -> str:
    """Baut die Rueck-URL nach Bearbeiten/Loeschen einer eigenen Buchung so,
    dass der gerade betrachtete Monat/Zeitraum erhalten bleibt, statt nach
    jeder Aktion auf den aktuellen Monat zurueckzuspringen."""
    params = {"mode": mode}
    if mode == "range":
        params["from"] = date_from
        params["to"] = date_to
    else:
        params["month"] = month
    return "/timeclock?" + urlencode({k: v for k, v in params.items() if v})


@app.post("/timeclock/self/{entry_id}/edit")
def timeclock_self_edit(
    entry_id: int, request: Request,
    clock_in: str = Form(...), clock_out: str = Form(""),
    mode: str = Form("month"), month: str = Form(""), date_from: str = Form(""), date_to: str = Form(""),
    db: Session = Depends(get_db),
):
    """Selbstbearbeitung der eigenen Buchungen - nur im Nutzer-Modus möglich
    (siehe AppSettings.timeclock_user_mode) und nur für die eigene Buchung,
    im Gegensatz zur Admin-Korrektur unter /admin/timeclock."""
    user = require_login(request, db)
    target = _timeclock_self_redirect(mode, month, date_from, date_to)
    if not get_app_settings(db).timeclock_user_mode:
        return RedirectResponse(target, status_code=302)
    entry = db.query(models.TimeEntry).filter(
        models.TimeEntry.id == entry_id, models.TimeEntry.user_id == user.id
    ).first()
    if entry:
        old_in, old_out = entry.clock_in, entry.clock_out
        entry.clock_in = _parse_local_dt(clock_in)
        entry.clock_out = _parse_local_dt(clock_out)
        _log_timeclock_change(db, entry, "edited", user.id, old_in, old_out)
        db.commit()
    return RedirectResponse(target, status_code=302)


@app.post("/timeclock/self/{entry_id}/delete")
def timeclock_self_delete(
    entry_id: int, request: Request,
    mode: str = Form("month"), month: str = Form(""), date_from: str = Form(""), date_to: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    target = _timeclock_self_redirect(mode, month, date_from, date_to)
    if not get_app_settings(db).timeclock_user_mode:
        return RedirectResponse(target, status_code=302)
    entry = db.query(models.TimeEntry).filter(
        models.TimeEntry.id == entry_id, models.TimeEntry.user_id == user.id
    ).first()
    if entry:
        _log_timeclock_change(db, entry, "deleted", user.id, entry.clock_in, entry.clock_out)
        db.delete(entry)
        db.commit()
    return RedirectResponse(target, status_code=302)


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
    return templates.TemplateResponse("timeclock_kiosk.html", {
        "request": request, "user": None, "authorized": True,
        "error": None, "result": None,
    })


@app.post("/timeclock/kiosk")
def timeclock_kiosk_punch(
    request: Request,
    identifier: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if not device_is_authorized(request, db):
        return templates.TemplateResponse("timeclock_kiosk.html", {
            "request": request, "user": None, "authorized": False,
        })

    target_user = find_user_by_identifier(db, identifier)
    if not target_user or not verify_password(password, target_user.password_hash):
        return templates.TemplateResponse("timeclock_kiosk.html", {
            "request": request, "user": None, "authorized": True,
            "error": "Benutzername/Personalnummer oder Passwort ist falsch.", "result": None,
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
        "error": None,
        "result": {
            "name": target_user.name,
            "action": action,
            "time_local": _aware(punch_time).astimezone(APP_TIMEZONE),
        },
    })


@app.post("/timeclock/punch")
def timeclock_punch(request: Request, db: Session = Depends(get_db)):
    """Ein-/Ausstempel-Button direkt im Dashboard - für den eingeloggten
    Nutzer. Im Terminal-Modus (Standard) nur, wenn das aktuelle Gerät
    autorisiert ist (Buchung bleibt an ein konkretes Gerät gebunden, ganz
    ohne den Umweg über das separate Kiosk-Terminal /timeclock/kiosk, wenn
    man ohnehin schon auf dem autorisierten Gerät eingeloggt ist). Im
    Nutzer-Modus darf jeder von jedem eigenen, eingeloggten Gerät aus
    stempeln - siehe AppSettings.timeclock_user_mode."""
    user = require_login(request, db)
    if not device_is_authorized(request, db) and not get_app_settings(db).timeclock_user_mode:
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
    admin = require_staff_or_developer(request, db)
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
    require_staff_or_developer(request, db)
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
    require_staff_or_developer(request, db)
    tag = db.query(models.NfcTag).filter(models.NfcTag.id == tag_id).first()
    if tag:
        tag.uid = uid.strip() or None
        tag.last_verified_at = ntptime.now_utc()
        db.commit()
    return RedirectResponse("/admin/nfc-tags", status_code=302)


@app.post("/admin/nfc-tags/{tag_id}/edit")
def admin_nfc_tags_edit(tag_id: int, request: Request, label: str = Form(""), db: Session = Depends(get_db)):
    require_staff_or_developer(request, db)
    tag = db.query(models.NfcTag).filter(models.NfcTag.id == tag_id).first()
    if tag:
        tag.label = label.strip() or None
        db.commit()
    return RedirectResponse("/admin/nfc-tags", status_code=302)


@app.post("/admin/nfc-tags/{tag_id}/delete")
def admin_nfc_tags_delete(tag_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin_or_developer(request, db)
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
    visible = [i for i in items if user_can_see_inventory_item(user, i)]
    # Persönliche Ausblendung (siehe User.hidden_inventory_groups) - kein
    # Recht, nur eine Deckelung der ohnehin schon erlaubten Sicht nach unten.
    if user and user.hidden_inventory_groups:
        hidden_ids = {g.id for g in user.hidden_inventory_groups}
        visible = [i for i in visible if i.group_id not in hidden_ids]
    return visible


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
        inventory_critical = sum(1 for i in items if compute_inventory_status(i)["status"] == "low")
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
    # Für die "Sichtbarkeit anpassen"-Auswahl: nur Gruppen, die für den Nutzer
    # ohnehin sichtbar wären (siehe user_can_see_inventory_item) - Ausblenden
    # ist eine Einschränkung der eigenen Sicht, kein Freischalten fremder Daten.
    visible_groups_for_filter = (
        db.query(models.Group).order_by(models.Group.name).all() if user and user.is_admin
        else sorted(user.groups, key=lambda g: g.name) if user else []
    )
    hidden_group_ids = {g.id for g in user.hidden_inventory_groups} if user else set()
    return templates.TemplateResponse("inventory.html", {
        "request": request,
        "user": user,
        "items": items,
        "inventory_status": inventory_status,
        "inventory_consumption": inventory_consumption,
        "inventory_chart": inventory_chart,
        "groups": db.query(models.Group).all(),
        "visible_groups_for_filter": visible_groups_for_filter,
        "hidden_group_ids": hidden_group_ids,
        "categories": sorted({i.category for i in items if i.category}),
        "locations": sorted({i.location for i in items if i.location}),
        "units": sorted({i.unit for i in items if i.unit}),
        "pack_units": sorted({i.pack_unit for i in items if i.pack_unit}),
        "img_fetch_failed": bool(img_fetch_failed),
    })


@app.post("/inventory/hidden-groups")
def inventory_set_hidden_groups(
    request: Request,
    hidden_group_ids: list[int] = Form([]),
    db: Session = Depends(get_db),
):
    # Persönliche Einstellung, kein Admin-Recht - jeder angemeldete Nutzer
    # darf seine eigene Sicht anpassen.
    user = require_login(request, db)
    user.hidden_inventory_groups = (
        db.query(models.Group).filter(models.Group.id.in_(hidden_group_ids)).all() if hidden_group_ids else []
    )
    db.commit()
    return RedirectResponse("/inventory", status_code=302)


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
    is_fetch = request.headers.get("X-Requested-With") == "fetch"
    if not item or not user_can_see_inventory_item(user, item):
        if is_fetch:
            raise HTTPException(status_code=404)
        return RedirectResponse("/inventory", status_code=302)

    item.stock_current += delta
    db.add(models.InventoryMovement(item_id=item.id, user_id=user.id, delta=delta, note=note or None))
    if item.stock_current >= item.stock_min:
        item.notified = False
    db.commit()

    if is_fetch:
        # Nur die betroffene Karte als Fragment zurueckgeben (siehe
        # _inventory_card.html) statt eines Redirects - so bleibt die
        # Scroll-Position beim Buchen erhalten und nur diese eine Karte wird
        # im Browser ersetzt, ohne das gesamte Grid neu zu rendern.
        now = ntptime.now_utc()
        consumption = compute_inventory_consumption(item, now)
        return templates.TemplateResponse("_inventory_card.html", {
            "request": request,
            "user": user,
            "item": item,
            "st": compute_inventory_status(item),
            "chart": build_consumption_chart(consumption),
            "consumption": consumption,
        })
    return RedirectResponse("/inventory", status_code=302)


@app.post("/inventory/{item_id}/image")
async def inventory_set_image(
    item_id: int,
    request: Request,
    image_file: UploadFile | None = File(None),
    image_url_input: str = Form(""),
    db: Session = Depends(get_db),
):
    actor = require_staff_or_developer(request, db)
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
REPORT_CATEGORIES = ("defekt", "material", "reinigung", "anschaffung", "sonstiges")
# Materialwunsch/Anschaffung ist an keinen Bereich gebunden (siehe
# models.Report.room_id) - man bestellt fuer die eigene Gruppe, nicht für
# einen Raum, daher braucht diese Kategorie zwingend eine zustaendige Gruppe
# statt eines Bereichs, und keine Priorität (die ist ein Dringlichkeits-Maß
# für Defekte, passt hier nicht).
REPORT_ROOMLESS_CATEGORIES = ("anschaffung",)
REPORT_STATUSES = ("open", "in_progress", "done")


def _sort_reports(reports):
    """Neueste zuerst, aber innerhalb dessen nach Priorität (kritisch zuerst) -
    zweistufig stabil sortiert, damit beides gleichzeitig gilt."""
    by_recency = sorted(reports, key=lambda r: r.created_at, reverse=True)
    return sorted(by_recency, key=lambda r: REPORT_PRIORITY_RANK.get(r.priority, 2))


_FEEDBACK_STATUS_RANK = {"open": 0, "in_progress": 1, "done": 2}


def _sort_feedback(items):
    """Offene zuerst, erledigte zuletzt - innerhalb eines Status neueste zuerst."""
    by_recency = sorted(items, key=lambda f: f.created_at, reverse=True)
    return sorted(by_recency, key=lambda f: _FEEDBACK_STATUS_RANK.get(f.status, 0))


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


@app.post("/feedback")
async def feedback_create(
    request: Request,
    type: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("medium"),
    url: str = Form(""),
    next: str = Form("/"),
    photos: list[UploadFile] = File([]),
    db: Session = Depends(get_db),
):
    """Schwebender Feedback-Button (siehe base.html) - für jeden eingeloggten
    Nutzer erreichbar, kein Admin-Vorbehalt, damit auch normales Personal
    Bugs/Wünsche melden kann. URL/Nutzer/Zeitpunkt werden automatisch erfasst
    (URL kommt als verstecktes Feld von der aufrufenden Seite mit, statt vom
    Server geraten zu werden - der Referer-Header ist nicht zuverlässig).
    Screenshots optional, gleiches Speichermuster wie bei Meldungs-Fotos
    (siehe REPORT_PHOTOS_DIR)."""
    user = require_login(request, db)
    if type not in ("bug", "feature"):
        type = "bug"
    if priority not in ("low", "medium", "high"):
        priority = "medium"
    feedback = models.Feedback(
        type=type, title=title.strip(), description=description.strip() or None,
        priority=priority, url=url or None, user_id=user.id,
    )
    db.add(feedback)
    db.flush()
    for photo in photos:
        if not photo or not photo.filename:
            continue
        data = await photo.read()
        if data and (photo.content_type or "").startswith("image/"):
            ext = os.path.splitext(photo.filename)[1][:10] or ".jpg"
            filename = f"{uuid.uuid4().hex}{ext}"
            with open(os.path.join(FEEDBACK_PHOTOS_DIR, filename), "wb") as f:
                f.write(data)
            db.add(models.FeedbackPhoto(feedback_id=feedback.id, filename=filename))
    db.commit()
    target = next if next.startswith("/") else "/"
    return RedirectResponse(_with_toast(target, "Danke! Feedback wurde übermittelt."), status_code=302)


_FEEDBACK_TYPE_LABEL = {"bug": "Bug/Fehler", "feature": "Funktionswunsch"}
_FEEDBACK_PRIORITY_LABEL = {"low": "Niedrig", "medium": "Mittel", "high": "Hoch"}


def _feedback_prompt(f) -> str:
    """Formatiert ein Feedback-Ticket als fertigen Claude-Prompt zum Beheben/
    Umsetzen - für den "Als Claude-Prompt kopieren"-Button in admin_feedback.html."""
    kind = _FEEDBACK_TYPE_LABEL.get(f.type, f.type)
    action = "behebe folgenden gemeldeten Bug" if f.type == "bug" else "setze folgenden Funktionswunsch um"
    reporter = f.user.name if f.user else "unbekannt"
    when = _to_local(f.created_at).strftime("%d.%m.%Y %H:%M") if f.created_at else "unbekannt"
    screenshots = (
        f"\nScreenshots: {len(f.photos)} angehängt (siehe Verwaltung > Feedback)" if f.photos else ""
    )
    return (
        f"Bitte {action} in ClubHUB:\n\n"
        f"Typ: {kind}\n"
        f"Titel: {f.title}\n\n"
        f"Beschreibung:\n{f.description or '(keine Beschreibung angegeben)'}\n\n"
        f"Priorität: {_FEEDBACK_PRIORITY_LABEL.get(f.priority, f.priority)}\n"
        f"Gemeldet von: {reporter} am {when}\n"
        f"Betroffene Seite: {f.url or 'nicht angegeben'}"
        f"{screenshots}"
    )


@app.get("/admin/feedback")
def admin_feedback_page(request: Request, db: Session = Depends(get_db)):
    admin = require_staff_or_developer(request, db)
    items = _sort_feedback(db.query(models.Feedback).all())
    prompts = {f.id: _feedback_prompt(f) for f in items}
    return templates.TemplateResponse("admin_feedback.html", {
        "request": request,
        "user": admin,
        "items": items,
        "prompts": prompts,
    })


@app.post("/admin/feedback/{feedback_id}/status")
def admin_feedback_set_status(
    feedback_id: int, request: Request, status: str = Form(...), db: Session = Depends(get_db)
):
    require_staff_or_developer(request, db)
    if status in ("open", "in_progress", "done"):
        item = db.query(models.Feedback).filter(models.Feedback.id == feedback_id).first()
        if item:
            item.status = status
            db.commit()
    return RedirectResponse("/admin/feedback", status_code=302)


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
        "rooms": db.query(models.Room).order_by(models.Room.name).all(),
        "groups": db.query(models.Group).all(),
    })


@app.post("/reports")
async def reports_create(
    request: Request,
    background_tasks: BackgroundTasks,
    room_id: str = Form(""),
    comment: str = Form(...),
    priority: str = Form("normal"),
    category: str = Form("sonstiges"),
    assigned_group_id: str = Form(""),
    photos: list[UploadFile] = File([]),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)

    if category not in REPORT_CATEGORIES:
        category = "sonstiges"
    roomless = category in REPORT_ROOMLESS_CATEGORIES
    assigned_group_id = int(assigned_group_id) if assigned_group_id.strip() else None
    room_id_int = int(room_id) if room_id.strip() else None

    # "Anschaffung" hängt an keinem Bereich, sondern an einer zuständigen
    # Gruppe (man bestellt für die eigene Gruppe, nicht für einen Raum) - und
    # hat keine Priorität, die ist ein Dringlichkeits-Maß für Defekte. Bei
    # allen anderen Kategorien bleibt der Bereich wie bisher Pflicht.
    if roomless:
        priority = None
        room_id_int = None
        if not assigned_group_id:
            return RedirectResponse(
                _with_toast("/reports", "Für „Anschaffung“ bitte eine zuständige Gruppe auswählen.", "error"),
                status_code=302,
            )
    else:
        if priority not in REPORT_PRIORITIES:
            priority = "normal"
        if not room_id_int:
            return RedirectResponse(
                _with_toast("/reports", "Bitte einen Bereich auswählen.", "error"), status_code=302,
            )

    report = models.Report(
        room_id=room_id_int, user_id=user.id, comment=comment, priority=priority, category=category,
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
    msg = comment if len(comment) <= 200 else comment[:197] + "…"
    if roomless:
        # Kein Bereich -> immer genau die (Pflicht-)Zuständigkeitsgruppe
        # benachrichtigen, keine Bereichsgruppen-Ableitung möglich.
        assigned_group = (
            db.query(models.Group).options(*group_loaders).filter(models.Group.id == assigned_group_id).first()
        )
        if assigned_group:
            background_tasks.add_task(
                notify_group, assigned_group, "Neuer Materialwunsch", msg, "default", f"/reports?focus=report-{report.id}"
            )
    else:
        room = (
            db.query(models.Room)
            .options(joinedload(models.Room.groups).options(*group_loaders))
            .filter(models.Room.id == room_id_int)
            .first()
        )
        if room:
            title = f"Neue Meldung: {room.name}"
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
                    background_tasks.add_task(notify_group, assigned_group, title, msg, "default", f"/reports?focus=report-{report.id}")
            else:
                for group in room.groups:
                    background_tasks.add_task(notify_group, group, title, msg, "default", f"/reports?focus=report-{report.id}")

    return RedirectResponse("/reports", status_code=302)


STATUS_CHANGE_TITLES = {
    "open": "Wieder geöffnet",
    "in_progress": "In Bearbeitung",
    "done": "Erledigt",
}


@app.post("/reports/{report_id}/status")
def reports_set_status(
    report_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if status not in REPORT_STATUSES:
        return RedirectResponse("/reports", status_code=302)

    # Gruppen inkl. Kanäle + Mitglieder/Push-Abos hier bereits vollständig laden
    # (nicht erst lazy in notify_group/notify_user), da die Session geschlossen
    # ist, sobald der Background-Task nach dem Response tatsächlich läuft.
    group_loaders = (
        joinedload(models.Group.channels),
        joinedload(models.Group.users).joinedload(models.User.push_subscriptions),
    )
    report = (
        db.query(models.Report)
        .options(
            joinedload(models.Report.room).joinedload(models.Room.groups).options(*group_loaders),
            joinedload(models.Report.assigned_group).options(*group_loaders),
            joinedload(models.Report.user).joinedload(models.User.push_subscriptions),
        )
        .filter(models.Report.id == report_id)
        .first()
    )
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

        # Nur der Melder und die zuständige(n) Gruppe(n) informieren (nicht
        # alle Nutzer) - z.B. damit man mitbekommt, dass sich schon jemand
        # kümmert, ohne dass es doppelt gemacht wird. Anders als bei einer
        # neuen Meldung bewusst ohne Arbeitszeit-Fenster: bleibt konsistent
        # mit dem Verhalten der ursprünglichen Meldungs-Benachrichtigung.
        title = f"{STATUS_CHANGE_TITLES[status]}: {report.room.name if report.room else 'Materialwunsch'}"
        comment_preview = report.comment if len(report.comment) <= 120 else report.comment[:117] + "…"
        msg = f"{user.name}: „{comment_preview}“"
        target_groups = [report.assigned_group] if report.assigned_group else (list(report.room.groups) if report.room else [])
        focus_url = f"/reports?focus=report-{report.id}"
        for group in target_groups:
            background_tasks.add_task(notify_group, group, title, msg, "default", focus_url)
        already_reached = any(report.user.id == member.id for group in target_groups for member in group.users)
        if report.user.id != user.id and not already_reached:
            background_tasks.add_task(notify_user, report.user, title, msg, focus_url)

    return RedirectResponse("/reports", status_code=302)


@app.post("/reports/{report_id}/delete")
def reports_delete(report_id: int, request: Request, db: Session = Depends(get_db)):
    """Meldung endgültig löschen - nur für den Melder selbst (z.B. um Test-
    Meldungen aufzuräumen) oder Admin/Schichtleiter, nicht für beliebige
    eingeloggte Nutzer wie beim Statuswechsel/Kommentieren."""
    user = require_login(request, db)
    report = db.query(models.Report).filter(models.Report.id == report_id).first()
    if report and (report.user_id == user.id or user.is_admin or user.is_shift_lead or user.is_developer):
        for photo in report.photos:
            try:
                os.remove(os.path.join(REPORT_PHOTOS_DIR, photo.filename))
            except OSError:
                pass
        db.delete(report)
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
        new_group_id = int(assigned_group_id) if assigned_group_id.strip() else None
        # Ohne Bereich (Anschaffung) gibt es keine Bereichsgruppen-Ableitung
        # als Rückfallebene - eine leere Zuständigkeit würde die Meldung ohne
        # jeden Benachrichtigungsweg zurücklassen, daher hier nicht zulassen.
        if report.room_id is None and new_group_id is None:
            return RedirectResponse("/reports", status_code=302)
        report.assigned_group_id = new_group_id
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


# ---------- Termine ----------

APPOINTMENT_RECURRENCE_LABELS = {7: "Wöchentlich", 14: "Alle 2 Wochen", 30: "Monatlich"}


def _is_appointment_visible(appointment, user) -> bool:
    """Ein Termin ohne Gruppe ('– nur ich –' im Formular) ist reine
    Privatsache - sichtbar nur fuer den Ersteller selbst und Admins, nicht
    fuer andere Kolleg:innen/Schichtleiter. Mit Gruppe bleibt er wie bisher
    fuer alle sichtbar (die Gruppe steuert dort schon, wer benachrichtigt
    wird - das ist bereits eine bewusste Freigabe an mehrere Personen)."""
    if appointment.group_id is not None:
        return True
    if not user:
        return False
    return user.id == appointment.user_id or user.is_admin


@app.get("/appointments")
def appointments_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    now_local = ntptime.now_utc().astimezone(APP_TIMEZONE)
    today = now_local.date()
    groups = db.query(models.Group).order_by(models.Group.name).all()

    entries = []
    for a in db.query(models.Appointment).order_by(models.Appointment.date.asc()).all():
        if not _is_appointment_visible(a, user):
            continue
        date_local = _aware(a.date).astimezone(APP_TIMEZONE).date()
        days_until = (date_local - today).days
        if days_until == 0:
            when = "Heute"
        elif days_until == 1:
            when = "Morgen"
        elif days_until > 1:
            when = f"in {days_until} Tagen"
        elif days_until == -1:
            when = "Gestern"
        else:
            when = f"vor {-days_until} Tagen"
        entries.append({
            "obj": a,
            "date_local": date_local,
            "when": when,
            "past": days_until < 0,
            "recurrence_label": APPOINTMENT_RECURRENCE_LABELS.get(
                a.recurrence_days, f"Alle {a.recurrence_days} Tage" if a.recurrence_days else None
            ),
        })

    return templates.TemplateResponse("appointments.html", {
        "request": request,
        "user": user,
        "entries": entries,
        "groups": groups,
    })


@app.post("/appointments")
def appointments_create(
    request: Request,
    name: str = Form(...),
    date: str = Form(...),
    recurrence_days: str = Form(""),
    notify_days_before: float = Form(1.0),
    group_id: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    parsed_date = _parse_local_date(date)
    if parsed_date:
        db.add(models.Appointment(
            name=name,
            date=parsed_date,
            recurrence_days=int(recurrence_days) if recurrence_days else None,
            notify_days_before=notify_days_before,
            group_id=int(group_id) if group_id else None,
            user_id=user.id,
        ))
        db.commit()
    return RedirectResponse("/appointments", status_code=302)


@app.post("/appointments/{appointment_id}/edit")
def appointments_edit(
    appointment_id: int,
    request: Request,
    name: str = Form(...),
    date: str = Form(...),
    recurrence_days: str = Form(""),
    notify_days_before: float = Form(1.0),
    group_id: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
    if appt and (appt.user_id == user.id or user.is_admin or user.is_shift_lead or user.is_developer):
        parsed_date = _parse_local_date(date)
        if parsed_date:
            appt.date = parsed_date
        appt.name = name
        appt.recurrence_days = int(recurrence_days) if recurrence_days else None
        appt.notify_days_before = notify_days_before
        appt.group_id = int(group_id) if group_id else None
        # Nach jeder Änderung neu erinnern lassen, statt evtl. für immer still
        # zu bleiben, weil der alte Termin schon als benachrichtigt galt.
        appt.notified = False
        db.commit()
    return RedirectResponse("/appointments", status_code=302)


@app.post("/appointments/{appointment_id}/delete")
def appointments_delete(appointment_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    appt = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
    if appt and (appt.user_id == user.id or user.is_admin or user.is_shift_lead or user.is_developer):
        db.delete(appt)
        db.commit()
    return RedirectResponse("/appointments", status_code=302)


# ---------- Urlaub ----------

@app.get("/vacations")
def vacations_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    now_local = ntptime.now_utc().astimezone(APP_TIMEZONE)
    today = now_local.date()
    users = db.query(models.User).order_by(models.User.name).all()

    entries = []
    for v in db.query(models.Vacation).order_by(models.Vacation.start_date.asc()).all():
        start_local = _aware(v.start_date).astimezone(APP_TIMEZONE).date()
        end_local = _aware(v.end_date).astimezone(APP_TIMEZONE).date()
        if today < start_local:
            state = "upcoming"
        elif today > end_local:
            state = "past"
        else:
            state = "current"
        entries.append({
            "obj": v,
            "start_local": start_local,
            "end_local": end_local,
            "state": state,
        })

    return templates.TemplateResponse("vacations.html", {
        "request": request,
        "user": user,
        "entries": entries,
        "users": users,
    })


@app.post("/vacations")
def vacations_create(
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
    user_id: str = Form(""),
    db: Session = Depends(get_db),
):
    actor = require_login(request, db)
    start = _parse_local_date(start_date)
    end = _parse_local_date(end_date)
    if not start or not end:
        return RedirectResponse("/vacations", status_code=302)
    if end < start:
        start, end = end, start
    # Nur Admin/Schichtleiter dürfen für jemand anderen eintragen - sonst
    # gilt der Eintrag immer für den, der ihn selbst anlegt.
    target_user_id = actor.id
    if user_id and (actor.is_admin or actor.is_shift_lead or actor.is_developer):
        target_user_id = int(user_id)
    db.add(models.Vacation(
        user_id=target_user_id, start_date=start, end_date=end, created_by_id=actor.id,
    ))
    db.commit()
    return RedirectResponse("/vacations", status_code=302)


@app.post("/vacations/{vacation_id}/edit")
def vacations_edit(
    vacation_id: int,
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
    user_id: str = Form(""),
    db: Session = Depends(get_db),
):
    actor = require_login(request, db)
    vac = db.query(models.Vacation).filter(models.Vacation.id == vacation_id).first()
    if vac and (vac.user_id == actor.id or actor.is_admin or actor.is_shift_lead or actor.is_developer):
        start = _parse_local_date(start_date)
        end = _parse_local_date(end_date)
        if start and end:
            if end < start:
                start, end = end, start
            vac.start_date = start
            vac.end_date = end
        if user_id and (actor.is_admin or actor.is_shift_lead or actor.is_developer):
            vac.user_id = int(user_id)
        db.commit()
    return RedirectResponse("/vacations", status_code=302)


@app.post("/vacations/{vacation_id}/delete")
def vacations_delete(vacation_id: int, request: Request, db: Session = Depends(get_db)):
    actor = require_login(request, db)
    vac = db.query(models.Vacation).filter(models.Vacation.id == vacation_id).first()
    if vac and (vac.user_id == actor.id or actor.is_admin or actor.is_shift_lead or actor.is_developer):
        db.delete(vac)
        db.commit()
    return RedirectResponse("/vacations", status_code=302)


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


@app.post("/push/subscriptions/{sub_id}/delete")
def push_delete_subscription(sub_id: int, request: Request, db: Session = Depends(get_db)):
    """Entfernt ein einzelnes Gerät aus der Liste (z.B. altes/falsches Test-
    Gerät) - anders als /push/unsubscribe (vom Gerät selbst aufgerufen) hier
    aus der Ferne über die "Meine Geräte"-Übersicht in /profile bzw. für
    Admins auch für andere Nutzer in der Nutzerverwaltung."""
    user = require_login(request, db)
    sub = db.query(models.PushSubscription).filter(models.PushSubscription.id == sub_id).first()
    if sub and (sub.user_id == user.id or user.is_admin):
        next_url = "/admin/users" if sub.user_id != user.id else "/profile"
        db.delete(sub)
        db.commit()
        return RedirectResponse(next_url, status_code=302)
    return RedirectResponse("/profile", status_code=302)


# ---------- Historie ----------

_HISTORY_PAGE_SIZES = (25, 50, 100)


@app.get("/history")
def history(
    request: Request,
    q: str = "",
    room_id: str = "",
    user_id: str = "",
    period: str = "",
    page: int = 1,
    page_size: int = 25,
    db: Session = Depends(get_db),
):
    user, redirect = require_login_page(request, db)
    if redirect:
        return redirect
    # Serverseitige Suche/Filter/Seiten statt alles auf einmal zu laden - bei
    # ca. 30-40 Erledigungen/Tag über alle Bereiche wäre eine Seite, die die
    # komplette Historie clientseitig rendert und filtert, in ein bis zwei
    # Jahren spürbar langsam (mehrere MB HTML, zehntausende DOM-Zeilen). So
    # bleibt jede Seite unabhängig von der Gesamtgröße der Historie leicht.
    query = (
        db.query(models.Completion)
        .join(models.Task, models.Completion.task_id == models.Task.id)
        .join(models.Room, models.Task.room_id == models.Room.id)
        .join(models.User, models.Completion.user_id == models.User.id)
    )
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(
            models.Task.name.ilike(like), models.Room.name.ilike(like), models.User.name.ilike(like),
        ))
    if room_id.isdigit():
        query = query.filter(models.Task.room_id == int(room_id))
    if user_id.isdigit():
        query = query.filter(models.Completion.user_id == int(user_id))
    if period in ("today", "week", "month"):
        now_local = ntptime.now_utc().astimezone(APP_TIMEZONE)
        if period == "today":
            start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            start_local = (now_local - timedelta(days=now_local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(models.Completion.timestamp >= start_local.astimezone(timezone.utc))

    total = query.count()
    page_size = page_size if page_size in _HISTORY_PAGE_SIZES else 25
    page_count = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, page), page_count)
    completions = (
        query.order_by(models.Completion.timestamp.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return templates.TemplateResponse("history.html", {
        "request": request,
        "user": user,
        "completions": completions,
        "rooms": db.query(models.Room).order_by(models.Room.name).all(),
        "history_users": db.query(models.User).order_by(models.User.name).all(),
        "q": q,
        "room_id": room_id,
        "user_id": user_id,
        "period": period,
        "page": page,
        "page_size": page_size,
        "page_sizes": _HISTORY_PAGE_SIZES,
        "page_count": page_count,
        "total": total,
        "range_start": (page - 1) * page_size + 1 if total else 0,
        "range_end": min(page * page_size, total),
    })


@app.post("/history/{completion_id}/delete")
def delete_completion(completion_id: int, request: Request, db: Session = Depends(get_db)):
    """Zieht eine versehentlich als erledigt markierte Aufgabe zurück - nur
    Admin/Entwickler, da das den Fälligkeits-Status der Aufgabe direkt beeinflusst."""
    require_admin_or_developer(request, db)
    completion = db.query(models.Completion).filter(models.Completion.id == completion_id).first()
    if completion:
        db.delete(completion)
        db.commit()
    return RedirectResponse("/history", status_code=302)


# ---------- Login / Logout ----------

@app.get("/login")
def login_form(request: Request, next: str = "/", db: Session = Depends(get_db)):
    return templates.TemplateResponse("login.html", {
        "request": request, "user": None, "next": next, "error": None,
    })


@app.post("/login")
def login_submit(
    request: Request,
    identifier: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    user = find_user_by_identifier(db, identifier)
    if not user or not verify_password(password, user.password_hash):
        if next in ("", "/"):
            # Login-Formular ist direkt ins Dashboard eingebettet (kein
            # Umweg über die eigenständige /login-Seite) - Fehler dorthin
            # zurückspiegeln statt auf die separate Seite umzuleiten.
            return RedirectResponse("/?login_error=1", status_code=302)
        return templates.TemplateResponse("login.html", {
            "request": request, "user": None, "next": next,
            "error": "Benutzername/Personalnummer oder Passwort ist falsch.",
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
        "password_error": None,
        "password_success": False,
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


@app.post("/profile/password")
def profile_change_password(
    request: Request,
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    error = None
    if not password.strip():
        error = "Passwort darf nicht leer sein."
    elif password != password_confirm:
        error = "Die Passwörter stimmen nicht überein."
    else:
        user.password_hash = hash_password(password)
        db.commit()
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": user,
        "password_error": error,
        "password_success": error is None,
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
    admin = require_staff_or_developer(request, db)
    return templates.TemplateResponse("admin_index.html", {
        "request": request,
        "user": admin,
        "users_count": db.query(models.User).count(),
        "groups_count": db.query(models.Group).count(),
        "groups_with_hours_count": db.query(models.Group).filter(models.Group.work_start_hour.isnot(None)).count(),
        "rooms_count": db.query(models.Room).count(),
        "inventory_count": db.query(models.InventoryItem).count(),
        "low_stock_count": db.query(models.InventoryItem)
            .filter(models.InventoryItem.stock_current < models.InventoryItem.stock_min).count(),
        "channels_count": db.query(models.NotificationChannel).count(),
        "open_time_entries_count": db.query(models.TimeEntry).filter(models.TimeEntry.clock_out.is_(None)).count(),
        "nfc_tags_count": db.query(models.NfcTag).count(),
        "nfc_tags_unscanned_count": db.query(models.NfcTag).filter(models.NfcTag.uid.is_(None)).count(),
        "feedback_count": db.query(models.Feedback).count(),
        "feedback_open_count": db.query(models.Feedback).filter(models.Feedback.status != "done").count(),
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
    admin = require_staff_or_developer(request, db)
    return templates.TemplateResponse("admin_groups.html", {
        "request": request,
        "user": admin,
        "groups": db.query(models.Group).order_by(models.Group.name).all(),
        "channels": db.query(models.NotificationChannel).all(),
    })


@app.get("/admin/rooms")
def admin_rooms_page(request: Request, db: Session = Depends(get_db)):
    admin = require_staff_or_developer(request, db)
    # Bewusst schlank: nur Bereiche anlegen/löschen + Gruppenzuweisung. Die
    # Aufgabenverwaltung läuft direkt auf der jeweiligen Bereichs-Detailseite
    # (/room/{id}), sichtbar für Admins/Schichtleiter/Entwickler.
    return templates.TemplateResponse("admin_rooms.html", {
        "request": request,
        "user": admin,
        "rooms": db.query(models.Room).order_by(models.Room.name).all(),
        "groups": db.query(models.Group).order_by(models.Group.name).all(),
    })


_INVENTORY_STATUS_ORDER = {"low": 0, "ok": 1}


def _build_admin_inventory_context(request: Request, admin, sort: str, db: Session, img_fetch_failed: bool = False) -> dict:
    """Kontext fuer admin_inventory.html - ausgelagert aus der GET-Route,
    damit die create/edit/delete-Routen bei fetch()-Anfragen (siehe
    X-Requested-With: fetch) direkt das aktualisierte Tabellen-Fragment
    (_inventory_content.html) zurueckgeben koennen, ohne Full-Page-Reload
    und ohne die aktuell gewaehlte Sortierung zu verlieren."""
    items = filter_inventory_for_user(db.query(models.InventoryItem).all(), admin)
    if sort == "name":
        items.sort(key=lambda i: i.name.lower())
    elif sort == "category":
        items.sort(key=lambda i: (i.category is None, (i.category or "").lower(), i.name.lower()))
    else:
        sort = "status"
        items.sort(key=lambda i: (_INVENTORY_STATUS_ORDER[compute_inventory_status(i)["status"]], i.name.lower()))
    return {
        "request": request,
        "user": admin,
        "inventory_items": items,
        "sort": sort,
        "groups": db.query(models.Group).all(),
        "categories": sorted({i.category for i in items if i.category}),
        "locations": sorted({i.location for i in items if i.location}),
        "units": sorted({i.unit for i in items if i.unit}),
        "pack_units": sorted({i.pack_unit for i in items if i.pack_unit}),
        "img_fetch_failed": img_fetch_failed,
    }


@app.get("/admin/inventory")
def admin_inventory_page(
    request: Request, img_fetch_failed: str = "", sort: str = "status", db: Session = Depends(get_db)
):
    admin = require_staff_or_developer(request, db)
    return templates.TemplateResponse(
        "admin_inventory.html", _build_admin_inventory_context(request, admin, sort, db, bool(img_fetch_failed))
    )


def _parse_local_dt(value: str):
    """Wandelt den Wert eines <input type=datetime-local> (als APP_TIMEZONE
    interpretiert, da Admins hier lokale Uhrzeiten nachtragen) in ein
    UTC-Datetime für die Speicherung um."""
    if not value:
        return None
    naive = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    return naive.replace(tzinfo=APP_TIMEZONE).astimezone(timezone.utc)


def _parse_local_date(value: str):
    """Wie _parse_local_dt, nur für <input type=date> (ohne Uhrzeit) - der
    Termin wird als lokale Mitternacht (APP_TIMEZONE) interpretiert."""
    if not value:
        return None
    naive = datetime.strptime(value, "%Y-%m-%d")
    return naive.replace(tzinfo=APP_TIMEZONE).astimezone(timezone.utc)


def _normalize_active_weekdays(values: list[int]):
    """Wandelt die angehakten Wochentage (0=Mo...6=So) aus dem Formular in
    den DB-Wert um - alle sieben (oder aus Versehen keiner) angehakt heißt
    "keine Einschränkung" (None), wie bisher an jedem Tag aktiv."""
    selected = sorted(set(values))
    if len(selected) == 0 or len(selected) == 7:
        return None
    return ",".join(str(d) for d in selected)


AUDIT_CHAIN_GENESIS = "genesis"


def _audit_chain_string(audit, prev_hash: str) -> str:
    """Kanonische, eindeutige Darstellung eines Änderungsprotokoll-Eintrags
    für die Hash-Kette - jedes Feld inkl. Hash des Vorgängers, damit eine
    nachträgliche Änderung/Löschung/Einfügung an beliebiger Stelle die Kette
    ab dort erkennbar bricht. Alle Zeitstempel über _aware() normalisiert,
    da SQLite die tzinfo beim Rückschreiben verliert (siehe _aware) - ohne
    das würde derselbe Eintrag vor und nach einem Commit/Reload unterschiedliche
    Hashes ergeben, weil .isoformat() dann mit/ohne UTC-Offset-Suffix formatiert."""
    parts = [
        str(audit.id),
        str(audit.time_entry_id),
        str(audit.entry_user_id),
        audit.action,
        str(audit.changed_by_id),
        _aware(audit.changed_at).isoformat() if audit.changed_at else "",
        _aware(audit.old_clock_in).isoformat() if audit.old_clock_in else "",
        _aware(audit.old_clock_out).isoformat() if audit.old_clock_out else "",
        _aware(audit.new_clock_in).isoformat() if audit.new_clock_in else "",
        _aware(audit.new_clock_out).isoformat() if audit.new_clock_out else "",
        prev_hash,
    ]
    return "|".join(parts)


def _log_timeclock_change(
    db: Session, entry, action: str, changed_by_id: int,
    old_clock_in=None, old_clock_out=None,
):
    """Protokolliert eine Zeiterfassungs-Korrektur (Admin oder Selbstbe-
    arbeitung im Nutzer-Modus) - siehe TimeEntryAudit. Nicht für normale
    Kiosk-/Dashboard-Stempelungen gedacht, die gelten als Originalquelle.
    Verkettet den neuen Eintrag per Hash mit dem vorherigen (siehe
    verify_audit_chain) - dafür muss der Eintrag erst geflusht werden, um
    seine id/changed_at zu kennen, bevor der Hash berechnet werden kann."""
    audit = models.TimeEntryAudit(
        time_entry_id=entry.id,
        entry_user_id=entry.user_id,
        action=action,
        changed_by_id=changed_by_id,
        old_clock_in=old_clock_in,
        old_clock_out=old_clock_out,
        new_clock_in=entry.clock_in if action != "deleted" else None,
        new_clock_out=entry.clock_out if action != "deleted" else None,
    )
    db.add(audit)
    db.flush()
    prev = (
        db.query(models.TimeEntryAudit)
        .filter(models.TimeEntryAudit.id < audit.id)
        .order_by(models.TimeEntryAudit.id.desc())
        .first()
    )
    prev_hash = prev.hash if prev and prev.hash else AUDIT_CHAIN_GENESIS
    audit.hash = hashlib.sha256(_audit_chain_string(audit, prev_hash).encode()).hexdigest()


def verify_audit_chain(db: Session) -> dict:
    """Läuft die Hash-Kette des Änderungsprotokolls komplett durch und meldet
    die Stelle des ersten Bruchs, falls vorhanden - erkennt jede nachträgliche
    Änderung/Löschung/Einfügung eines Eintrags direkt an der Datenbank (am
    App-Layer vorbei). Einträge von vor Einführung der Kette (hash=None)
    werden als neuer Kettenanfang behandelt, nicht als Bruch."""
    rows = db.query(models.TimeEntryAudit).order_by(models.TimeEntryAudit.id.asc()).all()
    prev_hash = AUDIT_CHAIN_GENESIS
    for row in rows:
        if row.hash is None:
            prev_hash = AUDIT_CHAIN_GENESIS
            continue
        expected = hashlib.sha256(_audit_chain_string(row, prev_hash).encode()).hexdigest()
        if row.hash != expected:
            return {"ok": False, "broken_at_id": row.id, "checked": len(rows)}
        prev_hash = row.hash
    return {"ok": True, "checked": len(rows)}


def _parse_month_param(month: str, local_now):
    """Parst '?month=YYYY-MM' zu (Monats-Anfang, Monats-Anfang-danach, als
    lokale date-Objekte) - fällt auf den laufenden Kalendermonat zurück, wenn
    der Parameter fehlt oder ungültig ist."""
    today = local_now.date()
    if month:
        try:
            year, mon = (int(p) for p in month.split("-", 1))
            month_start = today.replace(year=year, month=mon, day=1)
        except (ValueError, TypeError):
            month_start = today.replace(day=1)
    else:
        month_start = today.replace(day=1)
    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1)
    return month_start, next_month_start


def _build_timeclock_context(
    request: Request, admin, mode: str, month: str, date_from: str, date_to: str, db: Session, **extra
) -> dict:
    """Baut den vollen Kontext fuer admin_timeclock.html - ausgelagert aus der
    GET-Route, damit die Import-Analyse-Route bei gefundenen Konflikten
    dieselbe Seite (samt Konflikt-Modal, siehe **extra) direkt zurueckgeben
    kann, statt auf eine leere Seite umzuleiten. Nutzt _resolve_timeclock_period
    - dieselbe Monat/Freier-Zeitraum-Filterlogik wie die Mitarbeiter-
    Zeiterfassung (siehe dort _build_timeclock_self_context)."""
    now = ntptime.now_utc()
    local_now = now.astimezone(APP_TIMEZONE)
    period = _resolve_timeclock_period(mode, month, date_from, date_to, local_now)
    range_start_utc, range_end_utc = period["range_start_utc"], period["range_end_utc"]

    users = db.query(models.User).order_by(models.User.name).all()
    users_by_id = {u.id: u for u in users}

    range_entries = (
        db.query(models.TimeEntry)
        .filter(models.TimeEntry.clock_in >= range_start_utc, models.TimeEntry.clock_in < range_end_utc)
        .order_by(models.TimeEntry.clock_in.desc())
        .all()
    )

    rows = []
    hours_by_user = {u.id: 0.0 for u in users}
    active_user_ids = set()
    for e in range_entries:
        u = users_by_id.get(e.user_id)
        hours = _entry_hours(e, now)
        hours_by_user[e.user_id] = hours_by_user.get(e.user_id, 0.0) + hours
        active_user_ids.add(e.user_id)
        clock_in_local = _aware(e.clock_in).astimezone(APP_TIMEZONE)
        clock_out_local = _aware(e.clock_out).astimezone(APP_TIMEZONE) if e.clock_out else None
        rows.append({
            "id": e.id,
            "user_id": e.user_id,
            "user_name": u.name if u else "?",
            # Kurzes Wochentagskuerzel vor dem Datum (z.B. "Do, 23.07.2026") -
            # bei 50+ Mitarbeitern/vielen Buchungen erleichtert das, Wochenend-
            # oder Wochentags-Muster auf einen Blick zu erkennen.
            "date_label": f"{_WEEKDAY_ABBR_DE[clock_in_local.weekday()]}, {clock_in_local.strftime('%d.%m.%Y')}",
            "clock_in_local": clock_in_local,
            "clock_out_local": clock_out_local,
            "open": e.clock_out is None,
            "hours": hours,
        })

    # Zeitraum-KPIs statt Einzelkacheln pro Mitarbeiter - skaliert auch bei
    # vielen Mitarbeitern (die einzelnen Zeitraum-Stunden je Person stehen
    # stattdessen im "Alle Mitarbeiter"-Dropdown, siehe Template).
    total_range_hours = sum(hours_by_user.values())
    total_bookings = len(rows)
    active_employee_count = len(active_user_ids)

    device = get_authorized_device(db)

    # Serverseitig grosszuegiger gedeckelt (200) als initial angezeigt (10) -
    # der Rest liegt bereits im DOM und wird nur per "Aeltere laden"-Button
    # sichtbar gemacht (siehe admin_timeclock.html), ohne Nachladen vom Server.
    audit_entries = []
    for a in (
        db.query(models.TimeEntryAudit)
        .order_by(models.TimeEntryAudit.changed_at.desc())
        .limit(200)
        .all()
    ):
        audit_entries.append({
            "changed_at_local": _aware(a.changed_at).astimezone(APP_TIMEZONE),
            "changed_by_name": a.changed_by.name if a.changed_by else "?",
            "entry_user_name": a.entry_user.name if a.entry_user else "?",
            "action": a.action,
            "old_clock_in_local": _aware(a.old_clock_in).astimezone(APP_TIMEZONE) if a.old_clock_in else None,
            "old_clock_out_local": _aware(a.old_clock_out).astimezone(APP_TIMEZONE) if a.old_clock_out else None,
            "new_clock_in_local": _aware(a.new_clock_in).astimezone(APP_TIMEZONE) if a.new_clock_in else None,
            "new_clock_out_local": _aware(a.new_clock_out).astimezone(APP_TIMEZONE) if a.new_clock_out else None,
        })

    chain_result = None
    if "chain_ok" in request.query_params:
        chain_result = {"ok": True}
    elif "chain_broken_at" in request.query_params:
        chain_result = {"ok": False, "broken_at_id": request.query_params["chain_broken_at"]}

    context = {
        "request": request,
        "user": admin,
        "users": users,
        "rows": rows,
        "hours_by_user": hours_by_user,
        "total_range_hours": total_range_hours,
        "total_bookings": total_bookings,
        "active_employee_count": active_employee_count,
        "mode": period["mode"],
        "month_value": period["month_value"],
        "prev_month_value": period["prev_month_value"],
        "next_month_value": period["next_month_value"],
        "date_from_value": period["date_from_value"],
        "date_to_value": period["date_to_value"],
        "device": device,
        "device_authorized_local": _aware(device.authorized_at).astimezone(APP_TIMEZONE) if device else None,
        "kiosk_url": f"{str(request.base_url).rstrip('/')}/timeclock/kiosk",
        "timeclock_user_mode": get_app_settings(db).timeclock_user_mode,
        "audit_entries": audit_entries,
        "chain_result": chain_result,
        "import_conflicts": None,
        "import_payload": None,
        "import_user_name": None,
        "import_new_count": None,
        "import_duplicate_count": None,
        "import_invalid_count": None,
    }
    context.update(extra)
    return context


@app.get("/admin/timeclock")
def admin_timeclock_page(
    request: Request,
    mode: str = "month",
    month: str = "",
    date_from: str = Query("", alias="from"),
    date_to: str = Query("", alias="to"),
    db: Session = Depends(get_db),
):
    # Nur Admin, nicht Schichtleiter: Korrekturen wirken sich direkt auf den
    # berechneten Verdienst eines Nutzers aus (dieselbe Einschränkung wie
    # beim Stundensatz/Sollzeit selbst).
    admin = require_admin(request, db)
    context = _build_timeclock_context(request, admin, mode, month, date_from, date_to, db)
    # Monatswechsel per Klick/Auswahl läuft clientseitig über fetch() statt
    # einer echten Navigation (siehe admin_timeclock.html) - der Header
    # markiert diese Anfragen, damit wir nur das Tabellen-Fragment statt der
    # kompletten Seite zurückschicken (kein Full-Page-Reload, keine doppelte
    # Navigation/Header/Footer-Übertragung).
    if request.headers.get("X-Requested-With") == "fetch":
        return templates.TemplateResponse("_timeclock_fragment.html", context)
    return templates.TemplateResponse("admin_timeclock.html", context)


@app.get("/admin/timeclock/{user_id}/export.pdf")
def admin_timeclock_export_pdf(
    user_id: int,
    request: Request,
    mode: str = "month",
    month: str = "",
    date_from: str = Query("", alias="from"),
    date_to: str = Query("", alias="to"),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404)
    period = _resolve_timeclock_period(mode, month, date_from, date_to, ntptime.now_utc().astimezone(APP_TIMEZONE))
    return _timeentry_pdf_response(target, _timeentry_pdf_rows(db, target.id, period["range_start_utc"], period["range_end_utc"]))


@app.get("/admin/timeclock/export.csv")
def admin_timeclock_export_csv(
    request: Request,
    mode: str = "month",
    month: str = "",
    date_from: str = Query("", alias="from"),
    date_to: str = Query("", alias="to"),
    user_id: int = 0,
    db: Session = Depends(get_db),
):
    """CSV-Export im selben Pipe-getrennten Format wie der App-Import (siehe
    _parse_timeclock_import), erweitert um Dauer/Notizen-Spalten - respektiert
    den gerade aktiven Filter (Monat oder freier Zeitraum, siehe
    _resolve_timeclock_period) sowie den optionalen Mitarbeiter-Filter der
    Tabelle (siehe admin_timeclock.html, folgt per JS demselben Dropdown wie
    der PDF-Export). "Notizen" ist aktuell immer leer, da TimeEntry (noch)
    kein Notiz-Feld hat - Spalte bleibt trotzdem stehen, wie angefordert.
    Kopfzeile beginnt mit '#' und nennt die Kommen/Gehen-Spalten CHECKIN/
    CHECKOUT, damit _parse_timeclock_import die Datei unveraendert wieder
    einlesen kann (Re-Import-Kompatibilitaet)."""
    require_admin(request, db)
    now = ntptime.now_utc()
    local_now = now.astimezone(APP_TIMEZONE)
    period = _resolve_timeclock_period(mode, month, date_from, date_to, local_now)
    range_start_utc, range_end_utc = period["range_start_utc"], period["range_end_utc"]
    period_label = period["month_value"] if period["mode"] == "month" else f"{period['date_from_value']}_{period['date_to_value']}"

    query = db.query(models.TimeEntry).filter(
        models.TimeEntry.clock_in >= range_start_utc, models.TimeEntry.clock_in < range_end_utc,
    )
    target_user = None
    if user_id:
        target_user = db.query(models.User).filter(models.User.id == user_id).first()
        if not target_user:
            raise HTTPException(status_code=404)
        query = query.filter(models.TimeEntry.user_id == user_id)
    entries = query.order_by(models.TimeEntry.clock_in).all()

    users_by_id = {u.id: u for u in db.query(models.User).all()}

    def esc(value: str) -> str:
        # Pipe im Namen/Notiz waere sonst faelschlich ein Spaltentrenner.
        return (value or "").replace("|", "/")

    lines = ["#Datum|Mitarbeiter|Checkin|Checkout|Dauer_Minuten|Dauer_Formatiert|Notizen"]
    for e in entries:
        clock_in_local = _aware(e.clock_in).astimezone(APP_TIMEZONE)
        clock_out_local = _aware(e.clock_out).astimezone(APP_TIMEZONE) if e.clock_out else None
        u = users_by_id.get(e.user_id)
        hours = _entry_hours(e, now)
        lines.append("|".join([
            clock_in_local.strftime("%d.%m.%Y"),
            esc(u.name if u else "?"),
            clock_in_local.strftime(_TIMECLOCK_IMPORT_DT_FORMAT),
            clock_out_local.strftime(_TIMECLOCK_IMPORT_DT_FORMAT) if clock_out_local else "",
            str(round(hours * 60)),
            format_hours_de(hours),
            "",
        ]))
    csv_content = "\n".join(lines) + "\n"

    # Content-Disposition-Header muss ASCII-sicher sein (rohe Umlaute in
    # Response-Headern sind kein gueltiges HTTP) - deshalb hier transliterieren,
    # unabhaengig vom CSV-Inhalt selbst, der korrekt UTF-8 bleibt.
    ascii_name = (
        target_user.name.translate(str.maketrans("äöüÄÖÜß", "aouAOUs")) if target_user else "alle"
    )
    name_part = re.sub(r"[^A-Za-z0-9_-]+", "-", ascii_name).strip("-") or "mitarbeiter"
    filename = f"zeiterfassung-{period_label}-{name_part}.csv"
    return Response(
        # utf-8-sig (BOM) statt reinem UTF-8, damit Excel unter Windows die
        # Datei beim Doppelklick zuverlaessig als UTF-8 statt der System-
        # Codepage erkennt - sonst werden Umlaute dort falsch dargestellt.
        content=csv_content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/admin/timeclock/verify-chain")
def admin_timeclock_verify_chain(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    result = verify_audit_chain(db)
    if result["ok"]:
        return RedirectResponse("/admin/timeclock?chain_ok=1", status_code=302)
    return RedirectResponse(f"/admin/timeclock?chain_broken_at={result['broken_at_id']}", status_code=302)


@app.post("/admin/timeclock/mode")
def admin_timeclock_set_mode(request: Request, mode: str = Form(...), db: Session = Depends(get_db)):
    """Umschalten zwischen Terminal-Modus (Standard, ein autorisiertes Gerät)
    und Nutzer-Modus (jeder stempelt/bearbeitet auf eigenem Gerät) - z.B.
    solange offen ist, ob/wie die Zeiterfassung offiziell eingeführt wird."""
    require_admin(request, db)
    settings = get_app_settings(db)
    settings.timeclock_user_mode = (mode == "user")
    db.commit()
    return RedirectResponse("/admin/timeclock", status_code=302)


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


def _timeclock_redirect(
    mode: str, month: str, date_from: str, date_to: str, message: str, kind: str = "success",
) -> str:
    """Redirect zurück zur Zeiterfassungs-Tabelle, behält dabei den gerade
    aktiven Filter (Monat oder freier Zeitraum, siehe _resolve_timeclock_period)
    statt immer auf den aktuellen Monat zurückzuspringen, und zeigt eine kurze
    Toast-Bestätigung (siehe _with_toast)."""
    params = {"mode": mode}
    if mode == "range":
        params["from"] = date_from
        params["to"] = date_to
    else:
        params["month"] = month
    query = urlencode({k: v for k, v in params.items() if v})
    target = f"/admin/timeclock?{query}" if query else "/admin/timeclock"
    return _with_toast(target, message, kind)


_TIMECLOCK_IMPORT_DT_FORMAT = "%Y-%m-%d %H:%M:%S"


def _parse_timeclock_import(text: str):
    """Parst den Pipe-getrennten Export von 'Zeiterfassung Pro' (DynamicG,
    Datei z.B. timerec-workunits-pro.txt). Kopfzeile beginnt mit '#' und
    benennt die Spalten (z.B. 'DATE|CHECKIN|CHECKOUT|...') - CHECKIN/CHECKOUT
    werden über den Spaltennamen gesucht statt über eine feste Position, da
    die Datei weitere, für uns irrelevante Spalten enthalten kann.
    Gibt (rows, skipped_invalid, error) zurück: rows als Liste von
    (clock_in, clock_out) naiven datetimes (noch ohne Zeitzone), skipped_invalid
    als Anzahl übersprungener leerer/kaputter Zeilen, error als Fehlertext
    oder None (nur bei einem grundsätzlichen Formatproblem der ganzen Datei,
    einzelne kaputte Zeilen sind kein Fehler, siehe Anforderung "ignoriere
    ungültige Zeilen")."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return [], 0, "Datei ist leer."
    header = lines[0]
    if not header.startswith("#"):
        return [], 0, "Unbekanntes Dateiformat (Kopfzeile mit '#' fehlt)."
    columns = [c.strip().upper() for c in header.lstrip("#").strip().split("|")]
    if "CHECKIN" not in columns or "CHECKOUT" not in columns:
        return [], 0, "Spalten CHECKIN/CHECKOUT nicht in der Kopfzeile gefunden."
    checkin_idx = columns.index("CHECKIN")
    checkout_idx = columns.index("CHECKOUT")

    rows = []
    skipped_invalid = 0
    for line in lines[1:]:
        fields = line.split("|")
        if len(fields) <= max(checkin_idx, checkout_idx):
            skipped_invalid += 1
            continue
        checkin_raw = fields[checkin_idx].strip()
        checkout_raw = fields[checkout_idx].strip()
        if not checkin_raw:
            skipped_invalid += 1
            continue
        try:
            clock_in = datetime.strptime(checkin_raw, _TIMECLOCK_IMPORT_DT_FORMAT)
        except ValueError:
            skipped_invalid += 1
            continue
        clock_out = None
        if checkout_raw:
            try:
                clock_out = datetime.strptime(checkout_raw, _TIMECLOCK_IMPORT_DT_FORMAT)
            except ValueError:
                skipped_invalid += 1
                continue
        if clock_out and clock_out <= clock_in:
            skipped_invalid += 1
            continue
        rows.append((clock_in, clock_out))
    return rows, skipped_invalid, None


def _analyze_timeclock_import(db: Session, user_id: int, parsed_rows: list) -> dict:
    """Vergleicht geparste Import-Zeilen mit den bestehenden Buchungen des
    Mitarbeiters - gruppiert dafuer nach lokalem Kalendertag (nicht mehr nur
    nach exakter Kommen-Zeit wie zuvor), da ein Konflikt laut Anforderung
    genau dann vorliegt, wenn fuer denselben Tag schon eine Buchung existiert,
    deren Zeiten aber abweichen. Vereinfachung bei mehreren bestehenden
    Buchungen an einem Tag (Splitschicht o.ae., in der Praxis selten): die
    zeitlich erste davon dient als Vergleichspartner fuer einen Konflikt.
    Gibt {"new": [(clock_in_iso, clock_out_iso|None), ...], "duplicate_count": int,
    "conflicts": [...]} zurueck - noch ohne jede DB-Aenderung."""
    existing = db.query(models.TimeEntry).filter(models.TimeEntry.user_id == user_id).all()
    existing_by_date: dict = {}
    for e in existing:
        local_date = _aware(e.clock_in).astimezone(APP_TIMEZONE).date()
        existing_by_date.setdefault(local_date, []).append(e)
    for day_entries in existing_by_date.values():
        day_entries.sort(key=lambda e: e.clock_in)

    new_rows = []
    conflicts = []
    duplicate_count = 0
    for clock_in_naive, clock_out_naive in parsed_rows:
        clock_in_utc = clock_in_naive.replace(tzinfo=APP_TIMEZONE).astimezone(timezone.utc)
        clock_out_utc = (
            clock_out_naive.replace(tzinfo=APP_TIMEZONE).astimezone(timezone.utc) if clock_out_naive else None
        )
        local_date = clock_in_naive.date()
        same_day = existing_by_date.get(local_date, [])

        if not same_day:
            new_rows.append((clock_in_utc.isoformat(), clock_out_utc.isoformat() if clock_out_utc else None))
            continue

        exact_match = next(
            (e for e in same_day if _aware(e.clock_in) == clock_in_utc and _aware(e.clock_out) == clock_out_utc),
            None,
        )
        if exact_match:
            duplicate_count += 1
            continue

        existing_entry = same_day[0]
        existing_clock_out_local = (
            _aware(existing_entry.clock_out).astimezone(APP_TIMEZONE).strftime("%H:%M")
            if existing_entry.clock_out else "läuft noch"
        )
        conflicts.append({
            "existing_id": existing_entry.id,
            "date_label": f"{_WEEKDAY_ABBR_DE[clock_in_naive.weekday()]}, {clock_in_naive.strftime('%d.%m.%Y')}",
            "existing_clock_in_local": _aware(existing_entry.clock_in).astimezone(APP_TIMEZONE).strftime("%H:%M"),
            "existing_clock_out_local": existing_clock_out_local,
            "new_clock_in_local": clock_in_naive.strftime("%H:%M"),
            "new_clock_out_local": clock_out_naive.strftime("%H:%M") if clock_out_naive else "läuft noch",
            "new_clock_in_utc": clock_in_utc.isoformat(),
            "new_clock_out_utc": clock_out_utc.isoformat() if clock_out_utc else None,
        })
    return {"new": new_rows, "duplicate_count": duplicate_count, "conflicts": conflicts}


def _apply_timeclock_import_new_rows(db: Session, admin, user_id: int, new_rows: list) -> int:
    """Fuegt bereits als eindeutig 'neu' analysierte Zeilen ein (siehe
    _analyze_timeclock_import) - new_rows sind (clock_in_iso, clock_out_iso|None)."""
    imported = 0
    for clock_in_iso, clock_out_iso in new_rows:
        entry = models.TimeEntry(
            user_id=user_id,
            clock_in=datetime.fromisoformat(clock_in_iso),
            clock_out=datetime.fromisoformat(clock_out_iso) if clock_out_iso else None,
        )
        db.add(entry)
        db.flush()  # damit entry.id für das Protokoll existiert
        _log_timeclock_change(db, entry, "added", admin.id)
        imported += 1
    return imported


def _timeclock_import_summary_message(
    user_name: str, imported: int, duplicate_count: int, invalid_count: int, conflict_summary: str | None = None,
) -> str:
    parts = [f"{imported} {'Buchung' if imported == 1 else 'Buchungen'} für {user_name} importiert"]
    if duplicate_count:
        parts.append(f"{duplicate_count} Duplikat{'e' if duplicate_count != 1 else ''} übersprungen")
    if conflict_summary:
        parts.append(conflict_summary)
    if invalid_count:
        parts.append(f"{invalid_count} ungültige Zeile{'n' if invalid_count != 1 else ''} ignoriert")
    return ", ".join(parts) + "."


@app.post("/admin/timeclock/import/analyze")
async def admin_timeclock_import_analyze(
    request: Request,
    user_id: int = Form(...),
    file: UploadFile = File(...),
    mode: str = Form("month"),
    month: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
    db: Session = Depends(get_db),
):
    """Erster Schritt des Imports: liest die Datei ein und vergleicht sie mit
    dem Bestand, schreibt aber noch nichts in die DB. Ohne Konflikte wird
    sofort wie gehabt fertig importiert (Redirect + Toast); mit Konflikten
    wird stattdessen dieselbe Seite mit dem Konflikt-Lösungs-Modal
    zurückgegeben (siehe admin_timeclock.html) - der eigentliche Import
    passiert dann erst über /admin/timeclock/import/resolve."""
    admin = require_admin(request, db)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return RedirectResponse(_timeclock_redirect(mode, month, date_from, date_to, "Unbekannter Mitarbeiter.", "error"), status_code=302)

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    parsed_rows, skipped_invalid, error = _parse_timeclock_import(text)
    if error:
        return RedirectResponse(_timeclock_redirect(mode, month, date_from, date_to, error, "error"), status_code=302)

    analysis = _analyze_timeclock_import(db, user_id, parsed_rows)

    if not analysis["conflicts"]:
        imported = _apply_timeclock_import_new_rows(db, admin, user_id, analysis["new"])
        db.commit()
        message = _timeclock_import_summary_message(
            user.name, imported, analysis["duplicate_count"], skipped_invalid,
        )
        return RedirectResponse(_timeclock_redirect(mode, month, date_from, date_to, message), status_code=302)

    payload = json.dumps({
        "user_id": user_id,
        "mode": mode,
        "month": month,
        "date_from": date_from,
        "date_to": date_to,
        "new": analysis["new"],
        "duplicate_count": analysis["duplicate_count"],
        "invalid_count": skipped_invalid,
        "conflicts": [
            {
                "existing_id": c["existing_id"],
                "new_clock_in_utc": c["new_clock_in_utc"],
                "new_clock_out_utc": c["new_clock_out_utc"],
            }
            for c in analysis["conflicts"]
        ],
    })
    context = _build_timeclock_context(
        request, admin, mode, month, date_from, date_to, db,
        import_conflicts=analysis["conflicts"],
        import_payload=payload,
        import_user_name=user.name,
        import_new_count=len(analysis["new"]),
        import_duplicate_count=analysis["duplicate_count"],
        import_invalid_count=skipped_invalid,
    )
    return templates.TemplateResponse("admin_timeclock.html", context)


@app.post("/admin/timeclock/import/resolve")
def admin_timeclock_import_resolve(
    request: Request,
    payload: str = Form(...),
    resolution: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    """Zweiter Schritt: wendet die im Konflikt-Modal getroffenen
    Entscheidungen an (siehe admin_timeclock.html) und schreibt jetzt
    tatsächlich in die DB - payload enthält die bei der Analyse bereits
    klassifizierten Zeilen (siehe admin_timeclock_import_analyze), resolution
    ist parallel zu payload["conflicts"] indiziert (eine <select>-Auswahl pro
    Konflikt-Zeile im Formular, in derselben Reihenfolge gerendert)."""
    admin = require_admin(request, db)
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError, KeyError):
        return RedirectResponse(_timeclock_redirect("month", "", "", "", "Ungültige Import-Daten - bitte Datei erneut hochladen.", "error"), status_code=302)

    user_id = data.get("user_id")
    mode = data.get("mode", "month")
    month = data.get("month", "")
    date_from = data.get("date_from", "")
    date_to = data.get("date_to", "")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return RedirectResponse(_timeclock_redirect(mode, month, date_from, date_to, "Unbekannter Mitarbeiter.", "error"), status_code=302)

    imported = _apply_timeclock_import_new_rows(db, admin, user_id, data.get("new", []))

    conflicts = data.get("conflicts", [])
    kept_existing = replaced = kept_both = 0
    for i, conflict in enumerate(conflicts):
        action = resolution[i] if i < len(resolution) else "keep_existing"
        entry = db.query(models.TimeEntry).filter(models.TimeEntry.id == conflict["existing_id"]).first()
        if action == "replace" and entry:
            old_clock_in, old_clock_out = entry.clock_in, entry.clock_out
            entry.clock_in = datetime.fromisoformat(conflict["new_clock_in_utc"])
            entry.clock_out = (
                datetime.fromisoformat(conflict["new_clock_out_utc"]) if conflict.get("new_clock_out_utc") else None
            )
            _log_timeclock_change(db, entry, "edited", admin.id, old_clock_in, old_clock_out)
            replaced += 1
        elif action == "keep_both":
            new_entry = models.TimeEntry(
                user_id=user_id,
                clock_in=datetime.fromisoformat(conflict["new_clock_in_utc"]),
                clock_out=(
                    datetime.fromisoformat(conflict["new_clock_out_utc"]) if conflict.get("new_clock_out_utc") else None
                ),
            )
            db.add(new_entry)
            db.flush()
            _log_timeclock_change(db, new_entry, "added", admin.id)
            kept_both += 1
        else:
            kept_existing += 1
    db.commit()

    conflict_summary = None
    if conflicts:
        detail_parts = []
        if replaced:
            detail_parts.append(f"{replaced} ersetzt")
        if kept_both:
            detail_parts.append(f"{kept_both} beide behalten")
        if kept_existing:
            detail_parts.append(f"{kept_existing} bestehend behalten")
        conflict_summary = f"{len(conflicts)} Konflikt{'e' if len(conflicts) != 1 else ''} gelöst ({', '.join(detail_parts)})"

    message = _timeclock_import_summary_message(
        user.name, imported, data.get("duplicate_count", 0), data.get("invalid_count", 0), conflict_summary,
    )
    return RedirectResponse(_timeclock_redirect(mode, month, date_from, date_to, message), status_code=302)


@app.post("/admin/timeclock/{user_id}/add")
def admin_timeclock_add(
    user_id: int,
    request: Request,
    clock_in: str = Form(...),
    clock_out: str = Form(""),
    mode: str = Form("month"),
    month: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
    db: Session = Depends(get_db),
):
    admin = require_admin(request, db)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user:
        entry = models.TimeEntry(
            user_id=user.id,
            clock_in=_parse_local_dt(clock_in),
            clock_out=_parse_local_dt(clock_out),
        )
        db.add(entry)
        db.flush()  # damit entry.id für das Protokoll existiert
        _log_timeclock_change(db, entry, "added", admin.id)
        db.commit()
    return RedirectResponse(_timeclock_redirect(mode, month, date_from, date_to, "Buchung hinzugefügt."), status_code=302)


@app.post("/admin/timeclock/{entry_id}/edit")
def admin_timeclock_edit(
    entry_id: int,
    request: Request,
    clock_in: str = Form(...),
    clock_out: str = Form(""),
    mode: str = Form("month"),
    month: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
    db: Session = Depends(get_db),
):
    admin = require_admin(request, db)
    entry = db.query(models.TimeEntry).filter(models.TimeEntry.id == entry_id).first()
    if entry:
        old_in, old_out = entry.clock_in, entry.clock_out
        entry.clock_in = _parse_local_dt(clock_in)
        entry.clock_out = _parse_local_dt(clock_out)
        _log_timeclock_change(db, entry, "edited", admin.id, old_in, old_out)
        db.commit()
    return RedirectResponse(_timeclock_redirect(mode, month, date_from, date_to, "Buchung gespeichert."), status_code=302)


@app.post("/admin/timeclock/{entry_id}/delete")
def admin_timeclock_delete(
    entry_id: int,
    request: Request,
    mode: str = Form("month"),
    month: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
    db: Session = Depends(get_db),
):
    admin = require_admin(request, db)
    entry = db.query(models.TimeEntry).filter(models.TimeEntry.id == entry_id).first()
    if entry:
        _log_timeclock_change(db, entry, "deleted", admin.id, entry.clock_in, entry.clock_out)
        db.delete(entry)
        db.commit()
    return RedirectResponse(_timeclock_redirect(mode, month, date_from, date_to, "Buchung gelöscht."), status_code=302)


@app.get("/admin/notifications")
def admin_notifications_page(request: Request, db: Session = Depends(get_db)):
    admin = require_staff_or_developer(request, db)
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


def _admin_system_context(
    request: Request, admin, restore_error: str | None = None,
    import_error: str | None = None, import_summary: dict | None = None,
) -> dict:
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
        "import_categories": data_export.CATEGORIES,
        "import_category_labels": data_export.CATEGORY_LABELS,
        "import_error": import_error,
        "import_summary": import_summary,
    }


@app.get("/admin/system")
def admin_system_page(request: Request, db: Session = Depends(get_db)):
    admin = require_staff_or_developer(request, db)
    return templates.TemplateResponse("admin_system.html", _admin_system_context(request, admin))


@app.post("/admin/system/resync-ntp")
def admin_resync_ntp(request: Request, db: Session = Depends(get_db)):
    require_staff_or_developer(request, db)
    ntptime.sync()
    return RedirectResponse("/admin/system", status_code=302)


@app.get("/admin/system/backup")
def admin_download_backup(request: Request, db: Session = Depends(get_db)):
    require_admin_or_developer(request, db)
    data = backup.create_backup_bytes()
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{backup.backup_filename()}"'},
    )


@app.get("/admin/system/scheduled-backup/{filename}/download")
def admin_download_scheduled_backup(filename: str, request: Request, db: Session = Depends(get_db)):
    require_admin_or_developer(request, db)
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


@app.get("/admin/system/export-data")
def admin_export_data(request: Request, db: Session = Depends(get_db)):
    require_admin_or_developer(request, db)
    data = data_export.export_data_json(db)
    filename = f"clubhub-daten-{ntptime.now_utc().strftime('%Y%m%d-%H%M%S')}.json"
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/admin/system/import-data")
async def admin_import_data(
    request: Request,
    file: UploadFile = File(...),
    categories: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    admin = require_admin_or_developer(request, db)
    raw = await file.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return templates.TemplateResponse(
            "admin_system.html",
            _admin_system_context(request, admin, import_error="Keine gültige JSON-Datei (Struktur-Export erwartet, kein .db-Backup)."),
        )
    selected = {c for c in categories if c in data_export.CATEGORIES}
    summary = data_export.import_data_json(db, data, selected, admin)
    return templates.TemplateResponse("admin_system.html", _admin_system_context(request, admin, import_summary=summary))


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
    admin = require_admin_or_developer(request, db)
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
    admin = require_admin_or_developer(request, db)
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
    require_staff_or_developer(request, db)
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
    require_staff_or_developer(request, db)
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
    require_admin_or_developer(request, db)
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
    require_staff_or_developer(request, db)
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
    require_staff_or_developer(request, db)
    channel = db.query(models.NotificationChannel).filter(models.NotificationChannel.id == channel_id).first()
    if channel:
        channel.name = name
        channel.type = type
        channel.target = target or None
        db.commit()
    return RedirectResponse("/admin/notifications", status_code=302)


@app.post("/admin/channels/{channel_id}/delete")
def admin_delete_channel(channel_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin_or_developer(request, db)
    channel = db.query(models.NotificationChannel).filter(models.NotificationChannel.id == channel_id).first()
    if channel:
        db.delete(channel)
        db.commit()
    return RedirectResponse("/admin/notifications", status_code=302)


@app.post("/admin/users")
def admin_add_user(
    request: Request,
    name: str = Form(...),
    password: str = Form(...),
    personnel_number: str = Form(""),
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
    # Nur Admins dürfen beim Anlegen direkt Admin-/Schichtleiter-/Entwickler-/
    # Pauschalkraft-Rechte vergeben; ein Schichtleiter legt immer nur normale
    # Mitarbeiter-Konten an, auch wenn im Formular (z.B. per direktem POST)
    # etwas anderes übermittelt wird.
    user = models.User(
        name=name,
        password_hash=hash_password(password),
        personnel_number=personnel_number.strip() or None,
        is_admin=actor.is_admin and role == "admin",
        is_shift_lead=actor.is_admin and role == "schichtleiter",
        is_developer=actor.is_admin and role == "entwickler",
        is_flat_rate=actor.is_admin and role == "pauschalkraft",
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
    password: str = Form(""),
    personnel_number: str = Form(""),
    group_id: str = Form(""),
    role: str = Form(""),
    target_hours_per_month: str = Form(""),
    db: Session = Depends(get_db),
):
    actor = require_admin_or_shift_lead(request, db)
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if target:
        # Schichtleiter dürfen Admin-Konten gar nicht anfassen - sonst könnten sie
        # sich per Passwort-Reset eines Admin-Kontos faktisch selbst zum Admin machen.
        if target.is_admin and not actor.is_admin:
            return RedirectResponse("/admin/users", status_code=302)

        target.name = name
        target.personnel_number = personnel_number.strip() or None
        if password:
            target.password_hash = hash_password(password)
        group = db.query(models.Group).filter(models.Group.id == int(group_id)).first() if group_id else None
        target.groups = [group] if group else []

        # Nur ein Admin darf Rollen ändern (Admin/Schichtleiter/Entwickler/
        # Pauschalkraft vergeben oder entziehen); bei einem Schichtleiter als
        # Akteur bleibt die Rolle des bearbeiteten Nutzers unverändert, egal
        # was im Formular ankommt.
        if actor.is_admin:
            desired_role = role if role in ("mitarbeiter", "schichtleiter", "admin", "entwickler", "pauschalkraft") else "mitarbeiter"
            other_admins = db.query(models.User).filter(models.User.is_admin == True, models.User.id != user_id).count()
            if target.is_admin and desired_role != "admin" and other_admins == 0:
                desired_role = "admin"  # letzten Admin nicht versehentlich entmachten
            target.is_admin = desired_role == "admin"
            target.is_shift_lead = desired_role == "schichtleiter"
            target.is_developer = desired_role == "entwickler"
            target.is_flat_rate = desired_role == "pauschalkraft"
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
    require_staff_or_developer(request, db)
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
    require_staff_or_developer(request, db)
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if room:
        room.name = name
        room.groups = db.query(models.Group).filter(models.Group.id.in_(group_ids)).all()
        db.commit()
    return RedirectResponse("/admin/rooms", status_code=302)


@app.post("/admin/rooms/{room_id}/delete")
def admin_delete_room(room_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin_or_developer(request, db)
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if room:
        db.delete(room)
        db.commit()
    return RedirectResponse("/admin/rooms", status_code=302)


@app.post("/admin/tasks")
def admin_add_task(
    request: Request,
    room_ids: list[int] = Form(...),
    name: str = Form(...),
    interval_hours: float = Form(...),
    warn_hours: float = Form(5.0),
    note: str = Form(""),
    last_completed: str = Form(""),
    group_ids: list[int] = Form([]),
    active_weekdays: list[int] = Form([]),
    db: Session = Depends(get_db),
):
    actor = require_staff_or_developer(request, db)
    # Optional rückdatierbar: ohne Angabe würde der Turnus sonst erst ab dem
    # Anlegezeitpunkt zählen, auch wenn schon vorher geputzt wurde.
    completed_at = _parse_local_dt(last_completed)
    # Explizite Gruppen-Zuständigkeit optional - leer bedeutet automatisch
    # "alle Gruppen des Bereichs" (siehe Task.groups-Kommentar in models.py).
    selected_groups = db.query(models.Group).filter(models.Group.id.in_(group_ids)).all() if group_ids else []
    awd = _normalize_active_weekdays(active_weekdays)
    # Raumzentrierte Logik (Kopieren statt Verlinken): jeder ausgewählte
    # Bereich bekommt eine eigene, unabhängige Aufgabe mit eigener
    # Erledigungs-Historie - kein group_key, kein Zusammenhang zwischen den
    # Instanzen. Bearbeiten/Löschen betrifft danach ausschließlich die
    # jeweilige Bereichs-Instanz.
    for rid in room_ids:
        task = models.Task(
            room_id=rid, name=name, interval_hours=interval_hours, warn_hours=warn_hours,
            note=note.strip() or None, groups=list(selected_groups), active_weekdays=awd,
        )
        if completed_at:
            task.completions.append(models.Completion(user_id=actor.id, timestamp=completed_at))
        db.add(task)
    db.commit()
    # Zurück zur Bereichs-Detailseite (dort lebt die Inline-Aufgabenverwaltung
    # jetzt, siehe room.html) statt zur schlanken Verwaltung > Bereiche.
    return RedirectResponse(f"/room/{room_ids[0]}", status_code=302)


@app.post("/admin/tasks/{task_id}/edit")
def admin_edit_task(
    task_id: int,
    request: Request,
    name: str = Form(...),
    interval_hours: float = Form(...),
    warn_hours: float = Form(5.0),
    note: str = Form(""),
    group_ids: list[int] = Form([]),
    active_weekdays: list[int] = Form([]),
    db: Session = Depends(get_db),
):
    require_staff_or_developer(request, db)
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        return RedirectResponse("/rooms", status_code=302)

    # Explizite Gruppen-Zuständigkeit optional - leer bedeutet automatisch
    # "alle Gruppen des Bereichs" (siehe Task.groups-Kommentar in models.py).
    selected_groups = db.query(models.Group).filter(models.Group.id.in_(group_ids)).all() if group_ids else []
    # Bearbeiten betrifft ausschließlich diese eine Aufgabe des aktuellen
    # Bereichs (raumzentrierte Logik, kein bereichsübergreifendes Verlinken
    # mehr) - der Bereich selbst ist dabei nicht änderbar.
    task.name = name
    task.interval_hours = interval_hours
    task.warn_hours = warn_hours
    task.note = note.strip() or None
    task.groups = list(selected_groups)
    task.active_weekdays = _normalize_active_weekdays(active_weekdays)
    db.commit()
    return RedirectResponse(f"/room/{task.room_id}", status_code=302)


@app.post("/admin/tasks/{task_id}/delete")
def admin_delete_task(task_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin_or_developer(request, db)
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    room_id = task.room_id if task else None
    if task:
        db.delete(task)
        db.commit()
    return RedirectResponse(f"/room/{room_id}" if room_id else "/rooms", status_code=302)


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
    sort: str = "status",
    db: Session = Depends(get_db),
):
    admin = require_staff_or_developer(request, db)
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
    if request.headers.get("X-Requested-With") == "fetch":
        return templates.TemplateResponse(
            "_inventory_content.html", _build_admin_inventory_context(request, admin, sort, db, image_fetch_failed)
        )
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
    sort: str = "status",
    db: Session = Depends(get_db),
):
    actor = require_staff_or_developer(request, db)
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
        if request.headers.get("X-Requested-With") == "fetch":
            return templates.TemplateResponse(
                "_inventory_content.html", _build_admin_inventory_context(request, actor, sort, db, image_fetch_failed)
            )
        return RedirectResponse(_with_img_fetch_hint(next, image_fetch_failed), status_code=302)
    if request.headers.get("X-Requested-With") == "fetch":
        raise HTTPException(status_code=404, detail="Artikel nicht gefunden")
    return RedirectResponse(next if next.startswith("/") else "/admin/inventory", status_code=302)


@app.post("/admin/inventory/{item_id}/delete")
def admin_delete_inventory_item(item_id: int, request: Request, sort: str = "status", db: Session = Depends(get_db)):
    admin = require_admin_or_developer(request, db)
    item = db.query(models.InventoryItem).filter(models.InventoryItem.id == item_id).first()
    if item:
        db.delete(item)
        db.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return templates.TemplateResponse(
            "_inventory_content.html", _build_admin_inventory_context(request, admin, sort, db)
        )
    return RedirectResponse("/admin/inventory", status_code=302)
