# Sava iOS — App Store / TestFlight Launch Readiness Audit

**Audit date:** 2026-08-18
**Branch audited:** `feat/intelligence-foundation`
**Auditor role:** Senior iOS product-launch reviewer
**Audit type:** Read-only. No production code was modified as part of this audit.

---

## 0. How to use this document

This is a **preserved audit report**, not a task tracker and not a design doc. A future agent or
engineer picking this up with no prior context should read it in this order:

1. **§1 Context** — what Sava is and what was examined.
2. **§2 Current-state findings** — verified facts about the repo as of the audit date. These are
   observations, *not* recommendations. Every claim here was verified by reading the file cited.
3. **§3 / §4 / §5** — the three prioritized recommendation buckets
   (`MUST DO BEFORE TESTFLIGHT`, `MUST DO BEFORE PUBLIC LAUNCH`, `CAN WAIT`).
4. **§6 Security issues** — consolidated index of everything security-relevant, since these are
   scattered across the priority buckets.
5. **§7 Unresolved decisions** — things that require a human product/legal call, not code.
6. **§8 TestFlight strategy** and **§9 Acceptance criteria**.

**Important caveats for future sessions:**

- Findings reflect the repository state on **2026-08-18** on branch `feat/intelligence-foundation`,
  which at the time had a large amount of uncommitted/untracked work (the whole `api/ai/`,
  `api/pipeline/`, `api/services/`, and most of `ios/Sava/Features/`). **Re-verify any specific
  line number before acting on it** — line numbers will drift.
- Nothing in this document has been implemented. Treat every item in §3–§5 as open unless you
  verify otherwise in the code.
- This document must not be treated as legal advice, particularly §4.27–§4.28 and §7.

---

## 1. Context

### What Sava is

Sava is a save-and-understand product for short-form and long-form social video. Users save links
from TikTok / YouTube / Instagram (and several other platforms with basic support); a FastAPI
backend ingests the content, transcribes it, pulls comments, generates embeddings, and exposes
semantic search and per-video Q&A ("Ask Sava") built on Google Gemini.

There are three clients against one backend:
- `web/` — Next.js web app
- `ios/` — native SwiftUI iOS app (**the subject of this audit**)
- (planned) desktop

### What was examined

| Path | Purpose |
|---|---|
| `ios/Sava.xcodeproj/project.pbxproj` | Build settings, signing, targets, capabilities |
| `ios/Info.plist` | ATS, permission strings, URL types |
| `ios/Sava/**` | Full SwiftUI app source |
| `ios/README.md` | Stated architecture and known gaps |
| `api/main.py` | Route surface, auth gating, CORS |
| `api/auth.py` | JWT config, password hashing |
| `api/routes_intelligence.py` | AI endpoints (summary / ask / search) |
| `api/config.py`, `api/db.py`, `api/requirements.txt` | Providers, DB backend, dependency stack |
| `api/ai/`, `api/pipeline/`, `api/services/` | Gemini provider, ingestion pipeline, retrieval |
| `docker-compose.yml`, `.gitignore`, `git ls-files` | Deployment posture, tracked secrets |

### Headline conclusion

The app is a **well-built SwiftUI codebase** — clean layering, real Keychain usage, no fake data,
honest "not implemented yet" boundaries, and an App Intent / App Shortcut implementation that is
correct. It is nonetheless **not currently shippable to TestFlight**:

- there is no app icon image,
- no Development Team is set,
- there is no privacy manifest,
- there is no account deletion,
- there is no privacy policy,
- and `AppConfig.apiBaseURL` still defaults to `http://127.0.0.1:8000`.

Separately, several backend endpoints are unauthenticated in ways that would **expose real
testers' data the moment external testing begins**.

---

## 2. Current-state findings (verified facts, not recommendations)

### 2.1 Xcode project / build configuration

| Area | Verified state |
|---|---|
| Targets | **One** app target (`com.apple.product-type.application`). No share extension, widget, app clip, or test target |
| Bundle ID | `PRODUCT_BUNDLE_IDENTIFIER = com.sava.mobile` |
| Signing | `CODE_SIGN_STYLE = Automatic`; **`DEVELOPMENT_TEAM` is not set in any configuration** |
| Entitlements | **No `.entitlements` file exists anywhere in the project** |
| Privacy manifest | **No `PrivacyInfo.xcprivacy` exists anywhere in the project** |
| Deployment target | `IPHONEOS_DEPLOYMENT_TARGET = 17.0` |
| Device family | `TARGETED_DEVICE_FAMILY = 1` (iPhone only) |
| Orientation | `INFOPLIST_KEY_UISupportedInterfaceOrientations_iPhone = UIInterfaceOrientationPortrait` (portrait only) |
| Version | `MARKETING_VERSION = 1.0`, `CURRENT_PROJECT_VERSION = 1` |
| Launch screen | `INFOPLIST_KEY_UILaunchScreen_Generation = YES` (generated, no storyboard) |
| Swift | `SWIFT_VERSION = 5.0`; `SWIFT_EMIT_LOC_STRINGS = YES` |
| Release symbols | `DEBUG_INFORMATION_FORMAT = "dwarf-with-dsym"` in Release ✅ (dSYMs will be produced) |
| Release optimization | `SWIFT_COMPILATION_MODE = wholemodule`, `SWIFT_OPTIMIZATION_LEVEL = -O`, `VALIDATE_PRODUCT = YES` ✅ |
| Info.plist | Uses both `GENERATE_INFOPLIST_FILE = YES` and `INFOPLIST_FILE = Info.plist` |
| Dependencies | **Zero** SPM packages (`packageReferences` absent) |
| Schemes | **No shared `.xcscheme`** — `Sava.xcodeproj/xcshareddata` does not exist |

