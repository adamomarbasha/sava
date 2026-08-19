# Onboarding & First-Seven-Days Experience Design

**Area:** Sava iOS — new-user onboarding, activation, and week-one retention design
**Branch audited:** `feat/intelligence-foundation`
**Date:** 2026-08-18
**Status:** Design specification. Nothing in this document has been implemented.
**Companion artifact:** https://claude.ai/code/artifact/734e9db4-44b1-45ed-b40b-9cc77734efe2 ("Sava's First Seven Days")

---

## 0. How to read this document

This is a **design specification**, not an implementation report. It was produced by reading the
current state of the `feat/intelligence-foundation` branch — the FastAPI backend under `api/` and
the SwiftUI client under `ios/` — and designing the new-user experience *backwards from what the
system can honestly deliver at each library size*.

The document is organized as:

- **§1 Context** — what a future agent needs to know to act on this.
- **§2 Current-state findings** — what exists in the code today, with file citations. Facts only.
- **§3 The governing constraints** — four numbers read out of the backend that determine the entire
  onboarding sequence. Everything downstream follows from these.
- **§4 The design thesis** — the single sentence the first session must prove.
- **§5–§9 The designed experience** — session one (8 beats), day 1, day 3, day 7, notifications.
  These are **recommendations**, not current state.
- **§10 The anti-gamification charter** — explicit product rules.
- **§11 Gap analysis** — what must be built, with severity. **Launch blockers are here.**
- **§12 Measurement** — activation metric and funnel targets.
- **§13 Unresolved decisions and future-phase items.**
- **§14 Acceptance criteria.**

Everything in §2 and §3 is verifiable against the repo. Everything in §5 onward is proposed and
was not built.

---

## 1. Context for a future session

### 1.1 What Sava is

Sava is a save-and-recall product for short- and long-form social video. A user saves a TikTok,
YouTube, Instagram, or other post; a backend pipeline resolves the URL to canonical content,
fetches metadata, transcribes audio, optionally samples video frames, generates a structured
"understanding" record, and builds embeddings. The user can then search their library
semantically, read AI summaries, ask questions about a single save ("Ask This"), ask questions
across their whole library ("Ask Sava"), and organize saves into collections.

The signature capture mechanic is the **iPhone Action Button**: a single physical press saves what
you are watching without opening the app, via an App Intent.

### 1.2 The core product problem this document addresses

**Sava becomes more valuable as the library grows.** A brand-new account is therefore the worst
version of the product any user will ever see. Onboarding must get a stranger to a genuine
"holy shit" moment fast, while the library is nearly empty, without a long tutorial — and must keep
the payoff compounding through the first week.

The features the user should experience in week one:
Action Button capture · Search · Summary · Ask This · Collections · Ask Sava.

### 1.3 The hard constraint on seeding

Every instinct says: seed a new account with demo content so day one feels full. **Do not.** A
stranger's own saves are the entire point — a library of someone else's videos proves nothing and
teaches the wrong mental model. You cannot be amazed that a stranger's video was found.

The design therefore has to work with a genuinely empty library, which is why §3 matters so much.

---

## 2. Current-state findings

All findings below were read from the working tree of `feat/intelligence-foundation`. File paths are
repo-relative.

### 2.1 Backend — what exists and works

The intelligence layer is substantially complete. `api/routes_intelligence.py` mounts the following
alongside the pre-existing bookmark/transcript/comment routes, without changing their contracts:

| Endpoint | Purpose | Notes |
| --- | --- | --- |
| `GET /api/ai/modes` | Copy for the Ask Sava model picker | Deliberately provider-neutral; never exposes a vendor or model name. Returns `auto` / `fast` / `advanced`, `auto` is `is_default`. |
| `GET /api/bookmarks/{id}/status` | Processing status | Returns `state`, `level`, `content_type`, `stages{}`, `error`, `has_transcript`, `has_understanding`, `linked`. |
| `POST /api/bookmarks/{id}/reprocess` | Re-run the pipeline | Accepts `force`; creates the canonical link if missing; enqueues `content.process`. |
| `GET /api/search` | Hybrid retrieval | Vectors + keyword + structured filters. **No generative model runs here** — it is designed to stay fast. Returns `took_ms` and a `semantic` availability flag. |
| `GET /api/bookmarks/{id}/summary` | AI summary | `refresh` and `mode` params. Cached via `ContentUnderstanding`. |
| `POST /api/bookmarks/{id}/ask` | Ask This (single save) | Threaded; persists turns to `ChatThread` / `ChatMessage`. |
| `POST /api/ask` | Ask Sava (library-wide) | Retrieval-first RAG. Threaded. |
| `GET /api/threads`, `GET /api/threads/{id}/messages` | Chat history | Scoped `save` or `library`. |
| `GET /api/bookmarks/{id}/related` | Related saves | No model involved. Returns `{"results": [], "reason": "not_processed"}` when unlinked. |
| `GET /api/resurface` | "Worth revisiting" | Deterministic ranking, no model. |
| `GET/POST /api/collections` | List / create | **Create returns `suggestions` in the same response.** |
| `GET /api/collections/{id}` | Collection contents | |
| `POST /api/collections/{id}/items` | Add items | |
| `GET /api/collections/{id}/suggestions` | Suggestions for an existing collection | |
| `POST /api/collections/rebuild` | Rebuild automatic collections from the user's own save patterns | Backgrounded by default via the job queue. |
| `GET /api/ops/usage`, `GET /api/ops/queue` | Cost telemetry and queue stats | |

Auth exists at `POST /auth/register` and `POST /auth/login` (`api/main.py:128`, `api/main.py:155`).
Registration returns a usable session; there is no email-verification gate in the flow.

### 2.2 The processing state machine

`api/models.py:131` defines `ProcessingState`:

```
QUEUED       = "queued"
FETCHING     = "fetching"
TRANSCRIBING = "transcribing"
ANALYZING    = "analyzing"
READY        = "ready"
PARTIAL      = "partial"    # usable, but some enrichment failed
FAILED       = "failed"
```

