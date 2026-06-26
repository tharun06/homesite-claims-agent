"""Login (mock — pick a seeded user) + identity."""
from fastapi import APIRouter, Depends, HTTPException, Form
from sqlmodel import Session, select

from app.database import get_session
from app.models import User, Team
from app.auth import create_token, get_current_user

router = APIRouter()


@router.get("/auth/users")
def list_login_users(session: Session = Depends(get_session)):
    """User picker for the mock login screen — grouped info per user."""
    users = session.exec(select(User).where(User.active == True)).all()  # noqa: E712
    teams = {t.id: t.name for t in session.exec(select(Team)).all()}
    return [
        {"id": u.id, "name": u.name, "email": u.email,
         "role": u.role.value, "team": teams.get(u.team_id)}
        for u in users
    ]


@router.post("/auth/login")
def login(email: str = Form(...), session: Session = Depends(get_session)):
    """Mock login: no password, just resolve the user by email → token."""
    user = session.exec(select(User).where(User.email == email)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"access_token": create_token(user), "token_type": "bearer",
            "user": {"id": user.id, "name": user.name, "role": user.role.value}}


@router.get("/me")
def me(user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    team = session.get(Team, user.team_id) if user.team_id else None
    return {"id": user.id, "name": user.name, "email": user.email,
            "role": user.role.value, "team": team.name if team else None}
