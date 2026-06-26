from state import ClaimState
from clients.search_client import search_policy_docs
from clients.openai_client import ground_answer

# ── SYSTEM PROMPT ─────────────────────────────────────────────────
# this controls how the LLM answers — it must only use the retrieved clauses
SYSTEM_PROMPT = """
You are an insurance claims assistant for HomeSite Insurance.

Your job is to answer ONE question: based on the policy clauses
provided below, is the reported damage covered?

Rules you MUST follow:
1. Answer ONLY from the policy clauses provided. No outside knowledge.
2. If the answer is not in the clauses, say exactly:
   "This information is not available in the provided policy clauses."
3. Always state the deductible amount if mentioned in the clauses.
4. Always flag any exclusions that might apply to this claim.
5. Be factual and concise. No opinions, no guesses.

Format your answer exactly like this:
- Coverage: [covered / not covered / partially covered]
- Reason: [one sentence directly from the clauses]
- Deductible: [dollar amount or "not specified"]
- Exclusions: [any that apply, or "none found"]
"""


def retrieval_engine(state: ClaimState) -> dict:
    """
    Node 4: REAL Azure AI Search + Azure OpenAI.
    Retrieves relevant policy clauses and generates a grounded answer.
    This is the RAG step — the only node that calls real Azure.
    """
    print("\n[NODE 4] retrieval_engine running...")

    # step 1: build the search query from claim info
    query = (
        f"Does this policy cover: {state['damage_description']}? "
        f"Incident: {state['claimant_description']}. "
        f"What is the deductible? Are there exclusions?"
    )

    # step 2: search Azure AI Search (real RAG retrieval)
    # returns a list of relevant policy text chunks
    clauses = search_policy_docs(
        query=query,
        policy_id=state["policy_id"],   # metadata filter: right policy only
        top_k=3                          # retrieve top 3 most relevant chunks
    )

    print(f"  retrieved {len(clauses)} policy clauses")

    # step 3: build the user message with retrieved context
    clauses_text = "\n\n".join(clauses)   # join the list into one block of text
    user_message = f"""
Policy Clauses:
{clauses_text}

Claim Details:
- Damage: {state['damage_description']}
- Claimant said: {state['claimant_description']}

Question: Is this damage covered under these policy clauses?
"""

    # step 4: call Azure OpenAI — answer ONLY from the retrieved clauses
    coverage_summary = ground_answer(
        system_prompt=SYSTEM_PROMPT,
        user_message=user_message
    )

    print(f"  coverage summary: {coverage_summary[:100]}...")

    # step 5: write results back to state
    return {
        "policy_clauses": clauses,
        "coverage_summary": coverage_summary
    }