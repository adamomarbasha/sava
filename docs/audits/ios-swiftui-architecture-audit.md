# Sava iOS — SwiftUI Architecture Audit

**Status:** Findings recorded. **Nothing in this document has been implemented.**
**Audit date:** 2026-08-18
**Branch at time of audit:** `feat/intelligence-foundation`
**Scope:** the entire `ios/` project (66 Swift files, ~4,211 LOC), plus `ios/Info.plist` and `ios/Sava.xcodeproj/project.pbxproj`.
**Method:** full read of every Swift file in `ios/Sava/`, the Info.plist, and the Xcode project build settings. Backend route auth requirements were cross-checked against `api/main.py` only where an iOS finding depended on them.
**Explicitly out of scope:** UI/visual redesign. The audit was asked not to redesign the UI, and it did not. Backend architecture was not audited; it is referenced only where the client couples to it.

---

## 0. Context for a future session with no memory of this work

Read this section first if you are picking this up cold.

Sava is a native SwiftUI iOS client (`ios/`) for an existing FastAPI backend (`api/`). It is *another client* of the same service the web app uses — it duplicates no backend logic, no database, and no AI processing. The product saves social-video links (TikTok / YouTube / Instagram / etc.), ingests them server-side, and surfaces transcripts, comments, and metadata.

Architecture as it exists today:

```
ios/Sava/
  App/              SavaApp (@main) + RootView (splash / auth / signed-in routing)
  DesignSystem/     Color, type, spacing, motion, haptics tokens + shared components
  Core/
    Networking/     APIClient, Endpoint, APIError, AppConfig, SavaClientFactory
    Security/       KeychainStore (auth token at rest)
    Images/         ImagePipeline actor, RemoteImage, ThumbnailURL proxy resolution
    Models/         Bookmark, Platform, AuthModels, ContentModels
    Utilities/       Formatters, Masonry, DevFlags
  Features/
    Launch/         Session-restore splash
    Auth/           AuthFlowView, AuthService, AuthViewModel, SessionStore
    Shell/          AppShell — custom glass tab bar + NavigationStacks
    Library/        LibraryView/ViewModel, BookmarkService, BookmarkGrid/Card, skeleton
    Detail/         BookmarkDetailView, DetailViewModel, ContentService, Intelligence,
                    Ask/Transcript/Comments/Metadata sections
    Search/         SearchView, SearchViewModel
    Save/           QuickSaveSheet, QuickSaveViewModel
    Capture/        SaveToSavaIntent (App Intent), SavaShortcuts, CapturePipeline,
                    ContentResolverService, CaptureModels
    Profile/        ProfileView (account + Action Button setup guide)
```

Key facts a future agent needs:

- **Single Xcode target.** `PRODUCT_BUNDLE_IDENTIFIER = com.sava.mobile`, `IPHONEOS_DEPLOYMENT_TARGET = 17.0`, `SWIFT_VERSION = 5.0`, `TARGETED_DEVICE_FAMILY = 1` (iPhone only), portrait only. Uses a `PBXFileSystemSynchronizedRootGroup`, so new files on disk are picked up automatically — there is no per-file entry to maintain in the pbxproj.
- **There is no iOS test target.** The `Sources` build phase in `project.pbxproj` is empty and no test target exists. `tests/` at the repo root is Python (pytest), unrelated to iOS.
- **The App Intent lives in the main app target**, not an extension. This is deliberate and correct on iOS 17 — it avoids needing a Keychain access group.
- **Two features are intentionally unshipped and stubbed honestly:** `IntelligenceService` (`POST /api/bookmarks/{id}/ask`) and `ContentResolverService` (`POST /api/resolve`). Both have `static let isEnabled = false` and present truthful "coming soon" UI rather than fabricated results. This is a deliberate product decision — see §5.
- **Severity vocabulary used here:** CRITICAL = ships broken or blocks release. HIGH = user-visible dead end, data race, memory risk, or a structural gap that blocks other work. MEDIUM = correctness/perf/privacy defect with a workaround or a limited blast radius. LOW = polish, hygiene, latent risk.

---

## 1. Launch blockers, security issues, and unresolved decisions (read first)

### Launch blockers — must be fixed before any TestFlight or App Store build
- **C1** — Action Button / Shortcuts / Siri saves are 100% broken (unauthenticated requests).
- **C2** — Release builds resolve the API base URL to `http://127.0.0.1:8000`; every request fails off-device.

### Security / privacy issues
- **H7** — Keychain write and delete failures are silently discarded. A failed sign-out delete leaves a live token on device.
- **H5** — Any 401, including on unauthenticated endpoints, triggers a global sign-out and Keychain wipe.
- **M14** — Sign-out leaves the previous account's recent searches (`UserDefaults`), in-memory `NSCache`, and 256MB on-disk `URLCache` intact. A second account inherits the first account's search history and cached thumbnails.
- **M12** — The Action Button intent reads `UIPasteboard` unconditionally, surfacing the iOS "Sava pasted from …" banner on every press even when the clipboard is never used.
- **M18** — The client is coded against two server routes that are currently unauthenticated. `GET /api/comments/{bookmark_id}` (`api/main.py:685-689`) has no `get_current_user` dependency, so any user's comments are readable by bookmark ID. This is a **server-side IDOR** and needs a separate backend ticket; the iOS side of it is only that the client should send the bearer token anyway.
- **M11** — `GET /api/thumbnail` (`api/main.py:492`) is an unauthenticated open URL-fetch proxy that the image pipeline depends on. Securing it (which should happen) will silently break every proxied Instagram/TikTok thumbnail unless the client is fixed first.
- **C2 (secondary)** — `NSAllowsLocalNetworking` is shipped in the Release `Info.plist`.

