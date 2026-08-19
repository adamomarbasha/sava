# Sava Search — Audit & Design Specification

**Area:** Library Search (hybrid retrieval pipeline + Search vs Ask Sava UX)
**Date:** 2026-08-18
**Branch at time of audit:** `feat/intelligence-foundation`
**Type:** Read-only audit + forward design. **No code was written or modified.**
**Status:** Design approved for reference; nothing implemented.

---

## 0. How to use this document

This is a standalone specification. A future session with no memory of the
originating conversation should be able to act on it directly.

- **Part A — Current State** is what the code actually does *as audited*. Treat
  every claim as verified-at-the-time but re-verify line numbers before acting;
  the repo was under active development during the audit.
- **Part B — Design** is the recommended target architecture. Nothing in Part B
  exists yet.
- **Part C — Execution** carries phases, acceptance criteria, blockers,
  security invariants, and unresolved decisions.

Anything marked **BLOCKER**, **SECURITY**, or **DECISION** needs a human before
it is implemented.

### Files audited

```
api/services/retrieval.py       hybrid search + Ask Sava retrieval  (~331 lines)
api/services/intelligence.py    summary / ask_this / ask_sava / resurface
api/services/collections.py     manual + auto collections
api/models.py                   full schema incl. intelligence layer
api/vectors.py                  portable pgvector / SQLite kNN + MMR
api/config.py                   embedding model/dims, pipeline + vision policy
api/routes_intelligence.py      /api/search, /api/ask, collections, ops
api/pipeline/understanding.py   content classification + entity extraction
api/pipeline/chunking.py        transcript/text chunking + doc text builder
api/pipeline/frames.py          frame selection, OCR, vision
api/pipeline/ingest.py          orchestration, chunk modalities
api/content/identity.py         URL normalization + content_key dedupe
api/main.py                     legacy GET /api/bookmarks?q= search
ios/Sava/Features/Search/*      SearchView, SearchViewModel
ios/Sava/Features/Library/*     BookmarkService, BookmarkCard, grid
ios/Sava/Features/Shell/*       AppShell tab bar
```

### Product context (needed to read the rest)

Sava saves social-media content (TikTok / YouTube / Instagram / 9 tiered
platforms) and enriches it: transcript, sampled frames, OCR, vision captions,
structured entity extraction, and embeddings. A **two-layer schema** is central:

- `CanonicalContent` — one row per real piece of content in the world, **shared
  across all users**. All expensive processing hangs off this, so it is paid
  for once globally.
- `Bookmark` — the per-user save, with a nullable FK into `CanonicalContent`.

This sharing is the reason user-scoping is a **security-critical** concern in
every retriever (see §C.3).

---

# PART A — CURRENT STATE FINDINGS

## A.1 What already works well

The foundation is genuinely good. The hard, expensive parts are built:

| Asset | Location | Notes |
|---|---|---|
| Two-layer content/save schema | `models.py` | Process-once-globally is correct and already implemented |
| Multi-modal retrievable chunks | `ContentChunk` | modality ∈ transcript / caption / vision / OCR, time-anchored |
| Structured understanding | `ContentUnderstanding` | `entities` (people, brands, products, places, foods, ingredients, activities, prices, dates, urls, key_facts, recommendations) + `typed_data` per content type |
| Frame-level artifacts | `ContentFrame` | `ts_ms`, `phash`, `ocr_text`, `vision_caption`, `stored_key` |
| Doc-level vectors | `ContentEmbedding` | one per canonical item |
| Portable vector search | `vectors.py` | pgvector HNSW on Postgres; packed float32 + single NumPy matmul on SQLite. `knn()` already accepts `where_sql` + `params` |
| Correct normalization | `vectors.py:normalize` | Matryoshka-truncated gemini-embedding-001 at 1536 dims requires explicit L2 normalization — this is handled |
| No-silent-truncation chunking | `chunking.py` | Overlapping windows sized to the 2048-token embed limit |
| Content-type taxonomy | `understanding.py` | 16 types + `VISUAL_DEPENDENCY_PRIOR` per type |
| Job queue | `jobs.py`, `models.Job` | DB-backed, idempotency as a UNIQUE constraint, `FOR UPDATE SKIP LOCKED` on PG |
| Cost ledger | `UsageEvent`, `ai/telemetry.py` | per-operation unit economics |
| Correct stated invariant | `retrieval.py` docstring | "Search is retrieval only — no model runs. Ask Sava retrieves first, then hands *only* the selected context to a model." |

The architecture already asserts the right product boundary. The problem is that
search does not yet use the layer that was built for it.

## A.2 Findings (severity-ordered)

### F1 — **BLOCKER** · iOS Search never calls `/api/search`

`SearchViewModel.run()` calls `BookmarkService.list(query:)`, which issues
`GET /api/bookmarks?q=`. That endpoint (`api/main.py` ~line 345) is a 4-column
`ILIKE '%q%'` over `Bookmark.title / author / description / note` only.

**Impact:** zero percent of the intelligence layer — embeddings, entities,
transcripts, OCR, vision — reaches the user. Every quality claim about search is
currently false in the shipped client.

**Severity:** Blocker. Also the single highest-leverage fix in the repo: one
client-side endpoint change moves search from "substring match on 4 columns" to
"hybrid vector + keyword".

*Refs:* `ios/Sava/Features/Search/SearchViewModel.swift` (`run`),
`ios/Sava/Features/Library/BookmarkService.swift` (`list`), `api/main.py:330-351`.

### F2 — **BLOCKER** · Filters are applied *after* ranking and truncation

In `retrieval.search_library`, `platform` and `content_type` are filtered inside
the loop that iterates `ranked` — which has already been truncated to `limit`.

```python
ranked = sorted(...)[: limit * 3]
ranked = mmr(ranked, vecs, k=limit, ...)      # now exactly `limit` items
...
for cid, score in ranked:
    if platform and save.platform != platform:   # POST-filter
        continue
```

