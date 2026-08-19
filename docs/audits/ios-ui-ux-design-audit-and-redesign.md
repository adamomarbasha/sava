# Sava iOS — UI/UX Design Audit & Redesign Specification

**Status:** Findings and specification recorded. **Nothing in this document has been implemented.**
**Audit date:** 2026-08-18
**Branch at time of audit:** `feat/intelligence-foundation` (commit `05192fc`)
**Scope:** every SwiftUI screen, component, design token, navigation structure and user flow in `ios/` (64 Swift files, ~4,211 LOC), evaluated as a consumer product design — not as code architecture.
**Method:** full read of every file under `ios/Sava/DesignSystem/`, `ios/Sava/Features/`, `ios/Sava/App/` and `ios/Sava/Core/`, cross-referenced against the live backend surface in `api/routes_intelligence.py`, `api/services/intelligence.py`, `api/services/retrieval.py`, `api/services/collections.py` and `api/main.py`. Design guidance from the project's `ui-ux-pro-max` skill.
**Explicitly out of scope:** code architecture (see `ios-swiftui-architecture-audit.md`), backend design, App Store submission readiness (see `ios-app-store-launch-readiness.md`).
**Read-only:** no production file was modified during this audit, and none should be modified on the basis of this document without a separate implementation task.

**Related documents in this folder** — read these before acting, they overlap:
- `ios-swiftui-architecture-audit.md` — code-level architecture. Deliberately did *not* cover UI.
- `conversational-intelligence-ux.md` — Ask This / Ask Sava UX.
- `collections-system-design.md` — Collections system design.
- `sava-search-design.md` — search design.
- `product-vocabulary-and-ux-copy-system.md` — copy and vocabulary system.
- `action-button-capture-architecture.md` — capture pipeline.

Where this document and those disagree on a detail, treat theirs as authoritative for their domain and this one as authoritative for visual/IA design. This document was written without reading them; a future session should reconcile before building.

---

## 0. Context for a future session with no memory of this work

Read this first if you are picking this up cold.

Sava is a native SwiftUI iOS client (`ios/`) for a FastAPI backend (`api/`). The product saves social-video links (TikTok / YouTube / Instagram / etc.), ingests and analyses them server-side, and is *supposed* to surface: effortless capture, multimodal search, personalised and manual Collections, AI Summary, Ask This (per-save Q&A), Ask Sava (library-wide Q&A), Related, and resurfacing.

The single most important thing to understand:

> **The backend on this branch implements nearly all of that. The iOS client implements almost none of it.**

That is not a styling problem, and it dominates every recommendation below. The audit was commissioned as a visual design review; the visual problems are real and catalogued in Section 2, but they are second-order. Sequencing matters — see Section 7.

Current signed-in structure of the app as built:

```
RootView                     — routes on SessionStore.phase
├── LaunchView               — splash, SavaMark + wordmark over LiquidBackground
├── AuthFlowView             — glass card over animated gradient blobs
└── AppShell                 — custom capsule tab bar, NOT a native TabView
    ├── Library              — 2-col masonry, pinned header, platform filter pills
    ├── Search               — .searchable over GET /api/bookmarks?q=
    ├── [center FAB]         — QuickSaveSheet (paste a URL)
    └── Profile              — identity, Action Button how-to, capture explainer, sign out

BookmarkDetailView (pushed from any stack)
    hero → header → Open in… → Ask Sava (MOCK) → Transcript → Comments → Details
```

---

## 1. Current-state finding: the capability gap

**This is the headline finding.** Every endpoint below is live on this branch. The "Client status" column is what the iOS app does with it.

| Product pillar | Endpoint (live) | Client status | What actually ships today |
|---|---|---|---|
| **Effortless capture** | `POST /bookmarks` | Partial | Paste-a-URL sheet only. Share extension and App Intents extension targets do not exist. `ContentResolverService.isEnabled = false` hard-disables screenshot resolution. |
| **Multimodal search** | `GET /api/search` | **Unwired** | Search calls `GET /api/bookmarks?q=` — a SQL keyword filter over title/author/note. The semantic endpoint (vector + keyword hybrid, `matched_on` provenance, content-type facets, `semantic` flag, `took_ms`) is never called. |
| **Summary** | `GET /api/bookmarks/{id}/summary` | **Missing** | No screen, no model, no service. Endpoint returns `tl_dr`, `key_points`, `topics`, `entities`, `typed_data`, `chapters`, `sources_used`, `processing_state` — all unused. |
| **Ask This** | `POST /api/bookmarks/{id}/ask` | **Faked** | A disabled mock card with three static example chips and a decorative send arrow. Real endpoint returns `answer` plus `citations[]` with `start_s`/`end_s`/`timestamp`/`source`/`text`, and `grounded_in`. |
| **Ask Sava** | `POST /api/ask` | **Missing** | No global entry point anywhere in the app. Endpoint does library-wide RAG, returns `answer` with numbered `[n]` references and a `sources[]` array of real saves. |
| **Collections** | `GET`/`POST /api/collections` | **Missing** | No screen. Server does auto-clustering (`kind == "auto"`), per-collection suggestions, manual curation, cover thumbnails, counts. |
| **Related** | `GET /api/bookmarks/{id}/related` | **Missing** | No surface. |
| **Resurfacing** | `GET /api/resurface` | **Missing** | No surface. Server ranks by forgetting-curve age (peaks ~30 days), note presence and summary presence, and returns a human-readable `reason` string per item. |
| Processing state | `GET /api/bookmarks/{id}/status` | **Unwired** | The "Analyzing" pill is client-guessed from a local `Set<Int>` and never cleared (F31). Real per-stage status with `processing_level`, `stages`, `has_transcript`, `has_understanding` is available. |
| Threads / history | `GET /api/threads`, `GET /api/threads/{id}/messages` | **Missing** | Conversations persist server-side and are invisible on device. |
| Model modes | `GET /api/ai/modes` | **Missing** | Returns Auto / Fast / Advanced, deliberately vendor-neutral. No picker in the app. |
| Reprocess | `POST /api/bookmarks/{id}/reprocess` | **Missing** | No recovery path for an unprocessed save. |
| Transcript | `POST /api/transcript` | **Wired** | Genuinely good: preview, full reader, in-transcript search, tap-to-timestamp deep link. |
| Comments | `GET /api/comments/{id}` | **Wired** | Renders, but fails silently (F34). |

**Consequence for design.** Sava's entire differentiation lives in the column the client ignores. Of eight stated product pillars the app ships one and a half — capture, plus a keyword `LIKE` filter standing in for multimodal search. A user opening Sava today cannot distinguish it from a 2015-era read-later app, and the one screen that promises otherwise is a dead rectangle.

**Blocking implication:** any visual direction that does not put summary, ask and collections in the first screenful is decorating the commodity half of the product. Restyling before wiring the intelligence layer is painting a house with no rooms in it.

