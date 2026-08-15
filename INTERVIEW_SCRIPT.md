# Interview script — corrected against the codebase

Every claim here is checkable against this repo or the Azure resource group.
Where something isn't built, it's phrased as a gap or an intention, not a fact.

---

## 1. Introduce yourself

> I'm Tharun Kumar, around ten years in enterprise software. I started in React
> and frontend, moved into full-stack with Python, FastAPI, Node and cloud
> services.
>
> The last three years have been GenAI application engineering. Most recently a
> Claims Copilot for insurance adjusters — React, FastAPI, LangGraph, Azure
> OpenAI, Azure AI Search. My ownership was the application and orchestration
> layer: the retrieval and claims tools, the human-approval workflow, token
> streaming, and wiring it into the existing dashboard.
>
> My strength is building secure, production-oriented AI applications on
> enterprise data. I'm not an LLM researcher — I integrate pretrained models,
> RAG, tools, APIs, security and user workflows into working business systems.

*(Unchanged — nothing to correct.)*

---

## 2. Explain your recent GenAI project

> Adjusters were navigating several screens and searching policy documents by
> hand before responding to a claim. A single lookup could take ten or fifteen
> minutes when they needed both policy language and live claim data.
>
> We built a copilot inside the existing React dashboard, covering three things:
> retrieving policy clauses with citations, looking up live claim data through
> backend tools, and proposing governed actions like a status change.
>
> Policy documents live in Azure Blob Storage. An Azure AI Search indexer and
> skillset splits them into 512-token chunks with 64 tokens of overlap, embeds
> each chunk with `text-embedding-3-small`, and index projections fan them out so
> **one chunk becomes one index document** rather than one file. Each chunk keeps
> the source filename and path so the answer can cite it.
>
> At runtime FastAPI validates the caller and builds a security context. A
> LangGraph agent picks a tool. **The tool enforces authorization, never the
> model.** Document questions go to Azure AI Search — BM25 plus vector, fused
> with RRF, then a semantic reranker. Azure OpenAI answers from those chunks with
> citations, or says the evidence is insufficient.
>
> Writes are separated from reads and pause for adjuster approval before
> executing. The system assists the adjuster; it doesn't make coverage decisions.

**Corrected:** chunk metadata is **filename and path only** — no policy ID,
version, or section. Claiming rich metadata invites "show me the index schema."

---

## 3. Walk through one request end to end

> An adjuster asks "what does the policy say about roof damage?"
>
> React posts the question, thread ID and bearer token to FastAPI.
> `get_current_user` validates the token and loads the **User row from the
> database** — so role comes from the DB at request time, not from the token.
>
> The request enters the agent node. The model sees the question plus nine tool
> schemas and picks `search_policy_docs`. Its arguments are untrusted input.
>
> The question is embedded with the same model used at index time. Azure AI
> Search runs BM25 and vector search, fuses them with reciprocal rank fusion,
> then a cross-encoder reranks the fused list. The top chunks come back with
> their source filenames.
>
> Those go to Azure OpenAI with instructions to answer only from the supplied
> text, name the source document explicitly, and compare any numeric threshold
> directly against the adjuster's figures rather than hedging.
>
> The answer streams back to React as newline-delimited JSON. It presents the
> policy language and leaves the coverage determination to the adjuster.

**Corrected — this was the biggest error.** Policy search has **no filter and no
per-claim authorization**, because policy documents are org-wide reference
material. There's no adjuster-specific policy text to scope.

The filter story is real, but it belongs to a **different tool**. Say it
separately, because the distinction is the good part:

> Claims retrieval is different. `search_similar_claims` calls the backend for
> the claims this adjuster may see, then builds an Azure AI Search filter from
> those claim numbers with `vectorFilterMode: preFilter` — so scoping happens
> *before* ranking, and it applies to the keyword leg too. The scope comes from
> the JWT, never the model.
>
> The weakness I'd name: that filter carries up to 500 claim numbers in a filter
> string. It works at this size and won't scale — pgvector would make it a WHERE
> clause.

---

## 4. Fixed or agent-controlled retrieval? How many nodes?

