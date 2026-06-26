"""
Mock JWT auth + role identity.

Phase 1 has no passwords — you log in by picking a seeded user. But the
token + get_current_user dependency are real, so Phase 2's MCP tools reuse
this exact identity flow (the adjuster's token scopes every tool call).
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlmodel import Session, select

from app.config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_HOURS
from app.database import get_session
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def create_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "name": user.name,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    creds_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise creds_error
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise creds_error

    user = session.get(User, user_id)
    if not user:
        raise creds_error
    return user
