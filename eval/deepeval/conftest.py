"""Shared fixtures for the DeepEval suite.

Everything the evaluation needs lives here so the test files stay readable:
the judge model, the labelled cases, gold-context lookup, and the call to the
model under test.

Run the whole suite with:

    pytest eval/deepeval -v

The point of pytest rather than a bespoke runner is that half of what needs
testing is not a metric at all. NL2SQL execution accuracy, the approval gate and
role scoping are plain assertions; RAG faithfulness needs a judge. Both live in
the same suite and the same CI step.
"""
import json
import os
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

CASES = ROOT / "eval" / "cases.jsonl"

SEARCH = os.getenv("AZURE_SEARCH_ENDPOINT", "").rstrip("/").strip("'")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "").strip("'")
INDEX = os.getenv("AZURE_SEARCH_INDEX", "policy-index").strip("'")
AOAI = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
AOAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
CHAT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
API_VERSION = "2024-08-01-preview"

# Same prompt promptfoo used, so the numbers stay comparable across the port.
VERDICT_PROMPT = """You are assisting an insurance claims adjuster.

Answer ONLY from the policy text below. Before concluding, list every exclusion
or limitation present in the text - then decide.

Respond with json only, in this shape:
{{"verdict": "covered" | "excluded" | "conditional" | "informational" | "insufficient_evidence",
 "quote": "the exact sentence from the text that controls the answer",
 "answer": "one or two sentences for the adjuster"}}

Use "insufficient_evidence" if the text does not settle it.

=== POLICY TEXT ===
{context}
=== END POLICY TEXT ===

QUESTION: {question}
"""


def _load_cases() -> list[dict]:
    if not CASES.exists():
        return []
    return [json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip()]


ALL_CASES = _load_cases()


def case_id(c: dict) -> str:
    """Readable pytest node ids: test_verdict[case-001-excluded]."""
    return f"{c['id']}-{c['expected_verdict']}"


@pytest.fixture(scope="session")
def judge():
    """The model DeepEval uses to GRADE. Deliberately a fixture with a name that
    says what it is — these scores are one model's opinion of another's output,
    and they need their own validation before being quoted."""
    from deepeval.models import AzureOpenAIModel
    return AzureOpenAIModel(
        model=CHAT,
        deployment_name=CHAT,
        api_key=AOAI_KEY,
        base_url=f"{AOAI}/",
        api_version=API_VERSION,
        temperature=0,
    )


def gold_context(source: str, limit: int = 6) -> list[str]:
    """Every chunk of the KNOWN-CORRECT document, as a list.

    Injecting this instead of whatever search returned is what separates the
    layers: a failure here cannot be blamed on retrieval. Retrieval is measured
    separately in eval/run_eval.py --retrieval.
    """
    r = httpx.post(
        f"{SEARCH}/indexes/{INDEX}/docs/search?api-version=2024-07-01",
        headers={"api-key": SEARCH_KEY},
        json={"search": "*", "filter": f"metadata_storage_name eq '{source}'",
              "top": limit, "select": "content"},
        timeout=120,
    )
    r.raise_for_status()
    return [d["content"] for d in r.json()["value"]]


def real_search(question: str, k: int = 5) -> list[str]:
    """What retrieval ACTUALLY returns in production — hybrid + rerank, no gold.

    Measured precision on this path is 0.487, so roughly half of what comes back
    is from other documents. Faithfulness against THIS is the number that
    reflects what users get; faithfulness against gold context is the ceiling.
    """
    r = httpx.post(
        f"{AOAI}/openai/deployments/{os.getenv('AZURE_EMBEDDING_DEPLOYMENT','')}/embeddings"
        f"?api-version=2023-05-15",
        headers={"api-key": AOAI_KEY}, json={"input": question}, timeout=90)
    r.raise_for_status()
    vector = r.json()["data"][0]["embedding"]
    r = httpx.post(
        f"{SEARCH}/indexes/{INDEX}/docs/search?api-version=2024-07-01",
        headers={"api-key": SEARCH_KEY},
        json={"search": question,
              "vectorQueries": [{"kind": "vector", "vector": vector,
                                 "fields": "content_vector", "k": k}],
              "queryType": "semantic", "semanticConfiguration": "default",
              "top": k, "select": "content"},
        timeout=120)
    r.raise_for_status()
    return [d["content"] for d in r.json()["value"]]


def ask_model(question: str, context: str) -> dict:
    """Call the model UNDER TEST — the same deployment the copilot uses."""
    r = httpx.post(
        f"{AOAI}/openai/deployments/{CHAT}/chat/completions?api-version={API_VERSION}",
        headers={"api-key": AOAI_KEY},
        json={"messages": [{"role": "user",
                            "content": VERDICT_PROMPT.format(context=context, question=question)}],
              "temperature": 0,
              "response_format": {"type": "json_object"}},
        timeout=180,
    )
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    return json.loads(text)


