# Sava — Production Security & Privacy Audit

**Status:** Findings recorded. **No remediation has been implemented.**
**Verdict:** Not shippable. 12 hard launch blockers.

---

## 0. About this document

### What this is

A full pre-launch security and privacy audit of Sava (FastAPI backend + native SwiftUI
iOS client + Next.js web routes), performed as a read-only review before App Store
submission. It is a **record of findings as of the baseline below** — it is not a task
list that has been worked through, and nothing in the "Remediation plan" section has
been executed.

### Audit baseline

| Field | Value |
| --- | --- |
| Branch | `feat/intelligence-foundation` |
| HEAD commit at audit time | `05192fc` ("Add UI/UX Pro Max Claude skill") |
| Working tree | Dirty — the intelligence layer (`api/ai/`, `api/pipeline/`, `api/services/`, `api/routes_intelligence.py`, `api/jobs.py`, `api/worker.py`, `api/vectors.py`, `api/migrations.py`, `api/config.py`, `api/content/`) and the iOS feature modules were untracked/modified at review time |
| Scope reviewed | `api/` (all), `ios/Sava/` (all), `web/app/api/`, `docker-compose.yml`, `run_api.py`, `.gitignore`, git history |

### ⚠️ Known drift since the audit

Several files were modified on disk **after** the findings below were produced:
`api/main.py`, `api/pipeline/acquire.py`, `api/pipeline/ingest.py`,
`api/pipeline/handlers.py`, `api/routes_intelligence.py`.

Observed changes include a new `ASYNC_SAVE` config flag, a `_clean_title()` helper in
`main.py`, and yt-dlp cookie support (`YTDLP_COOKIEFILE` / `YTDLP_COOKIES_FROM_BROWSER`)
plus a new `fetch_captions_via_ytdlp()` in `acquire.py`.

**Implication for a future agent:** line numbers in this document are anchored to the
baseline and may have shifted. **Re-verify each finding against the current file before
acting on it.** Findings are described in enough detail to be re-located by symbol name
and behaviour, not just line number.

Note also: the new yt-dlp cookie support introduces a *new* credential-handling surface
(a browser cookie jar or cookiefile readable by the worker process) that was **not**
covered by this audit and should be reviewed separately — see §7, item F-4.

### How to use this document

1. §2 is the pass list — what is already correct. **Do not "fix" these; verify before changing.**
2. §3–§5 are current-state findings by severity. Each is a factual statement about the
   code as it stood at baseline.
3. §6 is the recommended remediation plan, phased. **Recommendations only — not decisions
   that have been made, and not work that has been done.**
4. §7 tracks unresolved decisions and out-of-scope items.
5. §8 lists acceptance criteria that should gate a "this is fixed" claim.

---

## 1. Executive summary

The user-isolation **architecture is sound**. The API surface **in front of it is not**.

The intelligence layer's cross-user canonical caching — the thing most likely to leak one
user's private data to another — was audited specifically and **holds correctly today**.
User scoping is enforced in SQL on every retrieval path, and no user-scoped field (notes,
chat, collections) is written into any canonical/shared table.

The blockers are elsewhere and are mostly of one kind: **endpoints where nobody remembered
to add authentication.** The codebase has good ownership checks wherever someone wrote
them and none where they didn't. That is a structural problem, not a collection of
independent bugs — see §6 Phase 1, item 2, for why making auth the router-level default is
the single highest-leverage change.

Secondary themes:

- **SSRF in two places** (one unauthenticated and direct, one via yt-dlp in the worker).
- **A schema-level multi-tenancy break** (`Bookmark.url` is globally unique).
- **Unmetered, unauthenticated access to paid AI/compute** on the operator's API key.
- **Missing legal/compliance surface** (no account deletion, no data export, consumer-tier
  AI provider terms).
- **Temp media and cached thumbnails that are never cleaned up.**

---

## 2. Current state — what is CORRECT (verified, do not regress)

These were checked deliberately because they are the highest-risk areas. They pass.
Any future change touching them needs a regression test (see §8).

| # | Verified behaviour | Evidence |
| --- | --- | --- |
| C-1 | **Retrieval is user-scoped in SQL on every path.** `_USER_SCOPE` restricts every vector/keyword query to canonical content the caller personally saved. Applied to library search, related saves, chunk retrieval, and collection matching. | `api/services/retrieval.py:28` and all call sites |
| C-2 | **Notes are never embedded into shared vectors.** `build_document_text()` *accepts* a `note=` kwarg, but the ingest call site does **not** pass it. User notes therefore never enter `content_embeddings` (a cross-user table). | `api/pipeline/ingest.py:580` (call), `api/pipeline/chunking.py:157` (signature) |
| C-3 | **Collection item addition verifies bookmark ownership.** `add_items()` filters candidate bookmarks by `coll.user_id`, closing the obvious IDOR that would have exposed another user's `note` through `GET /api/collections/{id}`. | `api/services/collections.py:179` |
| C-4 | **Chat threads and messages are user-scoped.** Thread lookup filters on `ChatThread.user_id`; messages are reached only through an owned thread. | `api/routes_intelligence.py:196`, `:249` |
| C-5 | **No user-scoped data is written to canonical tables.** `CanonicalContent`, `ContentTranscript`, `ContentFrame`, `ContentChunk`, `ContentUnderstanding`, `ContentEmbedding` receive only content-derived fields. Notes, chat, and collections live in user-scoped tables. | `api/models.py:141-269`, `api/pipeline/ingest.py` |
| C-6 | **`ask_this` uses the note per-request only.** `bookmark.note` is injected into the prompt at request time and never persisted to a canonical row. | `api/services/intelligence.py:187` |
| C-7 | **Password hashing is bcrypt via passlib, cost 12.** | `api/auth.py:15` |
| C-8 | **No SQL injection.** Every dynamically-built clause (keyword search, collection token match, telemetry rollups, `knn` `where_sql`) uses bound parameters; only fixed internal strings are interpolated. | `api/services/retrieval.py:82-118`, `api/services/collections.py:102-123`, `api/ai/telemetry.py:127-150`, `api/vectors.py:102-127` |
| C-9 | **iOS Keychain usage is correct.** Token stored in Keychain (not `UserDefaults`) with `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`. | `ios/Sava/Core/Security/KeychainStore.swift:23` |
| C-10 | **iOS ATS is not weakened.** `Info.plist` declares only `NSAllowsLocalNetworking` — no `NSAllowsArbitraryLoads`. | `ios/Info.plist` |
| C-11 | **No secrets in source or git history.** `api/.env` was never committed; no `AIza…` or `sk-…` keys appear anywhere in history. All credentials are read from env. | `git log --all -- "*.env"` (empty), history grep (empty) |
| C-12 | **No sensitive client-side logging.** No `print`/`NSLog`/`debugPrint` of tokens, emails, or credentials in the iOS app. | `ios/Sava/` grep |
| C-13 | **Job queue idempotency is enforced by a UNIQUE constraint**, not application bookkeeping — retries and replays cannot double-schedule expensive work. | `api/models.py:314`, `api/jobs.py:73-105` |

