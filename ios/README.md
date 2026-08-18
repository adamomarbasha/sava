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
  App/              App entry + root routing (splash / auth / signed-in)
  DesignSystem/     Tokens (color, type, spacing, motion, haptics) + components
  Core/
    Networking/     APIClient, Endpoint, APIError, AppConfig
    Security/       KeychainStore (auth token at rest)
    Models/         Typed Codable models
  Features/
    Launch/         Session-restore splash
    Auth/           Sign in / register (AuthService, SessionStore, view model, UI)
    Home/           Signed-in placeholder (library is the next phase)
```

- Auth: JWT bearer against `/auth/login`, `/auth/register`, `/auth/me`. The token
  is stored in the Keychain; a 401 on any call cleanly returns to signed-out
  (the backend token has a 30-minute TTL with no refresh).
- All network access flows through `APIClient` — views never touch `URLSession`.
