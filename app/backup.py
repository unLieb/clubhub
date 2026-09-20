import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Iterator

from .database import DB_PATH, engine
from . import ntptime

logger = logging.getLogger("reinigungsplan.backup")

# Präfix automatischer, geplanter Sicherungen (siehe scheduler.py) - eigenes
# Präfix, damit die Rotation nur diese und nicht die Vor-Wiederherstellung-
# Sicherheitskopien anfasst.
AUTO_BACKUP_PREFIX = "auto-"

# Hochgeladene Bilder (Meldungs-, Inventar-, Aufbau-, Feedback-Fotos, Avatare)
# liegen im Datenverzeichnis unter uploads/<Bereich>/<Datei> - nicht in der
# SQLite-Datei und damit in keiner reinen DB-Sicherung. Deshalb gibt es (a) das
# ZIP-Backup (Datenbank + Bilder), (b) ein taegliches Bild-Archiv neben den
# automatischen DB-Sicherungen und (c) den Einzeldatei-Abgleich in die
# Nextcloud (siehe nextcloud.sync_images).
UPLOADS_DIR = os.path.join(os.path.dirname(DB_PATH), "uploads")
# Feste Namen im ZIP: die Datenbank liegt direkt im Wurzelverzeichnis, die
# Bilder unter uploads/<Bereich>/<Datei> - dieselbe Struktur wie im
# Datenverzeichnis und wie in der Nextcloud, damit sich auch ein per Hand
# heruntergeladener Nextcloud-Ordner als ZIP wieder einspielen laesst.
ZIP_DB_NAME = "putzplan.db"
ZIP_UPLOADS_DIR = "uploads"
IMAGE_ARCHIVE_PREFIX = "bilder-"
# Obergrenze fuer das entpackte Volumen beim Einspielen (Schutz vor
# versehentlich riesigen bzw. manipulierten ZIP-Dateien).
MAX_RESTORE_UNCOMPRESSED_BYTES = 8 * 1024 ** 3

# Kern-Tabellen, die in jeder halbwegs aktuellen ClubHUB-Datenbank
# existieren müssen. Bewusst knapp gehalten (nicht z.B. inventory_items oder
# notification_channels), damit auch ältere Backups aus Vorgänger-Versionen
# akzeptiert werden - fehlende neuere Tabellen legt Base.metadata.create_all()
# beim Neustart einfach leer an, genau wie bei einem frischen Setup.
REQUIRED_TABLES = {"users", "groups", "rooms", "tasks", "completions"}


def _snapshot_database(dest_path: str) -> None:
    """Konsistente Kopie der SQLite-Datenbank über die Online-Backup-API -
    funktioniert auch, während die App parallel schreibt, und liefert nie
    einen halb geschriebenen Datenstand."""
    source = sqlite3.connect(DB_PATH)
    dest = sqlite3.connect(dest_path)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()


