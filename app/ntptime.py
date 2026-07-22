import os
import logging
import threading
from datetime import datetime, timezone, timedelta

import ntplib

logger = logging.getLogger("reinigungsplan.ntptime")

NTP_SERVER = os.environ.get("NTP_SERVER", "pool.ntp.org")

_lock = threading.Lock()
_offset = timedelta(0)
_last_sync = None
_last_error = None


def sync(timeout: float = 3.0) -> bool:
    """Fragt den konfigurierten NTP-Server nach der echten Zeit und merkt sich
    die Differenz zur Systemuhr des Containers als Offset. Schlägt lautlos fehl
    (z.B. kein Internet/Firewall) - dann bleibt der zuletzt bekannte bzw. beim
    allerersten Versuch der Null-Offset aktiv, die App läuft dann einfach mit
    der Systemuhr weiter wie zuvor."""
    global _offset, _last_sync, _last_error
    client = ntplib.NTPClient()
    try:
        response = client.request(NTP_SERVER, version=3, timeout=timeout)
        with _lock:
            _offset = timedelta(seconds=response.offset)
            _last_sync = datetime.now(timezone.utc)
            _last_error = None
        logger.info("NTP-Sync mit %s erfolgreich, Offset %.3fs", NTP_SERVER, response.offset)
        return True
    except Exception as exc:
        with _lock:
            _last_error = str(exc)
        logger.warning("NTP-Sync mit %s fehlgeschlagen: %s", NTP_SERVER, exc)
        return False


def now_utc() -> datetime:
    """Aktuelle Zeit, um den zuletzt ermittelten NTP-Offset korrigiert (0, falls
    noch nie/nie erfolgreich synchronisiert - dann identisch zur Systemuhr)."""
    with _lock:
        offset = _offset
    return datetime.now(timezone.utc) + offset


def status() -> dict:
    with _lock:
        return {
            "server": NTP_SERVER,
            "offset_seconds": _offset.total_seconds(),
            "last_sync": _last_sync,
            "last_error": _last_error,
        }
