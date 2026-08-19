# Audit — iPhone Action Button / App Intents / Shortcuts Capture Architecture

**Area:** Sava iOS one-press capture (Action Button → App Intent → backend save)
**Date of audit:** 2026-08-18
**Branch audited:** `feat/intelligence-foundation`
**Type:** Read-only architecture audit + production workflow design
**Status:** Findings recorded. **Nothing in this document has been implemented.**

---

## 0. How to use this document

This is a preserved audit. A future session picking this up should read it as:

- **§2–§4 = current state.** Facts verified by reading the code on 2026-08-18. Line
  references were accurate at that commit; re-verify before acting.
- **§5–§10 = analysis.** Platform constraints, permissions, privacy, failure modes,
  App Store exposure.
- **§11–§13 = recommendations.** Proposed design. **Not built.** Do not treat any of
  it as existing behavior.
- **§14 = blockers and ordering.** Start here if you are implementing.

**Nothing in this audit was implemented.** No production code was modified during
the audit or during the writing of this document.

### Product intent this was audited against

The behavior the product wants:

**TikTok:** Action Button → get direct on-screen URL → send to Sava → save →
haptic/confirmation → continue scrolling. Do **not** screenshot TikTok when a valid
URL exists.

**YouTube / Instagram:** Action Button → try direct on-screen URL first → if
unavailable, screenshot-assisted resolution → if that fails, clipboard/copy-link
fallback → send canonical result to Sava.

**Constraint:** temporary screenshots must not unnecessarily enter Photos.

---

## 1. Executive summary

