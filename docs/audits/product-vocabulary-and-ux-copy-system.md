# Sava — Product Vocabulary & UX Copy System

**Status:** Specification / design deliverable. Not implemented.
**Created:** 2026-08-18
**Scope:** All user-facing language across the Sava iOS app, web client, backend-returned copy, notifications, and subscription surfaces.
**Author note:** Produced as a read-only design pass. No production code was modified when this document was written.

---

## 0. How to use this document

This is the single source of truth for **what Sava calls things and how Sava talks**. It is a
specification, not a record of what is currently shipped. Sections are split so a future agent
can tell them apart:

- **Part I — Grounding** — what was read to produce this, and what the codebase actually does today.
- **Part II — The system** — the vocabulary and copy spec itself (this is the recommendation set).
- **Part III — Current-state findings & migration** — strings that exist today and violate the spec.
- **Part IV — Blockers, risks, unresolved decisions, phasing, acceptance criteria.**

**Do not implement anything in Part II or III without checking Part IV first** — several items
depend on product decisions that were never made (pricing, tier limits) or on backend endpoints
that do not exist yet.

The hard constraint that generated this work: **the word "bookmark" must never appear in
user-facing copy**, even though it is the dominant term in the database schema, API paths, and
Swift type names. This document draws the line between *internal naming* (may stay `bookmark`)
and *user-facing naming* (must be `Save`).

---

# PART I — GROUNDING

## I.1 What was read

This spec is grounded in the real codebase, not invented from a brief. Files consulted:

| File | What it established |
|---|---|
| `api/routes_intelligence.py` | The full feature surface: processing status, search, summary, Ask This, Ask Sava, threads, Related, resurface, collections, suggestions, rebuild, ops/usage |
| `api/models.py` | `ProcessingState` (`queued`/`running`/`ready`/`partial`/`failed`), `CanonicalContent.media_kind` (`video`/`image`/`carousel`/`article`), `Collection.kind` (`manual`/`auto`), `stage_status` JSON |
| `api/pipeline/ingest.py` | Real pipeline stage names: `metadata`, `transcript`, `vision`, `understanding`; statuses `ok`/`failed`/`skipped`/`deferred` |
| `api/pipeline/understanding.py` | `CONTENT_TYPES` taxonomy (16 types) driving type-aware prompts and search examples |
| `api/services/collections.py` | `MIN_CLUSTER_SIZE=3`, `MIN_SAVES_FOR_AUTO=8`, `MAX_AUTO_COLLECTIONS=8`, `MATCH_THRESHOLD=0.62`; auto-collections are k-means over the user's own embeddings, named by a cheap model |
| `api/services/intelligence.py` | `worth_revisiting()` — deterministic, no model; peaks around 30 days old; signals are age, never-opened, and presence of a user note |
| `api/ai/router.py` | `describe_modes()` — the Ask mode picker copy, explicitly provider-neutral |
| `ios/Sava/Features/**` | ~150 existing user-facing strings, extracted and audited (see Part III) |

## I.2 Current-state facts about the product (as of 2026-08-18)

These are **findings**, not recommendations. They constrain the copy.

1. **Backend processing states are five:** `queued`, `running`, `ready`, `partial`, `failed`
   (`api/models.py:131-138`). `partial` explicitly means "usable, but some enrichment failed."
2. **The pipeline has four named stages** — `metadata`, `transcript`, `vision`, `understanding` —
   each recorded per-content in `stage_status` with `ok`/`failed`/`skipped`/`deferred`.
   `understanding` is `deferred` for long media (duration-gated).
3. **`media_kind` exists on `CanonicalContent`** (`video|image|carousel|article|unknown`). This
   makes media-aware processing verbs implementable today with no schema change.
4. **Auto-collections are real and user-specific.** `api/services/collections.py` is explicit
   that a fixed taxonomy is forbidden — a user who never saves cooking must never be handed a
   "Recipes" collection. Grouping is discovered from the user's own embeddings. Auto-collections
   require **≥8 saves** before any appear, cluster minimum **3**, cap **8** collections.
5. **Resurfacing is deterministic and model-free.** `worth_revisiting()` peaks around 30 days
   old, down-weights <7 days, decays after 60. It favours never-opened saves and saves with a note.
6. **Ask has two scopes**, persisted as `ChatThread.scope`: `"save"` (Ask This) and `"library"`
   (Ask Sava). Threads and citations are already modelled.
7. **Ask modes are provider-neutral by design.** `describe_modes()` carries a code comment:
   *"Never exposes a vendor or model name."* The copy system must preserve that guarantee.
