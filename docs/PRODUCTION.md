# Production database requirements

Verified by running the whole test suite against PostgreSQL 16 + pgvector 0.8.1
(`449 passed, 0 skipped`), not inferred.

## Required

| Requirement | Why |
| --- | --- |
| PostgreSQL 14+ | `FOR UPDATE SKIP LOCKED` for the job queue |
| pgvector extension | `ContentEmbedding.embedding` is a `vector` column |
| Superuser or `CREATE EXTENSION` rights on first boot | `ensure_extensions()` creates `vector`, `pg_trgm`, `btree_gin` |

`api.db.ensure_extensions()` runs **before** `create_all`, because the embedding
table declares a `vector` column and the type must already exist. Managed
providers that ship pgvector (Neon, Supabase, RDS, the `pgvector/pgvector` image)
satisfy this.

## Running the suite against Postgres

    createdb sava_suite && createdb sava_pgtest
    SAVA_TEST_DATABASE_URL=postgresql://user@host/sava_suite \
    SAVA_TEST_POSTGRES_URL=postgresql://user@host/sava_pgtest \
    python -m pytest tests/ -q

`SAVA_TEST_DATABASE_URL` retargets the *entire* suite; `SAVA_TEST_POSTGRES_URL`
enables the Postgres-only tests in `tests/test_postgres.py`. They must be
different databases: `test_postgres.py` builds its own `sava_test` schema, and
`create_all(checkfirst=True)` would find the suite's tables through the search
path and skip creating them.

## Bugs this found that SQLite could not

1. `vector` was never created, and extensions ran *after* `create_all` — a first
   deploy against a fresh Postgres failed with `type "vector" does not exist`.
2. `knn()` bound the query vector as a Python list, which psycopg2 adapts to
   `numeric[]` — so `embedding <=> :qvec` raised `UndefinedFunction`. **Semantic
   search had never worked on Postgres.** The branch carried a
   `# pragma: no cover` marker admitting it was untested.
3. Reading an embedding back through raw SQL returns pgvector's text form
   (`[0.1,0.2,…]`), which the decoder did not handle — every related-saves
   lookup raised `ValueError`.
4. `record_feedback()` swallowed all exceptions. SQLite does not enforce foreign
   keys by default and Postgres always does, so a stale `bookmark_id` silently
   discarded the user's correction and the next rebuild undid their change.