---

## 2. Current-state findings: the visual and interaction layer

38 findings. IDs are stable — cite them in commits and issue titles. Severity is design/product impact, not code risk.

### 2.1 Information architecture & navigation

**F01 — CRITICAL — Profile occupies a third of primary navigation.**
The tab bar is Library / Search / Profile. Profile contains an email address, a "Saving since" line, an Action Button how-to, a capture explainer and Sign out. That is a settings sheet visited twice in a lifetime holding 33% of top-level real estate — while Collections, an actual product pillar, has no home at all.

**F02 — CRITICAL — Hand-rolled tab bar destroys navigation state on every switch.**
`AppShell.tabContent` is a `switch` that constructs a fresh `NavigationStack` per tab. Leaving Search and returning wipes the query, results and scroll offset — `SearchViewModel` is a `@StateObject` on a view that gets torn down. Only Library survives, because `libraryPath` was manually hoisted into `AppShell`. Native `TabView` provides state preservation, scroll-to-top on re-tap, VoiceOver tab rotor, Dynamic Type labels, iPad sidebar adaptation and double-tap-to-root for free. All are absent.

**F03 — HIGH — The most prominent button in the app is the fallback capture path.**
The accent-filled 52pt centre FAB opens a paste-a-URL sheet. On iOS the real capture surfaces are the share sheet and the Action Button — neither exists as a build target. The product's own tagline is "effortless capture", and the only implemented path requires the user to leave an app, copy a link, open Sava, tap a button and confirm.

**F04 — HIGH — Search is a tab whose default state is marketing copy.**
The idle state shows "Search your memory" and "Find saved videos by what happens in them — a recipe, a place, a topic, a moment." on ~85% empty screen. That is a promise the wired endpoint cannot keep. An idle search screen should offer the user's own material — recent queries, topics actually present in their library.

**F05 — MEDIUM — Detail screen has no toolbar.**
No share, no delete, no edit, no add-to-collection, no overflow menu. Every action is an inline button competing with content. Delete exists only inside a context menu on the grid.

### 2.2 Surface treatment — the card problem

**F06 — CRITICAL — One card recipe, copy-pasted five times, none of it componentised.**
`RoundedRectangle(cornerRadius:).fill(SavaColors.surface).overlay(strokeBorder(hairline))` appears verbatim in `AskSavaSection`, `MetadataSection`, `TranscriptSection`, `CommentsSection` and `ProfileView.InfoCard`. Meanwhile the design system's one real card component, `GlassCard`, is used on exactly one screen (auth). A token layer that feature code ignores is the clearest possible signal of code generated screen-by-screen rather than designed as a system.

**F07 — CRITICAL — Detail is five identical boxes, so nothing has hierarchy.**
"Ask Sava" — nominally the flagship AI moment — gets the same fill, border, radius and elevation as "Details", a four-row table of platform and dates. Visual weight communicates importance; uniform weight communicates nothing.

**F08 — HIGH — Nested rounded rectangles.**
Comment rows are radius-16 bordered cards inside a radius-22 bordered section. Concentric radii without a concentric relationship reads as a component library applied without judgement.

**F09 — MEDIUM — Five radii for six surface types.**
`sm 10 / md 16 / lg 22 / xl 28 / pill 999`. A single detail screen renders radii 999, 22, 16 and 0 (the hero) with no rhythm connecting them.

**F10 — MEDIUM — Redundant information stated four times.**
On Detail the platform appears as the navigation title, as a coloured badge next to the author, and as row one of the Details table. Then "Open in YouTube" says it a fourth time.

### 2.3 Typography

**F11 — CRITICAL — Nothing in the app responds to Dynamic Type.**
Every token in `SavaFont` is `.system(size:)` with a fixed point value. No `relativeTo:`, no text styles, no scaling. The file's own doc comment claims "Dynamic-Type friendly". A user who has raised their text size sees no change anywhere in Sava. This is simultaneously the largest accessibility failure and the clearest evidence the app was never run on a real person's phone. **Treat as a launch blocker.**

**F12 — HIGH — Twenty-plus inline font sizes bypass the scale.**
`.system(size: 9)`, `10`, `11`, `12`, `13`, `14`, `15`, `16`, `20`, `22`, `26`, `30`, `38` all appear inline in feature views. The 10pt tab-bar labels and the 9pt comment heart glyph are below any defensible floor.

**F13 — HIGH — Rounded and grotesque mixed without reason.**
Display sizes are `design: .rounded`; headline and body are default SF Pro. "Library" is geometric and friendly while the card titles beneath it are not. Rounded then reappears at 11–13pt on duration badges and step numbers. The stated rationale ("warmer, more consumer feel") is applied to three sizes and abandoned everywhere else.

**F14 — HIGH — Two points of separation between a card's title and its metadata.**
Bookmark card titles use `subheadline` (14pt medium); the author/age line uses `caption` (12pt medium). A 2pt delta at the same weight family is not a hierarchy. The scale also carries three overlapping steps (`subheadline` 14, `footnote` 13, `caption` 12) doing near-identical jobs.

### 2.4 Spacing, density & wasted space

**F15 — HIGH — Six control heights, no control-height token.**
32, 34, 46, 48, 52 and 54 all appear as fixed frame heights. `SavaPrimaryButton` is 54; `OpenOriginalButton` is 52; `StatusView`'s action is 48; Sign out is 52. Nothing in `SavaMetrics` governs this.

**F16 — HIGH — `.padding(.bottom, 120)` repeated in four files.**
A magic number compensating for the floating tab bar. It breaks the moment the bar's height changes, and it breaks now at large Dynamic Type. `.safeAreaInset` exists precisely for this.

**F17 — HIGH — A pinned header that never collapses spends ~14% of the viewport on the word "Library".**
34pt title row plus filter pills, pinned via `pinnedViews: [.sectionHeaders]`, ≈110pt permanently. The user already knows which tab they're on — it's highlighted. Native large titles collapse on scroll; this one cannot.

**F18 — MEDIUM — Ad-hoc spacing constants throughout.**
`spacing: 3`, `spacing: 5`, `.padding(.horizontal, 6).padding(.vertical, 3)`, `frame(width: 46)`, `frame(width: 90, height: 10)`. The 4pt grid in `Spacing` is advisory at best.

**F19 — MEDIUM — The empty library has no way out of itself.**
`StatusView` is called with no `actionTitle`, so a new user is told their library is empty and given no button to save anything. The single most important conversion moment in the app is a dead end.

**F20 — MEDIUM — Metadata gets equal billing with understanding.**
"Platform / Saved / Published / Source" is a full card on the detail page. It belongs in an overflow menu.

### 2.5 Colour & contrast

