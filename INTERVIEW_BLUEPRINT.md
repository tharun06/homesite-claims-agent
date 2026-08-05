# Adjuster Copilot — end-to-end blueprint

Two deliverables sit on one codebase:

1. **The chatbot** — an in-dashboard copilot an adjuster talks to. LangGraph
   orchestrator, RAG, an NL2SQL subgraph, durable conversation state, and a
   human approval gate on writes.
2. **The plugin surface** — the same nine tools exposed over MCP so Claude and
   ChatGPT can drive them, protected by OAuth 2.1 against Entra ID.

The interesting engineering is in the seams: identity crossing three hops,
which tools are safe to expose to a third-party LLM, and what "read-only" has to
mean when a model is writing the SQL.

---

## Saying it out loud

### "Tell me about your recent project" — ~75 seconds

Lead with the problem. Plant hooks. Stop early and let them dig.

> So it's an AI copilot for insurance claims adjusters.
>
> The problem is pretty simple. An adjuster has maybe forty open claims, and
> answering something like *"which of mine are about to breach SLA"* means
> clicking through a dashboard for ten minutes. So we put a chat assistant
> inside the dashboard that just answers it.
>
> Under the hood it's a LangGraph agent with three ways of finding things. Two
> are vector search — one over past claims, one over policy documents. The
> third is natural-language-to-SQL, because a question like *"how many
> fraud-flagged claims are open in the Southeast"* isn't a similarity problem,
> it's a counting problem. Embeddings can't count. That was probably the most
> interesting design call.
>
> The part I'd want to talk about is safety. The agent writes SQL, so I assumed
> that eventually it would write something it shouldn't. The database connection
> is a read-only role — so even if everything above it fails, Postgres just
> refuses. And anything that modifies a claim stops and waits for human approval
> before it runs.
>
> Then the second half. We published the same tools as an MCP server, so Claude
> and ChatGPT can use them too. That one's public on the internet, so it sits
> behind OAuth 2.1 with Entra ID.
>
> That's the shape of it — happy to go deeper anywhere.

### The 20-second version

For a screen, a recruiter, or when they've already heard a lot.

> It's an AI copilot for insurance claims adjusters — a chat assistant inside
> their dashboard that answers questions about their caseload. It's a LangGraph
> agent with vector search and a natural-language-to-SQL path for aggregate
> questions. We also published the same tools as an MCP server behind OAuth, so
> Claude and ChatGPT can use them.

### Delivery notes

- **Lead with the adjuster, not the stack.** Forty claims, ten minutes of
  clicking. Anyone can picture that. Nobody pictures "LangGraph StateGraph."
- **Say "adjuster", "SLA", "peril", "SIU".** Domain vocabulary signals you
  understood the business, not just the ticket.
- **Pause after "embeddings can't count."** It's the best hook you have. Let it
  land.
- **Name-drop at most four technologies.** Listing fifteen is the POC smell —
  it reads as "I followed a tutorial." Four sounds like you chose them.
- **Stop before they stop you.** The goal of an opener is a follow-up question,
  not completeness. Everything else in this document is the follow-up.
- **End with an offer, not a trail-off.** "Happy to go deeper anywhere" beats
  "…yeah, so, that's basically it."

**Hooks you're deliberately planting**, each of which you can answer well:
LangGraph · NL2SQL · embeddings can't count · read-only role · human approval ·
MCP · OAuth 2.1.

### The written version (for a CV or a follow-up email)

> A claims dashboard with an embedded copilot. The copilot is a LangGraph agent
> with three retrieval paths — vector search over past claims, vector search
> over policy documents, and a natural-language-to-SQL subgraph for aggregate
> questions. Conversation state is checkpointed to Postgres so a session
> survives a container restart, and any write pauses for human approval before
> it executes. The same tools are published as an MCP server so Claude and
> ChatGPT can use them, secured as an OAuth 2.1 resource server against Entra
> ID with per-request JWT validation. Deployed to Azure Container Apps for
> about $0/month.

---

## "Explain your RAG architecture" — the most-asked question

### The spoken answer, ~90 seconds

> Sure. There are two halves — how documents get in, and what happens at query
> time.
>
> Documents live in Azure Blob Storage. Azure AI Search has an indexer pointed at
> that container, and the indexer runs what Azure calls a *skillset* — two
> skills. The first splits each document into 512-token chunks with 64 tokens of
> overlap. The second embeds each chunk with `text-embedding-3-small`. Then index
> projections fan those chunks out, so **one chunk becomes one document in the
> index** — rather than one file becoming one document.
>
> That last bit matters more than it sounds. If you index per file, the
> retrievable unit is a whole policy document, and retrieval hands the model
> forty pages. You want the chunk to be the unit.
>
> Worth saying — no chunking or embedding code runs in my application. It's all
> inside the indexer. Which also handles freshness: blob indexers do incremental
> change detection, so on a schedule only the files that actually changed get
> re-cracked and re-embedded.
>
> At query time I embed the question, run a vector query against the chunk
> vectors, and then Azure's semantic reranker reorders the top hits with a
> cross-encoder. So two stages — **vectors for recall, reranker for precision.**
>
> The other thing I'd point out is what RAG *doesn't* cover. "How many
> fraud-flagged claims are open in the Southeast" isn't a similarity question —
> embeddings can't count. That routes to a natural-language-to-SQL path instead.
> Knowing which questions aren't RAG questions was probably the most important
> design call in the project.
>
> The honest gap is evaluation. I have no golden question set and no recall@k —
> I tuned by reading outputs, which doesn't scale.

