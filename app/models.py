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

    groups = relationship("Group", secondary=user_group, back_populates="users")
    completions = relationship("Completion", back_populates="user", cascade="all, delete-orphan")


class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    nfc_tag_id = Column(String, unique=True, nullable=True)  # frei wählbarer Code im NFC-Tag

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
    unit = Column(String, nullable=True)              # z.B. "Stück", "Liter", "Packung"
    stock_current = Column(Float, default=0.0)         # Ist
    stock_min = Column(Float, default=0.0)             # Soll / Mindestbestand
    category = Column(String, nullable=True)          # frei vergeben, z.B. "Reinigungsmittel"
    location = Column(String, nullable=True)          # Lagerort, frei vergeben, z.B. "Lager A"
    reorder_url = Column(String, nullable=True)        # Produktseite zum Nachbestellen (nur Link, kein Checkout)
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
    status = Column(String, default="open")             # "open" | "done"
    created_at = Column(DateTime(timezone=True), default=utcnow)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    room = relationship("Room")
    user = relationship("User", foreign_keys=[user_id])
    resolved_by = relationship("User", foreign_keys=[resolved_by_id])
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