**App icon:** `ios/Sava/Assets.xcassets/AppIcon.appiconset/` contains **only `Contents.json`**,
which declares a single `universal / ios / 1024x1024` slot with **no `filename` key and no image
file on disk**. `git ls-files ios | grep -iE "png|icon"` returns **zero results** — no icon art is
tracked in the repository.

**Info.plist contents (complete):**
```xml
<key>NSAppTransportSecurity</key>
<dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
</dict>
```
That is the entire file. No permission usage strings, no `CFBundleURLTypes`, no
`LSApplicationQueriesSchemes`, no `SAVA_API_BASE` key, no `CFBundleDisplayName` override.

### 2.2 Permissions and required-reason APIs

A grep across `ios/Sava` for permission-gated and required-reason APIs found:

| API | Location | Note |
|---|---|---|
| `UserDefaults` | `Features/Search/SearchViewModel.swift:19,53,73` | Recent-search persistence. **This is a required-reason API** (`NSPrivacyAccessedAPICategoryUserDefaults`) |
| `UIPasteboard` | `Features/Capture/SaveToSavaIntent.swift:42–44` | Clipboard fallback inside the App Intent |
| `UIPasteboard` | `Features/Save/QuickSaveViewModel.swift:27–29` | Clipboard prefill in the in-app quick-save sheet |
| Keychain (`SecItem*`) | `Core/Security/KeychainStore.swift` | Token storage |
| `URLCache` disk | `Core/Images/ImageLoader.swift:18` | 256 MB image disk cache |

**Not present:** Photos, Camera, Location, Notifications, Contacts, Speech, AVAudioSession,
App Tracking Transparency. **No permission usage strings are required today**, because no
permission-gated framework is used.

### 2.3 App Intents / Shortcuts / Action Button

- `Features/Capture/SaveToSavaIntent.swift` — `SaveToSavaIntent: AppIntent`,
  `openAppWhenRun = false`, parameters `link: URL?` and `screenshot: IntentFile?`,
  returns `some IntentResult & ProvidesDialog`.
- `Features/Capture/SavaShortcuts.swift` — `SavaShortcuts: AppShortcutsProvider` with phrases
  `"Save to \(.applicationName)"`, `"Save this to \(.applicationName)"`,
  `"Add to \(.applicationName)"`, `shortTitle: "Save to Sava"`,
  `systemImageName: "bookmark.fill"`, `shortcutTileColor = .navy`.
- **Availability checked and confirmed OK:** `ShortcutTileColor` is annotated
  `@available(macOS 13.0, iOS 16.0, ...)` in the iOS 26.5 SDK's `AppIntents.swiftinterface`.
  It is **not** an iOS 18-only symbol, so it compiles cleanly against the 17.0 deployment target.
  *(This was specifically investigated because it looked like a likely availability bug. It is not
  one — do not "fix" it.)*
- `Features/Capture/CapturePipeline.swift` implements the documented strategy ladder:
  `direct URL → screenshot resolution → clipboard → graceful failure`.
- `Core/Networking/SavaClientFactory.swift` provides `KeychainTokenProvider` so the headless
  intent can read the token without `SessionStore`. This is correct and thread-safe.

**Behavioral risk noted:** the intent reads `UIPasteboard.general.url` / `.string` while running
in the background with `openAppWhenRun = false`. `hasURLs` does not prompt, but *reading* the
pasteboard value triggers the iOS system paste confirmation, which cannot be reliably presented
from a background intent. The clipboard fallback path may therefore be unreliable in practice.

### 2.4 Networking and configuration

`Core/Networking/AppConfig.swift`:
- Resolution order: `SAVA_API_BASE` process environment → `SAVA_API_BASE` Info.plist key →
  **fallback `http://127.0.0.1:8000`**.
- The Info.plist key mechanism exists in code but **no such key is present in `Info.plist`**.

