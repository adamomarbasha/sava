# Sava — Performance & Scalability Audit

**Scope:** Backend (`api/`), ingestion pipeline, vector/retrieval layer, job queue, AI provider
routing, and the iOS networking + image-loading stack.
**Type:** Read-only audit. No production code was modified as part of this work.
**Audit date:** 2026-08-18
**Git context at time of audit:** branch `feat/intelligence-foundation`, HEAD `05192fc`
**Analysis range:** expected behaviour at 100 / 1,000 / 10,000 / 100,000 / 1,000,000 users

---

## 0. How to use this document (read this first if you are a future agent)

This document preserves a completed audit. It is **not** a task list that has been started, and
nothing in it has been implemented.

Structure:

- **§1 Provenance & staleness** — how much to trust the line numbers below.
- **§2 Verdict** — the one-paragraph conclusion.
- **§3 Current-state findings** — what the code does today. Factual. Includes measured data.
- **§4 Scaling behaviour per tier** — projected behaviour at each user count.
- **§5 Premature complexity** — things that exist and should be removed.
- **§6 Systems that stay simple** — things that are correct and should be left alone.
- **§7 Recommendations** — proposed changes. Explicitly separated from findings.
- **§8 Metrics & alert thresholds** — what to instrument.
- **§9 Ordered fix list with effort estimates.**
- **§10 Blockers, security issues, unresolved decisions, future-phase items.**
- **§11 Acceptance criteria** for each recommended fix.

**Rule for a future session:** §3 is observation, §7 is opinion. Re-verify §3 line numbers against
the current tree before acting (see §1). Do not treat §7 as approved work — it has not been
reviewed or scheduled.

---

## 1. Provenance & staleness warning

The audit was performed by reading the tree at HEAD `05192fc` on branch
`feat/intelligence-foundation`, with a large amount of uncommitted work present in the working
tree (the entire intelligence layer: `api/ai/`, `api/pipeline/`, `api/services/`,
`api/content/`, `api/jobs.py`, `api/vectors.py`, `api/worker.py`, `api/migrations.py`,
`api/routes_intelligence.py`, `api/config.py`, plus most of `ios/Sava/Features/`).

**Files were actively being edited during and immediately after the audit.** The following were
observed changing on disk mid-session:

- `api/config.py` — gained `YTDLP_COOKIES_FROM_BROWSER` / `YTDLP_COOKIEFILE`, later `ASYNC_SAVE`
- `api/pipeline/acquire.py` — gained `fetch_captions_via_ytdlp()`; `fetch_native_captions()` now
  prefers it over the legacy `transcript_service` path
- `api/main.py` — gained `ASYNC_SAVE` import and a `_clean_title()` helper
- `api/pipeline/ingest.py` — gained `from ..platform_budget import PlatformUnavailable, guarded`
  and a `_meta_from_info()` helper (a new `api/platform_budget.py` module now exists that was not
  present during the audit)
- `api/migrations.py`, `api/models.py`, `api/jobs.py`, `api/ai/telemetry.py`,
  `api/pipeline/handlers.py` — changed; diffs not captured

**Consequence:** every `file.py:NN` reference below was accurate at audit time but line numbers may
have drifted. The *substance* of each finding was verified by reading the code, not inferred.
Before acting on any finding, grep for the quoted code rather than trusting the line number.

**Explicitly re-verify these before acting**, since the areas around them were being edited:

- Whether `/api/transcript` still routes through `transcript_service` + the global rate limiter
  (Finding 5.4) — the ingestion-side captions path was migrated to yt-dlp, the HTTP endpoint was
  not, but that may have since changed.
- Whether `api/platform_budget.py` introduces a rate-limit / circuit-breaker layer that partially
  addresses Finding 3.13 (single static proxy, no rotation).
- Whether `ASYNC_SAVE` changes the blocking behaviour described in Finding 3.2c.

---

## 2. Verdict

**The architecture is right. The implementation has four defects that cap the product between
roughly 200 and 2,000 users, none of which are architectural, and all of which are cheap to fix.**

The good decisions — canonical cross-user content, a transactional DB-backed job queue, a
deterministic model router, chunk-level retrieval with MMR, lazy long-form summaries — are correct
and several of them buy years of not needing new infrastructure.

Past the four implementation defects, the first *genuine* architectural bottleneck is pgvector
filtered search, which bites at ~1,000–5,000 users.

There is also **one correctness blocker that fires at two users**, before any load consideration
applies.

Bottleneck ordering by when each bites:

| Order | Bottleneck | Threshold | Kind |
|---|---|---|---|
| 0 | `bookmarks.url` global UNIQUE | **2 users** | Correctness blocker |
| 1 | Event-loop blocking in `async def` handlers | ~20–50 concurrent users | Implementation |
| 2 | `bookmarks.raw` payload amplification (236 KB/row) | ~500–2,000 users | Implementation |
| 3 | pgvector filtered HNSW user-scoped search | ~1,000–5,000 users | **Architectural** |
| 4 | Ask Sava embedding fan-out (11× redundant calls) | Immediate (latency, not capacity) | Implementation |
| 5 | Worker fleet / local Whisper; single proxy exit | ~10,000 users | Capacity |
| 6 | Auth `LOWER(email)` seqscan; login storm | ~100,000 users | Implementation |
| 7 | Vector storage tier; proxy COGS | ~100,000–1,000,000 users | **Architectural** |

---

## 3. Current-state findings

All findings below describe **what the code does today**. Recommendations are in §7.

### 3.0 Baseline assumptions used for all projections

These are modelling assumptions, not measurements. They are stated so they can be challenged.

| Parameter | Value |
|---|---|
| Saves per user, lifetime | 200 |
| Saves per active user per day | 2 |
| DAU / MAU | 30% |
| Library opens / search / Ask Sava per DAU/day | 3 / 1 / 0.3 |
| Peak : average request ratio | 10× (mobile diurnal) |
| Avg short-form clip @ 480p through proxy | ~12 MB |
| Residential proxy price | $3.00/GB (from `api/ai/telemetry.py`, `USD_PER_PROXY_GB`) |
| Canonical dedupe (cache-hit) rate | 5% @ 100 users → 85% @ 1M users |

### 3.1 BLOCKER — `Bookmark.url` is globally unique

**Severity: Launch blocker. Fires at 2 users.**

`api/models.py:34`

```python
url = Column(Text, nullable=False, unique=True)
```

Confirmed present in the live SQLite schema as `sqlite_autoindex_bookmarks_1`.

Two different users cannot save the same URL. The second user's insert raises `IntegrityError`.
`api/ingestors/registry.py:137` and `:175` catch it with a substring check for
`"unique constraint"`, which matches both the Postgres message (`duplicate key value violates
unique constraint`) and the SQLite message (`UNIQUE constraint failed`), so the user receives a
409 "You already have this link bookmarked!" for a link they have never seen.

The application-level dedupe check at `api/ingestors/registry.py:83` is correctly scoped:

```python
existing = db.query(Bookmark).filter(Bookmark.url == url, Bookmark.user_id == user_id).first()
```

It is the **database constraint** that disagrees with the intent.

**Why this matters beyond a bug:** the entire `CanonicalContent` design exists so that the second
user to save a piece of content gets the first user's processed record for free. This schema makes
the second saver impossible. It negates the core economic premise of the product.

