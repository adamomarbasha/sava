# Sava Collections — Audit & System Design Specification

**Document type:** Audit of current implementation + full product/technical specification
**Area:** Collections (user-created and Sava-generated), including discovery, clustering,
naming, membership, merge/split, covers, search, Ask Collection, UI, notifications,
privacy, and cost architecture.
**Status:** Design only. **Nothing in this document has been implemented.**
**Authored:** 2026-08-18
**Branch at time of audit:** `feat/intelligence-foundation`
**Last commit at time of audit:** `05192fc Add UI/UX Pro Max Claude skill`

---

## 0. How to read this document (for a future session with no prior context)

This document is the output of a read-only design pass over Sava's Collections feature.
It was produced **without modifying any production code**. It contains:

- **Section 1** — audit of the code as it exists today, including 5 defects that must be
  fixed before building on it. **This is current-state findings.**
- **Sections 2–19** — the recommended design. **These are recommendations, not code.**
- **Section 20** — phased release plan (V1 / V2 / V3) with acceptance criteria.
- **Section 21** — metrics and kill signals.
- **Section 22** — consolidated list of blockers, unresolved decisions, and risks.

Every constant, threshold, and formula below is a **design proposal**. Section 14.2
defines the calibration procedure that must run before any of the numeric thresholds are
treated as real.

### Files this design touches (none were modified)

| File | Role in this design |
|---|---|
| `api/services/collections.py` | The service being redesigned |
| `api/models.py` | `Collection`, `CollectionItem`, plus 2 proposed new tables |
| `api/migrations.py` | Idempotent introspect-then-ALTER migration path |
| `api/vectors.py` | `knn()`, `mmr()`, `normalize()`, `VectorColumn` — reused unchanged |
| `api/services/retrieval.py` | `search_library()`, `_USER_SCOPE` — gains a collection filter |
| `api/services/intelligence.py` | `ask_sava()` — reused for Ask Collection |
| `api/ai/router.py` | `CHEAP` / `BALANCED` specs, `classify_question()` — reused |
| `api/jobs.py` | `enqueue()` idempotency + debounce — reused |
| `api/pipeline/handlers.py` | Job handler registry — gains 4 handlers |
| `api/routes_intelligence.py` | REST surface — expanded |
| `api/pipeline/understanding.py` | `entities` / `topics` extraction — **the key data source** |
| `ios/Sava/Features/Library/` | Where the Collections UI lands |
| `ios/Sava/App/AppShell.swift` | Navigation decision (segmented header, not a 4th tab) |

### Note on codebase drift observed during this session

While this document was being written, `api/jobs.py`, `api/vectors.py`, `api/config.py`,
`api/models.py`, `api/pipeline/handlers.py`, and `api/routes_intelligence.py` were modified
on disk by other work in progress. Two changes are relevant to this design and are already
compatible with it:

- `jobs.enqueue()` gained a `platform=` keyword and `Job` gained a `platform` column, with
  `claim_next` skipping throttled/circuit-open platforms. Collection jobs are
  platform-agnostic and should pass `platform=None`, which the new `claim_next` treats as
  always-claimable.
- `vectors.from_storage()` now handles `memoryview` in addition to `bytes` and plain
  sequences. This makes raw-SQL centroid reads safe on both engines, which this design
  relies on.

Neither change invalidates anything below. The `Bookmark.url` uniqueness blocker
(§1.2, item 5) was re-verified as still present at `api/models.py:34`.

---

# PART I — CURRENT-STATE AUDIT

## 1.1 What exists today

| Component | Status | Location |
|---|---|---|
| `Collection` / `CollectionItem` tables | exist | `api/models.py:265` |
| Manual create + name/description embedding | works | `collections.py:39` `create_collection` |
| Semantic + LIKE member matching | works | `collections.py:78` `suggest_for_collection` |
| k-means auto clustering | works, **structurally wrong** | `collections.py:226` `rebuild_auto_collections` |
| Cluster naming via LLM | works, **cost-inefficient** | `collections.py:186` `_name_cluster` |
| Cover selection | naive first-thumbnail | `collections.py:178` `_set_cover` |
| REST surface | create / list / get / add-items / suggest / rebuild | `routes_intelligence.py:281` |
| `collections.recluster` job | wired | `pipeline/handlers.py:57` |
| `collection.match` job | wired | `pipeline/handlers.py:66` |
| `ChatThread.scope='collection'` + `collection_id` | **column exists, no endpoint** | `models.py:352` |
| iOS Collections UI | **does not exist at all** | `ios/Sava/Features/` |

Existing constants in `collections.py`:
`MIN_CLUSTER_SIZE = 3`, `MIN_SAVES_FOR_AUTO = 8`, `MAX_AUTO_COLLECTIONS = 8`,
`MATCH_THRESHOLD = 0.62`, hardcoded cohesion floor `0.55`.

Database state observed at audit time (local `bookmarks.db`): 11 bookmarks, 1 user,
**0 rows** in `canonical_content`, `content_embeddings`, `content_understanding`,
`content_chunks`, `collections`, `collection_items`. The intelligence pipeline has not
yet been run against real data, so **no threshold in the existing code has ever been
validated against real vectors.**

## 1.2 Defects found — must be fixed before building on this

These are ordered by severity. Items 1–3 are correctness/trust defects. Item 4 is cost.
Item 5 is a launch blocker that is not strictly a Collections bug but blocks validation.

### DEFECT 1 — SEVERITY: HIGH — Collection identity churns on every rebuild

`rebuild_auto_collections` (`collections.py:283`) deletes every `kind='auto'` collection
and its items, then recreates them from scratch:

```python
old = db.query(Collection).filter(Collection.user_id == user_id,
                                  Collection.kind == "auto").all()
for c in old:
    db.query(CollectionItem).filter(CollectionItem.collection_id == c.id).delete()
    db.delete(c)
```

Consequences: collection IDs change on every run, so deep links and any client-cached ID
break; names flip between runs because k-means is seed- and order-sensitive; any user
interaction (rename, pin, cover choice, manual add) is destroyed. **Stable collection
identity across rebuilds is the single most important change in this specification.**
See §4 for the fix.

### DEFECT 2 — SEVERITY: HIGH — k-means forces every save into a cluster

`_kmeans` (`collections.py:161`) assigns a label to every vector. There is no noise class.
`k = max(2, min(max_collections, len(X) // MIN_CLUSTER_SIZE))` chooses `k` a priori from
library size alone, with no relationship to how many real interests the user has.

Consequence: a user with 40 saves and 3 genuine interests receives up to 8 collections, of
which ~5 are noise. The `cohesion < 0.55` guard removes some but not the structural
problem. k-means is also unstable to initialization (`seed=13` masks but does not fix
this), which is what makes DEFECT 1 visible to users. See §3.4 for the fix.

### DEFECT 3 — SEVERITY: HIGH — Removing an auto-added save does not stick