`Core/Utilities/DevFlags.swift` (DEBUG-only, correctly `#if DEBUG` guarded, compiled out of
Release): `SAVA_DEV_TAB`, `SAVA_DEV_SEARCH`, `SAVA_DEV_OPEN_FIRST`. `SessionStore.restore()`
additionally honours `SAVA_DEV_TOKEN` under `#if DEBUG`. **This is good hygiene and is also a
ready-made harness for scripted screenshot runs.**

### 2.5 Authentication and session

- Email + password only. `AuthService` → `/auth/login`, `/auth/register`, `/auth/me`.
- JWT bearer, **30-minute TTL, no refresh token**. A 401 on any call routes cleanly to signed-out
  via `SessionStore.handleUnauthorized()`.
- Token at rest in Keychain with `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` ✅.
- Keychain service string is `"com.sava.mobile"`, account `"com.sava.mobile.authToken"` —
  **coupled to the bundle ID**, see §3.3.
- `finishSignIn` correctly deletes the token if `/auth/me` fails, avoiding a half-authenticated
  state ✅.
- **No Sign in with Apple.** *This is acceptable and not a violation* — Guideline 4.8 only
  triggers when an app offers third-party/social login (Google, Facebook, etc.). Sava offers
  none, so SIWA is optional.
- **No password reset flow exists** in the app or the backend (grep for `forgot` / `reset password`
  returns nothing relevant).
- **No email verification.**
- Client-side password minimum is 6 characters (`AuthFlowView` placeholder "At least 6 characters").

### 2.6 Account deletion

- `Features/Profile/ProfileView.swift` contains: identity block, Action Button setup guide,
  capture-behavior explainer, and a **Sign out** button. **There is no delete-account affordance.**
- Backend route inventory shows **no user-deletion endpoint**. The only `DELETE` route is
  `@app.delete("/api/bookmarks/{bookmark_id}")` (`api/main.py:401`).

### 2.7 Legal / policy surfaces

- **No privacy policy page** anywhere: `web/app` contains only
  `contexts/`, `auth/`, `auth/register/`, `auth/login/`, `components/`, `api/`,
  `api/ai-summary/`, `api/transcript/`, `api/transcript/ai-summary/`.
- **No terms of service page.**
- **No ToS / privacy links in the iOS sign-up flow** — grep for `terms|privacy|policy|agree`
  across `ios/Sava` returns only unrelated cache-policy matches.
- `.env_docs` (tracked) was inspected and contains **only platform-support documentation, no
  secrets** ✅.

### 2.8 Monetization

- **No StoreKit, no IAP, no subscription, no paywall** anywhere in `ios/Sava` or `api/`.

### 2.9 Analytics and crash reporting

- **None.** Grep for `firebase|sentry|amplitude|mixpanel|posthog|analytics|crashlytics|MetricKit`
  across `ios/Sava` returns **zero matches**.
- Release builds do emit dSYMs, so Xcode Organizer opt-in crash reports would work.
- The backend has `api/ai/telemetry.py` recording operations (`content.cache_hit`, `search`, …)
  server-side — useful for cost tracking, but it is not client analytics.

### 2.10 Onboarding

- `App/RootView.swift` routes `restoring → LaunchView`, `signedOut → AuthFlowView`,
  `signedIn → AppShell`.
- **First launch is a hard authentication wall.** There is no value-proposition sequence, no
  guest/demo mode, and no Action Button setup during onboarding — the Action Button guide lives
  only in the Profile tab.

### 2.11 AI features — client/server mismatch (important)

**The backend implements the AI features. The iOS client has them switched off.**

Backend (`api/routes_intelligence.py`, mounted at `api/main.py:830` via
`app.include_router(intelligence_router)`), all correctly scoped to `current_user` through an
`_owned_bookmark` ownership check:

| Route | Line | Auth |
|---|---|---|
| `GET /api/ai/modes` | 50 | — |
| `GET /api/bookmarks/{id}/status` | 58 | `current_user` ✅ |
| `POST /api/bookmarks/{id}/reprocess` | 81 | `current_user` ✅ |
| `GET /api/search` | 104 | `current_user` ✅ |
| `GET /api/bookmarks/{id}/summary` | 125 | `current_user` ✅ |
| `POST /api/bookmarks/{id}/ask` | 143 | `current_user` ✅ |
| `POST /api/ask` | 173 | `current_user` ✅ |

iOS client:
- `Features/Detail/Intelligence.swift` — `IntelligenceService.isEnabled = false`, with a comment
  stating the backend "does NOT" expose a Q&A endpoint. **That comment is now stale.**
- `Features/Detail/AskSavaSection.swift` — renders example question chips, a disabled input, and a
  **"Conversational answers are coming soon"** badge.