Note that `api/migrations.py:100` already creates a non-unique `idx_bookmarks_user_url` on
`(user_id, url)`, so the correct shape was already anticipated.

### 3.2 FIRST BOTTLENECK — event-loop blocking in `async def` handlers

**Severity: Critical. Threshold ~20–50 concurrent users.**

In FastAPI, a sync `def` route is dispatched to an `anyio` threadpool and is safe. An `async def`
route that performs blocking work stalls the **single asyncio event-loop thread**, halting every
other request in the process — including health checks.

Three handlers do exactly this.

#### 3.2a Thumbnail proxy — the worst instance

`api/main.py:493-503`

```python
@app.get("/api/thumbnail")
async def proxy_thumbnail(url: str):
    ...
    response = requests.get(url, stream=True)   # blocking; NO timeout
    ...
    return StreamingResponse(response.iter_content(chunk_size=8192), media_type=content_type)
```

`ios/Sava/Core/Images/ThumbnailURL.swift` routes **all** Instagram, Facebook-CDN and TikTok
thumbnails through this endpoint (`proxyHosts = ["fbcdn.net", "cdninstagram.com", "instagram.f",
"tiktokcdn", "tiktok.com"]`).

A library grid of 30 TikTok cards issues 30 concurrent requests. Each blocks the loop for the
upstream header round-trip (~200–800 ms to a CDN) and they serialize:
**~12 seconds of process-wide stall for one user opening their library.**

With no `timeout=`, a single hung CDN connection blocks the entire API **indefinitely**.

Three compounding problems on the same endpoint:

1. **Unauthenticated, no host allowlist** → open SSRF proxy and an open bandwidth relay
   (see §10 Security).
2. **No cache headers on the response** → the iOS `URLCache` (32 MB memory / 256 MB disk,
   `ios/Sava/Core/Images/ImageLoader.swift:19`) cannot store anything. Every scroll re-fetches
   through your server. The client-side image cache is inert for exactly the images that use it.
3. **No CDN in front** → egress is paid twice (CDN→origin, origin→phone) on every impression.

#### 3.2b Login — bcrypt on the event loop

`api/main.py:156,165`

```python
@app.post("/auth/login", response_model=Token)
async def login(user: UserLogin):
    ...
    if not verify_password(user.password, existing_user["password_hash"]):
```

Passlib bcrypt is ~100 ms of pure CPU, executed on the loop. **10 logins/sec = 100% event-loop
saturation.**

This compounds with `ACCESS_TOKEN_EXPIRE_MINUTES = 30` (`api/auth.py:13`) and **no refresh token**.
`ios/Sava/Features/Auth/SessionStore.swift:40-41` documents this explicitly: *"A 401 on any
authenticated call means the token expired (30-min TTL, no refresh). Fall back to signed-out
cleanly."* Every active user therefore re-authenticates roughly 48×/day.

| Users | DAU | Logins/day | Avg logins/s | Peak logins/s | bcrypt CPU-s/s at peak |
|---|---|---|---|---|---|
| 1,000 | 300 | 14,400 | 0.17 | 1.7 | 0.17 |
| 10,000 | 3,000 | 144,000 | 1.7 | 17 | **1.7 — loop saturated** |
| 100,000 | 30,000 | 1.44 M | 17 | 170 | **17** |

The event loop saturates on login alone at **~10,000 users**, independent of all other load.

#### 3.2c YouTube oEmbed during save

`api/ingestors/youtube.py:125` — `requests.get(oembed_url, timeout=8)` executed inside the `async`
ingestor chain reached from `async def create_bookmark`. It at least has a timeout, but it is up to
8 seconds of potential loop stall per save.

*(Re-verify: the new `ASYNC_SAVE` config flag may have changed this path.)*

### 3.3 SECOND BOTTLENECK — `bookmarks.raw` payload amplification

**Severity: High. Threshold ~500–2,000 users. Degrades continuously, not cliff-edged.**

Measured directly from `api/bookmarks.db` (101 bookmark rows):

```
avg(length(raw)) = 236,111 bytes
max(length(raw)) = 914,004 bytes
sum(length(raw)) =  23.8 MB      ← this is why the 101-row DB file is 49 MB
avg(length(description)) = 545 bytes
```

`api/main.py:337` uses `db.query(Bookmark)`, which SELECTs every mapped column — including `raw`.
**`raw` never appears in the response payload** built at `api/main.py:355-379`. It is read from
disk, sent over the wire, hydrated into a Python ORM object, and discarded.

Per `GET /api/bookmarks`:

| `limit` | Bytes read from DB and discarded |
|---|---|
| 100 (default) | **23 MB** |
| 500 (max allowed) | **118 MB** |

Projected waste:

| Users | Library loads/day | Wasted DB read/day |
|---|---|---|
| 1,000 | 900 | **20 GB** |
| 10,000 | 9,000 | **207 GB** |
| 1,000,000 | 900,000 | **20 TB** |

At 1M users × 200 saves, the `bookmarks` table is 200M rows × 236 KB = **~47 TB of JSON that is
never read**. Without `raw` the same table is roughly 60 GB.

**Compounding N+1 on the same endpoint:** `api/main.py:371-372` accesses
`bookmark.youtube_details`, a lazy relationship — one extra query per YouTube bookmark.
`limit=500` can issue up to 501 queries for a single request.

`CanonicalContent.metadata_json` is already capped at 60,000 chars in
`api/pipeline/ingest.py`, which is the right instinct applied to the new table but not the old one.

### 3.4 THIRD BOTTLENECK (first architectural one) — pgvector filtered HNSW

**Severity: High. Threshold ~1,000–5,000 users / ~100k–500k `content_embeddings` rows.**

`api/vectors.py:118-125` generates, for every semantic search:

```sql
SELECT canonical_content_id AS id, 1 - (embedding <=> :qvec) AS sim
FROM content_embeddings
WHERE canonical_content_id IN (
        SELECT canonical_content_id FROM bookmarks
        WHERE user_id = :uid AND canonical_content_id IS NOT NULL)
  AND embedding IS NOT NULL
ORDER BY embedding <=> :qvec LIMIT :k
```

The scope predicate is `_USER_SCOPE` in `api/services/retrieval.py:27-30`.

This is the classic pgvector filtered-search failure mode. HNSW is an approximate graph index that
**post-filters**. A user owning 200 of 6M canonical rows is a ~0.003%-selective filter. Postgres
then either:

1. walks essentially the entire HNSW graph to accumulate `k=60` surviving rows — and frequently
   returns **fewer than `k`**, silently degrading recall; or
2. rejects the index entirely and sequential-scans + sorts all of `content_embeddings`.

Both are O(corpus), not O(user library).

Projected `GET /api/search` p95:

| `content_embeddings` rows | Approx users | Expected p95 |
|---|---|---|
| 10k | 100 | 15 ms — fine |
| 150k | 1,000 | 80–200 ms — warning |
| 1M | 10,000 | **1.5–6 s — broken** |
| 6M | 100,000 | **10–60 s / timeout** |

Three things make this worse than it appears:

**(a) The Postgres path has never executed.** `api/requirements.txt` contains **no `pgvector`, no
`numpy`, and no `google-generativeai`**. Every `IS_POSTGRES` branch in `api/vectors.py` and
`api/migrations.py` is annotated `# pragma: no cover - needs live Postgres`. `numpy` is only
present transitively via `openai-whisper`. The entire production vector path is untested.