`CollectionItem` has no tombstone state. Removing a row means the next
`rebuild_auto_collections` or membership assignment re-adds the same save. There is
currently no delete endpoint at all, but the moment one is added, this becomes the fastest
way to destroy user trust in the feature. See §9 for the fix (`state='rejected'` rows).

### DEFECT 4 — SEVERITY: MEDIUM — One LLM call per cluster during naming

`_name_cluster` (`collections.py:186`) is invoked inside the per-cluster loop, so 8
clusters means 8 sequential round trips per rebuild. Most clusters do not need a model at
all — the name is already sitting in `content_understanding.entities` or
`canonical_content.creator_name`. See §3.1–§3.3 and §5 for the fix (source ladder +
single batched call).

### DEFECT 5 — SEVERITY: BLOCKER (not a Collections bug) — `Bookmark.url` is globally unique

`api/models.py:34`:

```python
url = Column(Text, nullable=False, unique=True)
```

Two different users cannot save the same URL. This directly contradicts the shared
`CanonicalContent` premise documented at `models.py:115` ("one row per real video in the
world, shared by every user who saves it"). It caps every collection metric, makes
multi-user testing impossible, and means the entity/creator/topic discovery sources in §3
cannot be validated on realistic data.

**This is a launch blocker for V1.** The fix is outside Collections scope (drop the global
unique constraint; add `UNIQUE(user_id, url)` instead), but Collections cannot ship
validated without it.

## 1.3 Missing capability inventory (current state)

None of the following exist today:

- No rename / delete / pin / reorder endpoint for collections (no `PATCH`, no `DELETE`)
- No item removal endpoint
- No rejection or negative-feedback storage
- No merge or split, automatic or manual
- No candidate/suggestion state — a discovered collection is either live or absent
- No confidence score
- No collection search (neither finding collections nor searching within one)
- No Ask Collection endpoint (the `ChatThread` columns exist unused)
- No cover refresh, no user cover override, no multi-image cover
- No notification strategy of any kind
- No global topic priors, so no way to distinguish a personal interest from a universal one
- No audit trail, so no undo
- No iOS surface

---

# PART II — RECOMMENDED DESIGN

> Everything from here on is a recommendation. None of it is implemented.

## 2. Product model

### The framing that drives every decision

> A collection is a **saved question about your library**, not a folder you drag things into.

A collection has a *definition* (centroid + anchors + optional rules). Membership is
**computed**, then **overridden** by the user. Because membership is computed, rebuilds,
merges, splits, and new saves are all non-destructive operations on the definition —
never on the user's expressed intent.

### The two collection types

| | User-created | Sava-generated |
|---|---|---|
| Trigger | User types a name | Discovery job finds a pattern |
| Definition | Name/description embedding + literal anchors | Cluster centroid + dominant entity/topic |
| Naming | User's words, never changed | Derived, then locked on first user edit |
| Failure mode to avoid | Empty collection ("Japan" finds nothing) | Stupid collection ("Videos 3") |
| Deletion | Hard (user asked) | Soft → archived, never resurrected |
| Trust posture | Assume the user is right | Assume Sava is wrong until confirmed |

**Convergence rule.** The moment a user renames, pins, reorders, or manually adds to an
auto collection, it is **promoted to `origin='user'`** and stops being automatically
managed (its centroid still updates; membership becomes suggest-only). This is a
deliberate one-way door.

---

## 3. Schema changes (proposed)

Additive only. `api/migrations.py` already performs introspect-then-ALTER plus
`create_all()` for new tables, so all of this fits the existing migration mechanism with
no Alembic baseline required.

```sql
-- collections: additions
ALTER TABLE collections ADD COLUMN origin           VARCHAR(8)  DEFAULT 'user';   -- user | auto
ALTER TABLE collections ADD COLUMN status           VARCHAR(16) DEFAULT 'active'; -- active|candidate|archived|merged|suppressed
ALTER TABLE collections ADD COLUMN source           VARCHAR(16);                  -- entity|creator|topic|embedding|manual|rule
ALTER TABLE collections ADD COLUMN anchor_terms     TEXT DEFAULT '[]';            -- ["bmw","m3","bimmer"]
ALTER TABLE collections ADD COLUMN anchor_entity    VARCHAR(160);                 -- normalized key, e.g. brand:bmw
ALTER TABLE collections ADD COLUMN match_threshold  FLOAT DEFAULT 0.62;           -- per-collection, calibrated
ALTER TABLE collections ADD COLUMN confidence       FLOAT DEFAULT 0.0;
ALTER TABLE collections ADD COLUMN cohesion         FLOAT;
ALTER TABLE collections ADD COLUMN name_locked      BOOLEAN DEFAULT 0;
ALTER TABLE collections ADD COLUMN cover_locked     BOOLEAN DEFAULT 0;
ALTER TABLE collections ADD COLUMN sort_order       INTEGER DEFAULT 0;
ALTER TABLE collections ADD COLUMN merged_into_id   INTEGER;
ALTER TABLE collections ADD COLUMN last_built_at    TIMESTAMP;
ALTER TABLE collections ADD COLUMN last_opened_at   TIMESTAMP;
ALTER TABLE collections ADD COLUMN surfaced_at      TIMESTAMP;   -- first shown to the user
ALTER TABLE collections ADD COLUMN dismissed_at     TIMESTAMP;
ALTER TABLE collections ADD COLUMN is_private       BOOLEAN DEFAULT 0;
-- The existing `embedding` column is retained; its meaning becomes: the CENTROID.

-- collection_items: additions. Rows are NEVER deleted for auto-sourced members.
ALTER TABLE collection_items ADD COLUMN state      VARCHAR(10) DEFAULT 'member'; -- member|suggested|rejected
ALTER TABLE collection_items ADD COLUMN source     VARCHAR(12) DEFAULT 'user';   -- user|cluster|centroid|anchor|rule
ALTER TABLE collection_items ADD COLUMN decided_at TIMESTAMP;
ALTER TABLE collection_items ADD COLUMN decided_by VARCHAR(8);                   -- user|system
CREATE INDEX idx_ci_collection_state ON collection_items(collection_id, state);
```

Two new tables:

```python
class CollectionEvent(Base):
    """Append-only audit. Powers undo for merge/split/rename/dismiss."""
    __tablename__ = "collection_events"
    id            = Column(Integer, primary_key=True)
    user_id       = Column(Integer, index=True, nullable=False)
    collection_id = Column(Integer)
    kind          = Column(String(24), nullable=False)  # created|renamed|merged|split|
                                                        # dismissed|archived|promoted|
                                                        # item_added|item_rejected|threshold_tuned
    detail        = Column(Text, nullable=False, default="{}")
    created_at    = Column(DateTime(timezone=True), default=func.now())


class TopicPrior(Base):
    """Global aggregate topic frequencies. Counts only — no user identifiers.

    Enables personal-lift scoring: what is DISTINCTIVE about this user, not merely
    frequent. Rows below K_ANON distinct users are never written. See §12 (Privacy)."""
    __tablename__ = "topic_priors"
    term         = Column(String(120), primary_key=True)   # normalized
    kind         = Column(String(12), primary_key=True)    # topic|brand|person|place|product
    user_count   = Column(Integer, nullable=False, default=0)   # must be >= K_ANON to exist
    save_count   = Column(Integer, nullable=False, default=0)
    updated_at   = Column(DateTime(timezone=True), default=func.now())
```

**Implementation note.** `collections.embedding` already uses `VectorColumn(EMBED_DIM)`,
and `vectors.knn()` is table-agnostic — it takes `table`, `vector_column`, `id_column`.
Collection-centroid search therefore requires **no new vector infrastructure**.

---

## 4. Discovery — four sources, cheapest first

**Core thesis: most good collections do not require clustering.** They are already sitting
in `content_understanding.entities` and `canonical_content.creator_name`. Embedding
clustering is the *fallback* for what those miss, not the primary engine. This is what
makes the whole system cost approximately nothing.

### 4.1 Source A — Entity collections (zero AI, highest precision)

`api/pipeline/understanding.py` `_COMMON_SCHEMA` already extracts and persists:

```
"entities": {"people":[], "brands":[], "products":[], "places":[],
             "foods":[], "ingredients":[], "activities":[], "prices":[],
             "dates":[], "urls":[], "key_facts":[], "recommendations":[]}
```

Group the user's saves by normalized entity:

```
normalize(e) = casefold, strip punctuation/possessives, collapse whitespace,
               resolve alias table (bmw|bimmer|bayerische -> brand:bmw)
```

- `brands` → "BMW", "Nike"
- `people` → "Kai Cenat", "AMP"
- `places` → "Japan", "Tokyo"
- `products`, `activities` → "Gym", "Sourdough"

**This is exactly the example set in the original brief.** "Kai Cenat", "BMW", "Japan",
"AMP" are entities, not clusters. Finding them with k-means over document embeddings is
strictly worse than reading the field the pipeline already populated.

Candidate if `count >= MIN_ENTITY (4)` and the entity is not in the generic/stopword set.

### 4.2 Source B — Creator collections (zero AI)

`GROUP BY canonical_content.creator_handle` where `count >= MIN_CREATOR (5)`.
Name = most frequent `creator_name` surface form.

Only surfaced if the creator's saves are **not** already ≥70% inside an entity collection.
A Kai Cenat *fan* gets "Kai Cenat" from Source A; the creator collection is the dedupe
loser in that case.

### 4.3 Source C — Topic collections with personal lift (zero AI)

**This is the mechanism that satisfies "do not create generic categories for everyone."**

```python
user_share   = user_saves_with_term / user_total_saves
global_share = prior.save_count / global_total_saves        # from TopicPrior
lift         = log((user_share + eps) / (global_share + eps))

topic_score  = min(1.0, n / 12) * sigmoid(lift)             # frequency x distinctiveness
```

- "funny", "motivation", "life hacks" → high global share → lift ≈ 0 → suppressed for
  everyone.
- "high protein" → this user saves it 8x more than the population → high lift → surfaced.
- A user who only saves cooking gets "Pasta Recipes" and "Desserts"; a user who only saves
  streamers never sees a food collection, because the food terms have zero user share.

**Cold start.** With no `TopicPrior` rows yet (first N users), fall back to a hardcoded
generic-term banlist plus pure frequency. Backfill priors nightly once `user_count >=
K_ANON` per term.

### 4.4 Source D — Embedding clusters (zero AI to discover, ≤1 call to name)

For saves not claimed by A/B/C. **Replace k-means with threshold-graph clustering
(DBSCAN-on-cosine).** Reasons: no `k` parameter, produces a noise class, deterministic,
order-independent, and stable under small library changes — which is what makes identity
matching in §5 possible at all.

```python
def cluster(X, tau=0.72, min_size=4, max_size=40):
    """X: (n, 1536) L2-normalized. Deterministic, no model, no sklearn."""
    S = X @ X.T                                   # n<=6000 -> 144MB f32, one matmul
    np.fill_diagonal(S, 0.0)
    A = S >= tau
    A = A & (A.T)                                 # mutual: kills hub-chaining
    comps = connected_components(A)
    out = []
    for c in comps:
        if len(c) < min_size:
            continue                              # NOISE - the class k-means lacks
        if len(c) > max_size and tau < 0.86:
            out += cluster(X[c], tau + 0.03, min_size, max_size)   # recursive tighten
        else:
            out.append(c)
    return out
```

Above ~6k saves per user, chunk the matmul, or take top-32 neighbours per item via
`vectors.knn()` on Postgres/HNSW and build a sparse mutual-kNN graph. Same algorithm,
different edge source.

### 4.5 Reconciliation across sources

Sources run in priority order A → B → C → D. Each emits candidates with member sets. Then:

1. **Subset suppression** — drop any candidate whose members are ≥80% contained in a
   higher-priority candidate.
2. **Near-duplicate merge** — centroid cosine ≥ 0.88 or member Jaccard ≥ 0.6 → merge
   *before* naming or surfacing. Prefer the entity/creator name over a derived one.
3. Rank by confidence (§8), cap at `MAX_SURFACED_AUTO = 12`; the remainder stay
   `status='candidate'` (queryable, not shown).

---

## 5. Stability — collection identity across rebuilds

**Never delete-and-recreate.** (Fixes DEFECT 1.) On each rebuild, match new candidate
clusters to existing collections:

```python
def match(candidate, existing_auto):
    best, best_score = None, 0.0
    for coll in existing_auto:
        j = jaccard(candidate.members, coll.member_ids)
        c = cosine(candidate.centroid, coll.centroid)
        score = max(j, 0.9 * c)
        if score > best_score:
            best, best_score = coll, score
    return best if best_score >= 0.40 else None
```

| Case | Action |
|---|---|
| Matched | Update centroid + membership **in place**. Keep `id`, `created_at`, cover, `sort_order`. Rename only if `name_locked=0` **and** the old name fails validation against the new member set. |
| New, confidence ≥ 0.75 | Create `status='candidate'`, `surfaced_at=NULL` — enters the surfacing queue (§11). |
| New, confidence < 0.75 | Create `status='candidate'`, not surfaced. Cheap to keep; still improves search and Related. |
| Existing unmatched, `origin='auto'`, never opened | `status='archived'` after **2 consecutive** rebuilds with no match. Never hard-deleted. |
| Existing unmatched, user ever touched it | `origin='user'`. Membership frozen except suggestions. Never archived. |

The 2-rebuild grace period matters: a user who saves 5 unrelated things should not lose a
collection to one noisy run.

---

## 6. Naming

### Ladder — the model is the last resort (fixes DEFECT 4)

| Source | Name | Cost |
|---|---|---|
| Entity | The entity, in its most frequent surface casing | $0 |
| Creator | `creator_name` | $0 |
| Topic | Highest-lift topic, title-cased (`"high protein"` → `High Protein`) | $0 |
| Embedding | Dominant entity if ≥60% of members share one; else topic-vote; else **one batched LLM call covering all unnamed clusters for this user** | ~$0.001 / rebuild |

The batched call replaces the per-cluster loop in `collections.py:_name_cluster`. One
`CHEAP` call (`gemini-3.5-flash-lite`, per `api/ai/router.py`), JSON array in, JSON array
out:

```
system: For each group of a person's saved videos, return a name they would
have chosen themselves. 1-3 words. A creator, topic, place, brand, or activity.
Return STRICT JSON: {"names":[{"i":int,"name":str,"description":str}]}

Never use: Videos, Content, Collection, Saved, Miscellaneous, Other, Random,
Assorted, Mixed, Stuff, Clips, Posts, Media, Favorites, Interesting.
Never name a group after a platform, a date, or a file format.
If no honest specific name exists, return name:"".
```

**An empty name means the cluster stays `status='candidate'` and is never surfaced.**
A cluster with no good name is not a good collection — this is the cheapest quality filter
available.

### Name validation (runs on every name, model-derived or not)

```python
def valid_name(name, members, user_collections):
    if not name or len(name) > 40: return False
    if len(name.split()) > 4: return False
    if name.casefold() in BANNED: return False
    if name.casefold() in {c.name.casefold() for c in user_collections}: return False
    if SENSITIVE_RE.search(name): return False                       # see §12
    # Groundedness: the name must actually appear in the members' own metadata,
    # unless it's a recognized category term. Kills confident hallucinated names.
    if not is_known_category(name):
        hits = sum(name.casefold() in blob(m).casefold() for m in members)
        if hits / len(members) < 0.30: return False
    return True
```

---

## 7. Thresholds

**All values below are STARTING POINTS, chosen for consistency with the existing code
(`MATCH_THRESHOLD=0.62`, cohesion `0.55`) — they are NOT measured constants.**
`gemini-embedding-001` has a high similarity floor (unrelated documents sit around
0.4–0.55), so every value here must be recalibrated against a labelled set before V1
ships. See §15.2.

| Constant | Proposed value | Purpose |
|---|---|---|
| `TAU_CLUSTER` | 0.72 | Graph edge threshold |
| `TAU_CLUSTER_MAX` | 0.86 | Recursion ceiling |
| `MIN_CLUSTER_SIZE` | 4 | up from 3 |
| `MAX_CLUSTER_SIZE` | 40 | Triggers recursive split |
| `MIN_ENTITY` / `MIN_CREATOR` / `MIN_TOPIC` | 4 / 5 / 5 | Candidate floors |
| `MIN_SAVES_FOR_AUTO` | 15 | up from 8 — 8 saves cannot yield 3 honest collections |
| `MIN_COHESION` | 0.62 | up from 0.55 |
| `MAX_SURFACED_AUTO` | 12 | up from 8; the rest stay candidates |
| `MATCH_ADD` | 0.72 | Auto-add a new save |
| `MATCH_SUGGEST` | 0.62 | Suggest a new save |
| `ANCHOR_MATCH` | 0.80 | Literal name/entity hit (matches today's behaviour) |
| `DUP_MERGE` / `DUP_SUGGEST` | 0.88 / 0.80 | Auto-merge / propose merge |
| `SPLIT_SEP` | 0.70 | Sub-centroid separation required to offer a split |
| `IDENTITY_MATCH` | 0.40 | Jaccard floor for rebuild identity matching |
| `CONF_SURFACE` / `CONF_KEEP` | 0.75 / 0.50 | Surface / retain-as-candidate |
| `DIRTY_SAVES` / `MAX_AGE` | 15 / 7d | Full-rebuild trigger |
| `K_ANON` | 50 users | `TopicPrior` write floor |

### 7.1 Per-collection threshold calibration (V2)

Rejections are the training signal:

```python
# after every 3rd rejection on a collection
rejected_sims = [sim(item, centroid) for item in rejects]
members_sims  = [sim(item, centroid) for item in confirmed_members]
# set the threshold above the 90th percentile of rejects, below the 10th of members
coll.match_threshold = clamp(np.percentile(rejected_sims, 90) + 0.02, 0.60, 0.85)
recompute_centroid(exclude=rejects)
```

Three "no"s and the collection stops making that mistake. This is the loop that makes
manual overrides feel like teaching rather than janitorial work.

---

## 8. Confidence

```python
confidence = (0.28 * source_precision      # entity .95 | creator .90 | topic .75 | embedding .60
            + 0.22 * clamp01((cohesion - 0.55) / 0.30)
            + 0.18 * clamp01(log(n) / log(20))
            + 0.16 * clamp01(lift / 2.5)              # distinctiveness vs population
            + 0.16 * temporal_spread)
            - penalties)

temporal_spread = clamp01(distinct_save_weeks / 4)    # 4+ weeks = sustained interest

penalties = 0.15 * single_creator_non_creator_collection
          + 0.20 * burst_only                         # >=80% saved within 24h, no revisit
          + 0.10 * name_from_model_not_grounded
          + 0.25 * (overlaps_existing_collection > 0.60)
```

`temporal_spread` and `burst_only` encode a real product insight: **a research binge is
not an interest.** Someone who saves 12 apartment videos in one evening while flat-hunting
should not get a permanent "Apartment Ideas" collection until they come back to it — so it
is held as a candidate until a save lands outside the original window.

**Bands:**
- `>= 0.75` — surface
- `0.50 – 0.75` — keep as candidate, promotable when it grows
- `< 0.50` — retain silently for search/Related, never named, never shown

---

## 9. Preventing stupid collections

A single gate every candidate must pass. Failing any → `status='candidate'` or
`'suppressed'`, **never a hard drop** (the cluster still improves retrieval).

1. `n >= MIN_CLUSTER_SIZE`
2. `cohesion >= MIN_COHESION`
3. **≥2 distinct creators**, unless `source='creator'`
4. **≥2 distinct save days**, unless `source='entity'` with `n >= 8`
5. Name passes `valid_name()` — including the groundedness check
6. Not ≥80% subset of an existing collection
7. Not ≥0.88 centroid-similar to an existing collection (auto-merge instead)
8. Not a **platform** cluster ("all my TikToks"), a **time** cluster, a **language**
   cluster, or a **media-kind** cluster — these are filters, not collections. Detect by
   checking whether ≥90% of members share a `platform` / `media_kind` value *and* the
   cluster has no dominant entity or topic.
9. Not in a **sensitive category** (§12)
10. Global cap: 12 surfaced auto collections; overflow ranked out by confidence
11. **Not already dismissed.** A dismissed candidate's `stability_key` (sorted member
    canonical IDs, hashed) is remembered; a re-discovered near-identical cluster
    (Jaccard ≥ 0.6 against a dismissed one) is not re-offered for 90 days.

Rule 11 is the difference between a feature users tolerate and one they turn off.

---

## 10. Adding and removing saves

### 10.1 On new save — free, zero AI calls

`content.process` finishes → `_sync_bookmark_states` (`pipeline/handlers.py:79`) →
enqueue `collections.assign` (priority 300).

```python
def assign_new_save(db, user_id, canonical_id):
    v = embedding(canonical_id)                       # already computed at ingest
    ents = entities(canonical_id)                     # already extracted at ingest
    for coll in active_collections(user_id):
        if coll.anchor_entity in ents:
            add(coll, state="member", source="anchor", score=0.85); continue
        if any(t in blob for t in coll.anchor_terms):
            add(coll, state="member", source="anchor", score=0.80); continue
        s = float(v @ centroid(coll))                 # one dot product
        if s >= coll.match_threshold + 0.10: add(coll, "member",    "centroid", s)
        elif s >= coll.match_threshold:      add(coll, "suggested", "centroid", s)
```

Cost per save: one matmul against ≤30 centroids. Microseconds. **No model runs.** This is
what makes "suggest/add matching future content" free.

**Rejection memory guard.** Never auto-add to a collection where this canonical ID (or one
≥0.92-similar) already has `state='rejected'`.

### 10.2 Removing (fixes DEFECT 3)

`DELETE /api/collections/{id}/items/{bookmark_id}` writes
`state='rejected', decided_by='user'` rather than deleting the row. Deleting the row is
reserved for `source='user'` items — the user added it by hand, so removing it is a clean
undo and there is nothing to learn.

Response includes `{"undo_token": ...}` valid 30s. Every rejection appends a
`CollectionEvent`.

### 10.3 Suggestion inbox (V2)

Each collection shows `state='suggested'` items in a tray at the top: *"3 saves might
belong here."* Accept → `member`. Dismiss → `rejected` (feeds calibration). Suggestions
are **never** mixed into the main grid — doing so makes the user distrust the whole
collection.

---

## 11. Surfacing new collections

A discovered collection is not shown the moment it exists.

```
candidate -> (confidence >= 0.75 AND passes the §9 gate) -> surfacing queue
          -> shown in "Suggested for you" strip at top of Collections
          -> user: Keep    -> status=active, origin stays auto, surfaced_at=now
             user: Rename  -> status=active, origin=user, name_locked=1
             user: Dismiss -> status=archived, stability_key blacklisted 90d
             no action 14d -> auto-promote to active if it grew by >=3 members,
                              else silently archive
```

The 14-day silent auto-promotion matters: most users will not tap anything. A collection
that kept growing while ignored has earned its place; one that did not has not.

---

## 12. Merge and split

### 12.1 Merge

- **Automatic, pre-surface:** cos ≥ 0.88 or Jaccard ≥ 0.6. No user involvement.
- **Suggested, post-surface:** 0.80–0.88 → banner: *"BMW and M3 Builds look similar."* →
  Merge / Keep separate. "Keep separate" writes a `no_merge` pair into `CollectionEvent`,
  permanently.
- **Manual:** long-press → Merge into…

Merge algorithm — the target is an existing collection, never a new row:

```
target = pinned > origin=user > older created_at > larger member count
members(target)   |= members(source)          # rejects preserved as rejects
rejects(target)   |= rejects(source)
anchor_terms      |= anchor_terms
centroid           = weighted mean of both centroids, re-normalized
name               = target's if name_locked else the higher-confidence name
source.status      = 'merged'; source.merged_into_id = target.id
CollectionEvent(kind='merged', detail={...})  -> Undo available 30 days
```

Chat threads scoped to the source repoint to the target.

### 12.2 Split

- **Detected:** run the §4.4 clustering *within* a collection's members. If ≥2 subclusters
  each ≥5 members with inter-centroid cos < `SPLIT_SEP` (0.70) → suggest. Runs only on
  collections with ≥15 members, at most monthly.
- **Manual:** multi-select in the grid → "Move to new collection". The original keeps or
  drops the moved items (user choice; default: keeps for `origin='user'`, drops for
  `origin='auto'`).
- Split children inherit `anchor_terms` filtered to those grounded in each child's
  members. The parent's centroid is recomputed.

---

## 13. Privacy

**Isolation.** Collections, centroids, names, anchors, and items are strictly per-user.
`canonical_content` is shared; nothing derived from *which* user saved *what* ever crosses
the boundary. Every query keeps the existing `_USER_SCOPE` pattern
(`api/services/retrieval.py:28`) — scoping enforced in SQL, not in Python.

**Global priors are the only cross-user surface**, and they are counts only. `TopicPrior`
rows require `user_count >= K_ANON (50)` before being written; no user IDs are stored; the
aggregation job writes only terms clearing the floor. A term unique to one user can never
appear.

**What reaches a model.** Only for `source='embedding'` clusters, and only: member titles,
creator names, top topics. Never the user's email, never `Bookmark.note` (personal
annotations), never the full library, never transcripts. Notes are excluded from naming
input while remaining searchable — a note is the most personal field in the schema.

