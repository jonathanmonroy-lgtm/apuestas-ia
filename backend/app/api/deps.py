"""Request-scoped dependencies shared by the user-facing endpoints."""

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import User


def current_user(
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> User:
    """PLACEHOLDER while there is no authentication (INTERNAL MODE, single local user).

    Every request is the internal user, created on first use. This must be replaced by
    real auth (and age verification) before anyone else can reach the API.
    """
    user = db.scalar(select(User).where(User.email == settings.internal_user_email))
    if user is None:
        user = User(email=settings.internal_user_email)
        db.add(user)
        db.commit()
    return user
