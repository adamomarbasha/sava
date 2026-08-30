"""Onboarding: what it teaches, what it must never claim, and that it persists.

Source-level assertions, in the style of `test_ios_shortcut.py`. The behaviour
they protect is the kind that only breaks in ways nobody notices until a user
complains — a tour that replays after every update, a completion flag stored
somewhere transient, instructions that no longer match the app they describe.

They deliberately test *properties of the implementation* rather than
re-implementing SwiftUI: that completion is keyed by user id, that both save
workflows are taught for all three platforms, that screenshot saving is gone,
that the Shortcut is presented as a prerequisite of the Action Button rather
than as a way to save, and that no monetisation appears before the app does.
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


def literals(source: str) -> str:
    """Every single-line string literal, lowercased.

    No newline in the character class: a Swift literal cannot span lines, and
    allowing it lets the pattern swallow the comments between two unrelated
    quotes — which is how an earlier version of this file failed on its own
    design rationale.
    """
    return " ".join(re.findall(r'"([^"\\\n]*)"', source)).lower()


ONBOARDING = ("Sava", "Features", "Onboarding")
STATE = (*ONBOARDING, "OnboardingState.swift")
VIEW = (*ONBOARDING, "OnboardingView.swift")
PIECES = (*ONBOARDING, "OnboardingPieces.swift")
FLOW = (*ONBOARDING, "SaveFlowDemo.swift")
FIND = (*ONBOARDING, "FindDemo.swift")
SETUP = (*ONBOARDING, "SetupPieces.swift")
UNIVERSE = (*ONBOARDING, "ContentUniverse.swift")
LIBRARY = (*ONBOARDING, "DemoLibrary.swift")
CARD = (*ONBOARDING, "DemoCard.swift")
BUTTON = (*ONBOARDING, "ActionButtonSupport.swift")
ROOT = ("Sava", "App", "RootView.swift")
PROFILE = ("Sava", "Features", "Profile", "ProfileView.swift")
LEARN = ("Sava", "Features", "Profile", "SaveAnywhereView.swift")

ALL_ONBOARDING = [VIEW, PIECES, FLOW, FIND, SETUP, UNIVERSE, LIBRARY, CARD]


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
        assert "OnboardingState.markComplete" in code_of(*ROOT)

    def test_signing_out_clears_the_in_memory_flag(self):
        """Otherwise a second account inherits the first account's answer."""
        assert "onboardingDone = false" in code_of(*ROOT)

    def test_profile_offers_a_way_back_in(self):
        assert "Learn Sava" in read(*PROFILE)

    def test_the_tour_can_actually_be_replayed_from_learn_sava(self):
        code = code_of(*LEARN)
        assert "OnboardingView(" in code
        assert "fullScreenCover" in code


# ─── Screenshot saving is gone ───────────────────────────────────────────────

class TestScreenshotSavingIsNotAdvertised:
    """Sava saves links. Nothing may suggest otherwise."""

    @pytest.mark.parametrize("parts", ALL_ONBOARDING + [LEARN])
    def test_no_surface_mentions_screenshots_to_the_user(self, parts):
        text = literals(read(*parts))
        for forbidden in ("screenshot", "screen shot", "photo library",
                          "camera roll"):
            assert forbidden not in text, f"{parts[-1]} still says {forbidden!r}"

    def test_the_demo_library_holds_no_image_save(self):
        """The old first screen showed an "IMAGE / Screenshot" card, which
        advertised a workflow by implication."""
        text = literals(read(*LIBRARY))
        assert "screenshot" not in text
        assert "image" not in text.split()

    def test_onboarding_never_asks_for_the_photo_library(self):
        code = "\n".join(code_of(*parts) for parts in ALL_ONBOARDING)
        for forbidden in ("PhotosPicker", "PHPickerViewController",
                          "UIImagePickerController", "PHPhotoLibrary"):
            assert forbidden not in code, forbidden

    def test_collection_covers_may_still_use_the_photo_picker(self):
        """Guards the *other* direction: this removal was about a capture
        workflow, not about image infrastructure. Choosing a cover for a
        collection is a different feature and must survive."""
        assert "PhotosPicker" in read("Sava", "Features", "Collections",
                                      "CoverPickerSheet.swift")


