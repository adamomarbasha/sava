# Sava economics — pipeline, plans, and the numbers behind them

## The short version

Sava used to download a 7.4 MB video to understand a TikTok, **every time**.
It now reads captions, the creator's description and the cover image it had
already fetched, and downloads the video only when there is evidence it needs to.

| Platform | Old cost/video | New cost/video | Reduction |
|---|---|---|---|
| TikTok | $0.0310 | **$0.0068** | **78%** |
| Instagram / Reels | $0.0145 | **$0.0052** | **64%** |
| YouTube (incl. Shorts) | $0.0097 | **$0.0026** | **73%** |

That paid for the plans:

| | Free | Sava Pro — $9.99/mo, $79.99/yr |
|---|---|---|
| Saves | unlimited | unlimited |
| **Videos understood** | **~120 / month** | **~460 / month** |
| Ask messages | 150 | 1,500 |
| Concurrent jobs | 1 | 3 |
| Priority processing | — | yes |
| Deep video analysis | — | yes, on request |

~460 is a *mixed* short-form library. A YouTube-leaning one goes roughly twice
as far (~920); a TikTok-only one somewhat less (~360). The allowance stretches
automatically because it is denominated in work, not in items.

---

# Phase 1 — what the old pipeline actually did

`api/pipeline/ingest.py`, as it stood:

```
save URL
  → L1 metadata            (yt-dlp extract_info, no media)
  → L2 transcript          ← decided whether to download video
  → L3 classify            ← computed visual_dependency
  → L4 vision
  → L5 understanding + embeddings
```

**The bug was the ordering.** At L2 the code called `wants_vision()` to decide
whether to fetch audio or the whole video. That function reads
`visual_dependency` — which L3 had not computed yet, so it was `None` and
defaulted to `0.5` — and returns `True` whenever there is no transcript. TikTok
had `try_native_captions=False`, so at that moment there was never a transcript.

Every TikTok therefore took the most expensive path available, before anything
in the system had an opinion about whether it should.

The telemetry agrees: `acquire.video` ran on **20 of 22** TikTok items.

### Measured cost table (per processed item, `platform.*` double-counts excluded)

| Operation | n | Avg | Notes |
|---|---|---|---|
| `acquire.video` (TikTok) | 20 | **7.39 MB → $0.0216** | max 26.3 MB |
| `acquire.video` (Instagram) | 3 | 5.12 MB → $0.0150 | |
| `acquire.metadata` | 27 | 0.001–0.14 MB → ~$0.0001 | |
| `vision` | 18 | 6,237 in / 418 out → $0.0029 | 5.5 frames @640px |
| `understanding` | 25 | 1,189 in / 287 out → $0.0011 | |
| `classify` | 31 | 345 in / 36 out → $0.0002 | |
| `embedding.chunks` + `.document` | 63 | → $0.0002 | |
| `asr` | 15 | → $0.0002 | local whisper |
| Ask (blended) | 68 | → $0.00095 | |

**Acquisition was 83% of the cost of understanding a TikTok.** Inference was
noise by comparison. This is why the answer was never "charge more units".

### Where duration goes wrong

| Platform | Band | n | Mean |
|---|---|---|---|
| TikTok | ≤3 min | 14 | **$0.0310** |
| YouTube | 10–30 min | 3 | $0.0097 |
| YouTube | ≤3 min | 3 | **$0.0015** |

A TikTok and a YouTube Short of the same length differ **20×**. A 30-minute
YouTube video is **3× cheaper** than a 3-minute TikTok. Cost tracks *what the
pipeline had to do*, and on Sava's core platform duration correlates backwards.

---

# Phase 2 — the cheap-first architecture

`api/pipeline/route.py` is new. It decides, **before anything is fetched**,
which route an item needs. Classification moved ahead of acquisition; it costs
$0.0002 and buys the whole decision.

