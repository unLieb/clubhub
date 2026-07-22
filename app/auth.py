import bcrypt
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session

from .database import get_db
from .models import User


def hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_pin(pin: str, pin_hash: str) -> bool:
    try:
        return bcrypt.checkpw(pin.encode("utf-8"), pin_hash.encode("utf-8"))
    except ValueError:
        return False


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