---

## 3. LAUNCH BLOCKERS (P0)

Every item here must be resolved before any external user touches the system.

### P0-1 — `GET /users` is unauthenticated and dumps all user PII
- **File:** `api/main.py:196` (baseline `:190`)
- **Current state:** No `Depends(get_current_user)`. Returns every user's `id`, `email`,
  and `created_at`.
- **Impact:** Complete user enumeration and email harvest in a single unauthenticated
  request. Feeds credential stuffing and targeted phishing.
- **Type:** Security — broken access control / PII disclosure.

### P0-2 — `GET /api/thumbnail?url=` is an unauthenticated full SSRF proxy
- **File:** `api/main.py` (`proxy_thumbnail`, baseline `:483`)
- **Current state:** `requests.get(url, stream=True)` against any attacker-supplied URL,
  with the response body streamed back to the caller. No scheme check, no host allowlist,
  no private-IP/link-local rejection, no redirect cap, no response size cap, no auth.
- **Impact:** Reaches cloud instance metadata endpoints, internal-only services, and
  anything else routable from the API host, and returns the body to the attacker. Also
  usable as an open proxy to launder traffic through your IP.
- **Type:** Security — SSRF.

### P0-3 — `Bookmark.url` is globally `unique=True` — multi-tenancy is broken at the schema level
- **Files:** `api/models.py:34` (`Bookmark.url`), `api/models.py:69`
  (`YouTubeDetails.video_id`), `api/ingestors/registry.py:173-177`
- **Current state:** The uniqueness constraint is global, not per-user. Once user A saves
  a URL, user B **cannot save it at all** — the insert raises `IntegrityError`, which
  `_create_new_bookmark` catches and reports as *"You already have this link bookmarked!"*
- **Impact (two distinct problems):**
  1. **Functional:** The product does not work for a second user. Any popular URL is
     permanently claimed by whoever saved it first.
  2. **Security:** The error message is a **cross-user existence oracle** — anyone can
     probe whether an arbitrary URL exists in *any other user's* library, and the message
     falsely asserts it is in their own.
- **Note:** `YouTubeDetails.video_id` carries the same global constraint, so the problem
  reproduces on the YouTube path independently.
- **Type:** Security + correctness. **Requires a data migration**, so schedule early.

### P0-4 — `GET /api/comments/{bookmark_id}` — no auth, no ownership check
- **File:** `api/main.py` (`get_bookmark_comments`, baseline `:683`)
- **Current state:** Takes only `bookmark_id` and a `db` session. No `current_user`.
- **Impact:** Enumerate sequential integer IDs to read comments attached to any user's
  bookmark. Classic IDOR.
- **Type:** Security — IDOR / broken access control.

### P0-5 — `POST /api/comments/save/{bookmark_id}` — no auth, unauthenticated write to any bookmark
- **File:** `api/main.py` (`save_comments_to_bookmark`, baseline `:712`)
- **Current state:** No `current_user`. Fetches comments for a caller-supplied
  `video_url_or_id` and writes them onto a caller-supplied `bookmark_id`.
- **Impact:** Unauthenticated write into another user's data. Also a **content-injection
  primitive**: the attacker plants arbitrary text that later reaches the model and the
  victim's UI.
- **Type:** Security — IDOR (write) + injection vector.

### P0-6 — `SECRET_KEY` falls back to a public default value
- **File:** `api/auth.py:11`
- **Current state:** `SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")`.
  No startup assertion that the env var is actually set.
- **Impact:** If the env var is ever missing or misspelled in production, every JWT
  becomes forgeable by anyone who has read this repository — full authentication bypass
  for every account, silently.
- **Type:** Security — cryptographic failure / insecure default.

### P0-7 — No password policy of any kind
- **File:** `api/main.py:146` (`register`)
- **Current state:** Whatever string arrives is hashed and accepted. Empty string, `"a"`,
  `"password"` — all valid. There is elaborate *email* validation
  (`api/email_validation.py`) and **zero** password validation.
- **Impact:** Accounts trivially guessable; combined with P0-8 (no login rate limit),
  mass account takeover.
- **Type:** Security — weak authentication.