**F21 — CRITICAL — The app's most common secondary text fails WCAG AA in both themes.**
`textTertiary` light `#8B8F98` on `#FAFAF7` ≈ **2.9:1**. Dark `#6C6F78` on `#0A0A0C` ≈ **3.7:1**. Both fail 4.5:1. This token carries the author/age line on every card, the library count, filter counts, transcript line counts, comment likes and the entire QuickSave hint. **Treat as a launch blocker.**

**F22 — HIGH — Platform badges put white glyphs on brand fills without contrast checks.**
Snapchat `#FFD400` with white at a 0.5pt stroke is roughly **1.4:1** — effectively invisible. The pattern is wrong in principle: brand colour belongs to the platform, not to Sava's UI, and eight saturated discs scattered over already-colourful thumbnails is visual noise.

**F23 — HIGH — The accent is the default AI-product indigo.**
`#5457FF` / `#7C7EFF`. Periwinkle-indigo is the house colour of every LLM wrapper shipped since 2023. The ink-and-paper neutrals underneath it are genuinely well-considered — warm paper `#FAFAF7`, a designed deep ink `#0A0A0C` rather than an inversion. The accent squanders that.

**F24 — MEDIUM — Dead parameter: `FilterPill(tint:)`.**
Every call site computes and passes a platform tint. The view body never reads it. Selected pills fill with `textPrimary` regardless.

### 2.6 Tells: AI-generated and vibe-coded

**F25 — CRITICAL — Non-functional UI shipped as a feature.**
`AskSavaSection` renders a text field placeholder, a send button and three suggestion chips, then disables the entire block with `.opacity(0.7)` and `.allowsHitTesting(false)`. Users tap dead pixels. This is the single most damaging thing in the app.

**F26 — HIGH — Doc comments that are aspirational, self-congratulatory, or false.**
"Canvas-UI-inspired ripple." "A particle-reveal-inspired burst" (it is a checkmark scaling from 0.4). "Inspired by Canvas UI's Liquid/Displacement feel." "Dynamic-Type friendly" (it is not — F11). "The backend… does NOT expose a per-video summary or Q&A endpoint" (it does — Section 1). The codebase also repeatedly argues with itself about honesty — "never fabricated", "never inventing engagement", "No AI text is synthesized here" — which is what generated-then-patched code looks like.

**F27 — HIGH — The gradient-blob auth screen.**
`LiquidBackground` is three blurred circles at 90pt blur drifting on a 6-second loop, behind an `.ultraThinMaterial` glass card. This is the most instantly recognisable AI-generated design artifact of the current era. It also appears nowhere else in the app, so it is brand identity that vanishes the moment you sign in.

**F28 — MEDIUM — Icons chosen by name-matching.**
`sparkles` for AI (the most overused symbol in the category), `wand.and.stars` for "How capture works", `clock.badge` for "coming soon", `antenna.radiowaves.left.and.right` for Reddit. The Search tab's selected and unselected symbols are the same glyph, so tapping it produces no state change beyond colour.

**F29 — MEDIUM — Marketing copy inside the product.**
"Sava saves it instantly and analyzes it in the background." "Save a video… and it lands here — understood and searchable." "Just a sec…" as a loading button label, which replaces the action verb and loses the user's context. Product UI states what is happening; it does not sell.

**F30 — MEDIUM — The logo appears three times before any content does.**
`SavaMark` at 76pt on launch, 60pt on auth, 64pt on Profile. The user has already installed the app.

### 2.7 Functional defects found during design review

These are behaviour bugs, surfaced because they are visible to the user. They are recorded here for completeness; `ios-swiftui-architecture-audit.md` may cover some at code level.

**F31 — CRITICAL — The "Analyzing" badge never goes away.**
`LibraryViewModel.processingIDs` is inserted into on save and has no removal path anywhere in the file. Nothing polls `/status`. A saved item pulses "Analyzing" until the process is killed.

**F32 — CRITICAL — Save is permanently disabled after one failure.**
`QuickSaveViewModel.canSave` returns false for any phase other than `.editing`, and the failure path sets `.failed` without returning to `.editing`. One network blip and the user must dismiss and reopen the sheet.

**F33 — HIGH — Transcript retry is a no-op.**
`TranscriptSection.reload()` calls `loadTranscript`, which guards on `transcriptState == .idle`. State is `.failed`. The button does nothing. The comment beside it — "state guards on .idle, so nudge through" — describes a nudge that is not in the code.

**F34 — HIGH — Comments fail silently.**
`CommentsSection` renders `EmptyView()` for `.loading`, `.empty` *and* `.failed`. A server error is indistinguishable from a video with no comments.

**F35 — HIGH — Session expiry drops the user onto the auth screen mid-session.**
Token TTL is 30 minutes with no refresh. Any 401 calls `signOut()`, which flips the root phase and destroys all navigation state. The user is scrolling their library and lands on a gradient blob. **The missing refresh token is a backend dependency the client cannot design around.**

**F36 — MEDIUM — `UIScreen.main.bounds` drives masonry layout.**
In `BookmarkGrid.estimatedHeight`. Deprecated, wrong under multitasking, wrong on rotation, wrong on iPad. Column balance is estimated rather than measured, so it drifts.

**F37 — MEDIUM — Random fallback identity on a Decodable.**
`SavedComment.id = Int.random(in: Int.min...Int.max)` when decoding fails. Identity changes on every decode, breaking `ForEach` diffing and any animation over the list.

**F38 — MEDIUM — One error illustration for every failure mode.**
`wifi.exclamationmark` and "Can't reach Sava" render for 500s, decode errors and genuine offline alike. The app tells the user to check their connection when the server threw.

---

## 3. Current-state findings: screen by screen

| Screen | Verdict | The core problem |
|---|---|---|
| **Library** | Best screen | The 2-column masonry is genuinely good — content-first, correct aspect ratios per platform, honest skeletons. Undermined by a non-collapsing ~110pt header, a permanently stuck processing badge, no resurfacing strip, and card metadata below AA contrast. |
| **Search** | Wrong endpoint | Calls the keyword filter, not semantic search. Idle state is a value proposition instead of the user's own topics and recents. Loading state is an 8-tile masonry shimmer for a query that returns three rows. |
| **Search results** | Hides the magic | Identical masonry to Library, so semantic retrieval looks exactly like scrolling. `matched_on`, `tl_dr` and the `semantic` flag are all returned by the real endpoint and none are shown. Nothing tells the user *why* a result matched. |
| **Save detail** | Card soup | Hero, then five bordered boxes of equal weight, ordered content → fake AI → transcript → comments → metadata. Understanding — the reason the product exists — is absent; metadata gets a card. |
| **AI Summary** | Does not exist | No screen, no model, no service, despite a live endpoint returning structured understanding with key points, chapters, topics, entities and typed data. |
| **Ask This** | Does not exist | No surface. The endpoint returns answers with timestamped citations that could deep-link straight into the transcript reader that is already built. |
| **Ask Sava** | Mockup | A disabled card on the detail screen labelled with a per-save scope — which is not even what Ask Sava is. The real endpoint is library-wide. |
| **Collections** | Does not exist | No screen. Server-side auto-clustering, per-collection suggestions and manual curation all unreachable. |
| **Profile** | Misplaced | Four instructional cards and a Sign out, holding a primary tab. The content is fine; the placement costs the app its Collections tab. |
| **Capture states** | One weak path | Paste-URL only. Success is a full-sheet takeover requiring a "Done" tap for a fire-and-forget action. No 409 "already saved" handling. Clipboard prefill happens silently, which is startling. |
| **Auth** | Generic | Gradient blobs, glass card, uppercase-tracked web-form labels, no Sign in with Apple, no password reset, and a 30-minute session that ejects you back here. |
| **Loading states** | Half right | The library skeleton matches its real geometry — good. But it is reused for Search, where the geometry does not match, and "Just a sec…" replaces button verbs. |
| **Error states** | Undifferentiated | One wifi icon for all failure classes; comments fail silently; transcript retry does nothing. |
| **Empty states** | Dead end | Centred 76pt tinted circle, title, message, no action. The templated empty-state component, used templatedly. |