**Sensitive-category suppression.** Never auto-create, name, notify about, or surface a
collection whose dominant entity/topic falls in: health conditions and treatments, mental
health, pregnancy/fertility, sexuality and gender identity, religion, political
affiliation, immigration status, addiction/recovery, financial distress, or anything
involving a named private individual. The *cluster is kept* — it genuinely improves search
and Related — with `status='suppressed'`: never listed, never named by an LLM, never
notified.

The user may still create such a collection **manually**. That is their explicit choice
about their own library; the rule constrains what Sava infers and volunteers, not what the
user does.

**Deletion.** Deleting a save cascades via the existing `ondelete=CASCADE` on
`collection_items`. Deleting a collection is soft for 30 days (`status='archived'`) then
hard-purged by a nightly job. Account deletion cascades through `collections.user_id`.
`CollectionEvent` rows purge with the user.

**Sharing (V3 only).** Shared collections publish member *canonical content* and the
collection name — never the centroid, never `Bookmark.note`, never `added_by`/`score`,
never rejections. `is_private=1` collections are ineligible and are excluded from any
future export.

---

## 14. Cost model

Per user with 500 saves, one full rebuild:

| Stage | Model | Cost |
|---|---|---|
| Load vectors + entities | — | $0 (SQL) |
| Sources A/B/C (entity, creator, topic) | — | **$0** |
| Source D graph clustering | — | **$0** (one matmul) |
| Naming: A/B/C clusters | — | **$0** |
| Naming: embedding clusters, batched | `CHEAP` ~1.5k in / 300 out | **~$0.0012** |
| Identity match + persist | — | $0 |
| **Total** | | **≈ $0.001 per rebuild** |