# ─── The two save workflows, for all three platforms ────────────────────────

class TestSaveWorkflowsAreTaught:

    def test_all_three_platforms_are_selectable(self):
        code = code_of(*FLOW)
        assert re.search(r"enum DemoPlatform.*?case tiktok, instagram, youtube",
                         code, re.S), "the picker must offer all three"
        for name in ("TikTok", "Instagram", "YouTube"):
            assert f'"{name}"' in code, name

    def test_both_methods_are_selectable(self):
        code = code_of(*FLOW)
        assert "case shareSheet, actionButton" in code
        assert '"Share sheet"' in code and '"Action Button"' in code

    @staticmethod
    def _steps_body() -> str:
        """Just `steps(for:)`.

        The enum's cases repeat across `title`, `requirement`, `steps` and
        `explanation`, so slicing on `case .shareSheet:` alone lands in the
        wrong function — which is how the first version of this test failed.
        """
        code = code_of(*FLOW)
        start = code.index("func steps(for platform: DemoPlatform) -> [FlowStep]")
        end = code.index("func explanation(for platform:")
        return code[start:end]

    def test_the_share_sheet_path_is_content_then_share_then_sava(self):
        """Three steps, in that order, for whichever platform is selected."""
        body = self._steps_body()
        share = body[body.index("case .shareSheet:"):body.index("case .actionButton:")]
        assert share.index('id: "content"') < share.index('id: "share"') \
            < share.index('id: "sava"')

    def test_the_action_button_path_is_copy_then_press_then_saved(self):
        body = self._steps_body()
        block = body[body.index("case .actionButton:"):]
        assert block.index('id: "content"') < block.index('id: "copy"') \
            < block.index('id: "press"') < block.index('id: "sava"')
        assert "Copy link" in block

    def test_the_share_sheet_is_stated_to_need_no_setup(self):
        code = code_of(*FLOW)
        assert '"Nothing to set up"' in code

    def test_the_action_button_is_stated_to_need_the_shortcut(self):
        code = code_of(*FLOW)
        assert '"Needs the Shortcut"' in code

    def test_instagram_is_taught_copy_link_because_it_has_no_on_screen_url(self):
        """This is the one platform where the distinction is load-bearing:
        Instagram never exposes the post URL, so Copy Link is the route rather
        than a fallback."""
        code = code_of(*FLOW)
        assert "Instagram never puts the link on screen" in code

    def test_nothing_claims_sava_can_capture_without_a_link(self):
        text = " ".join(literals(read(*parts)) for parts in ALL_ONBOARDING)
        for forbidden in ("automatically saves everything", "detects what you",
                          "watches your screen", "no link needed"):
            assert forbidden not in text, forbidden

    def test_the_demo_is_replayable(self):
        assert "Replay" in read(*FLOW)


# ─── The Shortcut ────────────────────────────────────────────────────────────

class TestShortcutCTA:

    def test_onboarding_opens_the_centralised_url(self):
        """Not a literal — one source of truth, already asserted by
        `test_ios_shortcut.py` to be the official link."""
        code = code_of(*VIEW)
        assert "AppConfig.saveShortcutURL" in code
        assert OFFICIAL_SHORTCUT not in code, \
            "the URL must not be duplicated into onboarding"

    def test_the_official_url_is_still_exactly_right(self):
        assert OFFICIAL_SHORTCUT in read("SavaShared", "AppConfig.swift")

    def test_the_url_still_lives_in_exactly_one_swift_file(self):
        hits = [p for p in IOS.rglob("*.swift")
                if OFFICIAL_SHORTCUT in p.read_text(encoding="utf-8", errors="replace")]
        assert len(hits) == 1, [str(p.name) for p in hits]

    def test_the_shortcut_is_explained_once_not_twice(self):
        """The previous tour put a full Shortcut card on stage 2 *and* stage 4.

        It now appears inside the Action Button path and again only as a status
        row on the final screen, so the explanation is not repeated.
        """
        code = code_of(*VIEW)
        assert code.count("ShortcutChain()") == 1, \
            "the Shortcut explainer belongs on exactly one stage"

    def test_the_shortcut_is_presented_as_the_action_buttons_prerequisite(self):
        """Not as a third way to save — it is not one."""
        code = code_of(*VIEW)
        assert "demoMethod == .actionButton" in code, \
            "the Shortcut explainer must be tied to the Action Button path"

    def test_nothing_claims_to_install_the_shortcut_itself(self):
        assert "openURL(AppConfig.saveShortcutURL)" in code_of(*VIEW)

    def test_the_chain_explains_why_the_shortcut_exists(self):
        code = code_of(*SETUP)
        assert "struct ShortcutChain" in code
        for step in ("Add the", "Assign it", "Copy any", "Press"):
            assert step in code, step


