def mock_assess_damage(frames: list) -> dict:
    """
    MOCK — pretends to be a vision AI model assessing damage.
    In production this would call Azure OpenAI vision (gpt-4o with images)
    and return a real damage assessment from the actual frames.
    """
    print(f"  [MOCK vision] assessing {len(frames)} frames")

    return {
        "damage_description": (
            "Front bumper is cracked and partially detached. "
            "Left headlight housing is broken. "
            "Hood has a minor dent near the left corner."
        ),
        "damage_severity": "moderate",
        "estimated_cost": 2400.00
    }