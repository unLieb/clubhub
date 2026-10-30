"""Event-Tage (Konzerte, Partys ...): steuern, an welchen Tagen "Nach Bedarf"-
Aufgaben in dafuer markierten Bereichen (Room.event_day_only, z.B. Backstage)
ueberhaupt erscheinen.

Quelle ist die Programmseite der eigenen Webseite (AppSettings.event_import_url,
z.B. eine WordPress-Seite mit Veranstaltungsliste) - eine API gibt es dafuer
nicht, deshalb wird das HTML ausgewertet. Das ist bewusst tolerant gebaut: ein
nicht lesbarer Eintrag wird uebersprungen, findet der Abgleich dagegen GAR
keine Veranstaltung mehr (Seite umgebaut / nicht erreichbar), bricht er mit
Fehlermeldung ab und aendert nichts, statt alle Termine zu loeschen.
Zusaetzlich lassen sich Tage in der Verwaltung von Hand eintragen oder
einzelne importierte Tage ausblenden (EventDay.hidden)."""
import html
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from . import ntptime
from .models import EventDay, AppSettings

logger = logging.getLogger("reinigungsplan.event_days")

DEFAULT_CATEGORIES = "Konzert, Party, Ballroom Party, Show"

# Ein Veranstaltungstag endet nicht um Mitternacht: nach dem Konzert wird
# erst in der Nacht aufgeraeumt, die Aufgaben sollen bis zum Morgen sichtbar
# bleiben.
ROLLOVER_HOUR = 6

_WEEKDAYS = {"montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3, "freitag": 4, "samstag": 5, "sonntag": 6}
_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}
_WEEKDAY_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