### What is genuinely good and must be preserved

Do not throw these away during a rewrite:

- The two-column masonry with per-platform aspect ratios (portrait for TikTok/Instagram/Snapchat, landscape otherwise).
- The transcript reader: preview, full-screen sheet, in-transcript search, tap-a-timestamp to open YouTube at `?t=`.
- The skeleton loading approach on Library — it matches real geometry.
- The neutral colour foundation: warm paper `#FAFAF7`, designed deep ink `#0A0A0C`. Only the accent is wrong.
- `Haptics` as a centralised vocabulary (over-applied, but the abstraction is right).
- `SavaMotion.respectingReduceMotion` — correct Reduce Motion handling that simply needs extending.
- The honest per-source loading states in `DetailViewModel` (`unsupported` / `unavailable` / `failed` are properly distinguished).

---

## 4. Recommendations: three visual directions

Three genuinely different premises about what Sava *is* — not three skins on one wireframe. Each evaluated on layout, navigation, colour, typography, AI placement, content presentation, collections presentation and detail-page structure.

### Direction A — "The Reading Room"

*Sava is not a bookmark grid. It is a personal publication, edited nightly by a machine that watched everything you saved. What the AI understood **is** the content; the video is the footnote.*

- **Layout** — Single-column editorial feed, no masonry. Each save is a row: 1/3-width thumbnail, display-serif title, `tl_dr` as body copy. ~4 items per screen, each carrying meaning rather than just an image. Feed sectioned editorially: "Continue", "Worth revisiting" (from `/resurface`, using its own `reason` string as the standfirst), "This week".
- **Navigation** — Two tabs (Library, Collections) plus a persistent bottom-anchored pill: "Ask or search your library". Search and Ask are the same gesture, expanding into a full-screen sheet. Profile becomes a nav-bar avatar. The AI is not a destination; it is the address bar.
- **Colour** — Paper `#FBFAF7` / ink `#12100E`, warm greys. A single warm signal — deep ochre — reserved *exclusively* for machine-generated content and citations, so the user learns the colour means "Sava wrote this". AI passages carry a left rule in that colour. Dark mode is a warm black, not a cool one.
- **Typography** — New York (the system serif, native and free) for titles and all AI prose at 20–22pt; SF Pro Text for chrome and metadata. Three roles only: Display, Read, Meta. Full Dynamic Type via `relativeTo:`. The serif is the brand — it says "this was written for you".
- **AI placement** — Maximum. Summary is the card body in the feed; key points are the expanded row; Ask This is a docked bar on Detail; Ask Sava is the global pill. Citations render inline as timestamp chips deep-linking into the transcript.
- **Content presentation** — Text-forward, thumbnails supporting. Low image density, high meaning density. Topics become editorial tags.
- **Collections** — Shelves: a horizontal row at the top of Library plus a dedicated tab. Auto-clusters labelled "Found by Sava"; manual ones plain, so the user can always tell who made the decision.
- **Detail page** — A reading page. Hero collapses to an inline thumb on scroll; title; summary in serif; key points; chapters as a tappable timeline; transcript expandable; related as a footer shelf; metadata behind an overflow menu. Ask This docked throughout.
- **Risk** — An editorial serif feed of TikToks is a category error for most of the library. Strongest for long-form YouTube and articles, weakest for the 30-second vertical video that is probably the median save.

### Direction B — "Signal"

*Sava is a visual memory. The interface disappears into near-black and the saved media becomes the only light source. Chrome-free, OLED-native; AI arrives as ambient intelligence rather than as a labelled feature.*

- **Layout** — Near-edge-to-edge two-column mosaic (12pt outer gutter, 8pt gaps) with **zero card chrome**: no fills, borders or shadows. Just media on black. Titles and metadata sit *outside* the tiles in a single tight line. Above the mosaic, a "Worth revisiting" strip of three large peeking cards that scrolls away.
- **Navigation** — Native `TabView`, four tabs: Library, Collections, **Ask**, Search. Ask is a first-class destination showing real thread history. A persistent Ask pill floats above the tab bar on Library, Collections and Detail — scope-aware, so on a save it becomes "Ask about this". Capture is the share extension and Action Button; in-app it is a quiet nav-bar `+`.
- **Colour** — OLED `#000000` ground, `#0E0E10` elevated, system greys for text. **No brand accent in the chrome at all.** The only saturated colour is derived from content: each detail page samples its thumbnail's dominant hue and lays it as a 12–14% ambient wash behind the hero. AI surfaces are marked by *light* — a 6% white fill and a 14% white hairline — never by a hue.
- **Typography** — SF Pro Display and Text exclusively, no rounded anywhere. Weight and size carry the whole hierarchy: 28pt Bold titles down to 13pt Regular meta, tracking −0.02em on titles. Seven roles, all text-style-based so Dynamic Type works end to end.
- **AI placement** — Ambient and unavoidable. Summary auto-expands at the top of every detail page with a shimmer-to-text reveal driven by real `/status` stages. Ask is a tab *and* a persistent pill. Answers show their sources as a shelf of real saves underneath. No sparkle icons, no "AI" badges — the intelligence is demonstrated, not announced.
- **Content presentation** — Media-maximal. Long-press plays a two-second muted loop. Thumbnails are the interface.
- **Collections** — Two-column grid of cover stacks: the cover thumbnail with two cards fanned behind it at ±3°. Tapping opens the collection as its own mosaic. Suggested additions appear as a swipe-to-accept review row at the top.
- **Detail page** — Immersive. Hero fills to the top under a transparent nav bar with parallax; a content sheet slides over it. Sections separated by whitespace and 11pt small-caps labels — *no card ever*. Order: Summary → Key points → Typed data → Chapters → Transcript → Comments → Related. Details lives in the overflow menu. Ask This docked at the bottom.
- **Risk** — Dark-first is a commitment; light mode needs a genuinely designed answer rather than an inversion. Colour-from-artwork needs a graceful fallback when a thumbnail is monochrome or missing.

