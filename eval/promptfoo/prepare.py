"""Materialise promptfoo test cases from eval/cases.jsonl.

promptfoo drives the GENERATION side. It has no idea how to talk to Azure AI
Search, so we resolve each case's gold context here and hand it over as a plain
variable. That keeps the split we already have:

    run_eval.py --retrieval   ->  did search find the right document?  (recall@k)
    promptfoo                 ->  given the right document, is the answer right?

Writes tests.json (valid YAML too, so promptfoo reads it either way).

    python eval/promptfoo/prepare.py
"""
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
load_dotenv(ROOT / ".env")

CASES = HERE.parent / "cases.jsonl"
OUT = HERE / "tests.json"

SEARCH = os.getenv("AZURE_SEARCH_ENDPOINT", "").rstrip("/").strip("'")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "").strip("'")
INDEX = os.getenv("AZURE_SEARCH_INDEX", "policy-index").strip("'")


def gold_context(source: str, limit: int = 6) -> str:
    r = httpx.post(
        f"{SEARCH}/indexes/{INDEX}/docs/search?api-version=2024-07-01",
        headers={"api-key": SEARCH_KEY},
        json={"search": "*", "filter": f"metadata_storage_name eq '{source}'",
              "top": limit, "select": "content"},
        timeout=120,
    )
    r.raise_for_status()
    return "\n\n".join(d["content"] for d in r.json()["value"])


def main() -> None:
    cases = [json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip()]
    tests = []
    for c in cases:
        ctx = gold_context(c["expected_source"])
        test = {
            "description": f"{c['id']} [{c['expected_verdict']}] {c['question'][:60]}",
            "vars": {
                # 'query' and 'context' are the names promptfoo's RAG assertions
                # (context-faithfulness, context-relevance) look for. Renaming
                # them silently disables those assertions.
                "query": c["question"],
                "context": ctx,
                "expected_verdict": c["expected_verdict"],
            },
            "assert": [],
        }
        # every required term must appear somewhere in the answer
        for term in c.get("must_mention", []):
            test["assert"].append({"type": "icontains", "value": term})
        tests.append(test)
        print(f"  {c['id']}  {len(ctx):>6} chars context  <- {c['expected_source']}")

    OUT.write_text(json.dumps(tests, indent=2), encoding="utf-8")
    print(f"\n{len(tests)} tests -> {OUT.name}")


if __name__ == "__main__":
    main()