**(b) There is a live binding bug on that path.** `api/vectors.py:124`:

```python
params.update({"qvec": q.tolist(), "k": k})
```

A Python list is bound into a raw `text()` query. psycopg2 adapts a list to a Postgres `float8[]`,
and `vector <=> double precision[]` is not a defined operator. The first Postgres search will raise
`UndefinedFunction`. This requires either `CAST(:qvec AS vector)` with a string vector literal, or
`pgvector.psycopg2.register_vector()` registered on the connection.

**(c) The SQLite fallback is dev-only by design.** `api/vectors.py:127-155` bulk-fetches every
candidate vector into Python and does a single NumPy matmul. This is correct and fast for
development (and is a large improvement over the per-row cosine loop the docstring says it
replaced), but it loads the entire filtered set into memory: at 1536 dims × 4 bytes = 6 KB/vector,
10k vectors = 60 MB per query. Combined with SQLite's single-writer lock, this must not reach
production.

### 3.5 FOURTH BOTTLENECK — Ask Sava embedding fan-out

**Severity: High (latency + cost). Present at all scales; not capacity-limited.**

`api/services/retrieval.py:289`, inside `retrieve_for_library_question()`:

```python
saves = search_library(db, user_id, question, limit=max_saves, diversify=True)
...
for save in saves:                                        # 10 saves
    pieces = retrieve_chunks(db, save.canonical_id, question, k=chunks_per_save)
```

`retrieve_chunks()` (`api/services/retrieval.py:244`) calls `_embed_query(query)`
(`api/services/retrieval.py:70`) — a Gemini embedding HTTP round-trip.

**The same question is embedded 11 times per Ask Sava call** (once inside `search_library`, once
per retrieved save), sequentially.

A second N+1 sits in `search_library` itself, on the MMR diversification path
(`api/services/retrieval.py:210-217`):

```python
for cid, _ in ranked:                      # up to limit * 3 == 90 candidates
    row = db.execute(sql_text(
        "SELECT embedding FROM content_embeddings WHERE canonical_content_id = :c"
    ), {"c": cid}).first()
```

One query per candidate — up to 90 round-trips, each returning 6 KB. **This is on the Search path
too**, whose own module docstring states search *"must feel instant"*.

Measured latency budget for one Ask Sava call:

| Stage | Time |
|---|---|
| Query embedding (search) | 150–300 ms |
| Vector kNN | 20 ms → seconds (see §3.4) |
| Keyword LIKE scan | 30–150 ms |
| MMR embedding re-fetch — one query per candidate, up to 90 | **400–900 ms** |
| **10× redundant query embeddings** | **1,500–3,000 ms** |
| Gemini generation (`gemini-3.7-flash`, ~3k in / 1–3k out) | 2,000–6,000 ms |
| **Total p50** | **~4.5–9 s** |
| **Avoidable portion** | **~2.5 s (>30%)** |

### 3.6 Auth does a sequential scan on every authenticated request

**Severity: High. Threshold ~100,000 users (Postgres CPU saturation).**

`api/auth.py:35-42`, called by `get_current_user` on **every authenticated request**:

```python
def get_user_by_email(email: str):
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT id, email, password_hash, created_at FROM users "
                 "WHERE LOWER(email) = LOWER(:email)"),
            {"email": email.strip()}
        ).mappings().first()
```

Two problems:

1. The unique index is on `email` (plain, from `unique=True` in `api/models.py`), not on
   `lower(email)`. Postgres cannot use it for `LOWER(email) = ...` → **sequential scan of the whole
   `users` table per request**. At 100k users and 50 req/s that is 5M rows/s scanned purely for
   auth. Postgres CPU saturates on this before doing anything useful.
2. It opens its **own** `SessionLocal` outside the `get_db` FastAPI dependency, so every
   authenticated request checks out two connection-pool slots instead of one.

The JWT carries `sub` = email (`api/auth.py:26`), not `user_id`, which is why the lookup exists
at all.

### 3.7 Connection pool math caps API processes at 3

**Severity: Medium. Threshold ~10,000 users.**

`api/db.py:20-25`:

```python
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20, ...)
```

30 connections **per process**. Postgres default `max_connections` is 100. That is **3 API
processes** before exhaustion — and per §3.6 each request wants 2 slots, not 1.

Additionally, every long-running request (an Ask Sava call is 4.5–9 s) holds its `get_db` connection
for the full duration, including the entire blocking Gemini round-trip.

### 3.8 Worker capacity and Whisper model reloading

**Severity: Medium. Threshold ~10,000 users.**

`api/worker.py` runs `WORKER_CONCURRENCY` (default **2**, `api/config.py`) daemon threads in one
process, each polling `claim_next` every 2 s with backoff to 15 s.

Threads (rather than processes) are defensible here: `faster-whisper` releases the GIL in C,
`ffmpeg` is a subprocess, and `yt-dlp` is I/O-bound.

**But `api/pipeline/acquire.py:232` instantiates the model on every single call:**

```python
from faster_whisper import WhisperModel
model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE)
```

`WHISPER_MODEL` defaults to `small`, `WHISPER_DEVICE` to `cpu`, `WHISPER_COMPUTE_TYPE` to `int8`.
Model load is ~500 MB and 2–5 seconds — **per job**.

Per-job cost breakdown for an uncached save (yt-dlp download 5–15 s + ffmpeg frames + Whisper
transcription + 2–4 Gemini calls 5–10 s): **~45–70 seconds wall clock.**

| Users | Saves/day | Cache-miss rate | Uncached jobs/day | Job-hours/day | Workers needed (p95 wait < 5 min) |
|---|---|---|---|---|---|
| 1,000 | 600 | 70% | 420 | 7 | 2 (current default is fine) |
| 10,000 | 6,000 | 55% | 3,300 | 55 | **6–8** |
| 100,000 | 60,000 | 35% | 21,000 | 350 | **20–30** |
| 1,000,000 | 600,000 | 15% | 90,000 | 1,500 | **~65 sustained, ~150 peak** |

At 10,000 users, ~4 CPU-hours/day is burned on Whisper model loading alone.

`api/ai/telemetry.py:25-26` already prices the alternative:
`USD_PER_ASR_MINUTE_HOSTED = 0.04 / 60` (Groq `whisper-large-v3-turbo`) vs
`USD_PER_ASR_MINUTE_LOCAL = 0.0005`. Hosted ASR is ~$0.0007 per 60-second clip — a rounding error
next to $3/GB proxy bandwidth.

### 3.9 Job queue: no reaper, index/ORDER BY mismatch

**Severity: Low now, Medium at 100k+.**

`api/models.py` `Job.__table_args__`:

```python
Index("idx_jobs_claim", "state", "run_after", "priority")
```

`api/jobs.py:claim_next` orders by:

```sql
ORDER BY priority ASC, run_after ASC, id ASC
```

The index column order does not match the sort order, so the sort is not satisfied by the index.

More importantly, **`state='done'` rows accumulate forever.** There is no reaper anywhere in the
codebase. The claim query's index scan degrades progressively as completed rows come to dominate
the table. At 1M users that is 600k new job rows/day.

### 3.10 Job idempotency-key truncation mismatch

**Severity: Low (latent).**

`api/jobs.py:enqueue`:

```python
key = idempotency_key or f"{kind}:{json.dumps(payload, sort_keys=True, default=str)}"
existing = db.query(Job).filter(Job.idempotency_key == key).first()      # full key
...
job = Job(kind=kind, idempotency_key=key[:200], ...)                     # truncated key
```

The lookup uses the full key; the insert stores a 200-char truncation. For any key longer than 200
chars the lookup can never match the stored row → duplicate insert → unique violation → rollback →
`.first()` on the full key returns `None` → the caller silently receives `None` instead of a Job.

In practice the keys used today are short (`content.process:{id}`,
`collections.recluster:{user_id}`), so this is latent rather than active. It becomes live if any
handler ever enqueues with a payload-derived key.

### 3.11 Search keyword path uses leading-wildcard LIKE

**Severity: Low. Threshold ~5,000 saves per user.**

`api/services/retrieval.py:_keyword_scores` builds up to 8 `LIKE '%token%'` clauses across 8
columns spanning 3 joined tables (`bookmarks`, `canonical_content`, `content_understanding`).
Leading wildcards mean no index is usable. `LIMIT 200` is applied *after* the scan.

This is **user-scoped** (`WHERE b.user_id = :uid`), so it is bounded by the individual user's
library size rather than the global corpus — which is why it is not an early bottleneck. It becomes
a problem for power users past a few thousand saves.

`api/migrations.py` already creates a GIN full-text index on `canonical_content(title, description)`
for Postgres, but `_keyword_scores` does not use it — it uses `LIKE` on both engines for
portability.

### 3.12 `/users` endpoint leaks the entire user table

**Severity: Security + scalability. See §10.**

`api/main.py:196-199`:

```python
@app.get("/users")
def list_users(db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [{"id": u.id, "email": u.email, "created_at": u.created_at} for u in users]
```

No authentication, no pagination. At 1M users this is both a full-table dump of every registered
email address and an out-of-memory event.

### 3.13 Residential proxy: single static exit, no rotation

**Severity: High at 10,000+ users. Dominant COGS at 100,000+.**

`api/pipeline/acquire.py:_ydl_base_opts()`:

```python
proxy = os.getenv("SAVA_PROXY_URL")
if proxy:
    opts["proxy"] = proxy
```

A single static proxy URL for all yt-dlp calls. No rotation, no pool, no per-platform routing, no
bandwidth budget enforcement, no circuit breaker on repeated blocks.

*(Re-verify: the new `api/platform_budget.py` module — `PlatformUnavailable`, `guarded` — may
partially address this.)*

TikTok and Instagram throttle a single residential exit well below the required rate.

| Users | Uncached downloads/day | Sustained rate | Peak rate | GB/day | $/day @ $3/GB |
|---|---|---|---|---|---|
| 1,000 | 420 | 0.3/min | ~3/min | 5 | $15 |
| 10,000 | 3,300 | 2.3/min | ~15/min | 40 | **$120** |
| 100,000 | 21,000 | 15/min | ~150/min | 252 | **$756 ≈ $23k/mo** |
| 1,000,000 | 90,000 | 63/min | ~630/min | 1,080 | **$3,240 ≈ $97k/mo** |

`api/pipeline/acquire.py`'s own docstring states acquisition (proxy bandwidth) is **~78% of the
cost of a save** — far more than inference. **This is the dominant unit cost, and the canonical
dedupe cache is the only thing suppressing it.** An 85% dedupe rate at 1M users is the difference
between ~$97k/month and ~$650k/month.

### 3.14 AI provider quota ceiling

**Severity: Medium. Threshold ~100,000 users.**

Per uncached save: ~4 generation calls (classify, vision, understanding, plus summary on open) and
~15 embedding calls (one per chunk batch of 32, plus the document vector).

| Users | Gen calls/day | Embed calls/day | Peak gen RPM |
|---|---|---|---|
| 10,000 | 13,200 | 50,000 | ~90 |
| 100,000 | 84,000 | 315,000 | **~600** |
| 1,000,000 | 360,000 | 1,350,000 | **~6,000** |

A single Gemini project's paid RPM quota typically binds somewhere in the 1,000–4,000 range. This
starts to matter around 100,000 users.

`api/ai/router.py` already has the right mitigation shape — `FALLBACK_CHAIN = [BALANCED, CHEAP]`
degrades rather than failing — but there is no multi-project key rotation and no 429 backoff
beyond the fallback attempt.

### 3.15 Vector storage volume

**Severity: Architectural at 100,000+.**

1536 dims × 4 bytes = 6 KB per vector. A typical item produces ~15 chunks
(`CHUNK_TARGET_TOKENS = 180`, `CHUNK_OVERLAP_TOKENS = 30`) plus 1 document vector ≈ 96 KB.

| Users | Canonical items | `content_embeddings` (doc vectors) | `content_chunks` rows | Chunk vector bytes |
|---|---|---|---|---|
| 1,000 | 150k | 0.9 GB | 2.3M | 14 GB |
| 10,000 | 1M | 6 GB | 15M | **90 GB** |
| 100,000 | 6M | 36 GB | 90M | **540 GB** |
| 1,000,000 | 30M | 180 GB | 450M | **2.7 TB** |

HNSW graph overhead adds roughly the same again on top of the raw vectors.

**Critical observation:** `content_chunks` is the volume driver, and it is the table that should
not be globally indexed at all — see §5.1.

### 3.16 iOS client observations

**Generally well built.** Findings are limited.

- `ios/Sava/Core/Networking/APIClient.swift` — single typed entry point, 20 s request timeout,
  cancellation support, tolerant multi-format date parsing that never fails a payload on a bad
  timestamp, structured `APIError` mapping from status codes. `requestCachePolicy =
  .reloadIgnoringLocalCacheData` is correct for an API client.
- `ios/Sava/Core/Images/ImageLoader.swift` — `actor`-isolated `ImagePipeline` with `NSCache`
  (countLimit 200) in front of a `URLCache` (32 MB memory / 256 MB disk), cancellable loads.
  Correct design; **rendered inert for proxied thumbnails** because the server sets no cache
  headers (§3.2a).
- `ios/Sava/Features/Search/SearchViewModel.swift:31` — 320 ms debounce. Correctly tuned.
- `ios/Sava/Features/Auth/SessionStore.swift:40` — 30-minute token TTL with **no refresh**; a 401
  signs the user out. Drives the login storm in §3.2b.
- `ios/Sava/Features/Detail/ContentService.swift:10-16` — the detail view calls
  `POST /api/transcript` with `requiresAuth: false` on **every detail open**, fetching from YouTube
  live rather than reading the already-persisted `content_transcripts` row. See §5.4.
- No status polling was found for `processing_state`; `GET /api/bookmarks/{id}/status` exists in
  `api/routes_intelligence.py` but no iOS caller was located. This is a gap, not a bottleneck.

### 3.17 Data-state observations

- `api/bookmarks.db`: 19 users, 101 bookmarks, 50 youtube_details, 0 captions, 0 comments.
  **The intelligence-layer tables do not exist in this file at all** — the whole intelligence
  schema has never been created here.
- Root `bookmarks.db`: 1 user, 11 bookmarks; intelligence tables exist but **all are empty**
  (`canonical_content` 0, `content_chunks` 0, `content_embeddings` 0, `jobs` 0, `usage_events` 0).