### Direction C — "Workbench"

*Sava is a tool for someone with 800 saves and a question. Density, structure, filters, keyboard-speed. The most complete exploitation of what the backend actually returns.*

- **Layout** — A `List`-based dense column: 56pt leading thumbnail, two lines of text, trailing state glyph. A segmented control switches List / Grid / Gallery, as Files and Photos do. Section headers are time buckets with counts. Sticky filter chips that scroll away.
- **Navigation** — Four native tabs (Library, Collections, Ask, Search) adapting to `NavigationSplitView` with a real sidebar on iPad and landscape. `+` in the nav bar. Multi-select mode in the library for batch add-to-collection.
- **Colour** — Neutral greys on pure white / `#0B0B0D`. The accent is *structural*, not decorative: colour encodes processing state from `/status` — grey queued, amber processing, nothing at all when ready. Interactive text uses a single graphite blue. Semantic colour is the only colour.
- **Typography** — SF Pro Text throughout, tabular figures everywhere digits align (counts, durations, timestamps, scores). A tight four-step scale: 22 Semibold / 17 Semibold / 15 Regular / 13 Regular, plus 11pt uppercase tracked section labels — the one place tracking is permitted.
- **AI placement** — Structured and inspectable. Summary as a collapsible disclosure whose state is remembered. Key points as a real bulleted list. Uniquely: `typed_data` and `entities` rendered as structured tables — a saved recipe shows its ingredients, a saved place shows a map link. The only direction that pays off the backend's typed extraction.
- **Content presentation** — Metadata-rich rows. Topic chips feed back into `/api/search?content_type=` so browsing and searching are one loop.
- **Collections** — The richest treatment: a sidebar list with live counts, drag-to-add from the library, multi-select "Add to collection", and a dedicated review queue for `/suggestions` where Sava proposes and the user accepts or rejects. The personalisation loop made explicit.
- **Detail page** — Two-pane on iPad, stacked on iPhone. Everything is a disclosure group with remembered state: Summary → Key points → Typed data → Chapters → Transcript → Comments → Related → Details. Optimised for returning to a save you half-remember.
- **Risk** — Reads as a productivity tool rather than something you would show a friend. Also closest to what already exists, so it wins the least ground per unit of work.

---

## 5. Recommendation: ranking and the chosen direction

| Axis | A · Reading Room | B · Signal | C · Workbench |
|---|---|---|---|
| Fit to the median save (short vertical video) | Weak | **Strong** | Fair |
| Kills the card problem | Fair — cards become articles | **Strong — chrome removal is the thesis** | Fair — cards become rows |
| Makes AI unmissable | **Strong** | **Strong** | Fair — powerful but buried in disclosures |
| Exploits the backend fully | Fair | Good | **Strong** |
| Native iOS feel | Good | **Strong** | **Strong** |
| Content density | Low | **High** | **Highest** |
| Brand distinctiveness | **Highest** | High | Low |
| Scales to 1,000 saves | Weak | Good | **Strong** |
| Implementation cost | Medium | Medium | Low |

**Ranked:**

1. **B · Signal — recommended.** Sava's content is short-form video. A chrome-free, media-maximal presentation is the only direction where the *content* does the design work — precisely what separates Photos, Apple Music and Arc from developer UIs. It also fixes the worst structural failure by making Ask a tab and a persistent pill, and "delete every card" stops being a cleanup task and becomes the design premise.
2. **A · Reading Room.** The most distinctive brand and the best use of `tl_dr`, but it fights the medium. Keep it in the drawer as a future "Sava Digest" surface — a weekly edited read over what you saved. That is where the serif earns its place. *(Future-phase item, not discarded.)*
3. **C · Workbench.** The most complete and cheapest to build, and the only direction that pays off `typed_data`. But least likely to make anyone show the app to a friend, and nearest to what exists today.

**The actual recommendation: build B as the spine, and graft deliberately from both others.**
- From **A**: the conviction that AI prose is body copy, not a widget — this is what makes the Detail summary work.
- From **C**: the `typed_data` tables and the multi-select collection curation with a suggestion review queue — the depth that keeps power users.

Everything in Section 6 is B with those two grafts, and calls out which is which.

---

## 6. Recommendation: redesign specification

Written to be executed. An implementing agent should be able to work straight down this section. **None of it is implemented.**

### 6.1 Token layer

**Delete outright:**
- `LiquidBackground.swift` and the `meshA/B/C` tokens (F27)
- `GlassCard.swift` — replaced by material only on the tab bar and sheets
- `SavaColors.accent` / `accentDeep` / `accentSoft` in their current indigo (F23)
- `Radius.sm`, `Radius.lg`, `Radius.xl` — collapse to three (F09)
- `SavaMotion.bounce` and `SavaMotion.ambient`
- The `.rounded` design on `SavaFont.largeTitle` / `title` / `title2` (F13)
- `PlatformColor.tint` as a UI fill — retain the enum for reference only (F22)

**Colour — dark (primary):**

| Token | Hex | Note |
|---|---|---|
| `background` | `#000000` | OLED ground |
| `backgroundElevated` | `#0E0E10` | |
| `surface` | `#16161A` | sheets only |
| `textPrimary` | `#FFFFFF` | |
| `textSecondary` | `#A1A1A6` | ≈9.9:1 |
| `textTertiary` | `#8E8E93` | ≈7.6:1 |

**Colour — light:**

| Token | Hex | Note |
|---|---|---|
| `background` | `#FFFFFF` | |
| `backgroundElevated` | `#F5F5F7` | |
| `textPrimary` | `#000000` | |
| `textSecondary` | `#6C6C70` | ≈5.4:1 |
| `textTertiary` | `#6E6E73` | ≈5.2:1 |
| `link` | `#0066CC` | |

**Semantic only — never decorative:** `processing #FF9F0A`, `failed #FF453A`, `saved #30D158`.

**Rules.** Separator is `white @ 0.12` (dark) / `black @ 0.10` (light). There is **no brand accent in chrome** — interactive text uses the link colour, and AI surfaces are marked by light: `white @ 0.06` fill with a `white @ 0.14` hairline. `ready` has no indicator at all; absence means done. Add `ambientTint`: the dominant colour sampled from the hero thumbnail, applied at 0.14 opacity as a radial behind the hero and 0.06 behind the scroll view, with a plain-background fallback.

**Radius, spacing, controls:**

```swift
// Radius — three values, no more
enum Radius { static let tile: CGFloat = 14   // media, sheet inner
              static let sheet: CGFloat = 28  // presented surfaces
              static let pill: CGFloat = 999 }

// Layout — replaces every magic number
enum Layout { static let gutter: CGFloat = 12   // mosaic screen margin
              static let reading: CGFloat = 20  // text screen margin
              static let tileGap: CGFloat = 8 }

// Controls — exactly two heights (kills F15)
enum Control { static let primary: CGFloat = 50
               static let compact: CGFloat = 34 }  // visual; 44pt hit area
```

