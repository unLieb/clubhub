import os
import signal
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Depends, Form, File, UploadFile, BackgroundTasks
from fastapi.responses import RedirectResponse, Response, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from .database import Base, engine, get_db, DB_PATH
from . import models
from . import ntptime
from . import backup
from . import version
from .auth import hash_pin, verify_pin, get_current_user, require_login, require_admin, require_admin_or_shift_lead
from .status import task_status
from .scheduler import start_scheduler
from .notifications import notify_group

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Reinigungsplan")
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "change-me-in-production"))
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

# Foto-Uploads zu Meldungen liegen im persistenten Datenverzeichnis (nicht im
# Image), damit sie Container-Neustarts/-Updates überstehen.
UPLOADS_DIR = os.path.join(os.path.dirname(DB_PATH), "uploads")
REPORT_PHOTOS_DIR = os.path.join(UPLOADS_DIR, "reports")
os.makedirs(REPORT_PHOTOS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
# Live-Uhr (base.html) braucht auf jeder Seite die NTP-korrigierte Server-Zeit,
# ohne dass jede Route sie einzeln in den Kontext geben muss.
templates.env.globals["server_epoch_ms"] = lambda: int(ntptime.now_utc().timestamp() * 1000)
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


def _migrate_inventory_extras(db: Session):
    """Neue, frei vergebbare Kategorie/Lagerort- sowie Nachbestell-URL-Spalte
    pro Inventarartikel (kein Altdaten-Bezug)."""
    _ensure_column(db, "inventory_items", "category", "TEXT")
    _ensure_column(db, "inventory_items", "location", "TEXT")
    _ensure_column(db, "inventory_items", "reorder_url", "TEXT")


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
        _migrate_report_photos(db)
        _migrate_inventory_extras(db)
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
                overdue_tasks.append(task)
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
    done_today_reports = sum(
        1 for r in all_reports
        if r.status == "done" and r.resolved_at and r.resolved_at.astimezone(timezone.utc).date() == today
    )

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": get_current_user(request, db),
        "rooms": rooms,
        "attention_rooms": attention_rooms,
        "room_status": room_status,
        "stats": {"done_today": done_today, "due_soon": due_soon_count, "overdue": overdue_count},
        "recent_completions": recent_completions,
        "overdue_tasks": overdue_tasks[:6],
        "report_stats": {"open": len(open_reports), "done_today": done_today_reports},
        "recent_reports": open_reports[:5],
        "now": now,
    })


@app.get("/rooms")
def rooms_overview(request: Request, db: Session = Depends(get_db)):
    rooms = db.query(models.Room).all()
    now = ntptime.now_utc()
    room_status, _, _, _ = compute_room_statuses(rooms, now)
    return templates.TemplateResponse("rooms.html", {
        "request": request,
        "user": get_current_user(request, db),
        "rooms": rooms,
        "room_status": room_status,
    })


# ---------- Raum / Scan ----------

