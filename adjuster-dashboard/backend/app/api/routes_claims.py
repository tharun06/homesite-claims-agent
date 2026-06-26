"""
Claim endpoints — all role-scoped via scoping.scope_claims().
These are the primary read APIs that become MCP tools in Phase 2.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Form
from sqlmodel import Session, select

from app.database import get_session
from app.models import Claim, Conversation, ClaimEvent, User, ClaimStatus, Role
from app.auth import get_current_user
from app.scoping import scope_claims, can_view_claim
from app.serializers import claim_summary, claim_detail
from app.realtime.hub import hub

router = APIRouter()


@router.get("/claims")
def list_claims(
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(100, le=500),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """List claims visible to the caller, newest first. Filters: status, search."""
    q = select(Claim)
    q = scope_claims(q, user, session)
    if status:
        q = q.where(Claim.status == status)
    if search:
        like = f"%{search}%"
        q = q.where(Claim.claim_number.like(like))
    q = q.order_by(Claim.updated_at.desc()).limit(limit)

    claims = session.exec(q).all()
    return {"count": len(claims),
            "claims": [claim_summary(c, session) for c in claims]}


@router.get("/claims/{claim_id}")
def get_claim(claim_id: int, user: User = Depends(get_current_user),
              session: Session = Depends(get_session)):
    claim = session.get(Claim, claim_id)
    if not claim:
        raise HTTPException(404, "Claim not found")
    if not can_view_claim(claim, user, session):
        raise HTTPException(403, "Not authorized to view this claim")
    return claim_detail(claim, session)


@router.get("/claims/{claim_id}/conversations")
def get_conversations(claim_id: int, user: User = Depends(get_current_user),
                      session: Session = Depends(get_session)):
    claim = session.get(Claim, claim_id)
    if not claim:
        raise HTTPException(404, "Claim not found")
    if not can_view_claim(claim, user, session):
        raise HTTPException(403, "Not authorized")
    msgs = session.exec(
        select(Conversation).where(Conversation.claim_id == claim_id)
        .order_by(Conversation.timestamp)
    ).all()
    return [{"id": m.id, "sender_type": m.sender_type.value, "sender_name": m.sender_name,
             "channel": m.channel, "source": m.source, "content": m.content,
             "timestamp": m.timestamp.isoformat()} for m in msgs]


@router.get("/claims/{claim_id}/events")
def get_events(claim_id: int, user: User = Depends(get_current_user),
               session: Session = Depends(get_session)):
    claim = session.get(Claim, claim_id)
    if not claim or not can_view_claim(claim, user, session):
        raise HTTPException(404, "Claim not found")
    events = session.exec(
        select(ClaimEvent).where(ClaimEvent.claim_id == claim_id)
        .order_by(ClaimEvent.timestamp)
    ).all()
    return [{"event_type": e.event_type, "detail": e.detail,
             "actor": e.actor, "timestamp": e.timestamp.isoformat()} for e in events]


# ── writes ───────────────────────────────────────────────────────────────────

@router.patch("/claims/{claim_id}/status")
async def update_status(claim_id: int, new_status: str = Form(...),
                        user: User = Depends(get_current_user),
                        session: Session = Depends(get_session)):
    claim = session.get(Claim, claim_id)
    if not claim or not can_view_claim(claim, user, session):
        raise HTTPException(404, "Claim not found")
    try:
        claim.status = ClaimStatus(new_status)
    except ValueError:
        raise HTTPException(400, f"Invalid status: {new_status}")
    claim.updated_at = datetime.utcnow()
    session.add(claim)
    session.add(ClaimEvent(claim_id=claim_id, event_type="status_change",
                           detail=f"Status: {new_status}", actor=user.name))
    session.commit()
    await hub.broadcast_claim_event(claim_id, "status_change",
                                    {"claim_id": claim_id, "status": new_status,
                                     "actor": user.name})
    return {"ok": True, "status": new_status}


@router.post("/claims/{claim_id}/reassign")
async def reassign(claim_id: int, adjuster_id: int = Form(...),
                   user: User = Depends(get_current_user),
                   session: Session = Depends(get_session)):
    if user.role not in (Role.SENIOR_ADJUSTER, Role.ADMIN):
        raise HTTPException(403, "Only leads or admins can reassign")
    claim = session.get(Claim, claim_id)
    target = session.get(User, adjuster_id)
    if not claim or not target:
        raise HTTPException(404, "Claim or adjuster not found")
    claim.adjuster_id = adjuster_id
    claim.updated_at = datetime.utcnow()
    session.add(claim)
    session.add(ClaimEvent(claim_id=claim_id, event_type="assignment",
                           detail=f"Reassigned to {target.name}", actor=user.name))
    session.commit()
    await hub.broadcast_claim_event(claim_id, "assignment",
                                    {"claim_id": claim_id, "adjuster_id": adjuster_id,
                                     "adjuster_name": target.name})
    return {"ok": True, "adjuster_id": adjuster_id}


@router.post("/claims/{claim_id}/notes")
async def add_note(claim_id: int, note: str = Form(...),
                   user: User = Depends(get_current_user),
                   session: Session = Depends(get_session)):
    claim = session.get(Claim, claim_id)
    if not claim or not can_view_claim(claim, user, session):
        raise HTTPException(404, "Claim not found")
    msg = Conversation(claim_id=claim_id, sender_type="adjuster", sender_name=user.name,
                       channel="chat", source="typed", content=note)
    session.add(msg)
    session.add(ClaimEvent(claim_id=claim_id, event_type="note",
                           detail="Adjuster added a note", actor=user.name))
    session.commit()
    await hub.broadcast_claim_event(claim_id, "message",
                                    {"claim_id": claim_id, "sender_name": user.name,
                                     "content": note, "source": "typed"})
    return {"ok": True}
