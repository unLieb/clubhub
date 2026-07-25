from sqlalchemy import (
    Column, Integer, String, ForeignKey, DateTime, Table, Boolean, Float
)
from sqlalchemy.orm import relationship

from .database import Base
from . import ntptime


def utcnow():
    return ntptime.now_utc()


# --- Verknüpfungstabellen (n:m) ---

user_group = Table(
    "user_group",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("group_id", Integer, ForeignKey("groups.id"), primary_key=True),
)

room_group = Table(
    "room_group",
    Base.metadata,
    Column("room_id", Integer, ForeignKey("rooms.id"), primary_key=True),
    Column("group_id", Integer, ForeignKey("groups.id"), primary_key=True),
)

group_channel = Table(
    "group_channel",
    Base.metadata,
    Column("group_id", Integer, ForeignKey("groups.id"), primary_key=True),
    Column("channel_id", Integer, ForeignKey("notification_channels.id"), primary_key=True),
)


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    # Arbeitszeit (Stunde 0-23), außerhalb derer keine Benachrichtigungen
    # zugestellt werden; None/None = keine Einschränkung
    work_start_hour = Column(Integer, nullable=True)
    work_end_hour = Column(Integer, nullable=True)

    users = relationship("User", secondary=user_group, back_populates="groups")
    rooms = relationship("Room", secondary=room_group, back_populates="groups")
    channels = relationship("NotificationChannel", secondary=group_channel, back_populates="groups")


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)           # freier Anzeigename, z.B. "Hausmeister ntfy"
    type = Column(String, nullable=False)            # "ntfy" | "gotify" | "signal"
    target = Column(String, nullable=True)           # ntfy-Topic / Gotify-Token / Signal-Empfängernummer

    groups = relationship("Group", secondary=group_channel, back_populates="channels")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    pin_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    # Schichtleiter: darf in der Verwaltung fast alles außer löschen und darf
    # niemandem Admin-/Schichtleiter-Rechte zuweisen (nur ein Admin darf das).
    is_shift_lead = Column(Boolean, default=False)
    # Stundensatz pflegt jeder Nutzer selbst im eigenen Profil (siehe /profile) -
    # nur er sieht den daraus berechneten Verdienst in der Zeiterfassung.
    hourly_wage = Column(Float, nullable=True)              # Stundensatz in €
    # Soll-Arbeitszeit/Monat bleibt admin-gepflegt, siehe admin_users.html.
    target_hours_per_month = Column(Float, nullable=True)   # für Überstunden-Berechnung
    avatar_url = Column(String, nullable=True)

    groups = relationship("Group", secondary=user_group, back_populates="users")
    completions = relationship("Completion", back_populates="user", cascade="all, delete-orphan")
    time_entries = relationship(
        "TimeEntry", back_populates="user", cascade="all, delete-orphan",
        order_by="desc(TimeEntry.clock_in)"
    )
    push_subscriptions = relationship(
        "PushSubscription", back_populates="user", cascade="all, delete-orphan"
    )


class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)

    groups = relationship("Group", secondary=room_group, back_populates="rooms")
    tasks = relationship("Task", back_populates="room", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)

    interval_hours = Column(Float, nullable=False)      # rollierendes Intervall
    warn_hours = Column(Float, default=5.0)              # ab wie vielen Stunden vor Fälligkeit gelb

    room = relationship("Room", back_populates="tasks")
    completions = relationship(
        "Completion", back_populates="task", cascade="all, delete-orphan",
        order_by="desc(Completion.timestamp)"
    )


class Completion(Base):
    __tablename__ = "completions"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    timestamp = Column(DateTime(timezone=True), default=utcnow)

    task = relationship("Task", back_populates="completions")
    user = relationship("User", back_populates="completions")


class TaskGroupNotice(Base):
    """Merkt sich pro (Aufgabe, Gruppe) den zuletzt tatsächlich zugestellten
    Status, damit jede Gruppe unabhängig von anderen (z.B. wegen Arbeitszeiten)
    benachrichtigt bzw. später nachbenachrichtigt werden kann."""
    __tablename__ = "task_group_notices"

    task_id = Column(Integer, ForeignKey("tasks.id"), primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"), primary_key=True)
    last_status = Column(String, default="green")


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    unit = Column(String, nullable=True)              # gezähltes Gebinde (Einzahl), z.B. "Kanister", "Rolle"
    unit_plural = Column(String, nullable=True)        # Mehrzahl von unit, z.B. "Rollen"; leer = unit für alle Mengen
    pack_size = Column(Float, nullable=True)           # Inhalt je Gebinde, z.B. 10 (Liter) oder 8 (Rollen)
    pack_unit = Column(String, nullable=True)          # Einheit des Inhalts, z.B. "Liter", "Rollen", "Stück"
    stock_current = Column(Float, default=0.0)         # Ist (in Gebinden, z.B. Kanister)
    stock_min = Column(Float, default=0.0)             # Soll-Bestand (in Gebinden) - Zielwert, ab hier "Im Soll"
    # Mindestbestand/kritische Schwelle (in Gebinden) - darunter "critical" statt nur
    # "low". Optional; ohne eigenen Wert wird die Hälfte von stock_min angenommen
    # (siehe status.inventory_critical_threshold).
    stock_critical = Column(Float, nullable=True)
    category = Column(String, nullable=True)          # frei vergeben, z.B. "Reinigungsmittel"
    location = Column(String, nullable=True)          # Lagerort, frei vergeben, z.B. "Lager A"
    reorder_url = Column(String, nullable=True)        # Produktseite zum Nachbestellen (nur Link, kein Checkout)
    image_url = Column(String, nullable=True)          # entweder /uploads/inventory/... oder externe URL
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    notified = Column(Boolean, default=False)          # schon über aktuelle Unterschreitung informiert?

    group = relationship("Group")
    movements = relationship(
        "InventoryMovement", back_populates="item", cascade="all, delete-orphan",
        order_by="desc(InventoryMovement.timestamp)"
    )


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"

    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    delta = Column(Float, nullable=False)              # + Lieferung / - Verbrauch
    note = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=utcnow)

    item = relationship("InventoryItem", back_populates="movements")
    user = relationship("User")