| Route | What runs | Bytes | Cost | Units |
|---|---|---|---|---|
| `cached` | another user already had it understood | 0 | $0 | **0** |
| `metadata` | identity, title, creator, cover | ~0 | $0.0001 | **0** |
| `text` | captions / subtitles / description → understanding | 0 | $0.0017 | **1** |
| `cover` | `text` + one vision call on the **already-stored** cover | **0** | $0.0020 | **1** |
| `audio` | audio-only download + ASR | ~1.6 MB | $0.0066 | **3** |
| `light_vision` | 360p video + 4 sparse deduplicated frames | ~4.4 MB | $0.0157 | **8** |
| `deep_vision` | 8 frames — **only on explicit request** | ~4.4 MB | $0.0240 | **12** |

**The `cover` route is the important new idea.** Sava already fetches and mirrors
each item's cover image so the library grid can draw it. Reading that image costs
one ~258-token vision call and **zero additional bandwidth** — and short-form
creators put the hook on frame one as rendered text. A caption saying "wait for
it 😭" plus a cover saying "3 INGREDIENT PASTA" is the whole item. The old
pipeline could only reach that text by downloading 7 MB of video.

### The decision

```
force_deep                        → deep_vision
image / carousel                  → read stored slides (no fetch)
captions or caption ≥120 chars
    and visual_dependency < 0.6   → text (+ free cover read)
visual_dependency ≥ 0.8           → light_vision
visual_dependency ≥ 0.6           → cover first, escalate only if it was thin
no usable text                    → audio
```

Then **one** evidence-based escalation, after the cheap route has actually run:
if the transcript and the cover together produced under 200 characters *and* the
item is visual, pay for frames. Otherwise stop. Stopping is the common case, and
it is what keeps the average near $0.002 rather than near $0.016.

Two ordering details that matter:

- The default for unknown `visual_dependency` is **0.4**, below the threshold.
  The old code defaulted to 0.5 *and* escalated when there was no transcript, so
  an unclassified item always escalated — it escalated on ignorance.
- `deep_vision` is unreachable from any combination of signals. It requires an
  explicit request, and that request is gated on Pro. A test asserts this across
  the whole signal space.

### Other cost reductions

- **Frames 640px → 384px.** Gemini prices images per tile. A 9:16 frame at 640px
  is 640×1138 and measured ~1,134 input tokens; at 384px it is 384×683, inside
  one tile, so 258. Vision input drops ~4×. The frames are read for on-screen
  text and gross composition; 384px is plenty.
- **Download 480p → 360p.** The file exists only to cut 384px stills out of.
  Downloading 480p to produce 384px stills pays for pixels discarded before
  anything looks at them.
- **Per-item caption discovery.** The metadata response already lists
  `subtitles` and `automatic_captions`. The old code used a per-platform boolean
  (`try_native_captions`) and never looked at the item. TikTok and Instagram do
  expose caption tracks on some posts; consulting the list costs nothing.
- **Frame budget 8 → 4** on the light route.

---

# Phases 4–7 — reuse, caching, storage, models

**Deduplication** was already sound and is unchanged: `content_key` is
`platform:id`, tracking parameters are normalised away, short links (`vm.`,
`vt.`, `/t/`) are upgraded to the real id after metadata and **merged** into any
row that already owns it. One `content.process` job exists per canonical item
regardless of how many people saved it, keyed by idempotency key.

Cross-user reuse is safe by construction because of the two-layer model: derived
intelligence (transcript, frames, understanding, embeddings) hangs off
`canonical_content`, while notes, collections, chat threads and library
membership hang off `bookmarks`. Nothing user-owned is shared. The second person
to save a TikTok pays **0 units**, and so does anyone who saves it while the
first person's job is still running.

**Caching** — verified, not assumed: transcripts, frames, understanding, chunks,
document vectors, thumbnails and carousel slides are all persisted and re-read.
`process_content` returns early when the item is already `ready` at the current
pipeline version. The cover reading is stored as a `ContentFrame` at ts=0, so a
re-run costs no second vision call.

**Ask never reprocesses.** Retrieval and both Ask paths read the database only.
There is a test that runs the pipeline, records every acquisition call, then
answers three times and asserts the call log is unchanged.

**Storage** was already correct: raw video and audio are written to a temp dir
and removed in a `finally` block, on the failure path too. R2 holds only
thumbnails, carousel slides and collection covers — images, not media.

