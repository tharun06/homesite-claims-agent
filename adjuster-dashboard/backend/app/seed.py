"""
Seed the dashboard database with a coherent, realistic book of business.

Run:  python -m app.seed
Re-run anytime — it wipes and regenerates. Change the VOLUME constants
to scale up/down. Everything is internally consistent: every claim points
to a real vehicle, policy, customer, adjuster, and team, with a threaded
conversation, tasks, and a timeline.
"""
import random
from datetime import datetime, timedelta, date

from faker import Faker
from sqlmodel import Session, select

from app.database import engine, init_db
from app.models import (
    Team, User, Customer, Policy, Vehicle, Claim, ClaimTask,
    Conversation, ClaimEvent, RepairShop,
    Role, ClaimStatus, PerilType, TaskStatus, SenderType,
)

fake = Faker("en_US")
Faker.seed(42)
random.seed(42)

# North Carolina coordinates and cities
NC_CITIES = ["Charlotte", "Raleigh", "Greensboro", "Durham", "Winston-Salem",
             "Fayetteville", "Cary", "Wilmington", "High Point", "Greenville",
             "Asheville", "Chapel Hill", "Clemmons", "Wake Forest", "Apex"]
NC_ZIPS = ["28202", "28205", "28209", "27601", "27603", "27605", "27607",
           "27403", "27405", "27406", "27408", "28401", "28405", "28409"]
NC_LAT_MIN, NC_LAT_MAX = 33.8, 36.6
NC_LNG_MIN, NC_LNG_MAX = -84.3, -75.4

# ── Volumes ("good amount of data") ──────────────────────────────────────────
N_TEAMS      = 5
N_ADJUSTERS  = 15
N_SENIORS    = 5     # one lead per team
N_SIU        = 3
N_ADMIN      = 2
N_CUSTOMERS  = 200
N_CLAIMS     = 400
N_SHOPS      = 40

def nc_location():
    """Generate a random NC location."""
    lat = round(random.uniform(NC_LAT_MIN, NC_LAT_MAX), 4)
    lng = round(random.uniform(NC_LNG_MIN, NC_LNG_MAX), 4)
    city = random.choice(NC_CITIES)
    zip_code = random.choice(NC_ZIPS)
    return lat, lng, city, zip_code

MAKES = {
    "Honda": ["Civic", "Accord", "CR-V", "Pilot"],
    "Toyota": ["Camry", "Corolla", "RAV4", "Highlander"],
    "Ford": ["F-150", "Escape", "Explorer", "Mustang"],
    "Chevrolet": ["Silverado", "Equinox", "Malibu", "Tahoe"],
    "BMW": ["3 Series", "5 Series", "X3", "X5"],
    "Tesla": ["Model 3", "Model Y", "Model S"],
}
COLORS = ["White", "Black", "Silver", "Gray", "Blue", "Red"]
TASK_TYPES = [
    "Request police report", "Schedule appraisal", "Request repair estimate",
    "Verify coverage", "Contact claimant", "Review photos", "Order parts quote",
    "Confirm deductible", "Follow up on documents",
]
SHOP_SPECIALTIES = ["Body", "Glass", "Paint", "Frame", "Mechanical", "Detailing"]

VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"  # no I, O, Q

# Customer voice/chat lines — realistic FNOL conversation snippets
CUSTOMER_LINES = [
    "Hi, I need to report an accident that happened this morning.",
    "Another car hit me from behind at a red light.",
    "There's a big dent on the driver side door and the window is cracked.",
    "I have photos of the damage, where should I send them?",
    "The other driver gave me their insurance information.",
    "My car is not drivable, can I get a rental?",
    "When will the appraiser be able to look at my vehicle?",
    "I already took it to the body shop on Main Street.",
    "Do I need to file a police report for this?",
    "How much is my deductible for this claim?",
    "The bumper is hanging off and the headlight is broken.",
    "It happened in the parking lot, no other car was involved.",
    "Someone keyed my car overnight, the whole side is scratched.",
    "A tree branch fell on my hood during the storm.",
]
ADJUSTER_LINES = [
    "Thanks for reaching out, I've opened a claim for you.",
    "Could you upload the photos through the customer portal?",
    "I've scheduled an appraisal for your vehicle.",
    "Your deductible for this claim is on file, I'll confirm the amount.",
    "We'll need the police report before we can proceed.",
    "I'm reviewing the damage estimate now.",
    "Your rental car has been approved under the policy.",
    "I've requested a repair estimate from the network shop.",
    "The claim is now under review, I'll update you shortly.",
    "We've received your documents, thank you.",
]
SYSTEM_LINES = [
    "Claim status changed.",
    "Documents received and attached to claim.",
    "Appraisal appointment confirmed.",
    "Voice call transcribed and added to thread.",
]

