import os
import shutil
import sqlite3
import tempfile
from datetime import datetime

from .database import DB_PATH, engine
from . import ntptime

# Kern-Tabellen, die in jeder halbwegs aktuellen Reinigungsplan-Datenbank
# existieren müssen. Bewusst knapp gehalten (nicht z.B. inventory_items oder
# notification_channels), damit auch ältere Backups aus Vorgänger-Versionen
# akzeptiert werden - fehlende neuere Tabellen legt Base.metadata.create_all()
# beim Neustart einfach leer an, genau wie bei einem frischen Setup.
REQUIRED_TABLES = {"users", "groups", "rooms", "tasks", "completions"}


def create_backup_bytes() -> bytes:
    """Erzeugt eine konsistente Kopie der SQLite-Datenbank über die
    Online-Backup-API - funktioniert auch, während die App parallel
    schreibt, und liefert nie einen halb geschriebenen Datenstand."""
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        source = sqlite3.connect(DB_PATH)
        dest = sqlite3.connect(tmp_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        os.remove(tmp_path)


def backup_filename() -> str:
    return f"reinigungsplan-backup-{ntptime.now_utc().strftime('%Y%m%d-%H%M%S')}.db"


def _validate_backup_file(path: str) -> str | None:
    """Prüft, ob die Datei eine plausible Reinigungsplan-Datenbank ist.
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
        return f"Das sieht nicht nach einer Reinigungsplan-Datenbank aus (fehlende Tabellen: {', '.join(sorted(missing))})."
    return None


def restore_from_bytes(data: bytes) -> str | None:
    """Ersetzt die laufende Datenbank durch die hochgeladene Datei. Legt vorher
    automatisch eine Sicherheitskopie der aktuellen Datenbank an. Gibt bei
    einem Validierungsfehler eine Fehlermeldung zurück (Datenbank bleibt dann
    unverändert), sonst None."""
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)

        error = _validate_backup_file(tmp_path)
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
        shutil.move(tmp_path, DB_PATH)
        return None
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
