"""Onboarding: shown once, per account, and reachable again on purpose.

Source-level assertions, in the style of `test_ios_shortcut.py`. The behaviour
they protect is the kind that only breaks in ways nobody notices until a user
complains — a tour that replays after every update, a completion flag stored
somewhere transient, a paywall that creeps into first run.

They deliberately test *properties of the implementation* rather than
re-implementing SwiftUI: that completion is keyed by user id, that the key is
not derived from a build number, that no monetisation appears before the app
does, and that the Shortcut CTA points at the single centralised URL.
"""
from __future__ import annotations

import pathlib
import re

import pytest

IOS = pathlib.Path(__file__).resolve().parent.parent / "ios"
OFFICIAL_SHORTCUT = "https://www.icloud.com/shortcuts/c718dbc210a646cea3326d596d1895ef"


def read(*parts: str) -> str:
    return IOS.joinpath(*parts).read_text(encoding="utf-8")


def code_of(*parts: str) -> str:
    """Source with doc comments stripped, so prose cannot satisfy an assertion."""
    return "\n".join(line for line in read(*parts).splitlines()
                     if not line.strip().startswith("///")
                     and not line.strip().startswith("//"))


STATE = ("Sava", "Features", "Onboarding", "OnboardingState.swift")
VIEW = ("Sava", "Features", "Onboarding", "OnboardingView.swift")
PIECES = ("Sava", "Features", "Onboarding", "OnboardingPieces.swift")
ROOT = ("Sava", "App", "RootView.swift")
PROFILE = ("Sava", "Features", "Profile", "ProfileView.swift")
SAVE_ANYWHERE = ("Sava", "Features", "Profile", "SaveAnywhereView.swift")


# ─── Persistence ─────────────────────────────────────────────────────────────

class TestOnboardingPersistence:

    def test_completion_is_durable_not_transient_state(self):
        """A `@State` flag would replay the tour on every cold launch."""
        code = code_of(*STATE)
        assert "UserDefaults" in code
        assert "@State" not in code

    def test_completion_is_keyed_per_account(self):
        """Two people sharing a phone must get their own answer, and signing
        back in must not replay the tour for somebody who finished it."""
        code = code_of(*STATE)
        assert "userID" in code
        assert "Set<Int>" in code or "completedIDs" in code

    def test_the_key_is_not_derived_from_the_app_version(self):
        """Keying on version or build is how a bug-fix release re-onboards
        everybody. The version constant must be hand-set."""
        code = code_of(*STATE)
        for forbidden in ("CFBundleShortVersionString", "CFBundleVersion",
                          "appVersion", "buildNumber"):
            assert forbidden not in code, forbidden
        assert re.search(r"static let version\s*=\s*\d+", code), \
            "onboarding needs an explicit, hand-set version"

    def test_there_is_an_explicit_complete_and_reset(self):
        code = code_of(*STATE)
        assert "func markComplete" in code
        assert "func reset" in code
        assert "func isComplete" in code

    def test_an_unknown_user_is_not_shown_onboarding(self):
        """Nil means "we do not know who this is yet". Guessing "show it" would
        flash the tour mid-launch."""
        code = code_of(*STATE)
        assert re.search(r"guard let userID else \{ return true \}", code), \
            "isComplete must return true for a nil user id"

    def test_reset_all_is_debug_only(self):
        source = read(*STATE)
        if "resetAll" in source:
            assert "#if DEBUG" in source


# ─── Where it is shown ───────────────────────────────────────────────────────

class TestOnboardingIsWiredIn:

    def test_the_root_shows_it_only_to_a_signed_in_account(self):
        code = code_of(*ROOT)
        assert "OnboardingView(" in code
        assert "OnboardingState.isComplete" in code or "showOnboarding" in code

    def test_finishing_persists_before_the_shell_appears(self):
        code = code_of(*ROOT)
        assert "OnboardingState.markComplete" in code

    def test_signing_out_clears_the_in_memory_flag(self):
        """Otherwise a second account inherits the first account's answer."""
        code = code_of(*ROOT)
        assert "onboardingDone = false" in code

    def test_profile_offers_a_way_back_in(self):
        assert "Learn Sava" in read(*PROFILE)

    def test_the_tour_can_actually_be_replayed(self):
        code = code_of(*SAVE_ANYWHERE)
        assert "OnboardingView(" in code
        assert "fullScreenCover" in code


# ─── Product rules ───────────────────────────────────────────────────────────