8. **Search is hybrid retrieval** (vectors + keyword + structured filters) via
   `retrieval.search_library`, and the response carries a `semantic` boolean. Note: the older
   `GET /api/bookmarks?q=` path is SQL `ILIKE` only. The copy must not promise semantics the
   active endpoint cannot deliver — see Part IV risk R3.
9. **There is no subscription/billing code anywhere in the repo.** No Stripe, no StoreKit, no
   tier gating, no entitlement model. All subscription copy in Part II.11 is greenfield design
   against an unmade product decision.
10. **`docs/` did not exist** before this document was written. The memory index references
    `docs/BUILD_PLAN.md`; that file is not present in the working tree.

---

# PART II — THE SYSTEM (specification / recommendations)

## II.0 Voice rules

Six rules, in priority order. When two conflict, the higher one wins.

1. **Sava does the work; the user gets the result.** Say what happened, not what the system is
   doing. "Ready" beats "Processing complete."
2. **Short beats complete.** If a sentence can lose a clause, it loses it. Empty states get one
   line plus one action.
3. **Never name the machinery.** No "AI", "model", "embedding", "vector", "semantic",
   "pipeline", "queue", "sync". The user hired Sava, not a stack.
4. **Confident, never cute.** No exclamation marks, no emoji, no "Oops", no "Whoops", no
   "Let's get you started."
5. **Second person, present tense, active.** "You saved this a month ago." Not "This was saved
   by you."
6. **Sentence case everywhere** except product nouns (Library, Collection, Save, Ask Sava).
   Never Title Case Buttons.

**Banned from all user-facing surfaces:** bookmark, link (as a noun for a Save), item, content,
asset, entry, record, AI, model, semantic, processing (as a user-facing word), sync, error,
failed, oops, magic, smart, powered by, seamless, effortless, unlock (except in billing),
supercharge, curate.

---

## II.1 Core vocabulary

| Term | What it is | UI label | Never say |
|---|---|---|---|
| **Save** (noun) | One captured piece of content | "Save", "your Saves" | bookmark, item, link, post |
| **Save** (verb) | The act of capturing | "Save" | add, bookmark, clip, collect |
| **Library** | Everything you've saved | "Library" | feed, dashboard, home, memory, vault |
| **Collection** | A named group of Saves | "Collection" | folder, tag, playlist, board, list |
| **Search** | Finding inside your Library | "Search" | filter, query, lookup |
| **Summary** | What a Save is about, in Sava's words | "Summary" | AI summary, TL;DR, overview, recap |
| **Ask this** | Questions about one Save | "Ask this" | chat, chat with video, AI chat |
| **Ask Sava** | Questions across the whole Library | "Ask Sava" | assistant, copilot, AI search |
| **Related** | Other Saves that connect to this one | "Related" | similar, you might also like, recommended |
| **Worth revisiting** | Resurfaced older Saves | "Worth revisiting" | rediscover, memories, on this day, throwback |

**Internal vs. user-facing:** `bookmark` may remain in table names, API routes
(`/api/bookmarks/{id}/…`), Swift types (`Bookmark`), and variable names. It must not appear in
any string a user can read, including App Intent dialogs, notification bodies, and error text.

### The Save lifecycle

Backend states are `queued · running · ready · partial · failed` (`api/models.py:131`). They map
to four user-facing states — **never expose the raw values**.

| Backend state | Neutral label | On the Save itself | Meaning |
|---|---|---|---|
| `queued`, `running` | **In progress** | Media-aware verb (see II.7) | Sava is going through it |
| `ready` | **Ready** | *(no label — absence is the signal)* | Summary, Search, Ask, Related all live |
| `partial` | **Ready** | "Some parts didn't come through" | Usable, with gaps |
| `failed` | **Couldn't finish** | "Sava couldn't get through this one" | Saved, but unreadable |

**Rule: a Save is never lost.** Every failure message says the Save is safe *before* it says
anything went wrong. The word "failed" never appears; "Couldn't finish" does.

### Capture terms

| Concept | Product term | Notes |
|---|---|---|
| Action Button capture | **One press** | The hardware is always "the Action Button" — Apple's name, used exactly. The feature is "one press." |
| Screenshot fallback | **Screenshot capture** | Only surfaced when a link isn't available |
| Manual Save | **Add a link** | Button: "Add a link". Never "manual", never "paste URL" |
| Share sheet | **Share to Sava** | Matches the system affordance |

### Collection terms

| Concept | Product term | Notes |
|---|---|---|
| Manual collection (`kind="manual"`) | **Collection** | No qualifier. The default kind needs no adjective. |
| Auto-collection (`kind="auto"`) | **Found by Sava** | Badge on the collection. Section header: "Found in your Library". Never "auto", never "smart". |
| Collection suggestions | **Suggested Saves** | "Sava found 12 that fit." Actions: "Add all" / "Review" |
| Rebuild auto-collections (`POST /api/collections/rebuild`) | **Look again** | Profile action, not a gear icon |

