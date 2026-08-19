# Sava — Conversational Intelligence UX Specification

> **Document type:** Design specification + current-state audit
> **Area:** Ask This / Ask Collection / Ask Sava — the conversational surface over a user's saved library
> **Date produced:** 2026-08-18
> **Branch at time of audit:** `feat/intelligence-foundation`
> **Status:** Specification only. **Nothing in this document has been implemented.** No production code was modified when this was written.

---

## 0. How to use this document

This is the complete design specification for Sava's conversational intelligence, produced from a read-only audit of the backend (`api/`) and the iOS client (`ios/Sava/`). It is written so a future session with **no memory of the originating conversation** can act on it.

Read in this order:

1. **§1 Context** — what Sava is and what already exists in the codebase.
2. **§2 Design thesis** — the four non-negotiables. If a later decision conflicts with these, the decision is wrong.
3. **§3–§16** — the specification proper.
4. **§17 Current-state findings** — what is built today, verbatim from the audit.
5. **§18 Launch blockers & required backend deltas** — the gating work.
6. **§19 Risks, unresolved decisions, and future-phase items.**
7. **§20 Build order and acceptance criteria.**

Sections **§3–§16 are recommendations** (design, not yet built). Sections **§17 is current state** (observed facts about the code as of the audit date). §18–§19 are explicitly labelled blockers/risks.

---

## 1. Context for a future agent

**Sava** is a personal memory product: a user saves social media links (TikTok, YouTube, Instagram, Reddit, X, etc.); an ingestion pipeline acquires the content, transcribes it (Whisper), extracts frames + OCR when visually dependent, and produces a structured "understanding" record. That understanding, plus chunk-level embeddings, powers search and conversational Q&A.

### Architecture facts established during the audit

**Backend (`api/`, FastAPI + SQLAlchemy, SQLite locally / Postgres-capable):**

| File | Role |
|---|---|
| `api/routes_intelligence.py` | All intelligence endpoints. Mounted alongside existing routes; changes nothing pre-existing. |
| `api/ai/base.py` | Provider-neutral interfaces: `TaskType`, `Mode` (`auto`/`fast`/`advanced`), `Capability`, `ModelSpec`, `Completion`. |
| `api/ai/router.py` | **"Sava Auto" is this file, not a model.** Task×Mode → `ModelSpec` table, deterministic question-shape heuristics, fallback chain. |
| `api/ai/gemini.py` | The only configured provider today. |
| `api/ai/telemetry.py` | Per-call cost/latency recording; `summarize()` aggregates per user. |
| `api/services/retrieval.py` | Hybrid search (vector + keyword fusion, MMR diversification). **No model runs in search.** |
| `api/services/intelligence.py` | `get_or_create_summary`, `ask_this`, `ask_sava`, `worth_revisiting`. |
| `api/services/collections.py` | Manual + auto (clustered) collections. |
| `api/pipeline/understanding.py` | Classification into 16 `CONTENT_TYPES`, then type-specific structured extraction (`TYPED_SCHEMAS`). |
| `api/models.py` | `ChatThread` / `ChatMessage` already exist. |
| `api/config.py` | Central config; `EMBED_DIM=1536`, `LAZY_SUMMARY_OVER_SECONDS=1200`, vision modes. |

**Key backend behaviours the UX depends on:**

- `Mode` is `auto | fast | advanced`. `describe_modes()` returns exactly the copy `Sava Auto / Best model automatically`, `Fast / Quick everyday questions`, `Advanced / Deeper reasoning` — **and deliberately never names a vendor or model.** The product must hold this line.
- `classify_question()` in `api/ai/router.py` is a **deterministic regex heuristic** (no model call) deciding whether AUTO escalates: `_STRONG_REASONING` (compare/plan/why/themes/…) always escalates; `_WEAK_REASONING` (recommend/best/worth it/…) escalates unless the question is short and fact-shaped; length and source-count thresholds also escalate.
- `ask_this()` returns `{ok, answer, mode, citations[], grounded_in}` where each citation is `{start_s, end_s, timestamp, source: "transcript"|"vision", text}`.
- `ask_sava()` returns `{ok, answer, mode, sources[], grounded_in}` where sources are `RetrievedSave.to_dict()` and the answer text contains `[n]` markers.
- `retrieve_for_library_question()` is **two-stage**: pick ≤10 saves, then 2 chunks each. Only selected passages reach the model — the library is never dumped into a prompt.
- `ChatThread.scope` is `save | library | collection` and `ChatThread.collection_id` **already exists**. Ask Collection is schema-ready.
- `_thread_and_history()` replays the **entire** thread into every request (cost grows linearly with turn count).
- Failure reason codes returned to the client: `not_processed`, `not_linked`, `no_content`, `ai_unavailable`, `generation_failed`.
- `GET /api/ai/modes` returns `{modes, available}`; `available: false` when no provider is configured.

**iOS client (`ios/Sava/`, SwiftUI):**

- Design system: `SavaColors` (ink & paper; warm paper light `#FAFAF7`, designed deep ink dark `#0A0A0C`; single `accent` `#5457FF`/`#7C7EFF` used sparingly; `accentSoft` at 0.10/0.16 alpha), `SavaFont` (SF Pro, rounded on display sizes), `Spacing` (4pt grid), `Radius` (sm 10 / md 16 / lg 22 / xl 28 / pill), `SavaMotion` (springs: `tap`, `standard`, `smooth`, `bounce`, `ambient`, plus `respectingReduceMotion`), `Haptics`.
- `AppShell` = custom glass tab bar: **Library · Search · [+ Save] · Profile**.
- Existing components reused by this spec: `FlexibleWrap`, `FlowChips`, `LibrarySkeleton`, `BookmarkGrid`, `StatusView`, `InlineBanner`, `GlassCard`, `.pressable` button style.

---

## 2. Design thesis (non-negotiable)

A general assistant answers from the world. **Sava answers from *your* library, and never from anything else.** That single constraint is the product, and every screen must make it visible.

Four non-negotiables that keep this from becoming a ChatGPT clone:

1. **The scope is a physical object, never a dropdown.** You are always looking at *what Sava is allowed to read*, rendered as your own thumbnails.
2. **Retrieval is shown before the answer, not hidden behind a spinner.** `retrieve_for_library_question()` picks the saves first — surface that. The loading state is the proof of grounding.
3. **No sources → no answer.** `ask_sava` already returns "I couldn't find anything in your library" on empty blocks. The UI treats that as a *save prompt*, not an error.
4. **Never a blank canvas.** No "How can I help you today?" Ever. The empty state is made of your saves.

