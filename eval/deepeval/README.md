# DeepEval suite

    pytest eval/deepeval -v                    # everything
    pytest eval/deepeval -v -m "not judged"    # deterministic only: fast, free
    pytest eval/deepeval -v -m judged          # the LLM-graded half

## Why pytest and not a bespoke runner

Half of what needs testing is not a metric. NL2SQL execution accuracy, the
approval gate and role scoping are plain assertions; faithfulness needs a judge.
pytest holds both, so it is one command and one CI step.

## The split that matters

| test | decided by | trust |
| --- | --- | --- |
| `test_returns_valid_shape` | code | high |
| `test_verdict` | code | high — **this is the one that catches the real bug** |
| `test_quote_is_real` | code | high |
| `test_mentions_key_term` | code | high |
| `test_faithfulness` | an LLM | needs the judge validated first |
| `test_answer_relevancy` | an LLM | same |

`test_verdict` catches "correct clause retrieved, wrong conclusion". No LLM
metric catches that: a wrong conclusion phrased in the document's own words is
perfectly faithful.

## What is covered

    pytest eval/deepeval -m "not judged"        ~8 min, no LLM grading

    test_retrieval.py       recall@k, context precision, sufficiency
    test_policy_rag.py      verdict, citations, required terms, faithfulness
    test_nl2sql.py          execution accuracy, safety, scoping, phrasing
    test_tool_selection.py  routing, via ToolCorrectnessMetric

Only `test_retrieval.py` exercises Azure AI Search as a search engine.
`test_policy_rag.py` uses it as a lookup - `search:"*"` filtered to one filename
- because handing the model the correct document on purpose is what separates a
reasoning failure from a retrieval one.

## Judge model

`AzureOpenAIModel` pointed at the same deployment the copilot uses. That means
the model is grading its own output on the judged tests, which inflates
agreement. Before quoting those numbers, hand-label ~50 and measure how often
the judge agrees with you — or point the judge at a different model.