@app.get("/room/{room_id}")
def room_view(room_id: int, request: Request, db: Session = Depends(get_db)):
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if not room:
        return RedirectResponse("/", status_code=302)
    statuses = {t.id: task_status(t) for t in room.tasks}
    return templates.TemplateResponse("room.html", {
        "request": request,
        "user": get_current_user(request, db),
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


# ---------- Inventar ----------

def distinct_inventory_values(db: Session, column: str) -> list[str]:
    col = getattr(models.InventoryItem, column)
    return sorted({v for (v,) in db.query(col).distinct().all() if v})


def compute_inventory_status(item) -> dict:
    """Ampel-Status ('ok' | 'low' | 'empty') + Füllstand in Prozent (relativ
    zum Mindestbestand, ab dem Mindestbestand gilt die Anzeige als voll) für
    die farbige Bestandsanzeige."""
    if item.stock_current <= 0:
        status = "empty"
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

    return {"status": status, "fill_pct": fill_pct}


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
def inventory_overview(request: Request, db: Session = Depends(get_db)):
    items = db.query(models.InventoryItem).order_by(models.InventoryItem.name).all()
    now = ntptime.now_utc()
    inventory_status = {item.id: compute_inventory_status(item) for item in items}
    inventory_consumption = {item.id: compute_inventory_consumption(item, now) for item in items}
    inventory_chart = {item.id: build_consumption_chart(inventory_consumption[item.id]) for item in items}
    return templates.TemplateResponse("inventory.html", {
        "request": request,
        "user": get_current_user(request, db),
        "items": items,
        "inventory_status": inventory_status,
        "inventory_consumption": inventory_consumption,
        "inventory_chart": inventory_chart,
        "groups": db.query(models.Group).all(),
        "categories": distinct_inventory_values(db, "category"),
        "locations": distinct_inventory_values(db, "location"),
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
    if item:
        item.stock_current += delta
        db.add(models.InventoryMovement(item_id=item.id, user_id=user.id, delta=delta, note=note or None))
        if item.stock_current >= item.stock_min:
            item.notified = False
        db.commit()
    return RedirectResponse("/inventory", status_code=302)


# ---------- Meldungen ----------

REPORT_PRIORITIES = ("critical", "high", "normal", "low")
REPORT_PRIORITY_RANK = {p: i for i, p in enumerate(REPORT_PRIORITIES)}
REPORT_CATEGORIES = ("defekt", "material", "reinigung", "sonstiges")


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
    reports = db.query(models.Report).all()
    now = ntptime.now_utc()
    open_reports = _sort_reports([r for r in reports if r.status != "done"])
    done_reports = _sort_reports([r for r in reports if r.status == "done"])
    report_meta = {r.id: compute_report_meta(r, now) for r in reports}
    return templates.TemplateResponse("reports.html", {
        "request": request,
        "user": get_current_user(request, db),
        "open_reports": open_reports,
        "done_reports": done_reports,
        "report_meta": report_meta,
        "rooms": db.query(models.Room).all(),
    })


@app.post("/reports")
async def reports_create(
    request: Request,
    background_tasks: BackgroundTasks,
    room_id: int = Form(...),
    comment: str = Form(...),
    priority: str = Form("normal"),
    category: str = Form("sonstiges"),
    photos: list[UploadFile] = File([]),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)

    if priority not in REPORT_PRIORITIES:
        priority = "normal"
    if category not in REPORT_CATEGORIES:
        category = "sonstiges"

    report = models.Report(room_id=room_id, user_id=user.id, comment=comment, priority=priority, category=category)
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

    # Gruppen inkl. Kanäle hier bereits vollständig laden (nicht erst lazy in
    # notify_group) - die Session ist geschlossen, sobald der Background-Task
    # nach dem Response tatsächlich läuft, sonst DetachedInstanceError.
    room = (
        db.query(models.Room)
        .options(joinedload(models.Room.groups).joinedload(models.Group.channels))
        .filter(models.Room.id == room_id)
        .first()
    )
    if room:
        title = f"Neue Meldung: {room.name}"
        msg = comment if len(comment) <= 200 else comment[:197] + "…"
        for group in room.groups:
            background_tasks.add_task(notify_group, group, title, msg)

    return RedirectResponse("/reports", status_code=302)


@app.post("/reports/{report_id}/resolve")
def reports_resolve(report_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    report = db.query(models.Report).filter(models.Report.id == report_id).first()
    if report and report.status != "done":
        report.status = "done"
        report.resolved_at = ntptime.now_utc()
        report.resolved_by_id = user.id
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


# ---------- Historie ----------

@app.get("/history")
def history(request: Request, db: Session = Depends(get_db)):
    completions = db.query(models.Completion).order_by(models.Completion.timestamp.desc()).limit(200).all()
    return templates.TemplateResponse("history.html", {
        "request": request,
        "user": get_current_user(request, db),
        "completions": completions,
    })


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
        "ntp_status": ntptime.status(),
    })


@app.get("/admin/users")
def admin_users_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    return templates.TemplateResponse("admin_users.html", {
        "request": request,
        "user": admin,
        "users": db.query(models.User).all(),
        "groups": db.query(models.Group).all(),
    })


@app.get("/admin/groups")
def admin_groups_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    return templates.TemplateResponse("admin_groups.html", {
        "request": request,
        "user": admin,
        "groups": db.query(models.Group).all(),
        "channels": db.query(models.NotificationChannel).all(),
    })


@app.get("/admin/rooms")
def admin_rooms_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    return templates.TemplateResponse("admin_rooms.html", {
        "request": request,
        "user": admin,
        "rooms": db.query(models.Room).all(),
        "groups": db.query(models.Group).all(),
    })


@app.get("/admin/tasks")
def admin_tasks_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    return templates.TemplateResponse("admin_tasks.html", {
        "request": request,
        "user": admin,
        "tasks": db.query(models.Task).all(),
        "rooms": db.query(models.Room).all(),
    })


@app.get("/admin/inventory")
def admin_inventory_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    return templates.TemplateResponse("admin_inventory.html", {
        "request": request,
        "user": admin,
        "inventory_items": db.query(models.InventoryItem).all(),
        "groups": db.query(models.Group).all(),
        "categories": distinct_inventory_values(db, "category"),
        "locations": distinct_inventory_values(db, "location"),
    })


@app.get("/admin/notifications")
def admin_notifications_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    return templates.TemplateResponse("admin_notifications.html", {
        "request": request,
        "user": admin,
        "channels": db.query(models.NotificationChannel).all(),
    })


@app.get("/admin/system")
def admin_system_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin_or_shift_lead(request, db)
    return templates.TemplateResponse("admin_system.html", {
        "request": request,
        "user": admin,
        "app_timezone": os.environ.get("APP_TIMEZONE", "Europe/Berlin"),
        "ntp_status": ntptime.status(),
    })


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


def _trigger_restart():
    # SIGTERM statt hartem os._exit(), damit uvicorn sauber herunterfährt;
    # Docker startet den Container per restart-Policy automatisch neu und
    # durchläuft dabei die normalen Startup-Migrationen gegen die neue Datei.
    os.kill(os.getpid(), signal.SIGTERM)


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
        return templates.TemplateResponse("admin_system.html", {
            "request": request,
            "user": admin,
            "app_timezone": os.environ.get("APP_TIMEZONE", "Europe/Berlin"),
            "ntp_status": ntptime.status(),
            "restore_error": error,
        })
    background_tasks.add_task(_trigger_restart)
    return HTMLResponse("""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="6;url=/admin/system">
<title>Wiederherstellung – Reinigungsplan</title></head>
<body style="font-family:ui-sans-serif,system-ui,sans-serif;background:#12161f;color:#e2e8f0;
             display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
<div style="text-align:center;max-width:24rem;padding:1.5rem;">
  <p>Datenbank wiederhergestellt. Die Anwendung startet neu…</p>
  <p style="opacity:.6;font-size:.85em;margin-top:.5rem;">Diese Seite lädt in wenigen Sekunden automatisch neu.</p>
</div>
</body></html>""")


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
    stock_current: float = Form(0.0),
    stock_min: float = Form(0.0),
    category: str = Form(""),
    location: str = Form(""),
    reorder_url: str = Form(""),
    group_id: str = Form(""),
    next: str = Form("/admin/inventory"),
    db: Session = Depends(get_db),
):
    require_admin_or_shift_lead(request, db)
    db.add(models.InventoryItem(
        name=name,
        unit=unit or None,
        stock_current=stock_current,
        stock_min=stock_min,
        category=category or None,
        location=location or None,
        reorder_url=reorder_url or None,
        group_id=int(group_id) if group_id else None,
    ))
    db.commit()
    return RedirectResponse(next if next.startswith("/") else "/admin/inventory", status_code=302)


@app.post("/admin/inventory/{item_id}/edit")
def admin_edit_inventory_item(
    item_id: int,
    request: Request,
    name: str = Form(...),
    unit: str = Form(""),
    stock_current: float = Form(0.0),
    stock_min: float = Form(0.0),
    category: str = Form(""),
    location: str = Form(""),
    reorder_url: str = Form(""),
    group_id: str = Form(""),
    next: str = Form("/admin/inventory"),
    db: Session = Depends(get_db),
):
    require_admin_or_shift_lead(request, db)
    item = db.query(models.InventoryItem).filter(models.InventoryItem.id == item_id).first()
    if item:
        item.name = name
        item.unit = unit or None
        item.stock_current = stock_current
        item.stock_min = stock_min
        item.category = category or None
        item.location = location or None
        item.reorder_url = reorder_url or None
        item.group_id = int(group_id) if group_id else None
        if item.stock_current >= item.stock_min:
            item.notified = False
        db.commit()
    return RedirectResponse(next if next.startswith("/") else "/admin/inventory", status_code=302)


@app.post("/admin/inventory/{item_id}/delete")
def admin_delete_inventory_item(item_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    item = db.query(models.InventoryItem).filter(models.InventoryItem.id == item_id).first()
    if item:
        db.delete(item)
        db.commit()
    return RedirectResponse("/admin/inventory", status_code=302)
