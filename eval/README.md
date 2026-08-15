# eval

Three things, in order of how much you can trust them.

## 1. `test_scoping.py` — cross-user access, zero tolerance

Deterministic. No LLM, no labelling, every assertion is a fact about the API.
Safe to gate CI on. Exit code is the failure count.

    DASHBOARD_URL=https://… python eval/test_scoping.py

Covers: unauthenticated access, a token signed with the wrong secret, two
adjusters' books not overlapping, direct-object reference by claim id (403),
search not widening scope, and role scoping for SIU / senior / admin.

The positive checks matter as much as the negative ones — "adjuster reads own
claim -> 200" is what stops a fix that simply denies everything from passing.

## 2. `run_eval.py` — retrieval and generation, measured separately

    python eval/run_eval.py --retrieval    # no LLM: fast, cheap, deterministic
    python eval/run_eval.py                # both passes
    python eval/run_eval.py --gate         # exit 1 on regression vs baseline.json

Two passes, because the layers fail for different reasons:

  retrieval    question -> real search        -> did the right document come back?
  generation   question -> GOLD chunks -> LLM -> did it reason correctly?

Pass 2 injects the known-correct document rather than whatever search found.
That is what separates "retrieval missed it" from "the model misread it".

Reports a confusion matrix, not accuracy — answering "excluded" to everything
scores well and is useless. The two directional rates are what matter:
`false_coverage_rate` (financial/regulatory) and `false_denial_rate`
(customer service). `FALSE_COVERAGE_WEIGHT` is where the business puts a number
on the difference; 10:1 is a placeholder.

## 3. `generate_cases.py` — bootstrap only

Writes questions FROM a known document, so the expected source is correct by
construction. The verdict is not. Output is `reviewed: false` and the runner
warns until that changes.

**Current cases are unreviewed.** Treat the numbers as indicative until someone
who knows claims has been through them.