| Other operation | Cost |
|---|---|
| Create manual collection | 1 embed call ≈ **$0.00002** |
| Suggest members for it | **$0** — reuses `content_embeddings` + LIKE |
| Assign a new save to collections | **$0** — ≤30 dot products |
| Refresh cover | **$0** — never generates an image |
| Ask Collection turn | 1 `BALANCED` call ≈ **$0.004** |

**Rebuild cadence.** Enqueue `collections.recluster` with
`idempotency_key=f"collections.recluster:{user_id}"` and `delay_seconds=21600` (6h). The
existing `enqueue` already collapses duplicates while queued (`api/jobs.py`), so a user
saving 40 items in an evening triggers **one** rebuild. Full rebuild only when
`dirty_saves >= 15` or `last_built_at > 7d`. Between rebuilds, incremental assignment
covers everything and costs nothing.

At 100k active users saving weekly: **< $150/month** for all collection intelligence.

---

## 15. API and jobs

### 15.1 Endpoints

```
GET    /api/collections                      ?include=candidates&sort=custom|recent|size
POST   /api/collections                      {name, description?, auto_populate}
GET    /api/collections/{id}                 ?state=member|suggested&limit&cursor
PATCH  /api/collections/{id}                 {name?, description?, is_pinned?,
                                              cover_bookmark_id?, sort_order?, is_private?}
DELETE /api/collections/{id}                 soft; ?hard=true for origin=user
POST   /api/collections/{id}/items           {bookmark_ids:[]}
DELETE /api/collections/{id}/items/{bid}     -> state='rejected', returns undo_token
POST   /api/collections/{id}/items/{bid}/accept        suggested -> member
GET    /api/collections/{id}/suggestions     ?limit
POST   /api/collections/{id}/dismiss                   candidate -> archived
POST   /api/collections/merge                {source_id, target_id}      + undo
POST   /api/collections/{id}/split           {bookmark_ids, name}
POST   /api/collections/{id}/ask             {question, mode, thread_id?}     [V2]
GET    /api/collections/search               ?q=                              [V1]
POST   /api/collections/rebuild              (already exists)
POST   /api/collections/undo                 {event_id}                       [V2]
GET    /api/search                           + &collection_id=                [V1]
```

