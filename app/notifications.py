import os
import logging
import httpx

from .database import SessionLocal
from .models import PushSubscription
from . import push as webpush_module

NTFY_BASE_URL = os.environ.get("NTFY_BASE_URL", "").rstrip("/")
GOTIFY_BASE_URL = os.environ.get("GOTIFY_BASE_URL", "").rstrip("/")
SIGNAL_BASE_URL = os.environ.get("SIGNAL_BASE_URL", "").rstrip("/")
SIGNAL_SENDER_NUMBER = os.environ.get("SIGNAL_SENDER_NUMBER", "")

logger = logging.getLogger("reinigungsplan.notifications")


def notify_group(group, title: str, message: str, priority: str = "default", url: str = "/"):
    """Schickt eine Push-Nachricht über alle Benachrichtigungskanäle einer
    Gruppe sowie zusätzlich per Browser-Push (Web Push) an alle Mitglieder,
    die das in ihrem Browser aktiviert haben - kein extra Kanal nötig."""
    for channel in group.channels:
        if channel.type == "ntfy" and channel.target and NTFY_BASE_URL:
            try:
                resp = httpx.post(
                    f"{NTFY_BASE_URL}/{channel.target}",
                    content=message.encode("utf-8"),
                    headers={"Title": title, "Priority": priority},
                    timeout=10,
                )
                if resp.is_error:
                    logger.warning(f"ntfy-Benachrichtigung fehlgeschlagen ({channel.name}): HTTP {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"ntfy-Benachrichtigung fehlgeschlagen ({channel.name}): {e}")

        elif channel.type == "gotify" and channel.target and GOTIFY_BASE_URL:
            try:
                resp = httpx.post(
                    f"{GOTIFY_BASE_URL}/message",
                    params={"token": channel.target},
                    data={"title": title, "message": message, "priority": 5},
                    timeout=10,
                )
                if resp.is_error:
                    logger.warning(f"Gotify-Benachrichtigung fehlgeschlagen ({channel.name}): HTTP {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"Gotify-Benachrichtigung fehlgeschlagen ({channel.name}): {e}")

        elif channel.type == "signal" and channel.target and SIGNAL_BASE_URL and SIGNAL_SENDER_NUMBER:
            try:
                resp = httpx.post(
                    f"{SIGNAL_BASE_URL}/v2/send",
                    json={
                        "message": f"{title}\n{message}",
                        "number": SIGNAL_SENDER_NUMBER,
                        "recipients": [channel.target],
                    },
                    timeout=10,
                )
                if resp.is_error:
                    logger.warning(f"Signal-Benachrichtigung fehlgeschlagen ({channel.name}): HTTP {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"Signal-Benachrichtigung fehlgeschlagen ({channel.name}): {e}")

    stale_subscription_ids = []
    for member in group.users:
        for subscription in member.push_subscriptions:
            if not webpush_module.send_web_push(subscription, title, message, url):
                stale_subscription_ids.append(subscription.id)

    if stale_subscription_ids:
        # Browser hat die Subscription widerrufen (z.B. Benachrichtigungen
        # deaktiviert, Cache geleert) - eigene, kurzlebige Session, da diese
        # Funktion auch mit einer bereits geschlossenen db-Session (Background-
        # Task nach dem Response) aufgerufen werden kann.
        cleanup_db = SessionLocal()
        try:
            cleanup_db.query(PushSubscription).filter(
                PushSubscription.id.in_(stale_subscription_ids)
            ).delete(synchronize_session=False)
            cleanup_db.commit()
        finally:
            cleanup_db.close()


def notify_user(user, title: str, message: str, url: str = "/"):
    """Schickt eine Browser-Push-Nachricht an genau eine Person (z.B. den
    Melder einer Meldung, wenn sich deren Status ändert) - unabhängig von
    Gruppen-Kanälen (ntfy/Gotify/Signal), da das eine persönliche
    Rückmeldung ist statt einer Gruppen-Benachrichtigung."""
    stale_subscription_ids = []
    for subscription in user.push_subscriptions:
        if not webpush_module.send_web_push(subscription, title, message, url):
            stale_subscription_ids.append(subscription.id)

    if stale_subscription_ids:
        cleanup_db = SessionLocal()
        try:
            cleanup_db.query(PushSubscription).filter(
                PushSubscription.id.in_(stale_subscription_ids)
            ).delete(synchronize_session=False)
            cleanup_db.commit()
        finally:
            cleanup_db.close()