> Routing is agent-controlled; each tool's internals are deterministic.
>
> The parent graph has **three nodes, and the third is the interesting one**.
> `agent` calls the LLM to decide what it needs. Then a conditional edge routes
> to **`tools`** for read-only tools, or **`action`** for the three that mutate a
> claim. Both loop back to `agent`.
>
> The split exists because `interrupt_before=["action"]` pauses the graph before
> writes execute. Reads flow straight through; writes stop for approval. If it
> were one node I couldn't gate one without gating the other.
>
>     agent ──► tools  ──► agent ──► END
>           └─► action ──► (pause for approval) ──► agent
>
> NL2SQL is one tool to the parent graph and a five-node subgraph internally:
> ground values, select tables, generate SQL, execute, format — with a
> conditional edge that retries on a database error.
>
> LangGraph is there for two features: the checkpointer, and `interrupt_before`.
> For a one-step RAG endpoint I wouldn't introduce it.

**Corrected:** three nodes, not two. The `tools`/`action` split *is* the
human-in-the-loop mechanism — describing it as two nodes loses the best part.

---

## 5. Authorization when the LLM calls tools

> A prompt is never a security control, and we never trust an
> authorization-relevant argument from the model.
>
> The auth layer validates the bearer token and loads the User row. The model can
> ask for `get_claim(claim_id=123)`, but it cannot set the user ID, role or
> scope — those aren't tool parameters at all.
>
> Inside the handler, the trusted identity decides. Single-claim reads go through
> `can_view_claim`. List endpoints go through `scope_claims`, which turns the
> role into a WHERE clause — admin sees everything, an SIU investigator sees only
> fraud-flagged, a senior adjuster sees their team, an adjuster sees their own.
>
> For model-generated SQL the query runs against a per-request **temp view** that
> already has the scope predicate baked in, as a role holding only SELECT. The
> model can't remove a filter it can't see.
>
> Invalid or expired token → 401. Authenticated but not permitted → 403. Valid
> query with nothing in scope → **empty result, never unfiltered data**. There's
> no fallback that widens a search when authorization fails.

**Corrected:** dropped "we tested the boundary with cross-user scenarios" —
there's no test suite, and that phrasing invites "show me the tests." If you want
the claim, write the tests first; it's an afternoon.

---

## 6. Dashboard auth vs MCP OAuth

> Two token systems, deliberately validated differently.
>
> The dashboard uses a **first-party HS256 token the backend issues itself**.
> FastAPI checks the signature against a shared secret and the expiry, then loads
> the user. There's no audience or issuer check, and that's correct — it's a
> symmetric first-party token, so there's no third party to distinguish. In a
> real deployment this would move to the corporate IdP.
>
> External MCP access uses Entra ID. The MCP server is a **resource server** — it
> never logs anyone in and never issues a token. It publishes protected-resource
> metadata so clients can discover the authorization server, then validates
> incoming tokens against Entra's published JWKS: signature, issuer, expiry, and
> **audience**.
>
> The expected audience is our API's Application ID URI, `api://<app-id>` — and we
> accept both that and the bare GUID, because which one you get depends on the
> app's token version.
>
> The audience check is the load-bearing one. A Microsoft Graph token is a
> perfectly valid Entra token from the same tenant, and it gets a 401 — I have
> that as a test. Without it, any app in the directory could drive these tools.
>
> We don't use the client secret to verify access tokens; verification is public-
> key only.

**Corrected:** "the company's existing authentication… validated using the
configured identity provider" implies an external IdP. Yours is self-issued
HS256, and the module is literally titled "Mock JWT auth." Own it — the *reason*
you don't check audience there is a better answer than pretending you do.

---

## 7. How did you evaluate it?