`PATCH` with `name` sets `name_locked=1` and `origin='user'`. `PATCH` with
`cover_bookmark_id` sets `cover_locked=1`. Both write a `CollectionEvent`.

### 15.2 Jobs (`api/pipeline/handlers.py`)

| Kind | Trigger | Priority | Cost |
|---|---|---|---|
| `collections.assign` | after `content.process` | 300 | $0 |
| `collections.recluster` | debounced 6h / dirty≥15 / 7d | 200 | ~$0.001 |
| `collection.match` | manual collection created | 250 | $0 |
| `collections.maintain` | nightly | 400 | $0 — covers, counts, archive sweep, dup detection |
| `priors.aggregate` | nightly, global | 500 | $0 — `TopicPrior` with K_ANON |
| `collections.purge` | nightly | 600 | $0 — hard-delete >30d archived |

All collection jobs are platform-agnostic and should pass `platform=None` to `enqueue()`
so the platform-throttling logic in `claim_next` never blocks them.

### 15.3 Threshold calibration procedure — REQUIRED BEFORE V1 SHIPS

Every constant in §7 is a hypothesis. Calibrate against real vectors:

1. Take 5 internal libraries, ≥150 saves each, fully processed through the pipeline.
2. Two people independently label all pairs within a 300-pair sample as same-collection /
   not. Report Cohen's κ.