**One line to keep on the wall:** every other assistant starts from a blank box and everything it knows. Sava starts from your thumbnails and nothing else — and when it doesn't know, it asks you to save something.

---

## 3. The scope model

| Scope | Thread `scope` | Endpoint | Grounding | Citation unit |
|---|---|---|---|---|
| **Ask this** | `save` | `POST /api/bookmarks/{id}/ask` | 1 save, top-6 chunks | **A moment** — timestamp + modality |
| **Ask collection** | `collection` | *(missing — see §18)* | N saves in one collection | **A save within a set** |
| **Ask Sava** | `library` | `POST /api/ask` | ≤10 saves, 2 chunks each | **A save** — numbered `[2]` |

Scope is **never** a picker at the top of a chat. Scope is decided by **where you started the conversation**, and it is immutable for the life of that thread. Changing scope forks a new thread.

> This is the anti-clone rule: in ChatGPT the context is invisible and mutable; in Sava it's the thing you tapped.

### 3.1 The Scope Header — the signature component

Pinned to the top of every Ask surface. Non-scrolling. Never collapses to a text label.

```
ASK THIS                          ASK SAVA
┌──────────────────────────────┐  ┌──────────────────────────────┐
│ ▢ 14-min · YouTube           │  │ ◱◱◱◱◱ +203                   │
│   "Kyoto in 3 days"          │  │   Your whole library          │
│   Abroad in Japan            │  │   203 saves · 187 readable    │
└──────────────────────────────┘  └──────────────────────────────┘
     ↑ the actual thumbnail            ↑ live thumbnail stack

ASK COLLECTION
┌──────────────────────────────┐
│ ◱◱◱ Japan Trip · 12 saves    │
│   auto-built from your saves │
└──────────────────────────────┘
```

- **Ask this:** the save's real `thumbnail_url`, title, creator, duration. 44pt thumb, `Radius.sm`.
- **Ask Sava:** a fanned stack of the 5 most-recent thumbnails at 28pt, −8pt overlap, 2° rotation each, plus a `+203` counter. It moves — slow parallax drift on `SavaMotion.ambient`. The library is *alive* in the header.
- **Ask collection:** 3-up stack + collection name; `kind == "auto"` gets the subtitle "auto-built from your saves" so a machine-made set never masquerades as a user-made one.

**Styling:** `SavaColors.surface`, `Radius.lg`, `hairline` border, with a **1pt `accentSoft` underline seam** at its bottom edge. That seam is the visual signature of "everything below this line came from above it."

---

## 4. Entry points

### 4.1 Ask this — three, in order of prominence

1. **Detail-view section.** A live composer with 3 type-aware chips (§5). Sits directly under the AI Summary, above Transcript.
2. **Timestamp tap-through.** Tapping a transcript line or a chapter opens Ask This pre-seeded: *"What's said at 4:12?"* Unique to Sava — a chat entry point that is a *position in a video*.
3. **Long-press on any library card → "Ask about this".** Skips the detail view entirely.

**Gating:** if `GET /api/bookmarks/{id}/status` reports `state != "ready"` or `has_understanding == false`, the composer renders **disabled with a live progress line** (§10.2), never hidden. Disappearing UI reads as a broken app.

### 4.2 Ask Sava — three

1. **The tab bar.** Ask Sava does **not** get a fourth tab — that would frame it as a separate app. Instead, **Search becomes a dual-mode surface** (§14), and Ask is reached by switching mode inside it. One search field, two intents.
2. **Library header affordance.** Above the grid: a single-line prompt bar reading `Ask Sava about your 203 saves →`. This is the discovery path.
3. **Search-to-Ask escalation.** The highest-value entry point. When a search query is question-shaped or returns weak results, an inline card offers the handoff (§14.2). The query carries over verbatim.

### 4.3 Ask collection — two

1. Collection detail header: `Ask this collection` button, styled identically to Ask This so scope feels like the same gesture at a different altitude.
2. **From an Ask Sava answer:** if ≥4 sources belong to one collection, a footer chip appears — **"Narrow to Japan Trip"** — forking a `collection`-scoped thread carrying the same question. Scope narrowing as a *result-driven* action, not a menu.

---

## 5. Suggested prompts

Generic starters ("Summarize this") are what makes chat feel hollow. Sava has `content_type`, `typed_data`, `entities`, `topics`, `chapters` persisted per save — **every suggestion must be built from real data on the device, deterministically, with zero model calls.**

### 5.1 Ask this — three rules, applied in order

**Rule 1 — Typed-data prompts** (from `understanding.typed_data`, schema per `TYPED_SCHEMAS` in `api/pipeline/understanding.py`):

| `content_type` | Suggestions derived from typed fields |
|---|---|
| `recipe` | "Scale this to 6 servings" · "What can I substitute for {ingredients[0].item}?" · "Just the steps" |
| `restaurant` | "Do I need a reservation?" · "What did they order?" · "How expensive?" |
| `travel` | "Build me a day around {places[0].name}" · "Best time to go?" · "What's near {places[1].name}?" |
| `product` / `shopping` | "Is it worth {items[0].price}?" · "What are the downsides?" · "What else did they compare it to?" |
| `fitness` | "Turn this into a 20-minute version" · "What do I need?" · "Is this beginner-friendly?" |
| `coding` | "Give me the commands" · "What are the gotchas?" · "Which version does this need?" |
| `beauty` / `fashion` | "List every product shown" · "Cheaper alternatives?" |

**Rule 2 — Entity prompts.** If `entities.prices` non-empty → "What did it cost?" If `entities.places` → "Where is this?" If `entities.people` has ≥2 → "Who's involved?"

**Rule 3 — Note prompt.** If `bookmark.note` exists, the *first* chip is always derived from it: note `"for mum's birthday"` → **"How does this fit 'for mum's birthday'?"** Nothing else in the category can do this. Your own words, quoted back, six weeks later.

Cap at 3 chips + a `⋯` that expands to 6. Chips are `SavaColors.surfaceMuted` capsules, `SavaFont.footnote`, 32pt — reuse `FlowChips`, made tappable.

### 5.2 Ask Sava — suggestions are *observations about your library*

Not questions Sava can answer in general — questions only *your* library can answer. Generated from library stats (cheap SQL, no model):