# ─── The Action Button and Settings ─────────────────────────────────────────

class TestActionButtonSetup:

    def test_only_the_supported_settings_url_is_used(self):
        """`App-prefs:` deep links into Settings subpages are private, get
        rejected in review, and break between releases."""
        code = code_of(*BUTTON)
        assert "UIApplication.openSettingsURLString" in code
        for forbidden in ("App-prefs", "prefs:root", "app-settings:"):
            assert forbidden not in code, forbidden

    def test_no_onboarding_file_hand_rolls_a_settings_url(self):
        for parts in ALL_ONBOARDING + [LEARN]:
            code = code_of(*parts)
            for forbidden in ("App-prefs", "prefs:root"):
                assert forbidden not in code, f"{parts[-1]}: {forbidden}"

    def test_the_settings_path_is_shown_because_the_deep_link_cannot_exist(self):
        code = code_of(*SETUP)
        assert "struct SettingsPathTrail" in code
        assert "ActionButtonSupport.settingsPath" in code

    def test_the_path_names_the_real_destination(self):
        code = code_of(*BUTTON)
        assert '"Settings", "Action Button", "Shortcut"' in code
        assert "AppConfig.officialSaveShortcutName" in code, \
            "the last step must be the Shortcut's actual name"

    def test_the_path_is_visible_with_the_button_not_after_pressing_it(self):
        """A button that drops somebody into Settings with no map is where
        setup gets abandoned."""
        code = code_of(*SETUP)
        trail = code.index("SettingsPathTrail()")
        button = code.index('Button("Open Settings"')
        assert trail < button, "the path must be rendered above the CTA"

    def test_unsupported_devices_are_not_given_impossible_instructions(self):
        code = code_of(*SETUP)
        assert "ActionButtonSupport.isAvailable" in code
        assert "iPhone 15 Pro" in code, \
            "an unsupported device should be told what it is missing"

    @pytest.mark.parametrize("identifier,expected", [
        ("iPhone14,5", False),   # iPhone 13
        ("iPhone15,4", False),   # iPhone 15 — no Action Button
        ("iPhone15,5", False),   # iPhone 15 Plus
        ("iPhone16,1", True),    # iPhone 15 Pro — the first one
        ("iPhone16,2", True),    # iPhone 15 Pro Max
        ("iPhone17,3", True),    # iPhone 16
        ("iPhone19,1", True),    # unreleased — must fail open
        ("", True),              # unknown — must fail open
        ("arm64", True),         # Simulator — must fail open
    ])
    def test_action_button_detection_boundary(self, identifier, expected):
        """Documents the rule the Swift implements: `major >= 16`, failing open.

        A stale allow-list that hides the feature on hardware that has it would
        be a silent permanent bug on every new iPhone; showing one extra setup
        card on an iPhone 13 is a paragraph somebody can ignore.
        """
        code = code_of(*BUTTON)
        assert "major >= 16" in code
        assert "return true" in code

        # Mirror of the Swift, so the boundary itself is asserted.
        def supported(ident: str) -> bool:
            if not ident.startswith("iPhone"):
                return True
            digits = ""
            for ch in ident[len("iPhone"):]:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            if not digits or int(digits) == 0:
                return True
            return int(digits) >= 16

        assert supported(identifier) is expected