3. Sweep `TAU_CLUSTER` over [0.60, 0.85] in steps of 0.01; plot precision/recall of
   within-cluster pairs.
4. Choose τ at **precision ≥ 0.85** — for this feature a missed collection is invisible,
   a wrong one is a bug report.
5. Repeat for `MATCH_ADD` on held-out saves; `MATCH_ADD` targets **precision ≥ 0.90**
   because auto-add is silent.
6. Record measured values in `api/config.py` **with the measurement date**, exactly as
   `api/ai/router.py` documents its model-probe date.

---

## 16. Collection search

Three distinct behaviours:

**A. Find a collection** — `GET /api/collections/search?q=`. Fuse prefix/fuzzy name match
with `vectors.knn()` over `collections.embedding` scoped by `user_id`. Zero new
infrastructure. Powers the Collections search field and the "Add to collection" picker.

**B. Search inside a collection** — `search_library(..., collection_id=)`. One extra SQL
clause in `_USER_SCOPE`:

```sql
AND canonical_content_id IN (
    SELECT b.canonical_content_id FROM collection_items ci
    JOIN bookmarks b ON b.id = ci.bookmark_id
    WHERE ci.collection_id = :cid AND ci.state = 'member')
```

**C. Collections in global search** — `/api/search` returns a `collections` block above
`results`. A user typing "japan" should see the *Japan collection* first, then individual
saves. Match on name similarity + centroid-to-query cosine.

**C is the highest-leverage single addition to search**, and it costs one extra `knn` over
a table with ≤30 rows per user.

---

## 17. Ask Collection (V2)

`ChatThread.scope='collection'` and `ChatThread.collection_id` already exist
(`api/models.py:352`) — only the endpoint and retrieval scoping are missing.

`retrieval.retrieve_for_library_question` gains a `collection_id` parameter that scopes
`search_library`. Everything else in `intelligence.ask_sava` is reused unchanged.

**Why this is the strongest surface for synthesis:** the corpus is already topically
coherent, so retrieval precision is high and MMR diversification (`vectors.mmr`) actually
samples across the *whole* collection rather than one dense neighbourhood. Cross-save
synthesis works here in a way it does not over a mixed library.

**Retrieval budget scales with collection size:**
`max_saves = clamp(ceil(n * 0.4), 6, 20)`. A 9-item collection sends nearly all of it; a
200-item one sends the top 20. Question routing already handles escalation via
`router.classify_question()` — no change needed.

**Preset chips, chosen by the collection's dominant `content_type`:**

| Dominant type | Chips |
|---|---|
| `recipe` | "Shopping list for all of these" · "Which are under 30 minutes?" · "High protein ones" |
| `travel` | "Build a 5-day itinerary" · "Everything in Tokyo" · "Best time to go" |
| `product` | "Compare these" · "Cheapest option" · "What did people complain about?" |
| `fitness` | "Build a weekly split" · "No-equipment ones" |
| creator/entity | "What does he keep recommending?" · "Summarize everything I saved here" |
| mixed | "What do these have in common?" · "Summarize this collection" |

Answers cite member saves via the existing `ChatMessage.citations` field.

**V3:** pin an answer into the collection as a persistent artifact ("Shopping List") that
regenerates when members change.

---

## 18. Covers

**Never generate an image.** It costs money, looks like AI slop, and the user's own
thumbnails are better.

- **V1** — single thumbnail. Rank by `has_thumbnail x score x recency`, prefer
  portrait/square, exclude `state != 'member'`. Deterministic so it does not reshuffle
  between loads.
- **V2** — 2x2 mosaic from the top 4 members with **distinct creators** (a Kai Cenat
  collection showing 4 identical thumbnails is worse than one). Composed client-side in
  SwiftUI from the existing `RemoteImage` — no server work, no storage, no new CDN cost.
- **V3** — user picks any member as cover (`cover_locked=1`), or uploads one.

**Refresh policy.** Only when the cover's bookmark is deleted, or in `collections.maintain`
when member count has doubled since `last_built_at`. A cover that changes on every visit
makes the collection feel unstable.

**Fallback** for a collection with zero thumbnails: a deterministic gradient seeded by
`hash(collection.id)` over the `SavaColors` palette plus the first letter — never a
generic grey placeholder.