**Impact:** filtering to TikTok can return 3 results when the user has 40
matching TikToks. It fails silently and reads to the user as missing data — the
worst class of search bug because it destroys trust in the corpus, not just the
ranking.

**Fix direction:** push every constraint into candidate generation. `knn()`
already takes `where_sql`/`params`; the plumbing exists and is unused for user
filters.

*Refs:* `api/services/retrieval.py` (~lines 220-245).

### F3 — **HIGH** · Incomparable score scales fused by weighted sum

```python
fused[cid] = 0.72 * semantic + 0.42 * keyword
```

`semantic` is a raw cosine from gemini-embedding-001, which clusters in roughly
**0.60–0.85 for essentially everything** — very low dynamic range. `keyword` is
a hit-ratio in [0,1] with high dynamic range.

**Impact:** the semantic term behaves as a near-constant offset. Ranking is
effectively keyword-only with a semantic tiebreak. The system is not actually
hybrid despite being structured as one.

**Fix direction:** Reciprocal Rank Fusion (scale-free) instead of weighted sum
of raw scores.

*Refs:* `api/services/retrieval.py` (~line 214).

### F4 — **HIGH** · No chunk-level or frame-level recall in library search

`search_library` searches only `content_embeddings` (document level) plus the
`LIKE` scan. It never touches `content_chunks` (including
`modality='vision'`) or `content_frames.vision_caption` / `ocr_text`.

Worse, the doc vector is built by `chunking.build_document_text`, which
deliberately favours distilled signal and caps OCR at `ocr_text[:800]` — correct
for its stated purpose (finding the right *save*), but it means fine-grained
visual detail is diluted out of the only vector search actually runs.

**Impact:** visual queries are structurally unanswerable. "girl holding pink
moisturizer" exists only in frame vision captions, which are never searched.

*Refs:* `api/services/retrieval.py:196-215`, `api/pipeline/chunking.py:build_document_text`.

### F5 — **MEDIUM** · MMR applied to search results, plus an N+1 query

`search_library` runs `mmr(..., lambda_=0.75)` over results, and to do so issues
**one `SELECT` per candidate** to fetch each embedding.

Two separate problems:

1. **Diversity is wrong for search.** MMR exists to stop Ask Sava from citing
   the same video five times. But a *search* for "red dress" should return every
   red dress. Suppressing similar items is precisely the wrong behaviour for a
   recall-oriented surface. What search actually needs is **exact dedup**
   (`content_key`, frame `phash`), not marginal-relevance diversification.
2. **N+1 query** in the hot path of the latency-critical endpoint.

*Refs:* `api/services/retrieval.py:222-232`, `api/vectors.py:mmr`.

### F6 — **MEDIUM** · Unindexed substring LIKE as the entire lexical retriever

`_keyword_scores` builds up to 8 tokens × 8 columns of `LOWER(col) LIKE '%tok%'`
with no supporting index.

**Impact:**
- Full scan on every keystroke-driven search.
- Substring semantics: `art` matches `st**art**`; `ice` matches `pol**ice**`.
- No phrase matching, no prefix matching, no field weighting, no stemming, no
  BM25-style saturation.

*Refs:* `api/services/retrieval.py:70-104`.

### F7 — **MEDIUM** · `matched_on` ships internal vocabulary to the UI

`save.matched_on` returns literally `["semantic", "keyword"]`.

**Impact:** explains nothing to a user. "Why is this here?" is the core trust
question in a search product, and the answer currently returned is an
implementation detail.

*Refs:* `api/services/retrieval.py:249`.

### F8 — **MEDIUM** · Empty-query browse ignores filters entirely

The `if not query:` branch returns recent bookmarks ordered by `created_at` and
never consults `platform` or `content_type`.

**Impact:** "show me my TikToks" (no text, filter only) returns everything.

*Refs:* `api/services/retrieval.py:170-190`.

### F9 — **HIGH (data)** · The intelligence layer has never been run

Local `bookmarks.db` state at audit time:

| Table | Rows |
|---|---|
| `bookmarks` | 11 |
| `canonical_content` | 0 |
| `content_chunks` | 0 |
| `content_understanding` | 0 |
| `content_frames` | 0 |
| `content_embeddings` | 0 |
| `content_transcripts` | 0 |
| `collections` | 0 |

**Impact:** every ranking change is currently untestable, and the *live product
state* is "no save has been analyzed". This must be reflected in the zero-result
UX (§B.9) rather than presented as "no matches".

**Action required before any ranking work:** backfill by enqueuing
`content.process` over existing saves (`POST /api/bookmarks/{id}/reprocess`
already does this per-item).

### F10 — **LOW/UX** · Client search behaviour

- Debounce is 320 ms and triggers a **full search** per keystroke — simultaneously
  too slow to feel like typeahead and too expensive to run per character.
- Recent searches are local-only `UserDefaults` with **no outcome tracking**, so
  queries that returned nothing get re-suggested forever.
- Zero-result copy is a single generic string; it cannot distinguish "empty
  library" from "nothing analyzed yet" from "no match" from "no match after
  filters".
- The zero-query state is a static paragraph plus hardcoded example queries
  (`"cooking"`, `"Japan"`, `"basketball"`) that will return nothing for most
  libraries.
- `GET /api/resurface` already exists and is fully unused by the client.

*Refs:* `SearchViewModel.swift` (~line 34), `SearchView.swift` (idle/empty states).

---

# PART B — TARGET DESIGN

## B.1 Product definition

> **Search answers "where is it?"  Ask Sava answers "what do I know about…?"**

**Search** is a retrieval surface. Zero generative models, real saves returned,
fast enough that the user never *decides* whether to wait. Unit of output: an
**item**. Failure mode: a wrong card — costs a glance.

**Ask Sava** is a synthesis surface. Unit of output: a **sentence**. Failure
mode: a wrong claim — costs trust. It must therefore look, feel, and be paced
differently.

The invariant already asserted in `retrieval.py`'s docstring is the right one and
this design preserves it.

## B.2 Query taxonomy (the seven driving queries)

