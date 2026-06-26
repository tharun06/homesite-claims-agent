from state import ClaimState

def rules_evaluator(state: ClaimState) -> dict:
    """
    Node 4: evaluates all collected info against policy rules.
    Currently uses simple hardcoded logic. Replace with a more
    sophisticated LLM-based evaluator when ready.
    """
    print("\n[NODE 5] rules_evaluator running...")

    # simple hardcoded rules for demonstration

    fraud_score = state.get("fraud_risk_score", 0)
    coverage = state.get("coverage_summary", "").lower()
    risk_level = state.get("risk_level", "LOW")

    print(f"  fraud score: {fraud_score} ({risk_level})")
    print(f"  coverage summary: {coverage[:80]}...")


    if fraud_score >= 0.7:
        return {
            "decision": "flag_fraud",
            "decision_reason": (
                f"Fraud risk score {fraud_score} exceeds threshold 0.7. "
                f"Routing to Special Investigation Unit."
            )
        }

    # medium risk overrides coverage — ask for more info before approving
    if fraud_score >= 0.3 or risk_level == "MEDIUM":
        return {
            "decision": "need_more_info",
            "decision_reason": (
                f"Fraud risk score {fraud_score} is moderate and/or risk level is {risk_level}. "
                f"Asking for more information to make a decision."
            )
        }

    if "not covered" in coverage:
        return {
            "decision": "fail",
            "decision_reason": "Policy coverage summary indicates claim is not covered."
        }

    if "covered" in coverage:
        estimated_cost = state.get("estimated_cost") or 0
        deductible     = state.get("deductible") or 0
        payout         = max(0.0, estimated_cost - deductible)

        print(f"  estimated cost: ${estimated_cost}  deductible: ${deductible}  payout: ${payout}")

        if payout == 0:
            return {
                "decision": "fail",
                "decision_reason": (
                    f"Damage estimate (${estimated_cost:.0f}) does not exceed "
                    f"your deductible (${deductible:.0f}). No payout issued."
                ),
                "approved_amount": 0.0
            }

        return {
            "decision": "pass",
            "decision_reason": "Claim is covered under the policy.",
            "approved_amount": payout
        }

    return {
        "decision": "need_more_info",
        "decision_reason": "Insufficient information to make a decision."
    }