**Models**: routine work (classification, extraction, summaries, vision, OCR)
already runs on the cheap tier; only Ask escalates, and only on a deterministic
question-shape heuristic. The cover call uses `max_output_tokens=768` rather
than the 4096 sized for eight frames, which stops a reasoning model spending the
difference on hidden thinking tokens.

---

# Phase 8 — the economics

Assumptions, stated because they are the weakest part of the model:

- Route distribution per platform is **estimated**, not measured — there is no
  corpus yet. `GET /api/ops/routes` records the real distribution per item and
  is how these get replaced.
  - YouTube: 90% cover/text, 8% audio, 2% frames
  - Instagram: 60% / 25% / 15%
  - TikTok: 35% / 45% / 20%
- **Dedup benefit modelled at zero.** The current ratio is 1.015 (133 saves,
  131 unique). It only improves, so real margins should beat these.
- Apple's commission at **15%** (Small Business Program). At 30% every figure
  below worsens by ~18%; enrol before launch.
- Ask volume modelled at half the video count.
- Audio-download size is **inferred**, not measured — YouTube blocks unproxied
  requests from this machine. The codebase's own conservative figure (~4.5× less
  than video) is used; the real ratio is likely better. `acquire.audio`
  telemetry records actual bytes in production.

## Pro — $9.99/month, $8.49 net

| Mix | 200 | 300 | 500 | 750 | 1000 videos |
|---|---|---|---|---|---|
| **A** 40% TikTok / 40% Reels / 20% YT | $1.16 (86%) | $1.74 (80%) | $2.91 (66%) | $4.36 (49%) | $5.81 (32%) |
| **B** 70% TikTok+Reels / 30% YT | $1.10 (87%) | $1.64 (81%) | $2.74 (68%) | $4.11 (52%) | $5.47 (36%) |
| **C** 50% YT Shorts / 25% TT / 25% IG | $0.96 (89%) | $1.44 (83%) | $2.40 (72%) | $3.60 (58%) | $4.80 (44%) |
| **D** heavy YouTube long-form | $0.69 (92%) | $1.04 (88%) | $1.73 (80%) | $2.59 (70%) | $3.45 (59%) |
| **E** every item on the frames route | $3.24 (62%) | $4.85 (43%) | $8.09 (5%) | $12.13 (−43%) | $16.18 (−91%) |

*(cost, and gross margin in brackets)*

Row E is why the allowance exists — and why it is denominated in **units**
rather than videos. A user on the frames route consumes the allowance eight
times faster, so they reach 150 videos, not 1,000. Row E past 500 is
unreachable.

### At the 1,200-unit allowance

| Scenario | Videos | Cost | % of net | Margin |
|---|---|---|---|---|
| Typical (250 videos, 200 asks) | 250 | $1.52 | **18%** | 82% |
| Full allowance (mix A) | ~460 | $2.46 | 29% | 71% |
| Full allowance + every Ask | ~460 | $3.22 | **38%** | 62% |
| **Absolute worst case** — every unit on frames, every Ask | 150 | $3.78 | **45%** | **55%** |

**The last row is the result that matters.** Under the previous duration-based
model the worst case was **126% of net revenue** — a subscriber could lose money
without doing anything abusive. It is now bounded at 45% and structurally cannot
go negative. No throttle, no fair-use clause, no cap on saves: the arithmetic
does it.

Targets from the brief: ≤20–25% typical (**18% ✅**), ≤30–40% heavy (**38% ✅**).

## Free — 300 units, ~120 videos

| Scenario | Cost/month |
|---|---|
| Typical (40% utilisation) | **$0.30** |
| Full allowance | $0.75 |
| Every unit on frames | $0.73 |

~120 understood videos a month is about four a day — enough to build a habit and
form an opinion, roughly a quarter of Pro. Saves, metadata, thumbnails, library,
collections and search stay unlimited regardless.

---

# Phase 9 — internal metering

**One meter.** Processing units, charged by the route that ran.

- **Save time**: 1 unit reserved (`UNITS_ON_SAVE`). `create_save` does no network
  I/O so it cannot know the route. Reserving the *cheap* amount is deliberate —
  reserving the expensive one would refuse a video the user can easily afford.
