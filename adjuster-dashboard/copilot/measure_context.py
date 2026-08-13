"""Measure what context budgeting actually saves.

Simulates a realistic adjuster conversation — policy lookups, a claims search,
an aggregate question — and reports tokens sent to Azure OpenAI per agent call,
with and without _prepare_context.

    python measure_context.py

The number this prints is the answer to "how much did that optimisation buy?",
which is a question worth being able to answer with a figure rather than a
shrug.
"""
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent import (
    CONTEXT_BUDGET_TOKENS,
    SYSTEM_PROMPT,
    TOOL_RESULT_KEEP_CHARS,
    _count_tokens,
    _prepare_context,
    _tok,
)

POLICY_CHUNK = (
    "Adjuster authority matrix, section 4.2. An adjuster may approve settlements "
    "up to $10,000 per claim without secondary review. A senior adjuster may "
    "approve up to $35,000. Claims above $35,000, and any total loss regardless "
    "of amount, require manager approval. Where the vehicle is within its first "
    "24 months and under 24,000 miles, a total loss is settled at replacement "
    "cost rather than actual cash value. Salvage disposition must be recorded "
    "within 5 business days of settlement approval. "
) * 3          # ~1 chunk of retrieved policy text

TURNS = [
    ("what is my approval limit for a total loss settlement",
     "search_policy_docs", POLICY_CHUNK * 5,
     "Per adjuster-authority-matrix.txt, you may approve up to $10,000..."),
    ("does that change if the car is nearly new",
     "search_policy_docs", POLICY_CHUNK * 5,
     "Yes — per endorsements-and-riders.txt, within 24 months..."),
    ("have we seen similar claims before",
     "search_similar_claims", '{"claim_number": "CLM-313426", "content": "Collision claim for a 2020 Toyota RAV4"}' * 5,
     "Five similar collision claims, the closest being CLM-313426..."),
    ("how many of my claims are fraud flagged",
     "query_claims_data", "23 of your 400 claims are fraud-flagged (5.75%).",
     "You have 23 fraud-flagged claims out of 400, about 5.75%."),
    ("which ones are near SLA breach",
     "list_my_claims", '{"claim_number": "CLM-850435", "sla_due": "2026-08-09", "status": "Under Review"}' * 8,
     "Six claims are within 48 hours of SLA breach..."),
]


def build_thread(n_turns: int) -> list:
    """The message list as the checkpointer would hold it after n turns."""
    messages: list = []
    for i in range(n_turns):
        question, tool_name, tool_result, answer = TURNS[i % len(TURNS)]
        call_id = f"call_{i}"
        messages.append(HumanMessage(content=question))
        messages.append(AIMessage(
            content="",
            tool_calls=[{"name": tool_name, "args": {"query": question}, "id": call_id}],
        ))
        messages.append(ToolMessage(content=tool_result, tool_call_id=call_id))
        messages.append(AIMessage(content=answer))
    return messages


def main() -> None:
    system_tokens = _tok(SYSTEM_PROMPT)
    schema_tokens = 522          # measured separately from the tool docstrings

    print(f"budget {CONTEXT_BUDGET_TOKENS} tokens | "
          f"old tool results kept to {TOOL_RESULT_KEEP_CHARS} chars")
    print(f"fixed overhead per call: system {system_tokens} + schemas {schema_tokens}\n")
    print(f"{'turn':>4} {'before':>9} {'after':>9} {'saved':>9} {'':>4}")
    print("-" * 42)

    totals = [0, 0]
    for n in range(1, len(TURNS) + 1):
        thread = build_thread(n)
        fixed = system_tokens + schema_tokens
        before = _count_tokens(thread) + fixed
        after = _count_tokens(_prepare_context(thread)) + fixed
        totals[0] += before
        totals[1] += after
        pct = (before - after) / before * 100 if before else 0
        print(f"{n:>4} {before:>9,} {after:>9,} {before-after:>9,} {pct:>5.0f}%")

    print("-" * 42)
    saved = totals[0] - totals[1]
    print(f"{'sum':>4} {totals[0]:>9,} {totals[1]:>9,} {saved:>9,} "
          f"{saved/totals[0]*100:>5.0f}%")

    tpm = 100_000
    print(f"\nAt {tpm:,} TPM on gpt-4.1-mini, and roughly 2 agent calls per turn:")
    for label, total in (("before", totals[0]), ("after", totals[1])):
        per_turn = total / len(TURNS) * 2
        print(f"  {label:<7} ~{per_turn:>7,.0f} tokens/turn -> ~{tpm/per_turn:>4.0f} turns/min across all users")


if __name__ == "__main__":
    main()