**The card rule, enforced.** No filled-and-bordered rounded rectangle may be used to group content. Sections are separated by whitespace and an 11pt uppercase label. Rounded rectangles are permitted only for: media tiles, presented sheets, pills/chips, and text input fields. Nothing else. **This single rule resolves F06 through F09.**

**Bottom insets.** Delete every `.padding(.bottom, 120)`. Native `TabView` handles this; anything floating uses `.safeAreaInset(edge: .bottom)`.

**Typography — seven roles, all Dynamic Type:**

```swift
enum SavaFont {
  static let display   = Font.system(.largeTitle, weight: .bold)    // tracking -0.5
  static let title     = Font.system(.title2,     weight: .bold)
  static let headline  = Font.system(.headline,   weight: .semibold)
  static let body      = Font.system(.body,       weight: .regular)  // AI prose
  static let callout   = Font.system(.subheadline, weight: .regular)
  static let meta      = Font.system(.footnote,   weight: .regular)
  static let label     = Font.system(.caption2,   weight: .semibold) // uppercase, +0.8
  static let mono      = Font.system(.footnote, design: .monospaced)
}
// App root: .dynamicTypeSize(...DynamicTypeSize.accessibility2)
// Zero .rounded. Zero .system(size:) outside this file — enforce in review.
```

### 6.2 Information architecture

```
TabView (native)
├── Library      NavigationStack   avatar → Settings sheet · trailing +
├── Collections  NavigationStack
├── Ask          NavigationStack   thread list + composer
└── Search       NavigationStack   .searchable + .searchScopes

Floating AskPill — .safeAreaInset above the tab bar
  on Library / Collections → scope: library    ("Ask Sava")
  on Detail                → scope: this save  ("Ask about this")

Capture, by priority:
  1. Share Extension target        ← NEW, primary
  2. App Intents extension target  ← NEW, wires the existing SaveToSavaIntent
  3. In-app + sheet                ← demoted from FAB to nav-bar button
```

Profile moves into a Settings sheet reached from a nav-bar avatar on Library. Delete `SavaTabBar` entirely (F02). Delete the centre FAB (F03).

### 6.3 Library

- **Nav bar:** native large title "Library" that collapses on scroll (fixes F17). Leading: avatar → Settings. Trailing: `+`.
- **Resurface strip** — render only when `GET /api/resurface` returns ≥3. Horizontal scroll of 240×160 cards: thumbnail, title, and the server's `reason` string as the caption. Scrolls away with content. *New surface — closes the resurfacing gap.*
- **Filter chips** — All + top 5 platforms + content-type facets. 34pt visual, 44pt hit area. Scrolls away, not pinned. Remove the unused `tint` parameter (F24).
- **Mosaic** — `LazyVGrid`, 2 columns, `Layout.tileGap` 8, `Layout.gutter` 12. Tiles are pure media at `Radius.tile`, **no border, no shadow, no fill**. Overlays: monochrome white platform glyph bottom-leading with a 0.5 shadow (replaces the coloured disc — fixes F22), duration bottom-trailing. Title and meta sit *outside* the tile.
- **Processing** — poll `GET /api/bookmarks/{id}/status` at 2s → 5s → 15s backoff, stopping at `ready` or `failed`. Render `processing_level` as a 3-segment 2pt amber bar under the tile. On `ready`, clear silently — no toast. Replaces the pulsing pill and fixes F31.
- **Paging** — 50 per page; trigger the next fetch from the eighth-from-last item's `.task`. Today the app requests `limit: 500` in one shot.
- **Context menu** — `contextMenu(preview:)` with a large media preview and actions: Add to Collection, Ask about this, Share, Delete.
- **Layout math** — replace `UIScreen.main.bounds` with `GeometryReader` or a `ViewThatFits`-driven column count: 2 on compact width, 3–4 on regular (F36).

### 6.4 Search & results

- **Point it at the real endpoint:** `GET /api/search?q=&platform=&content_type=&limit=30`. Delete the `/api/bookmarks?q=` path from search entirely.
- **Native search:** `.searchable` with `.searchScopes` bound to content types (All / Video / Article / Recipe / Place).
- **Idle state** — replace the marketing copy (F04, F29) with the user's own material: recent searches as swipe-to-delete rows, then **"Topics in your library"** — a wrapped chip cloud built from aggregated `topics` across summaries, each chip running a search on tap.
- **Results are rows, not a mosaic.** 72pt leading thumbnail; title on 2 lines; then **`tl_dr` on 2 lines in secondary**; then a provenance chip row built from `matched_on` — "transcript", "on-screen text", "title". *This row is where semantic search becomes visible.* Never show the raw score.
- **Header:** "12 results · semantic" when the response's `semantic` flag is true, plain count otherwise. `took_ms` in DEBUG only.
- **Empty:** "No matches for 'x'" plus a primary button — **"Ask Sava instead"** — handing the query straight to `POST /api/ask`. Highest-leverage moment in the app: a failed search becomes a demonstration.
- **Loading:** four row skeletons matching the result geometry. Stop reusing `LibrarySkeleton` here.

### 6.5 Save detail

Ordered by what the user came for. Every section is whitespace + an 11pt uppercase label. **Not one card on this screen.**

1. **Hero** — full-bleed under a transparent nav bar (`.toolbarBackground(.hidden, for: .navigationBar)`), height `min(width × aspect, 0.55 × screenHeight)`, ambient tint radial behind, scrim to background over 120pt. Tapping the hero opens the original — replaces the standalone "Open in…" button (F10).
2. **Title block** — title at `.title` Bold, 3 lines max; one meta line: creator · age · duration; view and like counts inline as `.meta` tabular figures. No chips, no badges.
3. **SUMMARY** — `GET /api/bookmarks/{id}/summary`. The `tl_dr` set at `.body` 17pt in `textPrimary` with generous leading — *graft from Direction A: AI prose is body copy, not a widget.* Then `key_points` as a real list. Then `topics` as tappable chips routing to search. Close with a provenance line built from `sources_used`: "Sava read the transcript and 42 comments." That line is what makes the feature trustworthy and it exists nowhere today.
   - *States:* `not_linked` → "Sava hasn't processed this yet" + "Process now" → `POST /reprocess`. `ai_unavailable` → hide the section entirely; never advertise a broken feature. `no_content` → one quiet line. Generating → 3-line shimmer labelled "Reading this for you…", shown only when user-triggered.
