import os
import logging
import httpx

NTFY_BASE_URL = os.environ.get("NTFY_BASE_URL", "").rstrip("/")
GOTIFY_BASE_URL = os.environ.get("GOTIFY_BASE_URL", "").rstrip("/")
SIGNAL_BASE_URL = os.environ.get("SIGNAL_BASE_URL", "").rstrip("/")
SIGNAL_SENDER_NUMBER = os.environ.get("SIGNAL_SENDER_NUMBER", "")

logger = logging.getLogger("reinigungsplan.notifications")


def notify_group(group, title: str, message: str, priority: str = "default"):
    """Schickt eine Push-Nachricht über alle Benachrichtigungskanäle einer Gruppe."""
    for channel in group.channels:
        if channel.type == "ntfy" and channel.target and NTFY_BASE_URL:
            try:
                httpx.post(
                    f"{NTFY_BASE_URL}/{channel.target}",
                    content=message.encode("utf-8"),
                    headers={"Title": title, "Priority": priority},
                    timeout=10,
                )
            except Exception as e:
                logger.warning(f"ntfy-Benachrichtigung fehlgeschlagen ({channel.name}): {e}")

        elif channel.type == "gotify" and channel.target and GOTIFY_BASE_URL:
            try:
                httpx.post(
                    f"{GOTIFY_BASE_URL}/message",
                    params={"token": channel.target},
                    data={"title": title, "message": message, "priority": 5},
                    timeout=10,
                )
            except Exception as e:
                logger.warning(f"Gotify-Benachrichtigung fehlgeschlagen ({channel.name}): {e}")

        elif channel.type == "signal" and channel.target and SIGNAL_BASE_URL and SIGNAL_SENDER_NUMBER:
            try:
                httpx.post(
                    f"{SIGNAL_BASE_URL}/v2/send",
                    json={
                        "message": f"{title}\n{message}",
                        "number": SIGNAL_SENDER_NUMBER,
                        "recipients": [channel.target],
                    },
                    timeout=10,
                )
            except Exception as e:
                logger.warning(f"Signal-Benachrichtigung fehlgeschlagen ({channel.name}): {e}")