### The structure underneath it

Memorise the spine, not the words. Four beats:

1. **Ingest** — where documents live → how they reach the index
2. **Retrieve** — what happens when a question arrives
3. **The boundary** — what RAG is *not* for, and where those questions go
4. **The gap** — evaluation

Beats 3 and 4 are what separate you. Almost everyone can describe chunk-embed-
retrieve. Far fewer volunteer where the approach stops working.

### If they hand you a marker

Draw exactly this, left to right, and talk over it:

```
  Blob          Indexer          Skillset              Index
 ┌──────┐      ┌────────┐      ┌───────────┐      ┌─────────────┐
 │ docs │ ───► │ crack  │ ───► │ split 512 │ ───► │ chunk       │
 └──────┘      │ +meta  │      │ overlap64 │      │ vector 1536 │
               └────────┘      │ embed     │      │ HNSW        │
                               └───────────┘      └─────────────┘
                                                         ▲
   question ──► embed ──► vector query ──► semantic rerank
```

Then add the second arrow — the one that *doesn't* go through the index:

```
   "how many …?" ──► NL2SQL subgraph ──► Postgres (read-only role)
```

Drawing the second path unprompted is the whole answer to "does this person
understand retrieval, or did they follow a tutorial."

### Follow-ups that always come next

- *Why 512 and 64?* → signal density vs. keeping a clause intact; overlap stops
  a rule on a boundary being lost by both neighbours.
- *Hybrid or pure vector?* → vector + semantic reranker. Two stages.
- *How do you keep it fresh?* → scheduled indexer, incremental change detection
  on blob `LastModified`.
- *How do you know it works?* → you don't have numbers. Say so, then say what
  you'd build.
- *How big is the corpus?* → ten documents, it's a demo. Then pivot to what
  changes at 10k: deletion handling and embedding quota.

---

## System map

```
  Browser
     │
     ▼
  React SPA  ──────────────►  FastAPI backend  ──────►  Postgres
  (Static Web Apps)          (Container Apps)          (Flexible Server)
     │                             ▲   ▲
     │ /chat /approve /reject      │   │
     ▼                             │   │
  Copilot service ─────────────────┘   │
  (Container Apps)                     │
     │  LangGraph                      │
     │   ├── agent node (Azure OpenAI) │
     │   ├── action node (tools)       │
     │   └── checkpointer ─────────────┼──► Postgres (state)
     │                                 │
     └── MCP client (stdio) ───────────┘

  Claude / ChatGPT ──── OAuth 2.1 ───►  MCP server  ──► FastAPI backend
                        (Entra ID)     (Container Apps)
```

Note the shape: **the MCP server is a client of the backend**, not a second door
into the database. One authorization implementation, not two.

---

# Part A — The chatbot

## A1. The orchestrator

A LangGraph `StateGraph` with two nodes:

- **`agent`** — calls Azure OpenAI with the tool schemas bound. Either answers
  or emits `tool_calls`.
- **`action`** — executes the requested tools, appends results, loops back.

A conditional edge routes `agent → action` when tool calls are present, and
`agent → END` otherwise. That loop is the whole agent; everything else is tools
and plumbing.

**Why LangGraph over a plain while-loop:** the checkpointer and the interrupt.
Those two features are the entire reason for the dependency, and both are load-
bearing here. Say that — it shows you chose the framework rather than defaulted
to it.

## A2. The three retrieval paths

Different question shapes need different machinery. Picking the wrong one is
the most common RAG mistake.

| question | path | why |
| --- | --- | --- |
| "any claims like this one?" | vector search over claims | fuzzy similarity |
| "what does the policy say about water damage?" | vector search over policy docs | fuzzy similarity |
| "how many fraud-flagged claims are open in the Southeast?" | **NL2SQL** | counting, grouping, filtering |

**Embeddings cannot count.** A vector index returns the *k* nearest neighbours;
it has no notion of "all rows matching X". Aggregate questions are a SQL
problem wearing a natural-language costume. Recognising that split is the point.

### The `description` problem

The original plan was to embed the claim `description` field. It turned out to
be unusable — short, templated, near-duplicate across claims, so every
embedding landed in the same neighbourhood and similarity was noise. Fixing it
meant constructing a richer text representation to embed rather than trusting
the raw column.

**Lesson to state:** the retrieval quality ceiling is set by what you embed, not
by which vector store you pick.

### The top-k prefilter

`search_similar_claims` originally took the top *k* by vector distance alone.
With `k=3` you get the three nearest claims globally — which may all be outside
the adjuster's region, or closed, or irrelevant.

The fix is **filter first, then rank**: narrow by structured predicates
(region, status, peril, date window) and run the vector search over that
candidate set. This is standard practice — the vector index is for ranking
within a relevant set, not for finding the relevant set.

## A2b. The document pipeline — how policy docs get indexed

This is the ingestion half of RAG, and it's the part interviewers probe when
they want to know whether you've run a corpus or just called an embedding API.

