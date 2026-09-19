"""Optionaler Offsite-Upload der automatischen Sicherungen per WebDAV
(Nextcloud, grundsaetzlich auch andere WebDAV-Server).

Konfiguration wie bei den ntfy/Signal-Verbindungen (siehe notifications.py:
_channel_config): Werte aus der Verwaltung (Verwaltung -> System) haben
Vorrang, ansonsten greifen die gleichnamigen Umgebungsvariablen
(NEXTCLOUD_ENABLED/URL/USER/PASSWORD) - deckt sowohl per docker-compose
konfigurierte Installationen als auch den Fall ab, dass niemand Zugriff auf
den Docker-Stack hat.

Ein fehlgeschlagener Upload darf die lokale Sicherung nie beeintraechtigen:
upload_if_due() faengt deshalb jede Ausnahme ab und vermerkt sie nur als
Status (AppSettings.nextcloud_last_error) fuer die Anzeige in der Verwaltung
plus Log-Eintrag."""
import base64
import logging
import os
import re
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import ntptime
from .database import SessionLocal
from .models import AppSettings

logger = logging.getLogger("reinigungsplan.nextcloud")

# Bewusst knapp: ein haengender Server darf den Scheduler-Thread nicht
# minutenlang blockieren (der naechste lokale Backup-Lauf teilt sich denselben
# Job und wuerde sonst uebersprungen, solange dieser noch laeuft).
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=120.0, pool=10.0)


class NextcloudError(Exception):
    """Fehler mit bereits nutzerfreundlicher, deutscher Meldung (ohne
    Zugangsdaten) - wird unveraendert in der Verwaltung angezeigt."""


# ---------- Passwort-Verschluesselung ----------

def _fernet() -> Fernet:
    # Schluessel aus SECRET_KEY abgeleitet: das Passwort landet dadurch nicht
    # im Klartext in der Datenbank - und damit auch nicht in den Backups, die
    # ja ihrerseits per Download/Offsite-Kopie die Anwendung verlassen. Wird
    # SECRET_KEY geaendert (oder ein Backup auf einer anderen Instanz
    # eingespielt), ist das gespeicherte Passwort bewusst nicht mehr lesbar
    # und muss neu eingegeben werden (siehe decrypt_secret -> None).
    secret = os.environ.get("SECRET_KEY", "change-me-in-production").encode("utf-8")
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"clubhub-nextcloud-password-v1",
    ).derive(secret)
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str | None:
    """None, falls das Token nicht (mehr) entschluesselbar ist."""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


# ---------- Konfiguration ----------

@dataclass
class NextcloudConfig:
    enabled: bool
    url: str
    user: str
    password: str
    # "db" | "env" | "form" | "none" | "undecryptable" - nur fuer die Anzeige
    password_source: str

    @property
    def is_complete(self) -> bool:
        return bool(self.url and self.user and self.password)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on", "ja")


def resolve_config(settings: AppSettings | None, overrides: dict | None = None) -> NextcloudConfig:
    """Effektive Konfiguration: Formular-Override (nur fuer "Verbindung
    testen" mit noch nicht gespeicherten Eingaben, leere Werte zaehlen nicht)
    > Wert aus der Verwaltung > Umgebungsvariable."""
    overrides = overrides or {}

    def pick(override_key: str, db_value: str | None, env_name: str) -> str:
        return (overrides.get(override_key) or "").strip() or (db_value or "").strip() \
            or os.environ.get(env_name, "").strip()

    url = pick("url", settings.nextcloud_url if settings else None, "NEXTCLOUD_URL")
    user = pick("user", settings.nextcloud_user if settings else None, "NEXTCLOUD_USER")

    password, source = "", "none"
    if overrides.get("password"):
        password, source = overrides["password"], "form"
    else:
        token = settings.nextcloud_password_enc if settings else None
        if token:
            decrypted = decrypt_secret(token)
            if decrypted:
                password, source = decrypted, "db"
            else:
                source = "undecryptable"
        if not password:
            env_password = os.environ.get("NEXTCLOUD_PASSWORD", "")
            if env_password:
                password, source = env_password, "env"

    if settings is not None and settings.nextcloud_enabled is not None:
        enabled = bool(settings.nextcloud_enabled)
    else:
        enabled = _env_truthy("NEXTCLOUD_ENABLED")

    return NextcloudConfig(enabled=enabled, url=url, user=user, password=password, password_source=source)