**Never** say "auto-collection" out loud in the UI. The user sees a Collection with a "Found by
Sava" mark on it. Everything else reads identically to one they made.

### Ask modes

`describe_modes()` (`api/ai/router.py:271`) returns Auto / Fast / Advanced. Tighten the copy:

| id | Title | Subtitle |
|---|---|---|
| `auto` | Auto | Picks the right depth |
| `fast` | Fast | Quick answers |
| `advanced` | Advanced | Takes its time |

Never name a vendor or model. Never say "smarter" — say "takes its time."

---

## II.2 Button labels

**Primary actions**
- `Save`
- `Add a link`
- `Ask Sava`
- `Ask this`
- `Search`
- `New collection`
- `Add all` · `Add 12` (when the count is known)
- `Try again`
- `Open in YouTube` / `Open in TikTok` (platform name, never "Open original")

**Secondary**
- `Summary`
- `Transcript`
- `Related`
- `Comments`
- `Add to collection`
- `Review suggestions`
- `Look again`
- `Read full transcript`
- `Remove` (never "Delete" for a Save — Delete is reserved for the account)

**Destructive**
- `Remove from Library` — confirm: `Remove this Save? It won't be in your Library or in Search.` → `Remove` / `Keep`
- `Delete account` — confirm: `Delete your account? Your Library goes with it. This can't be undone.` → `Delete` / `Cancel`

**Auth**
- `Sign in` · `Create account` · `Sign out`