**Consequence: the entire intelligence layer has never been exercised against any data volume.**
Every performance characteristic in §3.4, §3.5, §3.15 is a projection from code reading, not a
measurement.

---

## 4. Scaling behaviour per tier

### 100 users — 60 saves/day, 0.002 req/s

Everything works except the `url` UNIQUE blocker (§3.1) and the 50/hour global transcript limiter
(§5.4). SQLite is adequate. One API process, one worker.

**Action: none. Ship features.**

### 1,000 users — 600 saves/day, 0.07 req/s avg / 0.7 peak

- Event-loop stalls (§3.2) become visible as erratic p99 on library open.
- ~20 GB/day of discarded `raw` bytes; p95 on `/api/bookmarks` ~400–900 ms.
- Vector search 80–200 ms — acceptable, but the curve has turned.
- Worker: 420 uncached jobs/day × ~60 s = 7 job-hours/day. `WORKER_CONCURRENCY=2` clears this in
  ~3.5 wall-hours. Comfortable.
- **Must have migrated off SQLite by here** — §3.4(c) plus SQLite's single-writer lock.

**Threshold crossed: Postgres + PgBouncer.**

### 10,000 users — 6,000 saves/day, 0.07 req/s avg / 7 peak

Three thresholds cross simultaneously.

1. **Worker fleet.** 55 job-hours/day → 6–8 concurrent workers needed. One process with 2 threads
   is exhausted. Whisper model reloading (§3.8) burns ~4 CPU-hours/day on its own.
2. **Residential proxy.** 2.3 downloads/min sustained, ~15/min peak through a single static exit
   (§3.13). Platforms will throttle. Cost: ~$120/day.
3. **Vector search.** ~1M `content_embeddings` rows → search is 1.5–6 s. Broken.

Also: login rate ~1.7/s reaches event-loop saturation (§3.2b).

**Thresholds crossed: worker fleet ≥6, rotating proxy pool, vector redesign, hosted ASR,
refresh tokens, PgBouncer.**

### 100,000 users — 60,000 saves/day, 0.7 req/s avg / 7–70 peak

- **Auth becomes the hot spot** (§3.6): ~5M rows/s of sequential scan purely for authentication.
- **Login storm** (§3.2b): 17 CPU-seconds/second sustained on bcrypt — ~17 dedicated cores for
  password hashing even after moving it off the event loop.
- **Connection math** (§3.7): 3 API processes max on default Postgres. PgBouncer mandatory.
- **Storage** (§3.15): ~540 GB of chunk vectors plus comparable HNSW graph overhead.
- **Provider quota** (§3.14): ~600 peak RPM — a single Gemini project's quota starts to bind.
- **Proxy COGS** (§3.13): ~$756/day ≈ $23k/month. Now the largest single line item.

### 1,000,000 users — 600,000 saves/day, 7 req/s avg / 70 peak

Everything above ×10, plus two that change in kind:

1. **Proxy bandwidth ≈ $97k/month** at list price (1.08 TB/day). At this volume you renegotiate to
   bulk residential or build an owned fetch fleet. This is the dominant COGS and the number the
   entire canonical-cache design exists to suppress.
2. **~2.7 TB of raw chunk vectors** (450M rows). Dedicated vector tier required — though see §5.1:
   most of this should never have been globally indexed.

Additionally:
- Worker fleet: ~65 sustained concurrent workers, ~150 at peak.
- `bookmarks` table: 200M rows × 236 KB `raw` = **~47 TB**. Without `raw`, ~60 GB. The single most
  valuable byte you will ever delete.

---

## 5. Unnecessary premature complexity — candidates for removal

### 5.1 The HNSW index on `content_chunks`

`api/migrations.py:113-121` builds an HNSW index over every chunk vector:

```python
for table, col in (("content_chunks", "embedding"),
                   ("content_embeddings", "embedding"),
                   ("collections", "embedding")):
    _ensure_index(conn, f"hnsw_{table}",
        f"CREATE INDEX IF NOT EXISTS hnsw_{table}_{col} ON {table} "
        f"USING hnsw ({col} vector_cosine_ops) WITH (m = 16, ef_construction = 64)")
```

But **every** query against `content_chunks` filters on a single, highly selective equality
(`api/services/retrieval.py:246-251`):

```python
hits = knn(db, table="content_chunks", ..., where_sql="canonical_content_id = :cid", ...)
```

A canonical item has ~15 chunks. HNSW is exactly the wrong index for "find the top 6 of 15 rows":
a btree on `canonical_content_id` plus a brute-force dot product over 15 vectors is faster, exact,
and free.

**This index is the largest single storage and index-build cost in the system** — 450M entries at
1M users — and it serves no query.

Keep the `content_embeddings` HNSW index, which does serve a genuine cross-item search.

### 5.2 Local Whisper

`api/config.py`: `WHISPER_MODEL=small`, `WHISPER_DEVICE=cpu`, `WHISPER_COMPUTE_TYPE=int8`.
Roughly 0.3–0.5× realtime, plus a 2–5 s model load per call (§3.8).

`api/pipeline/acquire.py`'s own docstring acknowledges this: *"Kept local because this deployment
has no hosted-ASR credential. The audit recommends a hosted endpoint (Groq whisper-large-v3-turbo
at $0.04/hr) once a key exists; swapping this function is the only change required."*

**The local path exists because there was no key, not because it is better.** It is the largest
single constraint on worker fleet size and it removes itself for ~$0.0007 per clip.

### 5.3 Six TikTok ingestor implementations

`api/ingestors/` contains: `tiktok.py`, `tiktok_api.py`, `tiktok_old.py`, `tiktok_optimized.py`,
`tiktok_recovered.py`, `tiktok_broken_backup.py`, `tiktok_api_backup.py`.

`api/ingestors/registry.py:63-73` tries each registered TikTok ingestor in sequence, catching
exceptions:

```python
for ingestor in tiktok_ingestors:
    try:
        return await _create_new_bookmark(ingestor, url, user_id, db)
    except Exception as e:
        logger.warning(f"{type(ingestor).__name__} failed: {e}")
        continue
```

**Each failed attempt costs a full network round-trip through the paid proxy** before the next one
runs. Pick one implementation, delete the rest.

The same pattern exists for YouTube (`youtube.py`, `youtube_optimized.py`) and Instagram
(`instagram_api.py`, `instagram_api_old.py`), plus `registry_backup.py` and
`registry_optimized.py`. There is also a top-level `ingestors/` directory and a `recovered_files/`
directory duplicating `api/` modules.

### 5.4 Two competing captions paths

`api/pipeline/acquire.py:279` now correctly prefers `fetch_captions_via_ytdlp()` (shared session,
proxy, and cookies with metadata extraction) — this was changed during the audit and is the right
direction.

But `/api/transcript` — which iOS calls on **every detail open**, unauthenticated
(`ios/Sava/Features/Detail/ContentService.swift:14`) — still routes through the legacy
`api/transcript_service.py`, gated by `api/rate_limiter.py:44`:

```python
rate_limiter = RateLimiter(max_requests=50, time_window=3600)
```

Three separate problems with that limiter:

1. It is a **process-global, in-memory** counter — a hard 50-requests-per-hour ceiling shared by
   **all users combined**, regardless of user count.
2. It **resets on every deploy** and is not shared across processes, so it neither protects
   upstream nor behaves predictably.
