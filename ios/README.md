# Sava — iOS

Native SwiftUI client for the existing Sava backend. It is another **client** of
the same FastAPI service the web app uses — it does not duplicate any backend
logic, database, or AI processing.

## Requirements
- Xcode 16+ (built/verified on Xcode 26)
- iOS 17.0+ target

## Run
1. Start the Sava API (from the repo root):
   ```bash
   python3 -m uvicorn api.main:app --host 127.0.0.1 --port 8000
   ```
2. Open `ios/Sava.xcodeproj` and run on an iPhone simulator, or from the CLI:
   ```bash
   cd ios
   xcodebuild -project Sava.xcodeproj -scheme Sava \
     -destination 'platform=iOS Simulator,name=iPhone 17' build
   ```

## API base URL
Defaults to `http://127.0.0.1:8000` (matches `run_api.py`). Override without a
rebuild via either:
- Scheme environment variable `SAVA_API_BASE` (e.g. a LAN IP for device testing), or
- An `SAVA_API_BASE` key in `Info.plist`.

The simulator shares the host network, so `127.0.0.1` reaches a locally running
API. A local-networking ATS exception is set in `Info.plist` for HTTP during
development.

## Architecture
```
Sava/
  App/              App entry + root routing (splash / auth / signed-in shell)
  DesignSystem/     Tokens (color, type, spacing, motion, haptics) + components
  Core/
    Networking/     APIClient, Endpoint, APIError, AppConfig, client factory
    Security/       KeychainStore (auth token at rest)
    Images/         Cached async image pipeline + thumbnail proxy resolution
    Models/         Typed Codable models (Bookmark, Platform, transcript, …)
    Utilities/      Formatters, masonry layout, DEBUG dev flags
  Features/
    Launch/         Session-restore splash
    Auth/           Sign in / register (AuthService, SessionStore, VM, UI)
    Shell/          AppShell — glass tab bar + navigation (Library/Search/Profile)
    Library/        Real bookmark feed, cards, masonry grid, states
    Detail/         Bookmark detail — media, transcript, comments, metadata, Ask Sava
    Search/         Semantic-ish search over the q endpoint
    Save/           In-app quick save (paste a link)
    Capture/        Action Button: App Intent, App Shortcut, capture pipeline
    Profile/        Account + Action Button setup guide
```

### Backend integration (all real endpoints)
- **Auth**: JWT bearer against `/auth/login`, `/auth/register`, `/auth/me`. Token in
  Keychain; a 401 on any call cleanly returns to signed-out (30-min TTL, no refresh).
- **Library**: `GET /api/bookmarks` (`platform`, `q`, `limit`, `offset`).
- **Detail**: `POST /api/transcript` (YouTube/TikTok), `GET /api/comments/{id}`.
- **Search**: `GET /api/bookmarks?q=` (server-side ILIKE).
- **Save**: `POST /bookmarks` (backend ingests + dedupes), `DELETE /api/bookmarks/{id}`.
- All network access flows through `APIClient` — views never touch `URLSession`.
- Thumbnails from signed CDNs (Instagram/TikTok) route through the backend
  `/api/thumbnail` proxy; YouTube loads directly. Expired signed URLs fall back
  to a platform glyph.

### Action Button / Quick Save (`Features/Capture`)
`SaveToSavaIntent` (App Intent) + `SavaShortcuts` (App Shortcut) make "Save to Sava"
discoverable in Shortcuts/Spotlight/Siri and assignable to the Action Button. The
`CapturePipeline` strategy: **direct URL → screenshot resolution → clipboard →
graceful failure**. Screenshots are used only when no direct URL exists and are
never written to Photos. The screenshot resolver (`ContentResolverService`) is a
clean client boundary; its server endpoint (`POST /api/resolve`) and the per-video
Q&A endpoint (`POST /api/bookmarks/{id}/ask`, see `Intelligence.swift`) are **not yet
implemented server-side** — the UI shows honest "coming soon" states rather than
faking results. Flip `isEnabled` in each service when the endpoints ship.

### Dev/QA flags (DEBUG only, compiled out of Release)
Launch-environment hooks for driving a real account during QA:
`SAVA_DEV_TOKEN` (seed a bearer token), `SAVA_DEV_TAB`, `SAVA_DEV_SEARCH`,
`SAVA_DEV_OPEN_FIRST`. See `Core/Utilities/DevFlags.swift`.