### P0-8 — No rate limiting on authentication or on paid AI operations
- **File:** `api/rate_limiter.py` (the only limiter in the codebase)
- **Current state:** `rate_limiter` is a **single in-process global counter**
  (`RateLimiter(max_requests=50, time_window=3600)`), shared across all users, and used
  **only** by the YouTube transcript path. It is not per-user, not per-IP, not
  distributed, and not applied to:
  - `/auth/login` — unlimited brute force
  - `/auth/register` — unlimited account creation / spam
  - `/api/ask`, `/api/bookmarks/{id}/ask` — unlimited paid inference
  - `/api/bookmarks/{id}/summary?refresh=true` — unlimited forced regeneration
  - `/api/bookmarks/{id}/reprocess?force=true` — unlimited forced re-download +
    re-transcription (the single most expensive operation in the product)
- **Impact:** Credential brute force, account-creation spam, and unbounded cost
  amplification. A single authenticated user can drain the AI budget.
- **Type:** Security + abuse + cost.

### P0-9 — No account deletion and no data export
- **Current state:** No such endpoint exists anywhere in `api/`. `ProfileView.swift`
  contains no deletion affordance.
- **Impact:**
  - **App Store Guideline 5.1.1(v)** requires in-app account deletion for any app with
    account creation. **This is an automatic rejection.**
  - GDPR Art. 15 (access/portability) and Art. 17 (erasure), and CCPA equivalents.
- **Design note:** `Bookmark.canonical_content_id` is `ondelete="SET NULL"`
  (`api/models.py:49`), so deletion needs a deliberate design pass: user rows and
  user-scoped tables cascade, but orphaned canonical content, its transcripts/frames/
  chunks/embeddings, and cached thumbnails need an explicit garbage-collection story.
- **Type:** Compliance blocker (App Store) + legal blocker (privacy law).

### P0-10 — Live SQLite databases are committed to git
- **Files:** `bookmarks.db`, `api/bookmarks.db` (both tracked)
- **Current state:** `HEAD` contains a real user row (email + bcrypt hash) and 11
  bookmarks. `.gitignore` covers `.env` (verified never committed) but not `*.db`.
- **Impact:** Small today — the developer's own account. But the pattern guarantees that
  production user data enters git history the moment the DB is larger. Unrecoverable once
  the repo is shared, forked, or published.
- **Type:** Security — data exposure in VCS.

### P0-11 — Unauthenticated LLM/compute proxies running on the operator's API key
- **Files:**
  - `api/main.py`: `POST /api/transcript`, `GET /api/transcript`,
    `GET /api/transcript/{video_id}`, `GET /api/transcript/languages`, `POST /api/comments`
  - `web/app/api/ai-summary/route.ts`
  - `web/app/api/transcript/ai-summary/route.ts`
- **Current state:** No auth, no rate limit, no quota. The Next.js routes accept arbitrary
  caller-supplied title/description/transcript/comments and forward them to Gemini using
  `process.env.GEMINI_API_KEY`.
- **Impact:** Free LLM inference and free YouTube scraping for anyone on the internet,
  billed to you. Also drags your IP into YouTube/TikTok rate-limiting and blocking.
- **Type:** Security + abuse + direct financial exposure.

### P0-12 — `/api/ops/usage?mine=false` leaks platform-wide telemetry to any authenticated user
- **Files:** `api/routes_intelligence.py:383` (endpoint), `api/ai/telemetry.py:127`
  (`summarize`)
- **Current state:** `mine` is a client-controlled boolean. Passing `false` sets
  `user_id=None`, which drops the `AND user_id = :uid` filter and returns **global**
  aggregates: total spend, token counts, audio seconds, proxy bytes, cache-hit rate,
  failure counts, and a per-operation and per-platform breakdown.
- **Related:** `GET /api/ops/queue` (`routes_intelligence.py:390`) exposes job-queue depth
  and state counts to any authenticated user.
- **Impact:** Any user with a free account learns your unit economics, total volume, and
  operational health. Competitive intelligence leak; also useful reconnaissance for
  timing a cost-amplification attack.
- **Type:** Security — broken access control (missing admin authorization tier).

---

## 4. HIGH severity (P1)

Not strictly launch-blocking in every case, but each is a real exploitable weakness or a
material compliance risk. Treat P1-13, P1-14, and P1-22 as effectively blocking.

### P1-13 — yt-dlp SSRF and unbounded resource consumption in the background worker
- **File:** `api/pipeline/ingest.py:187` (`fetch_metadata(cc.canonical_url)`),
  `api/pipeline/acquire.py`
- **Current state:** When `detect_platform()` returns `"other"`, the pipeline still calls
  `acquire.fetch_metadata()`, handing an arbitrary user-supplied URL to yt-dlp's **generic
  extractor**, executed inside the worker process. There is:
  - no host allowlist, no private/link-local IP rejection,
  - no `max_filesize` on `download_audio` / `download_video_lowres`,
  - no wall-clock ceiling on the overall job.
- **Data path:** Fetched content is written to `cc.title`, `cc.description`, and
  `cc.metadata_json` (capped at 60,000 chars) — and `description` is returned to the user
  through search results. So this is SSRF **with a read-back channel**.
- **Impact:** Internal network reach from the worker, plus disk/bandwidth exhaustion via a
  single crafted URL.
- **Note:** `Bookmark.url` is a Pydantic `HttpUrl`, so the scheme is constrained to
  http/https and `strip_tracking()` rewrites the scheme to `https` — but private and
  internal **hosts** are entirely unrestricted.
- **Type:** Security — SSRF + resource exhaustion.

### P1-14 — Prompt injection from saved content poisons the SHARED canonical cache
- **Files:** `api/services/intelligence.py:189-195` (`ask_this` context assembly),
  `api/pipeline/understanding.py` (`extract`), `api/pipeline/frames.py:210` (vision
  system prompt), `api/pipeline/ingest.py` (persistence)
