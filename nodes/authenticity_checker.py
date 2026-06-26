from state import ClaimState
from clients.fraud_client import run_fraud_checks


def authenticity_checker(state: ClaimState) -> dict:
    """
    Node 3: fraud detection — L1 metadata + L4 prior claims.
    Both checks are free (no LLM calls).
    If risk is HIGH, the rules_evaluator will short-circuit to flag_fraud
    before any GPT-4o calls are made.
    """
    print("\n[NODE 3] authenticity_checker running...")

    result = run_fraud_checks(
        file_path=state.get("video_path", ""),
        claim_id=state["claim_id"],
        policy_id=state["policy_id"],
    )

    print(f"  fraud risk score: {result['fraud_risk_score']}")
    print(f"  risk level: {result['risk_level']}")
    bd = result["breakdown"]
    if bd["L1_metadata"]["flags"]:
        print(f"  L1 metadata flags: {bd['L1_metadata']['flags']}")
    if bd["L4_prior_claims"]["flags"]:
        print(f"  L4 prior claims flags: {bd['L4_prior_claims']['flags']}")

    return {
        "fraud_risk_score": result["fraud_risk_score"],
        "fraud_breakdown": result["breakdown"],
        "risk_level": result["risk_level"]
    }