> Honestly — this is the weakest part of the project.
>
> There's no automated evaluation. I wrote about fifteen questions a real
> adjuster would ask, recorded the expected source document and whether the
> system should answer or abstain, and checked the results by hand. Prompts and
> retrieval config are in version control, so a change is at least reviewable.
>
> What I'd build, in order. A labelled set of around a hundred questions with the
> known-correct source, so I can measure **retrieval recall@k separately from
> answer quality** — they fail for different reasons and you can't fix what you
> can't attribute. Then faithfulness: is every claim in the answer supported by a
> retrieved chunk. Then a regression run in CI, because prompts are code.
>
> And I'd report a **confusion matrix rather than accuracy**, weighted — falsely
> saying "covered" is a financial and regulatory error, falsely saying "not
> covered" is a customer-service one. A single accuracy number hides that. It
> also needs both classes, or "always say excluded" scores perfectly.
>
> One real failure drove this: the model retrieved the correct exclusion clause
> and still concluded coverage. That pushed me to strengthen the grounding
> instructions and to sharpen the product boundary — the assistant presents the
> evidence, the adjuster decides.
>
> Right now a prompt change could degrade quality and I'd find out from a user.

**Corrected:** removed "Promptfoo-style regression checks" and "reviewed by
claims users." Neither exists, and the original contradicted itself — it claimed
automated regression checks, then closed by saying it wouldn't claim a benchmark
that isn't actually executed.

---

## 8. NL2SQL

> Aggregate questions route to SQL rather than vector search, because embeddings
> can't count — top-k gives you the nearest neighbours, not "all rows matching X."
>
> Five nodes: ground real business values, select the relevant tables, generate
> SQL, validate and execute, format the result.
>
> Three layers of safety. The connection is a Postgres role with only SELECT and
> TEMPORARY, so a write fails in the database rather than depending on my parser.
> The query runs against a per-request **temp view**, `my_claims`, already
> filtered to what the caller may see. And a statement guard rejects anything
> that isn't a single SELECT, plus any attempt to name the base table.
>
> The scoping is in the view, not the query — so the generated SQL contains **no
> adjuster filter at all**. The model is explicitly told not to add one. For
> "which region has the most claims" it produces roughly:
>
> ```sql
> SELECT t.region, COUNT(*) AS claim_count
> FROM my_claims c
> JOIN app_users u ON c.adjuster_id = u.id
> JOIN team     t ON u.team_id     = t.id
> GROUP BY t.region
> ORDER BY claim_count DESC
> ```
>
> Two details there. `region` isn't on the claim table — it's on `team` — so this
> needs a two-hop join the model has to work out from the schema description.
> And it joins `app_users`, not `user`: in Postgres `user` is reserved, and an
> unquoted `FROM user` silently resolves to the CURRENT_USER function and returns
> one row instead of twenty-five. No error, just wrong data.
>
> On failure the database error is fed back for regeneration, bounded to three
> attempts, then it stops and says so rather than inventing numbers. Results are
> capped at 200 rows.
>
> The honest limit: execution success proves the SQL ran, not that it's
> semantically right. A query that returns the wrong answer is silent, and the
> only real fix is an eval set with known-correct results.

**Corrected:** the original SQL (`FROM claims WHERE adjuster_id = :adjuster_id`)
is wrong twice — the scope lives in the view, not the WHERE clause, and `region`
isn't on the claim table. Also dropped "execution timeouts" (there's a *connect*
timeout, not a statement timeout) and "compared against approved reference SQL."

---

## 9. A production issue you diagnosed

> Under burst traffic we saw Azure OpenAI 429s, and some requests sat in flight
> long enough to time out.
>
> The cause wasn't user count. **A turn isn't one LLM call** — the agent node
> decides, a tool runs, the agent node composes, and an NL2SQL question adds
> three more. Every one of those calls re-sends the entire message list. So a
> retrieval result isn't paid for once; it's paid for on every subsequent call
> for the rest of the conversation. Per-prompt growth is linear; cumulative token
> spend across a conversation is quadratic.
>
> When I measured it, the surprise was *where* the tokens were. All nine tool
> schemas came to 522 tokens. A single policy-search result was about 2,560 —
> five times the entire tool surface, and it's the part that repeats. By turn
> five we were re-sending roughly ten thousand tokens of chunks the model had
> already read and summarised.
>
> The fix was to bound what we **send** without shrinking what we **store**. Old
> tool results are truncated in the outgoing context and the window is held under
> a token budget. The checkpoint keeps everything — that's the audit trail, and
> it's how I pull a conversation by thread ID when someone reports a bad answer.
>
> One detail that matters: I truncate tool output rather than dropping the
> message. Every tool result has to stay paired with its tool call — drop one
> side and the next request is a 400, not a soft degradation.
>
> That was 56% fewer tokens over a five-turn thread, which roughly doubled the
> turns per minute against the same quota.
>
> The capacity side was separate: the container apps were capped at one replica,
> so concurrent load queued behind a single container — that's where the timeouts
> came from, not the LLM.
>
> What I'd add next, and haven't: honouring `Retry-After` with jittered backoff,
> a concurrency semaphore so we queue rather than get rejected, a retry budget so
> retries can't amplify an overload, and capturing the rate-limit headers Azure
> returns on every response so I can see which limit binds.