| Query | Intent | Winning signal | Answerable today? |
|---|---|---|---|
| "red dress" | visual attribute + object | visual entities (object+color), OCR overlay, `entities.fashion` | ✗ no visual index |
| "vodka pasta recipe" | typed lookup | `content_type=recipe` + `entities.foods/ingredients` + `typed_data.recipe.dish` | partial (LIKE over entity JSON) |
| "Kai Cenat stream" | navigational (creator) | `creator_handle` / `creator_name`, `entities.people` | partial, unranked |
| "restaurant in NYC" | typed + place | `content_type=restaurant` + `typed_data.restaurant.city` | ✗ no place parsing |
| "girl holding pink moisturizer" | visual scene | frame `vision_caption` embedding | ✗ |
| "BMW interior" | brand + visual scene | `entities.brands` + vision caption | ✗ |
| "video I saved last month" | temporal browse | `media_kind` + `saved_at` range, **relevance-free** | ✗ |

**Two structural lessons that drive the whole design:**

1. **Three of seven are visual.** For a library of short-form video, frame-level
   understanding is not a nice-to-have — it is roughly a third of query load.
2. **"video I saved last month" must never touch the ranker.** It has no lexical
   content; it is a structured query wearing natural-language clothes. Sending it
   through a semantic ranker returns noise sorted by cosine. It needs intent
   detection that routes it to a sorted browse.

## B.3 The index

One materialized row per **user save**, denormalized so ranking needs no joins.

```sql
CREATE TABLE search_documents (
  bookmark_id            INT PRIMARY KEY,
  user_id                INT NOT NULL,
  canonical_content_id   INT NOT NULL,

  -- lexical, weighted A > B > C > D
  text_title             TEXT,   -- A: title, creator_name, creator_handle, user note
  text_distilled         TEXT,   -- B: tl_dr, key_points, topics, flattened entities
  text_visual            TEXT,   -- C: cleaned OCR + vision captions + visual entities
  text_body              TEXT,   -- D: transcript
  fts                    TSVECTOR,          -- GIN
  trgm_title             TEXT,              -- GIN pg_trgm, typo net

  -- structured prefilters
  platform               VARCHAR(20),
  creator_key            VARCHAR(255),      -- normalized handle
  content_type           VARCHAR(32),
  media_kind             VARCHAR(16),
  lang                   VARCHAR(10),
  duration_bucket        SMALLINT,          -- 0:<60s 1:1-10m 2:10-30m 3:30m+
  collection_ids         INT[],             -- GIN
  visual_dependency      REAL,

  -- entity arrays, GIN-indexed, exact match
  e_people    TEXT[], e_brands  TEXT[], e_products TEXT[],
  e_places    TEXT[], e_foods   TEXT[], e_colors   TEXT[],
  e_objects   TEXT[], e_topics  TEXT[],

  -- time + state
  saved_at               TIMESTAMPTZ,       -- BRIN or btree
  published_at           TIMESTAMPTZ,
  processing_state       VARCHAR(16),
  has_transcript BOOL, has_ocr BOOL, has_vision BOOL, has_note BOOL,

  -- personal priors
  open_count INT DEFAULT 0, last_opened_at TIMESTAMPTZ
);
```

**Weighted tsvector** — where most keyword quality comes from, at zero query-time cost:

```sql
setweight(to_tsvector('simple',  text_title),     'A') ||
setweight(to_tsvector('english', text_distilled), 'B') ||
setweight(to_tsvector('simple',  text_visual),    'C') ||
setweight(to_tsvector('english', text_body),      'D')
```

### B.3.1 The visual index — the unlock for 3 of the 7 queries

`ContentFrame` already stores `ocr_text` + `vision_caption` per sampled frame,
capped at `MAX_FRAMES_PER_VIDEO` (default 8). Extend the vision extraction schema
in `pipeline/frames.py` — **same model call, more JSON, ~zero marginal cost**:

```json
{"caption": "...", "ocr": "...",
 "visual_entities": [
   {"object":"dress","color":"red","attributes":["long","satin"],"prominence":0.8}
 ]}
```

```sql
CREATE TABLE visual_index (
  id BIGSERIAL PRIMARY KEY,
  canonical_content_id INT, frame_id INT, ts_ms INT,
  object TEXT, color TEXT, attributes TEXT[],
  caption TEXT,
  embedding VECTOR(1536),        -- HNSW
  prominence REAL
);
```

**Why a separate table rather than `content_chunks(modality='vision')`:** size
asymmetry. `visual_index` holds ~8 rows per video versus ~60 transcript chunks
per video. It is small enough that a globally-indexed HNSW with a user-scope
prefilter retains accuracy, whereas filtered ANN over ~200k chunks/user hits a
recall cliff. That asymmetry is the entire justification — record it, because it
looks like duplication otherwise.

**Payoff:** "red dress" becomes an **exact structured match**
(`object='dress' AND color='red'`), not a fuzzy embedding hope. This is the
difference between a demo and a product.

### B.3.2 SQLite parity

FTS5 external-content table mirroring `search_documents` (BM25 built in);
`e_*` arrays stored as JSON and queried with `json_each`. Keep the
`IS_POSTGRES` branching confined to `vectors.py` plus one new `fts.py`, matching
the existing pattern in the codebase.

## B.4 Retrieval pipeline

```
query
  │
  ├─ [0] Query understanding ................ ~3ms, no model
  │        normalize · spell · parse · classify intent
  │
  ├─ [1] Prefilter → single WHERE, applied to EVERY retriever
  │
  ├─ [2] Candidate generation (parallel)
  │        R1 lexical FTS (ts_rank_cd, weighted)        → 200
  │        R2 doc kNN     (content_embeddings)          → 200
  │        R3 chunk kNN   (scoped to R1 ∪ R2 doc ids)   → 200
  │        R4 structured  (entity arrays, creator, typed_data) → 100
  │        R5 visual      (visual_index HNSW + exact object/color) → 100
  │        R6 trigram     (typo net; only fires if R1 thin) → 50
  │        R0 browse      (saved_at DESC) — replaces all when intent=temporal
  │
  ├─ [3] Fusion — weighted RRF, intent-conditional weights
  │
  ├─ [4] Rerank top-60 — feature scorer, no LLM
  │
  ├─ [5] Dedup (content_key, phash) · hydrate · attach evidence
  │
  └─ results + facets + evidence spans
```

