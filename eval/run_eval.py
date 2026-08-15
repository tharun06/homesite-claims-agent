"""Evaluation harness — retrieval and generation measured separately.

    python eval/run_eval.py                 # both passes
    python eval/run_eval.py --retrieval     # retrieval only: no LLM, cheap, fast
    python eval/run_eval.py --gate          # compare to baseline.json, exit 1 on regression
    python eval/run_eval.py --save-baseline

Why two passes:

  Pass 1 (retrieval)  question -> real search        -> did the right doc come back?
  Pass 2 (generation) question -> GOLD chunks -> LLM -> did it reason correctly?

Pass 2 injects the known-correct document instead of whatever search found. That
is what makes the layers separable: if pass 2 is right and end-to-end is wrong,
the fault is retrieval; if pass 2 is wrong, no amount of retrieval work helps.

Accuracy is deliberately not reported. Saying "excluded" to everything scores
well on exclusion questions and is useless, so the output is a confusion matrix
plus two directional error rates — false coverage (a financial and regulatory
error) and false denial (a customer-service one). They are not equal, and
FALSE_COVERAGE_WEIGHT is where the business puts a number on that.
"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

CASES = Path(__file__).parent / "cases.jsonl"
BASELINE = Path(__file__).parent / "baseline.json"

SEARCH = os.getenv("AZURE_SEARCH_ENDPOINT", "").rstrip("/").strip("'")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "").strip("'")
INDEX = os.getenv("AZURE_SEARCH_INDEX", "policy-index").strip("'")
AOAI = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
AOAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
CHAT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
EMBED = os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "")

# 10:1 is a placeholder. Getting a claims manager to set this is a better
# conversation than picking it yourself.
FALSE_COVERAGE_WEIGHT = 10
FALSE_DENIAL_WEIGHT = 1

VERDICT_PROMPT = """You are assisting an insurance claims adjuster.

Answer ONLY from the policy text provided. Before concluding, list every
exclusion or limitation present in the text — then decide.

Return ONLY JSON:
{"verdict": "covered" | "excluded" | "conditional" | "informational" | "insufficient_evidence",
 "quote": "the exact sentence from the text that controls the answer",
 "answer": "one or two sentences for the adjuster"}

Use "insufficient_evidence" if the text does not settle it.

POLICY TEXT:
---
{context}
---

