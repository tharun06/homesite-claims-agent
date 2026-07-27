# Plan — Schema Linking at Scale (the hybrid table picker)

**Status:** future task. The simple version already exists (`select_tables` node
in `sql_graph.py` — the LLM picks tables from a catalog). This plan adds an
embedding pre-filter *in front* of it so it scales to 50+ tables.

## The problem it solves
`generate_sql` needs the schema in its prompt. At 3 tables we send them all. But
a real warehouse has 50–500 tables:
- **Prompt limit** — you physically can't fit every table's columns in context.
- **Cost** — a huge schema on every call is expensive.
- **Accuracy** — the more irrelevant tables the LLM sees, the more it hallucinates
  joins and picks wrong columns. Fewer, relevant tables = better SQL.

So before generating SQL you must **select only the relevant tables** ("schema
linking"). The question is *how* to select them.

## Why hybrid (not embeddings-only, not LLM-only)
| Approach | Problem |
|---|---|
| Send all tables | Breaks past the prompt limit; expensive; hurts accuracy |
| LLM picks from all tables | The expensive call still has to *read* all 50+ every time |
| Embeddings only | Cheap + scales, but misses non-obvious relevance (e.g. "overdue" → the SLA-date column isn't lexically similar) |
| **Hybrid (chosen)** | Embeddings do the cheap bulk cut; the LLM does the smart final cut on a small set |

## The hybrid picker
1. **Precompute once** (re-run only when the schema changes): embed every table's
   description into a vector.
2. **Per query — narrow (embeddings):** embed the question, cosine-similarity vs.
   the table vectors, take **top ~15**. Cheap, deterministic, scales past the
   prompt limit.
3. **Per query — refine (LLM):** hand those ~15 to the LLM to pick the final **~5**,
   catching the non-obvious relevance embeddings miss (e.g. "overdue" → SLA-date
   column). *The expensive call never sees more than 15 tables, never 50+.*

## Key insight
This is just **RAG over the schema**: tables are "documents", their descriptions
are the content, table selection is retrieval. It reuses the exact stack we
already have for `search_policy_docs`:
- `_embed()` (Azure `text-embedding-3-small`) to vectorize descriptions + question.
- Azure AI Search to store and query them. At scale, index the table descriptions
  in their **own AI Search index** and query it per question.

## How it plugs into the current graph
The current node:
```
ground_values → select_tables (LLM picks) → generate_sql
```
becomes:
```
ground_values → select_tables → generate_sql
                 │  narrow: embeddings → top ~15   (new stage)
                 └  refine: LLM → final ~5          (existing logic)
```
`select_tables` keeps the same output contract — it still returns
`{"tables": [...]}` into `SqlState` — so `generate_sql` / `_render_schema(tables=...)`
need **no change**. Only the *inside* of `select_tables` gains the embedding pre-cut.

## Build steps (when the schema outgrows ~15–20 tables)
1. `build_table_index()` — for each table, `_embed(description)`; store
   `{name, vector}`. Cache to disk (or push to a dedicated AI Search index).
   Re-run only on schema change.
2. In `select_tables`: `_embed(question)` → cosine vs. table vectors → keep top-K
   (~15).
3. Pass only those ~15 catalog lines to the existing LLM refine step → final ~5.
4. Fallback: if embeddings return nothing (cold cache), send the full catalog to
   the LLM (current behavior).

## Don't build it yet
At 3 tables this is pure overhead — the embedding cut has nothing to remove. It
earns its keep only once the catalog is too big to send whole. Ship the simple
`select_tables` now; add the embedding pre-filter when the table count demands it.
