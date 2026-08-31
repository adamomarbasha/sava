"""Appearance: one source of truth, and it reaches every process.

Source-level assertions, in the style of `test_ios_shortcut.py`.

── The reported bug ────────────────────────────────────────────────────────

Switching Automatic / Light / Dark left parts of the UI stale until the user
navigated or tapped something, and the Sava logo did not switch at all.

Two independent causes, both reproduced in the simulator:

  1. **Two stores over one container.** `AppTheme.store` was a *computed*
     property returning `UserDefaults(suiteName:)` — a **new object every
     access**. `@AppStorage(_:store:)` captures whatever instance it is handed,
     so `RootView` and `ProfileView` observed two different objects over the
     same container. A write through one did not notify the other, the view
     that owns `apply` never re-evaluated, and the preference changed on disk
     while the screen stayed as it was. Navigating rebuilt the view, which
     re-read the store — hence "stale until you navigate".

     Observed: the group store read `dark` while the screen was still light.

  2. **The logo could not switch.** `SavaMark` filled its tile with `accent`
     (which inverts) and drew a fixed near-black PNG on top. In light mode the
     tile becomes ink and the glyph stayed ink: a black mark on a black tile.
     Measured after the fix — light 16.3:1, dark 17.1:1.

A third, invisible in-process: the share extension is a **separate process**
and the preference lived in `UserDefaults.standard`, which is per-process. It
could not read the value at all.
"""
from __future__ import annotations

import pathlib
import re

import pytest

IOS = pathlib.Path(__file__).resolve().parent.parent / "ios"


def read(*parts: str) -> str:
    return IOS.joinpath(*parts).read_text(encoding="utf-8")


def code_of(*parts: str) -> str:
    return "\n".join(line for line in read(*parts).splitlines()
                     if not line.strip().startswith("///")
                     and not line.strip().startswith("//"))


PREF = ("SavaShared", "AppearancePreference.swift")
THEME = ("Sava", "DesignSystem", "AppTheme.swift")
ROOT = ("Sava", "App", "RootView.swift")
PROFILE = ("Sava", "Features", "Profile", "ProfileView.swift")
APP = ("Sava", "App", "SavaApp.swift")
MARK = ("Sava", "DesignSystem", "Components", "SavaMark.swift")
SHARE = ("SavaShare", "ShareViewController.swift")
MARK_ASSET = ("Sava", "Assets.xcassets", "SavaMark.imageset", "Contents.json")


# ─── One source of truth ─────────────────────────────────────────────────────

class TestSingleSourceOfTruth:

    def test_the_store_is_one_instance_not_one_per_access(self):
        """The root cause. A computed property mints a new `UserDefaults` per
        access, and `@AppStorage` observers attach to different objects."""
        code = code_of(*PREF)
        assert "public static let store: UserDefaults =" in code
        assert "public static var store: UserDefaults {" not in code

    def test_every_reader_uses_that_store(self):
        for parts in (ROOT, PROFILE):
            assert "store: AppTheme.store" in code_of(*parts), parts[-1]

    def test_there_is_one_storage_key(self):
        assert 'public static let storageKey = "sava.appearance"' in code_of(*PREF)
        assert "static let storageKey = AppearancePreference.storageKey" in code_of(*THEME)

    def test_no_screen_declares_its_own_default_store(self):
        """A bare `@AppStorage(key)` reads `.standard` and would be a second,
        invisible source of truth."""
        for parts in (ROOT, PROFILE):
            code = code_of(*parts)
            assert "@AppStorage(AppTheme.storageKey)\n" not in code
            assert "@AppStorage(AppTheme.storageKey) " not in code

    def test_the_preference_lives_in_the_app_group(self):
        """`UserDefaults.standard` is per-process, so the share extension could
        not see the value at all."""
        assert "PendingSaveQueue.appGroup" in code_of(*PREF)


# ─── Migration ───────────────────────────────────────────────────────────────

class TestMigrationFromTheLegacyStore:

    def test_there_is_a_migration(self):
        assert "public static func migrateIfNeeded()" in code_of(*PREF)

    def test_it_runs_before_anything_reads(self):
        code = code_of(*APP)
        assert "AppearancePreference.migrateIfNeeded()" in code
        assert code.index("AppearancePreference.migrateIfNeeded()") \
            < code.index("SavaAppearance.apply()")

    def test_it_checks_the_suite_not_the_search_list(self):
        """A `UserDefaults(suiteName:)` object's search list *also* contains the
        process's standard domain, so `string(forKey:)` returns the legacy value
        and the migration looks unnecessary. Reads then work in-app by accident
        while the group container stays empty — and the extension, whose
        standard domain is its own, still sees nothing."""
        code = code_of(*PREF)
        assert "persistentDomain(forName: PendingSaveQueue.appGroup)" in code

    def test_an_upgrading_user_keeps_their_choice(self):
        """Verified in the simulator by planting a legacy value and launching:
        the group container gained `sava.appearance = light` and the app
        rendered light."""
        code = code_of(*PREF)
        assert "UserDefaults.standard" in code


