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
    return db.query(User).filter(User.id == user_id).first()


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