### Where the documents live

Azure **Blob Storage**, container `policy-documents`. Blob is the source of
truth; the search index is derived and disposable. That separation matters —
you can rebuild the index from scratch at any time, and you never lose the
originals to an indexing bug.

### The pipeline — Azure AI Search *integrated vectorization*

The important architectural point: **no chunking or embedding code runs in the
application.** Azure AI Search does it inside the indexer.

```
Blob container (policy-documents)
        │
        ▼
  Data source  ── "azureblob", points at the container
        │
        ▼
  Indexer  ── cracks each document, extracts contentAndMetadata
        │
        ▼
  Skillset ── SKILL 1: SplitSkill      → 512-token pages, 64-token overlap
        │     SKILL 2: AzureOpenAIEmbeddingSkill
        │                              → text-embedding-3-small, 1536 dims
        ▼
  indexProjections ── one index document per CHUNK, not per file
        │              (projectionMode: skipIndexingParentDocuments)
        ▼
  policy-index ── chunk_id (key) · content · content_vector (HNSW)
                  metadata_storage_name · metadata_storage_path
```

**"What skills did you use"** — in Azure AI Search, *skill* is a specific term:
a step in the enrichment pipeline. Two:

1. **`Microsoft.Skills.Text.SplitSkill`** — `textSplitMode: pages`,
   `maximumPageLength: 512`, `pageOverlapLength: 64`.
2. **`Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill`** — context
   `/document/chunks/*`, so it runs **per chunk**, not per document.

**Why 512 with 64 overlap:** small enough that a retrieved chunk is mostly
signal rather than surrounding boilerplate, large enough to keep a clause
intact. The overlap stops a rule that straddles a boundary from being lost by
both neighbours.

**`indexProjections` is the piece people miss.** Without it you get one index
document per file, with the vector of… what, exactly? The whole file? Then
retrieval returns a 40-page PDF and you've gained nothing. Projections fan the
chunks out so the retrievable unit is the chunk, and
`skipIndexingParentDocuments` stops the parent being indexed alongside them.

### The query side

`_search_policies` does a **vector query plus semantic reranking**:

- embed the user's question
- `vectorQueries` against `content_vector`, top *k*
- `queryType: "semantic"` — Azure's L2 reranker reorders the vector hits using
  a cross-encoder

That's a two-stage retrieval: **vector for recall, semantic reranker for
precision**. Worth naming explicitly — "we rerank" is a strong signal.

`_search_similar_claims` adds **`vectorFilterMode: "preFilter"`** — narrow to
the caller's own claims *before* ranking, so *k* only has to cover the matches
you want, instead of being padded to survive competition from every other
adjuster's claims. That's the top-k fix from A2, implemented in the engine
rather than in application code.

### Freshness

The indexer runs on a schedule (`interval: PT2H`) and Azure blob indexers do
**incremental change detection** via blob `LastModified` — only new and changed
blobs are re-cracked, re-chunked and re-embedded. Unchanged files cost nothing.

`seed_policy_docs.py` mirrors `data/policies/` to blob, deletes blobs that no
longer exist locally, and reruns the indexer — so the index reflects exactly the
local document set.

### Scale — answer this one carefully

**The demo corpus is 10 policy documents.** Say that if asked. What makes the
answer strong is that the *architecture* is the one you'd run at 10,000, and you
can explain exactly what changes:

| at 10k docs, daily churn | what handles it |
| --- | --- |
| re-embedding everything daily is unaffordable | incremental change detection — only changed blobs reprocess |
| indexer run time | indexers batch and can run in parallel; large corpora need a schedule + batch size tuning |
| deletions | blob soft-delete detection policy, or a delete marker — **otherwise deleted docs stay searchable forever** |
| embedding throughput | the embedding skill hits Azure OpenAI quota; needs a rate limit and retry, and PTU if sustained |
| cost | embedding is per-token on ingest; storage is per-vector. HNSW is memory-resident, so vector count drives tier |
| index tier | the free tier caps storage and index count — 10k docs of chunks needs Basic or Standard |

**Do not claim a corpus you don't have.** "Over 10,000 PDFs updated daily"
invites exactly the follow-ups above — indexer runtime, embedding spend,
deletion handling, quota throttling — and a wrong number there costs you more
than the bigger figure ever earns. The honest version is stronger anyway:

> "The demo corpus is ten policy documents, but the pipeline is integrated
> vectorization against blob, so it's the same design at ten thousand — the
> indexer does incremental change detection, and the embedding happens inside
> the skillset rather than in my code. What would actually change at that scale
> is deletion handling and embedding quota, and I'd want a soft-delete policy
> before going near production."

That answer demonstrates more than the inflated one, and it survives follow-ups.

### Two loose ends, if someone reads the repo

- `clients/search_client.py` is **legacy** — from the original claims agent,
  keyword-only, no vector query. The live path is `_search_policies` in
  `mcp_server.py`. Don't let a reviewer find the old file and conclude the
  vectors are unused.
- The query passes `semanticConfiguration: "default"` but no setup script
  creates one — it was added in the portal. That's exactly the kind of thing
  IaC would have caught.

## A3. NL2SQL as a subgraph-as-a-tool

