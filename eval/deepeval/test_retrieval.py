"""Retrieval — recall, precision and sufficiency.

The half of RAG that generation tests cannot see. Everything in
test_policy_rag.py hands the model the correct document on purpose; this file
asks whether production search would have found it in the first place.

All deterministic. No LLM grades anything here, so this is the cheapest part of
the suite and the safest to gate every push on.

Three questions, and they fail for different reasons:

  recall@k             did the right document come back at all?
  context_precision    how much of what came back was noise?
  context_sufficiency  did the retrieved text contain the fact the answer needs?

Recall was 1.0 at k=3 while precision sat at 0.487 — the right document was
always found, buried among chunks from other documents. Recall alone would have
reported that as healthy.

    pytest eval/deepeval/test_retrieval.py -v
    pytest eval/deepeval/test_retrieval.py -v -m judged   # DeepEval's LLM-judged view
"""
import pytest

from conftest import ALL_CASES, case_id, norm, record_metric

pytestmark = pytest.mark.skipif(not ALL_CASES, reason="eval/cases.jsonl not found")

K = 5
MIN_RECALL_AT_5 = 0.90       # the right document must almost always be found
MIN_PRECISION = 0.40         # current measured 0.487 — a floor, not a target
MIN_SUFFICIENCY = 0.85


@pytest.fixture(scope="session")
def retrieved() -> dict:
    """Run production search once per case and reuse.

    Deliberately `real_search` — the same hybrid + rerank the application sends,
    not a simplified version. A retrieval test against a different query shape
    measures something nobody ships.
    """
    out = {}
    for c in ALL_CASES:
        try:
            hits = _search_once(c["question"], k=K)
            out[c["id"]] = {"chunks": [h["content"] for h in hits],
                            "sources": [h["metadata_storage_name"] for h in hits]}
        except Exception as e:
            out[c["id"]] = {"chunks": [], "sources": [], "error": str(e)[:200]}
    return out