4. **ASK THIS** — a docked bar pinned above the tab bar, not a section. Tapping opens a sheet at `[.medium, .large]` carrying the thread from `GET /api/threads?bookmark_id=`. Answers render citation chips from the response's `citations` array (`timestamp` + `source`); tapping one opens the transcript reader scrolled to that segment, or the video at `?t=`. Mode picker from `GET /api/ai/modes` as a nav-bar menu in the sheet — Auto / Fast / Advanced, never a vendor name.
5. **CHAPTERS** — when `chapters` is non-empty: timestamp + title rows, tappable to the original at that offset.
6. **IN THIS** — *graft from Direction C.* Render `typed_data` per content type: a recipe shows ingredients and steps; a place shows name and a map link; a product shows price and source. Key-value rows on hairlines. The highest-value unused data in the system.
7. **TRANSCRIPT** — keep the existing preview and full reader (both genuinely good), remove the card chrome, and fix the retry no-op (F33) with an explicit `retry()` that resets state to `.idle` first.
8. **COMMENTS** — hairline-separated rows, no nested cards (F08). Render a real failure state instead of `EmptyView()` (F34).
9. **RELATED** — `GET /api/bookmarks/{id}/related`, horizontal shelf of 120×160 tiles. Hidden when the response reason is `not_processed`.
10. **Toolbar** — `...` menu: Share · Add to Collection · Regenerate summary (`refresh=true`) · Open original · Details · Delete. The Details table moves here from the scroll (F20, F05).

### 6.6 Ask Sava (new tab)

- **Landing, no threads:** centred composer with four suggested questions generated from the user's own top topics — "What did I save about Tokyo?", "Show me the recipes I saved". Never generic examples.
- **Landing, with threads:** list from `GET /api/threads` — title, relative date, a glyph distinguishing library scope from save scope — with the composer docked at the bottom.
- **Thread view:** user turns right-aligned and plain; assistant turns at `.body`. The answer text contains numbered references like `[2]` — parse them into tappable inline chips, and render the `sources` array as a horizontal shelf of real saves beneath the answer. **This is the product's magic trick; it must be the most visible thing on the screen.**
- **Completion line:** "Answered from 6 saves" from `grounded_in`.
- **Pending:** the endpoint is synchronous — show a typing indicator, not a spinner.
- **Failure:** `ok: false, reason: "ai_unavailable"` renders an honest inline line and the tab shows a disabled state. It is not hidden — hiding is how you get F25 back.

### 6.7 Collections (new tab)

- **Grid:** 2 columns of cover stacks — the cover thumbnail with two cards fanned behind at −3° and +3°, 4pt offsets. Name at `.headline`, count at `.meta`. Auto collections (`kind == "auto"`) carry a small glyph and the line "Found by Sava"; manual ones do not, so authorship is always legible.
- **Collection detail:** large title = name, description beneath, then the Library mosaic. Toolbar: Rename · Add saves · Rebuild.
- **Suggestion review** — *graft from Direction C.* When `GET /api/collections/{id}/suggestions` returns items, show a horizontal review row at the top: each suggestion as a tile with accept and dismiss. Accept posts to `/items`. This is the personalisation loop, and there is currently no surface for it anywhere.
- **Add saves:** multi-select over the library mosaic → `POST /api/collections/{id}/items`.
- **Empty:** "Collections group saves that belong together." Primary: "Find them automatically" → `POST /api/collections/rebuild`. Secondary: "New collection".

### 6.8 Capture

- **Share Extension (new target)** — compact UI: thumbnail, detected platform, optional note, collection picker, Save. Auto-dismiss ~600ms after success with a checkmark morph. This becomes the primary path.
- **App Intents extension (new target)** — wire the existing `SaveToSavaIntent` with an `AppShortcutsProvider`, returning `.result(dialog:)` with a snippet of the saved item so the Action Button confirms without opening the app.
- **In-app sheet fixes:** reset `phase` to `.editing` on failure so Save re-enables (F32); surface a clipboard URL as a tappable suggestion chip rather than silently prefilling; handle 409 as "Already in your library" with an "Open it" action.
- **Success:** delete the full-sheet takeover with its "Done" button. Dismiss, insert at the top of Library, start status polling.
- `ContentResolverService` stays disabled and its dead UI copy stays out of the interface until the endpoint ships.

### 6.9 Auth

- Delete `LiquidBackground`; solid ground (F27).
- Invert the hierarchy: small wordmark, one line of value, then **Sign in with Apple** as the primary action, then "Continue with email" as a text button that reveals the form.
- Native field styling. Validate on blur, not on keystroke. Add "Forgot password".
- Loading keeps the verb and appends a trailing `ProgressView`. Delete "Just a sec…" (F29).
- **Session expiry (F35):** on 401, do *not* flip the root phase. Keep the UI mounted and present a compact re-auth sheet over it, preserving navigation state. **Blocker/dependency:** a 30-minute hard expiry with no refresh token is a backend product decision the client cannot design around — see Section 9.

### 6.10 States, everywhere

- **Scope rule:** a state view replaces the container that failed, never the whole screen. A failed related-shelf does not blank the detail page.
- **Skeletons** must match real geometry exactly — same columns, gaps, radii, line heights. Never reuse one screen's skeleton on another (F04).
- **Errors classify** (F38): `.offline` → "You're offline", show stale cache if present. `.server` → "Sava had a problem" + Retry. `.unauthorized` → re-auth sheet. `.notFound` → its own copy. One icon for all four is a diagnosis the app has not made.
- **Empty states** carry exactly one primary action, always (F19). Delete the 76pt tinted circle treatment; empty states become typographic — a 22pt Semibold line, a 15pt secondary line, a button — left-aligned at the content gutter, not centred. Centred pity-circles are the templated look.
- **Never** ship a disabled mock. If an endpoint is not live, the surface does not exist (F25).

### 6.11 Accessibility gates

- Every font via text styles; app root clamped to `.accessibility2`; layouts verified at `.xxxLarge`.
- Every text/background pair ≥ 4.5:1 — the tokens in 6.1 are chosen to pass (F21).
- Coloured platform discs deleted in favour of monochrome glyphs, removing the Snapchat failure (F22).
- Tiles get `.accessibilityElement(children: .combine)` with an explicit label: "*title*, by *author*, *platform*, saved *age*" and a hint of "Opens details".
- Every icon-only control gets a label — overflow menu, ask arrow, `+`.
- 44×44 minimum hit areas: chips render at 34 visual with `.contentShape(Rectangle()).frame(minHeight: 44)`.
- Reduce Transparency → tab bar and sheets fall back to solid `backgroundElevated`. Reduce Motion → extend the existing (genuinely good) handling to hero parallax and the ambient tint.

### 6.12 Motion

Three curves, down from five: `tap` (0.28 / 0.72), `standard` (0.42 / 0.82), `sheet` (0.5 / 0.9). All gated on Reduce Motion via the existing helper. Haptics: keep the vocabulary, but stop firing `Haptics.tap()` on every pressable — cards should not buzz. Reserve haptics for save success, save failure, and filter selection.

---

