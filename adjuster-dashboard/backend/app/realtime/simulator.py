"""
Live event simulator — stands in for real telephony/voice→text + workflow.

Every few seconds it persists a real event to the DB and pushes it through
the hub: a new voice→text message lands, a status changes, a claim gets
reassigned, metrics tick. This is also the demo engine.
"""
import asyncio
import random
from datetime import datetime

from sqlmodel import Session, select

from app.database import engine
from app.models import (
    Claim, Conversation, ClaimEvent, User, ClaimStatus, Role, SenderType, Policy, Customer
)
from app.realtime.hub import hub

INCOMING_LINES = [
    "Hi, I wanted to check on the status of my claim.",
    "I just emailed over the repair estimate from the body shop.",
    "The other driver's insurance finally got back to me.",
    "Is there an update on my rental car approval?",
    "I have a few more photos of the damage to send.",
    "When can I expect the appraiser to call me?",
    "The tow company dropped my car at the network shop.",
]
ADVANCE = {
    ClaimStatus.FNOL: ClaimStatus.UNDER_REVIEW,
    ClaimStatus.UNDER_REVIEW: ClaimStatus.INVESTIGATION,
    ClaimStatus.INVESTIGATION: ClaimStatus.APPRAISAL,
    ClaimStatus.APPRAISAL: ClaimStatus.PENDING_APPROVAL,
    ClaimStatus.PENDING_APPROVAL: ClaimStatus.APPROVED,
}

_running = False


async def _emit_message():
    with Session(engine) as s:
        claim = _random_open_claim(s)
        if not claim:
            return
        pol = s.get(Policy, claim.policy_id)
        cust = s.get(Customer, pol.customer_id) if pol else None
        name = cust.name if cust else "Customer"
        content = random.choice(INCOMING_LINES)
        s.add(Conversation(claim_id=claim.id, sender_type=SenderType.CUSTOMER,
                           sender_name=name, channel="phone",
                           source="voice_transcript", content=content))
        s.add(ClaimEvent(claim_id=claim.id, event_type="message",
                         detail="Incoming voice message transcribed", actor=name))
        s.commit()
        await hub.broadcast_claim_event(claim.id, "message",
                                        {"claim_id": claim.id, "sender_name": name,
                                         "content": content, "source": "voice_transcript"})


async def _emit_status_change():
    with Session(engine) as s:
        claim = _random_open_claim(s)
        if not claim or claim.status not in ADVANCE:
            return
        new = ADVANCE[claim.status]
        claim.status = new
        claim.updated_at = datetime.utcnow()
        s.add(claim)
        s.add(ClaimEvent(claim_id=claim.id, event_type="status_change",
                         detail=f"Status: {new.value}", actor="System"))
        s.commit()
        await hub.broadcast_claim_event(claim.id, "status_change",
                                        {"claim_id": claim.id, "status": new.value,
                                         "actor": "System"})


async def _emit_assignment():
    with Session(engine) as s:
        claim = _random_open_claim(s)
        adjusters = s.exec(select(User).where(User.role == Role.ADJUSTER)).all()
        if not claim or not adjusters:
            return
        target = random.choice(adjusters)
        claim.adjuster_id = target.id
        claim.updated_at = datetime.utcnow()
        s.add(claim)
        s.add(ClaimEvent(claim_id=claim.id, event_type="assignment",
                         detail=f"Auto-assigned to {target.name}", actor="System"))
        s.commit()
        await hub.broadcast_claim_event(claim.id, "assignment",
                                        {"claim_id": claim.id, "adjuster_id": target.id,
                                         "adjuster_name": target.name})


def _random_open_claim(s: Session):
    open_states = [ClaimStatus.FNOL, ClaimStatus.UNDER_REVIEW, ClaimStatus.INVESTIGATION,
                   ClaimStatus.APPRAISAL, ClaimStatus.PENDING_APPROVAL]
    claims = s.exec(select(Claim).where(Claim.status.in_(open_states))).all()
    return random.choice(claims) if claims else None


async def run_simulator():
    """Background loop. Started on app startup."""
    global _running
    _running = True
    tick = 0
    while _running:
        await asyncio.sleep(random.uniform(3, 6))
        roll = random.random()
        try:
            if roll < 0.55:
                await _emit_message()
            elif roll < 0.80:
                await _emit_status_change()
            else:
                await _emit_assignment()
            tick += 1
            if tick % 3 == 0:
                await hub.broadcast_metrics_tick()
        except Exception as e:
            print(f"[simulator] error: {e}")


def stop_simulator():
    global _running
    _running = False
