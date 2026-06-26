"""
Enriched response shapes — join claim with its vehicle/customer/adjuster
so the UI (and later the Copilot) gets everything in one payload.
"""
from sqlmodel import Session
from app.models import Claim, Vehicle, Policy, Customer, User


def claim_summary(claim: Claim, session: Session) -> dict:
    v = session.get(Vehicle, claim.vehicle_id)
    pol = session.get(Policy, claim.policy_id)
    cust = session.get(Customer, pol.customer_id) if pol else None
    adj = session.get(User, claim.adjuster_id)
    return {
        "id": claim.id,
        "claim_number": claim.claim_number,
        "status": claim.status.value,
        "peril_type": claim.peril_type.value,
        "fraud_flagged": claim.fraud_flagged,
        "fraud_score": claim.fraud_score,
        "estimated_amount": claim.estimated_amount,
        "reserve_amount": claim.reserve_amount,
        "approved_amount": claim.approved_amount,
        "deductible": claim.deductible,
        "loss_date": str(claim.loss_date),
        "reported_date": str(claim.reported_date),
        "sla_due_date": claim.sla_due_date.isoformat(),
        "updated_at": claim.updated_at.isoformat(),
        "incident_city": claim.incident_city,
        "incident_state": claim.incident_state,
        "vehicle": {"vin": v.vin, "make": v.make, "model": v.model,
                    "year": v.year, "color": v.color} if v else None,
        "customer": {"name": cust.name, "phone": cust.phone,
                     "email": cust.email} if cust else None,
        "policy": {"policy_number": pol.policy_number,
                   "coverage_type": pol.coverage_type,
                   "in_force": pol.in_force} if pol else None,
        "adjuster": {"id": adj.id, "name": adj.name} if adj else None,
    }


def claim_detail(claim: Claim, session: Session) -> dict:
    base = claim_summary(claim, session)
    base["description"] = claim.description
    return base