The piece worth talking about most, because it's where "let an LLM write SQL"
meets "don't let an LLM touch the database".

**Structure:** a second, self-contained `StateGraph`, compiled, then wrapped in
an `@tool` function. The orchestrator sees one tool called
`query_claims_data`. It has no idea there's a five-node graph behind it.

```
ground_values → select_tables → generate_sql → execute_sql → ┬─ retry ──┐
                                                             └─ format_answer
                                                                   ▲     │
                                                                   └─────┘
```

- **`ground_values`** — map phrases in the question to real column values
  ("Southeast" → the actual region code). Prevents SQL that is syntactically
  perfect and semantically empty.
- **`select_tables`** — schema linking. Pick only the relevant tables so the
  generation prompt stays small and focused.
- **`generate_sql`** — the LLM call.
- **`execute_sql`** — runs it behind the guardrail (below).
- **retry edge** — on a database error, feed the error text back and regenerate.
  Bounded retries, then give up honestly.
- **`format_answer`** — turn rows into prose.

**Why a subgraph and not just a function:** it gets its own state, its own retry
loop, and its own node-level observability, without any of that leaking into the
orchestrator's state. Composition, not nesting.

### Schema profiles — the part teams get wrong

`schema_profiles.py` holds a semantic description of each table and column. The
industry pattern is a **split**:

- **Mechanical half, generated:** column names, types, nullability, cardinality,
  sample values — reflected from the database and data-profiled.
- **Semantic half, hand-written:** what the column *means*, which values are
  legal, what the gotchas are.

Nobody hand-writes 200 column types, and no generator knows that
`SIU Flagged` means Special Investigations Unit.

### The three-layer SQL guardrail (`sql_runtime.py`)

Assume the model will eventually emit something hostile — through a bug, a
jailbreak, or injected content in a retrieved document.

1. **Statement inspection** — reject anything that isn't a single `SELECT`.
   Blocks multi-statement (`; DROP …`), DDL, DML.
2. **Database-enforced read-only** — a Postgres role granted only `SELECT` and
   `TEMPORARY`. If layer 1 is bypassed, the *database* refuses. This is the one
   that matters: it doesn't depend on your parser being correct.
3. **Row scoping** — the query runs against TEMP VIEWs, not base tables, and
   `_scope_predicate()` bakes the caller's authorization into those views. An
   adjuster cannot read another region's claims *even with a correct query*.

Layer 3 deliberately mirrors the backend's `scope_claims()` — same rule, so a
question answered via SQL and the same question answered via the REST API return
the same rows.

**The line to use:** "I didn't try to make the LLM safe. I made the blast radius
small enough that it doesn't need to be."

## A4. The checkpointer — why the bot remembers

`StateGraph.compile(checkpointer=saver)`.

After **every node transition**, LangGraph serialises the graph state and writes
it under a `thread_id`. On the next message with the same `thread_id`, it loads
that state and continues.

- **What's stored:** the whole state object — the message list, and any other
  channels. Not "what the LLM said" — the full state at each step.
- **Why per-node and not per-turn:** that's what makes resume-from-interrupt
  possible. You can stop between the agent node and the action node and pick up
  exactly there.
- **`thread_id`** is the conversation key and the isolation boundary.

**Progression, and why:** `AsyncSqliteSaver` locally → `AsyncPostgresSaver` once
deployed. SQLite died on a shared Azure Files volume (see war stories), and a
scale-to-zero container loses in-process state on every idle timeout. Durable
state has to live outside the container.

## A5. Human-in-the-loop on writes

Three tools mutate: `update_claim_status`, `add_note_to_claim`, `reassign_claim`.

`interrupt_before=["action"]` stops the graph before the tool node runs. The
pending call is surfaced to the adjuster. On approve, `ainvoke(None, config)`
resumes from the checkpoint — the `None` means "no new input, continue from
saved state." Reject discards.

This is exactly why the checkpointer is not optional: the interrupt is only
useful if the paused state survives long enough for a human to look at it.

### The three bugs found here — good material

Found by reading the code rather than by a failure:

1. **`/approve` trusted a client-supplied `thread_id` with no ownership check.**
   Anyone could approve anyone's pending write. A complete bypass of the gate.
2. **Only one of three write tools was in `WRITE_TOOLS`.** Two mutations sailed
   past the interrupt.
3. **The router only inspected `tool_calls[-1]`.** A batch with a write anywhere
   but last went unchecked.

**The lesson:** a security control that is *present* is not a security control
that *works*. All three would have passed a demo.

## A6. Streaming

Token streaming via `astream_events`, filtered on
`event["metadata"]["langgraph_node"] != "agent"` so intermediate node chatter
doesn't reach the user — only the final answer streams.

## A7. Identity and authorization

`_resolve_caller()` establishes who's asking; `scope_claims()` filters rows by
role and region. Enforcement is **server-side in the backend**, not in the agent
prompt. An LLM instruction is not an access control.

---

# Part B — The plugin surface (MCP + OAuth 2.1)

## B1. What MCP is, in one line

A standard protocol for exposing tools to an LLM client, so you write the tool
once instead of once per vendor. The same nine tools serve the in-house copilot,
Claude, and ChatGPT.

## B2. Transports