### Unresolved decisions — need a human call, not a code change
1. **Feature-flag mechanism.** `IntelligenceService.isEnabled` and `ContentResolverService.isEnabled` are source constants requiring a rebuild to flip. Decide whether these become build config, remote config, or a server capability response before either backend endpoint ships. (L7)
2. **Pagination strategy.** `BookmarkService.list` already plumbs `offset`, but `LibraryViewModel` hardcodes `limit: 500, offset: 0` and never paginates. Decide between infinite scroll, a hard cap with server-side search, or cursor pagination before the library grows. (L11 / M7)
3. **Swift 6 migration timing.** The codebase is *nearly* Swift 6 ready — every view model is `@MainActor`, `ImagePipeline` is a real actor. `APIClient` is essentially the only holdout. Decide whether to flip `SWIFT_STRICT_CONCURRENCY = complete` as part of the H3 fix or as a separate migration. (M17)
4. **Tab persistence approach.** M1's fix (TabView vs. a ZStack with opacity/zIndex) changes navigation and animation feel. The custom glass tab bar must be preserved either way; the mechanism underneath is the open question.
5. **Whether to secure `/api/thumbnail` and `/api/comments/{id}`** and on what timeline — this is a backend decision that the iOS fixes for M11 and M18 should be sequenced against.

### Future-phase items (not defects — deliberately deferred)
- `POST /api/resolve` — screenshot-assisted content resolution for YouTube/Instagram, where the on-screen URL is not reliably exposed. Client boundary exists (`ContentResolverService`), server does not implement it. `CapturePipeline` strategy step 2 is a no-op until then.
- `POST /api/bookmarks/{id}/ask` — per-video conversational Q&A. Client boundary exists (`IntelligenceService`), `AskSavaSection` renders a labeled "coming soon" state.
- Dynamic Type support across the design system (L6) — a single-file change in `SavaFont`, deferred because the audit was scoped away from UI work.

---

## 2. CRITICAL findings (current state)

### C1 — The Action Button intent sends every request unauthenticated. The flagship feature is 100% broken.
**Files:** `ios/Sava/Features/Capture/SaveToSavaIntent.swift:33`, `ios/Sava/Core/Networking/APIClient.swift:18`, `ios/Sava/Core/Networking/SavaClientFactory.swift:19-24`

`APIClient.tokenProvider` is declared `weak var`. `SavaClient.authenticated()` returns the provider as a tuple element *specifically so the caller retains it* — and the one and only caller discards it:

```swift
let (client, _) = SavaClient.authenticated()   // provider deallocates immediately
```

The `_` sub-binding creates no binding, so `KeychainTokenProvider` is released at the end of that statement and `client.tokenProvider` becomes `nil`. `buildRequest` (`APIClient.swift:122`) then skips the `Authorization` header.

**Failure chain:** every Action Button save hits `POST /bookmarks`, which requires `get_current_user` (`api/main.py:287`) → 401 → `APIError.unauthorized` → wrapped by `CapturePipeline.run` into `CaptureError.saveFailed(error.userMessage)` → the user, who *is* signed in with a valid Keychain token, is told "Your session expired. Please sign in again." Every single time.

**Why it was not caught:** the in-app path works, because `SessionStore` conforms to `AuthTokenProviding` and is retained by `SavaApp` as a `@StateObject`. That masks the ownership flaw on the only path that exercises it.

### C2 — Release builds ship pointing at `http://127.0.0.1:8000`.
**Files:** `ios/Sava/Core/Networking/AppConfig.swift:10-15`, `ios/Info.plist`, `ios/Sava.xcodeproj/project.pbxproj:200-227`

`AppConfig.apiBaseURL` resolves in this order: `ProcessInfo.processInfo.environment["SAVA_API_BASE"]` → `Bundle.main.object(forInfoDictionaryKey: "SAVA_API_BASE")` → hardcoded `URL(string: "http://127.0.0.1:8000")!`.

There is no xcconfig or scheme separation between Debug and Release, and no `SAVA_API_BASE` key in the shipped `Info.plist`. Launch environment variables are not populated for App Store launches. Therefore a TestFlight or App Store build falls through to `127.0.0.1` and every request fails.

Compounding: `NSAllowsLocalNetworking` is present in the Release `Info.plist`, permitting cleartext local HTTP in a shipped binary with no reason to.

This is a hard release blocker, not a configuration nicety.

---

## 3. HIGH findings (current state)

### H1 — Quick Save's button is permanently disabled after the first failure.
**File:** `ios/Sava/Features/Save/QuickSaveViewModel.swift:15-18, 43-46`

```swift
var canSave: Bool {
    guard case .editing = phase else { return false }   // .failed → false, forever
    return CapturedLink(rawURL: urlText, source: .direct) != nil
}
```

`save()` sets `phase = .failed(...)` on error. Nothing returns it to `.editing` — not editing `urlText`, not `onSubmit`, not tapping the button (`save()` itself re-guards on `canSave`). `reset()` exists on the view model but is never called from `QuickSaveSheet`. One transient network blip turns the sheet into a dead end showing an error with a disabled button, recoverable only by dismissing and reopening.