---

## 19. iOS UI

### 19.1 Navigation decision

`AppShell` is currently `library / search / profile` with a prominent center Save
(`ios/Sava/App/AppShell.swift`). **Do not add a fourth tab** — 4 tabs plus a center action
is cramped and dilutes Save.

Instead: **Library gets a segmented header** — `All · Collections` — directly under the
existing "Library" title. The platform `FilterPill` bar stays under "All". This makes
Collections a *view of the library*, which is what it actually is.

```
+------------------------------+
| Library                  47  |
| /---------\ /-------------\  |   segmented, glass, SavaMotion.standard
| |   All   | | Collections |  |
| \---------/ \-------------/  |
+------------------------------+
```

### 19.2 Collections view

```
+---------------------------------------+
|  [search] Search collections          |
|                                       |
|  Suggested for you              x     |   only when a candidate is pending
|  +-------------------------------+    |
|  |  ##  BMW                      |    |
|  |  ##  11 saves - from 6 weeks  |    |
|  |      [ Keep ]   [ Not for me ]|    |
|  +-------------------------------+    |
|                                       |
|  +--------------+ +--------------+    |   2-col, reuses Masonry.swift
|  | ####         | | ####         |    |
|  | ####         | | ####         |    |
|  | Kai Cenat  * | | High Protein |    |
|  | 23 saves     | | 14 - 2 new   |    |   "2 new" = suggested count
|  +--------------+ +--------------+    |
|                                       |
|              [ + New Collection ]     |
+---------------------------------------+
```

**No visual distinction between auto and user collections in the grid.** Surfacing the
machinery ("AI-generated") invites suspicion and gives the user nothing actionable. The
distinction lives in the detail sheet's provenance line.

### 19.3 Create flow

```
+ New Collection
  |- Name: [ Japan            ]        <- inline, no modal chrome
     |- (as you type, debounced 400ms)
        "12 saves look like they belong"      <- live count from /suggestions
     |- [ Create ]
        |- opens the collection with 12 pre-selected suggestions
           +------------------------------+
           | Add these 12?  [Add all]     |
           | # # # # # #  (tap to exclude)|
           +------------------------------+
```

**The live count during typing is the moment the product proves itself.** It costs one
embedding call (~$0.00002) debounced to fire once. Named `Recipes` on a library with no
food → *"No matches yet — we'll add them as you save"*, which sets the right expectation
instead of showing an empty room.

### 19.4 Collection detail

```
+---------------------------------------+
| < Back              Kai Cenat    ...  |   ... = Rename - Pin - Cover - Merge -
| 23 saves - 4 creators                 |         Split - Make private - Delete
|                                       |
| +- 3 saves might belong here ----- >  |   suggestion tray, dismissible
| +-------------------------------------+
|                                       |
|  +---------------------------------+  |
|  | (chat) Ask this collection      |  |   V2
|  +---------------------------------+  |
|  [What does he keep recommending?]    |   chips by content_type
|                                       |
|  ####  ####     <- existing BookmarkGrid, unchanged
+---------------------------------------+
```

Long-press a card → *Remove from collection* → item fades, toast *"Removed · Undo"*.
After the 3rd removal, a one-time line: *"Got it — we'll be stricter about what we add
here."* Making the learning visible converts a chore into a contribution.

### 19.5 Empty states

| Situation | Copy | Action |
|---|---|---|
| 0 collections, < 15 saves | "Collections appear as you save. Keep going — we'll spot the patterns." + live progress `9 / 15` | Create one anyway |
| 0 collections, ≥ 15 saves, nothing found | "Nothing's grouped up yet. Make one and we'll fill it." | Create |
| Collection with 0 members (user-created, no matches) | "Empty for now. New saves about **Japan** will land here automatically." | Browse library / Add manually |
| Collection whose saves were all deleted | "Everything here was deleted." | Delete collection / Keep |
| All candidates dismissed | Nothing. No nag. | — |
| Search inside collection, no results | "No saves in **BMW** match *warranty*." | Search whole library ↗ |

The `< 15 saves` progress counter is deliberate: it makes an empty feature feel like a
countdown rather than a failure.

---

## 20. Notifications

Notifications are how this feature earns a permanent uninstall. **The default is silence.**

**V1: zero push.** In-app only — a dot on the Collections segment when a candidate is
pending. That is all.

**V2: at most one push per user per 14 days.** All of these must hold:

- `confidence >= 0.80` and `n >= 8`
- collection is genuinely new (not a resurrected dismissal)
- user has not opened the app in 48h
- local time 10:00–20:00
- ≥14 days since the last collection push
- user has ≥40 saves (below that, a collection is not news)

Copy names the actual thing:

> **"11 of your saves are about BMW."** Want them in one place?

Never *"Sava found new collections for you"* — it says nothing, and it is the sentence
every abandoned app sends.

**Adaptive suppression.** If the last 2 collection pushes produced no open within 72h,
permanently downgrade this user to in-app only. Never ask them to re-enable.

**V3:** opt-in monthly digest ("Your month in saves — 4 new interests"), and an opt-in
resurface tie-in with the existing `/api/resurface` endpoint. Both strictly opt-in, both
inside the same 1-per-14-days budget.

---

# PART III — RELEASE PLAN

## 21. Phases

### V1 — Collections that work (~2.5 weeks)

**Goal: a user with 40 saves opens Collections and every collection there is correct.**
Breadth is not the goal; zero embarrassing collections is.

**Backend**
- Schema migration (§3), `CollectionEvent`, `TopicPrior`
- Discovery **sources A, B, C only** (entity, creator, topic-with-lift) —
  **no embedding clustering, no LLM naming, $0 marginal cost**
- Stable identity matching (§5) — replaces delete-and-recreate (fixes DEFECT 1)
- Rejection tombstones + rejection memory guard (fixes DEFECT 3)
- `collections.assign` on new save (free)
- Gate rules 1–8, 10, 11 (§9)
- Full CRUD: `PATCH`, `DELETE`, item accept/reject/undo
- Collection search + `collection_id` filter on `/api/search` + collections block in
  global search (§16)
- Covers V1 (§18)
- Threshold calibration (§15.3) **before ship**
- **Fix `Bookmark.url` global uniqueness — BLOCKER** (§1.2 DEFECT 5)

**iOS**
- `All · Collections` segmented header in `LibraryView`
- Collections grid (reuses `Masonry`, `BookmarkCard`, `RemoteImage`)
- Create flow with live match count
- Collection detail + remove-with-undo
- All empty states
- **No notifications**

**V1 acceptance criteria**
- On 5 internal libraries, **≥90% of surfaced collections rated "I'd keep this"**
- **0 collections named from the banlist**
- **A removed save never reappears across 3 consecutive rebuilds**
- Collection IDs stable across 3 consecutive rebuilds
- `Bookmark.url` blocker resolved and multi-user save of the same URL verified

### V2 — Collections that learn (~3 weeks)