# ── Realistic distribution knobs ─────────────────────────────────────────────
SLA_DAYS = 30                # working window before a claim breaches SLA
BASE_FRAUD_RATE = 0.06       # ~6% baseline, then multiplied by region + peril signal

# some perils are simply more common than others
PERIL_WEIGHTS = {
    PerilType.COLLISION: 30, PerilType.COMPREHENSIVE: 22, PerilType.GLASS: 16,
    PerilType.WEATHER: 14, PerilType.VANDALISM: 10, PerilType.THEFT: 8,
}
# typical repair / loss cost band per peril (USD)
PERIL_AMOUNT = {
    PerilType.GLASS: (200, 900),      PerilType.VANDALISM: (500, 4000),
    PerilType.THEFT: (3000, 26000),   PerilType.WEATHER: (800, 9000),
    PerilType.COLLISION: (1500, 20000), PerilType.COMPREHENSIVE: (1000, 15000),
}
# deliberate fraud SIGNAL so "fraud by team / by peril" tells a real story
REGION_FRAUD_MULT = {"West": 1.9, "Southwest": 1.3, "Midwest": 1.0,
                     "Southeast": 0.9, "Northeast": 0.7}
PERIL_FRAUD_MULT = {
    PerilType.THEFT: 2.6, PerilType.VANDALISM: 1.8, PerilType.COLLISION: 1.0,
    PerilType.COMPREHENSIVE: 1.0, PerilType.WEATHER: 0.6, PerilType.GLASS: 0.4,
}


def pick_status(age_days: int, fraud_flagged: bool) -> ClaimStatus:
    """Status correlates with claim age (a real lifecycle funnel) and fraud,
    so a queue snapshot looks believable: recent claims are in-flight, older
    ones are resolved, fraud claims sit in investigation / SIU."""
    S = ClaimStatus
    if fraud_flagged:
        if age_days < 30:
            return random.choices([S.SIU_FLAGGED, S.INVESTIGATION], [0.7, 0.3])[0]
        return random.choices([S.SIU_FLAGGED, S.DENIED, S.INVESTIGATION], [0.5, 0.3, 0.2])[0]
    if age_days < 7:
        return random.choices([S.FNOL, S.UNDER_REVIEW], [0.6, 0.4])[0]
    if age_days < 21:
        return random.choices([S.UNDER_REVIEW, S.INVESTIGATION, S.APPRAISAL], [0.4, 0.3, 0.3])[0]
    if age_days < 60:
        return random.choices([S.APPRAISAL, S.PENDING_APPROVAL, S.APPROVED, S.UNDER_REVIEW],
                              [0.30, 0.30, 0.25, 0.15])[0]
    if age_days < 150:
        return random.choices([S.APPROVED, S.PENDING_APPROVAL, S.CLOSED, S.DENIED],
                              [0.45, 0.15, 0.30, 0.10])[0]
    return random.choices([S.CLOSED, S.APPROVED, S.DENIED], [0.55, 0.35, 0.10])[0]


def vin():
    return "".join(random.choice(VIN_CHARS) for _ in range(17))


def wipe():
    """Drop and recreate all tables for a clean re-seed."""
    from sqlmodel import SQLModel
    SQLModel.metadata.drop_all(engine)
    init_db()


