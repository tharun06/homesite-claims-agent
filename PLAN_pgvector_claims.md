# Plan: move claims similarity search from Azure AI Search to pgvector

You're driving this one — this is the reference doc so you don't have to
scroll back through the conversation that produced it. Policy document search
is **not touched** by any of this; it stays on Azure AI Search.

## What this achieves

Claims similarity search (`search_similar_claims`) currently duplicates every
claim into a second store — `claims-index` on Azure AI Search — kept in sync
by `seed_claims.py`, a script with no trigger, capped at 500 claims, no
delete path. Moving it onto the Postgres database that already holds the
claims removes that duplication entirely, and turns the authorization filter
from an enumerated list of claim numbers (which has a hard length ceiling)
into a normal `WHERE` clause using the exact `scope_claims()` function
already used everywhere else in the app.

## Design decisions already made, and why — don't relitigate these mid-build

- **The backend gets no new external dependencies.** No Azure OpenAI
  credentials on the backend, no embedding call on any request path. This was
  the original plan and it was wrong — see "corrections" below.
- **A claim's vector is built once and never needs to change.** The embedded
  content includes only facts that are fixed at creation — peril type,
  vehicle, incident location. It deliberately **excludes** `status` and
  `estimated_amount`, which change constantly. Those stay as plain columns,
  read alongside the vector match, never embedded.
- **Hybrid search (keyword + vector) has to be rebuilt by hand.** Azure AI
  Search did BM25 + vector + RRF + a cross-encoder rerank in one call. None
  of that is native to Postgres. This is a real, accepted quality tradeoff —
  worth making here because claims similarity is a soft, secondary feature,
  not a compliance-sensitive one like policy search.
- **The old `claims-index` is left in place, not deleted**, as a rollback
  path and a way to compare old vs. new results during verification. Delete
  it later, once this is proven.
- **Going straight at the real Postgres server**, not a local Docker
  sandbox — this is synthetic demo data, zero real stakes, and the extra
  environment wasn't worth the detour.

### Corrections made while planning this (keep these in mind)

1. First plan put a re-embed hook on `PATCH /claims/{id}/status`, requiring
   the backend to get Azure OpenAI credentials for the first time in the
   project. Rejected: it breaks the backend's narrow data+auth-only
   responsibility, and it couples a business transaction's latency/reliability
   to an external, rate-limited API — the same class of problem already
   fixed elsewhere in this project (the copilot's 429 story).
2. Realized the actual fix wasn't a smarter reconciliation job — it was **not
   embedding volatile fields in the first place.** `status` and
   `estimated_amount` never needed to be inside the vector; they're already
   columns. Once that's fixed, there is nothing to re-embed after creation,
   and the whole hook/job discussion becomes moot.

## Connection details for the real server

```
host:     homesite-pg-westus3-1985.postgres.database.azure.com
port:     5432
database: claims
user:     pgadmin
sslmode:  require
```

Password: yours, not stored anywhere in this session. Your current IP
already has a firewall rule (`allowdev`), so no networking setup is needed.

---

## Phase 1 — enable the extension

```sql
CREATE EXTENSION IF NOT EXISTS vector;
\dx                                          -- confirm it's listed
```

If that fails on permissions: `vector` needs to be added to the
`azure.extensions` server parameter first —

```bash
az postgres flexible-server parameter set --name azure.extensions \
  --value VECTOR -s homesite-pg-westus3-1985 -g rg-homesite-claims
```

— possibly followed by a restart, then retry `CREATE EXTENSION`.

**Verify:** `\dx` lists `vector`.

## Phase 2 — schema

```sql
ALTER TABLE claim ADD COLUMN content_vector vector(1536);
ALTER TABLE claim ADD COLUMN content_tsv tsvector;
CREATE INDEX ON claim USING hnsw (content_vector vector_cosine_ops);
CREATE INDEX ON claim USING gin (content_tsv);
```

Both columns are `NULL` for every existing row — expected, backfill is Phase 4.

**Verify:** `\d claim` shows both columns and both indexes.

## Phase 3 — backend code

Three files, `adjuster-dashboard/backend/`:

- **`requirements.txt`** — add `pgvector`.
- **`app/models.py`** — extend `Claim` with the two new columns via
  `sa_column=Column(...)`. Types: `pgvector.sqlalchemy.Vector(1536)` and
  `sqlalchemy.dialects.postgresql.TSVECTOR`.
- **A new content-builder** — write fresh, don't reuse
  `azure_setup/seed_claims.py`'s `build_content()` — that one bakes in
  `status`/`estimated_amount`, which is the exact thing being fixed. Use only
  `peril_type`, vehicle fields, `incident_city`/`incident_state`.
- **`app/api/routes_claims.py`** — new `POST /claims/similar`. Same pattern
  as any existing endpoint: `select(Claim)` → `scope_claims(query, user,
  session)` → then order by `content_vector <=> :embedding` for the vector
  leg, and separately filter/rank on `content_tsv @@
  plainto_tsquery(:query)` for the keyword leg. Fusing the two: simplest
  honest approach at this scale is two ordered queries merged by rank in
  Python — no need for anything fancier.

**Verify:** call the new endpoint directly (once deployed, or locally against
the real DB) with a hand-built embedding and confirm it returns rows scoped
correctly per role.

## Phase 4 — backfill (one-time script)

New script, sibling to `seed_claims.py` in spirit (fetch → build content →
embed → write) but writing straight into Postgres columns instead of pushing
to Azure AI Search, and using the new stable-only content-builder from Phase
3. Run with local `.env` Azure OpenAI credentials — no new secrets anywhere
in deployed infrastructure.

**Verify:** `SELECT count(*) FROM claim WHERE content_vector IS NOT NULL;`
matches the total claim count.

## Phase 5 — MCP server rewrite

`adjuster-dashboard/copilot/mcp_server.py`, `_search_similar_claims`:
currently builds a request to Azure AI Search's `claims-index`. Change it to
embed the query (existing `_embed()` call, unchanged) and call the new
`POST /claims/similar` on the backend instead — same shape as how `_get`
already talks to the backend elsewhere in this file.

**Verify:** a real `search_similar_claims` call through the copilot or MCP
client returns claims, and a different adjuster's account never sees another
adjuster's claims in the results.

## Phase 6 — verify, then deploy

- Confirm scoping holds — two different adjusters, same query, disjoint or
  correctly-overlapping results per role.
- Compare a few queries against what the old Azure AI Search path would have
  returned (still live, untouched) as a sanity check.
- Redeploy `homesite-backend` and `homesite-mcp`.
- Worth adding to the eval suite afterward — claims retrieval has never
  actually been measured the way policy retrieval has
  (`eval/deepeval/test_retrieval.py` has no claims equivalent yet).

## Leave alone until this is proven

`claims-index` on Azure AI Search — nothing reads it once Phase 5 lands, but
don't delete it. It's the rollback path.