**Never as a button:** "Submit", "OK", "Got it", "Let's go", "Continue" (unless in a numbered
flow), "Learn more" (say what they'll learn).

---

## II.3 Empty states

Pattern: **one line of fact, one line of direction, one action.** Never a joke, never an
illustration caption.

**Library, no Saves yet**
> **Nothing saved yet**
> Press the Action Button on anything worth keeping.
> `[Set up one press]` `[Add a link]`

**Library, filtered to a platform with no results**
> **Nothing from TikTok yet**
> `[Show everything]`

**Search, before typing**
*(No empty state — show Worth revisiting instead. See II.9 for the placeholder.)*

**Search, no results**
> **No matches for "burnt butter"**
> Sava searches what was said and shown, not just titles. Try fewer words.

**Collections, none yet**
> **No collections yet**
> Make one, and Sava fills it with Saves that fit.
> `[New collection]`

**Collection, empty after creation**
> **Nothing in here yet**
> Sava is looking for Saves that belong.
> *(auto-replaces with suggestions when they arrive)*

**Collection, empty and nothing matched**
> **Nothing fits yet**
> Save something about this and it lands here.

**Found-by-Sava collections, not enough Saves** (`MIN_SAVES_FOR_AUTO = 8`)
> **Collections appear as you save**
> Sava starts grouping around eight Saves. You have three.

**Related, none**
> **Nothing related yet**
> This is the only one of its kind in your Library.

**Related, Save not processed** (API returns `reason: "not_processed"`)
> **Related comes after Sava reads this**

**Transcript, none available**
> **No transcript for this one**
> Sava read what was on screen instead.

*(If `vision` also failed or was skipped: **No transcript for this one.** — full stop, no second line.)*

**Comments, none**
> **No comments saved**

**Ask Sava, no history**
> **Ask about anything you've saved**
> *(followed by suggested prompts, II.10)*

**Worth revisiting, nothing old enough**
> *(Hide the section entirely. Never show an empty resurfacing shelf.)*

---

## II.4 Errors

Pattern: **what happened → what's still true → what to do.** Never apologize, never blame the
user, never show a code.

**Network**
> **Can't reach Sava**
> Your Library is here. New Saves will go through when you're back online.
> `[Try again]`

**Save failed — network**
> **Saved to this device**
> Sava will finish when you're back online.

**Save failed — unsupported link**
> **Sava can't read that link**
> Works with YouTube, TikTok, Instagram, X, Reddit and a few more.

**Save failed — already saved** (HTTP 409, dedup path is verified working)
> **Already in your Library**
> `[Open it]`

**Save failed — no link found** (Action Button, host app exposes no URL)
> **No link found**
> Share or copy the link first, then press again.
> `[Use a screenshot instead]`

**Processing — couldn't finish** (`processing_state = failed`)
> **Sava couldn't get through this one**
> It's still in your Library, and you can open the original.
> `[Try again]` `[Open in TikTok]`

**Processing — partial** (`processing_state = partial`)
> **Some parts didn't come through**
> Summary and Search work. The transcript is missing.

*(Second line is generated from `stage_status` — name the specific missing stage, never a generic phrase.)*

**Search failed**
> **Search didn't come back**
> `[Try again]`

**Ask — no answer found in Library**
> **Nothing in your Library covers that**
> Sava only answers from what you've saved.

**Ask — question empty** (backend returns 422)
> *(Disable the send button. Never show a message for an empty field.)*

**Ask — model unavailable** (`get_router().is_available() == false`)
> **Ask is unavailable right now**
> Search and your Library are unaffected.

**Ask — over the monthly limit** (see II.11)
> **You've used this month's questions**
> Resets on the 1st.
> `[See Pro]`

**Auth — unknown email** (404)
> **No account with that email**
> `[Create one]`

**Auth — wrong password** (401)
> **That password doesn't match**

**Auth — invalid email** (400/422)
> **Enter a valid email address**

**Auth — password too short**
> **Passwords need at least 6 characters**

**Auth — session expired** (30-min JWT TTL, no refresh token)
> **Sign in again**
> Sava signs you out after a while for safety.

**Unknown / 500**
> **Something didn't work**
> `[Try again]`

**Never write:** "Oops!", "Uh oh", "An error occurred", "Please try again later", "Invalid
input", "Request failed with status 500", "Something went wrong. Please try again."

---

## II.5 Onboarding

Four screens. No carousel dots as the only navigation; every screen has a skippable action.

**1 — What Sava is**
> **Everything you save, finally worth saving**
> Sava watches, reads and remembers what you keep — so you can find it in your own words later.
> `[Get started]`

**2 — One press**
> **One press, from anywhere**
> Set the Action Button to Sava. See something worth keeping? Press once. Sava takes it from there.
> `[Set it up]` · `Not now`

*Setup detail (expanded):*
> **Settings → Action Button → Shortcut → Save to Sava**
> `[Open Settings]`

**3 — What happens after**
> **Sava does the watching**
> Every Save gets read, watched and understood in the background. Ask about it later like you'd
> ask a friend who saw it.

**4 — First Save**
> **Save something**
> Anything you've been meaning to come back to.
> `[Add a link]` · `I'll do it later`

**First-Save confirmation (in-app, once)**
> **That's one.**
> Sava is going through it now. It'll be searchable in a minute.

**Empty-Library nudge (day 3, in-app, once)**
> **Sava gets better around ten Saves**
> That's when Collections start forming.

*(Grounded in `MIN_SAVES_FOR_AUTO = 8` — "around ten" is deliberately soft so the promise is never early.)*

**Account creation**
> **Create your account**
> Your Library lives here.

**Sign in**
> **Sign in**
> *(No second line. Nothing to explain.)*

---

## II.6 Capture education

Shown in Profile → How capture works. Grounded in real per-platform behavior.

> **YouTube · X · Reddit**
> Sava grabs the link directly. Instant.

> **TikTok · Instagram**
> Same — link first. If the app doesn't hand one over, Sava reads a screenshot instead.

> **Anything else**
> Share to Sava, or add the link yourself.

---

## II.7 Processing copy

The word "processing" never appears. Sava uses a **media-aware verb** while it works, and says
nothing at all when it's done.

**Active state, on the Save** — driven by `CanonicalContent.media_kind`

| `media_kind` | Line |
|---|---|
| `video` | **Watching** |
| `image`, `carousel` | **Looking** |
| `article` | **Reading** |
| `unknown` | **Working through it** |

Neutral label in lists, filters and counts: **In progress**.

**Progressive detail** (optional second line, detail screen only, driven by `stage_status`)

| Stage | Line |
|---|---|
| `metadata` | Getting the details |
| `transcript` | Listening |
| `vision` | Looking at what's on screen |
| `understanding` | Putting it together |
| `understanding` = `deferred` (long media) | This one's long — a few minutes |

**Completion**
- The label disappears. No "Ready!" toast, no confetti, no checkmark animation.
- If the user is *looking at* the Save when it finishes: the Summary fades in. Nothing else.
- The existing string **"AI ready"** must be removed entirely — see Part III.

**On the detail screen while in progress**
> **Sava is watching this**
> Summary, Ask and Related open up when it's done. The video's ready now.
> `[Open in TikTok]`

**Long queue / many at once**
> **8 in progress**
> *(Never a progress bar across multiple Saves. Never a percentage.)*

**Re-run** (`POST /api/bookmarks/{id}/reprocess`)
- Button: `Try again` (not "Reprocess", not "Retry")
- Confirmation after tap: **Back in the queue.**

---

## II.8 Notifications

Rules: never more than one Sava notification per day; never notify on a single Save completing;
never use the app name as the first word.

**First Save is ready** (once, ever)
> **Your first Save is ready**
> Ask it anything.

**Batch ready** (only if ≥3 finished while the app was closed)
> **5 Saves are ready**
> Everything you kept today is searchable now.

**Worth revisiting** (weekly, max — driven by `GET /api/resurface`)
> **You saved this a month ago**
> "How to actually braise short ribs" — still worth a watch?

*(Alternate framings, rotate: "Worth another look" · "You kept this in June" · "This one's still sitting there". The "a month ago" phrasing is safe because `worth_revisiting()` peaks at ~30 days; the copy must be generated from the real age, not hardcoded.)*

**New collection found**
> **Sava found a group in your Library**
> 14 Saves about Tokyo. Want it as a collection?

**Couldn't finish** (only if the user saved it in the last hour and is likely waiting)
> **Sava couldn't get through one Save**
> It's in your Library — the original still opens.

**Never notify for:** a single Save completing, a Save being added, a summary being written,
marketing, streaks, "we miss you", usage stats, tips.

---

## II.9 Search placeholders

Placeholder rotates on each appearance. Every example is written the way a person actually
remembers something — vague, sensory, half-recalled. **That is the product claim, made in the
placeholder.**

**Primary placeholder**
> `Search your Library`

**Rotating examples** (ghost text under the field, or tappable chips before typing)
- `that pasta recipe with the burnt butter`
- `the ramen place in the East Village`
- `the guy explaining compound interest`
- `leg day, no equipment`
- `the jacket from that haul video`
- `Tokyo, where to stay`
- `the one about rate limiting`
- `that hotel with the outdoor bath`
- `the skincare one with the pink bottle`
- `something about sourdough starter`

*(Each example maps to a real `CONTENT_TYPES` value: recipe, restaurant, finance, fitness, fashion, travel, coding, travel, beauty, recipe.)*

**In-transcript search**
> `Search this transcript`

**Collection search**
> `Search in Tokyo`

**Never use:** "Search…", "Type to search", "Find anything", "Search bookmarks", "Search your
memory" ("memory" is a competing metaphor to Library — drop it).

---

## II.10 Ask — suggested prompts

Two distinct sets. Never mix them.

### Ask Sava (whole Library, `scope="library"`, `POST /api/ask`)

Shown as chips before the first message. Pick 4, weighted toward the user's actual content types.

**Always available**
- `What have I been saving lately?`
- `What did I save this week?`
- `What do I keep coming back to?`

**Type-aware** (only if the user has ≥3 Saves of that type)
- `recipe` → `What should I cook this week?`
- `restaurant` → `Where should I eat in Brooklyn?`
- `travel` → `Plan a weekend from what I saved about Tokyo`
- `product` → `What did people say about that camera?`
- `fitness` → `Build a week from my saved workouts`
- `coding` / `educational` → `Summarize what I saved about databases`
- `shopping` / `fashion` → `What was in that haul?`

**After a search with no results**
- `Ask Sava instead`

### Ask this (single Save, `scope="save"`, `POST /api/bookmarks/{id}/ask`)

Type-aware, from `CONTENT_TYPES` in `api/pipeline/understanding.py:24`.

| Content type | Prompts |
|---|---|
| `recipe` | `What are the ingredients?` · `How long does it take?` · `Can I make this without a stand mixer?` |
| `restaurant` | `What should I order?` · `Where is it?` |
| `travel` | `List every place mentioned` · `How many days is this?` |
| `product` | `What's the verdict?` · `What are the downsides?` · `What did it cost?` |
| `tutorial`, `coding` | `Give me the steps` · `What do I need first?` |
| `educational`, `podcast`, `news` | `Key takeaways` · `What's the argument?` |
| `fitness` | `What's the routine?` · `How many sets?` |
| `fashion`, `beauty`, `shopping` | `What products are in this?` · `What are the brands?` |
| fallback (`entertainment`, `finance`, `other`) | `Summarize this` · `Key takeaways` · `What's the main point?` |

**Input placeholder:** `Ask about this Save` (single) · `Ask about your Library` (Ask Sava)

**Answer footer:** `From 3 Saves` → tappable, expands to the cited Saves (the API already returns
`citations`/`sources`). Never "Sources", never "Citations", never a disclaimer about accuracy.

**Streaming / thinking state:** `Looking through your Library` (Ask Sava) · `Reading this back`
(Ask this). Never "Thinking…", never "Generating".

---

## II.11 Subscription copy

> **UNRESOLVED DECISION.** No billing code exists in the repo. Price points, limit values, and
> the tier split below are a *proposal*, not a decision. See Part IV, D1–D3.

Two tiers. The free tier must feel complete, not crippled — the limit is on *depth and volume*,
never on access to your own Library.

**Names:** `Sava` (free) and **`Sava Pro`**. Never "Premium", never "Plus", never "Unlimited" as
a tier name.

### Proposed tier table

| | **Sava** | **Sava Pro** |
|---|---|---|
| Saves | Unlimited | Unlimited |
| Search | Everything you've saved | Everything you've saved |
| Fully read by Sava | 30 Saves a month | Everything |
| Ask | 20 questions a month | Unlimited |
| Advanced mode | — | Yes |
| Collections | Made by you | Made by you, and found by Sava |
| Worth revisiting | — | Yes |

**Principle:** saving and searching are never limited. What Pro buys is *how deeply Sava reads*
and *how much you can ask*. A free user who stops paying keeps every Save and everything Sava
already understood. This principle is the non-negotiable part; the specific numbers are not.

### Paywall

**Header**
> **Sava Pro**
> Sava reads everything you save, and answers as much as you ask.

**Bullets** (three, no icons, no checkmarks)
> Every Save read in full — transcript, screen and all
> Ask as much as you want, across your whole Library
> Collections Sava finds on its own

**Price**
> `$8/month` · `$72/year — two months free`

**CTA**
> `[Start Pro]` · `Restore purchase`

**Footer**
> Cancel anytime. Your Library is yours either way.

### Contextual prompts

**Hit the monthly read limit**
> **You've hit 30 fully-read Saves this month**
> New Saves still land in your Library and stay searchable. Pro reads all of them.
> `[See Pro]` · `Not now`

**Hit the Ask limit**
> **That's this month's 20 questions**
> Resets on the 1st.
> `[See Pro]` · `Not now`

**Tapped Advanced mode**
> **Advanced takes its time**
> Longer answers, harder questions. Part of Pro.
> `[See Pro]`

**Tapped a found collection**
> **Sava found this group on its own**
> Collections like this come with Pro.
> `[See Pro]`

### Lifecycle

**Purchased**
> **You're on Pro.**
> Sava is going back through everything it hadn't fully read.

**Renewal upcoming** *(only if they asked to be told)*
> **Pro renews on the 14th**

**Cancelled**
> **Pro ends on the 14th**
> Everything Sava has already read stays read.

**Lapsed**
> **You're back on the free plan**
> Your Library is untouched. Sava reads 30 new Saves a month.

**Never write:** "Upgrade now!", "Unlock the full power of Sava", "You're missing out",
"Limited time", "Most popular", a countdown timer, or a strikethrough price.

---

## II.12 Micro-rules

- **Counts:** spell out under ten in prose (`three Saves`), numerals in labels and badges (`3`).
  Always "Saves", never "items".
- **Dates:** relative under a week (`2 days ago`), absolute after (`June 14`). Never "1 day ago"
  — use `Yesterday`.
- **Truncation:** titles at two lines, summaries at three, then `More`. Never an ellipsis-only
  affordance.
- **Ellipses:** only in loading verbs, and only the single character `…`. Never `...`.
- **Emoji:** none. Includes removing the 🔖 from the Action Button confirmation.
- **Punctuation:** headlines take no period. Two-sentence body copy takes periods. One-line body
  copy takes a period only if it's a full sentence.
- **The word "Sava":** the subject of sentences about what the product did (`Sava couldn't get
  through this one`), never the object of the user's action. `Save to Sava` is the one exception
  — it is the system-level App Intent name and must match what iOS shows.
- **Platform names:** always exact — `YouTube`, `TikTok`, `Instagram`, `X`, `Reddit`,
  `LinkedIn`, `Pinterest`, `Facebook`, `Snapchat`.

---

# PART III — CURRENT-STATE FINDINGS & MIGRATION

Strings that exist in the build today and violate the system above. These are **findings with
prescribed fixes**, not yet applied. Severity: **P0** breaks the vocabulary contract or exposes
machinery; **P1** is tone/clarity; **P2** is polish.

| Sev | File | Current string | Replace with |
|---|---|---|---|
| P0 | `ios/Sava/Features/Detail/Intelligence.swift` | `"AI ready"` | *(remove entirely — completion is silent; also names the machinery)* |
| P0 | `ios/Sava/Features/Search/SearchView.swift` | `"Search your memory"` | `Search your Library` |
| P0 | `ios/Sava/Features/Capture/SaveToSavaIntent.swift` | `"Saved \(what) 🔖"` | `Saved \(what)` |
| P0 | `ios/Sava/Features/Detail/AskSavaSection.swift` | `"Ask Sava. Conversational answers are coming soon."` / `"Conversational answers are coming soon"` | Ask is live server-side (`POST /api/ask`, `POST /api/bookmarks/{id}/ask`). Replace with the Ask empty state (II.3) + suggested prompts (II.10) |
| P0 | anywhere `bookmark` reaches a user | `"bookmark"` / `"bookmarks"` in visible text | `Save` / `Saves` |
| P1 | `ios/Sava/Features/Detail/*` | `"Analyzing"` / `"Still analyzing"` / `"In progress"` on the Save | Media-aware verb: `Watching` / `Looking` / `Reading` (II.7) |
| P1 | `ios/Sava/Features/Search/SearchView.swift` | `"Nothing in your library matches “\(query)”. Try different words."` | `No matches for "…"` + `Sava searches what was said and shown, not just titles. Try fewer words.` |
| P1 | `ios/Sava/Features/Library/LibraryViewModel.swift` | `"Couldn't load your library."` | `Can't reach Sava` + `Your Library is here. New Saves will go through when you're back online.` |
| P1 | `ios/Sava/Features/Save/QuickSaveViewModel.swift` | `"Couldn't save that link."` / `"Couldn't save that."` | Split by cause: unsupported link / already saved (409) / offline (II.4) |
| P1 | app-wide | `"Something went wrong. Please try again."` | `Something didn't work` + `[Try again]` |
| P1 | `api/ai/router.py:274` | `"Sava Auto"` / `"Best model automatically"` | `Auto` / `Picks the right depth` |
| P2 | `ios/Sava/Features/Library/*` | `"Delete"` on a Save | `Remove` (Delete is reserved for the account) |
| P2 | `ios/Sava/Features/Detail/*` | `"Couldn't load comments."` / `"Couldn't load the transcript."` | `No comments saved` / `No transcript for this one` + the on-screen fallback line |
| P2 | `ios/Sava/Features/Auth/*` | `"That password doesn't look right."` | `That password doesn't match` |
| P2 | `ios/Sava/Features/Search/*` | `"Search failed. Try again."` / `"No matches"` | `Search didn't come back` + `[Try again]` |
| P2 | `ios/Sava/Features/Detail/*` | `"Auto-generated"` (transcript provenance) | *(drop the label; if provenance must show, use `From captions` / `From the audio`)* |

**Strings that are already correct and should be preserved as-is:**
`Library`, `Ask Sava`, `Save to Sava`, `Paste a link` (→ becomes `Add a link`), `One-press save`
(→ `One press`), `Open in \(platform)`, `Read full transcript`, `Can't reach Sava`,
`No link found. Share or copy the video's link, then press again.` (→ minor trim per II.4),
`Open Settings → Action Button`.

---

# PART IV — BLOCKERS, RISKS, DECISIONS, PHASING, ACCEPTANCE

## IV.1 Launch blockers

| ID | Blocker | Why it blocks |
|---|---|---|
| **B1** | `"Conversational answers are coming soon"` is shipped in `AskSavaSection.swift` while `POST /api/ask` and `POST /api/bookmarks/{id}/ask` exist server-side | The app tells users a shipped feature doesn't exist. Either wire the client or remove the promise — do not ship both states. |
| **B2** | No entitlement/billing layer exists | Every string in II.11 is unshippable until StoreKit + a server-side entitlement check exist. Do not ship the paywall copy against a stub. |
| **B3** | The word "bookmark" is user-visible in at least the App Intent surface and several view models | This is the one hard constraint of the vocabulary system. Must be zero before launch. |
| **B4** | Ask-limit and read-limit error copy (II.4, II.11) references limits that are not enforced anywhere | Shipping the copy without the meter produces messages that can never fire, or worse, fire wrongly. |

## IV.2 Risks

| ID | Risk | Mitigation |
|---|---|---|
| **R1** | The media-aware processing verbs (Watching / Looking / Reading) require `media_kind` to be populated *before* processing finishes. If it is only set by the `metadata` stage, the first seconds of every Save show the `unknown` fallback. | Either infer `media_kind` from the platform at save time, or accept `Working through it` as the opening frame and swap once metadata lands. Do not flicker between verbs. |
| **R2** | The "partial" second line (`The transcript is missing.`) must be generated from `stage_status`. A generic fallback would violate the "name the specific gap" rule. | Build the string from the stage map; if no stage is identifiable, show only `Some parts didn't come through` with no second line. |
| **R3** | Search copy promises "what was said and shown, not just titles". `GET /api/search` delivers that; the legacy `GET /api/bookmarks?q=` is SQL `ILIKE` only. If any client still calls the legacy path, the copy is a lie. | Confirm the client uses `/api/search` before shipping the no-results copy. The response's `semantic` boolean can gate the second line. |
| **R4** | Auto-collection naming is model-generated. A badly named cluster surfaces a "Found by Sava" collection the user finds absurd, and the badge makes Sava own it. | Needs a dismiss/rename affordance before auto-collections are promoted in onboarding or notifications. Copy for that affordance is not yet written. |
| **R5** | Notification frequency caps (one/day, weekly resurface) are copy-level rules with no scheduler behind them. | The caps are part of the spec, not decoration. They need enforcement wherever notifications are scheduled. |
| **R6** | 30-minute JWT TTL with no refresh token means `Sign in again` will fire often. | Copy is written to be calm about it, but the underlying frequency is a product problem, not a copy problem. |

## IV.3 Unresolved decisions (need a human)

| ID | Decision | Options considered |
|---|---|---|
| **D1** | Paid tier name | `Sava Pro` (proposed) vs. Plus / Premium / Unlimited. Pro chosen for being plain and confident; not final. |
| **D2** | Price points | `$8/mo` / `$72/yr` are placeholders chosen to read as premium-but-not-luxury. Must be set against real per-Save AI cost (see `GET /api/ops/usage` telemetry). |
| **D3** | Free-tier limits | 30 fully-read Saves/mo, 20 questions/mo. Chosen so the free tier is usable, not crippled. The *principle* (never limit saving or searching) should survive even if the numbers change. |
| **D4** | Whether `Worth revisiting` is Pro-gated | Proposed as Pro. It is deterministic and cheap (`worth_revisiting()` uses no model), so gating it is a pure packaging choice, not a cost one. Weak justification — reconsider. |
| **D5** | Whether the processing verb is media-aware at all | The simpler alternative is a single neutral `In progress` everywhere. Media-aware is more premium and is implementable, but costs a mapping and risks R1. |

## IV.4 Phasing

**Phase 1 — Vocabulary correctness (blocking)**
Remove every user-visible `bookmark`. Resolve B1. Apply all P0 rows in Part III. Ship the
Save / Library / Collection / Search nouns everywhere.

**Phase 2 — State & error copy**
Processing states (II.7), all error copy (II.4), all empty states (II.3). Requires `stage_status`
to be surfaced to the client.

**Phase 3 — Intelligence surfaces**
Ask This / Ask Sava prompts and empty states (II.10), Related (II.3), search placeholders (II.9).

**Phase 4 — Growth surfaces**
Onboarding (II.5, II.6), notifications (II.8). Requires the notification scheduler and the
frequency caps in R5.

**Phase 5 — Monetization**
All of II.11. Blocked on B2, D1, D2, D3.

## IV.5 Acceptance criteria

A surface conforms to this system when **all** of the following hold:

1. Zero occurrences of `bookmark`, `AI`, `model`, `semantic`, `processing`, `error`, `failed`,
   `oops`, or `sync` in any user-readable string. (Greppable check.)
2. Zero emoji in user-readable strings.
3. Zero exclamation marks outside of literal user-authored content.
4. Every error message names what is still true before what went wrong, and offers exactly one
   primary action.
5. Every empty state is ≤2 lines plus ≤2 actions, and no empty state is a joke.
6. No raw backend enum value (`queued`, `running`, `partial`, `failed`, `manual`, `auto`) appears
   in any label.
7. Every button label appears in II.2, or is justified as a new addition to it.
8. Completion of processing produces no toast, no sound, and no animation beyond the Summary
   appearing.
9. All headline text is sentence case; product nouns (Library, Collection, Save, Ask Sava) are
   the only capitalized exceptions.
10. Ask mode copy names no vendor or model, preserving the guarantee in `api/ai/router.py`.

---

## Appendix — Codebase constants this copy depends on

Change any of these and the corresponding copy must be revisited.

| Constant | Value | File | Copy that depends on it |
|---|---|---|---|
| `MIN_SAVES_FOR_AUTO` | 8 | `api/services/collections.py` | "Sava gets better around ten Saves"; "Sava starts grouping around eight Saves" |
| `MIN_CLUSTER_SIZE` | 3 | `api/services/collections.py` | Suggested-Saves counts |
| `MAX_AUTO_COLLECTIONS` | 8 | `api/services/collections.py` | "Found in your Library" section sizing |
| `MATCH_THRESHOLD` | 0.62 | `api/services/collections.py` | Confidence of "Sava found 12 that fit" |
| resurface age peak | ~30 days | `api/services/intelligence.py` (`worth_revisiting`) | "You saved this a month ago" |
| `CONTENT_TYPES` | 16 values | `api/pipeline/understanding.py` | Type-aware Ask prompts; search placeholder examples |
| pipeline stages | `metadata`, `transcript`, `vision`, `understanding` | `api/pipeline/ingest.py` | Progressive processing detail lines |
| `ProcessingState` | `queued`/`running`/`ready`/`partial`/`failed` | `api/models.py:131` | The whole Save lifecycle table |
| `media_kind` | `video`/`image`/`carousel`/`article`/`unknown` | `api/models.py` | Media-aware processing verbs |
| JWT TTL | 30 min, no refresh | `api/auth.py` | "Sign in again" frequency |
