import bcrypt
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session

from .database import get_db
from .models import User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def find_user_by_identifier(db: Session, identifier: str):
    """Sucht einen Nutzer per Name ODER Personalnummer (Login-Feld ersetzt das
    frühere Auswahl-Dropdown - ab mehr als ein paar Mitarbeitern unpraktisch,
    zumal betriebsintern ohnehin oft mit Personalnummer angemeldet wird)."""
    identifier = identifier.strip()
    if not identifier:
        return None
    return db.query(User).filter(
        (User.name == identifier) | (User.personnel_number == identifier)
    ).first()


def get_current_user(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    # Ein waehrend einer laufenden Session deaktiviertes Konto gilt sofort als
    # ausgeloggt, nicht erst beim naechsten Login-Versuch - sonst koennte ein
    # bereits eingeloggter, gerade deaktivierter Nutzer im offenen Tab
    # ungehindert weiterarbeiten.
    if user and not user.is_active:
        return None
    return user


def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Login erforderlich")
    return user


def require_admin(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_login(request, db)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Nur für Admins")
    return user


def require_admin_or_shift_lead(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_login(request, db)
    if not (user.is_admin or user.is_shift_lead):
        raise HTTPException(status_code=403, detail="Nur für Admins oder Schichtleiter")
    return user


def require_admin_or_developer(request: Request, db: Session = Depends(get_db)) -> User:
    """Für Aktionen, die bisher Admin-only waren (meist Löschen), aber keinen
    Bezug zu Personal-/Zeiterfassungsdaten haben - Entwickler dürfen hier
    ran, damit sie die App technisch vollständig testen können, ohne
    Zugriff auf Benutzerverwaltung oder Zeiterfassung zu bekommen (siehe
    require_admin, das für genau diese beiden Bereiche bewusst weiterhin
    Entwickler ausschließt)."""
    user = require_login(request, db)
    if not (user.is_admin or user.is_developer):
        raise HTTPException(status_code=403, detail="Nur für Admins oder Entwickler")
    return user


def require_staff_or_developer(request: Request, db: Session = Depends(get_db)) -> User:
    """Wie require_admin_or_shift_lead, zusätzlich für Entwickler geöffnet -
    für alle Verwaltungsbereiche außer Benutzerverwaltung und Zeiterfassung
    (die bleiben bewusst bei require_admin_or_shift_lead bzw. require_admin,
    damit ein Entwicklerkonto dort technisch keinen Zugriff bekommt)."""
    user = require_login(request, db)
    if not (user.is_admin or user.is_shift_lead or user.is_developer):
        raise HTTPException(status_code=403, detail="Nur für Admins, Schichtleiter oder Entwickler")
    return user
