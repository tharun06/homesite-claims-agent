"""Directory lookups: vehicle by VIN, repair shops by geo-proximity."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.database import get_session
from app.models import Vehicle, Policy, Customer, Claim, RepairShop, User
from app.auth import get_current_user
from app.scoping import scope_claims, can_view_claim
from app.serializers import claim_summary
from app.geo import haversine_km

router = APIRouter()


@router.get("/vehicles/{vin}")
def get_vehicle(vin: str, user: User = Depends(get_current_user),
                session: Session = Depends(get_session)):
    """Vehicle by VIN + its claims the caller is allowed to see."""
    v = session.exec(select(Vehicle).where(Vehicle.vin == vin)).first()
    if not v:
        raise HTTPException(404, "VIN not found")
    pol = session.get(Policy, v.policy_id)
    cust = session.get(Customer, pol.customer_id) if pol else None

    claim_q = scope_claims(select(Claim).where(Claim.vehicle_id == v.id), user, session)
    claims = session.exec(claim_q.order_by(Claim.updated_at.desc())).all()

    return {
        "vehicle": {"vin": v.vin, "make": v.make, "model": v.model,
                    "year": v.year, "color": v.color},
        "policy": {"policy_number": pol.policy_number, "coverage_type": pol.coverage_type,
                   "in_force": pol.in_force, "deductible": pol.deductible} if pol else None,
        "owner": {"name": cust.name, "phone": cust.phone, "email": cust.email} if cust else None,
        "claims": [claim_summary(c, session) for c in claims],
    }


@router.get("/repair-shops")
def repair_shops_near(
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    claim_id: Optional[int] = None,
    radius_km: float = Query(50.0, le=5000),
    in_network_only: bool = False,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Repair shops within radius_km of a point.
    Provide lat/lng directly, OR a claim_id to use that claim's incident location.
    Returns shops sorted by distance. (Phase 2: Azure Maps geocoding + spatial index.)
    """
    if claim_id is not None:
        claim = session.get(Claim, claim_id)
        if not claim or not can_view_claim(claim, user, session):
            raise HTTPException(404, "Claim not found")
        lat, lng = claim.incident_lat, claim.incident_lng

    if lat is None or lng is None:
        raise HTTPException(400, "Provide lat & lng, or a claim_id")

    shops = session.exec(select(RepairShop)).all()
    results = []
    for sh in shops:
        if in_network_only and not sh.in_network:
            continue
        dist = haversine_km(lat, lng, sh.lat, sh.lng)
        if dist <= radius_km:
            results.append({
                "id": sh.id, "name": sh.name,
                "address": f"{sh.address}, {sh.city}, {sh.state} {sh.zip}",
                "in_network": sh.in_network, "rating": sh.rating,
                "specialties": sh.specialties,
                "distance_km": round(dist, 1),
            })
    results.sort(key=lambda r: r["distance_km"])
    return {"origin": {"lat": lat, "lng": lng}, "radius_km": radius_km,
            "count": len(results), "shops": results}
