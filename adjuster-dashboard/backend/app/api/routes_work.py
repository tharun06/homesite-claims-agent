"""My-work endpoints: assigned tasks, assignments, queue metrics."""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, select, func

from app.database import get_session
from app.models import ClaimTask, Claim, User, ClaimStatus, Role
from app.auth import get_current_user
from app.scoping import scope_claims

router = APIRouter()


@router.get("/me/tasks")
def my_tasks(status: Optional[str] = None, days: int = 7,
             user: User = Depends(get_current_user),
             session: Session = Depends(get_session)):
    """Tasks assigned to me, optionally filtered by status, within N days."""
    q = select(ClaimTask).where(ClaimTask.assigned_to == user.id)
    if status:
        q = q.where(ClaimTask.status == status)
    since = datetime.utcnow() - timedelta(days=days)
    q = q.where(ClaimTask.created_at >= since).order_by(ClaimTask.due_date)
    tasks = session.exec(q).all()
    return [{"id": t.id, "claim_id": t.claim_id, "task_type": t.task_type,
             "description": t.description, "status": t.status.value,
             "due_date": t.due_date.isoformat()} for t in tasks]


@router.get("/me/assignments")
def my_assignments(days: int = 7, user: User = Depends(get_current_user),
                   session: Session = Depends(get_session)):
    """Claims assigned to me (or my scope) in the last N days."""
    since = datetime.utcnow() - timedelta(days=days)
    q = scope_claims(select(Claim), user, session).where(Claim.created_at >= since)
    claims = session.exec(q.order_by(Claim.created_at.desc())).all()
    return {"count": len(claims),
            "claims": [{"id": c.id, "claim_number": c.claim_number,
                        "status": c.status.value, "created_at": c.created_at.isoformat()}
                       for c in claims]}


@router.get("/metrics/queue")
def queue_metrics(user: User = Depends(get_current_user),
                  session: Session = Depends(get_session)):
    """Scoped queue metrics — counts by status, SLA breaches, fraud flagged."""
    scoped = scope_claims(select(Claim), user, session)
    claims = session.exec(scoped).all()
    now = datetime.utcnow()

    by_status = {}
    sla_breaches = 0
    fraud = 0
    open_count = 0
    closed_statuses = {ClaimStatus.CLOSED, ClaimStatus.DENIED, ClaimStatus.APPROVED}
    for c in claims:
        by_status[c.status.value] = by_status.get(c.status.value, 0) + 1
        if c.status not in closed_statuses:
            open_count += 1
            if c.sla_due_date < now:
                sla_breaches += 1
        if c.fraud_flagged:
            fraud += 1

    return {"total": len(claims), "open": open_count,
            "sla_breaches": sla_breaches, "fraud_flagged": fraud,
            "by_status": by_status}