### [0] Query understanding

Deterministic, rule-based, <3 ms, debuggable — and critically, **its output is
rendered to the user as removable chips**, so a wrong parse is visible and
reversible.

```json
{ "raw": "restaurant in NYC",
  "intent": "typed_lookup", "confidence": 0.82,
  "lexical_terms": ["restaurant","nyc"],
  "semantic_query": "restaurant in NYC",
  "constraints": { "content_type":["restaurant"], "place":["new york city"] } }
```

```json
{ "raw": "video I saved last month",
  "intent": "temporal_browse",
  "lexical_terms": [],
  "constraints": { "media_kind":["video"],
                   "time":{"field":"saved_at","from":"2026-07-01",
                           "to":"2026-07-31","hard":true} },
  "sort": "saved_at DESC" }
```

**The filler problem.** "I saved" must be recognized as a **save-reference
marker** (→ use `saved_at`, not `published_at`) and then stripped. Left in,
`video` and `saved` become lexical terms and match nothing. Markers to detect:
`I saved`, `I bookmarked`, `that I saved`, `from my saves`.

**Intent classes and their detectors:**

| Intent | Detector |
|---|---|
| `navigational` | token matches the user's creator lexicon, or `@handle`, or a quoted title |
| `typed_lookup` | token in the content-type synonym map (recipe / restaurant / workout / outfit / …) |
| `visual_scene` | a color word, or ≥2 concrete-object nouns, or a person-descriptor ("girl", "guy", "hands") |
| `temporal_browse` | a time expression present **and** ≤1 content token |
| `topical` | default |

