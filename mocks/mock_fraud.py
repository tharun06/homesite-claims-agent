def mock_check_fraud(claim_id: str, transcript: str, ocr_text: str) -> dict:
    """
    MOCK — pretends to run the 4-layer fraud detection system.
    In production each level (L1-L4) would call real services:
    L1: metadata/EXIF forensics
    L2: AI-generation/deepfake detection model
    L3: LLM consistency check
    L4: database cross-reference via API
    """
    print(f"  [MOCK fraud] running L1-L4 checks for: {claim_id}")

    return {
        "fraud_risk_score": 0.2,            # med risk
        "risk_level": "LOW",
        "recommendation": "proceed_to_coverage_check",
        "breakdown": {
            "L1_metadata": {
                "score": 0.1,
                "flag": False,
                "reason": "Timestamp and GPS match the claimed date and location"
            },
            "L2_ai_generated": {
                "score": 0.2,
                "flag": False,
                "reason": "No synthetic image artifacts detected"
            },
            "L3_consistency": {
                "score": 0.3,
                "flag": False,
                "reason": "Damage description matches the claimant story"
            },
            "L4_cross_reference": {
                "score": 0.4,
                "flag": False,
                "reason": "No prior claims found for this vehicle"
            }
        }
    }