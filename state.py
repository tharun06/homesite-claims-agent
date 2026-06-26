from typing import TypedDict, Optional


class ClaimState(TypedDict):
    """
    The shared claim folder that flows through every node.
    Each node reads from it and writes its results back.
    Fields start as None and get filled as the claim progresses.
    """

    # ── input fields (set at intake, required) ──
    claim_id: str                    # unique ID e.g. "CLM-001"
    policy_id: str                   # which policy e.g. "POL-001"
    video_path: str                  # local path to the uploaded file
    media_type: Optional[str]        # "video" or "photo"
    claimant_description: str        # what the user said happened

    # ── vehicle + policy info (loaded by policy_gate) ──
    vehicle_make: Optional[str]      # e.g. "Honda"
    vehicle_model: Optional[str]     # e.g. "Civic"
    vehicle_year: Optional[str]      # e.g. "2021"
    deductible: Optional[float]      # e.g. 500.0
    approved_amount: Optional[float] # payout = estimated_cost - deductible

    # ── filled by video_processor (MOCK) ──
    transcript: Optional[str]        # what was said in the video
    ocr_text: Optional[str]          # text seen on screen (plates, signs)
    frames: Optional[list]           # list of extracted frame filenames

    # ── filled by damage_assessor ──
    damage_description: Optional[str]   # what's damaged
    damage_severity: Optional[str]      # minor / moderate / severe
    estimated_cost: Optional[float]     # rough repair estimate

    # ── filled by authenticity_checker (MOCK) ──
    fraud_risk_score: Optional[float]   # 0.0 (safe) to 1.0 (high risk)
    fraud_breakdown: Optional[dict]     # per-level L1-L4 details
    risk_level: Optional[str]           # LOW / MEDIUM / HIGH

    # ── filled by retrieval_engine (REAL Azure) ──
    policy_clauses: Optional[list]      # retrieved policy text chunks
    coverage_summary: Optional[str]     # grounded LLM answer

    # ── filled by rules_evaluator + decision ──
    decision: Optional[str]             # pass/fail/need_more_info/flag_fraud
    decision_reason: Optional[str]      # why this decision was made

    # ── loop control + final output ──
    info_requests: Optional[int]        # how many times we asked for more info
    final_answer: Optional[str]         # message sent back to the user