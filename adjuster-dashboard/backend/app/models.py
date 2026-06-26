"""
Data model for the adjuster dashboard.

Designed API-first: every entity is a typed SQLModel so the same schema
serves the REST API now and the MCP tools (Phase 2 Copilot) later.
Geo fields (lat/lng) are present from day one so geo-proximity is a
data-availability problem, not a schema migration.
"""
from datetime import datetime, date
from enum import Enum
from typing import Optional

from sqlmodel import SQLModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────

class Role(str, Enum):
    ADJUSTER         = "adjuster"
    SENIOR_ADJUSTER  = "senior_adjuster"   # team lead — sees whole team
    SIU_INVESTIGATOR = "siu_investigator"  # sees fraud-flagged queue
    ADMIN            = "admin"             # full visibility + metrics


class ClaimStatus(str, Enum):
    FNOL             = "FNOL"               # first notice of loss
    UNDER_REVIEW     = "Under Review"
    INVESTIGATION    = "Investigation"
    APPRAISAL        = "Appraisal"
    PENDING_APPROVAL = "Pending Approval"
    APPROVED         = "Approved"
    DENIED           = "Denied"
    CLOSED           = "Closed"
    SIU_FLAGGED      = "SIU Flagged"


class PerilType(str, Enum):
    COLLISION     = "Collision"
    COMPREHENSIVE = "Comprehensive"
    THEFT         = "Theft"
    VANDALISM     = "Vandalism"
    WEATHER       = "Weather"
    GLASS         = "Glass"


class TaskStatus(str, Enum):
    PENDING     = "Pending"
    IN_PROGRESS = "In Progress"
    DONE        = "Done"
    OVERDUE     = "Overdue"


class SenderType(str, Enum):
    CUSTOMER = "customer"
    ADJUSTER = "adjuster"
    SYSTEM   = "system"


# ── Tables ───────────────────────────────────────────────────────────────────

class Team(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    region: str


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str = Field(index=True)
    role: Role
    team_id: Optional[int] = Field(default=None, foreign_key="team.id")
    active: bool = True


class Customer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str
    phone: str
    address: str
    city: str
    state: str
    zip: str
    lat: float
    lng: float


class Policy(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    policy_number: str = Field(index=True)
    customer_id: int = Field(foreign_key="customer.id")
    coverage_type: str            # Comprehensive / Collision / Liability
    deductible: float
    premium: float
    in_force: bool = True
    effective_date: date
    expiry_date: date


class Vehicle(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    vin: str = Field(index=True)
    make: str
    model: str
    year: int
    color: str
    policy_id: int = Field(foreign_key="policy.id")


class Claim(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    claim_number: str = Field(index=True)
    policy_id: int = Field(foreign_key="policy.id")
    vehicle_id: int = Field(foreign_key="vehicle.id")
    adjuster_id: int = Field(foreign_key="user.id", index=True)

    status: ClaimStatus = Field(index=True)
    peril_type: PerilType
    description: str

    loss_date: date
    reported_date: date

    # geo of the incident — powers Phase 2 "repairs/claims near X"
    incident_city: str
    incident_state: str
    incident_lat: float
    incident_lng: float

    estimated_amount: float = 0.0
    reserve_amount: float = 0.0
    approved_amount: Optional[float] = None
    deductible: float = 0.0

    fraud_score: float = 0.0
    fraud_flagged: bool = Field(default=False, index=True)

    sla_due_date: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ClaimTask(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    claim_id: int = Field(foreign_key="claim.id", index=True)
    assigned_to: int = Field(foreign_key="user.id", index=True)
    task_type: str                # "Request documents", "Schedule appraisal"...
    description: str
    status: TaskStatus = Field(default=TaskStatus.PENDING, index=True)
    due_date: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Conversation(SQLModel, table=True):
    """One message in a claim's thread. Source distinguishes voice→text."""
    id: Optional[int] = Field(default=None, primary_key=True)
    claim_id: int = Field(foreign_key="claim.id", index=True)
    sender_type: SenderType
    sender_name: str
    channel: str                  # phone / chat / email
    source: str                   # "voice_transcript" or "typed"
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)


class ClaimEvent(SQLModel, table=True):
    """Timeline / audit trail — also what realtime pushes to the dashboard."""
    id: Optional[int] = Field(default=None, primary_key=True)
    claim_id: int = Field(foreign_key="claim.id", index=True)
    event_type: str               # status_change / assignment / note / message
    detail: str
    actor: str
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)


class RepairShop(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    address: str
    city: str
    state: str
    zip: str
    lat: float
    lng: float
    in_network: bool = True
    rating: float = 4.0
    specialties: str = ""         # comma-separated: "Body, Glass, Paint"