### H2 — The transcript "Tap to retry" button does nothing.
**Files:** `ios/Sava/Features/Detail/DetailViewModel.swift:31`, `ios/Sava/Features/Detail/TranscriptSection.swift:38-44, 51-54`

`loadTranscript` guards `transcriptState == .idle`. On failure the state is `.failed(message)`, so the retry path returns instantly without doing anything. The source comment in `TranscriptSection.reload()` — `"Reset by loading again (state guards on .idle, so nudge through)"` — shows the author saw the problem, but the nudge was never written.

### H3 — `APIClient` has real data races and is not `Sendable`.
**File:** `ios/Sava/Core/Networking/APIClient.swift:15-40`

`send` / `perform` are `nonisolated async` on a plain `final class`. Calling them from a `@MainActor` view model hops to the cooperative thread pool (a nonisolated async function does **not** inherit the caller's actor), so concurrent calls execute on different threads against shared mutable state:

- `private lazy var decoder` (line 20) — Swift `lazy var` initialization is not atomic; two concurrent first-decodes can race and double-initialize or tear.
- `weak var tokenProvider` (line 18) — mutable and unsynchronized, plus weak-reference load/store traffic on every request.

This is exercised on a real path, not hypothetically: `BookmarkDetailView` renders `TranscriptSection` and `CommentsSection` as siblings, and each attaches its own `.task`, so `ContentService.transcript` and `ContentService.savedComments` run concurrently through the same client instance.

Swift 5 language mode with `SWIFT_STRICT_CONCURRENCY` unset is hiding all of it. Enabling strict concurrency will produce a wall of errors concentrated in this one file.

### H4 — Unbounded image memory; no downsampling.
**File:** `ios/Sava/Core/Images/ImageLoader.swift:10-32`

`NSCache` sets `countLimit = 200` and passes a `cost:` on insert, but **never sets `totalCostLimit`** — so the cost accounting is inert and the only bound is the object count. 200 full-resolution decoded YouTube/TikTok thumbnails displayed at ~180pt is easily 300MB+ resident.

Nothing downsamples to the target size (no `CGImageSourceCreateThumbnailAtIndex`, no `UIImage.preparingThumbnail(of:)`). Full-size images are decoded and held. On a 500-item library this is the most likely jetsam/OOM source in the app.

### H5 — Any 401 triggers a global sign-out, including a wrong password on the login screen.
**Files:** `ios/Sava/Core/Networking/APIClient.swift:88`, `ios/Sava/Features/Auth/AuthService.swift:8-17`, `ios/Sava/Features/Auth/SessionStore.swift:39-43`

```swift
case 401:
    await tokenProvider?.handleUnauthorized()   // fires regardless of endpoint.requiresAuth
    throw APIError.unauthorized
```

`POST /auth/login` is sent with `requiresAuth: false`. A wrong password returns 401 → `handleUnauthorized()` → `signOut()` → Keychain delete + `withAnimation` phase change to `.signedOut`.

Today the visible effect is benign (the user was already signed out), but the session is being torn down by an endpoint that was never authenticated in the first place. It will bite immediately when token refresh, multi-account, or any other unauthenticated route lands.

### H6 — There is no iOS test target, and `APIClient` cannot be faked.
**Files:** `ios/Sava.xcodeproj/project.pbxproj:110-118` (empty `Sources` phase, no test target), `ios/Sava/Core/Networking/APIClient.swift:33-40`

```swift
init(baseURL: URL = AppConfig.apiBaseURL) {
    self.baseURL = baseURL
    let config = URLSessionConfiguration.default
    ...
    self.session = URLSession(configuration: config)   // constructed internally, no seam
}
```

No `URLSessionConfiguration` parameter, no protocol abstraction. Services (`BookmarkService`, `AuthService`, `ContentService`) take a concrete `APIClient`, so there is nowhere to substitute a stub.

The view models are otherwise **excellently** positioned for testing — every one is `@MainActor`, takes its service as a method parameter rather than constructing it, and has an explicit state enum to assert against. A single injection point at `APIClient` unlocks all of them. `tests/` at the repo root is Python only and does not cover iOS.

### H7 — Keychain writes fail silently.
**File:** `ios/Sava/Core/Security/KeychainStore.swift:26-31, 58`

`SecItemUpdate`, `SecItemAdd`, and `SecItemDelete` return codes are all discarded (`SecItemAdd(insert as CFDictionary, nil)` with no status capture; `SecItemDelete` likewise).

If `SecItemAdd` fails — e.g. `errSecInteractionNotAllowed`, which is reachable when the App Intent runs before first unlock — `finishSignIn` proceeds to `.signedIn`, the user appears authenticated, and the token is silently absent on next launch, or worse, a stale token remains. A `delete` that fails on sign-out leaves a live token on device, which is a security issue rather than a bug.

---

## 4. MEDIUM findings (current state)

### M1 — Switching tabs destroys the navigation stacks and all Search/Detail state.
**File:** `ios/Sava/Features/Shell/AppShell.swift:40-49`

```swift
@ViewBuilder private var tabContent: some View {
    switch tab {
    case .library: NavigationStack(path: $libraryPath) { LibraryView().bookmarkDestination() }
    case .search:  NavigationStack { SearchView().bookmarkDestination() }
    case .profile: NavigationStack { ProfileView(user: user).bookmarkDestination() }
    }
}
```

Only the selected stack exists. Consequences:
- `SearchView`'s `@StateObject var model` is deallocated on every tab switch — query, results, and any in-flight task are lost.
- The Search and Profile stacks have no `path` binding, so any pushed detail view is destroyed and cannot be restored.
- Library scroll position resets.
- `LibraryViewModel` survives only because it was hoisted to `AppShell` as a `@StateObject` and guarded by `hasLoaded` — an accidental save, not a systematic one.

This is the single highest-value structural change available in the app.

### M2 — Submitted searches are uncancellable and race the debouncer.
**File:** `ios/Sava/Features/Search/SearchViewModel.swift:38-44`

```swift
func submit(_ service: BookmarkService) {
    searchTask?.cancel()
    ...
    Task { await run(trimmed, service: service) }   // never assigned to searchTask
}
```

The task is unreachable, so the `guard !Task.isCancelled` inside `run` (line 60) can never fire for it. A slow submitted request can land after a newer debounced one and overwrite fresher results. Additionally, `searchTask` is never cancelled in `deinit`, so a dying view model (which happens on every tab switch — see M1) keeps itself alive until the task finishes.

### M3 — Unparseable dates silently become `.distantPast` and render in the UI.
**File:** `ios/Sava/Core/Networking/APIClient.swift:22-30`

```swift
return SavaDateParsing.parse(raw) ?? .distantPast
```

The intent — never fail a whole payload because one timestamp is oddly shaped — is sound. The mechanism is wrong. `Format.relativeAge` then prints "2025 years ago" on the card (`BookmarkCard.swift:109`) and `MetadataSection` prints "Saved Jan 1, 1" (`MetadataSection.swift:15`). A wrong date is worse than a missing one.

### M4 — Every cached image flickers through the shimmer.
**File:** `ios/Sava/Core/Images/RemoteImage.swift:31-38`

```swift
private func load() async {
    image = nil            // clears before the hop
    failed = false
    guard let url else { failed = true; return }
    if let hit = await ImagePipeline.shared.cached(url) { image = hit; return }   // actor hop
```

`ImagePipeline` is an actor, so `cached` is `async` and there is no synchronous fast path — even a guaranteed cache hit renders at least one frame of placeholder, and `image = nil` guarantees the old content is torn down first. Combined with the actor-hop cost on every cell recycle, this is why scrolling shimmers. `NSCache` is already thread-safe, so a `nonisolated` synchronous probe is available.

### M5 — `UIScreen.main` and a dynamic-range `ForEach` in the grid.
**File:** `ios/Sava/Features/Library/BookmarkGrid.swift:13, 37`

- `UIScreen.main.bounds.width` (line 37) is deprecated in iOS 16+ and wrong under rotation, iPad, and Stage Manager. It is the *only* `UIScreen` usage in the codebase.
- `ForEach(0..<columns.count, id: \.self)` (line 13) over a non-constant range is unsupported; SwiftUI's constant-range `ForEach` assumes the count never changes.

### M6 — `LiquidBackground` regenerates view identities on every body evaluation.
**File:** `ios/Sava/DesignSystem/Components/LiquidBackground.swift:15, 22-28`

`Blob.id = UUID()` is a default initializer inside a **computed** `blobs` array, so `ForEach(blobs)` receives three brand-new identities each time the body runs. SwiftUI tears down and rebuilds three 460pt circles with a 90pt blur, and the ambient `withAnimation` driven from `.onAppear` is attached to views that no longer exist — which is why the ambient drift may not animate at all. Make `blobs` a `static let` (or key the `ForEach` by index).

### M7 — Library derived state is recomputed O(n) per render, over an unpaginated 500-item load.
**File:** `ios/Sava/Features/Library/LibraryViewModel.swift:22-36, 46`

- `availablePlatforms` builds a `Dictionary(grouping: all, by:)` plus a sort.
- `count(for:)` filters `all` once per filter pill.
- `visible` filters `all`.

All of these are invoked from `LibraryView.body` (`LibraryView.swift:37, 58-70, 106`). With `limit: 500` hardcoded and `offset` never used, that is roughly ten full passes over 500 items on every render.

### M8 — `RelativeDateTimeFormatter` allocated per call.
**File:** `ios/Sava/Core/Utilities/Formatters.swift:32-37`

Called twice per `BookmarkCard` body (`BookmarkCard.swift:106, 109`). Formatter construction is expensive. Note that `SavaDateParsing` in `APIClient.swift` already does this correctly with `static let` formatters — the pattern exists in the codebase, it just was not applied here.

### M9 — Unstable `Identifiable` IDs.
**File:** `ios/Sava/Core/Models/ContentModels.swift:20, 52`

- `SavedComment.id` falls back to `Int.random(in: Int.min...Int.max)` when the server omits `id`, producing a new identity on every decode — SwiftUI rebuilds the entire comment list on any refresh.
- `TranscriptSegment.id` is `"\(start)-\(text.hashValue)"`. `hashValue` is seeded per process launch (stable within a run, not across), and repeated lines at the same offset collide.

### M10 — A `.sheet` attached inside a conditional switch branch.
**File:** `ios/Sava/Features/Detail/TranscriptSection.swift:91-93`

The `.sheet(isPresented: $showReader)` modifier lives on the view returned by `loaded(_:language:)`, which only exists in the `.loaded` case of a `switch` inside a `Group`. Any state transition changes that subtree's identity and can dismiss the sheet or drop the binding.

### M11 — The image pipeline bypasses the networking boundary entirely.
**Files:** `ios/Sava/Core/Images/ImageLoader.swift:19, 26`, `ios/Sava/Core/Images/ThumbnailURL.swift:25-27`

`ThumbnailURL` correctly routes signed/referer-gated Instagram and TikTok CDN URLs through the backend's `GET /api/thumbnail?url=` proxy. But `ImagePipeline` uses its own `URLSession` with no `tokenProvider`, no `APIError` mapping, and no 401 handling. It works today only because `GET /api/thumbnail` (`api/main.py:492`) happens to be unauthenticated — and it is an open URL-fetch proxy that should be secured. When it is, every proxied thumbnail breaks with no diagnosable error path.

### M12 — The intent reads the clipboard on every run, even when it doesn't need it.
**File:** `ios/Sava/Features/Capture/SaveToSavaIntent.swift:41-44`

```swift
let clipboard = UIPasteboard.general.hasURLs
    ? UIPasteboard.general.url?.absoluteString
    : UIPasteboard.general.string
```

This is evaluated **unconditionally**, before `pipeline.run`, even though `CapturePipeline.resolve` only consults the clipboard as strategy #3 (after direct URL and screenshot resolution). `hasURLs` is free, but `.url` / `.string` are not: on iOS 16+ they surface the system "Sava pasted from Safari" banner on every single Action Button press — directly undermining the "press and keep scrolling" product design.

### M13 — A transient `/auth/me` failure destroys a valid token.
**File:** `ios/Sava/Features/Auth/SessionStore.swift:94-98`

`finishSignIn` deletes the freshly-issued token if `me()` throws for *any* reason, including `.offline` or `.timedOut`. The intent (don't leave a half-authenticated state) is right; the trigger condition is too broad. A user signing in on a flaky connection loses a perfectly good token.

### M14 — Cross-user residue after sign-out.
**Files:** `ios/Sava/Features/Auth/SessionStore.swift:81-84`, `ios/Sava/Features/Search/SearchViewModel.swift:15-19`, `ios/Sava/Core/Images/ImageLoader.swift:10-19`

`signOut()` deletes the Keychain token and nothing else. Recent searches live in a global `UserDefaults` key (`"sava.recentSearches"`), and both the `NSCache` and the 256MB on-disk `URLCache` retain the previous account's thumbnails. Sign in as a second account and you inherit the first account's search history.

### M15 — A synchronous Keychain read on every single request.
**File:** `ios/Sava/Features/Auth/SessionStore.swift:35-37`

```swift
nonisolated var currentToken: String? { KeychainStore().read(.authToken) }
```

Constructs a new `KeychainStore` and performs a `SecItemCopyMatching` syscall per request, on the cooperative thread pool. It is correct and thread-safe — and the `nonisolated` annotation with its documented rationale is a deliberate, sound choice — but it makes a syscall the hot path for token injection.

### M16 — DI is ad hoc: services are constructed in view computed properties.
**Files:** `ios/Sava/Features/Library/LibraryView.swift:9`, `ios/Sava/Features/Search/SearchView.swift:10`, `ios/Sava/Features/Save/QuickSaveSheet.swift:13`, `ios/Sava/Features/Detail/BookmarkDetailView.swift:13`

Four views each do `SomeService(client: session.api)`, which forces every one of them to hold an `@EnvironmentObject var session: SessionStore` purely to reach the client. Cheap at runtime (the services are structs), but the view layer knows the wiring and there is nowhere to substitute a fake.

### M17 — `SWIFT_STRICT_CONCURRENCY` is unset and the project is Swift 5 mode.
**File:** `ios/Sava.xcodeproj/project.pbxproj:196, 223`

`SWIFT_VERSION = 5.0` with no strict-concurrency setting means H3 and the `Sendable` gaps compile clean. The codebase is otherwise close to Swift 6 ready.

### M18 — The client relies on server routes that aren't authenticated.
**File:** `ios/Sava/Features/Detail/ContentService.swift:14, 23`

Both `POST /api/transcript` and `GET /api/comments/{bookmark_id}` are sent with `requiresAuth: false`, which accurately mirrors the backend today (`api/main.py:685-689` has no `get_current_user`). But it means the client is coded *against* a server-side IDOR: any user's comments are readable by bookmark ID. See §1 Security.

---

## 5. LOW findings (current state)

| # | Issue | File |
|---|---|---|
| L1 | `Endpoint.json` uses `try?` on `JSONEncoder().encode` — an encode failure silently sends a bodyless request | `Core/Networking/Endpoint.swift:25` |
| L2 | `CancellationError` escapes `SaveToSavaIntent`'s `catch let error as CaptureError` and surfaces as a generic Shortcuts failure | `Features/Capture/SaveToSavaIntent.swift:51` |
| L3 | The `onReceive(library.$all)` DEBUG hook subscribes in Release too — the `DevFlags.openFirst` guard is constant-false, but the Combine subscription itself is not elided | `Features/Shell/AppShell.swift:34-37` |
| L4 | `ShimmerPlaceholder` runs `.repeatForever` animations that never stop, including off-screen and after the image loads; `LibrarySkeleton` instantiates 24 of them | `Core/Images/RemoteImage.swift:83`, `Features/Library/LibrarySkeleton.swift` |
| L5 | The whole `Bookmark` value is the `NavigationPath` element; an ID + lookup is more robust to model refresh | `Features/Shell/AppShell.swift:13, 89` |
| L6 | Every font is a fixed `.system(size:)` — no Dynamic Type scaling anywhere. Single fix point in `SavaFont` | `DesignSystem/SavaTypography.swift` |
| L7 | Feature flags are `static let isEnabled = false` source constants requiring a rebuild, and produce dead-code warnings | `Features/Detail/Intelligence.swift:17`, `Features/Capture/ContentResolverService.swift:19` |
| L8 | `a.hasPrefix("@") ? a : a` — both ternary branches are identical (dead logic) | `Core/Models/Bookmark.swift:55` |
| L9 | `base.absoluteString + raw` can produce a double slash if the base URL ends in `/` | `Core/Images/ThumbnailURL.swift:18` |
| L10 | `APIError` does not conform to `LocalizedError`, so `localizedDescription` is useless in logs and crash reports | `Core/Networking/APIError.swift:5` |
| L11 | `offset` is plumbed through `BookmarkService.list` but never used — no pagination | `Features/Library/BookmarkService.swift:11`, `LibraryViewModel.swift:46` |
| L12 | Session restore has no timeout affordance — a hung network holds `LaunchView` for the full 20s request timeout | `App/RootView.swift:23`, `Features/Auth/SessionStore.swift:48` |
| L13 | `QuickSaveSheet` gets a redundant `.environmentObject(session)`; modern SwiftUI sheets already inherit it | `Features/Shell/AppShell.swift:31` |

---

## 6. Recommendations — safest remediation order

**These are recommendations only. None have been implemented.**

Ordered so each wave is independently shippable and no step depends on a later one. Waves 1–2 are contained bug fixes; the structural work comes only after there is a test seam.

### Wave 1 — release blockers, no structural change (~half a day)
1. **C1** — retain the provider in `SaveToSavaIntent`, or (preferred, fixes the class of bug) have `SavaClient.authenticated()` return a client that strongly holds its provider so the ownership contract is not the caller's problem.
   *Acceptance:* a real Action Button press on a signed-in device saves a bookmark and returns the "Saved …" dialog. Verify the `Authorization` header is present, not just that the call succeeds.
2. **C2** — add `Debug.xcconfig` / `Release.xcconfig` with `SAVA_API_BASE`, inject via `Info.plist`, and make the localhost fallback `#if DEBUG` only so a Release build with no configured base **fails loudly** rather than silently hitting localhost. Remove `NSAllowsLocalNetworking` from Release.
   *Acceptance:* a Release archive resolves to the production host; a Release build with the key removed fails visibly at first request rather than hanging.
3. **H1** — reset `phase` to `.editing` in `QuickSaveViewModel` when `urlText` changes (`didSet` or an explicit call from the view).
   *Acceptance:* fail a save with airplane mode on, re-enable networking, edit the URL, and save successfully without dismissing the sheet.
4. **H2** — give `DetailViewModel` an explicit `retryTranscript()` that clears state to `.idle` before delegating to `loadTranscript`.
   *Acceptance:* force a transcript failure, tap retry, observe a second network request.
5. **H7** — capture and propagate `OSStatus` from `KeychainStore`; make `save` throwing and surface failures in `finishSignIn`.
   *Acceptance:* a simulated `SecItemAdd` failure prevents the `.signedIn` transition rather than producing a phantom session.

### Wave 2 — correctness and safety, still localized (1–2 days)
6. **H5** — gate `handleUnauthorized()` on `endpoint.requiresAuth` in `APIClient.perform`.
7. **M13** — only discard the token in `finishSignIn` when the error is `.unauthorized`.
8. **M3** — decode timestamps as `String?` and parse to `Date?` at the model layer; delete the `.distantPast` fallback so a bad date renders as *no* date.
9. **M12** — make the intent's clipboard read lazy (pass a `() -> String?` closure into `CapturePipeline.run`) so it fires only when the fallback is actually reached.
   *Acceptance:* an Action Button press with a direct `link` parameter produces no paste banner.
10. **M14** — add a `SessionStore.teardown()` that clears the recents `UserDefaults` key, the `NSCache`, and the `URLCache` on sign-out.
11. **M2** — assign the submit task to `searchTask`; cancel it in `deinit`.

### Wave 3 — the test seam (do this before any refactor)
12. **H6** — add a `SavaTests` target; add `init(baseURL:configuration:)` to `APIClient` and drive tests through a `URLProtocol` stub. Then extract `BookmarkServing` / `AuthServing` / `ContentServing` protocols. **With this in place, everything below becomes verifiable rather than hopeful.**
    *Suggested first tests:* `CapturePipeline`'s four-way strategy ladder (it is nearly pure and is the highest-value target), then each view model's state-enum transitions including the cancellation arms.
13. **M16** — introduce a service container in the environment; drop `@EnvironmentObject var session` from `LibraryView`, `SearchView`, `QuickSaveSheet`, and `BookmarkDetailView`.

### Wave 4 — concurrency hardening (requires Wave 3)
14. **H3** — make `APIClient` an `actor`, or make it immutable and `Sendable` (eager `let decoder`, provider supplied at `init`). Small change, wide blast radius — hence the test target first.
15. **M17** — flip `SWIFT_STRICT_CONCURRENCY = complete`, fix the fallout, then move to Swift 6 language mode.
16. **M15** — in-memory token cache behind the Keychain (a small actor or a `nonisolated(unsafe)` box), refreshed on sign-in/sign-out, with the Keychain remaining the durable store.

### Wave 5 — performance and memory (independent of Waves 3–4; can run in parallel)
17. **H4** — set `totalCostLimit` on the `NSCache` and downsample via `UIImage.preparingThumbnail(of:)` (or `CGImageSourceCreateThumbnailAtIndex`) at the target display size.
    *Acceptance:* scrolling a 500-item library keeps resident memory bounded; measure before/after in Instruments.
18. **M4** — add a synchronous `nonisolated` cache probe in `RemoteImage` and stop nilling `image` before the hop.
    *Acceptance:* scrolling back over already-loaded cells shows no shimmer frame.
19. **M6** — hoist `LiquidBackground.blobs` to a `static let`.
20. **M7 / M8** — cache library filter counts in a `@Published` recomputed when `all` changes; hoist the relative-date formatter to a `static let`.
21. **M5** — replace `UIScreen.main` with a geometry-derived width; fix the dynamic `ForEach` range (enumerate the array instead of using a range).

### Wave 6 — structural (highest risk, do last)
22. **M1** — replace the `switch`-based `tabContent` with a `TabView` (custom glass bar retained via `.toolbar(.hidden, for: .tabBar)`), give each tab its own persistent path binding, and move `SearchViewModel` ownership up to `AppShell` alongside `LibraryViewModel`. This is the change most likely to disturb navigation and animation behavior, so it belongs after everything else is stable and covered by tests.
    *Acceptance:* switch tabs mid-search and return — query, results, and scroll position all survive; a pushed detail view in the Search tab survives a round trip to Library.
23. **M10 / M9 / M11 / M18** — hoist the sheet out of the switch branch; stable `Identifiable` IDs; route thumbnails through the auth boundary; send the bearer token on `ContentService` calls.

---

## 7. What is already right — do NOT rewrite this

These are genuinely good decisions. Several are better than what a typical rewrite would produce, and touching them would cost correctness. A future agent should treat this list as protected.

**The networking boundary is correct and should be preserved in shape.** `Endpoint` + `APIClient.send` + typed service structs is the right factoring. `URLSession` appears in zero views and zero view models — verified across all 66 files. Keep the boundary; change only `APIClient`'s internals (isolation and injection).

**`APIError` is a genuinely good error model.** One exhaustive enum, status-code-aware construction, a `userMessage` split cleanly from the developer-facing detail, and `Self.detail(from:)` correctly handling FastAPI's dual-shape `{"detail": ...}` — both the plain string form *and* the validation-error array-of-objects form. This is more careful than most production apps. Do not replace it with `LocalizedError` boilerplate; just add the conformance (L10).

**Cancellation handling is unusually disciplined.** `URLError.cancelled` is explicitly translated to `CancellationError` at the boundary (`APIClient.swift:74`), and every view model has a distinct `catch is CancellationError` arm that deliberately does *not* set an error state — `LibraryViewModel:50`, `SearchViewModel:62`, `DetailViewModel:40, 57`, `AuthViewModel:74`. Most codebases get this wrong and flash spurious errors on every navigation. Keep all of it.

**`@MainActor` placement is correct everywhere.** Every view model and `SessionStore` are actor-annotated, `ImagePipeline` is a real `actor`, and `SessionStore.currentToken` is deliberately `nonisolated` with a documented rationale. The isolation *design* is right; only `APIClient` is missing from the story (H3).

**The state-machine enums are the right pattern and should not be collapsed.** `SessionStore.Phase`, `LibraryViewModel.LoadState`, `SearchViewModel.State`, `QuickSaveViewModel.Phase`, and especially `DetailViewModel`'s *two independent* states — `TranscriptState` distinguishing `.unsupported` vs `.unavailable(reason)` vs `.failed(message)` — model reality precisely. This is what lets the UI be honest about what is actually available rather than showing a generic spinner-or-error. Do **not** reduce these to `isLoading: Bool` + `error: String?`.

**Optimistic delete with rollback** (`LibraryViewModel.delete`, lines 78-92) captures `previous`, mutates optimistically, and restores on failure with matching success/error haptics. Textbook. Leave it.

**`Bookmark`'s lenient decoding is a deliberate, correct choice.** The custom `init(from:)` with per-field `try?`, and the `meta.videoID != nil` sentinel for "the backend sends an empty object for non-YouTube items", is exactly right against a loosely-typed FastAPI backend. The `platformRaw` / `Platform` split, with `Platform.init(rawValue:)` falling back to `.other` rather than failing, means a new platform added server-side will not crash the client. Keep the shape; fix only the date strategy (M3) and the random-ID fallback (M9).

**`Platform` as a single source of display truth** — `displayName`, `tint`, `symbol`, `prefersPortrait` all colocated — means no view hardcodes per-platform styling. Verified: no view does.

**The design system is real and consistently used.** `SavaColors` uses proper `UIColor { traits in ... }` dynamic providers rather than `@Environment(\.colorScheme)` branching, plus a spacing scale, a motion vocabulary (`SavaMotion`), and centralized `Haptics`. There are zero hardcoded hex values in any feature file. Its one gap is Dynamic Type (L6), a single-file fix — not a reason to rewrite the system.

**Accessibility is threaded through properly.** `accessibilityReduceMotion` is honored in four independent places — `ProcessingPill`, `ShimmerPlaceholder`, `LiquidBackground`, `SaveSuccessView`. Decorative surfaces are `accessibilityHidden(true)`. The custom tab bar sets `.isSelected` / `.isButton` traits correctly, and `AskSavaSection` combines children with an honest composite label.

**The "honest boundary" pattern for unshipped backends is a deliberate product decision — preserve it.** `IntelligenceService` and `ContentResolverService` each define the real contract, document the exact planned route and payload shape in comments, and refuse rather than fabricate — with UI that says "coming soon" instead of showing invented AI text. This is the right call both product-wise and ethically. The only change worth making is moving `isEnabled` from a source constant to build or remote config (L7).

**`CapturePipeline`'s strategy ladder** (direct URL → screenshot resolve → clipboard → graceful failure) is a clean, nearly-pure struct with the policy in one place and no UI coupling. Once `APIClient` is injectable this becomes trivially unit-testable, and it is the single piece most worth writing tests against first.

**`DevFlags` is correctly compiled out of Release.** Both branches of the `#if DEBUG` are present with matching signatures, and `SAVA_DEV_TOKEN` in `SessionStore.restore()` is properly fenced. This is the right way to build QA hooks. The only leak is the Combine subscription in `AppShell` (L3), which is cosmetic.

**Keychain accessibility class is correctly chosen.** `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` is precisely right for an App Intent that must read the token while the device is locked, without syncing to iCloud. Someone thought about this. Fix the unchecked status codes (H7) but leave the policy alone.

**Single-target architecture for App Intents is correct on iOS 17.** Keeping `SaveToSavaIntent` in the app target rather than an extension avoids needing a Keychain access group entirely. `SavaShortcuts` uses `.applicationName` interpolation properly, so Action Button assignment works. The App Intent *design* is sound — only the token wiring (C1) is broken.

**`SavaDateParsing`** already demonstrates the correct `static let` formatter pattern with a sensible fallback chain (ISO8601 fractional → ISO8601 plain → six naive `DateFormatter`s pinned to `en_US_POSIX` / UTC). Use it as the model when fixing M8.

---

## 8. Finding index (quick reference)

| ID | Severity | One-line | Primary file |
|---|---|---|---|
| C1 | CRITICAL | Intent sends unauthenticated requests; Action Button 100% broken | `Features/Capture/SaveToSavaIntent.swift:33` |
| C2 | CRITICAL | Release builds point at `127.0.0.1`; no prod config | `Core/Networking/AppConfig.swift:10-15` |
| H1 | HIGH | Quick Save button permanently disabled after any failure | `Features/Save/QuickSaveViewModel.swift:15-18` |
| H2 | HIGH | Transcript "Tap to retry" is a no-op | `Features/Detail/DetailViewModel.swift:31` |
| H3 | HIGH | `APIClient` data races; not `Sendable` | `Core/Networking/APIClient.swift:15-40` |
| H4 | HIGH | Unbounded image memory; no downsampling | `Core/Images/ImageLoader.swift:10-32` |
| H5 | HIGH | Any 401 triggers global sign-out | `Core/Networking/APIClient.swift:88` |
| H6 | HIGH | No test target; `APIClient` not injectable | `Sava.xcodeproj/project.pbxproj:110-118` |
| H7 | HIGH | Keychain write/delete failures silently ignored | `Core/Security/KeychainStore.swift:26-31` |
| M1 | MEDIUM | Tab switch destroys NavigationStacks and Search state | `Features/Shell/AppShell.swift:40-49` |
| M2 | MEDIUM | Submitted search uncancellable; races debounce | `Features/Search/SearchViewModel.swift:38-44` |
| M3 | MEDIUM | Bad dates become `.distantPast` and reach the UI | `Core/Networking/APIClient.swift:22-30` |
| M4 | MEDIUM | Cached images still flash the shimmer | `Core/Images/RemoteImage.swift:31-38` |
| M5 | MEDIUM | `UIScreen.main` + dynamic-range `ForEach` | `Features/Library/BookmarkGrid.swift:13, 37` |
| M6 | MEDIUM | `LiquidBackground` regenerates UUIDs per body eval | `DesignSystem/Components/LiquidBackground.swift:15` |
| M7 | MEDIUM | O(n) derived state per render; unpaginated 500-item load | `Features/Library/LibraryViewModel.swift:22-36` |
| M8 | MEDIUM | `RelativeDateTimeFormatter` allocated per call | `Core/Utilities/Formatters.swift:32-37` |
| M9 | MEDIUM | Unstable `Identifiable` IDs (random / `hashValue`) | `Core/Models/ContentModels.swift:20, 52` |
| M10 | MEDIUM | `.sheet` attached inside a switch branch | `Features/Detail/TranscriptSection.swift:91` |
| M11 | MEDIUM | Image pipeline bypasses `APIClient` (no auth, no error mapping) | `Core/Images/ImageLoader.swift:19, 26` |
| M12 | MEDIUM | Intent reads clipboard on every run → paste banner | `Features/Capture/SaveToSavaIntent.swift:41-44` |
| M13 | MEDIUM | Transient `/auth/me` failure destroys a valid token | `Features/Auth/SessionStore.swift:94-98` |
| M14 | MEDIUM | Cross-user residue: recents, `NSCache`, `URLCache` | `Features/Auth/SessionStore.swift:81-84` |
| M15 | MEDIUM | Keychain syscall per request | `Features/Auth/SessionStore.swift:35-37` |
| M16 | MEDIUM | Services constructed in view computed props; no DI seam | `Features/Library/LibraryView.swift:9` |
| M17 | MEDIUM | `SWIFT_STRICT_CONCURRENCY` unset; Swift 5 mode | `Sava.xcodeproj/project.pbxproj:196, 223` |
| M18 | MEDIUM | Client codes against unauthenticated server routes (IDOR) | `Features/Detail/ContentService.swift:14, 23` |
| L1–L13 | LOW | See §5 table | various |