**Corrected — the big one.** The original claims Application Insights and
LangSmith traces. **Neither is enabled** — LangSmith is in `requirements.txt`
with no tracing env vars on the deployed app. It also claims concurrency control,
Retry-After handling and summarization as built; they aren't. Moved to "what I'd
add next," which is both true and still shows you know the answer.

---

## 10. Throughput and retry behaviour

> Our chat deployment is `gpt-4.1-mini` on GlobalStandard at capacity 100 — that
> is **100,000 tokens per minute**, not requests.
>
> Before the context work a five-turn thread averaged about 7,700 tokens per
> turn, so roughly 13 turns per minute across all users. After it, about 3,300 —
> roughly 30 turns per minute. That's the number that actually matters, and it's
> small, which is why the token work came before asking for more quota.
>
> I'd plan to 80% of that. NL2SQL turns are measured separately since they add
> three more model calls.
>
> On 429s: retries alone amplify overload. Honour the server's `Retry-After`,
> use **full jitter** rather than fixed backoff — fixed backoff resynchronises
> every rejected client into a wave that collides again — cap attempts and total
> elapsed time, and bound concurrency at the source. The mechanism most people
> miss is a **retry budget**: cap retries as a fraction of traffic, and if more
> than about one in ten requests is a retry, stop retrying and fail fast.
>
> Whether RPM or TPM binds comes from the `x-ratelimit-remaining-requests` and
> `x-ratelimit-remaining-tokens` headers Azure returns on every response —
> whichever hits zero first. Structurally, Azure quota is TPM-primary with RPM
> derived at roughly 6 requests per 1,000 TPM, so for an agent sending
> 4,000-token prompts, **TPM binds first essentially always**.

**Corrected:** the quota is **100,000 TPM, not one million** — a factor of ten,
and it's checkable in the portal in thirty seconds.

---

## 11. Token streaming

> FastAPI returns a `StreamingResponse` with media type `application/x-ndjson`.
> An async generator consumes LangGraph's event stream and maps it to three
> event shapes, one JSON object per line:
>
> - `{"status": "🔍 searching policy documents…"}` — tool progress
> - `{"delta": "…"}` — answer fragments
> - `{"done": true, "answer"|"pending"|"error": …}` — terminal
>
> Token events are filtered to the `agent` node specifically, because the NL2SQL
> subgraph runs its own model calls — without that filter the generated SQL would
> stream into the user's answer.
>
> The React client uses the fetch reader with a `TextDecoder` in streaming mode,
> appends each chunk to a persistent buffer, scans for newlines, parses only
> complete lines, and keeps the remainder. Never assume one network chunk is one
> JSON object. Streaming mode on the decoder matters because a multi-byte
> character — our status emoji — can split across a chunk boundary.
>
> The framing is trustworthy because `json.dumps` never emits a literal newline;
> a newline inside a string is escaped. So a raw `\n` on the wire is always a
> frame boundary, never data.
>
> Once the first byte is sent, HTTP 200 is committed — a later failure can't
> become a 500, so it has to be an error event in the body. The client also
> checks for a **missing terminal frame**, which catches a truncated stream where
> the connection died before the error could be written.
>
> Two gaps I'd name: no explicit client-disconnect detection, so an abandoned
> stream still burns tokens; and no anti-buffering headers, which works today
> because Container Apps ingress doesn't buffer, but would break behind an nginx.

**Corrected twice.** There is no `citation` event — citations are inline in the
answer text, enforced by the system prompt. And the original's proposed fix, a
**bounded queue, is wrong**: the current design has no queue, and that's correct.
The async generator chain gives natural backpressure — if the client stops
reading, the socket blocks, the generator suspends at its `yield`, and that
propagates back to Azure OpenAI. Adding a queue would create an unbounded buffer
where none is needed. The right fix is disconnect detection.