The App Intent scaffolding on the iOS side is genuinely well-built and correctly
shaped. It sits on **three things that do not exist** (a resolver endpoint, a durable
auth token, a Share Extension) and **one assumption that cannot hold on iOS** (reading
the frontmost third-party app's current URL).

Highest-leverage conclusions:

1. **Auth is the fatal blocker.** 30-minute JWT, no refresh endpoint. The Action
   Button will fail the large majority of real presses.
2. **There is no iOS mechanism to read a frontmost app's URL.** The `link:` parameter
   is dead by construction when invoked from the Action Button.
3. **TikTok is the *hardest* platform to resolve from a screenshot, not the easiest** —
   the video ID is never on screen. The product's assumed difficulty ordering is
   inverted.
4. **The Share Extension — which does not exist — is the highest-reliability path**,
   and is *especially* good on TikTok because of its persistent share arrow.
5. **Screenshots of arbitrary apps are the most sensitive data this product will ever
   touch**, and are currently uploaded without any on-device gating.

---

## 2. Current state — iOS capture stack

All four capture files live in `ios/Sava/Features/Capture/`, compiled into the single
app target (no extension).

| File | State | Notes |
|---|---|---|
| `SaveToSavaIntent.swift` | **Real** | `AppIntent`, `openAppWhenRun = false`, optional `link: URL?` + `screenshot: IntentFile?` params, returns `ProvidesDialog` |
| `SavaShortcuts.swift` | **Real** | `AppShortcutsProvider`, 3 phrases (all correctly contain `.applicationName`), `shortcutTileColor` — this is what makes it Action-Button-assignable |
| `CapturePipeline.swift` | **Real** | Strategy ladder: direct URL → screenshot resolve → clipboard → graceful fail |
| `ContentResolverService.swift` | **STUB** | `static let isEnabled = false`; `resolve()` returns `.unavailable` unconditionally. No multipart wiring. No server route exists. Comments document a planned `POST /api/resolve` contract |
| `CaptureModels.swift` | **Real** | `CapturedLink`, `PlatformDetector`, `CaptureInput`, `CaptureResolution`, `CaptureError` |

Supporting, correct:

- `Core/Networking/SavaClientFactory.swift` — `KeychainTokenProvider` reads the token
  directly from Keychain for headless contexts. **This part is right.**
- `Core/Security/KeychainStore.swift` — `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`.
  Correct accessibility class for background intent execution.

### Xcode project configuration (`ios/Sava.xcodeproj/project.pbxproj`)

- **One** native target: `com.sava.mobile`, `com.apple.product-type.application`
- iOS deployment target **17.0**, `TARGETED_DEVICE_FAMILY = 1` (iPhone only)
- **No Share Extension target**
- **No App Group**
- **No entitlements file** (`CODE_SIGN_ENTITLEMENTS` absent)
- **No keychain access group**
- **No widget / ControlWidget target**
- **No `CFBundleURLTypes`** (no custom URL scheme)
- `ios/Info.plist` contains **only** an ATS `NSAllowsLocalNetworking` exception
- No `BGTaskSchedulerPermittedIdentifiers`
- No `ITSAppUsesNonExemptEncryption`
- No `NSPhotoLibrary*` usage strings — **correct and should stay that way**

### What actually happens today when the Action Button is pressed

If the user follows the in-app setup card (`Features/Profile/ProfileView.swift`,
which instructs: Settings → Action Button → Shortcut), the App Shortcut is invoked
**with no parameters** — `link = nil`, `screenshot = nil`. The pipeline therefore
falls straight through to the clipboard branch, triggering a system paste prompt, or
fails outright.

**The current setup instructions produce a non-functional binding for the intended
behavior.**

---

## 3. Current state — backend save path

- `POST /bookmarks` (`api/main.py:284`) → `add_bookmark()` (`api/ingestors/registry.py:56`)
  → **synchronous** `extract_metadata()` network call → then
  `_link_and_schedule_processing()` (`api/main.py:205`) enqueues the async intelligence
  pipeline via the DB-backed job queue (`api/jobs.py`).
- TikTok path (`registry.py:63-72`): tries `TikTokApiIngestor` first (internal timeouts
  ~3s + ~2s), then falls back to `TikTokIngestor` which drives **Playwright headless
  browser** (`api/ingestors/tiktok.py:94`). Sequential fallback — latency compounds.
- **`POST /api/resolve` does not exist.**
- **`/auth/refresh` does not exist.** Confirmed by grep across `api/*.py`.
- `ACCESS_TOKEN_EXPIRE_MINUTES = 30` (`api/auth.py:13`).
- Canonical content dedupe works well (`api/content/identity.py:157` `resolve_identity`,
  `api/pipeline/ingest.py:102` `resolve_or_create_canonical`) — cross-user reuse of
  processed content is real and good.

### Client networking

- `APIClient` timeout `20s`, `waitsForConnectivity = false` (`Core/Networking/APIClient.swift:37-38`)
- `AppConfig.apiBaseURL` defaults to `http://127.0.0.1:8000` (`AppConfig.swift:14`),
  overridable via `SAVA_API_BASE` env var or Info.plist key
- 409 → `APIError.conflict` → surfaced to the user as a failure

---

## 4. The premise that does not hold

> *"TikTok: Action Button → get direct on-screen URL"*

**There is no iOS API, Shortcuts action, or App Intent that returns the current URL of
a frontmost third-party app.**

- "Get Current Web Page" / "Get Details of Safari Web Page" is **Safari-only**.
- TikTok, Instagram, and YouTube publish **no** App Intent or Shortcuts action exposing
  the currently playing item.
- The Action Button invokes App Shortcuts **without parameters**, so `link: URL?` can
  never be populated on that path.

### Screenshot resolvability by platform — corrected ordering

**A TikTok screenshot never contains the video ID.** At best it contains the creator
handle, caption, and music title. Resolution therefore means *search*, not *decode*.

| Platform | On screen at capture | Resolvability | Why |
|---|---|---|---|
| **YouTube (full player)** | Video **title** + channel name | **High (~90%)** | YouTube Data API search on title+channel resolves reliably |
| YouTube Shorts | Handle, often truncated title | Medium | Title frequently absent/clipped |
| Instagram Reels | `@handle` + caption | Low–medium | No public IG search API; scraping only; fragile |
| **TikTok** | `@handle` + caption + music | **Low–medium** | No legitimate search API; fragile scraping; ToS risk |

**Implication:** the product's assumed ordering is inverted. YouTube is the *easiest*
platform to resolve from screen; TikTok is the *hardest*.

### The compensating inversion

TikTok's share arrow is **permanently visible on the right rail**. Share → Sava is
**two taps, always, 100% canonical, zero ambiguity**.

Sava has **no Share Extension**. This is the single largest gap in the architecture:
the exotic path was built and the reliable one was skipped. A Share Extension should
carry roughly 90% of real saves.

---

## 5. Capability split — App Intents vs. Shortcuts vs. user config

### Can be done entirely with App Intents

- Registering "Save to Sava" for Action Button / Spotlight / Siri — **DONE** (`SavaShortcuts.swift`)
- Running headless without opening the app — **DONE** (`openAppWhenRun = false`)
- Reading the Keychain token headlessly — **DONE** (`SavaClientFactory.swift`)
- Accepting a URL and/or image parameter — **DONE**
- Network POST + dialog confirmation — **DONE**
- Reading the pasteboard — **DONE**, but see §8 (privacy) and §9 (failure #8)
- On-device Vision/OCR classification of a screenshot — **NOT DONE**, and it should be,
  for privacy (§8.1)
- A `ControlWidget` (Control Center / Lock Screen, iOS 18+) running the same intent —
  **NOT DONE**

### Still requires an Apple Shortcut

Exactly one thing, and it is unavoidable:

**Taking the screenshot.** No API lets an app or App Intent capture the screen of
another app. Only the Shortcuts app's built-in **Take Screenshot** action can, and only
when the shortcut itself was user-triggered.

The Action Button must therefore be bound to a **user-installed Shortcut**, not to the
App Shortcut, shaped as:

```
Take Screenshot            → in-memory image, NOT written to Photos
Run "Save to Sava" (Sava)  → screenshot: <Screenshot>
```

**Photos guarantee:** the Shortcuts *Take Screenshot* action outputs the image as a
variable and does **not** write to the Photos library unless a "Save to Photo Album"
action is added. The hardware screenshot chord (side + volume up) **does** write to
Photos. The design must use the Shortcuts action, never instruct a hardware chord.
This satisfies the "temporary screenshots must not enter Photos" requirement.

> **Needs device verification:** that *Take Screenshot* reliably captures the
> underlying app when the shortcut is fired from the Action Button over that app. This
> is a widely used pattern and expected to work, but cannot be verified in Simulator.

### Must be configured by the user

1. Sign in to Sava (token lands in Keychain)
2. Install the Shortcut — **ship it as an iCloud share link**; do not make users build it
3. Settings → Action Button → Shortcut → pick it
4. **Settings → Sava → Paste from Other Apps → Allow** — high leverage; permanently
   eliminates the "Allow Paste?" interruption
5. Allow notifications (required once resolution is async — §11)
6. On non-Action-Button iPhones: Accessibility → Touch → **Back Tap** → Double Tap → the Shortcut

### What can happen while Sava is not open

**Everything in the capture path.** App Intents defined in the app target launch the
app's process in the background; the app UI never appears. Keychain is readable
(`AfterFirstUnlockThisDeviceOnly`). **No App Group is required today** — one is needed
only when the Share Extension lands, along with a keychain access group so the
extension can read the token.

**What cannot happen:** any retry after the intent returns. There is no background task
registration, no `BGTaskSchedulerPermittedIdentifiers` in `Info.plist`, and no local
outbox. Combined with `waitsForConnectivity = false`, **one offline press silently
loses the save.**

---

## 6. Physical-iPhone limitations

- **Action Button hardware:** iPhone 15 Pro / 15 Pro Max, iPhone 16 series, iPhone 16e,
  iPhone 17 series. **Not** iPhone 15 / 15 Plus or earlier. Back Tap and a Control
  Center control are the coverage story for the rest of the install base.
- Device must have been unlocked at least once since boot (Keychain accessibility class).
- Shortcut execution over another app shows a **system banner that cannot be
  suppressed**. Truly silent capture is not achievable on iOS.
- **`UINotificationFeedbackGenerator` from a background intent is unlikely to fire**
  (`SaveToSavaIntent.swift:48,52`). What the user actually perceives is the system's own
  shortcut-completion feedback plus the returned dialog. Treat those two haptic lines as
  decorative until verified on hardware.
- **None of this is testable in the Simulator** — Action Button binding, Shortcuts
  screenshots, and the paste prompt all require a physical device.
- Background intent execution has a bounded window (seconds, not minutes) and the system
  can terminate it. See failure #3.

---

## 7. Permissions matrix

| Thing | Permission required | Status |
|---|---|---|
| App Intents / App Shortcuts | None. No entitlement (unlike legacy SiriKit) | ✅ satisfied |
| Screenshot via Shortcuts | None — user-triggered, image handed to the intent | ✅ satisfied |
| Photos | **Not needed and must stay not needed** | ✅ no `NSPhotoLibrary*` keys — correct |
| Pasteboard | System-gated prompt; one-time per-app user setting | ⚠️ unmitigated |
| Network | ATS — production **must** be HTTPS | ⚠️ default is `http://127.0.0.1:8000` |
| Notifications | Required for async save confirmations | ❌ never requested anywhere |
| Background refresh | `BGTaskSchedulerPermittedIdentifiers` for outbox flush | ❌ absent |
| Export compliance | `ITSAppUsesNonExemptEncryption` | ❌ absent — nags on every upload |

Note on pasteboard: `UIPasteboard.general.hasURLs` and `detectPatterns(for:)` do **not**
prompt. Reading `.url` or `.string` **does** prompt, unless the user has set
Settings → Sava → Paste from Other Apps → Allow.

---

## 8. Privacy concerns

### 8.1 Screenshots of arbitrary apps — 🔴 highest severity

The Action Button fires in pockets. A mispress in Messages, Mail, a banking app, or a
private DM thread uploads that screen to Sava's server. The current design uploads
unconditionally whenever no URL is present.

**Mitigation, and it is cheap:** classify **on-device** with `Vision` text recognition
before any upload. If the screen does not look like TikTok / Reels / YouTube, discard
the image and transmit nothing.

`ContentResolverService.swift:13` explicitly rejects on-device work as "fake." **That
reasoning is correct about *resolution* and wrong about *gating*.** On-device gating is
the right call and does not fake any result — it only decides whether to transmit.

### 8.2 Clipboard over-capture — 🔴 live bug, not hypothetical

`SaveToSavaIntent.swift:42-44` falls back to `UIPasteboard.general.string`, and
`CapturedLink` (`CaptureModels.swift:23`) accepts **any** well-formed http(s) URL.
`PlatformDetector` returns `.other` for unrecognized hosts but nothing rejects it.

Consequence: a copied password-manager URL, an internal corporate link, or a one-time
login link gets uploaded and saved as a bookmark. **There is no host allowlist.**

### 8.3 Screenshot retention policy — undefined

Nothing states retention because the endpoint does not exist. The policy to commit to
publicly: screenshots held in memory or ephemeral storage only, deleted the moment
resolution finishes or fails, never used for training, never sent to a third party
without disclosure.

### 8.4 Privacy nutrition label

Must declare screen contents collection if the screenshot path ships.

---

## 9. Failure cases (current state)

| # | Failure | Current behavior | Severity |
|---|---|---|---|
| 1 | **Token expired** — 30 min TTL (`api/auth.py:13`), **no refresh endpoint** | `SavaClient.hasStoredToken` only checks *presence*, not expiry. Press → 401 → "Your session expired." **Save lost.** In real use this is the common case, not the edge case | 🔴 **Fatal / launch blocker** |
| 2 | **Offline press** | `waitsForConnectivity = false` (`APIClient.swift:38`), no outbox → save silently lost | 🔴 **Launch blocker** |
| 3 | **Slow TikTok ingest** — TikTokApi then Playwright headless fallback (`tiktok.py:94`) | Can exceed the 20s `APIClient` timeout *and* the background intent budget. User sees failure while the bookmark may have been created anyway | 🔴 **Launch blocker** |
| 4 | **`Bookmark.url` is globally `unique=True`** (`api/models.py:34`), not per-user | User B saving a URL user A already saved → `IntegrityError` → 409 *"You already have this link bookmarked!"* — factually wrong, and it blocks the save | 🔴 **Launch blocker / data-model bug** |
| 5 | TikTok branch in `add_bookmark` skips the per-user `existing` check entirely (`registry.py:63-72`) | TikTok dedupe relies solely on the DB constraint from #4 | 🟠 |
| 6 | Screenshot present, resolver stubbed off | "Couldn't read the link here yet… coming soon" | 🟠 by design, honest |
| 7 | `vm.tiktok.com` short link | `identity.py:187-192` marks it unresolvable and hashes the URL → splits from the canonical `tiktok:<id>` row until `upgrade_identity` runs | 🟡 |
| 8 | Paste prompt appears mid-scroll | Breaks the "keep scrolling" promise entirely | 🟠 |
| 9 | Mispress in a private app | Screenshot uploaded (see §8.1) | 🔴 privacy |

---

## 10. App Store concerns

- **5.1.1 / 5.1.2 (data collection & purpose):** screen-content collection needs a clear
  in-app explanation and an accurate privacy label. Reviewers *will* ask why a
  bookmarking app uploads screenshots. Have an answer, and have the on-device
  pre-filter (§8.1) to point at.
- **HTTPS / ATS:** `AppConfig.swift:14` defaults to `http://127.0.0.1:8000`. Ship a real
  HTTPS base URL and drop `NSAllowsLocalNetworking` from Release.
- **Content risk — the real one:** server-side `yt-dlp`, Playwright, and `TikTokApi`
  violate those platforms' ToS. App Review rarely inspects a backend, but if the client
  ever surfaces downloaded media, that is "video downloader" territory — a well-known
  rejection category and a takedown magnet. **Keep media server-side; ship only
  transcripts, summaries, and thumbnails to the client.**
- **App Shortcut phrases** must contain `.applicationName` — ✅ satisfied.
- **Missing `ITSAppUsesNonExemptEncryption`** — cosmetic but should be fixed.
- **No private API use** — ✅ clean.
- **4.2 minimum functionality** — not a risk; the app has a full standalone library UI.

---

## 11. RECOMMENDED DESIGN (not implemented)

> Everything from here to §13 is proposed. None of it exists in the codebase.

### 11.1 Core architectural move

**Stop making the Action Button responsible for producing a canonical URL.** Make it
responsible for producing **evidence** — instantly and durably. Resolution becomes a
server-side async problem with a user-recoverable tail.

### 11.2 Two entry points, not one

**A. Share Extension — the workhorse (build this first).**
Share → Sava. Two taps. Canonical URL guaranteed. Every platform. No screenshot, no
clipboard, no privacy exposure, no resolution risk. TikTok's persistent share arrow
makes this *especially* strong on the #1 platform. Should carry ~90% of real saves.

**B. Action Button — the flourish.**
One press, evidence-based, best-effort. Genuinely magic on YouTube; a graceful assist
elsewhere.

### 11.3 Proposed Action Button flow

```
Press
 └─ Shortcut: Take Screenshot (in memory, never touches Photos)
     └─ Run "Save to Sava"
         ├─ On-device Vision OCR (~80ms)
         │   ├─ Not a supported surface? → discard image, no upload, silent no-op
         │   └─ Supported? → extract handle / title / caption text
         ├─ Clipboard: detectPatterns(.probableWebURL) → read ONLY if host is allowlisted
         ├─ POST /api/captures  (fire-fast, returns 202 in <500ms)
         │   └─ on failure: append to on-device durable outbox
         └─ Return dialog immediately: "Saving…" → user keeps scrolling
```

Server resolves asynchronously in the existing job queue (`api/jobs.py`). Ready →
optional notification.

### 11.4 The "Needs a link" inbox — the key UX idea

When resolution fails, the capture lands in a **"Needs a link" inbox** in the app,
holding the thumbnail crop and OCR text. One tap on a pasted or shared link completes it.

This converts every hard failure into a soft, recoverable one. **The user never loses a
save.** Every press means something. This is the single most important UX decision in
the design.

---

## 12. RECOMMENDED DATA CONTRACT (not implemented)

```http
POST /api/captures
Authorization: Bearer <token>
Content-Type: multipart/form-data
```

| Field | Type | Req | Notes |
|---|---|---|---|
| `meta` | JSON | ✅ | schema below |
| `screenshot` | binary | ⬜ | JPEG q0.7, long edge ≤1280. Omit entirely when `url` is present |

```jsonc
// meta
{
  "client":          "ios",
  "client_version":  "1.0.0 (12)",
  "entry_point":     "action_button" | "share_extension" | "control" | "in_app",
  "captured_at":     "2026-08-18T14:22:31.442Z",   // device clock, RFC3339
  "idempotency_key": "uuid-v4",                     // survives outbox retries
  "url":             "https://...",                 // when known — then resolution is skipped
  "url_source":      "share" | "clipboard" | "typed" | null,
  "platform_hint":   "tiktok" | "youtube" | "instagram" | null,
  "ocr": {                                          // on-device, screenshot path only
    "handle":     "@username" | null,
    "title":      "..." | null,
    "caption":    "..." | null,
    "confidence": 0.0
  }
}
```

**202 Accepted** — always, when the payload is well-formed:

```jsonc
{
  "capture_id": "cap_01J...",
  "state": "resolved" | "resolving" | "needs_link",
  "bookmark": { /* full Bookmark, only when state == "resolved" */ },
  "duplicate": false,
  "message": "Saved from @creator"     // ready-to-display dialog string
}
```

**Errors:**
- `401` expired → client refreshes and retries once
- `413` screenshot too large
- `422` malformed
- **Never `409` for duplicates** — return `200` with `duplicate: true` plus the existing
  bookmark. A duplicate is a *success* from the user's point of view; the current 409
  path (`api/main.py:317`) surfaces it as a failure.

**Inbox drive:** poll `GET /api/captures/{id}`, or list `GET /api/captures?state=needs_link`.

**Server guarantees to state publicly:** screenshots held in memory or ephemeral storage
only, deleted the moment resolution finishes or fails, never used for training, never
sent to a third party without disclosure.

---

## 13. Acceptance criteria for the recommended design

A future implementation should be considered done only when all of these hold:

1. Pressing the Action Button while scrolling TikTok returns control to the user in
   **< 1 second**, with no paste prompt (after the one-time Paste=Allow setting) and no
   app switch.
2. A press with an expired access token **still succeeds** — the client refreshes
   transparently and the user never sees "session expired."
3. A press with no network **is not lost** — it lands in the on-device outbox and is
   flushed on next connectivity or app foreground.
4. A screenshot of a **non-supported** app (Messages, Mail, banking) is **never
   transmitted** — verified by network capture.
5. The clipboard is read **only** when `detectPatterns` reports a probable web URL
   **and** the host is on the platform allowlist.
6. No captured screenshot ever appears in the Photos library.
7. Saving a URL another user already saved **succeeds** for the second user.
8. Saving a URL the *same* user already saved returns `200 duplicate: true` and reads as
   a success in the UI, not an error.
9. A failed resolution appears in the "Needs a link" inbox with its OCR context and can
   be completed in one tap.
10. The Share Extension resolves a canonical URL for TikTok, Instagram, and YouTube in
    two taps, with no screenshot and no clipboard access.
11. Production builds point at an HTTPS base URL with no `NSAllowsLocalNetworking`.

---

## 14. Blockers, ordering, and open decisions

### Ranked implementation order

| # | Work | Why | Class |
|---|---|---|---|
| 1 | **Long-lived auth** — refresh token, or a revocable per-device capture key | Without this the Action Button fails ~95% of presses. **Nothing else matters until it is fixed** | 🔴 Launch blocker |
| 2 | **`POST /api/captures` returning 202** | Decouples the intent from ingest latency; kills failure #3 | 🔴 Launch blocker |
| 3 | **Fix `Bookmark.url` `unique=True` → `UniqueConstraint(user_id, url)`** | Currently one user's save blocks every other user's | 🔴 Launch blocker (data model) |
| 4 | **Share Extension** (+ App Group + keychain access group) | Highest reliability per unit of work in this entire audit | 🔴 Launch blocker (product) |
| 5 | **On-device screenshot gating (Vision)** | The privacy story. Non-negotiable before shipping screenshots | 🔴 Security/privacy |
| 6 | **Clipboard host allowlist** | Live data-leak bug (§8.2) | 🔴 Security/privacy |
| 7 | Durable on-device outbox + `BGTaskScheduler` | No lost saves offline | 🟠 Should-fix pre-launch |
| 8 | Ship the Shortcut as an iCloud link; **rewrite the Profile setup card** | Current instructions produce a non-functional binding (§2) | 🟠 Should-fix pre-launch |
| 9 | `ControlWidget` + documented Back Tap | Covers non-Action-Button iPhones | 🟡 Phase 2 |
| 10 | Screenshot resolvers, **YouTube first** | Highest accuracy per unit of work (§4) | 🟡 Phase 2 |

### Security / privacy issues (explicit list)

- §8.1 Unconditional screenshot upload with no on-device gating
- §8.2 Clipboard over-capture with no host allowlist — **live bug**
- §8.3 Undefined screenshot retention policy
- §10 Production defaults to plaintext HTTP

### Unresolved decisions (need a human call)

1. **Auth model:** short access token + refresh token, **or** a separate long-lived,
   revocable per-device "capture key" scoped only to `POST /api/captures`? The second is
   safer for a headless intent but is more surface to build.
2. **Screenshot resolution vendor:** YouTube Data API is legitimate and reliable. TikTok
   and Instagram have **no legitimate search API** — resolution there means scraping, with
   ToS and durability risk. Decide whether to ship screenshot resolution for those two at
   all, or to route them entirely through the Share Extension and the "Needs a link" inbox.
3. **Whether to ship the screenshot path in v1 at all.** The Share Extension alone may
   deliver enough of the product value that the Action Button screenshot flow can be a
   Phase 2 differentiator, avoiding the §8.1 privacy surface at launch.
4. **Notification strategy:** silent-until-inbox vs. a notification on every async resolve.

### Phase 2 / future items

- `ControlWidget` (Control Center + Lock Screen), iOS 18+
- Back Tap documentation for pre-Action-Button devices
- YouTube screenshot resolver (highest-confidence platform)
- Instagram / TikTok screenshot resolvers (fragile — gated on decision #2)
- Short-link upgrade path already partly present: `upgrade_identity`
  (`api/content/identity.py:211`) merges `tiktok:u:<hash>` into `tiktok:<id>` once a
  redirect resolves — wire it into the capture flow (failure #7)

---

## 15. One-line version

The App Intent scaffolding is well-built and correctly shaped — but it sits on three
things that do not exist (a resolver endpoint, a durable token, a Share Extension) and
one assumption that cannot hold (reading a frontmost app's URL). Fix auth first, add
`POST /api/captures` returning 202, ship the Share Extension, and let the Action Button
be evidence-first with a recoverable inbox instead of a promise it cannot keep.