**Soft vs hard time — important.** Explicit ranges ("last month", "in June") →
**hard filter**. Vague expressions ("recently", "a while back", "a few months
ago") → **recency boost only**. A hard filter on a vague expression produces zero
results, and users blame the product, not their phrasing.

Time expressions to support: last month/week/year, yesterday, today, this week,
"in June", "last summer", "a few months ago" (soft, 2–6 months), "recently"
(soft, 30 d boost).

### [3] Fusion

Reciprocal Rank Fusion, **not** a weighted sum of raw scores. RRF is scale-free,
which is precisely the fix for F3.

```
base(d) = Σ_r  w_r / (60 + rank_r(d))
```

**Intent-conditional weights.** One weight vector cannot serve both "Kai Cenat
stream" and "girl holding pink moisturizer".

| Intent | R1 lex | R2 doc | R3 chunk | R4 struct | R5 visual | recency |
|---|---|---|---|---|---|---|
| navigational | 1.4 | 0.6 | 0.3 | **1.6** | 0.2 | low |
| typed_lookup | 1.0 | 1.0 | 0.8 | **1.4** | 0.4 | low |
| visual_scene | 0.4 | 0.8 | 0.6 | 0.8 | **1.8** | low |
| topical | 0.8 | **1.4** | 1.0 | 0.8 | 0.6 | med |
| temporal_browse | — | — | — | — | — | **sort** |

Baseline (intent-agnostic) weights if shipping before intent classification:
`{lexical 1.0, doc_vec 1.0, chunk_vec 0.8, entity 1.2, visual 1.0, trigram 0.4}`.

### [4] Rerank (top ~60, no LLM ever)

**Multiplicative for priors** (they *modulate*); **additive for hard evidence**
(it must be able to lift an item from nowhere).

```
score = base
      × recency        (1 + 0.15·exp(-Δdays/45),  capped at 1.15)
      × personal       (1 + 0.20·click_prior + 0.10·in_collection + 0.05·has_note)
      × completeness   (0.90 if processing_state != 'ready')
      + 0.30·exact_phrase
      + 0.25·creator_exact
      + 0.20·entity_exact
      + 0.15·title_prefix
      + 0.20·rare_ocr_hit      (idf-weighted within the user's own library)
```

Two details that matter more than they look:

- **Chunk saturation.**
  `chunk_score = max(sims) + 0.15·log(1 + n_above_threshold)`.
  Without it, a 40-minute podcast with 40 mediocre chunk hits outranks a
  30-second clip with one perfect hit.
- **Rare-OCR boost.** An on-screen term appearing in 1 of 800 saves is
  near-certain relevance. OCR is high-recall/low-precision in aggregate (hence
  weight C) but decisive on rare tokens.

**Never exclude for incompleteness** — `×0.90`, not removal. A still-processing
save that matches by title should appear, carrying an "Analyzing" pill
(`ProcessingPill` already exists in `BookmarkCard.swift`).

## B.5 Ranking — recency policy

Recency plays two distinct roles and they must not be conflated:

1. **As a constraint** ("last month") → parsed hard filter (see soft/hard rule).
2. **As a tiebreaker** → mild multiplicative decay,
   `1 + 0.15·exp(-Δdays/45)`, **capped at 1.15**.

Key decisions:

- Decay on **`saved_at`, not `published_at`.** The user's memory is anchored to
  their own save moment — "video I saved last month" is direct evidence of this.
- **Capped and multiplicative** so recency can never override a strong exact
  match. An additive recency term would let a recent irrelevant item outrank an
  older exact hit.
- **Browse mode** (empty query, or pure-filter query) = pure `saved_at DESC`, no
  relevance component at all.
- **Demonstrative exception:** queries containing "that video", "the one I
  saved" raise α from 0.15 to ~0.5 — the user is reaching for something recent.

## B.6 Typo handling

Five layers, ordered by cost:

1. **Normalize** — casefold, NFKD, strip diacritics / punctuation / emoji,
   collapse whitespace.
2. **Per-user lexicon correction** — SymSpell or BK-tree over *the user's own
   vocabulary* (creators, brands, topics, title terms, collection names),
   Damerau-Levenshtein ≤2, weighted by term frequency in their library and by
   keyboard adjacency. `kai senat → kai cenat` works because Kai Cenat is in
   **their** creator list; no global dictionary knows that.
3. **Trigram retriever (R6)** — `pg_trgm` safety net for terms absent from the
   lexicon. Fires only when R1 returns <5 candidates.
4. **Send the raw query to the embedder, the corrected query to FTS.**
   Embeddings are natively typo-tolerant, and correction can destroy a rare
   proper noun. This split is free and strictly better than choosing one form.
5. **UX policy.** Auto-correct only when *all* hold: edit distance ≤2, the
   original returns <3 results, and the correction returns strictly more. Then
   show: *"Showing results for **kai cenat**. Search instead for kai senat."*
   Otherwise show a passive "Did you mean" chip and leave results untouched.
   **Silent auto-correction of a query that already worked is the worst
   outcome** — it makes the system feel like it is arguing with the user.

Damerau (transposition) rather than plain Levenshtein matters specifically on
mobile keyboards.

## B.7 Filters

| Facet | Source column | Notes |
|---|---|---|
| Platform | `platform` | the 9 tiered platforms |
| Type | `content_type` | 16 values already defined in `understanding.py` |
| Creator | `creator_key` | top ~12 by count |
| Collection | `collection_ids` | |
| Time | `saved_at` | Today / Week / Month / Year / Custom |
| Media | `media_kind` | video / image / carousel / article |
| Duration | `duration_bucket` | <1 m / 1–10 m / 10 m+ |
| Has | boolean flags | transcript · on-screen text · note · unopened |

**Rules:**

- **Pre-filter, never post-filter.** Every constraint enters each retriever's
  `WHERE`. This is the fix for F2. `knn()` in `vectors.py` already accepts
  `where_sql` + `params` — the plumbing exists and is simply unused for user
  filters.
- **Filters apply to browse mode too** (fix for F8).
- **Parsed constraints render as removable chips.** "restaurant in NYC" shows
  `[Restaurants ×] [NYC ×]`. The user sees what the system understood, can undo a
  bad parse in one tap, and learns the vocabulary. This is the cheapest trust
  mechanism in the entire design and should not be cut for space.
- **Facet counts computed from the prefiltered candidate set**, cap 500, label
  `500+` past that — one `GROUP BY` over the same scan. Hide zero-count facets
  except ones currently applied (never hide an applied filter — that produces
  "where did my filter go" confusion).

## B.8 Autocomplete

Target **p95 40 ms**, which means it cannot be a server round-trip on most
keystrokes.

Three merged sources:

1. **History** (local, ~0 ms) — prefix match over recent queries.
2. **Entity lexicon** (local, ~0 ms) — the client downloads the user's
   `search_lexicon` on app open (a few thousand terms, ~50 KB) and prefix-matches
   in memory. Typed suggestions:
   `Kai Cenat — creator · 14 saves` / `pasta — topic · 31` / `NYC Eats — collection · 9`.
3. **Instant results** (server, debounced 120 ms) — the top 3 actual save
   thumbnails inline. This is the differentiator: **autocomplete *is* search.**

```sql
CREATE TABLE search_lexicon (
  user_id INT, term_norm TEXT, term_display TEXT,
  kind VARCHAR(16),   -- creator|brand|topic|place|product|collection|content_type
  count INT, last_used_at TIMESTAMPTZ,
  PRIMARY KEY (user_id, kind, term_norm)
);
```

Rebuilt incrementally when `content.process` completes for a save — same job
queue (`jobs.py`), new job kind `search.lexicon.refresh`. Client refreshes on
foreground.

**Debounce:** 120 ms for suggest, 250 ms for full search (replacing the current
single 320 ms full-search debounce, per F10).

## B.9 History

Server-side so it syncs across devices, mirrored locally for instant render.

```sql
CREATE TABLE search_history (
  user_id INT, query_norm TEXT, query_display TEXT,
  last_at TIMESTAMPTZ, run_count INT, click_count INT,
  PRIMARY KEY (user_id, query_norm)
);
CREATE TABLE search_clicks (
  user_id INT, query_norm TEXT, canonical_content_id INT, count INT
);
```

- **A query with runs but zero clicks is a failed query — never suggest it back.**
  Current behaviour stores every submitted string with no outcome, so failures
  are re-offered forever (F10).
- `search_clicks` powers the `click_prior` rerank term. Per-user click feedback
  is the highest-yield personalization signal available and costs one row per tap.
- Swipe-to-delete per item; "Clear" must delete **server** rows, not just the
  local mirror. A history toggle in Profile disables writes. (See §C.3 privacy.)

## B.10 Zero results

Never a bare empty state. **Four distinct states**, four different actions — the
current single "No matches" string conflates all of them.

| State | Detection | Response |
|---|---|---|
| Empty library | `count(bookmarks) = 0` | Onboarding: save your first link |
| **Unprocessed library** | 0 rows in `content_embeddings` for this user | **"Your saves haven't been analyzed yet."** + *Analyze my library* → enqueues `content.process`. **This is the actual current state of the DB (F9)** |
| No match | candidates = 0 | Recovery ladder ↓ |
| No match after filters | candidates > 0 pre-filter, 0 post | Show the relaxation directly |

**Recovery ladder**, in order, stopping at the first that yields results:

1. **Did you mean** — auto-run the correction, clearly labeled.
2. **Relax one constraint** — re-run with each constraint dropped (cheap; already
   prefiltered): *"No restaurants in NYC. **4 results** without the Restaurant
   filter."* with the chip pre-highlighted for one-tap removal.
3. **Semantic-only at a lower threshold** — *"Nothing exact. Closest matches:"* —
   visually separated, dimmer, honestly labeled.
4. **Coverage gap, stated plainly** — a visual query when `has_vision = false`
   across the library → *"None of your saves have been visually analyzed yet."*
5. **Bridge to Ask Sava** — *"Ask Sava about 'vodka pasta recipe' →"*, carrying
   the query over.

## B.11 Zero-query state (search suggestions)

The empty search screen should be a **map of the library**, not a paragraph of
copy (F10).

- **Recent searches** — chips
- **Jump back in** — last 6 opened
- **Your creators** — avatars + counts; tap = filtered browse
- **Browse by type** — `Recipes 23 · Restaurants 11 · Products 40`, real facet
  counts, tap = browse
- **Rediscover** — `GET /api/resurface`, which already exists and is unused
- **Try searching** — 3 rotating examples **seeded from the user's actual top
  entities**, generated at index time

**Absolute rule: never show an example query that returns nothing.** Suggesting
"red dress" to a user with no fashion saves teaches them the product is broken.
The current hardcoded examples violate this.

## B.12 Result cards

Search cards have a different job from library cards: **"is this the one?"** —
so they lead with **evidence**, not just a thumbnail.

**Layout selection by intent** — visual/browse intents → 2-column masonry (for
"red dress" the picture *is* the answer); textual intents → 1-column list (needs
a snippet). Header toggle, remembered globally.

**List card anatomy:**

```
┌──────────┐  Vodka Pasta That Broke The Internet
│          │  @carbone · TikTok · saved 3 weeks ago
│ MATCHED  │  ▸ 0:42 · on screen: "1 CUP VODKA · SAN MARZANO"
│  FRAME   │  [ On screen 0:42 ]  [ Recipe · ingredients ]
└──────────┘
```

**The single highest-impact detail: the thumbnail is the *matched frame*, not the
cover image.** Use `ContentFrame.stored_key` at the matching `ts_ms`. Searching
"red dress" and seeing the exact frame containing the red dress is the moment the
product becomes obviously different from a bookmark folder.

**Grid card:** matched frame, 1-line title, one match chip overlaid bottom-left.

Both reuse existing components: `PlatformBadge`, `ProcessingPill`, `RemoteImage`,
`Format.relativeAge`, `BookmarkGrid`.

## B.13 Why it matched (match explanation + highlighting)

This is trust infrastructure. Two levels.

**Level 1 — one chip, always visible, in the user's language.** Not
`semantic`/`keyword` (F7).

| Signal | Chip |
|---|---|
| transcript chunk | `In the transcript · 2:14` |
| OCR | `On screen · 0:42` |
| vision / visual entity | `Seen in video` |
| user note | `Your note` |
| creator | `By Kai Cenat` |
| typed entity | `Recipe · ingredients` |
| collection | `In "NYC Eats"` |
| **semantic only** | `Similar meaning` |

**Level 2 — tap to expand:** the evidence itself. Snippet with the matched span
highlighted; the frame thumbnail; the entity that matched. Tapping a timestamp
chip deep-links to Detail seeked to that moment — `TranscriptSection.swift`
already renders timed segments.

**The honesty rule:** if the *only* reason is `Similar meaning`, say so, and rank
it below anything with concrete evidence. Users forgive a wrong result whose
reason they can see. They stop trusting a product whose results appear from
nowhere.

**Highlighting mechanics:** the API returns **character spans**, not pre-marked
HTML, so SwiftUI can style with `AttributedString`. Highlight exact tokens,
stems, and the corrected form. **Do not highlight semantic-only matches** — the
absence of highlighting is itself the honest signal that this was a soft match.

```json
"match_evidence": [
  {"kind":"ocr","ts_ms":42000,"snippet":"1 CUP VODKA · SAN MARZANO",
   "spans":[[6,11]],"frame_url":"/media/frames/...","score":0.91},
  {"kind":"entity","field":"foods","value":"vodka sauce","score":0.84}
]
```

## B.14 Latency targets

| Interaction | p50 | p95 | Hard cap |
|---|---|---|---|
| keystroke → suggestions | 10 ms | 40 ms | — |
| submit → **first paint** (lexical + structured) | 45 ms | 90 ms | — |
| submit → full hybrid | 120 ms | 300 ms | 600 ms → degrade to lexical, never spin |
| filter toggle → repaint | 40 ms | 80 ms | embedding cached, skip embed |
| thumbnails visible | 90 ms | 150 ms | prefetch top 6 while typing |
| Ask Sava first token | 900 ms | 1.5 s | — |
| Ask Sava complete | 3 s | 8 s | — |

**Budget at p95 = 300 ms**

| Stage | ms |
|---|---|
| mobile RTT | 60 |
| parse + spell | 3 |
| **query embed (cold)** | **140** |
| FTS | 15 |
| doc kNN (HNSW, ~5 k docs) | 8 |
| chunk kNN (scoped) | 25 |
| entity + visual | 15 |
| fusion + rerank (60 items) | 5 |
| hydrate + serialize | 25 |

The **query embedding dominates** and is the only stage that cannot be optimized
away. Two mitigations:

1. **Query-embedding cache** (LRU / Redis, keyed by normalized query, 24 h TTL).
   Repeat queries, tapped history, and refinements of a query all hit. Expect
   >60% hit rate in steady state.
2. **Two-phase render.** Paint lexical + structured at ~45 ms; merge semantic
   results when the vector lands. To avoid jarring reflow:
   - **never reorder the top 3 after first paint** — semantic results insert
     below, animated;
   - suppress the merge entirely if the user has scrolled or begun a tap in the
     last 150 ms; apply it on the next keystroke instead.
   - The response carries `degraded: true` when the 600 ms cap fired, so the
     client can render a quiet "still improving" affordance rather than lying.

### Scale notes

- SQLite + NumPy brute force (`vectors.py` fallback) scans ~30 MB per query at
  5 k saves — fine for development, **dead at 50 k**. Postgres + pgvector HNSW is
  the production path, which the code already anticipates.
- **Chunk kNN:** filtered ANN over ~200 k chunks/user hits a recall cliff, so R3
  is **scoped to the doc candidates from R1 ∪ R2** (bounded and exact) rather than
  searched globally.
- **Visual index:** small enough (~8 rows/video) to stay globally HNSW-indexed
  with a user prefilter — which is exactly why it is a separate table from
  `content_chunks`. See §B.3.1.

## B.15 API contract

```
GET /api/search
  ?q= &platform[]= &content_type[]= &creator[]= &collection[]=
  &media_kind[]= &saved_after= &saved_before= &has[]=
  &sort=relevance|recent &limit= &cursor=

→ { "query": {...parse...},            # rendered as removable chips
    "results": [ { ...save...,
                   "score": 0.87,
                   "match_reason": {"kind":"ocr","label":"On screen · 0:42"},
                   "match_evidence": [...],
                   "thumbnail_url": "<matched frame>" } ],
    "facets":  { "platform": {...}, "content_type": {...} },
    "corrected_from": null,
    "suggestions": [...],              # populated on zero results
    "took_ms": 118, "degraded": false }

GET  /api/search/suggest?q=       → merged typeahead
GET  /api/search/lexicon          → full lexicon for client-side prefix match
GET  /api/search/history          / DELETE
POST /api/search/click            → {query, bookmark_id}   # feeds click_prior
```

`sort=recent` is the temporal-browse path — no ranking, no embedding, no LLM.

Note: the existing `/api/search` (`routes_intelligence.py`) already returns
`{query, count, results, semantic, took_ms}` and takes scalar `platform` /
`content_type`. The design above is a superset; the existing shape can be kept
and extended rather than broken.

---

# PART C — EXECUTION

## C.1 The Search vs Ask Sava UX distinction

| | **Search** | **Ask Sava** |
|---|---|---|
| Question | "Where is it?" | "What do I know about…?" |
| Output unit | Item | Sentence |
| Latency | <300 ms | 2–8 s |
| Marginal cost | ~$0 | LLM tokens |
| Correctness mode | recall / precision | groundedness |
| Failure cost | wrong card, ignored | wrong claim, believed |
| Privacy | query never leaves your infra to a model | content sent to a model |

### Placement: separate surfaces, one bridge each way

**Not** a merged omnibox. **Not** a mode toggle inside one field. Toggles get
forgotten, and a forgotten toggle produces mode errors in *both* directions: a
keyword query burning 5 seconds and LLM tokens, or a real question returning a
grid of thumbnails.

- **Search stays a tab.** Fast, neutral, no AI branding.
- **Ask Sava** is reached from the Library header, from `Ask this` in Detail
  (`AskSavaSection.swift` already exists), and from the Search bridge.
- **Forward bridge:** one quiet row at the bottom of any result list —
  `Ask Sava about this →`, carrying the query.
- **Reverse bridge:** every Ask Sava citation is a real, tappable result card,
  plus `See all N saves` that drops into Search prefiltered to those items.

### Question detection — detect, promote, never hijack

A question detector (starts with who/what/when/where/why/how/can/should; ends
with `?`; >7 tokens with a verb; comparatives; "summarize" / "compare" /
"which should I") fires — and **Search still runs normally**. It only *promotes*
the bridge to the top of results:

> **That looks like a question.** Ask Sava →

**Rationale (asymmetric costs):** a false positive that shows an extra row costs
one row of pixels. A false positive that *hijacks* the query costs 5 seconds,
real money, and the user's willingness to type freely ever again.

### Visual language

| | Search | Ask Sava |
|---|---|---|
| Chrome | monochrome, dense | accent, generous |
| Motion | fade only, ≤200 ms | streaming text |
| Header | `142 results` | `Grounded in 6 of your saves` |
| Controls | filter chips | mode picker (auto / fast / advanced — `describe_modes()` exists) |
| Loading | skeleton ≤300 ms, else nothing | explicit thinking state |

**No sparkles, shimmer, or AI iconography in Search — ever.** The moment Search
looks like AI, users start *expecting an answer*, and expecting an answer means
being willing to wait. The entire value of Search is that they never consider
waiting.

**Copy:** the placeholder rotates through the user's *real* content —
`"red dress"`, `"vodka pasta"`, `"@kaicenat"`.

## C.2 Phased rollout

| Phase | Scope | Unlocks |
|---|---|---|
| **P0** — days | Point iOS at `/api/search` (F1); pre-filter instead of post-filter (F2); RRF instead of weighted sum (F3); drop MMR from search (F5); human-readable `match_reason` (F7); browse honours filters (F8); **backfill `content.process` over existing saves (F9)** | The intelligence layer reaches users at all |
| **P1** — ~2 wks | `search_documents` + weighted FTS/FTS5 (F6); query parser + visible chips; filter rail + facets; evidence snippets; matched-frame thumbnails; `search_lexicon` + suggest endpoint | 5 of the 7 example queries |
| **P2** — ~2 wks | `visual_index` + extended vision schema; intent-conditional ranking weights; click model; zero-result recovery ladder; query-embedding cache + two-phase render | 7 of 7; p95 inside budget |
| **P3** — later | Learned reranker on click data; collection & creator result types; cross-device history; CLIP frame embeddings for "more like this frame" | Personalization |

## C.3 Invariants (must hold in every phase)

1. **SECURITY — user scoping in SQL at every retriever.** `_USER_SCOPE` in
   `retrieval.py` is correct today:
   ```sql
   canonical_content_id IN (SELECT canonical_content_id FROM bookmarks
                            WHERE user_id = :uid AND canonical_content_id IS NOT NULL)
   ```
   Every **new** retriever must carry it — including `visual_index`, which sits
   on **shared** `CanonicalContent`. This is the one place where the
   process-once-globally design can leak content between users. Any new table
   keyed on `canonical_content_id` is a potential cross-tenant leak until scoped.
2. **No generative model in the Search path. Ever** — including "just for
   reranking". It is the property the entire latency and cost budget rests on,
   and it is worth stating in the UI as a privacy feature.
3. **Nothing silently truncated.** The principle `chunking.py` was written to
   enforce applies to retrieval too: a capped candidate set must be *reported*
   (`"500+"`), never presented as complete.
4. **Pre-filter, never post-filter.** (Generalization of F2.)

## C.4 Acceptance criteria

Ranking changes without measurement are vibes. Build a golden set of ~100
`(query, expected_bookmark_ids)` pairs from a real library — **seeded with all
seven example queries as fixtures** — and gate CI on:

| Metric | Threshold |
|---|---|
| Recall@10 | ≥ 0.90 |
| MRR | ≥ 0.75 |
| p95 latency (5 k-save fixture) | ≤ 300 ms |
| Zero-result rate on real queries | ≤ 3 % |
| Median tapped result rank | ≤ 2 |

Per-phase gates:

- **P0 done when:** iOS search results come from `/api/search`; a platform filter
  returns *all* matching items (not a truncated subset); `match_reason` contains
  no internal vocabulary; `content_embeddings` is non-empty for the test user.
- **P1 done when:** the seven example queries each return the expected item in
  the top 10 for a seeded library, except the two pure-visual ones; parsed
  constraints appear as removable chips.
- **P2 done when:** all seven pass, including "girl holding pink moisturizer" and
  "red dress"; p95 ≤ 300 ms with a cold embedding cache.

Existing test scaffolding: `tests/conftest.py`, `tests/test_foundation.py`.

## C.5 Blockers, risks, and unresolved decisions

### Launch blockers

- **F1** — iOS points at the wrong endpoint. Until fixed, no other search work is
  user-visible.
- **F2** — post-filtering silently drops results. Ships wrong data.
- **F9** — the intelligence tables are empty; a backfill run is a prerequisite for
  testing anything in this document.

### Security items

- Cross-user leakage via shared `CanonicalContent` if any new retriever omits
  `_USER_SCOPE` (§C.3.1). Highest-risk single change in the plan.
- Search history and click logs are user content: deletion must remove server
  rows, and a history-off toggle must suppress writes (§B.9).

### Risks

- **pgvector filtered-HNSW recall cliff** on `content_chunks` at scale — mitigated
  by scoping R3 to doc candidates, but revisit if chunk-only recall proves
  necessary.
- **SQLite brute-force kNN does not scale past ~5 k saves/user.** Development
  parity will silently diverge from production behaviour.
- **Query-embedding latency (~140 ms)** is external and uncontrollable; the whole
  p95 target depends on the cache + two-phase render mitigations landing together.
- **OCR noise.** OCR text is low-precision in aggregate: store raw *and* cleaned
  forms (drop <2-char tokens, drop tokens >40 % non-alphanumeric, dedupe overlays
  repeated across consecutive frames since a caption persists for many frames).
  Weight it C, and rely on the idf-based rare-term boost for precision.

### Unresolved decisions (need a human)

1. **DECISION — extend the vision extraction schema now or later?**
   Adding `visual_entities` to the `pipeline/frames.py` vision call is *free at
   ingest time* but expensive to backfill later (requires re-downloading media —
   the largest per-save cost driver per `config.py`'s own note). Recommendation:
   decide before P1, not P2.
2. **DECISION — is search history server-side from day one?**
   Cross-device sync *and* the click model both depend on it. Cheap now, awkward
   to retrofit.
3. **DECISION — CLIP-style frame image embeddings** (true visual similarity,
   "more like this frame"). Adds a model dependency. Assessment: structured
   visual entities + vision captions cover roughly 85 % of the example visual
   queries at zero extra model cost, so this is deferred to P3 — but confirm.
4. **OPEN — result layout heuristic.** Auto-selecting grid vs list by intent may
   be too clever; the fallback is a plain user toggle defaulting to grid.

## C.6 Additional implementation notes carried from the design

- **Collections in retrieval** have three distinct roles: (a) a filter, (b) a
  rerank boost, (c) a **result type** — when the query matches a collection name
  or embedding, return a header row (`Collection: NYC Eats · 14 saves`). Same for
  creators. Multi-entity results are the "instant answers" of library search.
  (Result types are P3.)
- **Transcript scoring:** chunks are already time-anchored with overlap; max-pool
  per document with the saturation term in §B.4[4].
- **OCR entity extraction:** prices, handles, and URLs found in OCR should be
  promoted into entity arrays, not left as free text.
- **Dedup** is by `content_key` and frame `phash` (near-duplicate reposts), not
  by MMR. `identity.py` already produces stable `content_key`s.
- **`ProcessingPill`** should appear on search results for items where
  `processing_state != 'ready'`, matching library behaviour.

---

## Appendix — key source references

| Concern | File / symbol |
|---|---|
| Hybrid search entry point | `api/services/retrieval.py:search_library` |
| Keyword scorer (to be replaced) | `api/services/retrieval.py:_keyword_scores` |
| User scope SQL | `api/services/retrieval.py:_USER_SCOPE` |
| Chunk retrieval (Ask This) | `api/services/retrieval.py:retrieve_chunks` |
| Two-stage Ask Sava retrieval | `api/services/retrieval.py:retrieve_for_library_question` |
| Vector kNN / MMR | `api/vectors.py:knn`, `api/vectors.py:mmr` |
| Doc text for embeddings | `api/pipeline/chunking.py:build_document_text` |
| Entity flattening | `api/pipeline/understanding.py:entities_to_text` |
| Content types + visual priors | `api/pipeline/understanding.py:CONTENT_TYPES`, `VISUAL_DEPENDENCY_PRIOR` |
| Typed extraction schemas | `api/pipeline/understanding.py:TYPED_SCHEMAS` |
| Vision prompt / frame OCR | `api/pipeline/frames.py:_VISION_SYSTEM` |
| Search endpoint | `api/routes_intelligence.py:search` |
| Legacy ILIKE search | `api/main.py:list_bookmarks` |
| iOS search state machine | `ios/Sava/Features/Search/SearchViewModel.swift` |
| iOS search UI | `ios/Sava/Features/Search/SearchView.swift` |
| iOS API call for search | `ios/Sava/Features/Library/BookmarkService.swift:list` |
| Embedding model + dims | `api/config.py:EMBED_MODEL`, `EMBED_DIM` |
| Frame/vision cost policy | `api/config.py:*_VISION_MODE`, `MAX_FRAMES_PER_VIDEO` |
