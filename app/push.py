import os
import json
import base64
import logging

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import webpush, WebPushException

from .database import DB_PATH

logger = logging.getLogger("reinigungsplan.push")

# Liegt neben der Datenbank im persistenten Datenverzeichnis, damit der
# Schlüssel Neustarts/Updates übersteht - ein neuer Schlüssel würde alle
# bestehenden Browser-Abos ungültig machen (jeder Nutzer müsste erneut
# zustimmen).
_VAPID_DIR = os.path.dirname(DB_PATH)
_VAPID_PRIVATE_KEY_PATH = os.path.join(_VAPID_DIR, "vapid_private_key.pem")
VAPID_CONTACT_EMAIL = os.environ.get("VAPID_CONTACT_EMAIL", "admin@example.com")

_private_key_pem = None
_public_key_b64 = None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _generate_keys() -> bytes:
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    os.makedirs(_VAPID_DIR, exist_ok=True)
    with open(_VAPID_PRIVATE_KEY_PATH, "wb") as f:
        f.write(pem)
    return pem


def _ensure_keys():
    """Lädt den VAPID-Schlüssel (erzeugt ihn beim allerersten Aufruf einmalig)
    und leitet den öffentlichen Schlüssel im für die Push-API nötigen Format
    ab (unkomprimierter EC-Punkt, base64url ohne Padding)."""
    global _private_key_pem, _public_key_b64
    if _private_key_pem is not None:
        return
    if os.path.exists(_VAPID_PRIVATE_KEY_PATH):
        with open(_VAPID_PRIVATE_KEY_PATH, "rb") as f:
            pem = f.read()
    else:
        pem = _generate_keys()

    private_key = serialization.load_pem_private_key(pem, password=None)
    numbers = private_key.public_key().public_numbers()
    x = numbers.x.to_bytes(32, "big")
    y = numbers.y.to_bytes(32, "big")
    _public_key_b64 = _b64url(b"\x04" + x + y)
    _private_key_pem = pem


def get_vapid_public_key() -> str:
    _ensure_keys()
    return _public_key_b64


def send_web_push(subscription, title: str, message: str, url: str = "/") -> bool:
    """Schickt eine Web-Push-Benachrichtigung an eine einzelne Subscription.
    Gibt False zurück, wenn die Subscription nicht mehr gültig ist (Nutzer hat
    Benachrichtigungen im Browser deaktiviert o.ä.) - der Aufrufer sollte sie
    dann aus der Datenbank entfernen. True bei Erfolg oder unbekanntem Fehler
    (z.B. Server kurz nicht erreichbar - nicht gleich die Subscription killen)."""
    _ensure_keys()
    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=json.dumps({"title": title, "body": message, "url": url}),
            vapid_private_key=_VAPID_PRIVATE_KEY_PATH,
            vapid_claims={"sub": f"mailto:{VAPID_CONTACT_EMAIL}"},
            timeout=10,
        )
        return True
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        if status in (404, 410):
            return False
        logger.warning(f"Web-Push fehlgeschlagen: {e}")
        return True
    except requests.exceptions.RequestException as e:
        # Push-Dienst gerade nicht erreichbar o.ä. - kein Grund, die
        # Subscription zu löschen, könnte beim nächsten Mal klappen.
        logger.warning(f"Web-Push: Netzwerkfehler: {e}")
        return True
    except Exception as e:
        # z.B. korrupte/ungültige Schlüssel in der Subscription - kann nie
        # erfolgreich werden, also aufräumen statt endlos zu versuchen.
        logger.warning(f"Web-Push: ungültige Subscription, wird entfernt: {e}")
        return False