def create_backup_bytes() -> bytes:
    """Nur die Datenbank (ohne Bilder), als reine .db-Datei."""
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _snapshot_database(tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        os.remove(tmp_path)


def backup_filename(ext: str = "db") -> str:
    return f"clubhub-backup-{ntptime.now_utc().strftime('%Y%m%d-%H%M%S')}.{ext}"


# ---------- Bilder (uploads/) ----------

def _safe_component(name: str) -> bool:
    """Ein einzelner Datei-/Ordnername ohne Pfadanteile - schuetzt beim
    Einspielen vor Pfad-Traversal (z.B. "..") und uebergeht versteckte
    Dateien. Bewusst kein enges Zeichen-Muster: Dateinamen enthalten die vom
    Nutzer mitgelieferte Endung (z.B. ".JPG"), die nicht verloren gehen soll."""
    return bool(name) and name not in (".", "..") and not name.startswith(".") \
        and not any(c in name for c in "/\\\x00")


def iter_upload_files(uploads_dir: str | None = None) -> Iterator[tuple[str, str, str]]:
    """Alle Bilddateien als (Bereich, Dateiname, absoluter Pfad), sortiert.
    Nur Dateien direkt in einem Unterordner von uploads/ (so legt die App sie
    ab); Symlinks werden nicht verfolgt."""
    root = uploads_dir or UPLOADS_DIR
    if not os.path.isdir(root):
        return
    for sub in sorted(os.listdir(root)):
        sub_path = os.path.join(root, sub)
        if not _safe_component(sub) or os.path.islink(sub_path) or not os.path.isdir(sub_path):
            continue
        for name in sorted(os.listdir(sub_path)):
            path = os.path.join(sub_path, name)
            if _safe_component(name) and os.path.isfile(path) and not os.path.islink(path):
                yield sub, name, path


def uploads_stats(uploads_dir: str | None = None) -> dict:
    """Anzahl und Gesamtgroesse der Bilder - fuer die Anzeige in der Verwaltung."""
    count = size = 0
    for _sub, _name, path in iter_upload_files(uploads_dir):
        try:
            size += os.path.getsize(path)
            count += 1
        except OSError:
            continue
    return {"count": count, "size_bytes": size}


def _write_uploads_to_zip(zf: zipfile.ZipFile, uploads_dir: str | None = None) -> int:
    """Schreibt alle Bilder unter uploads/<Bereich>/<Datei> ins ZIP. Bilder
    sind bereits komprimiert - ZIP_STORED spart nur CPU-Zeit, kostet aber
    keinen Platz. Eine zwischenzeitlich geloeschte Datei wird uebersprungen."""
    written = 0
    for sub, name, path in iter_upload_files(uploads_dir):
        try:
            zf.write(path, f"{ZIP_UPLOADS_DIR}/{sub}/{name}", compress_type=zipfile.ZIP_STORED)
            written += 1
        except FileNotFoundError:
            continue
    return written


def create_full_backup_zip() -> str:
    """Vollstaendiges Backup als ZIP (Datenbank + alle Bilder) in einer
    temporaeren Datei - der Aufrufer loescht sie nach dem Ausliefern. Bewusst
    eine Datei statt Bytes im Speicher: die Bilder koennen mit der Zeit deutlich
    groesser werden als die Datenbank."""
    fd, zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    db_fd, db_tmp = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    try:
        _snapshot_database(db_tmp)
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(db_tmp, ZIP_DB_NAME, compress_type=zipfile.ZIP_DEFLATED)
            _write_uploads_to_zip(zf)
        return zip_path
    except BaseException:
        if os.path.exists(zip_path):
            os.remove(zip_path)
        raise
    finally:
        os.remove(db_tmp)


def _backup_dir() -> str:
    return os.path.join(os.path.dirname(DB_PATH), "backups")


def _parse_backup_timestamp(name: str, prefix: str, suffix: str) -> datetime | None:
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    try:
        return datetime.strptime(name[len(prefix):-len(suffix)], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def create_scheduled_images_archive(retention_days: int, tz) -> str | None:
    """Taegliches Bild-Archiv (bilder-JJJJMMTT-HHMMSS.zip) neben den
    automatischen DB-Sicherungen in backups/ - hoechstens eines pro (lokalem)
    Kalendertag, nur wenn ueberhaupt Bilder vorhanden sind. Bilder aendern sich
    selten, deshalb nicht bei jedem der mehrmals taeglichen Sicherungslaeufe.
    Aufbewahrung wie bei den DB-Sicherungen (retention_days). Gibt den Pfad
    eines neu angelegten Archivs zurueck, sonst None."""
    backup_dir = _backup_dir()
    os.makedirs(backup_dir, exist_ok=True)
    now = ntptime.now_utc()
    today = now.astimezone(tz).date()

    created = None
    already_today = any(
        e["timestamp"].astimezone(tz).date() == today for e in list_image_archives()
    )
    if not already_today and next(iter_upload_files(), None) is not None:
        final = os.path.join(backup_dir, f"{IMAGE_ARCHIVE_PREFIX}{now.strftime('%Y%m%d-%H%M%S')}.zip")
        partial = final + ".tmp"  # endet nicht auf .zip -> nie als fertiges Archiv gelistet
        try:
            with zipfile.ZipFile(partial, "w") as zf:
                _write_uploads_to_zip(zf)
            os.replace(partial, final)
            created = final
        finally:
            if os.path.exists(partial):
                os.remove(partial)

    cutoff = now - timedelta(days=retention_days)
    for name in os.listdir(backup_dir):
        if name.startswith(IMAGE_ARCHIVE_PREFIX) and name.endswith(".zip.tmp"):
            # Rest eines abgebrochenen Laufs (z.B. Neustart mitten im Schreiben).
            try:
                if os.path.getmtime(os.path.join(backup_dir, name)) < (now - timedelta(hours=1)).timestamp():
                    os.remove(os.path.join(backup_dir, name))
            except OSError:
                pass
            continue
        ts = _parse_backup_timestamp(name, IMAGE_ARCHIVE_PREFIX, ".zip")
        if ts is not None and ts < cutoff:
            os.remove(os.path.join(backup_dir, name))
    return created


def list_image_archives() -> list[dict]:
    """Fuer die Anzeige in der Verwaltung: alle Bild-Archive, neueste zuerst."""
    backup_dir = _backup_dir()
    if not os.path.isdir(backup_dir):
        return []
    entries = []
    for name in os.listdir(backup_dir):
        ts = _parse_backup_timestamp(name, IMAGE_ARCHIVE_PREFIX, ".zip")
        if ts is None:
            continue
        entries.append({
            "filename": name,
            "timestamp": ts,
            "size_bytes": os.path.getsize(os.path.join(backup_dir, name)),
        })
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return entries


def image_archive_path(filename: str) -> str | None:
    """Wie scheduled_backup_path, fuer Bild-Archive."""
    if _parse_backup_timestamp(filename, IMAGE_ARCHIVE_PREFIX, ".zip") is None:
        return None
    path = os.path.join(_backup_dir(), filename)
    return path if os.path.isfile(path) else None


def create_scheduled_backup(retention_days: int) -> str:
    """Schreibt eine automatische Sicherung ins Datenverzeichnis (per
    Scheduler mehrmals täglich aufgerufen, siehe scheduler.py) und löscht
    anschließend automatische Sicherungen, die älter als `retention_days`
    Tage sind. Rührt Vor-Wiederherstellung-Sicherheitskopien nicht an.
    Gibt den Pfad der neu erzeugten Sicherung zurück (Grundlage für den
    optionalen Offsite-Upload, siehe nextcloud.py)."""
    backup_dir = os.path.join(os.path.dirname(DB_PATH), "backups")
    os.makedirs(backup_dir, exist_ok=True)

    now = ntptime.now_utc()
    filename = f"{AUTO_BACKUP_PREFIX}{now.strftime('%Y%m%d-%H%M%S')}.db"
    backup_path = os.path.join(backup_dir, filename)
    with open(backup_path, "wb") as f:
        f.write(create_backup_bytes())

    cutoff = now - timedelta(days=retention_days)
    for name in os.listdir(backup_dir):
        if not name.startswith(AUTO_BACKUP_PREFIX) or not name.endswith(".db"):
            continue
        try:
            ts = datetime.strptime(name[len(AUTO_BACKUP_PREFIX):-3], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts < cutoff:
            os.remove(os.path.join(backup_dir, name))
    return backup_path


def list_scheduled_backups() -> list[dict]:
    """Für die Anzeige in der Verwaltung: alle automatischen Sicherungen,
    neueste zuerst, mit Zeitpunkt (UTC) und Dateigröße."""
    backup_dir = os.path.join(os.path.dirname(DB_PATH), "backups")
    if not os.path.isdir(backup_dir):
        return []
    entries = []
    for name in os.listdir(backup_dir):
        if not name.startswith(AUTO_BACKUP_PREFIX) or not name.endswith(".db"):
            continue
        try:
            ts = datetime.strptime(name[len(AUTO_BACKUP_PREFIX):-3], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        entries.append({
            "filename": name,
            "timestamp": ts,
            "size_bytes": os.path.getsize(os.path.join(backup_dir, name)),
        })
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return entries


def scheduled_backup_path(filename: str) -> str | None:
    """Löst einen Dateinamen sicher auf einen Pfad innerhalb von backups/ auf -
    None, falls der Name nicht zu einer vorhandenen automatischen Sicherung
    passt (verhindert Pfad-Traversal über die Route)."""
    if not filename.startswith(AUTO_BACKUP_PREFIX) or not filename.endswith(".db"):
        return None
    if "/" in filename or "\\" in filename:
        return None
    backup_dir = os.path.join(os.path.dirname(DB_PATH), "backups")
    path = os.path.join(backup_dir, filename)
    if not os.path.isfile(path):
        return None
    return path


def _validate_backup_file(path: str) -> str | None:
    """Prüft, ob die Datei eine plausible ClubHUB-Datenbank ist.
    None bei Erfolg, sonst eine für Nutzer verständliche Fehlermeldung."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"Keine gültige SQLite-Datenbank: {exc}"
    missing = REQUIRED_TABLES - tables
    if missing:
        return f"Das sieht nicht nach einer ClubHUB-Datenbank aus (fehlende Tabellen: {', '.join(sorted(missing))})."
    return None


def _swap_in_database(src_path: str) -> str | None:
    """Prüft die Datei unter src_path und setzt sie, falls gültig, als neue
    laufende Datenbank ein (vorher Sicherheitskopie der aktuellen Datenbank).
    Gemeinsamer Kern für restore_from_bytes (Upload) und restore_from_path
    (bereits vorhandene automatische Sicherung)."""
    error = _validate_backup_file(src_path)
    if error:
        return error

    backup_dir = os.path.join(os.path.dirname(DB_PATH), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    safety_copy = os.path.join(
        backup_dir, f"vor-wiederherstellung-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    )
    # Alle gepoolten Verbindungen schließen, bevor die Datei unter der Engine
    # weggetauscht wird - jede künftige Anfrage öffnet ohnehin eine frische
    # Verbindung über SessionLocal(), zusätzlich startet der Prozess danach neu.
    engine.dispose()
    shutil.copy2(DB_PATH, safety_copy)
    shutil.copy2(src_path, DB_PATH)
    return None


@dataclass
class RestoreResult:
    error: str | None = None
    db_restored: bool = False
    images_restored: int = 0
    images_already_present: int = 0


def _extract_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: str) -> None:
    """Entpackt einen ZIP-Eintrag atomar (erst .part, dann umbenennen), damit
    nie eine halb geschriebene Datei unter dem endgueltigen Namen liegt."""
    partial = dest + ".part"
    try:
        with zf.open(info) as src, open(partial, "wb") as out:
            shutil.copyfileobj(src, out)
        os.replace(partial, dest)
    finally:
        if os.path.exists(partial):
            os.remove(partial)


def _restore_from_zip(path: str) -> RestoreResult:
    """Spielt ein ZIP ein: die Datenbank (Eintrag putzplan.db im Wurzel-
    verzeichnis, falls vorhanden) und alle Bilder unter .../uploads/<Bereich>/
    <Datei> (auch hinter einem beliebigen Ordner-Praefix, wie ihn ein in der
    Nextcloud als ZIP heruntergeladener Ordner hat). Bilder werden nur
    ergaenzt, nie geloescht: Dateien, die schon in gleicher Groesse vorhanden
    sind, bleiben unangetastet. Reihenfolge: erst die Datenbank pruefen, dann
    Bilder entpacken, zuletzt die Datenbank austauschen - ein ungueltiges
    Backup aendert so nichts."""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        return RestoreResult(error="Die ZIP-Datei ist beschädigt oder unvollständig.")
    tmp_dir = tempfile.mkdtemp(prefix="clubhub-restore-")
    try:
        with zf:
            db_info = None
            images = []
            total = 0
            for info in zf.infolist():
                if info.is_dir():
                    continue
                parts = PurePosixPath(info.filename.replace("\\", "/")).parts
                if parts == (ZIP_DB_NAME,):
                    db_info = info
                elif len(parts) >= 3 and parts[-3] == ZIP_UPLOADS_DIR \
                        and _safe_component(parts[-2]) and _safe_component(parts[-1]):
                    images.append((info, parts[-2], parts[-1]))
                else:
                    continue
                total += info.file_size
            if db_info is None and not images:
                return RestoreResult(error=(
                    f"Das ZIP enthält weder eine ClubHUB-Datenbank ({ZIP_DB_NAME}) noch Bilder "
                    f"({ZIP_UPLOADS_DIR}/<Bereich>/<Datei>)."
                ))
            if total > MAX_RESTORE_UNCOMPRESSED_BYTES:
                return RestoreResult(error="Das ZIP ist zu groß zum Einspielen (mehr als 8 GB entpackt).")

            db_tmp = None
            if db_info is not None:
                db_tmp = os.path.join(tmp_dir, "restore.db")
                _extract_member(zf, db_info, db_tmp)
                error = _validate_backup_file(db_tmp)
                if error:
                    return RestoreResult(error=error)

            restored = present = 0
            for info, sub, name in images:
                dest_dir = os.path.join(UPLOADS_DIR, sub)
                dest = os.path.join(dest_dir, name)
                if os.path.isfile(dest) and os.path.getsize(dest) == info.file_size:
                    present += 1
                    continue
                os.makedirs(dest_dir, exist_ok=True)
                _extract_member(zf, info, dest)
                restored += 1

            if db_tmp is not None:
                error = _swap_in_database(db_tmp)
                if error:
                    return RestoreResult(error=error, images_restored=restored, images_already_present=present)
            return RestoreResult(
                db_restored=db_tmp is not None, images_restored=restored, images_already_present=present,
            )
    except (zipfile.BadZipFile, OSError, EOFError) as exc:
        logger.exception("Fehler beim Einspielen des ZIP-Backups")
        return RestoreResult(error=f"Das ZIP konnte nicht eingespielt werden ({type(exc).__name__}): "
                                   "Datei beschädigt oder nicht genug Speicherplatz.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def restore_from_upload(path: str) -> RestoreResult:
    """Spielt eine hochgeladene Datei ein: ein ZIP (Datenbank und/oder Bilder,
    siehe _restore_from_zip) oder - wie bisher - eine reine .db-Datei. Die
    Datenbank wird dabei durch die neue ersetzt (vorher automatische
    Sicherheitskopie); Bilder werden nur ergaenzt."""
    if zipfile.is_zipfile(path):
        return _restore_from_zip(path)
    error = _swap_in_database(path)
    return RestoreResult(error=error, db_restored=error is None)


def restore_from_path(path: str) -> str | None:
    """Wie restore_from_bytes, aber für eine bereits im Datenverzeichnis
    vorhandene Datei (z.B. eine automatische Sicherung) - kein Upload nötig."""
    return _swap_in_database(path)
