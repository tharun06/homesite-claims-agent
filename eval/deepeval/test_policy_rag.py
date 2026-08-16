"""Policy RAG — the generation half, ported from promptfoo.

Two kinds of test here, and the split is the point:

  DETERMINISTIC  verdict, citation, required terms      code decides, trust it
  LLM-JUDGED     faithfulness, answer relevancy         a model decides, verify it

The deterministic ones are the reason this is a pytest suite. `test_verdict` is
what catches the actual failure mode — correct clause retrieved, wrong
conclusion — and no LLM metric catches it, because a wrong conclusion phrased in
the document's own words is perfectly faithful.

    pytest eval/deepeval/test_policy_rag.py -v
    pytest eval/deepeval -v -m "not judged"     # skip the paid, slower half
"""
import pytest

from conftest import ALL_CASES, case_id, is_arguable, norm, record_metric

pytestmark = pytest.mark.skipif(not ALL_CASES, reason="eval/cases.jsonl not found")


# ── deterministic: no model grades these ────────────────────────────────────
@pytest.mark.parametrize("case", ALL_CASES, ids=case_id)
def test_returns_valid_shape(case, answers):
    got = answers[case["id"]]
    assert got.get("reply") is not None, got.get("error")
    for field in ("verdict", "quote", "answer"):
        assert field in got["reply"], f"missing {field}"


@pytest.mark.parametrize("case", ALL_CASES, ids=case_id)
def test_verdict(case, answers):
    """The conclusion must match the label.

    This is the assertion that matters most and the one no LLM metric provides.
    Note the labels are still candidate-quality — a failure here can mean the
    model was wrong OR the label was.
    """
    reply = answers[case["id"]].get("reply") or {}
    assert reply.get("verdict") == case["expected_verdict"], (
        f"expected {case['expected_verdict']}, got {reply.get('verdict')}"
    )


@pytest.mark.parametrize("case", ALL_CASES, ids=case_id)
def test_quote_is_real(case, answers):
    """A fabricated citation is worse than no citation — it looks checkable."""
    got = answers[case["id"]]
    quote = norm((got.get("reply") or {}).get("quote", ""))
    source = norm(" ".join(got["chunks"]))
    assert len(quote) > 20, "no usable quote returned"
    assert quote[:80] in source, "quote does not appear in the source document"


@pytest.mark.parametrize("case", [c for c in ALL_CASES if c.get("must_mention")], ids=case_id)
def test_mentions_key_term(case, answers):
    """The figure the answer turns on must actually appear in it."""
    reply = answers[case["id"]].get("reply") or {}
    blob = norm(reply.get("answer", "") + " " + reply.get("quote", ""))
    missing = [t for t in case["must_mention"] if norm(t) not in blob]
    assert not missing, f"answer never mentions {missing}"


def _answer_or_skip(got: dict, case: dict) -> str:
    """A generation failure is not a faithfulness failure.

    If the model returned nothing - a transient 429, a timeout, malformed JSON -
    the judged metrics have nothing to grade and would report a misleading zero.
    Skip instead, so the metric distributions stay honest and the real cause is
    visible in the skip reason.
    """
    import pytest as _pytest
    reply = got.get("reply") or {}
    answer = (reply.get("answer") or "").strip()
    if not answer:
        _pytest.skip(f"no answer generated for {case['id']}: "
                     f"{got.get('error') or 'empty answer field'}")
    return answer


# ── LLM-judged: a model grades these, so validate the judge before quoting ──
# These use deepeval's assert_test() rather than a bare assert. That is what
# registers the result with deepeval itself, so `deepeval test run` and the
# Confident AI dashboard can see them. The deterministic tests above stay on
# plain asserts deliberately - they are not metrics, and assert_test has nothing
# to offer them.
@pytest.mark.judged
@pytest.mark.parametrize("case", ALL_CASES, ids=case_id)
def test_faithfulness(case, answers, judge):
    """Hallucination check against GOLD context: is every claim in the answer
    supported by the source? This is the ceiling - the score the model can reach
    when retrieval is perfect.

    Catches INVENTION - a limit or clause that is not in the document. It does
    NOT catch misreading one that is; that is test_verdict's job.
    """
    from deepeval import assert_test
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.test_case import LLMTestCase

    got = answers[case["id"]]
    answer = _answer_or_skip(got, case)
    metric = FaithfulnessMetric(threshold=0.8, model=judge, include_reason=True)
    test_case = LLMTestCase(
        input=case["question"],
        actual_output=answer,
        expected_output=case["expected_verdict"],
        retrieval_context=got["chunks"],
        # Arguable labels do not gate CI, but are still measured and reported.
        flaky=is_arguable(case),
    )
    metric.measure(test_case)
    record_metric(case["id"], "faithfulness_gold", metric.score, metric.threshold, metric.reason)
    assert_test(test_case, [metric])


@pytest.mark.judged
@pytest.mark.parametrize("case", ALL_CASES, ids=case_id)
def test_answer_relevancy(case, answers, judge):
    """Did it answer the question that was asked, rather than a nearby one?"""
    from deepeval import assert_test
    from deepeval.metrics import AnswerRelevancyMetric
    from deepeval.test_case import LLMTestCase

    answer = _answer_or_skip(answers[case["id"]], case)
    metric = AnswerRelevancyMetric(threshold=0.7, model=judge, include_reason=True)
    test_case = LLMTestCase(
        input=case["question"],
        actual_output=answer,
        flaky=is_arguable(case),
    )
    metric.measure(test_case)
    record_metric(case["id"], "answer_relevancy", metric.score, metric.threshold, metric.reason)
    assert_test(test_case, [metric])


# ── end-to-end: the number users actually experience ────────────────────────
@pytest.mark.judged
@pytest.mark.parametrize("case", ALL_CASES, ids=case_id)
def test_faithfulness_on_real_retrieval(case, production_answers, judge):
    """Same question, but through PRODUCTION retrieval instead of gold context.

    Everything above hands the model the correct document on purpose, which
    isolates reasoning but flatters it. Measured context precision on the real
    path is 0.487 - about half of what the model sees is from other documents.

    The gap between faithfulness_gold and faithfulness_real is the cost of that
    noise, and it is the only number here that reflects what an adjuster gets.
    """
    from deepeval import assert_test
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.test_case import LLMTestCase

    got = production_answers[case["id"]]
    answer = _answer_or_skip(got, case)
    metric = FaithfulnessMetric(threshold=0.8, model=judge, include_reason=True)
    test_case = LLMTestCase(
        input=case["question"],
        actual_output=answer,
        retrieval_context=got["chunks"],     # what search REALLY returned
        flaky=is_arguable(case),
    )
    metric.measure(test_case)
    record_metric(case["id"], "faithfulness_real", metric.score, metric.threshold, metric.reason)
    assert_test(test_case, [metric])