- **stdio** — the client spawns your server as a subprocess and talks over
  stdin/stdout. Local only. This is Claude Desktop's config file path.
- **Streamable HTTP** — a real HTTP endpoint at `/mcp`. Required for anything
  remote.

`stateless_http=True` because Container Apps can scale to multiple replicas and
there's no sticky routing — any replica must be able to serve any request.

## B3. Identity across the hop

The MCP server calls the backend, so it needs a credential:

- **stdio:** `ADJUSTER_TOKEN` from the environment — a specific adjuster.
- **remote:** `SERVICE_EMAIL` self-login, with a **401-triggered re-login and
  retry**. Tokens expire; a long-lived server that doesn't handle that dies
  quietly after an hour.

## B4. OAuth 2.1 — the three players

| role | who | job |
| --- | --- | --- |
| Resource server | the MCP server | owns the data, validates tokens, **never logs anyone in** |
| Authorization server | Entra ID | knows the users, issues tokens |
| Client | Claude / ChatGPT | wants to call tools on a user's behalf |

The server **never sees a password** and **never phones Entra to validate**. It
verifies the signature offline against Entra's published JWKS. A token is a
signed statement: *"Entra says user U granted app C the scope `claims.access`
on API A, until time T."*

**Why this split matters:** changing identity provider means changing one file.
No tool code knows what a token is.

## B5. The handshake

```
 1. client → POST /mcp                          (no token)
 2. server → 401 + WWW-Authenticate: resource_metadata="…"
 3. client → GET /.well-known/oauth-protected-resource     (RFC 9728)
 4. server → { resource: <this server>, authorization_servers: [Entra] }
 5. client → Entra /authorize: client id, scope, resource, PKCE challenge
 6. Entra  → is that resource a registered App ID URI?
 7. Entra  → sign-in + consent screen
 8. user   → approves
 9. Entra  → redirect with one-time code
10. client → trades code + PKCE verifier for a JWT
11. client → POST /mcp with Authorization: Bearer <jwt>
12. server → verify signature / audience / issuer / expiry
13. server → 200, runs the tool
```

## B6. Validation, and why the audience check is the load-bearing one

Checked per request: **signature** (JWKS, cached with TTL, refetch on unknown
`kid` so key rotation self-heals), **audience**, **issuer**, **expiry**.

A token minted for a different API in the same tenant is a *perfectly valid
Entra token*. Accepting it would let any app in the directory drive these tools.
The audience check is what makes a token specific to this server.

**Proven, not asserted:**

| request | result |
| --- | --- |
| no token | 401 + `WWW-Authenticate` |
| garbage string | 401 |
| real Entra token, audience = Microsoft Graph | **401** |
| real Entra token, audience = our API | 200, 9 tools, live data |

Row 3 is the answer to "how do you know it's actually secure" — a genuine,
correctly-signed, unexpired token from the right tenant, refused.

## B7. Five non-obvious failures

Each found by pointing a real client at a hello-world server and watching it
fail. Worth knowing because they're the difference between reading the spec and
having shipped it.

1. **`required_scopes` must be the resource-qualified `api://<app id>/claims.access`.**
   The bare name leaves Entra unable to tell which API is meant, so it defaults
   the resource to Microsoft Graph and sign-in dies with `AADSTS650053` before
   your server is contacted at all.
2. **The token comes back with the *other* spelling** — `scp` holds the bare
   name while the SDK checks for the URI form. Report both.
3. **The SDK's protected-resource document cannot work with Entra.** It's built
   from a field pydantic pins to `AnyHttpUrl`, so `api://…` can't be expressed —
   and Entra rejects any RFC 8707 `resource` that isn't an App ID URI
   (`AADSTS9010010`), at the *authorization* endpoint, before sign-in, so
   nothing reaches you to debug.
4. **Clients probe several well-known paths; a 404 on the first one ends the
   handshake.** Serve all five.
5. **The metadata must advertise `S256`.** Entra supports PKCE but omits it from
   its own metadata, so a strict client refuses to start.

Debugging any of this needed a raw-ASGI request logger — deliberately *not*
Starlette's `BaseHTTPMiddleware`, which buffers responses and would break the
streaming transport.

## B8. The wall: identifier URIs need a verified domain

A client derives the RFC 8707 `resource` from **the URL the user typed**, not
from what you advertise. Entra accepts it only if that exact string is a
registered Application ID URI — and it now refuses any unverified domain:

```
HostNameNotOnVerifiedDomain
```

Tested against `azurecontainerapps.io`, `azurewebsites.net`, and a third-party
domain; on `az ad app update`, on **create**, and via a direct Microsoft Graph
PATCH. All rejected. Pre-existing registrations are grandfathered — which is why
a devtunnel URI registered before the change still completes a full sign-in.

**The fix is a domain you own**, mapped to the app and verified in Entra. That's
what production does anyway — you wouldn't ship a customer endpoint on a
generated cloud hostname. Everything short of that hop is deployed and verified.

**Two adjacent facts worth knowing:**

- **`requestedAccessTokenVersion`.** On v1 (`null`), the token's `aud` is
  *whatever URI the client requested* — so with a custom domain it'd be the
  https URL, and a verifier expecting `api://…` would 401 everything with
  nothing in the logs. Setting it to `2` makes `aud` always the app-id GUID.