- **Current state:** Transcript text and frame OCR text are concatenated directly into
  model prompts with no delimiting, no escaping, and no "untrusted content" framing.
- **Why this is worse than ordinary prompt injection:** The model output is persisted to
  `content_understanding` and `content_chunks`, which are **keyed by canonical content and
  shared across every user who saves that URL**. An attacker who controls a video's audio
  or on-screen text therefore poisons the `tl_dr`, `key_points`, `entities`, and retrieval
  chunks that are subsequently served to **every other user**.
- **Amplifiers:** `?refresh=true` on the summary endpoint and `?force=true` on
  `/reprocess` let the attacker re-trigger generation on demand, unmetered (see P0-8).
- **Boundary clarification:** This is **not** a cross-user data *leak* — no victim data
  reaches the attacker. It is a cross-user **integrity/content-injection** failure riding
  the canonical caching layer. The confidentiality boundary itself holds (see §2, C-1..C-6).
- **Type:** Security — prompt injection with cross-tenant blast radius.
- **Unresolved design decision:** see §7, D-1.

### P1-15 — Temporary media (extracted video frames) is never fully cleaned up
- **File:** `api/pipeline/frames.py:278` (`cleanup_frames`), `api/pipeline/ingest.py:388`,
  `:466`
- **Current state — four separate leaks:**
  1. `cleanup_frames()` unlinks individual JPEG files but **never removes the
     `/tmp/sava_frames_*` directory**. Empty directories accumulate forever.
  2. Frames dropped by `deduplicate()` are not present in `picked`, so they are
     **never deleted at all** — real image files persist.
  3. Any exception raised inside the vision stage is caught by the broad
     `except Exception` and cleanup is skipped entirely — the whole frame directory leaks.
  4. Failed yt-dlp downloads never reach `workdirs`, so partially-downloaded video files
     in `/tmp/sava_video_*` / `sava_audio_*` are never cleaned by the `finally` block.
