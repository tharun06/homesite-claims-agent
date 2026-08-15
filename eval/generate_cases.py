"""Bootstrap candidate eval cases from the policy corpus.

Generating questions FROM a known document means the expected source is known by
construction — that is the only part of labelling that can be automated. The
verdict and the abstention expectation still need a human, and the output is
written with `"reviewed": false` so an unreviewed case is obvious.

    python eval/generate_cases.py            # -> eval/cases.candidate.jsonl

Review, set `reviewed: true`, then rename to eval/cases.jsonl.
"""
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

POLICIES = ROOT / "data" / "policies"
OUT = Path(__file__).parent / "cases.candidate.jsonl"

AOAI = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
KEY = os.getenv("AZURE_OPENAI_KEY", "")
DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")

PROMPT = """You are building an evaluation set for an insurance claims assistant.

From the policy document below, write {n} questions a real claims adjuster would
ask. Requirements:

- Each must be answerable ONLY from this document.
- Vary the shape: at least one where the answer is a specific threshold or
  number, and at least one about an EXCLUSION or limitation.
- Write the question as an adjuster would say it, not as a document heading.

Return ONLY a JSON array. Each object:
  {{"question": "...",
    "expected_verdict": "covered" | "excluded" | "conditional" | "informational",
    "must_mention": ["term the answer must contain"],
    "should_abstain": false}}

Use "conditional" when the answer genuinely depends on facts not given, and
"informational" when the question is not a coverage question at all.

DOCUMENT: {name}
---
{body}
"""


def ask(name: str, body: str, n: int = 3) -> list[dict]:
    r = httpx.post(
        f"{AOAI}/openai/deployments/{DEPLOYMENT}/chat/completions?api-version=2024-08-01-preview",
        headers={"api-key": KEY},
        json={"messages": [{"role": "user",
                            "content": PROMPT.format(n=n, name=name, body=body[:6000])}],
              "temperature": 0.3},
        timeout=120,
    )
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    return json.loads(text)


def main() -> None:
    docs = sorted(POLICIES.glob("*.txt"))
    print(f"{len(docs)} documents -> {OUT.name}\n")
    cases, cid = [], 0
    for path in docs:
        body = path.read_text(encoding="utf-8", errors="replace")
        try:
            generated = ask(path.name, body)
        except Exception as e:
            print(f"  {path.name:38} FAILED {type(e).__name__}")
            continue
        for g in generated:
            cid += 1
            cases.append({
                "id": f"case-{cid:03d}",
                "question": g["question"],
                # known by construction: the question was written from this file
                "expected_source": path.name,
                "expected_verdict": g.get("expected_verdict", "informational"),
                "must_mention": g.get("must_mention", []),
                "should_abstain": g.get("should_abstain", False),
                "reviewed": False,
            })
        print(f"  {path.name:38} +{len(generated)}")

    with OUT.open("w", encoding="utf-8") as fh:
        for c in cases:
            fh.write(json.dumps(c) + "\n")

    verdicts: dict = {}
    for c in cases:
        verdicts[c["expected_verdict"]] = verdicts.get(c["expected_verdict"], 0) + 1
    print(f"\n{len(cases)} candidates written")
    print("verdict mix:", verdicts)
    print("\nNext: review each line, fix the verdict, set reviewed=true,")
    print("then `mv eval/cases.candidate.jsonl eval/cases.jsonl`")


if __name__ == "__main__":
    main()
