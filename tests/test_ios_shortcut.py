"""The official "Save to Sava" Shortcut link stays single-sourced and valid.

The failure these prevent is silent in every way that matters. A Shortcut link
that has been mistyped, blanked, or copied into a second file builds cleanly,
passes review, installs fine, and then hands users a one-tap button that 404s or
— worse — opens somewhere nobody chose. Nothing in Swift catches it, because
every one of those states is a perfectly good `String`.

These run without Xcode, so they gate every pull request in milliseconds rather
than only a build on somebody's Mac. The in-app equivalent is
`CaptureDiagnostics.runShortcutConfigSelfCheck()`, which checks the same
invariants at launch in Debug.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IOS = REPO / "ios"
APP_CONFIG = IOS / "SavaShared" / "AppConfig.swift"

# The one link. If this ever needs to change, it changes in AppConfig.swift and
# here — and nowhere else, which is the whole point of the test below.
OFFICIAL = "https://www.icloud.com/shortcuts/c718dbc210a646cea3326d596d1895ef"

ICLOUD_SHORTCUT = re.compile(r"https://www\.icloud\.com/shortcuts/[0-9a-f]{32}")


def _swift_sources() -> list[Path]:
    return sorted(IOS.rglob("*.swift"))


def test_official_url_is_declared_in_app_config():
    """The constant exists, is named for what it is, and holds the real link."""
    source = APP_CONFIG.read_text()
    assert "officialSaveShortcutURL" in source, (
        "AppConfig must expose the official Shortcut link under a named constant"
    )
    assert OFFICIAL in source, f"AppConfig no longer carries {OFFICIAL}"


def test_url_appears_in_exactly_one_swift_file():
    """One source of truth, enforced.

    Not "no hardcoded URLs" — one hardcoded URL, in the place documented as
    holding it. A second copy is how the link and the app drift apart.
    """
    carriers = [p for p in _swift_sources() if ICLOUD_SHORTCUT.search(p.read_text())]
    assert carriers == [APP_CONFIG], (
        "The iCloud Shortcut link must live only in AppConfig.swift; found it in "
        + ", ".join(str(p.relative_to(REPO)) for p in carriers)
    )


def test_url_appears_only_once_within_app_config():
    """Even inside AppConfig, once. A doc comment repeating it is a second copy."""
    found = ICLOUD_SHORTCUT.findall(APP_CONFIG.read_text())
    assert found == [OFFICIAL], f"expected one occurrence, found {found}"


def test_plists_do_not_carry_a_shortcut_url():
    """No plist copy.

    A plist value would have to be duplicated into Info.plist *and*
    Info-Release.plist — two files to keep in step, and the Debug one is what
    gets edited. The env/plist override still works for testing an unpublished
    Shortcut; it simply ships unset.
    """
    for name in ("Info.plist", "Info-Release.plist"):
        text = (IOS / name).read_text()
        assert "SAVA_SHARED_SHORTCUT_URL" not in text, (
            f"ios/{name} must not pin a Shortcut URL — AppConfig owns it"
        )


def test_url_is_https_and_hosted_by_apple():
    """Only Apple can host a Shortcut.

    Mirrors `AppConfig.validatedShortcutURL`: anything else behind a
    trusted-looking install button is a link to somewhere the user did not
    choose.
    """
    assert OFFICIAL.startswith("https://")
    assert OFFICIAL.split("/")[2].endswith("icloud.com")
    assert ICLOUD_SHORTCUT.fullmatch(OFFICIAL)


def test_install_button_opens_the_configured_url_not_a_literal():
    """The CTA resolves through AppConfig rather than carrying its own copy."""
    view = (IOS / "Sava" / "Features" / "Profile" / "SaveAnywhereView.swift").read_text()
    assert "AppConfig.saveShortcutURL" in view
    assert "Add Save to Sava" in view
    # openURL, not the pasteboard, is the primary action.
    assert "openURL(shortcut)" in view


def test_native_intents_are_still_registered():
    """The Shortcut is a wrapper. Removing what it wraps breaks every install.

    The published Shortcut calls these three by name; they are a contract with
    something already installed on users' phones, not internal symbols.
    """
    capture = IOS / "Sava" / "Features" / "Capture"
    intents = (capture / "SaveToSavaIntent.swift").read_text()
    for name in ("SaveToSavaIntent", "SaveLinkToSavaIntent",
                 "SaveScreenshotToSavaIntent"):
        assert f"struct {name}: AppIntent" in intents, f"{name} is no longer an AppIntent"

    provider = (capture / "SavaShortcuts.swift").read_text()
    assert "AppShortcutsProvider" in provider
    assert "Save to Sava" in provider, (
        "the App Shortcut phrase is what puts Sava in the Action Button picker"
    )


def test_link_intent_accepts_text_not_only_urls():
    """The clipboard branch of the official Shortcut sends text, not a URL.

    It passes the Clipboard output straight into `link`. A `[URL]` parameter
    makes Shortcuts coerce a caption to a URL before Sava ever runs, and a
    failed coercion is an error dialog instead of a save.
    """
    intents = (IOS / "Sava" / "Features" / "Capture" / "SaveToSavaIntent.swift").read_text()
    assert "var link: [String]" in intents, (
        "SaveLinkToSavaIntent.link must accept text so the clipboard branch works"
    )