3. It re-fetches from YouTube data that `content_transcripts` **already has persisted** by the
   ingestion pipeline.

Additionally, `api/transcript_service.py:21` imports it as `from rate_limiter import rate_limiter`
(absolute), while `api/main.py:25` imports `from .rate_limiter import rate_limiter` (relative) —
under some launch configurations these resolve to two distinct module instances with independent
counters.

### 5.5 `GET /users`

See §3.12. Delete.

---

## 6. Systems that can stay simple for a very long time

**Do not "improve" these. They are correct.**

### 6.1 The DB-backed job queue — the best decision in this codebase

`api/jobs.py`'s docstring claims "low thousands of jobs/minute on Postgres." That is right, and the
ceiling is one job per uncached save: **~15/minute at 1M users**. There are three orders of
magnitude of headroom.

**Do not introduce Celery, Redis, SQS, RQ, Dramatiq, or Temporal.** Ever, on the current
trajectory. The docstring's own reasoning is sound: a transactional queue inside the database you
already run gives durability, crash safety, idempotency as a UNIQUE constraint, one obvious place
to inspect stuck work, and zero new services.

Two small caveats, both already noted: the missing reaper and the index/ORDER BY mismatch (§3.9).

The `FOR UPDATE SKIP LOCKED` Postgres claim path in `api/jobs.py:claim_next` is correct.

### 6.2 Canonical content identity

`api/content/identity.py` is genuinely good work: per-platform ID extraction with validating
regexes, a 40-entry tracking-param strip list, host normalization (`www.`/`m.` stripping), a
hash fallback used only when an ID is genuinely unresolvable, and `upgrade_identity()` to merge a
short-link row into its resolved-ID row once known.

This is what makes the entire economic model work. It needs no changes at 1M users.

### 6.3 MMR

`api/vectors.py:mmr` — deterministic, no model involved, O(k²) over ≤90 candidates. Correct and
cheap forever. (The *fetching* of the vectors it operates on is the problem — §3.5 — not MMR
itself.)

### 6.4 k-means auto-collections

`api/services/collections.py:_kmeans` — k-means++ on unit vectors, no sklearn dependency, 40
iterations max. A user with 5,000 saves × 1536 dims × 8 clusters × 40 iterations is ~2.5 GFLOP,
about 1–3 s in NumPy, and it is already queued via `collections.recluster`. Fine to 1M users.

The cohesion guard (`cohesion < 0.55 → skip`) and `MIN_CLUSTER_SIZE = 3` are the right
noise-rejection defaults.

### 6.5 The model router

`api/ai/router.py` — regex question-shape classification (`_STRONG_REASONING`, `_WEAK_REASONING`,
`_SIMPLE_PATTERNS`) instead of a model call to decide which model to call. Correct, free, and
inspectable. The `FALLBACK_CHAIN` degradation strategy is right. The provider-neutral `Mode`
enum (Auto/Fast/Advanced) never leaks a vendor name to the client.

**Keep.** The one thing to add later is multi-project key rotation (§3.14).

### 6.6 Lazy long-form summary deferral

`LAZY_SUMMARY_OVER_SECONDS = 1200` in `api/config.py`, applied at `api/pipeline/ingest.py` L4.
Skipping generation for content most users never reopen is the highest-leverage cost decision after
dedupe itself.

### 6.7 Chunking

`api/pipeline/chunking.py` — overlapping, time-anchored windows sized to stay inside the
embedding model's 2,048-token limit, with nothing silently truncated. The docstring documents that
this replaced a `text[:20000]` truncation that was dropping ~60% of every long video. Correct.

### 6.8 iOS `APIClient` and `ImagePipeline`

See §3.16. Well built. The only defect is server-side (missing cache headers on the thumbnail
proxy), not client-side.

### 6.9 Telemetry / `usage_events`

`api/ai/telemetry.py` — one coherent entry point, best-effort writes that never break a user
request, per-operation and per-platform rollups. **The ledger already exists and nobody is reading
it.** See §8.

---

## 7. Recommendations

Everything in this section is a **proposal**. None of it has been implemented, reviewed, or
scheduled.

| Area | Recommendation |
|---|---|
| §3.1 blocker | Change `Bookmark.url` from `unique=True` to a composite `UniqueConstraint("user_id", "url")`. Requires a migration that drops the existing unique index. `idx_bookmarks_user_url` already exists as a non-unique index. |
| §3.2a | Drop `async` from `proxy_thumbnail` (moves it to the threadpool), add `timeout=(5, 30)`, add an explicit origin-host allowlist, and set `Cache-Control: public, max-age=604800, immutable` on the response. Long-term: put a CDN in front and stop proxying entirely where the CDN can sign requests. |
| §3.2b | Drop `async` from `login`. Add refresh tokens with a long-lived refresh + short-lived access token; update `SessionStore.handleUnauthorized()` to attempt refresh before signing out. |
| §3.2c | Wrap blocking ingestor network calls in `asyncio.to_thread()`, or make the ingestor chain fully sync and let FastAPI's threadpool handle it. |
| §3.3 | Add `.options(defer(Bookmark.raw))` (or an explicit column list) to `list_bookmarks`. Add `selectinload(Bookmark.youtube_details)`. Long-term: move `raw` to object storage keyed by `content_key`, or drop it entirely now that `CanonicalContent.metadata_json` exists. |
| §3.4 | (a) Add `pgvector`, `numpy`, `google-generativeai` to `requirements.txt`. (b) Fix the `:qvec` binding — either `CAST(:qvec AS vector)` with a string literal, or `register_vector()` on the connection. (c) Redesign filtered search: denormalize `user_id` onto the embedding row and index per-user, or enable pgvector 0.8 iterative index scans, or drop HNSW for `content_embeddings` and brute-force over the btree-filtered user subset (genuinely fast to a few thousand saves/user). |
| §3.5 | Hoist `qvec` out of `retrieve_for_library_question` and pass it into `retrieve_chunks`. Batch the MMR vector fetch into one `WHERE canonical_content_id = ANY(:ids)` query. |
| §3.6 | `CREATE UNIQUE INDEX ON users (lower(email))` — immediate mitigation. Then put `user_id` in the JWT claims and remove the per-request lookup entirely. Also reuse the request's `get_db` session rather than opening a second one. |
| §3.7 | PgBouncer in transaction mode. Re-tune `pool_size`/`max_overflow` per process afterwards. |
| §3.8 | Hoist `WhisperModel` to a module-level lazily-initialized singleton. Then migrate to hosted ASR (Groq `whisper-large-v3-turbo`, already priced in `telemetry.py`). |
| §3.9 | Add a reaper deleting `state='done'` rows older than 7 days. Reorder `idx_jobs_claim` to `(priority, run_after, id)` and make it partial: `WHERE state IN ('queued','running')`. |
| §3.10 | Truncate the key **before** the lookup, not only before the insert. |
| §3.11 | Use the existing Postgres GIN full-text index in `_keyword_scores` when `IS_POSTGRES`; keep the `LIKE` path for SQLite. Consider `pg_trgm` for substring matching. |
| §3.12 | Delete `GET /users`. |
| §3.13 | Rotating residential proxy pool with per-platform routing, per-exit failure tracking, and a circuit breaker. Enforce a bandwidth budget from `usage_events.proxy_bytes`. |
| §3.14 | Multi-project Gemini key rotation with 429-aware backoff, layered onto the existing `FALLBACK_CHAIN`. |
| §5.1 | Drop the `content_chunks` HNSW index. Replace with a plain btree on `canonical_content_id`; brute-force the ~15 chunks in Python or SQL. |
| §5.3 | Delete all but one implementation per platform. Remove `recovered_files/` and the duplicate top-level `ingestors/`. |
| §5.4 | Make `/api/transcript` read `content_transcripts` first and fall back to live fetch only on a miss. Require auth. Replace the in-process `RateLimiter` with a shared (DB or Redis) limiter, or delete it once the DB is the primary source. |

