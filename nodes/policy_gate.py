from state import ClaimState

# In production: replace with a real DB/API call to your policy system.
# This simulates what would come back from your policy database when
# a customer logs in — vehicle details are already on file.
POLICY_DB = {
    "POL-001": {
        "active": True,
        "holder": "John Smith",
        "vehicle_make": "Honda",
        "vehicle_model": "Civic",
        "vehicle_year": "2021",
        "coverage_type": "Comprehensive",
        "deductible": 500.0,
    },
    "POL-002": {
        "active": True,
        "holder": "Sarah Johnson",
        "vehicle_make": "Toyota",
        "vehicle_model": "Camry",
        "vehicle_year": "2019",
        "coverage_type": "Collision",
        "deductible": 1000.0,
    },
    "POL-003": {
        "active": True,
        "holder": "Mike Davis",
        "vehicle_make": "BMW",
        "vehicle_model": "3 Series",
        "vehicle_year": "2022",
        "coverage_type": "Comprehensive",
        "deductible": 250.0,
    },
}


def policy_gate(state: ClaimState) -> dict:
    """
    Gate node — runs before any LLM calls.
    1. Checks that the policy_id exists and is active.
    2. Loads vehicle details from the policy record into state.
       (Customer already has vehicle on file — we don't ask them to type it.)
    If invalid: sets decision=fail immediately, no GPT-4o calls made.
    """
    print("\n[GATE] policy_gate running...")

    policy_id = state.get("policy_id", "")

    # TODO: replace with real DB/API call
    # e.g. response = requests.get(f"{POLICY_API}/policies/{policy_id}")
    policy = POLICY_DB.get(policy_id)

    if not policy or not policy.get("active"):
        print(f"  policy {policy_id} not found — failing immediately")
        return {
            "decision": "fail",
            "decision_reason": f"Policy {policy_id} does not exist or is not active."
        }

    print(f"  policy {policy_id} valid — holder: {policy['holder']}")
    print(f"  vehicle on file: {policy['vehicle_year']} {policy['vehicle_make']} {policy['vehicle_model']}")

    return {
        "vehicle_make":  policy["vehicle_make"],
        "vehicle_model": policy["vehicle_model"],
        "vehicle_year":  policy["vehicle_year"],
        "deductible":    policy["deductible"],
    }