- **Cluster prompt** — largest `topics` cluster: *"You've saved 11 things about sourdough. What's the consensus?"*
- **Contradiction prompt** — ≥2 saves, same `content_type`, overlapping `entities.products`: *"Three creators reviewed the AirPods Pro. Do they agree?"*
- **Dormant prompt** — from `GET /api/resurface`: *"What was that Lisbon place you saved in March?"*
- **Planning prompt** — only when a `travel` collection has ≥3 items: *"Plan a weekend from my Japan saves."*
- **Recency prompt**: *"What did I save this week?"*

Each suggestion carries its evidence as a subtitle: `from 11 saves · sourdough`. **That subtitle is the anti-clone marker — every suggestion tells you why it exists.**

**Placement:** the Ask Sava idle state is **not** a centered prompt list. It is a scrollable column of suggestion cards, each with the thumbnail stack of the saves it would draw on. You are browsing your own memory, not staring at a cursor.

---

## 6. Anatomy of the Ask surface

```
┌─────────────────────────────────────┐
│ ✕            Ask Sava        ⏱ ⌄     │  ← close · scope title · history
├─────────────────────────────────────┤
│ ◱◱◱◱◱ +203  Your whole library      │  SCOPE HEADER (pinned)
│             203 saves · 187 readable │
├─────────────────────────────────────┤  ← accentSoft seam
│                                     │
│              What's worth eating  ▐ │  USER TURN (right, accentSoft
│              in Kyoto?              │   capsule, textPrimary)
│                                     │
│  ◱◱◱ Read 6 saves          [ ⌄ ]   │  SOURCE RAIL (collapsed)
│                                     │
│  Three of your saves point at the   │  ANSWER (full-width prose,
│  same street. Nishiki Market ①      │  paper, no bubble)
│  comes up twice — ② calls the       │
│  tamagoyaki stall the one thing…    │
│                                     │
│  ┌──────┬──────┬──────┐             │  SOURCE CARDS (h-scroll)
│  │ ◱ ①  │ ◱ ②  │ ◱ ③  │             │
│  └──────┴──────┴──────┘             │
│                                     │
│  ⟳ Ask again with Advanced    ⧉ ⤴   │  TURN ACTIONS
│                                     │
│  ⌁ What about breakfast?            │  FOLLOW-UPS
│  ⌁ Which of these are still open?   │
├─────────────────────────────────────┤
│ ⌂ Sava Auto ⌄   Ask your library… ↑ │  COMPOSER + MODEL CHIP
└─────────────────────────────────────┘
```

### 6.1 The bubble decision (important)

User turns are bubbles (right-aligned, `accentSoft`, `Radius.lg` with a squared bottom-right). Assistant turns are **not bubbles** — they are typeset directly on `SavaColors.background` at `SavaFont.body`, 1.5 line-height, full width.

**Rationale:** a bubble frames text as *someone talking to you*; unframed text on paper frames it as *a document about your library*. **That asymmetry alone kills ~80% of the ChatGPT resemblance.**

---

## 7. Loading — the retrieval theater

This is Sava's most differentiated 1.5 seconds. **Never a bouncing-dots indicator.** The two-stage retrieval in `retrieve_for_library_question()` maps directly onto two visible beats:

- **Beat 1 — Searching (0–400ms).** The header thumbnail stack scatters outward and shimmers. Copy: `Searching 203 saves…`
- **Beat 2 — Selecting (400–900ms).** Thumbnails of the actually-retrieved saves fly *in* and settle into the source rail, one at a time on `SavaMotion.standard` with 60ms stagger. Counter ticks `1 → 6`. Copy: `Reading 6 saves`. **Titles are legible.** The user reads the sources before the answer — so when the answer arrives it is already believable.
- **Beat 3 — Answering (900ms+).** Rail collapses to a 28pt row. Streaming begins.

For **Ask This**, beats compress: `Finding the moment…` → the top chunk's timestamp appears as a chip (`4:12`) → answer streams. The chip is the retrieval, made visible.

Skeletons use the existing `LibrarySkeleton` shimmer so Ask feels like the same app as Library.

**Slow-path copy:** at 4s → `Still reading — this one's long.` At 10s → `Taking longer than usual` + **Cancel**. Cancel is available from beat 1 onward.

---

## 8. Streaming

**The backend is currently non-streaming** (`router.complete()` returns a whole `Completion`). Streaming is required — an 8-second silent wait for a reasoning-model answer is unshippable. See §18 for the endpoint delta.

Rules:

- **Word-level reveal, not character.** Characters read as a typewriter gimmick; words read as thinking. Fade-in per word over 90ms with a 4pt upward drift, capped at ~40 words/s regardless of token arrival — a burst that dumps 200 tokens at once still reveals smoothly.
- **Cursor:** a 2pt × 14pt `SavaColors.accent` bar, 700ms pulse. Removed the instant the stream closes.
- **Citation markers materialize as they are parsed.** When `[2]` appears in the stream, replace it inline with a live superscript chip *as it lands* — and simultaneously pulse source card ②. Reading the answer physically lights up your saves. **This is the moment the product sells itself.**
- **Scroll:** follow the tail while the user is within 80pt of the bottom; the moment they scroll up, stop following and show a `↓ New` pill. Never yank.
- **Interruption:** the send button becomes a **stop square** during streaming. Stopping keeps the partial text and appends `— stopped`. Partial turns persist to history.
- **Reduce Motion:** all of the above collapses to a 3-chunk fade via `SavaMotion.respectingReduceMotion`. No per-word animation, no drift.

---

## 9. Citations

Two citation grammars, because the backend produces two shapes. **Do not unify them.**

### 9.1 Ask This → *moments*

Payload: `citations: [{start_s, end_s, timestamp, source: "transcript"|"vision", text}]`

Inline, immediately after the sentence they support:

```
He recommends the 24-hour cold ferment ⟨ 4:12 ⟩ and warns
against the metal bowl ⟨ 7:03 · on-screen ⟩.
```

- Monospace `SavaFont.mono`, `surfaceMuted` capsule, 20pt tall.
- `source == "vision"` gets an `eye` glyph + the `on-screen` label. **Genuinely differentiating:** Sava can cite *something written on the screen at 7:03*, which no transcript-only tool can.
- **Tap = scrub.** Jumps the transcript view to that line and highlights it. Long-press previews the raw `text` excerpt in a popover — the receipt.
- Below the answer: `Grounded in 6 excerpts from this save` — a plain-language rendering of `grounded_in`.

### 9.2 Ask Sava → *saves*

Payload: `sources: [RetrievedSave.to_dict()]`; answer text contains `[n]`.

