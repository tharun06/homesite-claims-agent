import base64, json
from openai import AzureOpenAI
from config import (
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_KEY,
    AZURE_OPENAI_DEPLOYMENT
)


def ground_answer(system_prompt: str, user_message: str) -> str:
    """
    REAL Azure OpenAI call.
    Returns mock data if Azure keys are not configured yet.
    """
    print(f"  [Azure OpenAI] generating grounded answer...")

    if not AZURE_OPENAI_KEY or "your_azure" in AZURE_OPENAI_KEY:
        print("  [Azure OpenAI] not configured — returning mock answer")
        return (
            "- Coverage: covered\n"
            "- Reason: Collision coverage applies for damage from vehicle collisions\n"
            "- Deductible: $500\n"
            "- Exclusions: none found for this claim type"
        )

    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_KEY,
        api_version="2024-02-01"
    )

    response = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        temperature=0
    )

    return response.choices[0].message.content


def assess_damage_from_images(
    frame_paths: list,
    vehicle_info: str = "unknown vehicle",
    cost_reference: str = ""
) -> dict:
    """
    Sends keyframe images to GPT-4o vision along with vehicle info and
    repair cost reference pulled from Azure AI Search.
    Returns structured damage assessment with description, severity, and cost.
    """
    print(f"  [Azure OpenAI Vision] assessing {len(frame_paths)} frames for {vehicle_info}...")

    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_KEY,
        api_version="2024-02-01"
    )

    image_content = []
    for path in frame_paths:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        image_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        })

    prompt = f"""You are a senior auto insurance damage assessor at HomeSite Insurance.

INSURED VEHICLE ON POLICY FILE: {vehicle_info}

IMPORTANT: First verify the vehicle in the images matches the insured vehicle above.
If the images clearly show a different vehicle type (e.g. policy says Honda Civic but
images show a large SUV or truck), set vehicle_mismatch to true.

Repair Cost Reference (use these ranges — do not estimate outside them):
{cost_reference if cost_reference else "No reference available — use general industry rates."}

Assess the damage visible in the images.

Respond ONLY in valid JSON with this exact structure:
{{
  "vehicle_mismatch": false,
  "damage_description": "specific description of which parts are damaged and how",
  "damage_severity": "minor|moderate|severe",
  "estimated_cost": 1500
}}

Severity guide:
  minor    = cosmetic only, vehicle drivable, cost under $1,000
  moderate = significant damage, may affect drivability, cost $1,000–$5,000
  severe   = major structural or mechanical damage, cost over $5,000

Return only the JSON, no other text."""

    response = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "user", "content": [{"type": "text", "text": prompt}] + image_content}
        ],
        temperature=0,
        response_format={"type": "json_object"}
    )

    result = json.loads(response.choices[0].message.content)
    print(f"  [Azure OpenAI Vision] severity={result.get('damage_severity')}  cost=${result.get('estimated_cost')}")
    return result