# ─── How it is applied ───────────────────────────────────────────────────────

class TestApplication:

    def test_it_sets_the_trait_collection_not_a_swiftui_environment_value(self):
        """`preferredColorScheme` only reaches SwiftUI. The navigation and tab
        bars, keyboard, context menus, alerts and scroll indicators all derive
        from the trait collection instead."""
        code = code_of(*THEME)
        assert "overrideUserInterfaceStyle" in code
        assert "preferredColorScheme" not in code

    def test_automatic_is_the_absence_of_an_override(self):
        code = code_of(*PREF)
        assert "case .automatic: return .unspecified" in code

    def test_light_and_dark_force_their_style(self):
        code = code_of(*PREF)
        assert "case .light:     return .light" in code
        assert "case .dark:      return .dark" in code

    def test_windows_created_later_are_covered(self):
        """`apply` walks the windows that exist when it is called; a scene
        connecting afterwards would keep the system appearance."""
        assert "UIScene.didActivateNotification" in code_of(*ROOT)

    def test_the_change_is_crossfaded_rather_than_animated_per_view(self):
        code = code_of(*THEME)
        assert "transitionCrossDissolve" in code

    def test_reduce_motion_skips_the_crossfade(self):
        assert "UIAccessibility.isReduceMotionEnabled" in code_of(*THEME)

    def test_the_share_extension_applies_it_too(self):
        """A separate process with its own window — nothing the app does at
        runtime reaches it."""
        code = code_of(*SHARE)
        assert "AppearancePreference.current.interfaceStyle" in code
        assert "overrideUserInterfaceStyle" in code

    def test_the_extension_does_not_touch_the_host_apps_window(self):
        """It does not own that window, and overriding it would change the
        appearance of whatever presented the share sheet."""
        code = code_of(*SHARE)
        assert "UIApplication.shared" not in code

    def test_the_shared_type_cannot_reach_for_uiapplication(self):
        """`UIApplication.shared` is unavailable in an app extension, and this
        file is compiled into one."""
        assert "UIApplication.shared" not in code_of(*PREF)


# ─── The logo ────────────────────────────────────────────────────────────────

class TestTheMarkInverts:

    def test_the_glyph_is_a_template(self):
        """A fixed-colour PNG has no appearance to switch to. The artwork is a
        single near-black silhouette, correct on citron and invisible on ink."""
        assert '"template-rendering-intent" : "template"' in read(*MARK_ASSET)

    def test_it_is_rendered_as_one(self):
        code = code_of(*MARK)
        assert ".renderingMode(.template)" in code

    def test_it_is_tinted_with_the_token_that_inverts_with_the_tile(self):
        """`onAccent` is defined as "what sits on the accent fill" and already
        trades places with it."""
        code = code_of(*MARK)
        assert ".foregroundStyle(SavaColor.onAccent)" in code

    def test_the_tile_and_the_glyph_use_paired_tokens(self):
        code = code_of(*MARK)
        assert ".fill(SavaColor.accent)" in code
        assert ".foregroundStyle(SavaColor.onAccent)" in code

    def test_the_asset_ships_no_appearance_variants(self):
        """One template beats two PNGs: the variants would have to be kept in
        step with a palette they cannot see."""
        content = read(*MARK_ASSET)
        assert "appearances" not in content


# ─── Tokens are dynamic, not captured ────────────────────────────────────────

class TestTokensResolvePerTrait:

    def test_the_dynamic_helper_resolves_against_the_trait_collection(self):
        code = code_of("Sava", "DesignSystem", "Color+Hex.swift")
        assert "UIColor { traits in" in code
        assert "traits.userInterfaceStyle == .dark" in code

    def test_no_token_is_resolved_once_into_a_concrete_colour(self):
        """`resolvedColor(with:)` and `.cgColor` freeze a dynamic colour against
        whatever traits were current at that moment."""
        design = IOS / "Sava" / "DesignSystem"
        for swift in design.rglob("*.swift"):
            body = "\n".join(l for l in swift.read_text().splitlines()
                             if not l.strip().startswith("//"))
            assert "resolvedColor(with:" not in body, swift.name

    def test_the_palette_is_built_from_dynamic_pairs(self):
        code = code_of("Sava", "DesignSystem", "Sava.swift")
        assert code.count(".dynamic(") > 10

    def test_hex_components_are_in_range(self):
        """Guards the "UIColor created with component values far outside the
        expected range" class of warning: every channel is a byte divided by
        255, so it cannot leave 0...1."""
        code = code_of("Sava", "DesignSystem", "Color+Hex.swift")
        assert "/ 255.0" in code
        assert re.search(r"hex >> 16\) & 0xFF", code)
