"""Fehlerprotokoll (siehe ErrorLog in models.py): reine ASGI-Middleware, die
Probleme auf dem Weg zwischen Browser und App festhaelt, sowie der
Schreib-Helfer, den auch die /client-log-Route (Meldungen aus dem Browser)
nutzt.

Hintergrund: Ein Kollege bekam beim Erstellen einer Meldung mit Foto nur die
Browser-Fehlerseite "Website nicht erreichbar" - im Server-Log war davon
nichts zu sehen. Ein Server-Log allein erfasst genau solche Faelle nicht,
wenn die Anfrage den Server nie erreicht; deshalb zusaetzlich client_log.js.

Die Middleware wird bewusst als reine ASGI-Middleware (nicht
BaseHTTPMiddleware) geschrieben: Sie muss ClientDisconnect mitten im
Upload sehen und den Request-Body durchreichen, ohne ihn zu puffern."""
import logging
import time
import traceback
from datetime import timedelta

from starlette.requests import ClientDisconnect

from . import ntptime

logger = logging.getLogger("reinigungsplan.error_log")

SLOW_SECONDS = 10
KEEP_DAYS = 30
MAX_ROWS = 5000
# Schutz vor Log-Fluten (z.B. eine Endlosschleife von Fehlern): hoechstens
# so viele Eintraege pro Minute insgesamt.
MAX_PER_MINUTE = 60
_SKIP_PREFIXES = ("/static", "/healthz", "/sw.js", "/client-log")

_recent = []


def _cut(value, limit):
    if value is None:
        return None
    value = str(value)
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _allowed_now() -> bool:
    now = time.monotonic()
    while _recent and now - _recent[0] > 60:
        _recent.pop(0)
    if len(_recent) >= MAX_PER_MINUTE:
        return False
    _recent.append(now)
    return True


def record_error(source, kind, *, user_id=None, method=None, path=None, status=None,
                 duration_ms=None, size_bytes=None, user_agent=None, details=None,
                 timestamp=None) -> None:
    """Schreibt einen Eintrag in einer eigenen DB-Session; wirft nie - ein
    Fehler beim Protokollieren darf die eigentliche Anfrage nie beeintraechtigen."""
    if not _allowed_now():
        return
    # Lokale Importe: nur um Zirkelimporte beim App-Start zu vermeiden.
    from .database import SessionLocal
    from . import models
    db = SessionLocal()
    try:
        user_name = None
        if user_id:
            user = db.query(models.User).filter(models.User.id == user_id).first()
            user_name = user.name if user else None
        entry = models.ErrorLog(
            source=source, kind=kind, user_id=user_id if user_name else None, user_name=user_name,
            method=_cut(method, 10), path=_cut((path or "").split("?")[0], 300) or None,
            status=status, duration_ms=duration_ms, size_bytes=size_bytes,
            user_agent=_cut(user_agent, 300), details=_cut(details, 2000),
        )
        if timestamp is not None:
            entry.timestamp = timestamp
        db.add(entry)
        db.commit()
    except Exception:
        logger.exception("Fehlerprotokoll: Eintrag konnte nicht gespeichert werden")
        db.rollback()
    finally:
        db.close()


def prune_error_log() -> None:
    """Loescht Eintraege aelter als KEEP_DAYS und kappt auf MAX_ROWS."""
    from .database import SessionLocal
    from . import models
    db = SessionLocal()
    try:
        cutoff = ntptime.now_utc() - timedelta(days=KEEP_DAYS)
        db.query(models.ErrorLog).filter(models.ErrorLog.timestamp < cutoff).delete()
        count = db.query(models.ErrorLog).count()
        if count > MAX_ROWS:
            border = (
                db.query(models.ErrorLog.id).order_by(models.ErrorLog.id.desc())
                .offset(MAX_ROWS).limit(1).scalar()
            )
            if border:
                db.query(models.ErrorLog).filter(models.ErrorLog.id <= border).delete()
        db.commit()
    except Exception:
        logger.exception("Fehlerprotokoll: Aufraeumen fehlgeschlagen")
        db.rollback()
    finally:
        db.close()


class ErrorLogMiddleware:
    """Muss INNERHALB der SessionMiddleware sitzen (also vor ihr per
    add_middleware registriert werden), damit scope["session"] bereits da
    ist und der Eintrag dem Nutzer zugeordnet werden kann."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or path.startswith(_SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        started = time.monotonic()
        state = {"received": 0, "disconnected": False, "status": None}

        async def recv():
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
            elif message["type"] == "http.disconnect":
                state["disconnected"] = True
            return message

        async def snd(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
            await send(message)

        error = None
        try:
            await self.app(scope, recv, snd)
        except BaseException as exc:  # auch CancelledError: nur auswerten, immer weiterwerfen
            error = exc
            raise
        finally:
            try:
                self._finish(scope, state, error, started)
            except Exception:
                logger.exception("Fehlerprotokoll: Auswertung fehlgeschlagen")

    def _finish(self, scope, state, error, started):
        duration = time.monotonic() - started
        status = state["status"]
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        try:
            expected = int(headers.get("content-length", ""))
        except ValueError:
            expected = None
        received = state["received"]
        session = scope.get("session") or {}

        kind = None
        details = None
        if isinstance(error, ClientDisconnect) or (state["disconnected"] and status is None and error is None):
            kind = "abgebrochen"
            details = f"Verbindung nach {duration:.1f} s getrennt, bevor die Antwort gesendet war"
            if expected:
                details += f"; {received / 1e6:.1f} von {expected / 1e6:.1f} MB empfangen"
        elif error is not None and isinstance(error, Exception):
            kind = "serverfehler"
            text = "".join(traceback.format_exception_only(type(error), error)).strip()
            frames = traceback.extract_tb(error.__traceback__)[-3:]
            where = " <- ".join(
                f"{f.filename.replace(chr(92), '/').split('/')[-1]}:{f.lineno} {f.name}" for f in reversed(frames)
            )
            details = f"{text} ({where})" if where else text
        elif error is None and status is not None and status >= 500:
            kind = "serverfehler"
            details = f"Antwort mit Status {status}"
        elif error is None and duration >= SLOW_SECONDS:
            kind = "langsam"
            details = f"Anfrage dauerte {duration:.1f} s"
            if expected:
                details += f" ({expected / 1e6:.1f} MB gesendet)"

        if not kind:
            return
        record_error(
            "server", kind,
            user_id=session.get("user_id"),
            method=scope.get("method"), path=scope.get("path"), status=status,
            duration_ms=int(duration * 1000), size_bytes=expected if expected is not None else (received or None),
            user_agent=headers.get("user-agent"), details=details,
        )