- **Entra does not support Dynamic Client Registration.** Most MCP clients
  expect DCR; with Entra you paste a pre-registered client id and secret into
  the connector dialog by hand.

## B9. What should not be exposed — and why

Currently all nine tools are public behind OAuth. Two real problems:

- **The write tools bypass the HITL gate.** The approval interrupt lives in the
  *copilot's* LangGraph. Claude calls the MCP server directly, so a write from
  Claude skips human approval entirely. Authentication answers *who*; it does
  not answer *what they may do*.
- **PII crosses a tenant boundary.** `claim_summary` returns customer name,
  phone, email, VIN and policy number — which would land on a third-party LLM
  provider's servers on every call.

The intended split: read-only tools external, PII-bearing tools only behind
redaction, write tools internal-only.

Also outstanding: **tool annotations** (`readOnlyHint` / `destructiveHint`) are
missing, which is why ChatGPT labels all nine tools DESTRUCTIVE.

**Say this unprompted.** Knowing the remaining hole is stronger than claiming
there isn't one.

---

# Part C — Infrastructure

## C1. Hosting

- **Frontend:** Azure Static Web Apps. Chosen over a container running nginx —
  no cold start, a separate free allowance, and no image to rebuild for a CSS
  change.
- **Backend, copilot, MCP server:** Azure Container Apps, Consumption plan.
  Scale-to-zero, and the monthly free grant (vCPU-seconds + requests) is
  always-free, not trial-only. `max-replicas` capped as a blast-radius limit —
  a runaway loop can't scale into a bill.
- **Database:** PostgreSQL Flexible Server.

## C2. Registry

**ghcr.io, not Azure Container Registry.** ACR Basic is ~$5.07/month *standing* —
charged whether or not you push. For public images ghcr.io is free. On a
$5–10 total budget that single choice is most of the budget.

## C3. CI/CD, honestly

GitHub Actions builds on push and pushes SHA-tagged images to ghcr.io.
Deployment is a manual `az containerapp update` to the new tag.

**Known gap:** there's no trigger that rolls a new image out automatically.
Fixing it properly means SHA-pinned tags plus OIDC federation from Actions to
Azure instead of a stored credential. Deliberately deferred — say so; "we tag by
SHA and deploy explicitly" is a defensible position, and `:latest` with no
rollout trigger is a real gap worth naming.

## C4. Cost control

Current spend: **$0.00**. The rules: no standing-charge resources, scale to
zero, capped replicas, free-tier registry, and checking the portal rather than
trusting a script's exit code — which is how a second, billable Postgres server
got caught mid-provision and deleted.

---

# Part D — War stories

The most useful interview material in the whole project. Each one is a real
failure with a transferable lesson.

**SQLite on an Azure Files share → `database is locked`.**
SMB doesn't provide POSIX advisory locks, so SQLite's locking silently doesn't
work. Killed the shared-volume plan and forced the move to Postgres.
*Lesson: "it's a filesystem" is not the same as "it's a filesystem with the
semantics your library assumes."*

**Postgres reserved word `user`.**
`SELECT … FROM user` returned **1 row** — unquoted `user` resolves to the
`CURRENT_USER` function. `FROM "user"` returned **25**. No error, just wrong
data. Fixed with an `app_users` view.
*Lesson: the dangerous bugs don't raise.*

**`default_transaction_read_only` blocked `CREATE TEMP VIEW`.**
The read-only setting fought the row-scoping mechanism. Resolved by relying on
role grants and setting the session read-only *after* view creation.
*Lesson: two correct-looking safety layers can conflict.*

**`mcp.settings.token_verifier` silently does nothing.**
FastMCP reads `mcp._token_verifier`. Assigning to `settings` leaves the server
**wide open while looking configured**. Caught by testing an unauthenticated
call rather than reading the config.
*Lesson: verify security controls from the outside.*

**`jose` audience check with a list.**
Its internal check is `audience not in audience_claims`, which only works for a
single string — passing a list would have rejected every token. Now the audience
is checked by hand.

**Version skew in the langgraph family.** Container died at import on a
`langgraph.runtime` import. Fixed by pinning the whole family together, not one
package.

**App Service quota of 0 vCPU** on the subscription → pivoted to Container Apps.
**`ManagedClusterSuspended`** on an 11-day-idle environment → rebuilt fresh.

**httpx's 5-second default timeout** was killing calls to an endpoint that takes
~7.5s. Raised to 60s.

**ChatGPT `ClientDisconnect`** turned out to be a scale-to-zero cold start, not
an auth failure.

**The realtime simulator was corrupting the data** — draining statuses to
APPROVED and randomly reassigning claims, flattening every distribution. Gated
behind an env var, default off.
*Lesson: demo scaffolding that writes to the database is a data-integrity risk.*

**Known and deliberately unfixed:** an N+1 in `claim_summary` — 174 queries,
~7.5s. An identity-map preload was attempted, didn't work, and was reverted
rather than left half-done.
*Lesson: knowing the cost and choosing not to pay it is engineering; not knowing
is not.*

---

# Part E — Questions you should expect, answered

Spoken answers, not written ones. Two or three sentences, then stop.

## On the agent