---

## 12. Correct clause retrieved, wrong answer

*(Unchanged — this one was sound.)*

Two things to add:

> Before concluding it's a reasoning failure, I'd check whether the exclusion is
> **complete in the chunk**. Our chunks are a fixed 512-token window, and policy
> exclusions are long — if the condition is in chunk 4 and the exception in chunk
> 5, the model is reading a truncated rule. That looks like a reasoning failure
> and is actually a chunking failure.
>
> The highest-yield prompt change is forcing enumeration before conclusion: list
> every exclusion in the retrieved text *first*, then decide. A model that has
> just written out the exclusion is much less likely to contradict it.

---

## 13. Prompting vs RAG vs tools vs rules vs fine-tuning

*(Unchanged — accurate and well-judged.)*

---

## 14. Chunking and hybrid search

> Fixed-window chunking: **512 tokens with 64 tokens of overlap**, using Azure AI
> Search's SplitSkill in pages mode. Small enough that a chunk is mostly signal
> rather than surrounding boilerplate, large enough to keep a clause intact; the
> overlap stops a rule on a boundary being lost by both neighbours. Chunks keep
> the source filename and path.
>
> **I'd change this.** Policy documents have real structure — numbered clauses,
> conditions attached to specific sections — and a fixed token window cuts
> through it arbitrarily. Section-aware chunking is the improvement I'd make
> first, along with tagging chunks by type (coverage, exclusion, definition) so
> exclusions don't have to win a similarity contest against coverage text they're
> structurally shorter than.
>
> At query time, keyword search catches exact terms embeddings blur — document
> numbers, a specific dollar threshold. Vector search catches paraphrase, where
> the adjuster's wording doesn't match the document's. Azure fuses them with
> reciprocal rank fusion, which scores **rank position** rather than raw score,
> because cosine and BM25 aren't comparable scales. Then a cross-encoder reranks
> the fused list — it reads the query and chunk together in one pass rather than
> comparing two independently-produced vectors.
>
> To be precise about credit: Azure AI Search provides all four natively. What I
> did was declare the semantic configuration on the index and send both legs.

**Corrected:** the original claims **structure-aware chunking preserving
headings, sections and tables**, and metadata including section, policy ID,
version and page. Neither is true — it's `SplitSkill` in pages mode at a fixed
512 tokens, with filename and path only. That's an easy thing to disprove and
the honest version is stronger, because "here's what I'd change and why" beats
claiming you already did.

---

## 15. Python and FastAPI

> FastAPI is the AI application layer — chat, approval and reject endpoints, with
> Pydantic models validating requests. Auth and the security context are a
> dependency, so every route gets them the same way.
>
> The chat path is async end to end: the LangGraph run, the model calls and the
> streaming response are all async, which is what lets one worker hold many
> in-flight streams. The MCP tool layer is currently **synchronous httpx** —
> which is fine because it runs in a subprocess, but it's a thing I'd change
> before raising concurrency. Timeouts are explicit: 60 seconds on backend calls,
> because the default 5 was shorter than a legitimately slow endpoint and turned
> into intermittent failures that looked random.
>
> Document ingestion runs outside the request path entirely — a scheduled Azure
> AI Search indexer, not something the chat request waits on.
>
> On observability I'd rather be straight: structured logs and the LangGraph
> checkpointer, which lets me pull the exact state of any conversation by thread
> ID — that's been the single most useful debugging tool. What's missing is
> correlation IDs and real LLM tracing, so I can't yet follow one user request
> through FastAPI, the graph, the tool calls and the model invocation. That's the
> next thing I'd add.
>
> Testing is the other honest gap: no real suite yet. The tests I'd write first
> are the security ones — cross-user access against every tool — because those
> are the failures that matter most and they're deterministic, so they're the
> easiest to automate.

**Corrected:** removed the claim of unit, integration, security and regression
tests — there is no test suite. Removed correlation IDs. Softened "asynchronous
clients," since the MCP layer is synchronous httpx.