_ARTICLE_RE = re.compile(r'<article\b([^>]*\bevents\b[^>]*\bdata-position="\d+"[^>]*)>(.*?)</article>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub("", fragment))).strip()


def _resolve_year(weekday: int, month: int, day: int, today: date) -> date | None:
    """Die Programmseite nennt Wochentag, Tag und Monat, aber kein Jahr. Das
    Jahr ergibt sich aus dem Wochentag: das naechste Datum ab (heute - 60 Tage)
    mit passendem Wochentag. Liefert None, wenn keines in Frage kommt."""
    for year in range(today.year - 1, today.year + 3):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate.weekday() == weekday and candidate >= today - timedelta(days=60):
            return candidate
    return None


def parse_program(page_html: str, today: date) -> list[dict]:
    """Liest alle Veranstaltungen der Programmseite: [{key, date, title,
    category}]. Abgesagte Veranstaltungen (im Titel als "-Abgesagt-"
    gekennzeichnet) werden ausgelassen."""
    events = []
    for attrs, body in _ARTICLE_RE.findall(page_html):
        post = re.search(r'\bid="post-(\d+)"', attrs)
        dayname = re.search(r'class="event-dayname">([^<]+)<', body)
        day = re.search(r'class="event-day">\s*(\d+)\s*<', body)
        month = re.search(r'class="event-month">([^<]+)<', body)
        title = re.search(r'<h2 class="event-title">(.*?)</h2>', body, re.S)
        category = re.search(r'<li class="event-typ">\s*<div class="value">(.*?)</div>', body, re.S)
        if not (post and dayname and day and month and title):
            continue
        weekday = _WEEKDAYS.get(_text(dayname.group(1)).lower())
        month_no = _MONTHS.get(_text(month.group(1)).lower())
        if weekday is None or month_no is None:
            continue
        when = _resolve_year(weekday, month_no, int(day.group(1)), today)
        title_text = _text(title.group(1))
        if when is None or not title_text or "abgesagt" in title_text.lower():
            continue
        events.append({
            "key": post.group(1),
            "date": when,
            "title": title_text,
            "category": _text(category.group(1)) if category else None,
        })
    return events


def category_set(raw: str | None) -> set[str]:
    return {c.strip().lower() for c in (raw or "").split(",") if c.strip()}


def fetch_program(url: str) -> str:
    response = httpx.get(
        url, timeout=20, follow_redirects=True,
        headers={"User-Agent": "ClubHUB-Eventabgleich"},
    )
    response.raise_for_status()
    return response.text


def import_events(db: Session, app_timezone: ZoneInfo) -> str:
    """Gleicht die Programmseite mit den importierten Event-Tagen ab und gibt
    eine Kurzfassung ("3 neu, 1 geaendert, 0 entfernt") zurueck. Wirft bei
    Fehlern (Seite nicht erreichbar, keine Veranstaltung gefunden)."""
    settings = db.query(AppSettings).first()
    if not settings or not settings.event_import_url:
        raise ValueError("Keine Adresse der Programmseite hinterlegt.")
    today = ntptime.now_utc().astimezone(app_timezone).date()
    parsed = parse_program(fetch_program(settings.event_import_url), today)
    if not parsed:
        raise ValueError("Auf der Seite wurde keine Veranstaltung erkannt (Seitenaufbau geändert?).")

    wanted_categories = category_set(settings.event_import_categories or DEFAULT_CATEGORIES)
    wanted = {
        e["key"]: e for e in parsed
        if (e["category"] or "").lower() in wanted_categories
    }

    existing = {
        d.source_key: d for d in db.query(EventDay).filter(EventDay.source == "import").all()
    }
    added = changed = removed = 0
    for key, e in wanted.items():
        row = existing.get(key)
        if row is None:
            db.add(EventDay(date=e["date"], title=e["title"], category=e["category"], source="import", source_key=key))
            added += 1
        elif (row.date, row.title, row.category) != (e["date"], e["title"], e["category"]):
            row.date, row.title, row.category = e["date"], e["title"], e["category"]
            changed += 1
    # Nur kuenftige importierte Tage entfernen, die auf der Seite nicht mehr
    # (oder abgesagt / in anderer Kategorie) vorkommen - vergangene bleiben als
    # Verlauf stehen.
    for key, row in existing.items():
        if key not in wanted and row.date >= today:
            db.delete(row)
            removed += 1
    return f"{added} neu, {changed} geändert, {removed} entfernt ({len(wanted)} Veranstaltungen im Programm)"


def run_import(db: Session, app_timezone: ZoneInfo) -> bool:
    """import_events() samt Statusvermerk in den Einstellungen fuer die
    Anzeige in der Verwaltung; faengt Fehler ab (True = Erfolg)."""
    settings = db.query(AppSettings).first()
    now = ntptime.now_utc()
    try:
        summary = import_events(db, app_timezone)
    except Exception as exc:
        db.rollback()
        logger.warning("Event-Abgleich fehlgeschlagen: %s", exc)
        if settings:
            settings.event_import_last_at = now
            settings.event_import_last_error = str(exc)[:300] or exc.__class__.__name__
            db.commit()
        return False
    settings.event_import_last_at = now
    settings.event_import_last_summary = summary
    settings.event_import_last_error = None
    db.commit()
    return True


def effective_day(now_utc: datetime, app_timezone: ZoneInfo) -> date:
    """Der fuer Event-Tage massgebliche Kalendertag (siehe ROLLOVER_HOUR)."""
    return (now_utc.astimezone(app_timezone) - timedelta(hours=ROLLOVER_HOUR)).date()


def event_status(db: Session, now_utc: datetime, app_timezone: ZoneInfo) -> dict:
    """{"today": [EventDay], "next": EventDay | None, "next_label": str | None}
    - Veranstaltungen des massgeblichen Tages und die naechste danach."""
    day = effective_day(now_utc, app_timezone)
    visible = db.query(EventDay).filter(EventDay.hidden.is_(False))
    todays = visible.filter(EventDay.date == day).order_by(EventDay.title).all()
    upcoming = visible.filter(EventDay.date > day).order_by(EventDay.date, EventDay.title).first()
    return {"today": todays, "next": upcoming, "next_label": format_event(upcoming) if upcoming else None}


def event_context(db: Session, now_utc: datetime, app_timezone: ZoneInfo) -> dict:
    """event_status() plus "active" (heute ist ein Event-Tag) und "day_start"
    (UTC-Beginn des Event-Tages, siehe ROLLOVER_HOUR) - was task_status() und
    die Event-Benachrichtigung brauchen."""
    status = event_status(db, now_utc, app_timezone)
    day = effective_day(now_utc, app_timezone)
    status["active"] = bool(status["today"])
    status["day_start"] = datetime.combine(day, time(ROLLOVER_HOUR), tzinfo=app_timezone).astimezone(timezone.utc)
    return status


def format_event(event: EventDay) -> str:
    return f"{_WEEKDAY_SHORT[event.date.weekday()]}, {event.date.strftime('%d.%m.%Y')} – {event.title}"