def seed():
    wipe()
    with Session(engine) as s:
        # ── Teams ──
        teams = []
        regions = ["Northeast", "Southeast", "Midwest", "West", "Southwest"]
        for i in range(N_TEAMS):
            t = Team(name=f"Team {regions[i]}", region=regions[i])
            s.add(t); teams.append(t)
        s.commit()
        for t in teams: s.refresh(t)

        # ── Users ──
        adjusters, seniors, siu, admins = [], [], [], []
        for i in range(N_ADJUSTERS):
            u = User(name=fake.name(), email=fake.unique.email(),
                     role=Role.ADJUSTER, team_id=teams[i % N_TEAMS].id)
            s.add(u); adjusters.append(u)
        for i in range(N_SENIORS):
            u = User(name=fake.name(), email=fake.unique.email(),
                     role=Role.SENIOR_ADJUSTER, team_id=teams[i].id)
            s.add(u); seniors.append(u)
        for i in range(N_SIU):
            u = User(name=fake.name(), email=fake.unique.email(),
                     role=Role.SIU_INVESTIGATOR, team_id=None)
            s.add(u); siu.append(u)
        for i in range(N_ADMIN):
            u = User(name=fake.name(), email=fake.unique.email(),
                     role=Role.ADMIN, team_id=None)
            s.add(u); admins.append(u)
        s.commit()
        for u in adjusters + seniors + siu + admins: s.refresh(u)

        # ── Customers (with geo — North Carolina only) ──
        customers = []
        for _ in range(N_CUSTOMERS):
            lat, lng, city, zip_code = nc_location()
            c = Customer(
                name=fake.name(), email=fake.unique.email(), phone=fake.phone_number(),
                address=fake.street_address(), city=city,
                state="NC", zip=zip_code,
                lat=lat, lng=lng,
            )
            s.add(c); customers.append(c)
        s.commit()
        for c in customers: s.refresh(c)

        # ── Policies + Vehicles (1 policy, 1 vehicle per customer here) ──
        policies, vehicles = [], []
        for c in customers:
            eff = fake.date_between(start_date="-2y", end_date="-1y")
            pol = Policy(
                policy_number=f"POL-{fake.unique.random_number(digits=6, fix_len=True)}",
                customer_id=c.id,
                coverage_type=random.choice(["Comprehensive", "Collision", "Comprehensive"]),
                deductible=random.choice([250, 500, 500, 1000]),
                premium=round(random.uniform(800, 2200), 2),
                in_force=random.random() > 0.05,
                effective_date=eff,
                expiry_date=eff + timedelta(days=365),
            )
            s.add(pol); policies.append(pol)
        s.commit()
        for p in policies: s.refresh(p)

        for p in policies:
            make = random.choice(list(MAKES.keys()))
            v = Vehicle(
                vin=vin(), make=make, model=random.choice(MAKES[make]),
                year=random.randint(2015, 2024), color=random.choice(COLORS),
                policy_id=p.id,
            )
            s.add(v); vehicles.append(v)
        s.commit()
        for v in vehicles: s.refresh(v)

        # ── Repair shops (with geo — North Carolina only) ──
        for _ in range(N_SHOPS):
            lat, lng, city, zip_code = nc_location()
            shop = RepairShop(
                name=f"{fake.last_name()} Auto Body",
                address=fake.street_address(),
                city=city, state="NC", zip=zip_code,
                lat=lat, lng=lng,
                in_network=random.random() > 0.3,
                rating=round(random.uniform(3.2, 5.0), 1),
                specialties=", ".join(random.sample(SHOP_SPECIALTIES, random.randint(1, 3))),
            )
            s.add(shop)
        s.commit()

        # ── Claims + Tasks + Conversations + Events ──
        team_region = {t.id: t.region for t in teams}
        # mild workload skew so "which adjuster is busiest" is a real answer
        adj_weights = [1.0] * len(adjusters)
        for idx, w in [(0, 2.4), (1, 1.8), (2, 1.5), (7, 1.4)]:
            if idx < len(adj_weights):
                adj_weights[idx] = w
        today = date.today()
        now = datetime.utcnow()

        for i in range(N_CLAIMS):
            v = random.choice(vehicles)
            pol = next(p for p in policies if p.id == v.policy_id)
            cust = next(c for c in customers if c.id == pol.customer_id)
            adjuster = random.choices(adjusters, weights=adj_weights)[0]
            region = team_region.get(adjuster.team_id, "Midwest")

            peril = random.choices(list(PERIL_WEIGHTS), weights=list(PERIL_WEIGHTS.values()))[0]

            # dates spread across the last 12 months
            age_days = random.randint(1, 365)
            loss = today - timedelta(days=age_days)
            reported = loss + timedelta(days=random.randint(0, 4))

            # fraud with a deliberate region + peril signal (min-capped)
            prob = min(0.55, BASE_FRAUD_RATE * REGION_FRAUD_MULT[region] * PERIL_FRAUD_MULT[peril])
            fraud_flagged = random.random() < prob
            fraud_score = (round(random.uniform(0.72, 0.98), 2) if fraud_flagged
                           else round(random.uniform(0.0, 0.5), 2))

            status = pick_status(age_days, fraud_flagged)

            lo, hi = PERIL_AMOUNT[peril]
            est = round(random.uniform(lo, hi), 2)

            resolved = status in (ClaimStatus.CLOSED, ClaimStatus.DENIED)
            reserve = 0.0 if resolved else round(est * random.uniform(0.85, 1.1), 2)
            approved = (round(max(0.0, est - pol.deductible), 2)
                        if status in (ClaimStatus.APPROVED, ClaimStatus.CLOSED) else None)

            reported_dt = datetime.combine(reported, datetime.min.time())
            sla_due = reported_dt + timedelta(days=SLA_DAYS)
            updated = reported_dt + timedelta(days=random.randint(1, max(1, min(age_days, 140))),
                                              hours=random.randint(0, 23))
            if updated > now:
                updated = now - timedelta(hours=random.randint(1, 48))

            lat, lng, city, _ = nc_location()
            claim = Claim(
                claim_number=f"CLM-{fake.unique.random_number(digits=6, fix_len=True)}",
                policy_id=pol.id, vehicle_id=v.id, adjuster_id=adjuster.id,
                status=status, peril_type=peril,
                description=random.choice(CUSTOMER_LINES),
                loss_date=loss, reported_date=reported,
                incident_city=city, incident_state="NC",
                incident_lat=lat, incident_lng=lng,
                estimated_amount=est,
                reserve_amount=reserve,
                approved_amount=approved,
                deductible=pol.deductible,
                fraud_score=fraud_score, fraud_flagged=fraud_flagged,
                sla_due_date=sla_due,
                created_at=reported_dt,
                updated_at=updated,
            )
            # flush (not commit) to get the generated PK: it assigns the id
            # without ending the transaction, so the whole batch commits once
            # below. Committing per claim means ~800 network round trips, which
            # is unnoticeable on local SQLite and painfully slow against a
            # remote Postgres.
            s.add(claim); s.flush()

            # tasks (active claims only)
            if status not in (ClaimStatus.CLOSED, ClaimStatus.DENIED, ClaimStatus.APPROVED):
                for _ in range(random.randint(1, 3)):
                    due = datetime.utcnow() + timedelta(days=random.randint(-2, 7))
                    tstatus = TaskStatus.OVERDUE if due < datetime.utcnow() else random.choice(
                        [TaskStatus.PENDING, TaskStatus.IN_PROGRESS])
                    s.add(ClaimTask(
                        claim_id=claim.id, assigned_to=adjuster.id,
                        task_type=random.choice(TASK_TYPES),
                        description=fake.sentence(nb_words=8),
                        status=tstatus, due_date=due,
                    ))

            # conversation thread (mix of voice + chat)
            n_msgs = random.randint(3, 8)
            ts = datetime.combine(reported, datetime.min.time()) + timedelta(hours=1)
            for i in range(n_msgs):
                if i % 3 == 2:
                    sender_type, name, lines, src, ch = (
                        SenderType.SYSTEM, "System", SYSTEM_LINES, "typed", "system")
                elif i % 2 == 0:
                    sender_type, name, lines, src, ch = (
                        SenderType.CUSTOMER, cust.name, CUSTOMER_LINES,
                        random.choice(["voice_transcript", "typed"]),
                        random.choice(["phone", "chat"]))
                else:
                    sender_type, name, lines, src, ch = (
                        SenderType.ADJUSTER, adjuster.name, ADJUSTER_LINES, "typed", "chat")
                s.add(Conversation(
                    claim_id=claim.id, sender_type=sender_type, sender_name=name,
                    channel=ch, source=src, content=random.choice(lines), timestamp=ts,
                ))
                ts += timedelta(hours=random.randint(1, 12))

            # timeline events
            s.add(ClaimEvent(claim_id=claim.id, event_type="created",
                             detail=f"Claim filed ({claim.peril_type.value})",
                             actor=cust.name,
                             timestamp=datetime.combine(reported, datetime.min.time())))
            s.add(ClaimEvent(claim_id=claim.id, event_type="assignment",
                             detail=f"Assigned to {adjuster.name}",
                             actor="System", timestamp=claim.created_at + timedelta(hours=2)))
            s.add(ClaimEvent(claim_id=claim.id, event_type="status_change",
                             detail=f"Status: {status.value}", actor=adjuster.name,
                             timestamp=claim.updated_at))
            # commit in batches rather than per claim — see the flush() note above
            if (i + 1) % 50 == 0:
                s.commit()
                print(f"   seeded {i + 1}/{N_CLAIMS} claims")
        s.commit()

        # ── Summary ──
        n = lambda model: len(s.exec(select(model)).all())
        print("✅ Seed complete:")
        print(f"   teams         {n(Team)}")
        print(f"   users         {n(User)}  (adjusters/seniors/siu/admin = "
              f"{N_ADJUSTERS}/{N_SENIORS}/{N_SIU}/{N_ADMIN})")
        print(f"   customers     {n(Customer)}")
        print(f"   policies      {n(Policy)}")
        print(f"   vehicles      {n(Vehicle)}")
        print(f"   claims        {n(Claim)}")
        print(f"   tasks         {n(ClaimTask)}")
        print(f"   conversations {n(Conversation)}")
        print(f"   events        {n(ClaimEvent)}")
        print(f"   repair_shops  {n(RepairShop)}")


if __name__ == "__main__":
    seed()