- **Inline marker** = a 20pt circular chip containing the source's **thumbnail**, not a number. Numbered fallback only when `thumbnail_url` is nil. A wall of text speckled with tiny images of things you saved is instantly, unmistakably Sava.
- **Source cards**, horizontal scroll under the answer, 148pt wide:

```
┌────────────────────┐
│ ◱ thumbnail        │
│ ① Kyoto in 3 days  │  title, 2 lines
│ Abroad in Japan    │  creator
│ ▶ YouTube · 14m    │  platform tint dot
│ ✎ "before May"     │  your note, if any
│ ◦ semantic + words │  matched_on
└────────────────────┘
```

- `matched_on` is a real field worth exposing: `semantic` → `similar meaning`, `keyword` → `exact words`, both → `strong match`. It answers "why is this here?" without a debug panel.
- **Tap** → the bookmark detail. **Long-press** → the actual excerpt text that entered the prompt. **Swipe up on a card** → forks an Ask This thread on that save with the same question (scope drilling by gesture).
- Cards **not** cited in the answer body still appear, dimmed to 60%, under a divider labelled `Also read, not cited`. **Never hide what was sent to the model.** That honesty is a feature you can market.

### 9.3 Citation integrity (defensive requirement)

The model can emit `[7]` when only 6 blocks were sent. **Client rule: any marker with no matching source renders as plain text and is silently dropped — never a dead chip.** Log the occurrence; if the rate is non-trivial, the fix is a prompt change, not UI.

---

## 10. Follow-ups

Follow-ups must be **derived from the answer's own sources**, not model-generated. Deterministic, instant, free, and structurally impossible in a generic chat app:

| Trigger | Follow-up |
|---|---|
| Any source has `content_type == "restaurant"` with `city` | "Which of these are in {city}?" |
| ≥2 sources share a `topics` entry | "Compare these on {topic}" |
| A source has unopened `typed_data.recipe` | "Show me the full ingredient list" |
| ≥4 sources in one collection | "Narrow to {collection}" *(forks scope)* |
| Answer contains a hedge (§11.1) | "Search my library for {missing thing}" |
| Ask This, `chapters` present | "What else is in this video?" |

Rendered as left-aligned rows with a `⌁` glyph — deliberately **not** chips, so they read as continuations rather than a menu. Max 3. Only on the **latest** turn; scrolling up clears them.

The composer placeholder also adapts after a turn: `Ask your library…` → `Ask a follow-up…`.

---

## 11. Uncertainty, failure, and empty states

Every state below maps to a real `reason` code the backend already returns. **There is no generic error state.**

### 11.1 Uncertainty (the answer came back hedged)

The system prompts explicitly instruct the model to say *"This save doesn't cover that"* and *"answer that part and say what is missing."* Detect these client-side and **promote the hedge out of the prose into a component** — buried in a paragraph, an admission of ignorance reads as a bad answer; surfaced as a card, it reads as integrity.

```
┌─────────────────────────────────────┐
│ ◑  Partly answered                  │
│ Your saves cover Kyoto and Osaka,   │
│ but nothing about Nara.             │
│              [ Save something about Nara ]
└─────────────────────────────────────┘
```

`surfaceMuted`, no accent (**uncertainty is not an error — never use `danger` red**). The CTA opens Quick Save with the topic pre-noted. **Sava's failure to know something becomes a prompt to feed it. That loop is the retention mechanic.**

**Grounding strength** shown as three dots next to `Read N saves`:

- `grounded_in >= 5` → ●●● `Well grounded`
- `2–4` → ●●○ `Partly grounded`
- `1` → ●○○ `Thin — based on one save`

Only surface the text label on tap; the dots alone carry it.

### 11.2 Not processed yet — `reason: "not_processed"` / `"no_content"`

Composer disabled, with **live stage progress** from `GET /api/bookmarks/{id}/status` (`stages`, `level`):

```
┌─────────────────────────────────────┐
│ ◴ Sava is still reading this        │
│ ▓▓▓▓▓▓▓░░░ transcribing · 2 of 4    │
│ You'll be able to ask in a moment.  │
└─────────────────────────────────────┘
```

Poll every 3s while visible; back off to 10s after 60s. On `state == "ready"`, the block **cross-fades into the live composer** with `Haptics.tap()` and the suggestion chips populate. Watching a save become askable is a genuinely delightful 8 seconds — and it teaches what Sava does.

If `stages` shows a failed stage: `Sava couldn't read this one` + **Try again** → `POST /api/bookmarks/{id}/reprocess?force=true`. Show `last_error` only behind a `Details` disclosure.

### 11.3 Failed retrieval — Ask Sava returned zero blocks

Backend returns `ok: true` with the string `"I couldn't find anything in your library that relates to that yet."` **Do not render that as an assistant message.** Replace the whole turn with:

```
┌─────────────────────────────────────┐
│  ◌  Nothing in your library on this  │
│                                     │
│  Sava only answers from what you've │
│  saved. Nothing here touches         │
│  "quantum computing" yet.            │
│                                     │
│  [ Search instead ]  [ Save a link ] │
│                                     │
│  Closest things you've saved:        │
│  ◱ ◱ ◱   ← from search_library, dimmed│
└─────────────────────────────────────┘
```

The line **"Sava only answers from what you've saved"** is the single most important sentence in the product. It converts a dead end into a positioning statement. **Show it every time — never suppress it as a repeat.**

The "closest things" row calls `GET /api/search` with the same query at `limit=3`. Even a total miss returns *something of yours*, so the surface is never empty.

### 11.4 Zero library — the true cold start

If the user has 0 processed saves, **Ask Sava is not reachable.** The Library header affordance is replaced by: `Save 3 things and Sava can start answering questions about them.` with progress dots `● ● ○`. **Locking the feature until it can succeed is better than letting it fail on first contact.**

### 11.5 AI unavailable — `reason: "ai_unavailable"`

`GET /api/ai/modes` returns `available: false` when no provider is configured. On `false`: **hide every Ask entry point and the model selector entirely**, and leave Search fully functional (`search_library` degrades to keyword-only without embeddings). Ask never renders a broken shell. **Search never mentions AI being down — it still works.**

### 11.6 Network / 5xx

Inline `InlineBanner` in `danger` tint, above the composer, with **Retry**. The question stays in the composer — never lost. **This is the only red state in the entire feature.**

---

## 12. Conversation history