- `Features/Capture/ContentResolverService.swift` — `isEnabled = false`; the planned
  `POST /api/resolve` (multipart screenshot → canonical URL) **is genuinely not implemented
  server-side** (confirmed absent from the route inventory).

`ProfileView`'s "How capture works" card tells users YouTube and Instagram capture uses
"screenshot resolution is coming" — accurate, but it means the flagship Action Button flow
degrades to "No link found" on two of three primary platforms.

### 2.12 AI provider and content pipeline

- Provider: **Google Gemini** (`api/ai/gemini.py`), behind an `AIProvider` abstraction
  (`api/ai/base.py`) with a model router (`api/ai/router.py`).
- Models routed: `gemini-3.5-flash-lite`, `gemini-3.7-flash`, `gemini-embedding-001`.
- `api/config.py` reads `GEMINI_API_KEY` and `OPENAI_API_KEY`; only Gemini is wired.
- Embeddings + retrieval in `api/vectors.py`, `api/services/retrieval.py`.
- **User content (transcripts, comments, and eventually screenshots) is sent to Google Gemini.**

**Content acquisition stack** (`api/requirements.txt`):
`yt-dlp`, `TikTokApi`, `playwright`, `instaloader`, `youtube-transcript-api`,
`youtube-comment-downloader`, `openai-whisper`, `faster-whisper`.

This means the backend **downloads third-party platform media and transcribes it**, and
**scrapes third-party comments**, which are then displayed in the iOS app
(`Features/Detail/CommentsSection.swift`, `TranscriptSection.swift`).

### 2.13 Backend security posture (as it affects iOS launch)

| Finding | Location | Verified detail |
|---|---|---|
| `GET /users` unauthenticated | `api/main.py:196` | `list_users` has **no `Depends(get_current_user)`** and returns `id`, `email`, `created_at` for **every user** |
| `GET /api/thumbnail` unauthenticated open proxy | `api/main.py:492` | `requests.get(url, stream=True)` on caller-supplied `url`. No auth, no host allowlist, no scheme check, no redirect limit, no size limit → **SSRF + open relay** |
| `POST /api/transcript` unauthenticated | `api/main.py:553` | No auth dependency; drives yt-dlp / Whisper compute |
| `GET /api/comments/{bookmark_id}` unauthenticated + unscoped | `api/main.py:685` | Only `Depends(get_db)`; no ownership check → **IDOR by integer bookmark ID** |
| Default JWT secret | `api/auth.py:11` | `SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")` — silently falls back |
| Account enumeration on login | `api/main.py:163–172` | 404 `"Email not found"` vs 401 `"Incorrect password"` |
| CORS | `api/main.py:56–74` | `allow_origins` localhost:3000–3003 **plus** `allow_origin_regex=r"http://(localhost\|127\.0\.0\.1):\d+"`, with `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]` |
| Databases tracked in git | `git ls-files` | **`bookmarks.db` and `api/bookmarks.db` are both tracked.** Inspected: 18 tables including `users` (with `password_hash`), `bookmarks`, `chat_messages`, `content_transcripts`. 1 user row at audit time |
| Default DB | `api/db.py:16` | `DATABASE_URL` defaults to `sqlite:///./api/bookmarks.db`; Postgres path exists and `docker-compose.yml` provides Postgres 16 with hardcoded dev credentials |

**Correctly scoped ✅:** `GET /api/bookmarks` (`api/main.py:327`) filters on
`Bookmark.user_id == current_user["id"]`, validates `platform` against an allowlist, and bounds
`limit` to `le=500`. `GET /bookmarks` (legacy, line 392) delegates to it and is likewise scoped.
All of `routes_intelligence.py` is owner-scoped.

---

## 3. 🔴 MUST DO BEFORE TESTFLIGHT

These block the upload itself, Beta App Review, or the safety of real testers' data.
**Nothing below is optional for an external TestFlight build.**

### 3.1 Build & signing — hard blockers on archive/upload

| # | Item | Why it blocks |
|---|---|---|
| 1 | **Ship an actual app icon.** `AppIcon.appiconset` has a manifest and no pixels | Archive validation fails outright with "missing app icon". This is the first thing that will stop you |
| 2 | **Set `DEVELOPMENT_TEAM`** in the target's build settings | With automatic signing and no team ID you cannot create a Distribution provisioning profile or archive for upload |
| 3 | **Change the bundle ID off `com.sava.mobile`** | You almost certainly do not control `sava.com`. Register a real owned reverse-DNS identifier (e.g. `com.astraeholdings.sava`) in the Developer portal. **The bundle ID cannot be changed after first submission.** Note this also changes the Keychain `service` string in `KeychainStore.swift` — decide it once, now, before any tester has a stored token |
| 4 | **Share the Xcode scheme** (`Product → Scheme → Manage Schemes → Shared`) | `xcshareddata` does not exist, so CI and any fresh clone cannot `xcodebuild -scheme Sava` |
| 5 | **Establish a build-number strategy** | Every TestFlight upload needs a unique `CURRENT_PROJECT_VERSION`; it is hardcoded to `1` |

