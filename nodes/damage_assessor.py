from state import ClaimState
from clients.openai_client import assess_damage_from_images
from clients.search_client import search_repair_costs


def damage_assessor(state: ClaimState) -> dict:
    """
    Node 2: assesses damage severity and estimates repair cost.
    1. Searches Azure AI Search for repair cost ranges for this vehicle type.
    2. Sends keyframe images + vehicle info + cost reference to GPT-4o vision.
    """
    print("\n[NODE 2] damage_assessor running...")

    vehicle_make  = state.get("vehicle_make") or "unknown"
    vehicle_model = state.get("vehicle_model") or "unknown"
    vehicle_year  = state.get("vehicle_year") or "unknown"
    vehicle_str   = f"{vehicle_year} {vehicle_make} {vehicle_model}"

    # step 1: pull relevant cost ranges from repair cost guide (free RAG call)
    cost_query = f"{vehicle_make} {vehicle_model} repair cost bumper hood door damage"
    cost_chunks = search_repair_costs(cost_query, top_k=3)
    cost_reference = "\n\n".join(cost_chunks) if cost_chunks else "No repair cost reference available."

    print(f"  vehicle: {vehicle_str}")
    print(f"  repair cost reference chunks: {len(cost_chunks)}")

    # step 2: send images + vehicle + cost reference to GPT-4o vision
    result = assess_damage_from_images(
        frame_paths=state["frames"],
        vehicle_info=vehicle_str,
        cost_reference=cost_reference
    )

    # if GPT-4o sees a different vehicle than what's on the policy → flag fraud
    if result.get("vehicle_mismatch"):
        print(f"  ⚠️  vehicle mismatch detected — images do not match policy vehicle")
        return {
            "damage_description": result.get("damage_description", ""),
            "damage_severity":    "severe",
            "estimated_cost":     0,
            "fraud_risk_score":   0.9,
            "risk_level":         "HIGH",
        }

    print(f"  damage severity: {result['damage_severity']}")
    print(f"  estimated cost: ${result['estimated_cost']}")

    return {
        "damage_description": result["damage_description"],
        "damage_severity":    result["damage_severity"],
        "estimated_cost":     result["estimated_cost"]
    }