class TestOnboardingProductRules:

    def test_there_is_no_paywall_in_first_run(self):
        """First run is not a monetisation funnel."""
        code = code_of(*VIEW)
        for forbidden in ("PaywallView", "SubscriptionManager", "purchase",
                          "$9.99", "$79.99", "Upgrade"):
            assert forbidden not in code, f"onboarding must not mention {forbidden}"

    def test_no_permission_prompts(self):
        code = code_of(*VIEW) + code_of(*PIECES)
        for forbidden in ("requestAuthorization", "UNUserNotificationCenter",
                          "AVCaptureDevice", "PHPhotoLibrary", "ATTrackingManager"):
            assert forbidden not in code, f"onboarding must not request {forbidden}"

    def test_the_user_is_never_trapped(self):
        source = read(*VIEW)
        assert "Skip" in source
        assert "Skip for now" in source

    def test_the_search_demo_is_local_and_makes_no_network_call(self):
        """First run must not depend on a cold backend or an AI provider."""
        code = code_of(*PIECES)
        for forbidden in ("APIClient", "IntelligenceService", "URLSession",
                          "await client", "BookmarkService"):
            assert forbidden not in code, f"the demo must not use {forbidden}"

    def test_the_demo_shows_search_and_ask(self):
        code = code_of(*PIECES)
        assert "SearchDemo" in code and "AskDemo" in code

    def test_there_are_four_stages(self):
        assert re.search(r"stageCount\s*=\s*4", code_of(*VIEW))


# ─── Accessibility and motion ────────────────────────────────────────────────

class TestOnboardingAccessibility:

    @pytest.mark.parametrize("parts", [VIEW, PIECES])
    def test_reduce_motion_is_honoured(self, parts):
        code = code_of(*parts)
        assert "accessibilityReduceMotion" in code

    def test_every_interactive_demo_has_an_accessibility_label(self):
        code = code_of(*PIECES)
        assert code.count("accessibilityLabel") >= 4

    def test_the_progress_indicator_announces_position(self):
        assert "Step \\(stage + 1) of \\(total)" in read(*PIECES)

    def test_content_scrolls_for_large_type(self):
        """A fixed layout clips the headline at accessibility text sizes."""
        assert "ScrollView" in code_of(*VIEW)

    def test_the_skip_control_meets_the_touch_target_minimum(self):
        assert "minHeight: 44" in code_of(*VIEW)


# ─── The Shortcut CTA ────────────────────────────────────────────────────────

class TestShortcutCTA:

    def test_onboarding_opens_the_centralised_url(self):
        """Not a literal — one source of truth, already asserted by
        `test_ios_shortcut.py` to be the official link."""
        code = code_of(*VIEW)
        assert "AppConfig.saveShortcutURL" in code
        assert OFFICIAL_SHORTCUT not in code, \
            "the URL must not be duplicated into onboarding"

    def test_the_official_url_is_still_exactly_right(self):
        config = read("SavaShared", "AppConfig.swift")
        assert OFFICIAL_SHORTCUT in config

    def test_the_url_still_lives_in_exactly_one_swift_file(self):
        hits = [p for p in IOS.rglob("*.swift")
                if OFFICIAL_SHORTCUT in p.read_text(encoding="utf-8", errors="replace")]
        assert len(hits) == 1, [str(p.name) for p in hits]

    def test_onboarding_offers_the_shortcut_on_two_stages(self):
        """Stage 2 teaches it; stage 4 is where someone acts on it."""
        assert code_of(*VIEW).count("Add Sava Shortcut") >= 2

    def test_nothing_claims_to_install_the_shortcut_itself(self):
        code = code_of(*VIEW)
        assert "openURL(AppConfig.saveShortcutURL)" in code


# ─── Sava Pro stays out of first run but is reachable after ─────────────────

class TestProDiscoverability:

    def test_profile_has_a_plan_section(self):
        source = read(*PROFILE)
        assert 'SectionHeader(text: "Plan")' in source
        assert "SubscriptionRow" in source

    def test_profile_shows_usage(self):
        assert "UsageSection" in read(*PROFILE)

    def test_the_paywall_is_reachable_from_profile(self):
        assert "PaywallView" in read(*PROFILE)

    def test_usage_is_expressed_in_videos_not_internal_units(self):
        """Users must never see route costs or unit accounting.

        Checked against the *string literals* the UI renders, not the file:
        the source legitimately discusses routes and units in comments
        explaining why the label says "videos", and an earlier version of this
        test failed on its own design rationale.
        """
        source = read("Sava", "Features", "Profile", "SubscriptionSection.swift")
        assert "Videos understood" in source

        # No newline in the class: a Swift string literal cannot span lines, and
        # allowing it let the pattern swallow the comments between two unrelated
        # quotes — which is how this test first failed on its own rationale.
        literals = " ".join(re.findall(r'"([^"\\\n]*)"', source)).lower()
        for forbidden in ("light_vision", "processing unit", "token",
                          "route", "unit cost", "credits"):
            assert forbidden not in literals, f"user-facing text says {forbidden!r}"

    def test_the_paywall_states_both_prices_from_storekit(self):
        """Prices come from StoreKit, never hardcoded."""
        code = code_of("Sava", "Features", "Paywall", "PaywallView.swift")
        assert "localizedPrice" in code
        assert "$9.99" not in code and "$79.99" not in code

    def test_the_paywall_offers_restore(self):
        assert "Restore Purchases" in read("Sava", "Features", "Paywall", "PaywallView.swift")
