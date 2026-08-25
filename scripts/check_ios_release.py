#!/usr/bin/env python3
"""Refuse an iOS Release configuration that cannot work in production.

Runs without Xcode, so it gates every pull request in seconds rather than only a
release build. It checks the three things that build cleanly, install fine, and
then fail for every real user:

  1. An API origin that is http, localhost, or a LAN address — works perfectly on
     the developer's Wi-Fi and times out everywhere else.
  2. An App Transport Security exception shipping in Release.
  3. A missing or inconsistent privacy manifest, which App Store Connect rejects.

The placeholder origin is *reported, not failed*. It is the correct value until
the backend is deployed, and failing on it would block all normal development;
`ios/Scripts/validate-api-config.sh` already refuses it at Release build time,
which is the right place for that check.
"""
from __future__ import annotations

import plistlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RELEASE_PLIST = REPO / "ios" / "Info-Release.plist"
MANIFEST = REPO / "ios" / "Sava" / "PrivacyInfo.xcprivacy"
PLACEHOLDER = "REPLACE_WITH_PRODUCTION_API_URL"

# Loopback, link-local, and the three RFC1918 ranges.
PRIVATE = re.compile(
    r"(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\.local\b"
    r"|10\.\d+\.\d+\.\d+"
    r"|192\.168\.\d+\.\d+"
    r"|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)", re.I)

problems: list[str] = []
notes: list[str] = []


def check_origin(plist: dict) -> None:
    url = (plist.get("SAVA_API_BASE_URL") or "").strip()
    print(f"Release API origin: {url or '<unset>'}")

    if not url:
        problems.append("SAVA_API_BASE_URL is unset in ios/Info-Release.plist")
        return
    if url == PLACEHOLDER:
        notes.append("Origin is still the placeholder; Release builds are "
                     "blocked until a real HTTPS origin is set.")
        return
    if not url.startswith("https://"):
        problems.append(f"Release API origin must be HTTPS, got {url!r}")
    if PRIVATE.search(url):
        problems.append(f"Release API origin is a private/local address: {url}")


def check_ats(plist: dict) -> None:
    if plist.get("NSAppTransportSecurity"):
        problems.append("Release Info.plist declares NSAppTransportSecurity; "
                        "no cleartext exemption may ship")
    else:
        print("No ATS exception in Release.")


def check_manifest() -> None:
    if not MANIFEST.exists():
        problems.append(f"{MANIFEST.relative_to(REPO)} is required for submission")
        return
    data = plistlib.loads(MANIFEST.read_bytes())
    if data.get("NSPrivacyTracking") is not False:
        problems.append("NSPrivacyTracking must be false unless tracking is "
                        "actually implemented")
    if not data.get("NSPrivacyAccessedAPITypes"):
        problems.append("No required-reason API declarations; UserDefaults "
                        "alone needs one")
    if not data.get("NSPrivacyCollectedDataTypes"):
        problems.append("No collected data types declared; Sava collects an "
                        "email address and user content")
    if not problems:
        print("Privacy manifest present and consistent.")


def main() -> int:
    if not RELEASE_PLIST.exists():
        print(f"::error::{RELEASE_PLIST.relative_to(REPO)} not found")
        return 1
    plist = plistlib.loads(RELEASE_PLIST.read_bytes())

    check_origin(plist)
    check_ats(plist)
    check_manifest()

    for note in notes:
        print(f"::notice::{note}")
    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