### 3.2 Configuration — the app will not function for testers

| # | Item | Detail |
|---|---|---|
| 6 | **Point `AppConfig.apiBaseURL` at a real HTTPS host** | It defaults to `http://127.0.0.1:8000`. A TestFlight build on a tester's phone reaches nothing. **This implies deploying the backend publicly over TLS before inviting anyone** |
| 7 | **Remove `NSAllowsLocalNetworking` from the Release Info.plist** once #6 lands | Not a rejection on its own, but it is a development artifact and ATS must not be relaxed for a production HTTPS host |
| 8 | **Add production `SAVA_API_BASE` as an Info.plist key** | The resolution mechanism already exists in `AppConfig`; use it so Debug and Release differ. Do not rely on a scheme environment variable, which does not ship |

### 3.3 Privacy — required for upload and for the App Store Connect record

| # | Item | Detail |
|---|---|---|
| 9 | **Add `PrivacyInfo.xcprivacy`** | The app calls `UserDefaults` (`SearchViewModel.swift:19,53,73`), a required-reason API — declare `NSPrivacyAccessedAPICategoryUserDefaults` with reason **`CA92.1`**. Also declare data collection: email address (account), user content (saved links, notes, search queries), linked to identity. Missing this produces **ITMS-91053** on upload |
| 10 | **Publish a privacy policy at a stable public URL** | App Store Connect requires a Privacy Policy URL on the app record before submitting for Beta App Review. **None exists anywhere in the repo.** It must specifically cover: what is stored server-side, that content is sent to **Google Gemini** for processing, retention periods, and deletion |
| 11 | **Complete the App Privacy questionnaire** in App Store Connect, matching the manifest exactly | |

### 3.4 Guideline compliance that Beta App Review does enforce

External TestFlight requires Beta App Review, which applies the App Review Guidelines **minus the
metadata-only sections**. These therefore apply:

| # | Item | Guideline |
|---|---|---|
| 12 | **Implement account deletion — in-app, self-service** | **5.1.1(v).** Any app with account creation must let users initiate deletion from within the app. Requires (a) a `DELETE /auth/me` (or equivalent) that cascades `bookmarks`, `comments`, `content_embeddings`, `chat_threads`, `chat_messages`, `collections`, `collection_items`, and `usage_events`; and (b) a destructive-confirmation flow in `ProfileView`. **This is the single most commonly cited rejection for apps at exactly this stage** |
| 13 | **Surface Terms of Service and Privacy Policy links in the sign-up flow** | `AuthFlowView` has neither. Registration without presenting terms is a routine rejection |
| 14 | **Provide a demo account in App Review notes** | The app is a hard auth wall from first launch (`RootView` → `AuthFlowView`). Without working credentials, review fails automatically under **2.1** |

### 3.5 Backend security — these leak real testers' data

**Do not invite a single external tester until §3.5 is closed.** These are hours of work and they
are the difference between a private prototype and a data leak with real users attached.

| # | Item | Location | Impact |
|---|---|---|---|
| 15 | **Close `GET /users`** | `api/main.py:196` | Unauthenticated; returns **every user's email and ID**. The instant you have external testers this is a public dump of their emails. Delete it or gate behind an admin check |
| 16 | **Add auth + ownership check to `GET /api/comments/{bookmark_id}`** | `api/main.py:685` | No auth, no ownership check → any caller can enumerate any user's saved content by integer ID. Classic IDOR |
| 17 | **Add auth to `POST /api/transcript`** | `api/main.py:553` | Unauthenticated; anyone can drive your yt-dlp/Whisper compute for free |
| 18 | **Fix `GET /api/thumbnail`** | `api/main.py:492` | Unauthenticated open proxy: `requests.get(url)` on caller-supplied input with no allowlist, scheme check, or redirect limit. **SSRF against your own network and cloud metadata endpoints, plus an open relay.** Restrict to a host allowlist of the CDNs you actually use, require auth, cap response size, and disable redirects |
| 19 | **Make `SECRET_KEY` fail loudly when unset** | `api/auth.py:11` | Defaults to `"your-secret-key-change-in-production"`. If the env var is unset in production, **every JWT is forgeable by anyone who has read this repo** |
| 20 | **Fix CORS for production** | `api/main.py:56–74` | Localhost-only origins plus an any-localhost-port regex, with `allow_credentials=True`. iOS does not care; the web app will break and the regex must not survive to production |
| 21 | **Purge `bookmarks.db` and `api/bookmarks.db` from git** | `git ls-files` | Both are tracked and the `users` table with `password_hash` is inside them. Add to `.gitignore`, `git rm --cached`, and **scrub history** before this repo goes anywhere |