---

## 8. Metrics to monitor — with alert thresholds

### Tier 0 — instrument before the next deploy

| Metric | Source | Alert threshold |
|---|---|---|
| asyncio event-loop lag, p99 | API process | **> 50 ms** — the single highest-signal metric available. Catches every blocking-call regression, including future ones. |
| Bytes returned per DB query, p99 | SQLAlchemy `after_cursor_execute` hook | **> 5 MB** — catches the `raw` bloat (§3.3) and any future equivalent |
| Queries per HTTP request, p99 | SQLAlchemy event hook | **> 20** — catches every N+1 including §3.3 and §3.5 |
| Canonical cache-hit rate | `usage_events WHERE operation='content.cache_hit'` | **< 30% at 10k users** — if dedupe underperforms, the COGS model is wrong and nothing else matters |
| **USD per save** | `SUM(estimated_usd) / COUNT(DISTINCT bookmark_id)`, 7-day window | **> $0.08** — the ledger already exists; nobody is reading it |

### Tier 1 — before 1,000 users

| Metric | Alert threshold |
|---|---|
| `GET /api/search` p95 | **> 300 ms** — the pgvector cliff warning (§3.4) |
| `POST /api/ask` p95 | **> 8 s** |
| Queue depth (`jobs WHERE state='queued'`) | **> 200** sustained for 10 min |
| Oldest queued job age | **> 15 min** → add a worker |
| Job failure rate by `kind` | **> 5%** |
| SQLAlchemy pool checkout wait | **> 100 ms** → PgBouncer time |
| `/api/thumbnail` requests/min | any sustained volume → CDN time |

### Tier 2 — before 10,000 users

| Metric | Alert threshold |
|---|---|
| Proxy GB/day (`SUM(usage_events.proxy_bytes)`) | **> 50 GB/day** → rotating pool required |
| Acquisition failure rate by platform | **> 10%** → you are being IP-blocked |
| ASR seconds/day ÷ worker count | **> 0.5** → workers are ASR-bound; go hosted |
| Gemini 429 rate | **> 0.1%** → quota increase or key rotation |
| `content_embeddings` row count | **> 250k** → vector redesign now, not later |
| `jobs` table row count | **> 1M** → the reaper is not running |
| Login rate | **> 5/s** → refresh tokens now |

### Tier 3 — before 100,000 users

| Metric | Alert threshold |
|---|---|
| Postgres `seq_scan` count on `users` | **any value > 0** → the `lower(email)` index is missing |
| pgvector HNSW recall@10 (sampled offline against brute force) | **< 0.85** → post-filtering is silently dropping results |
| Vector index build time | **> 1 h** → partition |
| Per-user save-count p99 | **> 5,000** → the keyword `LIKE` scan needs GIN/trigram |
| Cost per DAU per day | **> $0.05** |

---

## 9. Ordered fix list

| # | Fix | Effort | Buys |
|---|---|---|---|
| 1 | `bookmarks.url` → `UNIQUE(user_id, url)` | 30 min | Cross-user cache actually functions |
| 2 | Drop `async` from `login` + `proxy_thumbnail`; add `timeout=` | 15 min | ~10× concurrency |
| 3 | `defer(Bookmark.raw)` + `selectinload(youtube_details)` | 30 min | ~1000× smaller library payload |
| 4 | Thumbnail proxy: host allowlist + `Cache-Control` + CDN | 2 h | Closes SSRF; removes most image egress |
| 5 | Hoist `qvec`; batch the MMR vector fetch | 1 h | −2.5 s on Ask Sava; −90 queries on Search |
| 6 | `CREATE UNIQUE INDEX ON users (lower(email))`; `user_id` in JWT | 1 h | Removes a per-request seqscan |
| 7 | Add `pgvector`, `numpy`, `google-generativeai` to requirements; fix `:qvec` binding | 2 h | Postgres path executes at all |
| 8 | Module-level `WhisperModel` singleton | 15 min | −3 s per ASR job |
| 9 | Refresh tokens | 4 h | Removes the login storm |
| 10 | `jobs` reaper + partial claim index | 2 h | Queue stays fast indefinitely |
| 11 | Drop the `content_chunks` HNSW index | 15 min | Removes the largest future index |
| 12 | `/api/transcript` reads `content_transcripts` | 2 h | Removes a global 50/hr ceiling |
| 13 | PgBouncer | 4 h | Past 4 API processes |
| 14 | Hosted ASR | 1 day | Removes the worker fleet constraint at 10k |
| 15 | Vector user-partitioning | 1 week | Past ~5k users |

**Items 1–8 total under two working days and take the product from roughly 200 users to roughly
5,000. Nothing in that set is architectural. The architecture is fine; the plumbing is not.**

---

## 10. Blockers, security issues, unresolved decisions, future-phase items

### 10.1 Launch blockers

| ID | Item | Reference |
|---|---|---|
| B1 | `Bookmark.url` globally unique — two users cannot save the same URL | §3.1 |
| B2 | Event-loop blocking in `login` and `proxy_thumbnail` — the API stalls process-wide under trivial concurrency | §3.2 |
| B3 | Postgres vector path has never executed and contains a known binding bug; `pgvector` is not even a declared dependency | §3.4 |

### 10.2 Security issues

| ID | Item | Reference |
|---|---|---|
| S1 | `GET /api/thumbnail?url=` is unauthenticated with no host allowlist — **open SSRF proxy** (can reach internal/metadata endpoints) and an **open bandwidth relay** | §3.2a |
| S2 | `GET /users` is unauthenticated and returns every registered email address, unpaginated | §3.12 |
| S3 | `SECRET_KEY` defaults to the literal `"your-secret-key-change-in-production"` in `api/auth.py:12` if the env var is absent — a missing env var silently produces forgeable JWTs rather than failing to boot | `api/auth.py:12` |
| S4 | `POST /api/transcript` is unauthenticated and triggers outbound network fetches on arbitrary user-supplied input | §5.4 |
| S5 | CORS uses `allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+"` with `allow_credentials=True` — correct for development, must not ship to production unchanged | `api/main.py:56-72` |

*Note: S1–S5 were observed during a performance audit and are recorded here for completeness. They
have not been through a dedicated security review.*

### 10.3 Unresolved decisions (require a human)