- **Impact:** Frames extracted from users' saved video content sit on disk indefinitely.
  This directly contradicts the stated invariant in `acquire.py:286` ("Media is never
  retained after ingest"). Privacy exposure plus unbounded disk growth.
- **Type:** Privacy + reliability.

### P1-16 — `/static` is public, has predictable filenames, and is never cleaned
- **Files:** `api/main.py:54` (mount), `api/ingestors/instagram_api.py:114-131` (writes)
- **Current state:** `StaticFiles` mounted at `/static` with no authentication. Instagram
  thumbnails are written as `static/thumbnails/instagram_<shortcode>.jpg` — trivially
  enumerable from a public shortcode. Files are never deleted, including on bookmark
  deletion, and grow without bound.
- **Additional issue:** the path is relative to CWD, which is why both `static/thumbnails/`
  and `api/static/thumbnails/` exist in the tree with different contents — the same
  launch-method drift that `api/config.py` was written to solve for the database.
- **Impact:** Any private media that ever lands in this directory is world-readable. Today
  the content is public-origin, but the mechanism has no protection to lose.
- **Type:** Privacy + data retention.

### P1-17 — User enumeration on login
- **File:** `api/main.py:159-170`
- **Current state:** Unknown email → `404 "Email not found"`. Known email, wrong password
  → `401 "Incorrect password"`.
- **Impact:** Confirms which email addresses have Sava accounts. Combined with P0-8, this
  makes targeted brute force efficient.
- **Type:** Security — information disclosure.

### P1-18 — CORS is development configuration
- **File:** `api/main.py:56-72`
- **Current state:** `allow_origins` lists only localhost ports;
  `allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+"`; `allow_credentials=True`;
  `allow_methods=["*"]`; `allow_headers=["*"]`. **No production origin appears at all.**
- **Type:** Security — misconfiguration; also a functional blocker for the web client.

### P1-19 — Interactive API documentation is unconditionally exposed
- **File:** `api/main.py:43-50` — `docs_url="/docs"`, `redoc_url="/redoc"`,
  `openapi_url="/openapi.json"`, with no environment gate.
- **Impact:** Publishes a complete, machine-readable map of the API — including every
  unauthenticated endpoint listed in §3.
- **Type:** Security — information disclosure.

### P1-20 — Debug endpoint shipped in the application
- **File:** `api/main.py` — `GET /test/instagram-thumbnail` (baseline `:472`)
- **Current state:** Echoes back a caller-supplied URL. No auth. Serves no product purpose.
- **Type:** Hygiene / attack surface.

### P1-21 — Internal exception details returned to clients
- **File:** `api/main.py` — `detail=f"Internal server error: {str(e)}"` at baseline
  `:552`, `:574`, `:595`, `:641`, and in the TikTok comments handler at `:790`.
- **Impact:** Leaks library names, file paths, and internal state to unauthenticated
  callers. Note the surrounding handlers already return generic messages elsewhere, so
  this is inconsistent rather than intentional.
- **Type:** Security — information disclosure.

### P1-22 — AI provider data handling has no enterprise terms
- **File:** `api/ai/gemini.py:32-33`
- **Current state:** Uses `google.generativeai` configured with an AI Studio consumer key
  (`AIza…` prefix, per `api/.env`). Sent to it: full transcripts, frame images, user notes
  (via `ask_this`), and chat history.
- **Missing:** Vertex AI / enterprise endpoint, a Data Processing Agreement, zero-retention
  configuration, and any `safety_settings` block.
- **Impact:** Consumer AI Studio terms permit human review and use for model improvement.
  **You cannot make a truthful privacy-policy claim about user content on this setup**, and
  App Store privacy nutrition labels would be inaccurate.
- **Type:** Privacy / legal blocker.

### P1-23 — JWT design has no revocation and several structural weaknesses
- **Files:** `api/auth.py:25-35`, `api/auth.py:56-72`,
  `ios/Sava/Features/Auth/SessionStore.swift:81`
- **Current state:**
  - `sub` is the **email address**, not a stable user id — breaks on any future email change.
  - No `jti`, no `iat`, no `iss`, no `aud`; `jwt.decode` validates only signature and `exp`.
  - **No revocation list and no refresh token.** Sign-out is purely client-side (delete
    from Keychain) — a stolen token remains valid for its full lifetime with no server-side
    kill switch. There is no way to log a user out, ban an account, or respond to a breach.
- **Config drift (important):** `api/.env:4` sets `ACCESS_TOKEN_EXPIRE_MINUTES=10080`
  (7 days) but `api/auth.py:13` **hardcodes `30` and never reads the env var**. The system
  is currently safe *by accident*. The next person to "fix" the unused variable will ship
  7-day non-revocable tokens. Also note `SessionStore.swift:40` documents a "30-min TTL",
  matching the code and not the env file.
- **Type:** Security — session management.

### P1-24 — `docker-compose.yml` ships weak credentials on an exposed port
- **File:** `docker-compose.yml`
- **Current state:** `POSTGRES_PASSWORD: sava_pass` hardcoded; port `5432` published to
  `0.0.0.0`.
- **Type:** Security — credential + exposure misconfiguration.

### P1-25 — `run_api.py` binds all interfaces with autoreload enabled
- **File:** `run_api.py` — `host="0.0.0.0"`, `reload=True`.
- **Impact:** A development server reachable from the network. `reload=True` in particular
  must never run in production.
- **Type:** Security — deployment misconfiguration.

### P1-26 — Third-party scraping credentials held in plaintext environment variables
- **Files:** `api/ingestors/registry.py:33` (`IG_USERNAME`, `IG_PASSWORD`,
  `INSTAGRAM_SESSIONID`), `api/tiktok_comment_service.py:14` (`TIKTOK_COOKIES`)
- **Impact:** A server compromise hands the attacker a live Instagram account (username +
  password + session) and an authenticated TikTok session. These are *real accounts on
  third-party platforms*, with their own blast radius and their own ToS consequences.
- **Related:** `api/main.py` TikTok handler returns `refresh_instructions` describing how
  to re-harvest cookies from DevTools — operational detail exposed to API callers.
- **Type:** Security — secrets management.

### P1-27 — DNS resolution in the unauthenticated registration path
- **File:** `api/email_validation.py:16`
- **Current state:** `validate_email()` is called with deliverability checking at its
  default (enabled), performing an MX/DNS lookup per registration attempt.
- **Impact:** Unauthenticated, unthrottled outbound DNS driven by an anonymous caller
  (amplification vector), plus a slow, network-dependent registration path that fails when
  DNS is degraded.
- **Type:** Security + availability.

---

## 5. MEDIUM severity (P2)

### P2-28 — Substring-based platform detection matches attacker-controlled hosts
- **Files:** `api/content/identity.py:56-71`, and duplicated in `api/main.py`
  (`detect_platform`) and `api/ingestors/registry.py:252` (`_detect_platform`)
- **Current state:** Matching is `"youtube" in h`, `"tiktok" in h`, `"x.com" in h`, etc.
  So `youtube.evil.com` classifies as `youtube`, and `xx.com` / `notx.com` classify as
  `twitter`.
- **Impact:** Routes attacker-controlled hosts into platform-specific ingestors that make
  assumptions about the host they are talking to. Also note the logic is triplicated with
  three slightly different implementations.

### P2-29 — No length caps on user-supplied text
- `Bookmark.url` and `Bookmark.note` are unbounded `Text`. `BookmarkUpdate.note` has no
  validator. Unbounded per-user storage growth; also inflates prompt size (and cost) in
  `ask_this`, which injects the note verbatim.

### P2-30 — Logging records users' complete save history in plaintext
- **File:** `api/main.py:40` (`logging.basicConfig(level=logging.INFO)`) plus
  `logger.info(f"...{url}")` calls throughout `api/`, `api/ingestors/`, `api/pipeline/`
- **Current state:** Full URLs, video IDs, and titles are logged at INFO across the whole
  application. No redaction, no structured logging, no stated retention policy.
- **Impact:** Application logs constitute a behavioural profile of every user (what they
  watch, when, how often) sitting in whatever log sink the host provides. This is
  regulated personal data under GDPR.

### P2-31 — Full scraper payloads retained indefinitely
- `Bookmark.raw` (`api/models.py:43`) stores the complete scraper response as JSON and is
  never pruned or reviewed for what it contains.

### P2-32 — Keychain token survives app uninstall
- **File:** `ios/Sava/Core/Security/KeychainStore.swift`
- iOS Keychain items persist across app deletion. Nothing clears the token on first launch
  after install, so a reinstall silently resumes a prior session — surprising to the user
  and wrong on a shared or resold device.

### P2-33 — iOS ships no production base URL
- **File:** `ios/Sava/Core/Networking/AppConfig.swift:14`
- Defaults to `http://127.0.0.1:8000`. Overridable via `SAVA_API_BASE` (env or Info.plist),
  but no HTTPS production host is baked in. ATS itself is fine (see C-10) — this is a
  release-configuration prerequisite, not an ATS problem.

### P2-34 — Large volume of dead and duplicated code
- `recovered_files/` (contains a second `app.py`, `auth.py`, `main.py` — **not** mounted,
  verified), plus roughly ten ingestor variants: `tiktok_old.py`,
  `tiktok_broken_backup.py`, `tiktok_api_backup.py`, `tiktok_recovered.py`,
  `tiktok_optimized.py`, `instagram_api_old.py`, `registry_backup.py`,
  `registry_optimized.py`, `youtube_optimized.py`, `email_validation_backup.py`.
- **Impact:** Auditable attack surface nobody reviews; high risk that a future import
  accidentally activates an unreviewed variant. Note `recovered_files/auth.py` carries the
  same insecure `SECRET_KEY` default as P0-6.

### P2-35 — Unencrypted local database backups
- `.sava_backups/` holds ~49MB of unencrypted SQLite copies. Gitignored (correctly), but
  needs an encryption + retention policy before it becomes production data.

### P2-36 — Latent cross-user note leak: one keyword argument away
- **Files:** `api/pipeline/chunking.py:157` (`build_document_text(..., note=...)`),
  `api/pipeline/ingest.py:580` (call site)
- **Current state:** The function accepts `note=` and its output is embedded into
  `content_embeddings` — a **shared, cross-user table**. The ingest call site correctly
  omits it (verified, see C-2), so there is **no leak today**.
- **Risk:** There is no test asserting this. Passing that one kwarg — an entirely natural
  "improve search relevance" change — would silently embed one user's private notes into a
  vector served to every other user who saved the same content. This is the single most
  dangerous latent change in the codebase.
- **Recommended treatment:** remove the parameter entirely, or pin it with a regression
  test (see §8, AC-6).

---

## 6. RECOMMENDATIONS — remediation plan

> **These are recommendations only. None have been implemented, and none represent a
> decision that has already been made.** Effort estimates are rough.

### Guiding principle

The highest-leverage single change is **Phase 1, item 2**: make authentication the
router-level default with explicit opt-outs, rather than an opt-in decorator each author
must remember. That structurally eliminates P0-1, P0-4, P0-5, and P0-11 and prevents the
next instance of the same class. Fixing the five endpoints individually leaves the
underlying pattern intact.

### Phase 1 — before any external user (est. 1–2 days)

| # | Action | Closes |
| --- | --- | --- |
| 1 | Delete `GET /users` and `GET /test/instagram-thumbnail`. | P0-1, P1-20 |
| 2 | **Make auth the default.** Apply `Depends(get_current_user)` at the router/app level with an explicit public allowlist (`/`, `/auth/register`, `/auth/login`). Then add ownership checks to `/api/comments/{id}` and `/api/comments/save/{id}`. Audit the full route inventory against the new default. | P0-4, P0-5, P0-11 |
| 3 | Delete `/api/thumbnail`, **or** gate it behind auth + an allowlist of known platform CDN hostnames + resolved-IP private-range rejection + redirect cap + response size cap. Deleting is preferred; the iOS client can load platform CDN URLs directly. | P0-2 |
| 4 | Fail fast at startup if `SECRET_KEY` is unset or equals the default string. **Rotate the current key** (invalidates all existing tokens — acceptable pre-launch). | P0-6 |
| 5 | Drop `unique=True` from `Bookmark.url` and `YouTubeDetails.video_id`; add `UniqueConstraint("user_id", "url")` and move YouTube details to a per-bookmark key. **Requires a migration** — `api/migrations.py` is introspect-then-ALTER and does not currently handle constraint drops on SQLite, so this needs a table rebuild. Update the `registry.py` `IntegrityError` message accordingly. | P0-3 |
| 6 | Add per-IP **and** per-account rate limits on `/auth/*` with exponential backoff/lockout, and per-user quotas on every AI endpoint (`/api/ask`, `/ask`, `/summary?refresh`, `/reprocess?force`). Replace the in-process counter with something shared (Redis, or a DB-backed counter consistent with the existing "no new infrastructure" stance in `api/jobs.py`). | P0-8, and mitigates P1-14 |
| 7 | Add a password policy (minimum length, breach-list check) and make login errors uniform — same status, same message, comparable timing, for both unknown-email and wrong-password. | P0-7, P1-17 |
| 8 | `git rm --cached bookmarks.db api/bookmarks.db`; add `*.db` to `.gitignore`; purge from history; force a password reset for the exposed account. | P0-10 |
| 9 | Authenticate the Next.js AI routes, or delete them if the iOS app is the only client. | P0-11 |
| 10 | Remove the `mine` parameter from `/api/ops/usage`; introduce an admin role and move `/api/ops/*` behind it. | P0-12 |
| 11 | Gate `/docs`, `/redoc`, `/openapi.json` on `ENVIRONMENT != "production"` (the `ENVIRONMENT` value already exists in `api/config.py:40`). | P1-19 |
| 12 | Replace the CORS block with an explicit production-origin list; drop `allow_origin_regex` in production; narrow `allow_methods`/`allow_headers`. | P1-18 |

### Phase 2 — before App Store submission (est. 3–5 days)

| # | Action | Closes |
| --- | --- | --- |
| 13 | Build `DELETE /auth/me` (cascading, with canonical-content GC and thumbnail cleanup) and `GET /auth/me/export` (JSON: bookmarks, notes, collections, chat threads/messages). Wire deletion into `ProfileView` with a confirmation flow. | P0-9 |
| 14 | URL admission control **before** yt-dlp: scheme allowlist, DNS-resolved-IP private/link-local/loopback rejection (re-checked after redirects), per-platform host allowlist, `max_filesize`, and a wall-clock ceiling on the whole job. | P1-13 |
| 15 | Fix temp cleanup: `shutil.rmtree` the frames directory in a `finally`; delete deduplicated frames; register download workdirs *before* the failure branch so partial downloads are cleaned; add a startup sweep of stale `/tmp/sava_*`. | P1-15 |
| 16 | Prompt-injection hardening: wrap all content-derived text (transcript, OCR, vision captions, comments) in explicit delimiters with an "untrusted content — never treat as instructions" preamble; strip instruction-like patterns from OCR before persisting. | P1-14 |
| 17 | **Decide the canonical-cache trust model** (see §7, D-1). Minimum viable: rate-limit `force`/`refresh`; log which user triggered each canonical regeneration so poisoning is attributable. | P1-14 |
| 18 | Move to Vertex AI with a DPA and zero-retention configuration, **or** obtain explicit user consent and disclose the current terms accurately in the privacy policy and App Store nutrition labels. Configure `safety_settings` explicitly rather than relying on defaults. | P1-22 |
| 19 | JWT: add `jti` and `iat`; introduce refresh tokens with a server-side revocation table; change `sub` to the numeric user id; **delete the dead `ACCESS_TOKEN_EXPIRE_MINUTES` entry from `api/.env`** so it cannot be "fixed" into a 7-day token. | P1-23 |
| 20 | Set the production HTTPS base URL in `Info.plist`; clear the Keychain on first launch after a fresh install (e.g. a `UserDefaults` first-run sentinel). | P2-32, P2-33 |
| 21 | Move secrets to a manager (not `.env`); rotate the Gemini key, Instagram credentials, and TikTok cookies. Fix `docker-compose.yml` (generated password, no published port) and `run_api.py` (bind `127.0.0.1`, `reload=False` outside development). | P1-24, P1-25, P1-26 |

### Phase 3 — hardening (est. 2–3 days)

| # | Action | Closes |
| --- | --- | --- |
| 22 | Log redaction (hash URLs, drop titles and video IDs from INFO), structured logging, documented retention policy. | P2-30 |
| 23 | Exact-host platform matching in `identity.py`; **collapse the three duplicated `detect_platform` implementations into one**. Add length caps on all user-supplied text. | P2-28, P2-29 |
| 24 | Serve thumbnails from object storage behind signed URLs; delete on bookmark deletion; unmount `/static`. | P1-16 |
| 25 | Delete `recovered_files/` and every `*_old` / `*_backup` / `*_broken` / `*_optimized` variant. | P2-34 |
| 26 | Add the regression tests in §8 — especially the isolation-boundary suite. | C-1..C-6, P2-36 |
| 27 | Disable deliverability/DNS checking in `validate_email_comprehensive`, or move it behind a rate limit. | P1-27 |
| 28 | Encryption + retention policy for `.sava_backups/`; prune `Bookmark.raw`. | P2-31, P2-35 |

---

## 7. Unresolved decisions and out-of-scope items

### D-1 — Canonical-cache trust model (UNRESOLVED — needs a product decision)
The cross-user caching design is economically essential (per the comments in
`api/pipeline/ingest.py` and `api/services/intelligence.py`, acquisition is ~78% of the
cost of a save and caching is what makes second-and-subsequent saves free). But it means
**one user's attacker-controlled content produces artifacts served to all other users**
(P1-14). Options, none yet chosen:
- (a) Accept it, and rely on prompt hardening alone.
- (b) Do not share model-derived understanding until *N* independent users have saved the
  same content (raises the cost of poisoning; delays value for the first saver).
- (c) Share only deterministic artifacts (transcript, metadata) cross-user and generate
  understanding per-user (kills most of the economic benefit).
- (d) Attribute and quarantine: record the triggering user on each canonical regeneration
  and allow rollback.

**This decision gates Phase 2 item 17 and should be made before that work starts.**

### D-2 — Admin authorization tier (does not exist)
P0-12 assumes an admin role that the schema has no concept of. `User` has no role or
`is_admin` column. Introducing `/api/ops/*` behind an admin tier requires deciding how
admin identity is modelled and provisioned.

### D-3 — Account deletion semantics for canonical content (undesigned)
When the last user who saved a piece of canonical content deletes their account, what
happens to its transcript, frames, chunks, and embeddings? Retaining them is cheaper and
preserves the cache; deleting them is cleaner for erasure requests. This must be decided
as part of Phase 2 item 13, and the answer must be reflected in the privacy policy.

### D-4 — Web client status (unclear)
`web/` contains Next.js API routes that duplicate backend functionality and call Gemini
independently (P0-11). It is unclear whether the web client is a live surface or an
artifact of an earlier phase. **If it is not shipping, deleting `web/app/api/` closes two
blockers outright.**

### F-1 — Rate-limiter architecture (deferred)
The current in-process limiter cannot work across multiple API processes. Phase 1 item 6
requires choosing shared state; a DB-backed counter would be consistent with the
deliberate "no new infrastructure" stance documented in `api/jobs.py`.

### F-2 — Postgres migration path (deferred)
Findings were reviewed against both the SQLite and Postgres code paths, but the system
was running on SQLite at audit time. The pgvector/HNSW path in `api/vectors.py` and
`api/migrations.py` is marked `pragma: no cover` and has not been exercised against a live
Postgres. Re-verify P0-3's migration and the `knn` `where_sql` scoping on Postgres before
launch.

### F-3 — Multi-worker safety (not audited)
`api/worker.py` supports `--concurrency N` and claims SQLite serialisation is sufficient.
Not stress-tested. Out of scope for this audit.

### F-4 — yt-dlp cookie support (NOT AUDITED — added after baseline)
`api/pipeline/acquire.py` has since gained `YTDLP_COOKIEFILE` and
`YTDLP_COOKIES_FROM_BROWSER` handling, plus `fetch_captions_via_ytdlp()`. This introduces
a **new credential surface** (a browser cookie jar or cookiefile readable by the worker)
that was not covered here and shares the concerns in P1-26. It also adds a new outbound
`requests.get(sub_url, ...)` on a URL derived from extractor output — **review it against
P1-13's SSRF criteria.** Needs a follow-up review.

### F-5 — `ASYNC_SAVE` flag (NOT AUDITED — added after baseline)
A new `ASYNC_SAVE` config flag appeared in `api/config.py` / `api/main.py` after the
baseline. Its effect on the save path, and on where user data is written, was not reviewed.

---

## 8. Acceptance criteria

A finding may be marked resolved only when the corresponding criterion below is met by an
automated test, not by inspection.

- **AC-1 (P0-1/4/5/11/12):** An unauthenticated request to every non-allowlisted route
  returns 401. A test enumerates `app.routes` and asserts each is either in an explicit
  public allowlist or rejects an anonymous request.
- **AC-2 (P0-3):** Two distinct users can each save the same URL successfully, and each
  sees only their own bookmark in `GET /api/bookmarks`.
- **AC-3 (P0-2 / P1-13):** A request whose URL resolves to a loopback, link-local, or
  RFC1918 address is rejected before any network fetch — tested for both the thumbnail
  path (if retained) and the yt-dlp ingest path, including after an HTTP redirect.
- **AC-4 (P0-6):** The application refuses to start when `SECRET_KEY` is unset or equals
  the default string.
- **AC-5 (P0-8):** N+1 failed logins within the window are rejected; N+1 AI requests from
  one user within the window are rejected.
- **AC-6 (P2-36 / C-2) — the isolation-boundary suite.** User B must not be able to read
  user A's `note`, chat message, or collection through **any** endpoint. Separately, a test
  must assert that no user-scoped field is ever written into a canonical table — including
  an explicit assertion that `build_document_text` is never called with `note=` from the
  ingest path.
- **AC-7 (P0-9):** After `DELETE /auth/me`, the user's bookmarks, notes, collections, chat
  threads, chat messages, and cached thumbnails are gone, and `GET /auth/me` with the old
  token returns 401.
- **AC-8 (P1-15):** After a pipeline run that fails mid-vision-stage, no `/tmp/sava_*`
  directory remains.
- **AC-9 (P1-23):** A token presented after sign-out is rejected.
- **AC-10 (P0-7):** Registration with an empty or trivially weak password is rejected.

---

## 9. Findings index

| ID | Severity | Title | Primary file |
| --- | --- | --- | --- |
| P0-1 | Blocker | `GET /users` unauthenticated PII dump | `api/main.py` |
| P0-2 | Blocker | `/api/thumbnail` unauthenticated SSRF | `api/main.py` |
| P0-3 | Blocker | `Bookmark.url` globally unique — multi-tenancy break | `api/models.py` |
| P0-4 | Blocker | `/api/comments/{id}` IDOR (read) | `api/main.py` |
| P0-5 | Blocker | `/api/comments/save/{id}` IDOR (write) | `api/main.py` |
| P0-6 | Blocker | `SECRET_KEY` insecure default | `api/auth.py` |
| P0-7 | Blocker | No password policy | `api/main.py` |
| P0-8 | Blocker | No rate limiting on auth or AI | `api/rate_limiter.py` |
| P0-9 | Blocker | No account deletion / data export | — |
| P0-10 | Blocker | Databases committed to git | `bookmarks.db` |
| P0-11 | Blocker | Unauthenticated LLM/compute proxies | `api/main.py`, `web/app/api/` |
| P0-12 | Blocker | `/api/ops/usage?mine=false` global telemetry leak | `api/routes_intelligence.py` |
| P1-13 | High | yt-dlp SSRF + resource exhaustion | `api/pipeline/ingest.py` |
| P1-14 | High | Prompt injection poisons shared canonical cache | `api/services/intelligence.py` |
| P1-15 | High | Temp media never cleaned up | `api/pipeline/frames.py` |
| P1-16 | High | `/static` public, predictable, unbounded | `api/main.py` |
| P1-17 | High | User enumeration on login | `api/main.py` |
| P1-18 | High | CORS is dev config | `api/main.py` |
| P1-19 | High | `/docs` unconditionally exposed | `api/main.py` |
| P1-20 | High | Debug endpoint shipped | `api/main.py` |
| P1-21 | High | Internal exceptions returned to clients | `api/main.py` |
| P1-22 | High | Consumer-tier AI provider, no DPA | `api/ai/gemini.py` |
| P1-23 | High | JWT: no revocation, no refresh, email as `sub` | `api/auth.py` |
| P1-24 | High | `docker-compose` weak creds, exposed port | `docker-compose.yml` |
| P1-25 | High | `run_api.py` binds 0.0.0.0 with reload | `run_api.py` |
| P1-26 | High | Third-party scraping creds in plaintext env | `api/ingestors/registry.py` |
| P1-27 | High | DNS lookups in unauthenticated registration | `api/email_validation.py` |
| P2-28 | Medium | Substring platform detection | `api/content/identity.py` |
| P2-29 | Medium | No length caps on user text | `api/models.py` |
| P2-30 | Medium | Logs record full save history | `api/main.py` |
| P2-31 | Medium | `Bookmark.raw` retained indefinitely | `api/models.py` |
| P2-32 | Medium | Keychain token survives uninstall | `KeychainStore.swift` |
| P2-33 | Medium | No production base URL on iOS | `AppConfig.swift` |
| P2-34 | Medium | Dead / duplicated code | `recovered_files/`, `api/ingestors/` |
| P2-35 | Medium | Unencrypted local DB backups | `.sava_backups/` |
| P2-36 | Medium | Latent cross-user note leak via `note=` kwarg | `api/pipeline/chunking.py` |