- **Settlement**: when the worker finishes, `units_for_content` reads the
  recorded `route` and charges the difference. A captions-routed save settles at
  1; a frames-routed one settles up to 8. **Nobody pays for frames never read.**
- Atomic conditional-UPDATE debits, unique reservations keyed
  `(user, content, attempt)`, refunds only when the job died and
  `SUM(estimated_usd) == 0`, monthly reset anchored to the signup day — all
  unchanged and still tested.

One unit is calibrated to one ordinary short video (~$0.002), which is what lets
the UI say "videos" and be telling the truth.

---

# Phase 11 — what users actually see

No unit ever reaches a screen.

- **Profile** → `Videos understood 74 / 120`, `Ask messages 24 / 150`,
  `Resets Sep 29`
- **Paywall** → "Understand 460 videos a month", "1,500 Ask messages",
  "Priority processing", "Deep video analysis"

Both are computed server-side from `TYPICAL_UNITS_PER_VIDEO` and fetched from
`GET /api/pricing`, so the paywall cannot advertise a number the backend will
not honour — and raising an allowance is an environment variable, not an App
Store release.

**Graceful exhaustion** is unchanged: the bookmark saves, keeps its URL, note,
collections and thumbnail, stays searchable and openable, and its state becomes
`limit_reached` with an upgrade affordance. On upgrade or reset,
`resume_limited_saves` queues held items oldest-first.

---

# Phase 12 — abuse

| Control | Value |
|---|---|
| Per-user concurrency | Free 1, Pro 3, enforced at claim time |
| Queue priority | Pro 20, Free 50 |
| Rolling daily saves | 200 |
| Rolling daily asks | 300 |
| Rolling daily reprocesses | 30 |
| Monthly spend backstop | $15/user |
| Job retries | 4, exponential backoff, then dead |
| Duplicate jobs | impossible — unique idempotency key per canonical item |
| Deep vision | Pro-only, explicit, 12 units each (max 100/month) |
| Failed acquisition | no refund once money was spent; reservation settles instead |

Normal users touch none of these. The structural protection is the unit weighting
itself, which is why the ceilings can stay high.

---

# What is not changed

Platform acquisition behaviour is **less** aggressive, never more: fewer video
downloads, lower resolution, audio in preference to video. No new endpoint, no
new provider, no bypass. `api/providers.py` capability gates and kill switches
are respected by the router and there are tests asserting that a disabled
platform never downloads regardless of routing preference.

Nothing here changes Sava's legal or policy posture on TikTok, Instagram or
YouTube.

---

# Open questions

1. **The route distribution is estimated.** Everything downstream depends on it.
   Run `GET /api/ops/routes` after a few weeks and re-derive.
2. **Audio-download size is inferred.** Measure `acquire.audio` in production.
3. **Escalation thresholds are guesses** — `SAVA_ROUTE_MIN_CAPTION_CHARS=120`,
   `MIN_TRANSCRIPT_CHARS=200`, `VISION_THRESHOLD=0.6`. Every item records its
   `route_reason`, so they can be tuned against real content.
4. **Quality has not been A/B tested.** The cheap route should produce
   comparable understanding for most short-form, but "comparable" is an
   assertion until someone compares summaries on the same corpus both ways.
   `SAVA_ROUTE_VISION_THRESHOLD=0.0` forces the old always-vision behaviour for
   a controlled comparison.
5. **`platform.download_video` double-counts `acquire.video`** in
   `usage_events`. Not fixed — it is a telemetry accounting change. Every figure
   in this document excludes `platform.*`.

---

# App Store Connect — still outstanding

1. Subscription group **Sava Pro**; both products in it.
2. `com.sava.mobile.pro.monthly` — $9.99 / 1 month.
   `com.sava.mobile.pro.annual` — $79.99 / 1 year.
3. Localisation + review screenshot for each.
4. **Apple Root CA - G3** deployed and `SAVA_APPLE_ROOT_CA_PATH` set.
   Until then production grants nothing — by design.
5. Small Business Program enrolment (15% instead of 30%).
