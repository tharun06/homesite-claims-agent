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
        statuses = list(ClaimStatus)
        for _ in range(N_CLAIMS):
            v = random.choice(vehicles)
            pol = next(p for p in policies if p.id == v.policy_id)
            cust = next(c for c in customers if c.id == pol.customer_id)
            adjuster = random.choice(adjusters)

            status = random.choices(
                statuses,
                weights=[10, 18, 14, 12, 15, 12, 6, 14, 4],  # PENDING_APPROVAL increased to 15
            )[0]
            fraud_score = round(random.uniform(0, 1), 2)
            fraud_flagged = status == ClaimStatus.SIU_FLAGGED or fraud_score >= 0.75
            if fraud_flagged and status != ClaimStatus.SIU_FLAGGED and random.random() > 0.5:
                status = ClaimStatus.SIU_FLAGGED

            loss = fake.date_between(start_date="-90d", end_date="today")
            reported = loss + timedelta(days=random.randint(0, 5))
            est = round(random.uniform(300, 14000), 2)
            lat, lng, city, _ = nc_location()

            claim = Claim(
                claim_number=f"CLM-{fake.unique.random_number(digits=6, fix_len=True)}",
                policy_id=pol.id, vehicle_id=v.id, adjuster_id=adjuster.id,
                status=status, peril_type=random.choice(list(PerilType)),
                description=random.choice(CUSTOMER_LINES),
                loss_date=loss, reported_date=reported,
                incident_city=city, incident_state="NC",
                incident_lat=lat, incident_lng=lng,
                estimated_amount=est,
                reserve_amount=round(est * random.uniform(0.8, 1.1), 2),
                approved_amount=(round(max(0, est - pol.deductible), 2)
                                 if status == ClaimStatus.APPROVED else None),
                deductible=pol.deductible,
                fraud_score=fraud_score, fraud_flagged=fraud_flagged,
                sla_due_date=datetime.utcnow() + timedelta(days=random.randint(-3, 14)),
                created_at=datetime.combine(reported, datetime.min.time()),
                updated_at=datetime.utcnow() - timedelta(hours=random.randint(0, 72)),
            )
            s.add(claim); s.commit(); s.refresh(claim)

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