# ─── Product rules ───────────────────────────────────────────────────────────

class TestOnboardingProductRules:

    def test_there_is_no_paywall_in_first_run(self):
        """First run is not a monetisation funnel."""
        code = code_of(*VIEW)
        for forbidden in ("PaywallView", "SubscriptionManager", "purchase",
                          "$9.99", "$79.99", "Upgrade"):
            assert forbidden not in code, f"onboarding must not mention {forbidden}"

    def test_no_stage_mentions_plans_or_limits_to_the_user(self):
        text = " ".join(literals(read(*parts)) for parts in ALL_ONBOARDING)
        for forbidden in ("sava pro", "upgrade", "free plan", "per month",
                          "subscription", "trial"):
            assert forbidden not in text, forbidden

    def test_no_permission_prompts(self):
        code = "\n".join(code_of(*parts) for parts in ALL_ONBOARDING)
        for forbidden in ("requestAuthorization", "UNUserNotificationCenter",
                          "AVCaptureDevice", "PHPhotoLibrary", "ATTrackingManager"):
            assert forbidden not in code, f"onboarding must not request {forbidden}"

    def test_the_user_is_never_trapped(self):
        source = read(*VIEW)
        assert "Skip" in source
        assert "Skip for now" in source

    def test_there_are_four_stages(self):
        assert re.search(r"stageCount\s*=\s*4", code_of(*VIEW))

    def test_each_stage_has_one_job(self):
        """Four distinct headlines, none of them repeating another's subject."""
        code = code_of(*VIEW)
        for stage in ("stageWhy", "stageHowToSave", "stageFind", "stageReady"):
            assert f"private var {stage}" in code, stage


# ─── The local demos ─────────────────────────────────────────────────────────

class TestDemosAreLocalAndDeterministic:

    def test_no_demo_touches_the_network(self):
        """First run must not depend on a cold backend or an AI provider."""
        for parts in ALL_ONBOARDING:
            code = code_of(*parts)
            for forbidden in ("APIClient", "IntelligenceService", "URLSession",
                              "await client", "BookmarkService", "AsyncImage"):
                assert forbidden not in code, f"{parts[-1]} must not use {forbidden}"

    def test_the_artwork_is_drawn_not_fetched_or_bundled(self):
        """Bundled photography would be stock or somebody else's content, and
        a fetch would make first run depend on a CDN."""
        code = code_of(*ONBOARDING, "PosterArt.swift")
        assert "LinearGradient" in code or "RadialGradient" in code
        for forbidden in ("Image(\"", "UIImage(named", "AsyncImage", "Data(contentsOf"):
            assert forbidden not in code, forbidden

    def test_every_demo_item_has_artwork(self):
        library = read(*LIBRARY)
        art = read(*ONBOARDING, "PosterArt.swift")
        scenes = set(re.findall(r"poster: \.(\w+)", library))
        assert scenes, "the demo library has no artwork at all"
        declared = re.search(r"enum Scene[^\n]*\n\s*case ([^\n]+)", art)
        known = {s.strip() for s in declared.group(1).split(",")}
        assert scenes <= known, f"unknown scenes: {scenes - known}"

    def test_the_search_demo_resolves_deterministically(self):
        """The demo types one fixed query against one fixed corpus, so the
        outcome cannot depend on ranking, a model, or a network."""
        code = code_of(*LIBRARY)
        assert "static let searchQuery" in code
        assert "static var searchTarget" in code
        assert "func matches(" in code

    def test_the_demo_query_actually_finds_the_demo_target(self):
        """Mirrors `DemoLibrary.matches` so a copy edit that breaks the demo
        fails here rather than on a stranger's first launch."""
        source = read(*LIBRARY)
        query = re.search(r'searchQuery = "([^"]+)"', source).group(1)
        stop = set(re.findall(r'"(\w+)",', source[source.index("private static let stopWords"):]))
        items = re.findall(r'title: "([^"]+)",\s*\n\s*creator: "([^"]+)"', source)
        assert items, "could not parse the demo library"

        words = [w for w in re.split(r"[^0-9a-zA-Z]+", query.lower())
                 if len(w) > 1 and w not in stop]
        assert words, "the demo query is entirely stop words"

        matches = [t for t, c in items
                   if all(w in f"{t} {c}".lower() for w in words)]
        assert len(matches) == 1, f"{query!r} matched {matches}"

    def test_ask_is_answered_from_a_fixture_not_the_api(self):
        code = code_of(*LIBRARY)
        assert "static let askAnswer" in code
        assert "askQuestion" in code

    def test_the_find_demo_shows_search_and_ask(self):
        code = code_of(*FIND)
        assert "searchField" in code
        assert "askExchange" in code