### 3.6 Also true, worth fixing before testers arrive

| # | Item | Detail |
|---|---|---|
| 22 | **Login enumerates accounts** | `/auth/login` returns 404 `"Email not found"` vs 401 `"Incorrect password"`. Return one generic failure for both |
| 23 | **No password reset exists at all** | Any tester who forgets their password is **permanently locked out with no recovery path**. This will happen during TestFlight |
| 24 | **Password minimum is 6 characters** with no server-side strength check | |

---

## 4. 🟠 MUST DO BEFORE PUBLIC LAUNCH

### 4.1 The two features the app advertises but does not do

| # | Item | Detail |
|---|---|---|
| 25 | **Wire up `IntelligenceService` — the backend endpoints now exist** | `routes_intelligence.py` implements `POST /api/bookmarks/{id}/ask` (line 143), `GET /api/bookmarks/{id}/summary` (line 125), and `GET /api/search` (line 104), all owner-scoped. Meanwhile `Intelligence.swift` hardcodes `isEnabled = false` and `AskSavaSection` renders a **"Conversational answers are coming soon"** chip. Shipping the *differentiating* feature as a placeholder is both a product failure and a **Guideline 2.3.1** problem — Apple rejects apps whose UI advertises features as "coming soon" |
| 26 | **Resolve the screenshot-capture gap** | `ContentResolverService.isEnabled = false` and `POST /api/resolve` genuinely does not exist. `ProfileView` tells users in plain language that YouTube and Instagram capture works via screenshot resolution — **it does not**. The Action Button, the signature interaction, silently degrades to "No link found" on two of three primary platforms. Either build the resolver endpoint or rewrite that copy honestly |

**Note for future agents:** the source comments around both flags are commendably honest about the
gap. That honesty does not survive contact with App Review, which reads the *shipped UI*, not the
comments.

### 4.2 User-generated / third-party content — the biggest structural risk

| # | Item | Detail |
|---|---|---|
| 27 | **Guideline 1.2 applies to Sava** | The app displays scraped YouTube and TikTok comments (`CommentsSection.swift`, backed by `youtube-comment-downloader` and `TikTokApi`). That is third-party UGC surfaced in your app, which obligates you to provide: (a) a method to **filter objectionable material**, (b) a mechanism to **report offensive content** with a timely response, (c) a way to **block abusive users** where applicable, and (d) **published contact information**. **None of these exist.** This is a concrete rejection vector, not a theoretical one |
| 28 | **Guideline 5.2.3 / 5.2.1 — the existential one** | The backend uses `yt-dlp`, `instaloader`, `TikTokApi`, and `youtube-comment-downloader` to download and transcribe third-party platform content. **Downloading YouTube media violates YouTube's Terms of Service**, and Apple removes apps that do it. Before public launch you need a defensible content story: use official APIs where they exist (YouTube Data API for comments, official caption tracks), stop server-side media downloading of YouTube, and get legal input on the Instagram/TikTok paths. **This is the most likely reason Sava gets pulled after launch rather than rejected before it** |

### 4.3 AI disclosure

| # | Item | Detail |
|---|---|---|
| 29 | **Disclose third-party AI processing and obtain consent** | **Guideline 5.1.2(i)**, as updated in late 2025, requires apps to clearly disclose where personal data is shared with third parties for AI processing and to obtain explicit permission. Sava sends user-saved content — transcripts, comments, and eventually screenshots — to **Google Gemini**. This must appear in onboarding, in the privacy policy, and in the App Privacy answers |
| 30 | **Answer the AI questions in the age-rating questionnaire honestly** | Apple's current rating flow asks specifically about in-app **AI chatbots** and about **UGC exposure**. "Ask Sava" is a chatbot over third-party content. Expect **12+ at minimum**; **17+ is plausible** given uncurated TikTok/YouTube comments. Answering optimistically is a fast route to removal |
| 31 | **Label AI output as AI-generated** in the summary and Ask Sava UI, and do not present it as authoritative | |

### 4.4 Store presence

