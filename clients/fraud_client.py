"""
Fraud detection helpers for Node 3.

L1 — file metadata check (pymediainfo / Pillow EXIF)
L2 — REMOVED
L3 — REMOVED (GPT-4o consistency check removed to reduce LLM calls)
L4 — cross-reference prior claims (stub — replace with real DB call)
"""
import os


# ── L1: Metadata / EXIF ──────────────────────────────────────────────────────

def check_metadata(file_path: str) -> dict:
    """
    Extracts file metadata to detect tampering signals.
    Supports video files (via pymediainfo) and images (via Pillow).
    """
    if not file_path or not os.path.exists(file_path):
        return {"score": 0.0, "flags": [], "detail": "no file provided"}

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext in (".mp4", ".mov", ".avi", ".mkv"):
            score, flags = _check_video_metadata(file_path)
        elif ext in (".jpg", ".jpeg", ".png", ".heic"):
            score, flags = _check_image_metadata(file_path)
        else:
            return {"score": 0.0, "flags": [], "detail": f"unsupported type {ext}"}
    except Exception as e:
        return {"score": 0.0, "flags": [f"metadata read error: {e}"], "detail": str(e)}

    return {"score": score, "flags": flags, "detail": f"{len(flags)} metadata flags"}


def _check_video_metadata(file_path: str) -> tuple:
    try:
        from pymediainfo import MediaInfo
    except ImportError:
        return 0.0, ["pymediainfo not installed — skipped"]

    info = MediaInfo.parse(file_path)
    flags = []
    score = 0.0

    for track in info.tracks:
        if track.track_type == "General":
            if not track.encoded_date and not track.file_last_modification_date:
                flags.append("no creation date in metadata")
                score += 0.2
            if track.file_size and int(track.file_size) < 10_000:
                flags.append("suspiciously small file size")
                score += 0.3

        if track.track_type == "Video":
            fps = getattr(track, "frame_rate", None)
            if fps and float(fps) < 5:
                flags.append(f"unusually low frame rate: {fps} fps")
                score += 0.2

    return min(score, 1.0), flags


def _check_image_metadata(file_path: str) -> tuple:
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
    except ImportError:
        return 0.0, ["Pillow not installed — skipped"]

    flags = []
    score = 0.0

    img = Image.open(file_path)
    exif_data = img._getexif() if hasattr(img, "_getexif") else None

    if not exif_data:
        flags.append("no EXIF data found")
        score += 0.15
    else:
        exif = {TAGS.get(k, k): v for k, v in exif_data.items()}
        if "DateTimeOriginal" not in exif and "DateTime" not in exif:
            flags.append("no capture timestamp in EXIF")
            score += 0.2
        if "Make" not in exif and "Model" not in exif:
            flags.append("no camera make/model in EXIF")
            score += 0.1
        if "Software" in exif:
            software = str(exif["Software"]).lower()
            for kw in ["photoshop", "gimp", "lightroom", "snapseed", "facetune"]:
                if kw in software:
                    flags.append(f"edited with {exif['Software']}")
                    score += 0.4
                    break

    return min(score, 1.0), flags


# ── L4: Prior Claims Cross-Reference ─────────────────────────────────────────

def check_prior_claims(claim_id: str, policy_id: str) -> dict:
    """
    Checks claims history database for suspicious patterns.
    Replace with a real API call to your claims DB.

    Signals to check:
    - Multiple claims on same policy within short window
    - Escalating claim amounts
    - Same location or third-party repeating across claims
    """
    # TODO: replace with real DB/API call
    # Example: response = requests.get(f"{CLAIMS_DB_URL}/history/{policy_id}")
    print(f"  [L4 Prior Claims] checking history for policy {policy_id} (stub)")
    return {
        "score": 0.0,
        "prior_claim_count": 0,
        "flags": [],
        "detail": "no prior claims found (stub)"
    }


# ── Aggregate ─────────────────────────────────────────────────────────────────

def run_fraud_checks(
    file_path: str,
    claim_id: str,
    policy_id: str,
) -> dict:
    """
    Runs L1 (metadata) + L4 (prior claims).
    Both are free — no LLM calls.
    Returns fraud_risk_score, risk_level, and per-level breakdown.
    """
    print("  [L1] checking file metadata...")
    l1 = check_metadata(file_path)

    print("  [L4] cross-referencing prior claims...")
    l4 = check_prior_claims(claim_id, policy_id)

    # L1=50%, L4=50% (equal weight — both are hard signals)
    fraud_risk_score = round(
        0.50 * l1["score"] +
        0.50 * l4["score"],
        3
    )

    if fraud_risk_score >= 0.7:
        risk_level = "HIGH"
    elif fraud_risk_score >= 0.3:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "fraud_risk_score": fraud_risk_score,
        "risk_level": risk_level,
        "breakdown": {
            "L1_metadata": l1,
            "L4_prior_claims": l4
        }
    }
