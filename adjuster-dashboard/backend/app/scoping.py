"""
Role-based claim visibility — the single source of truth for "who sees what".

Every claim query (REST now, MCP tools later) runs through scope_claims()
so authorization lives in ONE place. This is the security model Phase 2's
Copilot inherits for free.
"""
from sqlmodel import Session, select

from app.models import Claim, User, Role


def scope_claims(query, user: User, session: Session):
    """Apply the caller's role-based visibility filter to a Claim select()."""
    if user.role == Role.ADMIN:
        return query                                  # everything

    if user.role == Role.SIU_INVESTIGATOR:
        return query.where(Claim.fraud_flagged == True)   # noqa: E712

    if user.role == Role.SENIOR_ADJUSTER:
        # whole team's claims: any adjuster sharing this lead's team
        team_adjuster_ids = session.exec(
            select(User.id).where(User.team_id == user.team_id)
        ).all()
        return query.where(Claim.adjuster_id.in_(team_adjuster_ids))

    # ADJUSTER — only their own book
    return query.where(Claim.adjuster_id == user.id)


def can_view_claim(claim: Claim, user: User, session: Session) -> bool:
    """Single-claim authorization check (for detail endpoints)."""
    if user.role == Role.ADMIN:
        return True
    if user.role == Role.SIU_INVESTIGATOR:
        return claim.fraud_flagged
    if user.role == Role.SENIOR_ADJUSTER:
        adj = session.get(User, claim.adjuster_id)
        return adj is not None and adj.team_id == user.team_id
    return claim.adjuster_id == user.id