- Source D: graph clustering (§4.4) + **batched** LLM naming (fixes DEFECT 2 and 4)
- Confidence scoring (§8), surfacing queue, 14-day auto-promotion (§11)
- Suggestion inbox (§10.3)
- Per-collection threshold calibration from rejections (§7.1)
- Auto-merge + suggested merge + manual merge/split, with undo (§12)
- **Ask Collection** + preset chips (§17)
- Mosaic covers (§18)
- `TopicPrior` aggregation live at `K_ANON = 50`
- Conservative push (§20) behind a remote flag, staged 5% → 100%

**V2 acceptance criteria**
- Suggestion accept rate **≥ 60%**
- Merge suggestions accepted **≥ 50%**
- Collection push open rate **≥ 15%** (kill the channel below 8%)

### V3 — Collections that do things

- **Smart collections**: saved rules —
  `content_type=recipe AND topics ⊇ [high protein] AND duration < 600`
- **Pinned artifacts**: an Ask Collection answer saved into the collection, regenerated
  when membership changes (shopping list, itinerary, weekly split)
- **Shared collections** with the privacy constraints in §13
- **Temporal drift**: split a collection whose recent members have diverged from its
  origin ("Gym" → "Gym" + "Powerlifting")
- **Cross-collection insight**: "Your Japan and Recipes collections overlap on ramen"
- Collection-scoped resurfacing
- On-device candidate pre-filtering (Core ML) to cut assignment latency to zero

---

## 22. Metrics and kill signals

| Metric | Target | Kill signal |
|---|---|---|
| Auto collections kept after 14d | ≥ 80% | < 60% → tighten `CONF_SURFACE` |
| Candidates dismissed | ≤ 20% | > 35% → discovery is wrong |
| Auto-added items rejected | ≤ 8% | > 15% → raise `MATCH_ADD` |
| Suggestions accepted | ≥ 60% | < 40% → lower recall |
| Users with ≥1 collection open / week | ≥ 35% | < 15% → feature is not wanted |
| Names user-edited | ≤ 25% | > 40% → naming is wrong |
| Merge suggestions accepted | ≥ 50% | < 30% → raise `DUP_SUGGEST` |
| Rebuild cost / user / month | ≤ $0.01 | > $0.05 → too many LLM naming calls |
| Collection push → open (72h) | ≥ 15% | < 8% → **turn push off** |

Everything lands in the existing `UsageEvent` ledger (`api/models.py:315`) via
`telemetry.record` with `operation='collections.*'` — no new analytics infrastructure.

---

# PART IV — CONSOLIDATED RISK REGISTER

## 23.1 Launch blockers (must be resolved before V1 ships)

| # | Blocker | Severity | Where |
|---|---|---|---|
| B1 | `Bookmark.url` is globally `unique=True`; two users cannot save the same URL. Contradicts the shared-`CanonicalContent` design and blocks multi-user validation of every discovery source. | **BLOCKER** | `api/models.py:34` |
| B2 | Threshold calibration (§15.3) has never been run. Every numeric constant in §7 and in today's `collections.py` is unvalidated. | **BLOCKER** | §15.3 |
| B3 | Delete-and-recreate rebuild destroys collection identity and all user edits. | **HIGH** | `collections.py:283` |
| B4 | No rejection tombstone — removed saves reappear. | **HIGH** | `collection_items` schema |
| B5 | Local DB has 0 rows in `canonical_content` / `content_embeddings` / `content_understanding`. Nothing in this design can be tested until the pipeline has been run against real saves. | **HIGH** | environment |

## 23.2 Security / privacy items

| # | Item | Requirement |
|---|---|---|
| P1 | Cross-user leakage via `TopicPrior` | Enforce `K_ANON = 50` distinct users before a row is written. Store counts only, never user IDs. |
| P2 | Personal notes reaching a model | `Bookmark.note` must be excluded from all cluster-naming prompts while remaining searchable. |
| P3 | Sensitive-category inference | Health, mental health, pregnancy/fertility, sexuality, gender identity, religion, politics, immigration, addiction, financial distress → `status='suppressed'`: never named, never surfaced, never notified. Manual user creation remains allowed. |
| P4 | Query scoping | Every collection query must scope by `user_id` **in SQL**, following the existing `_USER_SCOPE` pattern — never filter in Python. |
| P5 | Sharing (V3) | Never publish centroid, notes, `added_by`, `score`, or rejections. `is_private=1` is ineligible. |

## 23.3 Unresolved decisions (need a human call)

| # | Decision | Options | Recommendation in this doc |
|---|---|---|---|
| D1 | Collections navigation placement | 4th tab vs. segmented header inside Library | Segmented header (`All · Collections`) — 4 tabs plus a center Save is too crowded |
| D2 | Whether to show auto vs. user provenance in the grid | Show badge / hide | Hide in the grid; show only in the detail sheet |
| D3 | Exact `TAU_CLUSTER` and `MATCH_ADD` values | Any | Cannot be decided without §15.3 calibration — do not ship the proposed defaults as final |
| D4 | Whether V1 ships without embedding clustering at all | Yes / no | Yes — sources A/B/C alone are enough for a correct V1 and cost $0 |
| D5 | Push notifications at all | V2 flag / never | V2 behind a remote flag with a hard kill signal at <8% open rate |

## 23.4 Known risks

| Risk | Mitigation in this design |
|---|---|
| Embedding clusters produce plausible-sounding but meaningless collections | Groundedness check in `valid_name()`; empty-name → never surfaced; gate rules §9 |
| A research binge becomes a permanent collection | `burst_only` penalty and `temporal_spread` in the confidence formula (§8) |
| Users repeatedly re-offered a collection they dismissed | `stability_key` blacklist, 90 days (gate rule 11) |
| Rebuild cost scales badly with library size | Sources A/B/C are SQL-only; Source D is one matmul; naming is one batched call; rebuilds debounced to 6h via `enqueue` idempotency |
| Similarity matrix memory at large libraries | Chunked matmul above ~6k saves, or sparse mutual-kNN via `vectors.knn()` on pgvector/HNSW |
| Cover instability makes collections feel unreliable | Deterministic ranking + refresh only on member-doubling or cover deletion |
| Notification fatigue | 1 push / 14 days, adaptive permanent downgrade after 2 ignored |

---

## 24. The three ideas that carry this design

1. **Read the entities, don't cluster them.** `content_understanding.entities` already
   contains "Kai Cenat", "BMW", "Japan". Sources A/B/C produce better collections than
   k-means at literally zero cost, and they are what makes V1 shippable in ~2.5 weeks.

2. **Personal lift, not raw frequency.** Comparing a user's topic distribution against a
   k-anonymous global prior is the mechanism that gives one user "High Protein / Pasta
   Recipes / Desserts" and another "Kai Cenat / AMP / Streetwear" — from identical code,
   with no taxonomy anywhere in the system.

3. **Collections are definitions, and rejections are the training signal.** Stable
   identity across rebuilds, plus tombstoned rejections, plus per-collection threshold
   calibration, turns every correction into a permanent improvement — instead of a chore
   the user has to repeat every time the job runs.