**Why LangGraph and not a plain while-loop, or LangChain agents?**
> Two features, and I'd have had to build both by hand otherwise. The
> checkpointer — graph state persists to Postgres after every node, so a
> conversation survives a container restart. And `interrupt_before`, which lets
> me stop the graph *between* deciding to call a tool and actually calling it.
> That's where the human approval gate lives. If I didn't need those, a while
> loop would have been honest enough.

**What's actually stored in the checkpointer, and when?**
> The whole graph state, serialised after every node transition, keyed by
> `thread_id`. Not just the messages — every channel in the state. Per-node
> rather than per-turn is what makes resume-from-interrupt possible; you can
> stop between the agent node and the tool node and pick up exactly there.

**How do you resume?**
> `ainvoke(None, config)`. The `None` is the interesting part — it means "no new
> input, continue from saved state." LangGraph loads the checkpoint for that
> `thread_id` and carries on from where it stopped.

**What happens if the container restarts mid-conversation?**
> Nothing visible. State is in Postgres, not memory. That's not theoretical —
> the containers scale to zero when idle, so they *do* die between messages.
> In-process state was never an option.

**How are conversations isolated from each other?**
> `thread_id` is the key and the boundary. It's derived server-side from the
> authenticated caller, not accepted from the client — which was one of the bugs
> I found: `/approve` originally trusted a client-supplied `thread_id`, so you
> could approve someone else's pending write.

## On RAG and the document pipeline

**Walk me through your ingestion pipeline.**
> Documents live in Azure Blob. Azure AI Search has an indexer pointed at that
> container, and the indexer runs a skillset — a SplitSkill that chunks to 512
> tokens with 64 overlap, then an AzureOpenAI embedding skill running per chunk.
> Index projections fan the chunks out so the retrievable unit is a chunk, not a
> file. No chunking or embedding code runs in my application at all.

**Why 512 tokens with 64 overlap?**
> Small enough that a retrieved chunk is mostly signal instead of surrounding
> boilerplate, big enough to keep a clause intact. The overlap is so a rule that
> straddles a boundary isn't lost by both neighbours.

**How big is the corpus, and how does it stay fresh?**
> The demo corpus is ten policy documents — it's a demo. The pipeline is the one
> you'd run at ten thousand: blob indexers do incremental change detection on
> `LastModified`, so only changed files get re-cracked and re-embedded, on a
> schedule. What would actually bite at that scale is deletion handling — a
> deleted blob stays searchable unless you configure a soft-delete policy — and
> embedding quota on the ingest side.

**Why not just embed everything and skip SQL?**
> Because embeddings can't count. "How many fraud-flagged claims are open in the
> Southeast" isn't a similarity question — a vector index gives you the *k*
> nearest neighbours, it has no concept of "all rows matching X." Aggregates are
> a SQL problem wearing a natural-language costume.

**How do you stop top-k returning irrelevant results?**
> Filter first, rank second. For similar-claims I use `vectorFilterMode:
> preFilter`, which narrows to the caller's own claims *before* ranking. Without
> that, `k=3` gives you the three globally nearest claims, which may all be
> another adjuster's. The vector index is for ranking within a relevant set, not
> for finding the relevant set.

**Vector search alone, or hybrid?**
> Vector retrieval with Azure's semantic reranker on top — `queryType: semantic`.
> So two stages: vectors for recall, a cross-encoder reranker for precision.

**How do you evaluate retrieval quality?**
> I don't, formally, and that's the biggest gap in the project. I have no golden
> question set and no recall@k numbers — I tuned by reading outputs, which
> doesn't scale and isn't defensible. What I'd build is a labelled set of maybe
> a hundred adjuster questions with known-correct source documents, then measure
> recall@k on retrieval separately from answer quality, so I can tell a
> retrieval failure apart from a generation failure.