@pytest.fixture(scope="session")
def answers() -> dict:
    """Ask the model once per case, reuse across every test.

    Without this each metric would re-run generation and the suite would cost
    four times as much for identical outputs.
    """
    out = {}
    for c in ALL_CASES:
        chunks = gold_context(c["expected_source"])
        try:
            out[c["id"]] = {"chunks": chunks, "reply": ask_model(c["question"], "\n\n".join(chunks))}
        except Exception as e:
            out[c["id"]] = {"chunks": chunks, "reply": None, "error": str(e)[:200]}
    return out


@pytest.fixture(scope="session")
def production_answers() -> dict:
    """Same questions, but through REAL retrieval instead of gold context.

    The delta between this and `answers` is the cost of retrieval noise: any
    faithfulness drop here is context the model had to reason around.
    """
    out = {}
    for c in ALL_CASES:
        chunks = real_search(c["question"])
        try:
            out[c["id"]] = {"chunks": chunks,
                            "reply": ask_model(c["question"], "\n\n".join(chunks))}
        except Exception as e:
            out[c["id"]] = {"chunks": chunks, "reply": None, "error": str(e)[:200]}
    return out


def is_arguable(case: dict) -> bool:
    """Cases whose LABEL is genuinely debatable.

    `conditional` and `informational` are auto-generated distinctions that even a
    careful human would argue about — "what is my approval limit" is arguably
    informational and arguably covered. Marking them flaky keeps them measured
    without letting a label dispute gate CI. `excluded` and `covered` are not
    arguable and stay hard failures.
    """
    return case.get("expected_verdict") in ("conditional", "informational")


def norm(s: str) -> str:
    return " ".join((s or "").lower().split())


# ── result persistence ───────────────────────────────────────────────────────
# DeepEval only records results for tests that call its assert_test() helper.
# Ours are plain pytest assertions - deliberately, because half the suite is
# assertions rather than metrics - so `deepeval test run` reports "no test cases
# found" and .deepeval/ stays empty.
#
# This hook writes the run to disk regardless of framework: one row per test,
# plus a per-test-type tally. Pytest already knows every outcome; nothing here
# depends on DeepEval.
_RUN: list[dict] = []

# Metric SCORES, not just outcomes. A faithfulness of 0.81 and one of 1.00 both
# "pass" a 0.8 threshold and look identical in pytest output — but one is a
# near-miss and the other is comfortable. Distributions are what tell you a
# change made things quietly worse, so record the number, not the verdict.
_METRICS: dict = {}


def record_metric(case_id: str, name: str, score: float, threshold: float, reason: str = ""):
    """Called by judged tests so the score survives into results.json."""
    _METRICS.setdefault(case_id, {})[name] = {
        "score": round(float(score), 3),
        "threshold": threshold,
        "passed": float(score) >= threshold,
        "reason": (reason or "")[:200],
    }


def pytest_runtest_logreport(report):
    if report.when != "call":
        return
    name, _, param = report.nodeid.partition("[")
    _RUN.append({
        "test": name.split("::")[-1],
        "case": param.rstrip("]"),
        "outcome": report.outcome,
        "duration_s": round(report.duration, 2),
        "reason": (str(report.longrepr).splitlines()[-1][:160]
                   if report.failed and report.longrepr else ""),
    })


def pytest_sessionfinish(session, exitstatus):
    if not _RUN:
        return
    from collections import Counter
    out = Path(__file__).parent / "results.json"
    by_test: dict = {}
    for r in _RUN:
        t = by_test.setdefault(r["test"], Counter())
        t[r["outcome"]] += 1
    # aggregate each metric's score distribution, not just its pass rate
    scored: dict = {}
    for case, metrics in _METRICS.items():
        for name, m in metrics.items():
            scored.setdefault(name, []).append(m["score"])
    distributions = {
        name: {
            "n": len(v),
            "mean": round(sum(v) / len(v), 3),
            "min": round(min(v), 3),
            "max": round(max(v), 3),
            "below_threshold": sum(1 for x in v
                                   if x < next(iter(_METRICS.values()))[name]["threshold"]),
        }
        for name, v in scored.items()
    }
    summary = {
        "total": len(_RUN),
        "passed": sum(1 for r in _RUN if r["outcome"] == "passed"),
        "failed": sum(1 for r in _RUN if r["outcome"] == "failed"),
        "by_test": {k: dict(v) for k, v in by_test.items()},
        "metric_distributions": distributions,
        "metrics_by_case": _METRICS,
        "results": _RUN,
    }
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[eval] wrote {out.name}: "
          f"{summary['passed']}/{summary['total']} passed")
