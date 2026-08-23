"""Zentraler Schreib-Helfer für das Aktivitätsprotokoll (siehe AuditLog in
models.py). Wird direkt in den Admin-/Stammdaten-Routen in main.py
aufgerufen, jeweils kurz VOR dem regulären db.commit() der eigentlichen
Änderung (log_audit committet selbst nichts) - der Log-Eintrag landet
dadurch atomar in derselben Transaktion wie die Änderung, die er
protokolliert, statt bei einem fehlgeschlagenen/zurückgerollten Vorgang
verwaist als Eintrag ohne zugehörige Änderung stehen zu bleiben."""
from .models import AuditLog


def log_audit(db, user, action: str, target_type: str, details: str, request=None) -> None:
    """user darf None sein (z.B. ein rein systemseitiger Vorgang) - action/
    target_type sind freie, aber innerhalb der Aufrufstellen konsistent
    gehaltene Kurzbezeichner (siehe admin_audit_log.html für die Filter-
    Optionen, die aus den tatsächlich vorkommenden Werten gespeist werden)."""
    ip_address = None
    if request is not None:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            ip_address = forwarded.split(",")[0].strip()
        elif request.client:
            ip_address = request.client.host
    db.add(AuditLog(
        user_id=user.id if user else None,
        user_name=user.name if user else None,
        action=action,
        target_type=target_type,
        details=details,
        ip_address=ip_address,
    ))