| ID | Decision |
|---|---|
| D1 | **Vector filtered-search strategy.** Three viable options (§7, §3.4): denormalize `user_id` onto embedding rows; pgvector 0.8 iterative index scans; or drop HNSW for `content_embeddings` and brute-force over a btree-filtered user subset. The third is simplest and adequate to a few thousand saves/user. No decision has been made. |
| D2 | **Hosted vs. local ASR.** Requires acquiring a Groq (or equivalent) credential. `api/ai/telemetry.py` already prices both. Cost is negligible; the blocker is procurement. |
| D3 | **`bookmarks.raw` retention.** Defer-on-read is the immediate fix. Whether to move it to object storage, keep a truncated copy, or delete it now that `CanonicalContent.metadata_json` exists is unresolved. |
| D4 | **Proxy vendor and rotation model** at 10k+ users. Materially affects COGS at 100k+ (§3.13). |
| D5 | **Whether `api/platform_budget.py`** (added after the audit) already addresses the proxy circuit-breaker need. Not evaluated. |
| D6 | **Access-token TTL and refresh-token design.** 30 min with no refresh is the current state; the replacement design is not specified. |

### 10.4 Future-phase items (deliberately deferred)

| ID | Item | Trigger |
|---|---|---|
| F1 | PgBouncer | ~4 API processes / ~10k users |
| F2 | Multi-project Gemini key rotation | ~600 peak RPM / ~100k users |
| F3 | Dedicated vector storage tier | ~250k `content_embeddings` rows |
| F4 | Worker fleet autoscaling on queue depth | ~6 sustained workers / ~10k users |
| F5 | GIN/trigram keyword search replacing `LIKE` | ~5,000 saves for any single user |
| F6 | Owned media-fetch fleet replacing rented residential proxies | ~$50k/month proxy spend |
| F7 | Deleting duplicate ingestor implementations and `recovered_files/` | Any time; pure hygiene |
| F8 | iOS polling/push for `processing_state` — the endpoint exists, no client consumes it | Product decision |

---

## 11. Acceptance criteria

For each recommended fix, what "done" means. These are proposed criteria, not agreed ones.

| Fix | Acceptance criteria |
|---|---|
| **1. `UNIQUE(user_id, url)`** | Two distinct users can each save the same URL and both receive a 201 with distinct bookmark IDs pointing at the same `canonical_content_id`. The same user saving the same URL twice still receives a 409. Migration is idempotent and does not delete rows. |
| **2. De-`async` blocking handlers** | Under 50 concurrent `/api/thumbnail` requests, p99 on an unrelated `GET /` health check stays under 100 ms. Event-loop lag p99 < 50 ms under the same load. `requests.get` has an explicit connect+read timeout. |
| **3. Defer `raw` + eager-load details** | `GET /api/bookmarks?limit=100` returns in < 150 ms p95 against a 10k-bookmark user, issues ≤ 3 SQL queries total, and transfers < 500 KB from the database. |
| **4. Thumbnail proxy hardening** | Requests to non-allowlisted hosts return 400. Response carries `Cache-Control: public, max-age≥604800`. A repeat load of the same library grid on iOS issues zero network requests for already-seen thumbnails. |
| **5. Ask Sava fan-out** | One `POST /api/ask` issues exactly **one** embedding call to the provider and ≤ 5 SQL queries for vector retrieval. p50 latency drops by ≥ 2 s versus the pre-fix baseline. |
| **6. Auth index / JWT `user_id`** | `EXPLAIN` on the auth lookup shows an index scan, not a seq scan. Postgres `pg_stat_user_tables.seq_scan` on `users` stops incrementing under load. Ideally the lookup is gone entirely. |
| **7. Requirements + `:qvec` fix** | `GET /api/search` returns non-empty, correctly-ordered results against a live Postgres+pgvector instance. A regression test exercises the Postgres path in CI. |
| **8. Whisper singleton** | Model load appears exactly once per worker process lifetime in logs. Median ASR job wall time drops by ≥ 2 s. |
| **9. Refresh tokens** | An idle client resumes after > 30 min without a re-login prompt. Login rate under steady-state load drops by ≥ 95%. |
| **10. Job reaper + index** | `jobs` table row count stays bounded under sustained load. `EXPLAIN` on `claim_next` shows no sort node. |
| **11. Drop chunk HNSW** | `retrieve_chunks` p95 is unchanged or better. Total index size on `content_chunks` drops to the btree only. Ask This answers are byte-identical on a fixed corpus (the results become *exact* rather than approximate). |
| **12. Transcript from DB** | Opening a detail view for an already-processed save issues zero outbound YouTube requests. The endpoint requires auth. The 50/hr limiter no longer gates the common path. |
| **13. PgBouncer** | 8+ API processes run without exhausting `max_connections`. Pool checkout wait p99 < 10 ms. |
| **14. Hosted ASR** | Uncached-save p95 end-to-end drops below 30 s. Worker count required for 6,000 saves/day falls to ≤ 3. |
| **15. Vector partitioning** | `GET /api/search` p95 < 300 ms against a corpus of ≥ 1M `content_embeddings` rows. Measured recall@10 ≥ 0.9 versus brute-force ground truth. |

---

## 12. Appendix — measured data

Captured 2026-08-18, read-only.

### `api/bookmarks.db` (49 MB)

```
users                        19
youtube_details              50
captions                      0
comments                      0
bookmarks                   101

avg(length(bookmarks.raw))         = 236,111 bytes
max(length(bookmarks.raw))         = 914,004 bytes
sum(length(bookmarks.raw))         =  23.8 MB
avg(length(bookmarks.description)) =     545 bytes
max(length(bookmarks.description)) =   2,375 bytes
```

Indexes present:

```
users            sqlite_autoindex_users_1              (auto, unique on email)
youtube_details  sqlite_autoindex_youtube_details_1    (auto)
youtube_details  idx_youtube_video_id                  UNIQUE (video_id)
comments         idx_comments_bookmark_created         (bookmark_id, created_at)
bookmarks        sqlite_autoindex_bookmarks_1          (auto, UNIQUE on url)   ← §3.1
bookmarks        idx_bookmarks_platform_created_at     (platform, created_at)
bookmarks        idx_bookmarks_user_created            (user_id, created_at)
bookmarks        idx_bookmarks_raw_gin                 (raw)   ← btree on a 236 KB column
```

**No intelligence-layer tables exist in this file.**

Note `idx_bookmarks_raw_gin`: declared in `models.py` with `postgresql_using='gin'`, but on SQLite
it materializes as a plain btree over a 236 KB average column. That index is itself large and
useless.

### Root `bookmarks.db`

```
bookmarks                   11
users                        1
youtube_details              0
captions                     0
comments                     0
canonical_content            0    ← all intelligence tables exist but are empty
collections                  0
jobs                         0
usage_events                 0
content_transcripts          0
content_frames               0
content_chunks               0
content_understanding        0
content_embeddings           0
collection_items             0
chat_threads                 0
chat_messages                0
```

**Conclusion: the intelligence layer has never been exercised against any data volume. Every
performance characteristic in §3.4, §3.5, and §3.15 is a projection from code reading, not a
measurement.**

### `api/requirements.txt` — declared dependencies

```
fastapi, uvicorn[standard], sqlalchemy, python-dotenv, pydantic, python-jose[cryptography],
passlib[bcrypt], python-multipart, psycopg2-binary, alembic, yt-dlp, TikTokApi, playwright,
pytest, pytest-asyncio, requests, email-validator, instaloader, youtube-transcript-api,
youtube-comment-downloader, openai-whisper, faster-whisper
```

**Missing and required by code that is already written:** `pgvector`, `numpy` (only transitive via
whisper), `google-generativeai`.