| # | Item | Detail |
|---|---|---|
| 32 | **Screenshots** | None exist. Minimum: the **6.9" iPhone set (1320×2868)**; Apple scales down for smaller devices. iPad is **not** required since `TARGETED_DEVICE_FAMILY = 1`. **Implementation note:** the existing `DevFlags` (`SAVA_DEV_TOKEN`, `SAVA_DEV_TAB`, `SAVA_DEV_SEARCH`, `SAVA_DEV_OPEN_FIRST`) are a genuinely good foundation for scripting reproducible screenshot runs against a seeded account — use them |
| 33 | **App Store description, subtitle, keywords, promotional text, support URL, marketing URL** | All absent. The description **must not** promise the resolver or Ask Sava until #25 and #26 ship |
| 34 | **Age rating questionnaire** | Complete per #30 |
| 35 | **Crash reporting** | There is none. Release builds do produce dSYMs (`dwarf-with-dsym` ✅), so Xcode Organizer gives opt-in crash reports for free — but that is thin for a public launch. If you add a third-party SDK (Sentry, Firebase), note it brings **its own privacy manifest and signature requirements** and changes your App Privacy answers |
| 36 | **Product analytics** | Also none. You will launch blind on activation, save-success rate, and Action Button adoption — the three numbers that determine whether Sava works. A first-party events endpoint avoids adding an SDK and keeps the privacy story simple |

### 4.5 Product completeness

| # | Item | Detail |
|---|---|---|
| 37 | **No Share Extension** | On iOS the share sheet is *the* way people save links — it is how every competitor works. The Action Button is a great differentiator but **not a substitute**, and it only exists on iPhone 15 Pro and later. **Architecture note:** a share extension needs an **App Group** so the extension and app can share the Keychain token — that will be your first real `.entitlements` file, and it also requires adding `kSecAttrAccessGroup` to `KeychainStore` |
| 38 | **No onboarding** | First launch drops straight into a signup form with no value proposition and no Action Button setup. The Action Button guide is buried in Profile, where new users will not find it |
| 39 | **No deep linking or universal links** | No `CFBundleURLTypes`, no associated-domains entitlement. This blocks password-reset email links, marketing attribution, and web→app handoff |

---

## 5. 🟢 CAN WAIT

- **iPad support.** `TARGETED_DEVICE_FAMILY = 1` is a legitimate v1 choice.
- **Localization.** `SWIFT_EMIT_LOC_STRINGS = YES` is already on, so the groundwork is free
  whenever you want it.
- **Subscriptions / IAP.** No StoreKit code and no paywall exist. Ship free, learn what people
  use, then price it. **Cost note:** Gemini inference is the main variable cost — instrument it
  first. `api/ai/telemetry.py` already records operations and cache hits, which is a good start.
- **Widgets, Live Activities**, and Siri/Spotlight surfaces beyond the existing App Shortcut.
- **JWT refresh tokens.** The 30-minute TTL with clean 401 → signed-out is handled correctly in
  `SessionStore`; it is annoying, not broken. Fix it when testers complain — they will.
- **Landscape support**, and a Dynamic Type / VoiceOver accessibility pass. Worth doing
  eventually; not a blocker.
- **MetricKit**, App Store Connect API automation, fastlane.

---

## 6. Consolidated security index

Cross-reference of everything security-relevant, since these are distributed across §3 and §4.

| Severity | Issue | Item # | Location |
|---|---|---|---|
| **Critical** | User databases with `password_hash` tracked in git | 21 | `bookmarks.db`, `api/bookmarks.db` |
| **Critical** | Default JWT `SECRET_KEY` → forgeable tokens if env unset | 19 | `api/auth.py:11` |
| **Critical** | `GET /users` unauthenticated — full email dump | 15 | `api/main.py:196` |
| **High** | `GET /api/thumbnail` SSRF / open proxy | 18 | `api/main.py:492` |
| **High** | `GET /api/comments/{id}` IDOR — no auth, no ownership | 16 | `api/main.py:685` |
| **Medium** | `POST /api/transcript` unauthenticated compute abuse | 17 | `api/main.py:553` |
| **Medium** | Overly permissive CORS regex with credentials | 20 | `api/main.py:56–74` |
| **Medium** | Plaintext HTTP base URL / ATS local-networking exception shipping in Release | 6, 7 | `AppConfig.swift`, `Info.plist` |
| **Low** | Account enumeration on login | 22 | `api/main.py:163–172` |
| **Low** | Weak password policy (6 chars, no server-side check) | 24 | `AuthFlowView`, `api/main.py:128` |
| **Low** | No email verification | 2.5 | — |

**Verified-good, do not "fix":** Keychain uses `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`;
`GET /api/bookmarks` and all of `routes_intelligence.py` are correctly user-scoped;
`finishSignIn` cleans up on partial failure; `DevFlags` and `SAVA_DEV_TOKEN` are `#if DEBUG`-gated
out of Release.

---

## 7. Unresolved decisions (require a human call, not code)

1. **The scraping / Terms-of-Service question (§4.28).** Whether Sava continues to download and
   transcribe YouTube/Instagram/TikTok media server-side is a **product and legal decision**, not
   an engineering one. It should be settled **before** further investment in the transcript
   pipeline, because the answer determines whether that pipeline has a future.
2. **Final bundle identifier** (§3.3). Blocks the Keychain service string, the App Group name for
   a future share extension, and the associated-domains configuration. Decide once, early.