QUESTION: {question}
"""


def embed(text: str) -> list:
    r = httpx.post(f"{AOAI}/openai/deployments/{EMBED}/embeddings?api-version=2023-05-15",
                   headers={"api-key": AOAI_KEY}, json={"input": text}, timeout=90)
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def search(question: str, k: int = 5) -> list[dict]:
    """Same three-stage shape the application uses: BM25 + vector, fused, reranked."""
    payload = {
        "search": question,
        "vectorQueries": [{"kind": "vector", "vector": embed(question),
                           "fields": "content_vector", "k": k}],
        "queryType": "semantic", "semanticConfiguration": "default",
        "top": k, "select": "content,metadata_storage_name",
    }
    r = httpx.post(f"{SEARCH}/indexes/{INDEX}/docs/search?api-version=2024-07-01",
                   headers={"api-key": SEARCH_KEY}, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()["value"]


def gold_context(source: str, limit: int = 6) -> str:
    """Every chunk of the known-correct document — retrieval held perfect."""
    r = httpx.post(f"{SEARCH}/indexes/{INDEX}/docs/search?api-version=2024-07-01",
                   headers={"api-key": SEARCH_KEY},
                   json={"search": "*", "filter": f"metadata_storage_name eq '{source}'",
                         "top": limit, "select": "content"}, timeout=120)
    r.raise_for_status()
    return "\n\n".join(d["content"] for d in r.json()["value"])


def judge(question: str, context: str) -> dict:
    r = httpx.post(f"{AOAI}/openai/deployments/{CHAT}/chat/completions?api-version=2024-08-01-preview",
                   headers={"api-key": AOAI_KEY},
                   json={"messages": [{"role": "user",
                                       "content": VERDICT_PROMPT.replace("{context}", context)
                                                                .replace("{question}", question)}],
                         "temperature": 0},
                   timeout=180)
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    return json.loads(text)


def norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def run(cases: list[dict], with_generation: bool) -> dict:
    ranks, gen_rows, precisions, sufficient = [], [], [], []
    for i, c in enumerate(cases, 1):
        hits = search(c["question"])
        sources = [h["metadata_storage_name"] for h in hits]
        rank = next((j + 1 for j, s in enumerate(sources) if s == c["expected_source"]), None)
        ranks.append(rank)

        # ── context precision: how much of what came back was noise? ─────────
        # Every question here is answerable from ONE document, so a chunk from a
        # different file is noise by definition. Recall says the right document
        # was found; precision says how much junk arrived with it. Low precision
        # is the leading suspect for the false-coverage rate — if four of five
        # chunks describe coverage and one holds the exclusion, the model is
        # effectively voting.
        if sources:
            precisions.append(sum(1 for s in sources if s == c["expected_source"]) / len(sources))

        # ── context sufficiency: did the retrieved text contain what's needed?
        # A free recall proxy, using labels we already have — if `must_mention`
        # lists the figure the answer turns on, and it is absent from every
        # retrieved chunk, no prompt can rescue that answer.
        terms = c.get("must_mention", [])
        if terms:
            blob = norm(" ".join(h.get("content", "") for h in hits))
            sufficient.append(all(norm(t) in blob for t in terms))

        row = {"id": c["id"], "rank": rank}
        if with_generation:
            ctx = gold_context(c["expected_source"])
            try:
                out = judge(c["question"], ctx)
            except Exception as e:
                out = {"verdict": "error", "quote": "", "answer": str(e)[:120]}
            row["expected"] = c["expected_verdict"]
            row["predicted"] = out.get("verdict", "error")
            # citation validity: did the quoted sentence actually appear?
            q = norm(out.get("quote", ""))
            row["citation_ok"] = bool(q) and q[:80] in norm(ctx)
            # exclusion recall: were the required terms mentioned?
            blob = norm(out.get("answer", "") + " " + out.get("quote", ""))
            row["mentions_ok"] = all(norm(m) in blob for m in c.get("must_mention", []))
        gen_rows.append(row)
        print(f"  [{i:>3}/{len(cases)}] {c['id']}  rank={rank}"
              + (f"  {row.get('expected')}->{row.get('predicted')}" if with_generation else ""))

    n = len(cases)
    found = [r for r in ranks if r]
    metrics = {
        "n_cases": n,
        "recall_at_1": round(sum(1 for r in ranks if r == 1) / n, 3),
        "recall_at_3": round(sum(1 for r in ranks if r and r <= 3) / n, 3),
        "recall_at_5": round(sum(1 for r in ranks if r and r <= 5) / n, 3),
        "mrr": round(sum(1 / r for r in found) / n, 3) if n else 0.0,
        "context_precision": round(sum(precisions) / len(precisions), 3) if precisions else None,
        "context_sufficiency": round(sum(sufficient) / len(sufficient), 3) if sufficient else None,
    }

    if with_generation:
        graded = [r for r in gen_rows if r.get("predicted") not in (None, "error")]
        metrics["citation_validity"] = round(
            sum(1 for r in graded if r["citation_ok"]) / max(len(graded), 1), 3)
        metrics["mention_recall"] = round(
            sum(1 for r in graded if r["mentions_ok"]) / max(len(graded), 1), 3)

        matrix = Counter((r["expected"], r["predicted"]) for r in graded)
        n_excl = sum(1 for r in graded if r["expected"] == "excluded")
        n_cov = sum(1 for r in graded if r["expected"] == "covered")
        false_cov = sum(1 for r in graded
                        if r["expected"] == "excluded" and r["predicted"] == "covered")
        false_den = sum(1 for r in graded
                        if r["expected"] == "covered" and r["predicted"] == "excluded")
        metrics["false_coverage_rate"] = round(false_cov / n_excl, 3) if n_excl else None
        metrics["false_denial_rate"] = round(false_den / n_cov, 3) if n_cov else None
        metrics["weighted_cost"] = (FALSE_COVERAGE_WEIGHT * false_cov
                                    + FALSE_DENIAL_WEIGHT * false_den)
        metrics["_matrix"] = {f"{e}->{p}": c for (e, p), c in sorted(matrix.items())}
        metrics["_counts"] = {"excluded": n_excl, "covered": n_cov, "graded": len(graded)}
    return metrics


def show(m: dict) -> None:
    print("\n── retrieval " + "─" * 46)
    for k in ("recall_at_1", "recall_at_3", "recall_at_5", "mrr"):
        print(f"  {k:<22} {m[k]}")
    print(f"  {'context_precision':<22} {m.get('context_precision')}"
          "   (share of retrieved chunks from the right document)")
    print(f"  {'context_sufficiency':<22} {m.get('context_sufficiency')}"
          "   (retrieved text actually contained the key term)")
    if "false_coverage_rate" not in m:
        return
    print("\n── generation (gold context) " + "─" * 30)
    for k in ("citation_validity", "mention_recall"):
        print(f"  {k:<22} {m[k]}")
    print(f"\n  {'false_coverage_rate':<22} {m['false_coverage_rate']}   "
          f"(said covered when excluded — the costly direction)")
    print(f"  {'false_denial_rate':<22} {m['false_denial_rate']}")
    print(f"  {'weighted_cost':<22} {m['weighted_cost']}   "
          f"({FALSE_COVERAGE_WEIGHT}x false coverage + {FALSE_DENIAL_WEIGHT}x false denial)")
    print("\n  confusion (expected -> predicted):")
    for k, v in m["_matrix"].items():
        print(f"    {k:<44} {v}")
    c = m["_counts"]
    if min(c["excluded"], c["covered"]) < 10:
        print(f"\n  ! only {c['excluded']} excluded / {c['covered']} covered cases — "
              "this is a smoke alarm, not a thermometer.")


def gate(m: dict, base: dict) -> int:
    fails = []
    if base.get("false_coverage_rate") is not None and m.get("false_coverage_rate") is not None:
        if m["false_coverage_rate"] > base["false_coverage_rate"]:
            fails.append(f"false_coverage_rate {base['false_coverage_rate']} -> {m['false_coverage_rate']}")
    if m["recall_at_5"] < base["recall_at_5"] - 0.02:
        fails.append(f"recall_at_5 {base['recall_at_5']} -> {m['recall_at_5']}")
    if base.get("citation_validity") and m.get("citation_validity", 1) < base["citation_validity"] - 0.03:
        fails.append(f"citation_validity {base['citation_validity']} -> {m['citation_validity']}")
    print("\n" + ("REGRESSION\n  " + "\n  ".join(fails) if fails else "no regression vs baseline"))
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--retrieval", action="store_true", help="retrieval only (no LLM)")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--save-baseline", action="store_true")
    args = ap.parse_args()

    if not CASES.exists():
        print(f"no {CASES}. Run eval/generate_cases.py, review, then rename.")
        return 2
    cases = [json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip()]
    unreviewed = [c for c in cases if not c.get("reviewed")]
    if unreviewed:
        print(f"[warn] {len(unreviewed)}/{len(cases)} cases are unreviewed — "
              "labels are candidate-quality, treat results as indicative\n")

    metrics = run(cases, with_generation=not args.retrieval)
    show(metrics)

    if args.save_baseline:
        BASELINE.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        print(f"\nbaseline written to {BASELINE.name}")
    if args.gate:
        if not BASELINE.exists():
            print("\nno baseline — run --save-baseline first")
            return 2
        return gate(metrics, json.loads(BASELINE.read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
