import os
from dotenv import load_dotenv

load_dotenv()

os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGCHAIN_TRACING_V2", "true")
os.environ["LANGCHAIN_API_KEY"]    = os.getenv("LANGCHAIN_API_KEY", "")
os.environ["LANGCHAIN_PROJECT"]    = os.getenv("LANGCHAIN_PROJECT", "homesite-claims")

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
import uvicorn
import shutil
from workflow import claims_app

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic"}
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

api = FastAPI(title="HomeSite Claims Verification Agent")


def _blank_state(claim_id, policy_id, file_path, media_type, description):
    return {
        "claim_id":              claim_id,
        "policy_id":             policy_id,
        "video_path":            file_path,
        "media_type":            media_type,
        "claimant_description":  description,
        "info_requests":         0,
        "vehicle_make":          None,
        "vehicle_model":         None,
        "vehicle_year":          None,
        "deductible":            None,
        "approved_amount":       None,
        "transcript":            None,
        "ocr_text":              None,
        "frames":                None,
        "damage_description":    None,
        "damage_severity":       None,
        "estimated_cost":        None,
        "fraud_risk_score":      None,
        "fraud_breakdown":       None,
        "risk_level":            None,
        "policy_clauses":        None,
        "coverage_summary":      None,
        "decision":              None,
        "decision_reason":       None,
        "final_answer":          None,
    }


def _response_from_result(claim_id, result, current_state):
    if current_state.next:
        return JSONResponse({
            "claim_id": claim_id,
            "status":   "waiting_for_evidence",
            "message":  (
                "We need more information to process your claim. "
                "Please provide a police report, additional photos, "
                "or repair estimates."
            ),
            "next_step": f"POST /claims/{claim_id}/evidence"
        })
    return JSONResponse({
        "claim_id":     claim_id,
        "status":       "completed",
        "decision":     result.get("decision"),
        "final_answer": result.get("final_answer"),
        "estimated_cost":  result.get("estimated_cost"),
        "deductible":      result.get("deductible"),
        "approved_amount": result.get("approved_amount"),
    })


# ── ENDPOINT 1: Submit a new claim ────────────────────────────────────────────

@api.post("/claims/submit")
async def submit_claim(
    claim_id:    str        = Form(...),
    policy_id:   str        = Form(...),
    description: str        = Form(...),
    file:        UploadFile = File(...)
):
    print(f"\n{'='*60}\nNEW CLAIM: {claim_id}\n{'='*60}")

    # save uploaded file
    ext = os.path.splitext(file.filename)[1].lower()
    save_path = os.path.join(UPLOAD_DIR, f"{claim_id}{ext}")
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    if ext in VIDEO_EXTS:
        media_type = "video"
    elif ext in PHOTO_EXTS:
        media_type = "photo"
    else:
        return JSONResponse({"error": f"Unsupported file type: {ext}"}, status_code=400)

    print(f"  file saved: {save_path}  media_type: {media_type}")

    config = {"configurable": {"thread_id": claim_id}}
    state  = _blank_state(claim_id, policy_id, save_path, media_type, description)
    result = claims_app.invoke(state, config=config)

    return _response_from_result(claim_id, result, claims_app.get_state(config))


# ── ENDPOINT 2: Submit more evidence ──────────────────────────────────────────

@api.post("/claims/{claim_id}/evidence")
async def submit_evidence(
    claim_id:          str = ...,
    extra_description: str = Form(...)
):
    print(f"\n{'='*60}\nNEW EVIDENCE for: {claim_id}\n{'='*60}")

    config = {"configurable": {"thread_id": claim_id}}
    current_state = claims_app.get_state(config)

    if not current_state.values:
        return JSONResponse({"error": f"Claim {claim_id} not found"}, status_code=404)

    claims_app.update_state(config, {"claimant_description": extra_description})
    result = claims_app.invoke(None, config=config)

    return _response_from_result(claim_id, result, claims_app.get_state(config))


# ── ENDPOINT 3: Check status ───────────────────────────────────────────────────

@api.get("/claims/{claim_id}/status")
async def get_claim_status(claim_id: str):
    config = {"configurable": {"thread_id": claim_id}}
    s = claims_app.get_state(config)

    if not s.values:
        return JSONResponse({"error": f"Claim {claim_id} not found"}, status_code=404)

    return JSONResponse({
        "claim_id":       claim_id,
        "status":         "waiting" if s.next else "completed",
        "waiting_at":     list(s.next) if s.next else None,
        "decision":       s.values.get("decision"),
        "info_requests":  s.values.get("info_requests", 0),
        "estimated_cost": s.values.get("estimated_cost"),
        "deductible":     s.values.get("deductible"),
        "approved_amount":s.values.get("approved_amount"),
        "final_answer":   s.values.get("final_answer"),
    })


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        # quick CLI test without starting the server
        from workflow import claims_app as app
        config = {"configurable": {"thread_id": "CLM-CLI-001"}}
        state  = _blank_state("CLM-CLI-001", "POL-001", "uploads/test.jpg", "photo",
                              "My parked car was hit and the side door is dented.")
        result = app.invoke(state, config=config)
        cs = app.get_state(config)
        print("\n" + "="*60)
        print("FINAL ANSWER:\n" + (result.get("final_answer") or "none"))
        print("="*60)
    else:
        print("\n🚀 Starting HomeSite Claims API at http://localhost:8000")
        uvicorn.run(api, host="0.0.0.0", port=8000)