Threads are already persisted (`ChatThread`, `ChatMessage` with `citations` and `mode`). But **do not build a ChatGPT sidebar.** History is scoped, and it lives where its scope lives:

- **Ask This history** lives in the save's detail view: `3 conversations about this save` above the composer.
- **Ask Sava history** is reached from the `⏱` icon in the Ask header — a **sheet, not a drawer**. `GET /api/threads?scope=library`.
- **Ask Collection history** lives on the collection.

History rows show the thread `title` (server sets it to `question[:60]`), relative date, and **the thumbnails of the sources from the last assistant message** — decoded from the persisted `citations` JSON. You recognize a past conversation by the saves it touched, not by a truncated sentence.

```
┌──────────────────────────────────────┐
│ What's worth eating in Kyoto?        │
│ 2 days ago · ◱◱◱◱ 6 saves            │
└──────────────────────────────────────┘
```

Threads are **not auto-titled by a model** — `question[:60]` is free and honest. If a title truncates mid-word, trim to the last word boundary and append `…`. **Never spend inference on naming a thread.**

**Continuation cost control (important):** `_thread_and_history` replays the *entire* thread into every request. Client rule: at 12 turns, show an inline divider — `This conversation is getting long. Start fresh?` — with a **New thread** action that carries the scope over. This is a cost control disguised as a courtesy, and it is honest.

---

## 13. Model selector

### 13.1 Placement and form

**Left of the composer**, as a small text chip — never a header dropdown, never a settings page.

```
┌─────────────────────────────────────┐
│ ⌂ Sava Auto ⌄  │ Ask your library… ↑│
└─────────────────────────────────────┘
```

`SavaFont.caption`, `textSecondary`, `surfaceMuted` capsule, 26pt. **Deliberately quiet.** It is a speed/depth dial, not a feature you're supposed to fiddle with.

Tapping opens a compact menu populated from `GET /api/ai/modes` — **never hardcode the copy, and never render a model name even if one leaks into the payload:**

```
┌────────────────────────────────────┐
│ ⌂  Sava Auto            ✓          │
│    Best model automatically        │
├────────────────────────────────────┤
│ ⏵  Fast                            │
│    Quick everyday questions        │
├────────────────────────────────────┤
│ ◈  Advanced                        │
│    Deeper reasoning                │
└────────────────────────────────────┘
```

### 13.2 Sava Auto is a router — show it routing

This is where Sava earns the name. `resolve_task()` picks `ASK_SAVA` vs `ASK_SAVA_COMPLEX` from `classify_question()`. **When Auto escalates, say so — after the fact, in one line, in plain language:**

> `⌂ Sava Auto → deeper reasoning · comparison question`

Rendered under the answer at `SavaFont.caption` / `textTertiary`. **Never before sending** (reads as a warning); **always after** (reads as competence). Requires a `routing` block on the response (§18) — worth the small backend change, because it turns an invisible cost optimization into a visible product feature. Users learn that Auto is *deciding*, which is exactly why they should leave it alone.

When Auto stays cheap, show nothing. Silence is the default; the trace only appears on escalation.

### 13.3 When the selector disappears

Hide it completely — **not disabled, gone** — when:

| Condition | Why |
|---|---|
| `GET /api/ai/modes` → `available: false` | Nothing to select |
| **Ask This on a save with no transcript and no understanding** | Every mode routes to the same `CHEAP` spec; the choice is fake |
| **The first turn of a thread, for a user with < 5 total asks** | New users must not configure a router before they've seen it work |
| **Mid-stream** | **Locked, not hidden** — changing models mid-answer is incoherent |
| **A no-results / not-processed state** | Retrieval failed; the model was never reached, so offering a better model is a lie |

The third rule matters most. **The selector is progressively disclosed:** it appears on a user's 5th question, with a one-time 3-second inline note — `You can change how deeply Sava thinks. Auto is usually right.` — then never explains itself again.

Also: **the selection is per-thread, not global.** A thread remembers its mode (`ChatMessage.mode` already stores it). Choosing Advanced for one hard question must not silently make every future question slower and more expensive.

### 13.4 When Advanced should be suggested

Suggest it in exactly three situations, never more:

1. **Pre-send, on question shape.** `classify_question()`'s `_STRONG_REASONING` regex fires on composer text. Debounce 400ms, run server-side via a trivial `GET /api/ai/shape?q=` (pure regex — no model, no DB, no embedding). The composer's mode chip gently morphs:
   `⌂ Sava Auto ⌄` → `◈ Advanced suggested ⌄`
   **It does not auto-switch.** Auto already escalates internally; the suggestion is only for users who want to be explicit. One tap accepts.
2. **Post-answer, on thin grounding with wide retrieval.** `grounded_in >= 6` but the answer is under ~60 words — lots of sources, little synthesis. Offer `⟳ Ask again with Advanced` in the turn actions row, `accent`-tinted, once per turn.
3. **On explicit dissatisfaction.** If the user re-asks a semantically near-identical question within the same thread (client-side similarity on raw strings), offer Advanced instead of silently re-running Fast.

**Never suggest Advanced when:** the mode is already Advanced; the question is fact-shaped and short (`_SIMPLE_PATTERNS`, ≤14 words); retrieval returned 0–1 sources (a stronger model cannot invent context); or the user has hit their fair-use ceiling.

> **⚠ UNRESOLVED / RISK — see §19.1.** `STRONG` currently resolves to the same `gemini-3.7-flash` as `BALANCED` because the configured key has no Pro quota. **Advanced is today a real setting with no real effect on Auto-escalated Ask Sava paths.** The UX above is correct and forward-compatible, but hold the pre-send "Advanced suggested" nudge (case 1) behind a flag until `SAVA_STRONG_MODEL` points at something genuinely stronger — otherwise you are teaching users to pull a lever that does nothing.

---

## 14. Limits and fair use

**Principles: never surprise, never shame, never block mid-thought.**

### 14.1 The unit is questions, not tokens

Users cannot reason about tokens. Sava's meter is **questions asked this month**, with **Advanced counting as 3**. Show it as one line in Profile, sourced from `GET /api/ops/usage`:

> `Questions this month  128 / 300` with a hairline progress bar, `accent` fill.

### 14.2 Escalation ladder

| Threshold | Treatment |
|---|---|
| **< 80%** | Nothing. Silence. |
| **80%** | One-time inline note under the composer: `You've used 240 of 300 questions this month. Resets Sep 1.` Dismissible, never returns. |
| **95%** | The mode chip appends a counter: `⌂ Sava Auto · 15 left`. Advanced shows `· counts as 3`. |
| **100%** | Composer stays **enabled**; sending shows a sheet, not an error toast. |