DEFAULT_RETENTION_DAYS = 7


def resolve_retention_days(settings: AppSettings | None) -> int:
    """Aufbewahrung in der Nextcloud: Wert aus der Verwaltung > Umgebungs-
    variable NEXTCLOUD_RETENTION_DAYS > 7 Tage. 0 = nie automatisch loeschen."""
    if settings is not None and settings.nextcloud_retention_days is not None:
        return max(0, int(settings.nextcloud_retention_days))
    raw = os.environ.get("NEXTCLOUD_RETENTION_DAYS", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            logger.warning("NEXTCLOUD_RETENTION_DAYS=%r ist keine ganze Zahl - Standard (%d Tage) gilt.", raw, DEFAULT_RETENTION_DAYS)
    return DEFAULT_RETENTION_DAYS


def view_state(settings: AppSettings) -> dict:
    """Aufbereitete Werte fuer die Karte in der Verwaltung. Das Passwort selbst
    verlaesst diese Funktion nie - nur seine Herkunft."""
    cfg = resolve_config(settings)
    return {
        "retention_db": settings.nextcloud_retention_days if settings.nextcloud_retention_days is not None else "",
        "retention_fallback": resolve_retention_days(None),
        "enabled": cfg.enabled,
        "complete": cfg.is_complete,
        "url_db": settings.nextcloud_url or "",
        "url_env": os.environ.get("NEXTCLOUD_URL", "").strip(),
        "user_db": settings.nextcloud_user or "",
        "user_env": os.environ.get("NEXTCLOUD_USER", "").strip(),
        "password_source": cfg.password_source,
        "last_success_at": settings.nextcloud_last_success_at,
        "last_error": settings.nextcloud_last_error,
        "last_error_at": settings.nextcloud_last_error_at,
    }


def normalize_base_url(raw: str) -> tuple[str, list[str]]:
    """Prueft/normalisiert die WebDAV-Ordner-URL (immer mit abschliessendem
    Schraegstrich). Liefert (url, warnungen) oder wirft NextcloudError."""
    value = (raw or "").strip()
    if not value:
        raise NextcloudError("Keine Nextcloud-URL angegeben.")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise NextcloudError("Die URL muss mit https:// (oder http://) beginnen und einen Server enthalten.")
    if parts.username or parts.password:
        raise NextcloudError("Benutzername und Passwort bitte in die eigenen Felder eintragen, nicht in die URL.")
    if parts.query or parts.fragment:
        raise NextcloudError(
            "Die URL darf keine Parameter (?…) oder Anker (#…) enthalten – bitte den reinen WebDAV-Ordnerpfad angeben "
            "(nicht die Adresse aus dem Browser-Fenster der Nextcloud)."
        )
    warnings = []
    if parts.scheme == "http":
        warnings.append("Unverschlüsselte Verbindung (http): Benutzername und Passwort werden im Klartext übertragen.")
    if "/remote.php/dav/" not in parts.path and "/remote.php/webdav/" not in parts.path:
        warnings.append(
            "Die URL enthält nicht den bei Nextcloud üblichen WebDAV-Pfad "
            "(…/remote.php/dav/files/<Benutzer>/<Ordner>/)."
        )
    path = parts.path if parts.path.endswith("/") else parts.path + "/"
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")), warnings


# ---------- WebDAV ----------

def _client(cfg: NextcloudConfig) -> httpx.Client:
    # follow_redirects bewusst aus: eine Weiterleitung (z.B. http -> https)
    # wuerde bei PUT/PROPFIND zu unerwarteten Verhaeltnissen fuehren - lieber
    # eine klare Meldung mit der Aufforderung, die endgueltige Adresse zu nutzen.
    return httpx.Client(
        auth=(cfg.user, cfg.password), timeout=_TIMEOUT, follow_redirects=False,
        headers={"User-Agent": "ClubHUB-Backup"},
    )


def _has_ssl_cause(exc: BaseException) -> bool:
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, ssl.SSLError):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def _request(client: httpx.Client, method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        raise NextcloudError("Zeitüberschreitung – die Nextcloud antwortet nicht rechtzeitig.") from None
    except httpx.ConnectError as exc:
        if _has_ssl_cause(exc):
            raise NextcloudError(
                "Das TLS-Zertifikat der Nextcloud wird nicht akzeptiert (abgelaufen, selbst signiert "
                "oder passt nicht zum Server-Namen)."
            ) from None
        raise NextcloudError("Server nicht erreichbar – Adresse und Netzwerkverbindung prüfen.") from None
    except httpx.HTTPError as exc:
        raise NextcloudError(f"Netzwerkfehler bei der Verbindung zur Nextcloud ({type(exc).__name__}).") from None


def _raise_for_status(resp: httpx.Response, action: str) -> None:
    s = resp.status_code
    if s == 401:
        msg = "Anmeldung abgelehnt – Benutzername oder (App-)Passwort falsch."
    elif s == 403:
        msg = "Zugriff verweigert – fehlen Schreibrechte auf dem Zielordner?"
    elif s == 404:
        msg = "Adresse bzw. Ordner nicht gefunden – bitte die URL prüfen (…/remote.php/dav/files/<Benutzer>/<Ordner>/)."
    elif s == 405:
        msg = "Die Adresse scheint kein WebDAV-Endpunkt zu sein – bitte die URL prüfen."
    elif s == 409:
        msg = "Der Zielordner existiert nicht (oder ein übergeordneter Ordner fehlt)."
    elif s == 413:
        msg = "Die Datei ist größer als das Upload-Limit der Nextcloud bzw. des vorgeschalteten Servers."
    elif s == 423:
        msg = "Ziel in der Nextcloud ist gesperrt."
    elif s == 429:
        msg = "Zu viele Anfragen – die Nextcloud drosselt gerade (z. B. Brute-Force-Schutz nach fehlgeschlagenen Anmeldungen)."
    elif s == 507:
        msg = "Kein Speicherplatz mehr in der Nextcloud (Speicherkontingent erreicht)."
    elif s == 503:
        msg = "Die Nextcloud ist gerade nicht verfügbar (Wartungsmodus?)."
    elif s in (301, 302, 303, 307, 308):
        target = resp.headers.get("location", "")
        msg = (
            "Die Adresse leitet weiter"
            + (f" (nach {target})" if target else "")
            + " – bitte die endgültige Adresse (meist mit https://) eintragen."
        )
    else:
        msg = f"Unerwartete Antwort der Nextcloud (HTTP {s})."
    raise NextcloudError(f"{msg} [{action}]")


_PROPFIND_BODY = (
    '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>'
)


def _ensure_folder(client: httpx.Client, base: str) -> bool:
    """Stellt sicher, dass der Zielordner existiert. True, falls er dafuer neu
    angelegt werden musste. Legt nur den letzten Pfad-Abschnitt an (MKCOL) -
    fehlt darueber noch etwas, kommt eine verstaendliche Fehlermeldung."""
    resp = _request(
        client, "PROPFIND", base, headers={"Depth": "0", "Content-Type": "application/xml"}, content=_PROPFIND_BODY,
    )
    if resp.status_code == 207:
        return False
    if resp.status_code != 404:
        _raise_for_status(resp, "Ordner prüfen")
    mk = _request(client, "MKCOL", base)
    if mk.status_code == 201:
        return True
    if mk.status_code == 405:  # existiert (inzwischen) bereits
        return False
    _raise_for_status(mk, "Ordner anlegen")
    return True  # unerreichbar, _raise_for_status wirft immer


def test_connection(cfg: NextcloudConfig) -> dict:
    """"Verbindung testen": Ordner pruefen/anlegen, kleine Testdatei
    schreiben und wieder loeschen (weist Schreibrechte nach, nicht nur
    Lesezugriff). Liefert {"ok": True, "message": ..., "warnings": [...]}
    oder wirft NextcloudError."""
    missing = [label for label, value in (("URL", cfg.url), ("Benutzer", cfg.user), ("Passwort", cfg.password)) if not value]
    if missing:
        hint = " (das gespeicherte Passwort ist nicht mehr lesbar – bitte neu eingeben)" \
            if "Passwort" in missing and cfg.password_source == "undecryptable" else ""
        raise NextcloudError(f"Es fehlt noch: {', '.join(missing)}{hint}.")
    base, warnings = normalize_base_url(cfg.url)
    notes = []
    with _client(cfg) as client:
        if _ensure_folder(client, base):
            notes.append("Der Zielordner war noch nicht vorhanden und wurde angelegt.")
        test_url = base + f"clubhub-verbindungstest-{uuid.uuid4().hex[:8]}.tmp"
        put = _request(client, "PUT", test_url, content=b"ClubHUB Verbindungstest")
        if put.status_code not in (201, 204):
            _raise_for_status(put, "Schreibtest")
        delete = _request(client, "DELETE", test_url)
        if delete.status_code not in (200, 204):
            warnings.append("Die Testdatei konnte nicht wieder gelöscht werden (clubhub-verbindungstest-….tmp im Zielordner).")
    return {
        "ok": True,
        "message": ("Verbindung erfolgreich – Schreibzugriff auf den Zielordner funktioniert. " + " ".join(notes)).strip(),
        "warnings": warnings,
    }


def upload_backup(cfg: NextcloudConfig, path: str) -> None:
    """Laedt eine Sicherungsdatei per WebDAV-PUT in den Zielordner. Wirft
    NextcloudError bei jedem Fehler."""
    if not cfg.is_complete:
        raise NextcloudError("Nextcloud-Upload ist aktiviert, aber URL, Benutzer oder Passwort fehlen bzw. sind nicht lesbar.")
    base, _warnings = normalize_base_url(cfg.url)
    filename = os.path.basename(path)
    target = base + quote(filename)
    with open(path, "rb") as f:
        data = f.read()
    with _client(cfg) as client:
        resp = _request(client, "PUT", target, content=data)
        if resp.status_code in (404, 409):
            # Zielordner wurde seit dem Test geloescht - selbst heilen statt
            # dauerhaft zu scheitern.
            _ensure_folder(client, base)
            resp = _request(client, "PUT", target, content=data)
        if resp.status_code not in (201, 204):
            _raise_for_status(resp, "Upload")


_BACKUP_NAME_RE = re.compile(r"^auto-(\d{8})-(\d{6})\.db$")
_HREF_RE = re.compile(r"<(?:\w+:)?href>([^<]+)</(?:\w+:)?href>")


def prune_remote(cfg: NextcloudConfig, retention_days: int, keep_filename: str) -> int:
    """Loescht im Zielordner Sicherungen, die aelter als `retention_days` Tage
    sind (Zeitpunkt aus dem Dateinamen, UTC - derselbe, den auch die lokale
    Rotation nutzt). Bewusst eng gefasst, weil das die einzige Stelle ist, an
    der ClubHUB Dateien in der Nextcloud loescht: angefasst werden
    ausschliesslich Dateien, deren Name exakt dem Muster
    auto-JJJJMMTT-HHMMSS.db entspricht - alles andere im Ordner (eigene
    Dateien, Verbindungstest-Reste, manuelle Downloads) bleibt unberuehrt -,
    und die gerade hochgeladene Datei (keep_filename) nie. retention_days <= 0
    schaltet das Aufraeumen aus. Gibt die Anzahl geloeschter Dateien zurueck,
    wirft NextcloudError, wenn schon das Auflisten scheitert."""
    if retention_days <= 0:
        return 0
    base, _warnings = normalize_base_url(cfg.url)
    cutoff = ntptime.now_utc() - timedelta(days=retention_days)
    deleted = 0
    with _client(cfg) as client:
        resp = _request(
            client, "PROPFIND", base,
            headers={"Depth": "1", "Content-Type": "application/xml"}, content=_PROPFIND_BODY,
        )
        if resp.status_code != 207:
            _raise_for_status(resp, "Ordner auflisten")
        names = {unquote(h).rstrip("/").rsplit("/", 1)[-1] for h in _HREF_RE.findall(resp.text)}
        for name in sorted(names):
            match = _BACKUP_NAME_RE.match(name)
            if not match or name == keep_filename:
                continue
            try:
                stamp = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if stamp >= cutoff:
                continue
            result = _request(client, "DELETE", base + quote(name))
            if result.status_code in (200, 204):
                deleted += 1
            else:
                logger.warning("Nextcloud-Aufbewahrung: %s konnte nicht gelöscht werden (HTTP %s).", name, result.status_code)
    return deleted


# ---------- Scheduler-Anbindung ----------

def _get_or_create_settings(db) -> AppSettings:
    settings = db.query(AppSettings).first()
    if not settings:
        settings = AppSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _as_utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def upload_if_due(path: str, tz) -> None:
    """Wird vom Scheduler nach jeder erfolgreich erzeugten lokalen Sicherung
    aufgerufen. Laedt hoechstens einmal pro (lokalem) Kalendertag hoch: der
    erste erfolgreiche Versuch des Tages zaehlt, schlaegt er fehl, versucht es
    der naechste Backup-Lauf erneut. Wirft nie - Fehler landen nur im Status
    (Anzeige in der Verwaltung) und im Log."""
    db = SessionLocal()
    try:
        settings = _get_or_create_settings(db)
        cfg = resolve_config(settings)
        if not cfg.enabled:
            return
        now = ntptime.now_utc()
        last = settings.nextcloud_last_success_at
        if last and _as_utc(last).astimezone(tz).date() == now.astimezone(tz).date():
            return
        uploaded = False
        try:
            upload_backup(cfg, path)
        except NextcloudError as exc:
            logger.warning("Nextcloud-Upload fehlgeschlagen: %s", exc)
            settings.nextcloud_last_error = str(exc)
            settings.nextcloud_last_error_at = now
        except Exception:
            logger.exception("Unerwarteter Fehler beim Nextcloud-Upload")
            settings.nextcloud_last_error = "Unerwarteter Fehler beim Upload (Details im Server-Log)."
            settings.nextcloud_last_error_at = now
        else:
            logger.info("Sicherung %s in die Nextcloud hochgeladen.", os.path.basename(path))
            settings.nextcloud_last_success_at = now
            settings.nextcloud_last_error = None
            settings.nextcloud_last_error_at = None
            uploaded = True
        db.commit()
        if uploaded:
            # Erst nach dem festgeschriebenen Erfolg und in eigenem try: ein
            # fehlgeschlagenes Aufraeumen macht den Upload nicht nachtraeglich
            # zum Fehler (die Sicherung liegt ja sicher in der Nextcloud).
            try:
                removed = prune_remote(cfg, resolve_retention_days(settings), os.path.basename(path))
                if removed:
                    logger.info("Nextcloud-Aufbewahrung: %d ältere Sicherung(en) gelöscht.", removed)
            except NextcloudError as exc:
                logger.warning("Nextcloud-Aufbewahrung: Aufräumen fehlgeschlagen: %s", exc)
            except Exception:
                logger.exception("Unerwarteter Fehler beim Aufräumen alter Nextcloud-Sicherungen")
    except Exception:
        logger.exception("Fehler bei der Nextcloud-Statusverwaltung")
        db.rollback()
    finally:
        db.close()