`CanonicalContent` carries `processing_state`, `processing_level` (integer 0–4), and
`stage_status` (a JSON blob of per-stage `ok` / `failed` / `skipped`). `Bookmark` mirrors
`processing_state`.

`api/pipeline/ingest.py::process_content` runs the ladder and is **idempotent and resumable**. It
short-circuits when already `READY` at the current `PIPELINE_VERSION` unless forced. The observed
level progression:

- **L1 `FETCHING`** — metadata: title, description, creator name/handle, duration, thumbnail.
- **L2 `TRANSCRIBING`** — transcript; may be `cached` or `skipped: no audio`.
- **L3 `ANALYZING`** — classify (sets `content_type` and `visual_dependency`), then vision (frame
  sampling; can be skipped by strategy, by low visual dependency, or when ffmpeg is unavailable),
  then understanding.
- **L4** — embeddings, then terminal state: `PARTIAL` if any stage recorded `failed`, else `READY`.
  `FAILED` on an unhandled exception.

Understanding for long-form content is **deliberately deferred** to first open rather than
generated at ingest — `api/services/intelligence.py::get_or_create_summary` documents this as the
single largest avoidable inference cost, since most long saves are never reopened. The threshold is
`LAZY_SUMMARY_OVER_SECONDS`, default **1200 seconds / 20 minutes** (`api/config.py:71`). The stage
status records `deferred (long-form, on first open)`.

### 2.3 The summary payload shape

`api/services/intelligence.py::understanding_payload` returns:

```
content_type, tl_dr, key_points[], topics[], entities{}, typed_data{},
chapters[], sources_used[], schema_version, model, created_at
```

`get_or_create_summary` returns honest unavailability reasons rather than empty content:
`not_linked`, `ai_unavailable`, `no_content`, `generation_failed` — each with a user-facing
`message`, plus `processing_state` where known.

### 2.4 iOS client — what exists

The SwiftUI client under `ios/Sava/` has a complete design system and a working signed-in shell.

- **Design system** (`ios/Sava/DesignSystem/`): "ink & paper" semantic colors with one restrained
  indigo signal accent (`SavaColors.accent` = `#5457FF` light / `#7C7EFF` dark). Light mode is warm
  paper (`#FAFAF7`), dark is designed deep ink (`#0A0A0C`), not an inversion. Type scale is SF Pro
  with `.rounded` on display sizes. `Haptics.success()` / `.error()` / `.tap()` / `.selection()`
  exist. `LiquidBackground`, `GlassCard`, `StatusView`, `InlineBanner`, `SavaPrimaryButton`,
  `FlexibleWrap` are all built.
- **Shell** (`ios/Sava/Features/Shell/AppShell.swift`): a floating glass tab bar over
  Library / Search / Profile with a prominent center `+` Save action. The library view model is
  shared so a quick save appears in the feed immediately via `insertSaved`.
- **Auth** (`ios/Sava/Features/Auth/`): email + password + confirm, mode switching between sign-in
  and register, inline error banner. `RootView` routes on `session.phase`
  (`.restoring` / `.signedOut` / `.signedIn`).
- **Capture** (`ios/Sava/Features/Capture/`): `SaveToSavaIntent` is a real App Intent with
  `openAppWhenRun = false`. It accepts an optional `link: URL?` and an optional
  `screenshot: IntentFile?`. `SavaShortcuts` registers it as an App Shortcut with the phrases
  "Save to Sava" / "Save this to Sava" / "Add to Sava", making it assignable to the Action Button.
- **Capture strategy ladder** (`ios/Sava/Features/Capture/CapturePipeline.swift`), in priority
  order: (1) direct URL from the Shortcut, (2) screenshot resolution via the backend resolver, only
  when no direct URL exists, (3) clipboard/copy-link fallback, (4) graceful failure with an honest
  message. A screenshot is only used when a direct URL is unavailable — never captured or uploaded
  redundantly.
- **Library** (`ios/Sava/Features/Library/`): two-column masonry, platform filter pills with live
  counts, a plain count in the header, pull-to-refresh, a `processingIDs` set already threaded into
  `BookmarkGrid`, and a written empty state.
- **Detail** (`ios/Sava/Features/Detail/`): transcript section, comments section, metadata section,
  and an `AskSavaSection`.