## 7. Recommendation: build sequence

Eleven pull requests, ordered so the app compiles and ships at every step. **Architecture before pixels.**

| PR | Scope | Closes |
|---|---|---|
| 01 | **Tokens.** Rewrite `SavaColors`, `SavaFont`, `Radius`, `Layout`, `Control`. Delete `LiquidBackground`, `GlassCard`, mesh tokens, the indigo accent. Mechanical substitution only — no screen changes. | F09 F11 F13 F15 F21 F23 F27 |
| 02 | **Navigation.** Native `TabView`, four tabs. Delete `SavaTabBar` and the FAB. Profile → Settings sheet. Replace all bottom-padding magic numbers with `.safeAreaInset`. | F01 F02 F03 F16 |
| 03 | **Network.** Real `IntelligenceService`, `CollectionsService`, `SearchService`, `StatusService`. Codable models for Summary, Answer, Thread, RetrievedSave, Collection, Status, Mode. Delete the `isEnabled = false` gate and its false doc comment. | F26 |
| 04 | **Library.** Chrome-free mosaic, resurface strip, status polling, context-menu previews, paging, GeometryReader layout, monochrome platform glyphs. | F17 F19 F22 F24 F31 F36 |
| 05 | **Search.** Point at `/api/search`. Scopes, topic-cloud idle state, provenance rows with `tl_dr`, "Ask Sava instead" on empty, matched skeletons. | F04 F29 |
| 06 | **Detail.** Decard everything. Summary-as-lead with provenance line, chapters, typed data, related shelf, docked Ask This with citation deep-links, overflow toolbar. Fix transcript retry and comment failure. | F05 F06 F07 F08 F10 F20 F25 F33 F34 |
| 07 | **Ask Sava tab.** Threads, composer, inline numbered sources with a save shelf, mode picker, honest unavailable state. | — |
| 08 | **Collections tab.** Cover-stack grid, collection detail, suggestion review row, multi-select add, rebuild. | — |
| 09 | **Capture.** Share Extension target, App Intents extension target, QuickSave fixes, drop the success takeover. | F32 |
| 10 | **Auth.** Sign in with Apple, native fields, forgot-password, non-destructive re-auth sheet on 401. | F35 |
| 11 | **Sweep.** Dynamic Type audit at `.xxxLarge`, contrast script over all token pairs, VoiceOver pass, snapshot tests, haptic reduction, `SavedComment` identity fix. | F12 F14 F18 F28 F30 F37 F38 |

---

## 8. Acceptance criteria

Mechanically checkable. If any line fails, the redesign is not done.

- [ ] Zero occurrences of `.system(size:` outside `SavaFont.swift`.
- [ ] Zero occurrences of `RoundedRectangle(...).fill(...surface).overlay(...strokeBorder` anywhere under `Features/`.
- [ ] Zero hardcoded bottom paddings; every inset derives from `.safeAreaInset` or the tab bar.
- [ ] All thirteen live endpoints in Section 1 are reachable from at least one screen.
- [ ] Every text-on-background token pair measures ≥ 4.5:1 — checked by script, not by eye.
- [ ] The app is fully usable at `DynamicTypeSize.accessibility2` with no truncation, clipping or overlap.
- [ ] Switching tabs and returning preserves scroll offset, search query, and any pushed detail view.
- [ ] No UI element is rendered in a disabled or non-functional state to represent a future feature.
- [ ] A saved item's processing indicator clears on its own within one polling cycle of the server reporting ready.
- [ ] Every failure path is distinguishable: offline, server error, not found, and unauthorised each render differently.
- [ ] A failed search offers Ask Sava; a search result shows why it matched.
- [ ] Detail renders the summary above the fold on a 6.1″ device with default text size.

---

## 9. Launch blockers, risks, and unresolved decisions

### Launch blockers (design/accessibility)
1. **F11 — no Dynamic Type support anywhere.** Fails basic iOS accessibility expectations and is likely to draw App Store review attention.
2. **F21 — `textTertiary` fails WCAG AA in both light and dark.** It carries most secondary text in the app.
3. **F25 — a disabled, non-functional mock shipped as a product feature.** Users tap dead pixels on the flagship AI surface.
4. **F31 — the "Analyzing" indicator never clears.** Every saved item appears permanently stuck.
5. **F32 — Save permanently disables after a single failure.** One network blip breaks the core action until the sheet is reopened.

### Risks
- **Direction B is dark-first.** Light mode must be designed, not inverted, or the app ships one good theme and one bad one.
- **Ambient colour-from-artwork** needs a defined fallback for monochrome or missing thumbnails.
- **Removing all card chrome** raises the bar on spacing and typography — a chrome-free layout has nothing to hide behind. Budget for precision, not speed, in PR 04 and PR 06.
- **PR 03 is the critical path.** PRs 05–08 all depend on it; if the service layer is wrong, four PRs rework.
- **Reconciliation risk.** Sibling audits in this folder (`conversational-intelligence-ux.md`, `collections-system-design.md`, `sava-search-design.md`) cover overlapping surfaces and were not read during this audit. Reconcile before building, or two specifications will be implemented against each other.

### Unresolved decisions — need a human
- **Direction A vs B.** The recommendation is B, but A is the stronger brand. This is a positioning call: is Sava a visual memory or a personal publication? If the median save turns out to be long-form rather than short vertical video, A becomes correct.
- **Light mode's status under Direction B.** Full parity, or an explicitly secondary experience?
- **Whether Ask deserves a tab or only a persistent pill.** The spec gives it both; a four-tab bar plus a floating pill may be one affordance too many.

### Backend dependencies (not client-fixable)
- **No refresh token; 30-minute hard expiry (F35).** The client can soften the failure with a re-auth sheet, but the underlying session length is a product decision on the API side.
- **`POST /api/resolve`** (screenshot → canonical URL) does not exist. `ContentResolverService` is correctly disabled; keep its UI out of the app until the endpoint ships.

### Future-phase items (deliberately deferred, not rejected)
- **"Sava Digest"** — Direction A's editorial serif feed as a weekly edited read over what you saved. The best home for the serif brand idea.
- **iPad / `NavigationSplitView`** — Direction C's sidebar treatment. Out of scope for the iPhone redesign but the IA in 6.2 does not preclude it.
- **Long-press video preview loops** on library tiles (Direction B).
- **Drag-to-add collection curation** (Direction C).

---

## 10. Provenance

- Audit performed read-only against `feat/intelligence-foundation` at commit `05192fc` on 2026-08-18.
- No production file was modified. No code was implemented. Nothing was committed.
- Finding IDs F01–F38 are stable and safe to cite in commit messages and issue titles.
- A rendered version of this audit was published as an artifact during the session it was produced: `https://claude.ai/code/artifact/030e32c7-e6b8-41d1-b7ac-ae9898317832`. This Markdown file is the canonical record; the artifact may not be updated.
