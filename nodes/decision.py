from state import ClaimState

def decision_node(state: ClaimState) -> dict:
    print("\n[NODE 6] decision_node running...")

    decision       = state.get("decision", "need_more_info")
    reason         = state.get("decision_reason", "")
    estimated_cost = state.get("estimated_cost") or 0
    deductible     = state.get("deductible") or 0
    approved_amount = state.get("approved_amount") or 0
    vehicle        = f"{state.get('vehicle_year','')} {state.get('vehicle_make','')} {state.get('vehicle_model','')}".strip()
    severity       = state.get("damage_severity", "unknown")

    print(f"  final decision: {decision}")

    if decision == "pass":
        final_answer = (
            f"✅ Claim APPROVED\n\n"
            f"Vehicle:          {vehicle}\n"
            f"Damage severity:  {severity.capitalize()}\n"
            f"Repair estimate:  ${estimated_cost:,.0f}\n"
            f"Your deductible:  ${deductible:,.0f}\n"
            f"──────────────────────────\n"
            f"HomeSite pays:    ${approved_amount:,.0f}\n\n"
            f"{reason}"
        )

    elif decision == "fail":
        final_answer = (
            f"❌ Claim DENIED\n\n"
            f"Vehicle:          {vehicle}\n"
            f"Damage severity:  {severity.capitalize()}\n"
            f"Repair estimate:  ${estimated_cost:,.0f}\n"
            f"Your deductible:  ${deductible:,.0f}\n\n"
            f"Reason: {reason}"
        )

    elif decision == "flag_fraud":
        final_answer = (
            f"⚑ Claim FLAGGED — Special Investigation Unit\n\n"
            f"Vehicle:  {vehicle}\n"
            f"Reason:   {reason}"
        )

    elif decision == "need_more_info":
        final_answer = (
            f"📋 Additional information required\n\n"
            f"We need more details to process your claim.\n"
            f"Reason: {reason}"
        )

    else:
        final_answer = "Unable to determine claim decision. Please contact support."

    print(f"  final answer:\n{final_answer}")

    return {"final_answer": final_answer}