- **Profile** (`ios/Sava/Features/Profile/ProfileView.swift`): already contains an Action Button
  setup card with a three-step guide (Settings → Action Button → swipe to Shortcut → choose "Save
  to Sava") and an "Open Shortcuts" deep link, plus a "How capture works" card that is candid about
  per-platform behavior and states that screenshots are used only when a link isn't available, sent
  securely, and never saved to Photos.

### 2.5 iOS client — what is missing or stubbed

These are **current-state facts**, not recommendations. Severity is assigned in §11.

1. **`IntelligenceService.isEnabled` is hard-coded `false`** — `ios/Sava/Features/Detail/Intelligence.swift`.
   The file's own doc comment explains it was written *before* the backend Q&A endpoints existed and
   deliberately refuses rather than fabricating an answer. Those endpoints now exist
   (`POST /api/bookmarks/{id}/ask`, `GET /api/bookmarks/{id}/summary`). The service also does not
   call the summary route at all.
2. **`AskSavaSection` is a truthful "coming soon" stub** — it renders example question chips and a
   disabled, non-hit-testable input with a "Conversational answers are coming soon" badge. Its
   example questions are hard-coded per platform.
3. **Nothing polls `GET /api/bookmarks/{id}/status`.** `LibraryViewModel` exposes a `processingIDs`
   set, but there is no client for the status endpoint and no stage-level UI anywhere.
4. **No collections UI exists on iOS.** A case-insensitive search for "collection" across
   `ios/**/*.swift` returns zero files. The entire collections API is unconsumed.
5. **No notification infrastructure of any kind.** Searching iOS for `UNUserNotification` returns
   only `UINotificationFeedbackGenerator` (haptics). There is no permission request, no APNs
   registration, no device-token table on the server, and no send path.
6. **The Action Button setup guide assumes every device has an Action Button.** `ProfileView` shows
   the Settings → Action Button steps unconditionally. There is no device-model check and no
   documented fallback capture path for iPhone 15 (non-Pro), 14, or SE.
7. **No Share extension target** exists in the iOS project.
8. **No onboarding state is persisted anywhere** — neither client-side nor server-side. There is no
   record of which setup steps an account has completed.

---

## 3. The governing constraints (read from the backend)

These four numbers determine the entire onboarding sequence. They are not design opinions; they are
properties of the shipped code.

### 3.1 Eight saves before automatic collections exist

`api/services/collections.py`:
```
MIN_CLUSTER_SIZE   = 3
MIN_SAVES_FOR_AUTO = 8
```
`rebuild_auto_collections` returns early below `MIN_SAVES_FOR_AUTO` (checked twice — once on row
count, once on usable vector count), and discards any cluster with fewer than `MIN_CLUSTER_SIZE`
members.

**Consequence: Collections cannot be a first-session feature.** Any first-run "create your first
collection!" prompt would be scaffolding around a feature that cannot yet produce anything.

### 3.2 Ten saves is the Ask Sava retrieval window

`api/services/intelligence.py::ask_sava(..., max_saves: int = 10)`. Library questions are
retrieval-first: if `retrieve_for_library_question` returns no context blocks, the function returns
the honest string *"I couldn't find anything in your library that relates to that yet."*

**Consequence: with three saves the honest answer is thin; with thirty it is startling. Ask Sava is
a week-one finale, not an opener.**

### 3.3 Anything under seven days old scores 0.1 on resurfacing

`api/services/intelligence.py::worth_revisiting` is a deterministic ranker with no model. Its age
curve:

```
age_days < 7    → age_score = 0.1
age_days <= 60  → age_score = 1.0 - abs(age_days - 30) / 60.0     # peaks at day 30
else            → age_score = max(0.15, 0.6 - (age_days - 60)/500)
```
Plus `+0.25` if the save carries a user note and `+0.15` if it has a `tl_dr`.

**Consequence: week one gets no help from resurfacing.** Designing a day-3 "remember this?" moment
would ship a surface with nothing good to put in it. The first resurfacing push belongs around
**day 21**, when the earliest saves are finally old enough to have been genuinely forgotten. That is
a week-four design and should be built then (see §13).

### 3.4 One save is enough for search

`api/services/retrieval.py::search_library` is hybrid retrieval that runs the moment a single item
has embeddings. No generative model is involved, so it is fast.

**Consequence — and this is the pivot of the whole design: search is the only high-value surface
that is fully alive at library size one, so search carries the entire first session.**

### 3.5 What follows from reading the four together

The sequence writes itself:

- **Search is the aha, because search is the only thing that works when you have nothing.**
- Summary and Ask This ride along for free on the same single save.
- Collections wait for the eighth save.
- Ask Sava waits for the week.
- **Nothing is shown before it can be impressive.**

---

## 4. Design thesis

### 4.1 The single sentence the first session must prove

> **"I can find that video again using words nobody ever typed on screen."**

Saving is table stakes — the iOS Share sheet already does it, for free, everywhere. The thing no
other app on the phone can do is **find a fourteen-minute video by a sentence somebody said at 8:41
inside it.** That is the entire pitch, and it is demonstrable with a *single* save.

### 4.2 The engineered moment

The first session is engineered backwards from one specific event: the user types (or taps) a phrase
that does **not** appear in the title, thumbnail, or caption of the thing they saved ninety seconds
ago — and Sava returns it, with the timestamp and the matched sentence.

Everything before that moment exists to make it happen faster. Everything after it exists to make it
repeat.

---

## 5. Session one — install to aha, target 2:30

Eight beats. **Nothing here is a tutorial screen** — every step either configures something
irreversible or produces real output from the user's own content. The elapsed times are targets and
are also the design constraint: if a beat cannot fit its budget it gets cut or deferred, not
compressed into smaller type.

### Beat 01 — First launch · T+0:00

**One screen.** Not a carousel — a carousel is a promise that the app is about to be complicated. The
screen states the claim, shows the one gesture that matters, and moves on. No "Skip" link, because
there is nothing to skip.

Screen content:
- Sava mark.
- Headline: **"Save it once. Find it forever."**
- Subhead: "Press your Action Button on any video. Sava watches it, listens to it, and makes it
  searchable — by anything that happens inside it."
- Primary: **Get started**
- Ghost: **I already have an account**

Uses the existing `LiquidBackground` mesh. No illustration, no phone-in-hand mockup.

- **Budget:** one tap, ~6 seconds of reading.
- **Never do:** ask for notification permission here. Ask for anything here.
- **Fail state:** none — this screen cannot fail.

### Beat 02 — Account creation · T+0:12 · `POST /auth/register`

The existing flow is already close to right: email, password, confirm, done. Three rules to keep it
that way:

1. **No name field.** The backend does not store one and nothing in the app displays one — Profile
   shows the email. Do not collect what you will not use.
2. **No email-verification wall.** Registration returns a usable session immediately. Verification,
   if it ever ships, belongs behind a soft banner on day 3 — never between a stranger and their
   first save.
3. **No "what do you want to use Sava for?" survey.** It is a delay dressed as personalization, and
   the answer is always worse than the signal from their first five real saves.

**Error copy — email already registered:**
> "That email already has a Sava account. **Sign in instead**."

…with the mode already switched and the email pre-filled. The user does not retype anything.

- **Budget:** ~20 s with a password manager, ~35 s without.
- **Instrument:** `account_created` — the denominator for every rate in §12.

### Beat 03 — Action Button setup · T+0:35 · **highest-risk step in the product**

This is the highest-risk step in the whole product, because **iOS will not let the app do it.** The
user has to leave, walk three levels into Settings, and come back. Every second of ambiguity here is
a drop-off. Three design decisions carry it.

#### Decision one: branch on the device before saying a word

The Action Button exists on iPhone 15 Pro and later. **Showing its setup guide to someone on an
iPhone 14 is the single most damaging thing this flow could do** — it makes the core feature look
unavailable. Detect the model and show the right primary path:

| Device | Primary capture path | How it is framed |
| --- | --- | --- |
| iPhone 15 Pro and later | Action Button → Sava shortcut | "Your Action Button" — the marquee setup |
| iPhone 15 / 14 / SE | Control Centre control + Back Tap (double-tap the back of the phone) | "Your two-tap save" — presented as equally deliberate, **never as a downgrade** |
| Any device | Share sheet extension | Always installed, never explained here — it is the path they will discover on their own |

#### Decision two: verify by detection, not by asking

**Never show a "Did that work?" confirmation button.** A user who taps "Yes" without having done it
is now permanently broken and invisible to instrumentation. Instead the app waits on the real
signal: the intent firing at least once. The screen sits there, calm, and flips itself the moment it
sees a save land.

Screen 03a (guided, waiting):
- Title: "Set up your Action Button"
- Subhead: "Takes about fifteen seconds. We'll wait right here."
- Checklist: ① Open Settings → Action Button · ② Swipe to **Shortcut** · ③ Choose **Save to Sava**
  — steps tick off on return from Settings where detectable; the last resolves only on a real intent
  fire.
- Primary: **Open Settings**
- Status line: "Waiting for your first press…"
- Ghost: **Set this up later**

Screen 03b (verified):
- Green check, "Action Button is live", "Press it on anything you're watching. Sava saves without
  opening."
- Primary: **Try it now**
- Fires only on real evidence. Success haptic reuses the existing `Haptics.success()`.

#### Decision three: "Set this up later" is always visible and never punished

It routes straight to Beat 05 using the in-app `+` save with a pasted link, which reaches the same
aha by a slower road. Roughly a third of users will take it, and the ones who see the payoff come
back and configure the button on their own. **A blocked user configures nothing.**

- **Budget:** 15 s target · 40 s realistic · abandon-safe at any point.
- **Instrument:** `action_button_verified` — verified by intent fire, never self-reported.
- **Fail state:** 90 s with no press → the screen quietly offers "Save a link instead" without ever
  saying the setup failed.

### Beat 04 — Shortcut setup · T+0:55 · optional, one tap

There are genuinely two tiers of capture, and the app should be straight about it rather than
pretending only one exists.

| Tier | What it is | Setup cost |
| --- | --- | --- |
| **Tier 1 — automatic** | Assigning the bare `Save to Sava` intent to the Action Button works with zero extra setup. It saves whatever supported link is on the clipboard — exactly the flow after tapping Copy Link in TikTok or Instagram. | None |
| **Tier 2 — one extra tap** | A prebuilt "Sava Capture" shortcut that takes a screenshot and hands it to the intent, so the pipeline can resolve the post without a copied link. Installed from a single iCloud link, then assigned to the button instead. | One tap |

**Present tier 2 as an upgrade offered *after* the first successful save, not before it.** A user who
has never saved anything has no reason to care why screenshot resolution matters; a user whose first
clipboard save just worked will install it in one tap when told it makes the press work even when
they haven't copied a link.

**Upgrade prompt — shown once, after save #1:**
> **Make it work without copying**
> Right now Sava reads the link from your clipboard. Add the Capture shortcut and it can read the
> screen instead — so a single press works anywhere.
> `[ Add it — one tap ]`   `Not now`

### Beat 05 — The first save · T+1:10 · **their content, not ours**

**The single most important rule in this entire document: the first save must be something the user
actually wants.** Not a demo video, not a Sava-branded explainer, not a curated "try this one." The
whole emotional payload of the aha depends on the user having a genuine relationship with the
content — you cannot be amazed that a stranger's video was found.

So the flow sends them out. The "Try it now" button on the verified screen opens nothing; it
dismisses the app entirely with one line of copy, which is a jarring and memorable and correct thing
for an app to do:

> **Go watch something.**
> Open TikTok, YouTube, wherever you actually are. When something's worth keeping, press the button.
> We'll be here.

(Full screen, then the app backgrounds itself after ~4 s.)

For users on the "later" path, the in-app `+` sheet does the same job with a pasted link and
identical downstream behavior. Either way `POST /bookmarks` fires, the row appears in the library
grid immediately via the existing `insertSaved`, and processing begins.

- **Budget:** however long they scroll. Realistically 30–120 s.
- **Instrument:** `first_save` plus `capture_source` (`direct` / `resolved` / `clipboard`) — **the
  strategy mix is the health metric for the whole capture ladder.**
- **Fail state:** capture errors are already written well in `CapturePipeline`. Keep them: they say
  what to do next, not what went wrong internally.

### Beat 06 — The first processing state · T+1:20 · **the tour lives here**

The pipeline takes twenty seconds to two minutes. That gap is unavoidable — and it is also the only
moment in the product where a user will happily watch a screen that explains what Sava does.

**So the first time only, processing gets a full-screen narrated treatment. Every save after that
gets a three-pixel bar on the card.**

This is the tutorial. It just isn't shaped like one, because every line on it is a true statement
about a job being done to *their* video right now.

Three states of the narrated screen:

- **06a — `fetching`, level 1.** Placeholder thumbnail, "Reading the post…", four named jobs listed
  with the first in progress. No percentage, no ETA. Footer: "You can close this — we'll keep going."
- **06b — `transcribing`, level 2.** The instant metadata lands, the **real title and creator replace
  the placeholder** — this is the first proof it is working. "Listening — 14 minutes of audio."
- **06c — `ready`, level 4.** All four jobs checked with **real values from the response** ("Heard
  1,847 words", "Six topics, four chapters") — never rounded up, never invented. CTA: **See what Sava
  got.**

#### State → copy mapping

| State | Level | What the user reads | Behavior |
| --- | --- | --- | --- |
| `queued` | 0 | "In line — starting now" | Only shown if it lasts > 2 s; otherwise skipped silently. |
| `fetching` | 1 | "Reading the post" | On completion, title / creator / duration / thumbnail swap in live. **This is the beat that earns trust.** |
| `transcribing` | 2 | "Listening — 14 minutes of audio" | Duration comes from metadata. Longest stage; the one that makes the wait feel justified. |
| `analyzing` | 3 | "Working out what it's about" | Covers classify → vision → understanding. Users do not need three sub-stages; the pipeline does. |
| `ready` | 4 | "Ready" + real counts | **Success.** Haptic, then the CTA. |
| `partial` | 4 | "Ready — no audio to transcribe, so answers come from the caption and the frames" | **Degraded.** Name the missing stage *specifically*. Never a generic "some steps failed." |
| `failed` | — | "Couldn't open this one — it may be private or region-locked." | **Blocked.** "Try again" → `POST /api/bookmarks/{id}/reprocess`. If it is the user's very first save, immediately offer "save a different one" rather than leaving them stuck on a broken hero moment. |

#### Polling policy

1 s for the first 15 s → 2 s to 60 s → 5 s thereafter → hard stop at 3 minutes with "this one's
taking a while — we'll notify you."

**Only the first-ever save polls in the foreground like this.** From save two onward the library
reads `processing_state` on refresh and animates the card bar.

#### Long-form caveat

For content over `LAZY_SUMMARY_OVER_SECONDS` (20 min default), understanding is deferred and the
stage status reads `deferred (long-form, on first open)`. The narrated screen must not present this
as a failure — it reaches `ready` legitimately, and the summary generates on first open (see Beat
07).

### Beat 07 — The first summary · T+1:50 · `GET /api/bookmarks/{id}/summary`

"See what Sava got" opens the detail view **scrolled to the summary** — the summary is the top
section on first open, above transcript and comments. It reads as a set of claims about the video,
not as an essay, because the payload is structured: `tl_dr`, `key_points`, `topics`, `chapters`,
`entities`, `typed_data`.

Two design calls matter more than the layout:

1. **Chapters are tappable and jump the transcript.** This is the cheapest possible demonstration
   that Sava actually watched the thing — the user taps "Battery life", the transcript scrolls to
   8:41, and the abstraction becomes concrete in one gesture.
2. **Topics are live search links.** Every topic chip is a query. **This is how the user gets to
   Beat 08 without being told to** — they tap a topic out of curiosity and land in a search result.
   The bridge to the aha is built into the summary itself.

For long-form content the summary is generated lazily on first open rather than at ingest, which
means the first open of a 40-minute video can take a few seconds. **Show the section skeleton with
"Reading the transcript…"** — do not show an empty section that fills in silently.

Handle the honest-unavailability reasons the endpoint already returns (`not_linked`,
`ai_unavailable`, `no_content`, `generation_failed`) with their supplied `message`, not a generic
error.

### Beat 08 — The first search · T+2:10 · `GET /api/search` · **HOLY SHIT**

Here is the whole trick, and it is honest:

> **When the library has exactly one processed item, the search tab pre-fills suggested queries
> drawn from that item's `topics` and `key_points` — deliberately choosing phrases that do not
> appear in its title.**

The user taps "battery gains come from software." They never typed that. It was never on screen. It
was said out loud somewhere in the middle of a fourteen-minute video, and Sava returns the video with
the timestamp and the sentence. **That is the moment the product explains itself, and it costs one
tap and zero tutorial.**

It is not a trick in the dishonest sense — those words genuinely came from the transcript, and every
subsequent search behaves identically. The onboarding is only choosing a good first question, the way
a good demo picks a good example.

Screen 08a (seeded search): search field, "Try one of these", three generated queries, and the
disclosure line *"Pulled from what Sava heard in your save — none of it is in the title."*
**Suggestions exist only while the library is small; they fade out permanently past ~10 saves.**

Screen 08b (the hit): result count and `took_ms`, the card with thumbnail, title, **the matched
excerpt as a pull-quote**, and "matched at 8:41 — tap to jump." The matched excerpt and timestamp are
what sell it; a bare title row would not.

#### The only permission ask in session one

**Right here, immediately after the first successful search** — not at launch, not before the first
save. The user has just seen the product work; this is the one moment they have a reason to say yes,
and the ask is specific about what it buys them:

> "Want us to tell you when a save finishes? Long videos can take a couple of minutes. That's the
> only kind of thing we'll send."

Expected accept rate is roughly triple a launch-time prompt, and the promise is one that is actually
kept — see §9.

- **Instrument:** `first_search_with_open` — a query that returned a result the user then opened.
  **This is the activation event.**
- **Target:** 60% of accounts within the first session; 75% within 48 hours.

---

## 6. Session one, extended — Ask This finds itself

**Ask This needs no onboarding beat at all**, provided one thing is true: the input is **already
focused and already suggested** at the bottom of the detail view the user is now sitting in. The
existing section shape is right — example chips above a real input — it simply has to stop being a
"coming soon" stub.

- **Generate the example questions from the item's own understanding payload** rather than
  hard-coding them per platform. A user looking at a recipe video should see "what's the oven
  temperature?"; a user looking at a review should see "what did he not like?" The current
  platform-based fallbacks stay as the no-understanding case.
- **Hide the mode picker.** Auto / Fast / Advanced is a real capability (`GET /api/ai/modes`), but a
  week-one user has no basis for choosing and every extra control dilutes the moment. Default to
  `auto`, ship the picker in Profile, surface it inline only after the fifth question asked.
- **Citations are the point.** Answers return `citations` with timestamps. Render each as a tappable
  chip under the answer that seeks the transcript. **An uncited answer is indistinguishable from a
  chatbot; a cited one is proof Sava read the video.**

---

## 7. Day 1 — the return (~18 h later)

Between session one and day one, the realistic user saved somewhere between two and eight more
things during an evening scroll, **most of them without opening the app at all** — which is the
intended behavior and also means they have no idea what happened to any of them. Day one's job is to
close that loop once, and then get out of the way.

**The trigger is content-based, not calendar-based**: the digest fires when the last save from a
batch of two or more finishes processing, delivered at the hour they most often save. **If they saved
nothing, nothing sends.** This is the difference between a notification and a nag, and it is not a
subtle one.

Notification copy:
> **Sava** · now
> **Last night's 6 saves are ready**
> Three of them are about home espresso. Ask Sava anything about them.

The second line only appears if a real cluster was found.

In-app on day one: everything is already processed. **No "finish setting up" card, no unread badges,
no ceremony.** The one addition is a single line above the grid, shown once and never again:

> *"Everything from last night is read and searchable."*

It disappears on scroll. That sentence is the entire day-one message — the work happened while they
weren't looking.

- **Instrument:** `d1_return`, and separately `d1_search` — **the second is the one that predicts
  week-two retention.**
- **Do not:** prompt to rate the app; prompt to invite a friend; show a setup checklist; show a
  streak. **There is no day-one upsell of any kind.**

---

## 8. Day 3 — the first collection (fires at save #8, not on a date)

**Day three is a threshold, not a date.** The moment the eighth save finishes processing,
`POST /api/collections/rebuild` runs in the background, and if clustering produces a group of three
or more, one card appears at the top of the library:

> **NOTICED A PATTERN**
> **Home espresso**
> 4 of your saves are about dialling in a grinder.
> [four thumbnails]
> `[ Keep this collection ]`   `No thanks`

**Sava proposes, the user decides.** Dismissal is permanent for that cluster.

The **manual path is the more interesting one and it works from day one**: creating a collection
returns `suggestions` in the same response, so the user types a name and immediately gets a populated
shelf. **Naming a thing and having Sava fill it in is a second, smaller aha** — and it is the moment
collections stop looking like folders.

**If clustering finds nothing at save eight, no card appears and nothing is said.** Silence is
correct; an empty "Collections" tab with a "create your first collection!" prompt is the exact
species of hollow scaffolding this design is trying to avoid.

- **Fires when:** save #8 reaches `ready` — never on a timer.
- **Notification:** only if the app has been closed > 6 h *and* a real cluster exists. Otherwise it
  waits in-app.

---

## 9. Day 7 — Ask Sava, and the honest retro (~15–30 saves)

Week one closes with **the feature that could not have worked on day one.** With fifteen-plus
processed saves, library-wide retrieval finally has something to retrieve, and an answer can span
four videos from three platforms saved across six days. That is a categorically different experience
from anything else on the phone, and it is worth waiting a week to show properly.

Ask Sava gets promoted from a stub to a first-class surface — **a persistent field at the top of
Search** — and it opens seeded with a question **generated from their actual top topics**, so the
first library question is one they can immediately judge the answer to.

Answer rendering: sources are chips, each opening the save at the cited moment. **Grounded answers
only** — with no matches, the backend already says so and the UI must not dress that up.

The retro (shown once, not a weekly report):
> **YOUR FIRST WEEK**
> **24 saves · 5h 12m of video**
> Sava read all of it so you don't have to re-watch any of it.
> Home espresso 6 · Weeknight cooking 5 · Camera gear 4

Facts about their content. **No score, no rank, no comparison to other users, no "you're in the top
8%."**

### What day seven is deliberately NOT

**It is not a resurfacing moment.** Everything in the library is under seven days old and therefore
scores 0.1 on the revisit ranker — the curve does not peak until day thirty (§3.3). Building a
"remember this?" feature into week one would mean shipping a surface with nothing good to put in it.

**The first resurfacing push belongs around day twenty-one**, when the earliest saves are finally old
enough to have been genuinely forgotten. That is a week-four design and should be built then. See
§13.

---

## 10. Notifications — four in seven days, every one carrying content

Permission is requested **once**, after the first successful search, with a specific promise (Beat
08). These four are the entire week-one budget. **Each is triggered by something that actually
happened, so a user who saves nothing receives nothing** — which is the correct outcome and also the
reason the permission is worth granting.

| When | Trigger — never a timer | What it says | Suppressed if |
| --- | --- | --- | --- |
| **Session 1** | First-ever save reaches `ready` while the app is backgrounded | "Your first save is ready — 1,847 words, four chapters." | App is in the foreground (they are watching the stages already). |
| **Day 1** | Last save of a batch of 2+ finishes; delivered at their modal save hour | "Last night's 6 saves are ready." Second line only if a real cluster exists. | Fewer than two saves, or they have already opened the app since. |
| **Day 3-ish** | Clustering at save #8 produces a group of 3+ | "Four of your saves are about the same thing." | No cluster found, or app opened in the last 6 h. |
| **Day 7** | ≥10 processed saves on the seventh day after registration | "Your first week: 24 saves, 5h 12m. Ask Sava about any of it." | Under 10 saves — the retro would be sad rather than impressive. |

### Never sent

- "You haven't opened Sava in 3 days."
- "Don't break your streak."
- "Your library misses you."
- Anything about a percentage complete.
- **Anything fired by a clock with no new content behind it.**

Rationale: a notification that exists to cause a session, rather than to deliver one, is the fastest
way to get the permission revoked — **and the permission is worth more than the session.**

---

## 11. The anti-gamification charter — state, not score

Sava is a tool for people who save more than they can remember. The reward for using it is **finding
things** — which is already intrinsically motivating, and which cheap mechanics actively cheapen.

**The rule: show facts about their content, never scores about their behavior.**

### Not in this product

| Mechanic | Why it is excluded |
| --- | --- |
| **Streaks** | They punish the week someone is busy, for a product whose value survives absence perfectly well. |
| **Badges, levels, XP** | Nothing here is an achievement. Saving a video is not an accomplishment. |
| **Progress rings on the library** | A library is never "complete" — implying otherwise is nonsense. |
| **Red dot badges** | Manufactured anxiety with no information content. |
| **Confetti, celebration modals** | A success haptic and the actual result is the celebration. |
| **"Complete your profile"** | There is no profile to complete. There is barely a profile. |
| **Daily save goals** | **Optimising for save count optimises for junk in the library, which makes search worse. The metric is actively hostile to the product.** |
| **Leaderboards or social comparison** | Someone's saved videos are a private thing. |

### What replaces it

- **A plain count in the library header.** Already built. A fact, not a target.
- **Named work in progress.** "Listening — 14 minutes of audio" is more satisfying than a progress
  bar because it is true and specific.
- **Real numbers in the retro.** 5h 12m of video read. Their hours, their content, no ranking.
- **Findings, offered.** "Four of your saves are about the same thing" is Sava being observant.
  "You unlocked Collections!" is Sava being a game.
- **One setup checklist, which then vanishes.** The only progress affordance permitted, because it
  maps to genuine one-time configuration and permanently disappears — it is not a recurring score.
- **Silence when there's nothing to say.** The strongest anti-gamification signal available, and the
  cheapest to build.

---

## 12. Gap analysis — what this design needs that isn't built

The backend carries most of this already — summaries, Ask This, Ask Sava, search, collections, and
processing status all exist as working endpoints. **The gaps are concentrated on the client, and one
of them is a single boolean.**

Severity key: **BLOCKER** = the designed session-one aha cannot happen without it ·
**NEEDED** = a designed week-one beat cannot ship without it · **NICE** = improves a beat that
otherwise still works.

### BLOCKER — Flip `IntelligenceService.isEnabled` and wire the real routes
The iOS Ask surface is a truthful "coming soon" stub written before the endpoints landed. They have
landed. Ask This and the summary section are both gated behind a boolean and a service that does not
call the real routes yet.
- Files: `ios/Sava/Features/Detail/Intelligence.swift`, `ios/Sava/Features/Detail/AskSavaSection.swift`
- Endpoints: `POST /api/bookmarks/{id}/ask`, `GET /api/bookmarks/{id}/summary`
- Blocks: Beat 07, Beat 08, §6

### BLOCKER — Processing status client
Nothing polls the status endpoint. The library exposes a `processingIDs` set but the narrated
first-save screen and the per-card bar both need the real stage stream.
- Endpoint: `GET /api/bookmarks/{id}/status` → `state`, `level`, `stages{}`, `error`,
  `has_transcript`, `has_understanding`
- Blocks: Beat 06

### BLOCKER — Onboarding state, server-side
Which beats a user has cleared must survive reinstall and follow them across devices. A small
`user_onboarding` row beats `UserDefaults` — this is also the only source of truth for "has this
account ever completed a search with an open?"
- Proposed: `GET/PATCH /api/onboarding` (new)
- Blocks: all beats (gating, resumption, and the funnel in §12)

### NEEDED — Notification infrastructure
No APNs registration, no permission request, no device-token storage, no send path anywhere in client
or API. All four week-one notifications depend on it, and all four are content-triggered, so the job
queue is the natural place to fire them.
- iOS: `UNUserNotificationCenter` + registration
- API: `device_tokens` table + handlers in `api/jobs.py` / `api/pipeline/handlers.py`
- Blocks: Day 1, Day 3, Day 7 notifications; the session-one background-completion notification

### NEEDED — Collections UI on iOS
The full API exists — list, create-with-suggestions, add items, rebuild — and **there is no Swift
file that references it.** Needs the found-collection card, the create sheet with inline suggestions,
and a shelf view.
- Endpoints: `GET/POST /api/collections`, `GET /api/collections/{id}`,
  `POST /api/collections/{id}/items`, `GET /api/collections/{id}/suggestions`,
  `POST /api/collections/rebuild`
- Blocks: Day 3

### NEEDED — Seeded search suggestions
The aha depends on generating first queries from a save's `topics` and `key_points` **while
filtering out phrases present in the title.** Small, and **the highest-leverage item on this list.**
- Derive client-side from the `GET /summary` payload; hide past ~10 saves
- Blocks: Beat 08

### NEEDED — Device capability branch + capture fallbacks
Action Button setup currently assumes every device has one. Needs a model check, a Control Centre
control, Back Tap guidance, and a Share extension as the universal floor.
- Files: `ios/Sava/Features/Profile/ProfileView.swift`, new share extension target
- Blocks: Beat 03 for every non-Pro device

### NICE — Prebuilt "Sava Capture" shortcut
A hosted iCloud shortcut that screenshots and passes the file to the intent, so tier-2 capture is one
tap instead of a manual build. `SaveToSavaIntent` already accepts `screenshot: IntentFile?`.
- Improves: Beat 04

### NICE — Chapter → transcript seeking
Chapters are already in the understanding payload; wiring them to scroll the transcript is the
cheapest available proof that Sava watched the video.
- `understanding_payload.chapters` ↔ `ios/Sava/Features/Detail/TranscriptSection.swift`
- Improves: Beat 07

---

## 13. Measurement

### 13.1 The north-star activation metric

> **Share of new accounts that run a search returning a result they then open, within 48 hours of
> registering.**

Not saves. Not sessions. Not day-one retention. **A user who has searched and found is a user who has
understood what Sava is for**, and every other week-one number moves as a consequence of that one. It
is also, usefully, **impossible to inflate with mechanics** — which is exactly why it is the right
metric for a product that has sworn off them.

### 13.2 Funnel and targets

| Step | Event | Target | Read the drop as |
| --- | --- | --- | --- |
| Account created | `account_created` | 100% | — |
| Capture configured | `action_button_verified` | 55% | Below 40% → the Settings hand-off is broken, or the device branch is misfiring |
| First save | `first_save` | 80% | Below 65% → capture is failing silently; check the strategy mix |
| First item ready | `first_ready` | 92% | The gap to `first_save` is the pipeline's real-world failure rate |
| First summary opened | `summary_open` | 85% | Below 70% → the ready-state CTA isn't compelling |
| **First search with an open** | `first_search_with_open` | **75%** | **ACTIVATION.** Below 55% → the seeded suggestions aren't landing |
| Eighth save | `save_count_8` | 45% | The real week-one commitment signal |
| Collection kept | `collection_kept` | 60% of those | Below 40% → clusters aren't coherent enough to propose |
| Ask Sava, week 1 | `ask_sava_first` | 35% | Depends almost entirely on library size, not on placement |

Additional events referenced elsewhere in this document: `capture_source`
(`direct`/`resolved`/`clipboard`), `d1_return`, `d1_search`.

### 13.3 Two diagnostics to watch alongside the funnel

1. **Capture strategy mix** (`direct` / `resolved` / `clipboard`) — tells you which platforms are
   quietly degrading.
2. **Time from save to ready at p90** — sets the ceiling on how good the first session can possibly
   feel. **If p90 crosses two minutes, no amount of copy on the processing screen will save it.**

---

## 14. Unresolved decisions, risks, and future-phase items

### Unresolved decisions (need a human call)

1. **Share extension scope.** This design assumes a Share sheet extension as the universal capture
   floor for all devices, but no such target exists and its scope (does it show a note field? a
   collection picker?) is undecided.
2. **Back Tap guidance feasibility.** Back Tap is configured in Accessibility settings and cannot be
   deep-linked as cleanly as the Action Button. The exact guidance flow for non-Pro devices needs
   validation on-device.
3. **Modal save hour.** The Day-1 digest is designed to deliver "at the hour they most often save,"
   which requires either a stored per-user statistic or a simpler heuristic. Not specified here.
4. **Email verification.** Currently absent. If it ships, this document's position is: soft banner on
   day 3, never a wall before the first save. That position has not been ratified.
5. **`user_onboarding` schema.** Proposed but not designed. Needs the field list agreed before
   implementation.

### Risks

- **The Action Button setup step is the single largest drop-off risk in the product**, because iOS
  forces a context switch the app cannot control or reliably observe. The detection-based
  verification in Beat 03 mitigates but does not eliminate this.
- **p90 processing latency is an upper bound on first-session quality.** If the pipeline is slow, the
  narrated screen becomes a waiting room rather than a tour.
- **The seeded-search aha depends on transcript quality.** For a save whose transcript stage is
  `skipped: no audio`, the seeded queries must fall back to caption/vision-derived topics or the
  moment does not land. This fallback is not specified in detail.
- **Clustering coherence is unproven at small library sizes.** The Day-3 collection card proposes a
  name and a group; if clusters are incoherent at 8–12 saves, the feature reads as noise. The
  `collection_kept` metric is the early-warning signal.

### Future-phase (explicitly out of week-one scope)

- **Day-21 resurfacing.** The first "worth revisiting" push, timed to when `worth_revisiting`'s age
  curve actually produces good candidates. This is a **week-four design** and should be built then,
  not now. Building it into week one would ship an empty surface (§3.3).
- **Ask Sava mode picker inline.** Surfaced only after the fifth question asked (§6).
- **Weekly retro.** The day-7 retro is explicitly shown once. A recurring weekly report is a separate
  product decision and risks crossing into the gamification territory §11 rules out.

### Security / privacy notes

No security issues were identified in the course of this design work — this was not a security
audit, and §11 of `docs/audits/production-security-privacy-audit.md` should be treated as the
authority there. Two privacy-adjacent design commitments are worth recording:

- **Screenshots are only captured when no direct URL is available**, are sent for resolution only,
  and are never written to Photos. This is already true in `CapturePipeline` and already stated
  honestly in `ProfileView`'s "How capture works" card. **Do not weaken this** — the tier-2 shortcut
  upgrade in Beat 04 must preserve it.
- **The notification permission promise in Beat 08 is specific** ("that's the only kind of thing
  we'll send") and §10's "never sent" list is what makes that promise true. Sending a
  clock-triggered re-engagement push would break a stated commitment to the user.

---

## 15. Acceptance criteria

The design is correctly implemented when all of the following hold:

### Session one
- [ ] First launch is **one** screen with no carousel and no permission request.
- [ ] Account creation collects **email and password only** — no name, no survey, no verification
      wall.
- [ ] The Action Button setup screen **branches on device model** and shows a non-downgrade-framed
      alternative on non-Pro hardware.
- [ ] Action Button setup is **verified by observing a real intent fire**, never by a user-tapped
      confirmation.
- [ ] "Set this up later" is present on the setup screen and routes to a working alternate path.
- [ ] The first save is **the user's own content** — the app hands off to the outside world rather
      than offering a demo item.
- [ ] The first processing state is a **full-screen narrated view with four named jobs**; every
      subsequent save shows only a card-level bar.
- [ ] Real title / creator / thumbnail **replace the placeholder live** when metadata lands.
- [ ] All seven `ProcessingState` values map to distinct, honest copy per the table in Beat 06;
      `partial` names the specific missing stage.
- [ ] Polling follows 1 s / 2 s / 5 s with a 3-minute hard stop.
- [ ] The summary opens with **tappable chapters that seek the transcript** and **topic chips that
      run searches**.
- [ ] Search suggestions on a small library are **generated from the save's own topics/key points and
      exclude phrases present in the title**.
- [ ] Search results show **the matched excerpt and its timestamp**, not just a title row.
- [ ] The notification permission is requested **exactly once, immediately after the first successful
      search**, with the specific promise copy.

### Week one
- [ ] The Day-1 digest is **triggered by a batch of 2+ saves completing**, never by a clock, and is
      suppressed if the app was opened since.
- [ ] The Day-3 collection card fires **at save #8 reaching `ready`**, only when a real cluster of 3+
      exists, and is silent otherwise.
- [ ] Creating a collection manually shows **suggestions returned in the same response**.
- [ ] Ask Sava is promoted to a first-class surface **only once the library has ~15 processed saves**.
- [ ] The Day-7 retro is shown **once** and contains no score, rank, or cross-user comparison.
- [ ] **No resurfacing surface ships in week one.**
- [ ] Total week-one notifications ≤ 4, every one content-triggered.

### Charter compliance
- [ ] No streaks, badges, levels, XP, progress rings on the library, red-dot badges, confetti,
      "complete your profile", daily save goals, or leaderboards exist anywhere in the app.
- [ ] The only progress affordance is the one-time setup checklist, which permanently disappears.

### Instrumentation
- [ ] All events in §13.2 fire, plus `capture_source`, `d1_return`, and `d1_search`.
- [ ] `first_search_with_open` is defined as *a query that returned a result the user then opened*
      and is reported as the activation metric.
- [ ] Capture strategy mix and save→ready p90 are both dashboarded.

---

## 16. Provenance

Every constraint cited in this document — the eight-save clustering floor, the ten-save retrieval
window, the seven-day resurfacing dead zone, the twenty-minute lazy-summary threshold, and the
processing state machine with its levels — was **read from the `feat/intelligence-foundation`
branch** rather than assumed.

The original design work was delivered as a published artifact
(https://claude.ai/code/artifact/734e9db4-44b1-45ed-b40b-9cc77734efe2). No production code was
modified during the design work or during the writing of this document.
