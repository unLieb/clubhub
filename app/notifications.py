import os
import logging
import httpx

from sqlalchemy.orm import object_session

from .database import SessionLocal
from .models import PushSubscription, AppSettings
from . import push as webpush_module

logger = logging.getLogger("reinigungsplan.notifications")


def _resolve_setting(db_value, env_name: str, strip_trailing_slash: bool = False) -> str:
    value = (db_value or "").strip()
    if not value:
        value = os.environ.get(env_name, "").strip()
    return value.rstrip("/") if strip_trailing_slash else value


def _channel_config(db) -> dict:
    """Basis-URLs/Zugangsdaten fuer ntfy/Gotify/Signal: primaer aus der
    Verwaltung (Benachrichtigungen -> Verbindungen, siehe AppSettings),
    ansonsten Fallback auf die gleichnamige Umgebungsvariable - deckt sowohl
    frisch per docker-compose konfigurierte Installationen als auch den
    Fall ab, dass jemand keinen Zugriff auf den Docker-Stack hat und alles
    direkt in der App eintragen will. db darf None sein (z.B. bei fehlender
    Session), dann greift ausschliesslich die Umgebungsvariable."""
    settings = db.query(AppSettings).first() if db is not None else None
    return {
        "ntfy_base_url": _resolve_setting(settings.ntfy_base_url if settings else None, "NTFY_BASE_URL", True),
        "gotify_base_url": _resolve_setting(settings.gotify_base_url if settings else None, "GOTIFY_BASE_URL", True),
        "signal_base_url": _resolve_setting(settings.signal_base_url if settings else None, "SIGNAL_BASE_URL", True),
        "signal_sender_number": _resolve_setting(settings.signal_sender_number if settings else None, "SIGNAL_SENDER_NUMBER"),
    }


def notify_group(group, title: str, message: str, priority: str = "default", url: str = "/"):
    """Schickt eine Push-Nachricht über alle Benachrichtigungskanäle einer
    Gruppe sowie zusätzlich per Browser-Push (Web Push) an alle Mitglieder,
    die das in ihrem Browser aktiviert haben - kein extra Kanal nötig."""
    channels = list(group.channels)
    # group kann auch das nicht an eine Session gebundene _MergedGroup-
    # Platzhalterobjekt aus notify_groups sein - die Session daher ueber
    # einen der (echten, gemappten) Kanaele selbst ermitteln.
    db = next((s for s in (object_session(c) for c in channels) if s is not None), None)
    config = _channel_config(db)

    for channel in channels:
        if not channel.is_active:
            continue

        if channel.type == "ntfy" and channel.target and config["ntfy_base_url"]:
            try:
                resp = httpx.post(
                    f"{config['ntfy_base_url']}/{channel.target}",
                    content=message.encode("utf-8"),
                    headers={"Title": title, "Priority": priority},
                    timeout=10,
                )
                if resp.is_error:
                    logger.warning(f"ntfy-Benachrichtigung fehlgeschlagen ({channel.name}): HTTP {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"ntfy-Benachrichtigung fehlgeschlagen ({channel.name}): {e}")

        elif channel.type == "gotify" and channel.target and config["gotify_base_url"]:
            try:
                resp = httpx.post(
                    f"{config['gotify_base_url']}/message",
                    params={"token": channel.target},
                    data={"title": title, "message": message, "priority": 5},
                    timeout=10,
                )
                if resp.is_error:
                    logger.warning(f"Gotify-Benachrichtigung fehlgeschlagen ({channel.name}): HTTP {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"Gotify-Benachrichtigung fehlgeschlagen ({channel.name}): {e}")

        elif channel.type == "signal" and channel.target and config["signal_base_url"] and config["signal_sender_number"]:
            try:
                resp = httpx.post(
                    f"{config['signal_base_url']}/v2/send",
                    json={
                        "message": f"{title}\n{message}",
                        "number": config["signal_sender_number"],
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


def notify_groups(groups, title: str, message: str, priority: str = "default", url: str = "/"):
    """Wie notify_group, aber fuer mehrere Gruppen gleichzeitig (z.B. ein
    Termin mit mehreren ausgewaehlten Gruppen oder "Alle (Betriebsweit)") -
    dedupliziert Kanaele und Mitglieder ueber alle Gruppen hinweg, damit
    jemand, der in mehreren Zielgruppen ist (oder ein geteilter Kanal),
    nicht mehrfach dieselbe Nachricht bekommt."""
    seen_channel_ids = set()
    deduped_channels = []
    seen_user_ids = set()
    deduped_users = []
    for group in groups:
        for channel in group.channels:
            if channel.id in seen_channel_ids:
                continue
            seen_channel_ids.add(channel.id)
            deduped_channels.append(channel)
        for member in group.users:
            if member.id in seen_user_ids:
                continue
            seen_user_ids.add(member.id)
            deduped_users.append(member)

    # Platzhalter-Objekt mit den deduplizierten Kanaelen/Mitgliedern, das
    # notify_group unveraendert entgegennehmen kann (kennt nur "seine
    # eigene" group.channels/group.users-Liste).
    class _MergedGroup:
        pass
    merged = _MergedGroup()
    merged.channels = deduped_channels
    merged.users = deduped_users
    notify_group(merged, title, message, priority, url)


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
