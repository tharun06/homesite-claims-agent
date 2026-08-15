# promptfoo — generation-side evaluation

    python eval/promptfoo/prepare.py     # resolve gold context -> tests.json
    npx promptfoo@0.118.14 eval          # run
    npx promptfoo@0.118.14 view          # web UI

Retrieval is not measured here — `eval/run_eval.py --retrieval` owns recall@k,
because promptfoo has no notion of a search index. prepare.py hands each case
its correct document, so a wrong answer here can only be the model's fault.

## Why promptfoo alongside the Python harness

One reason: `providers`. Uncomment the second entry in the config and the same
30 cases run against both models, side by side, in one table. That turns "would
a bigger model fix this?" from an opinion into evidence.

## What each assertion catches

| assertion | catches | judged by |
| --- | --- | --- |
| `is-json` | malformed output | code |
| `js:verdict` | **wrong conclusion** — the real bug | code |
| `js:citation` | a fabricated quote | code |
| `icontains` | a required term missing from the answer | code |
| `context-faithfulness` | **hallucination** — claims not in the source | an LLM |
| `answer-relevance` | answering a different question | an LLM |

The first four are deterministic. Trust them. The last two are graded by a
model and need their own validation before you quote the numbers.

**Faithfulness and verdict catch different failures.** Faithfulness asks "did it
invent something?" A wrong conclusion phrased in the document's own words is
perfectly faithful and completely wrong — only the verdict assertion catches it.

## Two traps, both hit while building this

**promptfoo splits a `.txt` prompt on lines containing only `---`.** Using `---`
to delimit the policy text silently produced three prompts, two with no
instructions and no context. Every case "failed" for the wrong reason. Delimit
with anything else.

**`response_format: json_object` needs the word "json" in the prompt**, lower
case, or Azure rejects the request. Without it the model wraps replies in
```json fences and every JSON.parse in an assertion throws.

## Node

Current promptfoo needs Node >= 22. On Node 20, pin `promptfoo@0.118.14`.