# ─── Accessibility and motion ────────────────────────────────────────────────

class TestOnboardingAccessibility:

    @pytest.mark.parametrize("parts", [VIEW, FLOW, FIND, UNIVERSE])
    def test_reduce_motion_is_honoured(self, parts):
        assert "accessibilityReduceMotion" in code_of(*parts)

    @pytest.mark.parametrize("parts", [FLOW, FIND, UNIVERSE])
    def test_reduce_motion_lands_on_the_finished_state(self, parts):
        """Not a frozen first frame — the whole story in one composition."""
        code = code_of(*parts)
        assert re.search(r"guard !reduceMotion else \{|if reduceMotion \{", code), \
            f"{parts[-1]} has no explicit Reduce Motion path"

    def test_the_interactive_pieces_are_labelled(self):
        code = "\n".join(code_of(*parts) for parts in [FLOW, FIND, UNIVERSE, CARD])
        assert code.count("accessibilityLabel") >= 6

    def test_the_universe_is_operable_without_dragging(self):
        """A drag is not available to everyone. Tapping and the VoiceOver
        action must reach the same place."""
        code = code_of(*UNIVERSE)
        assert "onTapGesture" in code
        assert "accessibilityAction" in code

    def test_the_progress_indicator_announces_position(self):
        assert "Step \\(stage + 1) of \\(total)" in read(*PIECES)

    def test_content_scrolls_for_large_type(self):
        """A fixed layout clips the headline at accessibility text sizes."""
        assert "ScrollView" in code_of(*VIEW)

    def test_the_skip_control_meets_the_touch_target_minimum(self):
        assert "minHeight: 44" in code_of(*VIEW)

    def test_animations_do_not_outlive_their_screen(self):
        """`repeatForever` keeps a render loop alive after the view is gone.

        Everything here is driven by `TimelineView`, which SwiftUI stops with
        the view, or by a cancellable task keyed on a run id.
        """
        for parts in ALL_ONBOARDING:
            code = code_of(*parts)
            assert "repeatForever" not in code, f"{parts[-1]} animates forever"

    def test_the_sequenced_demos_can_be_cancelled(self):
        for parts in (FLOW, FIND):
            code = code_of(*parts)
            assert "runID" in code, f"{parts[-1]} cannot cancel a running sequence"
            assert "onDisappear" in code, f"{parts[-1]} does not stop on disappear"


# ─── Learn Sava keeps everything the tour taught ────────────────────────────

class TestLearnSava:

    def test_it_teaches_the_same_two_workflows(self):
        """Shared with the tour rather than restated, so they cannot drift."""
        code = code_of(*LEARN)
        assert "SaveFlowDemo(" in code

    def test_it_can_reinstall_the_shortcut(self):
        code = code_of(*LEARN)
        assert "AppConfig.saveShortcutURL" in code
        assert "Add Sava Shortcut" in read(*LEARN)

    def test_it_explains_action_button_setup(self):
        code = code_of(*LEARN)
        assert "SettingsPathTrail()" in code
        assert "ActionButtonSupport.appSettingsURL" in code

    def test_it_covers_the_share_sheet(self):
        assert "share sheet" in literals(read(*LEARN))

    def test_it_can_replay_the_tour(self):
        assert "showTour = true" in code_of(*LEARN)

    def test_it_no_longer_teaches_screenshot_saving(self):
        assert "screenshot" not in read(*LEARN).lower()