3. **Age rating target** (§4.30) — 12+ vs 17+ depends on how much comment moderation you build.
4. **Whether to build the screenshot resolver at all** (§4.26), or to reposition the Action Button
   as TikTok-first and rewrite the `ProfileView` copy accordingly.
5. **Analytics approach** (§4.36): first-party events endpoint vs third-party SDK. The SDK route
   adds privacy-manifest and SDK-signature obligations.

---

## 8. TestFlight strategy

**Internal first** — up to 100 App Store Connect users, no Beta App Review, same-day turnaround.
This is where you validate that items **1–8** are actually correct: that the app launches, reaches
a real HTTPS backend, signs in, and saves. **Do not invite anyone external until items 15–21 are
closed** — external testers are real people whose emails are currently readable by anyone who can
reach `/users`.

**External beta** — requires Beta App Review (~24–48h). Needs: the icon, the privacy policy URL,
App Privacy answers, a Beta App Description, a feedback email, and — because Beta App Review
applies the App Review Guidelines minus the metadata sections — **account deletion (#12)** and
**terms (#13)**. Start with 20–50 people you can talk to directly, weighted toward **iPhone 15 Pro
and later** so you actually get Action Button signal.

**Instrument before you invite.** With no analytics and no crash SDK, a beta produces vibes. At
minimum measure: sign-up completion, first save, saves per user per week, Action Button save
success rate **by platform**, and Ask Sava usage once #25 lands.

---

## 9. Acceptance criteria

### 9.1 "Ready for internal TestFlight"
- [ ] Release archive validates and uploads to App Store Connect without errors
- [ ] App icon renders on the home screen and in TestFlight
- [ ] A build installed from TestFlight on a physical device reaches the production HTTPS API
- [ ] Register → sign in → save a link → see it in Library, all on a device, over the public API
- [ ] `NSAllowsLocalNetworking` absent from the Release Info.plist; no plaintext HTTP in Release
- [ ] `xcodebuild -project ios/Sava.xcodeproj -scheme Sava …` succeeds from a clean clone
- [ ] Build number increments automatically per upload

### 9.2 "Ready for external TestFlight" (adds to 9.1)
- [ ] `PrivacyInfo.xcprivacy` present; upload produces **no ITMS-91053** warning
- [ ] Privacy Policy URL live and set on the App Store Connect record
- [ ] App Privacy questionnaire completed and consistent with the manifest
- [ ] Account deletion works end-to-end from `ProfileView` and cascades every user-owned table
- [ ] ToS + Privacy links present and reachable in the sign-up flow
- [ ] Demo account credentials supplied in Beta App Review notes and verified working
- [ ] `/users` removed or admin-gated; `/api/comments/{id}` auth + ownership enforced;
      `/api/transcript` authenticated; `/api/thumbnail` allowlisted + authenticated
- [ ] `SECRET_KEY` fails startup when unset; production secret rotated
- [ ] `bookmarks.db` / `api/bookmarks.db` untracked, gitignored, and scrubbed from history
- [ ] Password reset flow exists end-to-end
- [ ] Login returns a single generic failure for unknown-email and wrong-password

### 9.3 "Ready for public launch" (adds to 9.2)
- [ ] `IntelligenceService.isEnabled = true` and Ask Sava / summary return real answers, **or**
      the feature and all UI referencing it are removed from the shipped build
- [ ] Screenshot resolver shipped, **or** `ProfileView` capture copy rewritten to match reality
- [ ] UGC controls in place: content filtering, in-app report mechanism with a response process,
      and published contact information (Guideline 1.2)
- [ ] Content-acquisition legal position resolved and implemented (§7.1 / Guideline 5.2.3)
- [ ] AI processing disclosed in onboarding + privacy policy, with explicit consent captured
- [ ] AI-generated output visibly labeled as such
- [ ] Age rating questionnaire completed honestly, including the AI-chatbot and UGC questions
- [ ] Screenshots (6.9"), description, subtitle, keywords, support URL all in App Store Connect
- [ ] Crash reporting in place with dSYM upload verified
- [ ] Core funnel analytics instrumented and reporting

---

## 10. The three things to do first

1. **Icon + team + real bundle ID + production HTTPS backend.** Nothing else can be tested until
   an archive uploads and reaches a live server.
2. **Close `/users`, `/api/thumbnail`, `/api/comments/{id}`, `/api/transcript`, and the default
   `SECRET_KEY`** — then purge the two `bookmarks.db` files from git history.
3. **Build account deletion end-to-end.** It is the guideline that stops the most apps at exactly
   this stage, it touches every table in the schema, and it will take longer than expected.

**Item 28 (the scraping / ToS question) needs a decision from a human rather than code from
anyone — and it is worth making that decision before investing further in the transcript
pipeline.**