### 14.3 At the ceiling

```
┌─────────────────────────────────────┐
│  You've used all 300 questions      │
│  this month. Resets in 6 days.      │
│                                     │
│  Search still works — it's instant  │
│  and unlimited.                     │
│                                     │
│  [ Search my library ]   [ Upgrade ]│
└─────────────────────────────────────┘
```

**The critical move: degrade to Search, don't dead-end.** `search_library()` runs no generative model and is genuinely free — so at the ceiling, Sava is still fully useful. The user's typed question routes straight into search results. This is the strongest argument for keeping Search and Ask on one surface (§15).

### 14.4 Rate limiting, honestly

> **⚠ BLOCKER — see §18.** `api/rate_limiter.py` is today a **process-global, in-memory** limiter (50 requests / hour, shared across *all* users) and will not survive multiple workers. It is **not** a per-user quota. Ask needs a per-user, DB-backed counter before any of the §14 copy is truthful. `ai_telemetry` already records per-user rows, so the count exists; it needs exposing as a quota, not just a cost report.

**Burst limiting:** on rapid-fire, never show "too many requests." Show `One at a time — Sava's still reading.` and queue the send. A second question while one is streaming **cancels and replaces** the first.

---

## 15. Ask Sava vs. Search — the same field, two different worlds

They share `search_library()`, so they must feel like siblings — but they must never be confusable.

### 15.1 The split

| | **Search** | **Ask Sava** |
|---|---|---|
| **Mental model** | *Find the thing* | *Tell me about the things* |
| **Latency** | Instant, debounced 320ms, live-as-you-type | Deliberate, on submit only |
| **Layout** | `BookmarkGrid` — masonry, image-forward | Single column, text-forward, no grid |
| **Color** | Neutral. `background` + `surface`. **No accent.** | `accentSoft` seam, accent cursor, accent citations |
| **Motion** | None. Results snap in. | Staged, deliberate (§7) |
| **Field** | `magnifyingglass`, placeholder `Try "cooking", "Japan"` | `sparkle` seam glyph, placeholder `Ask your library…` |
| **Keyboard** | `.search` return key | `.send` return key |
| **Typography** | `subheadline` metadata, dense | `body` prose, 1.5 line-height, airy |
| **Failure** | "No matches" + suggest different words | "Nothing in your library on this" + offer to save |
| **Cost** | Free. Unlimited. | Metered. |

**The clearest tell: Search shows images. Ask shows sentences.** A user glancing at the screen from across the room knows which mode they're in.

### 15.2 The handoff

One field, mode toggle above it:

```
┌─────────────────────────────────────┐
│  [ Search ]  [ Ask Sava ]           │  segmented, Radius.pill
│  🔍 kyoto food                       │
└─────────────────────────────────────┘
```

**Search → Ask escalation** (the important direction). Injected as row 1 of the results grid — full-width, `accentSoft`, `Radius.md` — when *either*:

- the query matches `_SIMPLE_PATTERNS` or `_STRONG_REASONING` (it's phrased as a question), **or**
- results are weak: `count < 3`, or top `score < 0.45`.

```
┌─────────────────────────────────────┐
│ ✧ "what's worth eating in kyoto"     │
│   sounds like a question.            │
│   Ask Sava instead →                 │
└─────────────────────────────────────┘
```

**Ask → Search de-escalation.** Under any answer with ≥3 sources: `See all 14 matching saves →` — drops into Search with the same query, grid layout. Ask gives you the synthesis; Search gives you everything.

**The toggle is not remembered across sessions.** Search is always the default on open, because Search is free, instant, and correct more often. Ask is the deliberate choice.

---

## 16. Motion, haptics, accessibility

- **Send:** `Haptics.tap()`. **First token:** `Haptics.selection()` — a tiny tick meaning *it found something*. **Answer complete:** nothing (a success haptic per answer becomes noise fast). **No results:** `Haptics.selection()`, **never an error haptic** — an empty library is not a failure.
- All animation via `SavaMotion`; **every call site through `respectingReduceMotion`.**
- **VoiceOver:** the answer is one element; citation chips are separate elements labelled `Source 2, Kyoto in 3 days by Abroad in Japan` / `Moment at 4 minutes 12 seconds, from the transcript`. The source rail is a container labelled `Read 6 saves`. Announce stream completion via `.announcement`, **not** every token.
- **Dynamic Type** to AX3 minimum. Source cards reflow from horizontal scroll to a vertical list above AX1 — a 148pt card at AX5 is unreadable.
- **Contrast:** `accentSoft` (0.10 light / 0.16 dark) is a **background only**; citation chip *text* uses `accent` on `surfaceMuted`, never `accentSoft` on `background`.

---

## 17. CURRENT-STATE FINDINGS (observed, not recommended)

State of the code as of 2026-08-18 on `feat/intelligence-foundation`.

### 17.1 Backend — what exists and works

| Capability | Endpoint | Status |
|---|---|---|
| Mode metadata (provider-neutral) | `GET /api/ai/modes` | ✅ Exists, returns `{modes, available}` |
| Processing status | `GET /api/bookmarks/{id}/status` | ✅ Exists, returns `stages`, `level`, `has_transcript`, `has_understanding` |
| Reprocess | `POST /api/bookmarks/{id}/reprocess?force=` | ✅ Exists |
| Hybrid search (no model) | `GET /api/search` | ✅ Exists, returns `matched_on`, `took_ms`, `semantic` |
| AI summary (cached) | `GET /api/bookmarks/{id}/summary` | ✅ Exists, lazy for long-form |
| **Ask This** | `POST /api/bookmarks/{id}/ask` | ✅ Exists — timestamped citations, `grounded_in` |
| **Ask Sava** | `POST /api/ask` | ✅ Exists — `sources[]`, `[n]` markers, `grounded_in` |
| **Ask Collection** | — | ❌ **Missing** (schema ready, route + service absent) |
| Thread list | `GET /api/threads?scope=&bookmark_id=` | ✅ Exists |
| Thread messages | `GET /api/threads/{id}/messages` | ✅ Exists, includes persisted `citations` + `mode` |
| Related saves (no model) | `GET /api/bookmarks/{id}/related` | ✅ Exists |
| Resurfacing (no model) | `GET /api/resurface` | ✅ Exists, deterministic age/note/summary scoring |
| Collections CRUD + suggestions + rebuild | `/api/collections*` | ✅ Exists |
| Cost telemetry | `GET /api/ops/usage`, `GET /api/ops/queue` | ✅ Exists (cost report, **not** a quota) |
| **Streaming** | — | ❌ **Missing.** No `StreamingResponse` / SSE anywhere in `api/` |
| **Per-user quota** | — | ❌ **Missing.** Only a global in-memory `RateLimiter` |

### 17.2 Backend — specific gaps found

- `ask_this()` builds its citation dicts **without `chunk_id`** — the value is available in `retrieve_chunks()` output but dropped in the dict comprehension. Citations therefore cannot be resolved deterministically; the client must match on timestamp.
- No `routing` metadata is returned. `resolve_task()` already computes escalation, but the client cannot see it.
- `classify_question()` is not exposed over HTTP. Any client-side pre-send suggestion would have to duplicate the regex in Swift.
- No `readable_count` (saves with a `content_understanding` row) is exposed for the scope header line.
- `_thread_and_history()` accepts only `bookmark_id`; it has no `collection_id` passthrough, so it cannot create a `collection`-scoped thread even though the column exists.
- `_thread_and_history()` replays the full thread history into every completion call — unbounded prompt growth per thread.
- `api/rate_limiter.py` `RateLimiter` is instantiated as a module-level singleton with in-process state (`self.requests` list). Not per-user, not shared across workers, not persisted.

### 17.3 iOS — current state

- **`ios/Sava/Core/Models/Intelligence.swift` is complete and correct.** It already models: `ProcessingState` (with a tolerant `from(_:)` lookup and a documented note about not shadowing `init(rawValue:)`), `ProcessingStatus`, `AskMode` (`auto`/`fast`/`advanced` with exactly the server's copy), `SaveUnderstanding` (incl. `Chapter`), `AskAnswer` (incl. `Citation` with `start_s`/`end_s`/`timestamp`/`source`/`text`, plus `sources`, `grounded_in`, `thread_id`), `RelatedSave`, `SavaCollection`, `SearchResponse`. All decoders are defensive (`try?` + defaults).
- **`ios/Sava/Features/Detail/AskSavaSection.swift` is a working Ask This implementation** — mode picker menu, tappable example chips, turn list, timestamp citation row, loading row (`ProgressView` + "Reading this save…"), `InlineBanner` error, and a send composer. It is titled **"Ask this save"** with `text.bubble`, and its doc comment correctly states that the picker offers Sava's routing intents and never a vendor name.
- **`ios/Sava/Features/Detail/Intelligence.swift` (the old placeholder `IntelligenceService` with `isEnabled = false`) has been removed/emptied.** The stale "coming soon" preview described in earlier drafts of this spec **no longer exists**.
- Design system, `AppShell` (Library · Search · [+] · Profile), `SearchView`/`SearchViewModel` (debounced 320ms, persisted recents, teaching empty state) are all in place.
- `docs/audits/` existed but was empty prior to this document.

### 17.4 Gap between current iOS implementation and this spec

The shipped `AskSavaSection` is a **correct, honest v0** but implements roughly Phase 1 in its simplest form. Relative to §3–§16 it is missing:

- No scope header (§3.1) — no thumbnail, title, or creator anchor.
- Example prompts are **platform-based**, not `content_type`/`typed_data`/`note`-based (§5.1 Rules 1–3 unimplemented).
- Citations render as a flat row of timestamps under the answer, **not inline after the sentence they support**, and do not distinguish `source == "vision"` (`on-screen`) from `transcript` (§9.1).
- Citations are not tappable → no transcript scrub, no excerpt receipt popover.
- No grounding-strength indicator (`grounded_in` is decoded but unused).
- Model picker is always visible — the §13.3 disappearance rules and progressive disclosure are unimplemented.
- No routing trace, no Advanced suggestion (§13.2, §13.4).
- No streaming (backend cannot).
- No not-processed / stage-progress gating (§11.2); no uncertainty promotion (§11.1).
- No follow-ups (§10); no per-save thread history (§12).
- Ask Sava (library scope) and Ask Collection have **no UI at all**.

---

## 18. LAUNCH BLOCKERS & REQUIRED BACKEND DELTAS

Ordered by whether the UX above is shippable without them. **Do not implement from this document without a separate decision — this is a specification, not a work order.**

### 18.1 BLOCKING — must exist before the full experience ships

| # | Item | Severity | Detail |
|---|---|---|---|
| B1 | **Streaming endpoints** | **Blocker** | `POST /api/ask/stream` + `POST /api/bookmarks/{id}/ask/stream` as SSE. **Frame order matters and must mirror §7:** `sources` first (so the retrieval theater is real, not simulated), then `delta` tokens, then `done` with `{grounded_in, routing, thread_id, quota}`. Nothing in `api/` currently emits `text/event-stream`. Without this, an 8-second silent wait on a reasoning model is unshippable. |
| B2 | **Per-user quota** | **Blocker** (also a correctness/abuse issue) | The current `RateLimiter` is a global in-memory singleton (50/hr for *everyone*, resets on restart, wrong under multiple workers). Needs a DB-backed per-user monthly counter, exposed on every ask response as `quota: {used, limit, resets_at}`. **All §14 copy is fiction without it.** |

### 18.2 HIGH VALUE, LOW COST

| # | Item | Detail |
|---|---|---|
| H1 | `routing` block on ask responses | `{task, escalated: bool, reason: "comparison"\|"length"\|"breadth"}`. Powers §13.2. `resolve_task()` already computes this; it simply isn't returned. |
| H2 | `GET /api/ai/shape?q=` | Expose `classify_question()` directly. **Pure regex — no model, no DB, no embedding.** Powers §13.4 case 1 without duplicating the heuristic in Swift. |
| H3 | Stable citation IDs | `ask_this` drops `chunk_id` from its citation dicts. Include it so tapping a citation resolves deterministically rather than by timestamp matching. |
| H4 | `readable_count` on the library summary | Count of saves with a `content_understanding` row, for the scope header's `203 saves · 187 readable` line. |

### 18.3 REQUIRED FOR ASK COLLECTION (future phase)

| # | Item | Detail |
|---|---|---|
| C1 | `POST /api/collections/{id}/ask` + `intelligence.ask_collection()` | The service is `retrieve_for_library_question()` with the canonical-id set constrained to `collection_items`. |
| C2 | `_thread_and_history()` `collection_id` passthrough | Currently accepts only `bookmark_id`. `ChatThread.scope` already accepts `"collection"` and `ChatThread.collection_id` already exists — **the schema is done.** |

### 18.4 SECURITY / PRIVACY NOTES

- **Scoping is already enforced in SQL.** `_USER_SCOPE` in `api/services/retrieval.py` constrains every retrieval to canonical content the user has personally saved. Any new endpoint (notably C1) **must** reuse this constraint — do not hand-roll a new filter.
- **The library is never dumped into a prompt.** Retrieval selects ≤10 saves × 2 chunks. Preserve this invariant in any streaming or collection variant.
- **Vendor neutrality is a product commitment, not a style preference.** `describe_modes()` returns no model names; `api/ai/base.py` documents the boundary. The client must never render a model name **even if one leaks into a payload** (§13.1).
- **Never identify individuals from visual description** — already enforced in the extraction system prompt (`people`: creator handles or publicly named figures only). Any UI that surfaces `entities.people` inherits this constraint.
- No per-user quota (B2) is also an **abuse/cost exposure**, not merely a UX gap.

---

## 19. RISKS & UNRESOLVED DECISIONS

### 19.1 UNRESOLVED — "Advanced" currently does nothing distinct

`STRONG` in `api/ai/router.py` resolves to `_os.getenv("SAVA_STRONG_MODEL", "gemini-3.7-flash")` — the **same model as `BALANCED`** — because the configured key has no Pro quota (429). Under `Mode.AUTO`, `ASK_SAVA_COMPLEX` also maps to `BALANCED`. Consequence: **selecting Advanced changes nothing on Auto-escalated Ask Sava paths today.**

**Decision needed:** either (a) provision Pro quota and set `SAVA_STRONG_MODEL`, or (b) ship Advanced as-is but **gate the pre-send "Advanced suggested" nudge (§13.4 case 1) behind a flag** so users are not taught to pull an inert lever. The post-answer "Ask again with Advanced" (case 2) is also inert until this resolves.

### 19.2 RISK — unbounded thread cost

`_thread_and_history()` replays every prior message into each completion. A 30-turn Ask Sava thread sends 30 turns of history *plus* fresh retrieval context every time. §12's 12-turn "Start fresh?" divider is the UX mitigation; a server-side history window or summarization may also be needed. **Unresolved.**

### 19.3 RISK — citation marker hallucination

The model can emit `[7]` when only 6 blocks were sent. §9.3 specifies the client-side defense (drop unmatched markers silently, log the rate). If the observed rate is non-trivial, **the fix is a prompt change, not UI.**

### 19.4 RISK — hedge detection is string matching

§11.1 promotes hedges out of prose into a component by detecting phrasing the system prompt requests ("This save doesn't cover that", "…but nothing about…"). This is brittle across model versions and languages. A more robust option is a structured `coverage: {full|partial|none, missing: [...]}` field on the response — **not specified, deliberately deferred**, since it costs prompt complexity.

### 19.5 FUTURE PHASE (explicitly out of initial scope)

- **Ask Collection** end-to-end (§4.3, §18.3).
- Scope forking by gesture (swipe-up on a source card → Ask This).
- Cross-thread memory / user preferences learned over time — **not designed here, and out of scope by intent.** Sava's grounding claim depends on answers coming from saves, not from an accumulating profile.
- Multi-modal input (asking with an image).
- Sharing an answer with its sources.

---

## 20. BUILD ORDER & ACCEPTANCE CRITERIA

| Phase | Scope | Ships | Acceptance criteria |
|---|---|---|---|
| **1** | Ask This, non-streaming | Real composer; typed-data + entity + note suggestions (§5.1); inline timestamped citations with `on-screen` distinction; tap-to-scrub; not-processed stage gating (§11.2) | A save with `content_type == "recipe"` shows an ingredient-derived chip, not a generic one. A save with a `note` shows the note-derived chip first. Tapping `⟨4:12⟩` scrubs the transcript. A queued save shows live stage progress and cross-fades to an enabled composer on `ready`. |
| **2** | Ask Sava + Search dual-mode | Scope header (§3.1); retrieval theater (§7); thumbnail source cards + `matched_on` (§9.2); no-results-as-save-prompt (§11.3); Search↔Ask handoff (§15.2) | The loading state names real retrieved saves before the answer. A zero-result question renders the save-prompt card with "closest things you've saved", never a chat bubble. A question-shaped search query surfaces the Ask escalation row. Search remains accent-free and image-forward; Ask is prose-forward. |
| **3** | Streaming + routing trace | SSE (B1); word-level reveal; live citation materialization; `Sava Auto → deeper reasoning` (§13.2) | First `sources` frame arrives before any `delta`. Citation chips materialize inline and pulse their source card. Reduce Motion collapses to a 3-chunk fade. Stop preserves partial text and persists it. |
| **4** | Model selector + quota | Progressive disclosure at 5 asks; §13.3 disappearance rules; Advanced suggestion (flagged per §19.1); fair-use ladder (§14) | The selector is absent for a user's first 4 asks and absent when `available: false`. Below 80% usage nothing is shown. At the ceiling the composer still accepts input and routes to Search. |
| **5** | Ask Collection + history | C1/C2 route + service; scope forking from answers; thumbnail-keyed history (§12) | An Ask Sava answer with ≥4 sources in one collection offers "Narrow to {collection}" and forks a `collection`-scoped thread. History rows show source thumbnails decoded from persisted `citations`. |

**Cross-phase acceptance criteria (apply to every phase):**

- No vendor or model name appears anywhere in the UI, under any state, including error payloads.
- No screen in the Ask experience is ever a blank canvas or a generic "How can I help you?" prompt.
- Every failure state maps to a specific `reason` code with specific copy — the only generic red state is network/5xx (§11.6).
- The assistant's text is never rendered in a bubble (§6.1).
- Every animation path is routed through `SavaMotion.respectingReduceMotion`.
- All Ask surfaces remain usable at Dynamic Type AX3.

---

## 21. Provenance

Produced by a read-only audit on 2026-08-18 covering: `api/routes_intelligence.py`, `api/ai/{base,router}.py`, `api/services/{intelligence,retrieval}.py`, `api/pipeline/understanding.py`, `api/config.py`, `api/rate_limiter.py`, `api/models.py`, and the iOS layer `ios/Sava/DesignSystem/*`, `ios/Sava/Features/{Shell,Search,Detail}/*`, `ios/Sava/Core/Models/Intelligence.swift`.

No production code was modified in producing this document, and none of its recommendations have been implemented.