*(Don't bluff this one. Naming it clearly reads better than a vague answer.)*

## On NL2SQL

**What if the model writes `DROP TABLE`?**
> Three layers. The generated statement is inspected and rejected unless it's a
> single SELECT — that blocks DDL, DML and multi-statement. Then the connection
> itself is a Postgres role granted only SELECT and TEMPORARY, so if my parser
> is wrong, the database refuses. I didn't try to make the model safe; I made
> the blast radius small enough that it doesn't need to be.

**Why not just prompt it not to?**
> A prompt is a request, not a control. Anything that reaches the model — a
> retrieved policy document, a claim note a customer wrote — is untrusted input
> that can carry instructions. The enforcement has to be somewhere the model
> can't reach.

**What if it writes valid SQL for the wrong rows?**
> The query never touches base tables. It runs against TEMP VIEWs that already
> have the caller's authorization predicate baked in — same rule as the REST
> API's `scope_claims()`, deliberately mirrored so the same question gets the
> same rows either way. An adjuster can write a perfectly correct query for
> another region and get nothing.

**What about SQL that's syntactically valid but semantically wrong?**
> That's the hard one and it isn't fully solved. Two mitigations: value grounding
> before generation, so "Southeast" is mapped to the real column value rather
> than guessed; and a bounded retry that feeds the database error back for
> regeneration. But a query that runs and returns the wrong answer is silent,
> and the only real fix is an eval set. Same gap as retrieval.

**Why a subgraph rather than a function?**
> It gets its own state, its own retry loop, and node-level tracing, without any
> of that leaking into the orchestrator's state. The orchestrator sees one tool
> called `query_claims_data` and has no idea there's a five-node graph behind it.

## On MCP and OAuth

**You exposed tools publicly — how did you secure them?**
> It's an OAuth 2.1 resource server. It never logs anyone in and never issues a
> token; it validates tokens Entra issued, per request — signature against the
> JWKS, issuer, expiry, and audience. But authentication only answers *who*.
> It doesn't answer *what they're allowed to do*, and that part isn't finished —
> the three write tools shouldn't be on the public surface at all.

**Why offline validation instead of token introspection?**
> No network round trip per request. The signing keys are cached with a TTL, and
> an unknown `kid` forces a refetch, so key rotation self-heals without a
> deploy.

**What stops someone using a token for a different API in your tenant?**
> The audience check, and I can show you the test. A real Entra token from my own
> tenant, correctly signed and unexpired, minted for Microsoft Graph — gets a
> 401. That's the difference between validating a signature and validating that
> the token was meant for you.

**Would you give an external LLM write access?**
> No, and this is the thing I'd change first. The human approval gate lives
> inside the copilot's LangGraph. Claude calls the MCP server directly, so a
> write from Claude bypasses the gate entirely. The control isn't in the wrong
> place for the copilot — it's just not reachable from the other entry point.
> Writes should be internal-only.

**What about PII?**
> Real gap. `claim_summary` returns customer name, phone, email, VIN and policy
> number, and every one of those would land on a third-party provider's servers.
> A redaction layer belongs between the tool and the public surface, and until
> it exists that tool shouldn't be exposed.

**What was hardest about the OAuth work?**
> That the failures happen before your server is involved, so there's nothing to
> debug. The worst one: if you register the scope by its bare name instead of
> the `api://` form, Entra can't tell which API you mean, defaults to Microsoft
> Graph, and sign-in dies with an error code — my server never receives a
> request. I ended up building a standalone hello-world and a raw-ASGI request
> logger just to tell "the client never reached me" apart from "the client
> reached me and I rejected it."

**Is it live?**
> Token-based, yes — you can call it right now with a valid token. The browser
> sign-in from a Claude connector isn't, and the reason is specific: Entra only
> accepts a resource indicator that's a registered Application ID URI, and it
> won't register a domain you can't prove you own. Mine is on
> `azurecontainerapps.io`, which is Microsoft's. Production would use a company
> domain, which you'd want anyway.

## On infrastructure

**Why Container Apps over App Service, Functions, or AKS?**
> App Service was out — the subscription had a 0 vCPU quota. Functions I
> rejected on the 230-second execution cap, which doesn't fit a streaming
> transport. AKS is a cluster I'd have to operate for three containers. Container
> Apps scales to zero and has an always-free monthly grant, which is why the bill
> is zero.

**How does a code change reach production?**
> GitHub Actions builds on push and pushes SHA-tagged images to ghcr.io. Deploy
> is an explicit `az containerapp update` to that tag. The gap is there's no
> automatic rollout — I tag by SHA rather than chasing `:latest`, which I'd
> defend, but the missing piece is OIDC federation from Actions to Azure so the
> deploy step doesn't need a stored credential.

**Why ghcr.io and not Azure Container Registry?**
> ACR Basic is about five dollars a month standing, whether you push or not. On
> a ten-dollar total budget that single line item would have been most of it.
> ghcr.io is free for public images.

**How do you control cost?**
> No resources with standing charges, scale to zero, capped max-replicas as a
> blast-radius limit so a runaway loop can't scale into a bill, free registry.
> And checking the portal rather than trusting a script's exit code — that's how
> I caught a second billable Postgres server mid-provision and deleted it.

## The ones that separate candidates

**What's still broken?**
> The tool-surface split and PII redaction — biggest one. No retrieval eval. No
> automatic rollout. A known N+1 in claim summary, 174 queries and about seven
> seconds; I tried a preload, it didn't work, I reverted it rather than leave
> something half-done. And the identifier-URI wall on the OAuth browser flow.

**What would you do differently?**
> Infrastructure as code from day one. Everything in Azure was clicked or typed
> by hand, which means it isn't reproducible and I can't diff it. I found a
> semantic search configuration referenced in code that no setup script creates —
> somebody added it in the portal. That's exactly the class of drift IaC catches.

**What did you skip on purpose?**
> Microsoft Graph — it's users and mail, irrelevant to claims. Bot Service —
> different stack from LLM connectors. API Management — fifty dollars a month
> floor for a gateway I don't need yet. And VNet with private endpoints, which
> would be actively wrong here: connectors need public reachability, so locking
> it down would break the feature.

**What's the thing you're most pleased with?**
> That the database enforces read-only rather than the prompt. It's the one
> control that keeps working when everything above it is wrong.

---

# Part F — Three things to lead with

1. **"The database enforces read-only, not the prompt."** Shows you assume the
   model is compromised.
2. **"A valid Entra token from my own tenant gets a 401 — here's the test."**
   Shows the difference between configuring auth and verifying it.
3. **"Authentication answers who, not what. The write tools shouldn't be on the
   public surface at all, because the approval gate can't reach there."** Shows
   you can find the hole in your own design.
