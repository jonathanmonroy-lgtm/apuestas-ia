"""Request-scoped dependencies shared by the user-facing endpoints."""

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
    query = select(User).where(User.email == settings.internal_user_email)
    user = db.scalar(query)
    if user is None:
        db.add(User(email=settings.internal_user_email))
        try:
            db.commit()
        except IntegrityError:  # a parallel first request created it (QA F5 #6)
            db.rollback()
        user = db.scalar(query)
    return user