class Report(Base):
    """Meldung: Mitarbeiter melden Defekte/Fehlendes zu einem Bereich,
    optional mit mehreren Fotos. Kann von jedem eingeloggten Nutzer als
    erledigt markiert werden (wie das Abhaken einer Aufgabe)."""
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    comment = Column(String, nullable=False)
    # Veraltet (einzelnes Foto) - bleibt für Altdaten stehen, siehe ReportPhoto.
    # create_all() legt nur fehlende Tabellen an und ändert bestehende nicht,
    # daher wird diese Spalte nie entfernt, nur nicht mehr neu befüllt.
    photo_filename = Column(String, nullable=True)
    priority = Column(String, default="normal")         # "critical" | "high" | "normal" | "low"
    category = Column(String, default="sonstiges")      # "defekt" | "material" | "reinigung" | "sonstiges"
    status = Column(String, default="open")             # "open" | "in_progress" | "done"
    # Zuständige Gruppe für diese konkrete Meldung (z.B. "Technik" bei einem
    # Defekt), unabhängig von den Gruppen des Bereichs - None = wie bisher an
    # die Bereichsgruppen melden.
    assigned_group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    room = relationship("Room")
    user = relationship("User", foreign_keys=[user_id])
    resolved_by = relationship("User", foreign_keys=[resolved_by_id])
    assigned_group = relationship("Group")
    photos = relationship(
        "ReportPhoto", back_populates="report", cascade="all, delete-orphan", order_by="ReportPhoto.id"
    )
    comments = relationship(
        "ReportComment", back_populates="report", cascade="all, delete-orphan", order_by="ReportComment.created_at"
    )


class ReportPhoto(Base):
    __tablename__ = "report_photos"

    id = Column(Integer, primary_key=True)
    report_id = Column(Integer, ForeignKey("reports.id"), nullable=False)
    filename = Column(String, nullable=False)           # relativ zu uploads/reports/

    report = relationship("Report", back_populates="photos")


class ReportComment(Base):
    __tablename__ = "report_comments"

    id = Column(Integer, primary_key=True)
    report_id = Column(Integer, ForeignKey("reports.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    text = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    report = relationship("Report", back_populates="comments")
    user = relationship("User")


class NfcTag(Base):
    """Registrierter physischer NFC-Tag - reine Verwaltungs-/Bestandsliste
    für Admins (welcher Tag liegt wo, ist er noch der erwartete). Steuert
    selbst keinerlei Zugriff: die Scan-Route (/room/<id>) funktioniert
    unabhängig davon, ob ein Tag hier eingetragen ist."""
    __tablename__ = "nfc_tags"

    id = Column(Integer, primary_key=True)
    uid = Column(String, unique=True, nullable=True)   # physische Seriennummer, erst nach erstem Scan bekannt
    label = Column(String, nullable=True)              # freier Anzeigename, z.B. "Eingangstür"
    target_type = Column(String, nullable=False)        # "room" (bisher auch "timeclock", siehe Migration)
    target_room_id = Column(Integer, ForeignKey("rooms.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)

    target_room = relationship("Room")


class TimeEntry(Base):
    """Ein Kommen/Gehen-Paar der Zeiterfassung. Wird ausschließlich über das
    autorisierte Zeiterfassungs-Terminal (/timeclock/kiosk) erzeugt/geschlossen
    (Toggle je nachdem, ob der Nutzer schon eine offene Buchung hat), damit
    das Ein-/Ausstempeln an ein konkretes, stationäres Gerät gebunden bleibt.
    clock_out ist None, solange der Nutzer noch eingestempelt ist."""
    __tablename__ = "time_entries"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    clock_in = Column(DateTime(timezone=True), default=utcnow)
    clock_out = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="time_entries")


class AuthorizedDevice(Base):
    """Genau ein Gerät kann gleichzeitig für das Zeiterfassungs-Terminal
    (/timeclock/kiosk) autorisiert sein - das Autorisieren eines neuen Geräts
    ersetzt Token und Angaben dieser einzigen Zeile, wodurch das vorherige
    Gerät automatisch die Berechtigung verliert. Verhindert, dass Ein-/
    Ausstempeln von einem beliebigen Gerät (z.B. dem eigenen Handy von
    zuhause) aus möglich ist."""
    __tablename__ = "authorized_device"

    id = Column(Integer, primary_key=True)
    token = Column(String, nullable=False)
    label = Column(String, nullable=True)               # freier Anzeigename, z.B. "Tablet Empfang"
    authorized_at = Column(DateTime(timezone=True), default=utcnow)
    authorized_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    authorized_by = relationship("User")


class PushSubscription(Base):
    """Eine Web-Push-Anmeldung eines Browsers/Geräts für einen Nutzer (siehe
    push.py). Ein Nutzer kann mehrere haben (z.B. Handy + Desktop). endpoint
    ist eindeutig pro Browser-Installation, daher als Unique-Key genutzt, um
    beim erneuten Abonnieren einfach zu aktualisieren statt zu duplizieren."""
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    endpoint = Column(String, unique=True, nullable=False)
    p256dh = Column(String, nullable=False)
    auth = Column(String, nullable=False)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    user = relationship("User", back_populates="push_subscriptions")