def _search_once(question: str, k: int = K) -> list[dict]:
    """ONE query, selecting both the text and the filename.

    Precision needs the filename, sufficiency needs the text. Fetching them with
    two separate queries doubled the embedding calls and the search calls for
    identical results - `select` takes a list, so one round trip does both.
    """
    import os
    import httpx
    from conftest import AOAI, AOAI_KEY, SEARCH, SEARCH_KEY, INDEX

    v = httpx.post(
        f"{AOAI}/openai/deployments/{os.getenv('AZURE_EMBEDDING_DEPLOYMENT','')}"
        f"/embeddings?api-version=2023-05-15",
        headers={"api-key": AOAI_KEY}, json={"input": question}, timeout=90,
    ).json()["data"][0]["embedding"]
    r = httpx.post(
        f"{SEARCH}/indexes/{INDEX}/docs/search?api-version=2024-07-01",
        headers={"api-key": SEARCH_KEY},
        json={"search": question,
              "vectorQueries": [{"kind": "vector", "vector": v,
                                 "fields": "content_vector", "k": k}],
              "queryType": "semantic", "semanticConfiguration": "default",
              "top": k, "select": "content,metadata_storage_name"},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["value"]


def _rank(case: dict, sources: list[str]):
    for i, s in enumerate(sources, 1):
        if s == case["expected_source"]:
            return i
    return None


# ── per case ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("case", ALL_CASES, ids=case_id)
def test_expected_document_is_retrieved(case, retrieved):
    """The controlling document must appear somewhere in the top k.

    A miss here is unrecoverable: no prompt can make the model reason about a
    clause it was never shown.
    """
    got = retrieved[case["id"]]
    assert not got.get("error"), got["error"]
    rank = _rank(case, got["sources"])
    record_metric(case["id"], "rank", rank or 99, K, f"sources={got['sources']}")
    assert rank is not None, (
        f"{case['expected_source']} not in top {K}: {got['sources']}")


@pytest.mark.parametrize("case", ALL_CASES, ids=case_id)
def test_context_is_sufficient(case, retrieved):
    """The term the answer turns on must be present in the retrieved text.

    A free recall proxy using labels we already have. When this fails, the
    generation tests are being asked to do the impossible.
    """
    if not case.get("must_mention"):
        pytest.skip("no must_mention label")
    blob = norm(" ".join(retrieved[case["id"]]["chunks"]))
    missing = [t for t in case["must_mention"] if norm(t) not in blob]
    assert not missing, f"retrieved text never contains {missing}"


# ── aggregate ───────────────────────────────────────────────────────────────
def test_recall_at_k(retrieved):
    """recall@1/3/5 across the whole set, gated on recall@5."""
    ranks = [_rank(c, retrieved[c["id"]]["sources"]) for c in ALL_CASES]
    n = len(ranks)
    r1 = sum(1 for r in ranks if r == 1) / n
    r3 = sum(1 for r in ranks if r and r <= 3) / n
    r5 = sum(1 for r in ranks if r and r <= 5) / n
    mrr = sum(1 / r for r in ranks if r) / n
    print(f"\n  recall@1={r1:.3f}  recall@3={r3:.3f}  recall@5={r5:.3f}  mrr={mrr:.3f}")
    assert r5 >= MIN_RECALL_AT_5, f"recall@5 {r5:.3f} below floor {MIN_RECALL_AT_5}"


def test_context_precision(retrieved):
    """What share of retrieved chunks came from the right document?

    Each question is answerable from ONE document, so a chunk from elsewhere is
    noise by definition. This is the number recall hides: perfect recall with
    low precision means the controlling clause arrives surrounded by competing
    text from other policies.
    """
    scores = []
    for c in ALL_CASES:
        srcs = retrieved[c["id"]]["sources"]
        if srcs:
            scores.append(sum(1 for s in srcs if s == c["expected_source"]) / len(srcs))
    precision = sum(scores) / len(scores)
    print(f"\n  context_precision={precision:.3f}  (n={len(scores)})")
    assert precision >= MIN_PRECISION, f"precision {precision:.3f} below floor {MIN_PRECISION}"


def test_context_sufficiency(retrieved):
    """Share of cases where the retrieved text actually contained the key term."""
    checked = [c for c in ALL_CASES if c.get("must_mention")]
    ok = 0
    for c in checked:
        blob = norm(" ".join(retrieved[c["id"]]["chunks"]))
        ok += all(norm(t) in blob for t in c["must_mention"])
    rate = ok / len(checked)
    print(f"\n  context_sufficiency={rate:.3f}  ({ok}/{len(checked)})")
    assert rate >= MIN_SUFFICIENCY, f"sufficiency {rate:.3f} below floor {MIN_SUFFICIENCY}"


# ── DeepEval's semantic view of the same question ───────────────────────────
@pytest.mark.judged
@pytest.mark.parametrize("case", ALL_CASES, ids=case_id)
def test_contextual_relevancy(case, retrieved, judge):
    """DeepEval's ContextualRelevancyMetric — a second, semantic opinion.

    Ours is structural: a chunk is relevant if it came from the expected file.
    That is cheap and exact but blunt — a chunk from the right document can
    still be irrelevant to the question. This asks a model instead, and the gap
    between the two numbers is how much the filename proxy flatters us.
    """
    from deepeval.metrics import ContextualRelevancyMetric
    from deepeval.test_case import LLMTestCase

    got = retrieved[case["id"]]
    if not got["chunks"]:
        pytest.skip("no chunks retrieved")
    metric = ContextualRelevancyMetric(threshold=0.4, model=judge, include_reason=True)
    metric.measure(LLMTestCase(
        input=case["question"],
        actual_output="",                 # not used by this metric
        retrieval_context=got["chunks"],
    ))
    record_metric(case["id"], "contextual_relevancy", metric.score, 0.4, metric.reason)
    assert metric.score >= metric.threshold, metric.reason
