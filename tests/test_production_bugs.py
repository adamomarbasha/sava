"""Four bugs reported from a physical iPhone against production.

Each was reproduced before it was changed, and two of them turned out to have
the *same* cause: production was running code from before the fixes landed.

    POST /api/ask            -> 403   (route exists, auth required)
    POST /api/ask/stream     -> 404   (route absent)

That 404 is what the user saw as "Not found": the app posts to the streaming
endpoint, `APIClient` maps 404 to `.notFound`, and the message is "We couldn't
find that." It also explains why no `/api/ask` POST appeared in their log — the
app never calls it.
"""
from __future__ import annotations

import itertools
import json
import pathlib

import pytest

from api.models import (Bookmark, CanonicalContent, ContentUnderstanding,
                        ProcessingState)
from api.services.collections import rebuild_auto_collections
from api.services import grouping

from conftest import make_user

IOS = pathlib.Path(__file__).resolve().parent.parent / "ios"
_seq = itertools.count(1)


def read(*parts: str) -> str:
    return IOS.joinpath(*parts).read_text(encoding="utf-8")


def code_of(*parts: str) -> str:
    return "\n".join(l for l in read(*parts).splitlines()
                     if not l.strip().startswith("///")
                     and not l.strip().startswith("//"))


# ─── Bug 1: Ask against a server that cannot stream ─────────────────────────

class TestAskSurvivesAnOlderServer:

    def test_a_missing_stream_falls_back_to_the_plain_endpoint(self):
        """A client and a server do not deploy in lockstep, and an app already
        on a phone cannot wait for one."""
        code = code_of("Sava", "Core", "Networking", "AskStream.swift")
        assert "private func withFallback" in code
        assert "case APIError.notFound = error" in code

    def test_both_ask_scopes_use_it(self):
        code = code_of("Sava", "Core", "Networking", "AskStream.swift")
        assert code.count("withFallback(") == 3   # definition + two call sites

    def test_only_a_missing_route_triggers_it(self):
        """A 401, 402 or 500 means something the user needs to hear; retrying
        those against a second endpoint doubles the work and buries the error."""
        code = code_of("Sava", "Core", "Networking", "AskStream.swift")
        block = code[code.index("private func withFallback"):]
        assert "guard !sawAnything" in block, "a mid-stream failure is real"

    def test_the_fallback_produces_the_same_events(self):
        code = code_of("Sava", "Core", "Networking", "AskStream.swift")
        block = code[code.index("private func withFallback"):]
        for event in (".meta(", ".sources(", ".token(", ".done("):
            assert event in block, event


# ─── Bug 3: nothing to group, because nothing was read ──────────────────────

def _save(db, user, *, title, creator, topics=None, understood=True):
    n = next(_seq)
    cc = CanonicalContent(
        content_key=f"yt:prod{n}", platform="youtube", platform_content_id=str(n),
        canonical_url=f"https://x/prod{n}", media_kind="video",
        title=title, creator_name=creator, description="",
        content_type="video" if understood else None,
        processing_state=ProcessingState.READY if understood else ProcessingState.PARTIAL,
        processing_level=4 if understood else 1,
        stage_status="{}", metadata_json="{}")
    db.add(cc); db.flush()
    if understood:
        db.add(ContentUnderstanding(
            canonical_content_id=cc.id, tl_dr=title or "",
            topics=json.dumps(topics or []), key_points="[]", entities="{}",
            typed_data="{}", chapters="[]", sources_used="[]"))
    db.add(Bookmark(user_id=user.id, url=cc.canonical_url, platform="youtube",
                    raw="{}", canonical_content_id=cc.id,
                    processing_state=cc.processing_state))
    db.commit()


@pytest.fixture
def user(clean_db):
    return make_user(clean_db, f"prodbug-{next(_seq)}@example.com")


class TestGroupingOnUnreadSaves:
    """The reported production result, reproduced exactly:

        POST /api/collections/rebuild?background=false -> 200
        {"status":"ok","collections":[],"saves_considered":13,"items_covered":0}
    """

    def test_thirteen_unread_saves_offer_the_algorithm_nothing(self, clean_db, user):
        for _ in range(13):
            _save(clean_db, user, title=None, creator=None, understood=False)
        items = grouping.load_items(clean_db, user.id)
        assert len(items) == 13
        assert sum(1 for i in items if i.creator) == 0
        assert sum(1 for i in items if i.topics) == 0
        assert sum(1 for i in items if i.hashtags) == 0
        candidates, _ = grouping.discover(clean_db, user.id, limit=12,
                                          rejected=set(), removed={})
        assert candidates == [], "no signal can produce no candidate"

    def test_that_case_is_reported_as_awaiting_understanding(self, clean_db, user):
        """Not "no new groups". Sava has not looked yet, and the fix is
        processing rather than a lower threshold."""
        for _ in range(13):
            _save(clean_db, user, title=None, creator=None, understood=False)
        result = rebuild_auto_collections(clean_db, user.id)
        assert result["status"] == "awaiting_understanding"
        assert result["saves_considered"] == 13
        assert result["with_signal"] == 0
        assert result["collections"] == []

    def test_the_same_library_groups_once_it_has_been_read(self, clean_db, user):
        """The algorithm was never the problem."""
        for i in range(3):
            _save(clean_db, user, title=f"Gym {i}", creator="fitcoach", topics=["fitness"])
        for i in range(3):
            _save(clean_db, user, title=f"Pasta {i}", creator="cookwithme", topics=["cooking"])
        for i in range(3):
            _save(clean_db, user, title=f"GTA {i}", creator="clipsdaily", topics=["gaming"])
        for t, c in (("Berlin", "faye"), ("Kyoto", "onthewayto"),
                     ("Compilers", "handmade"), ("Espresso", "thebean")):
            _save(clean_db, user, title=t, creator=c, topics=["misc"])
        result = rebuild_auto_collections(clean_db, user.id)
        assert result["status"] == "ok"
        assert len(result["collections"]) >= 3
        assert result["items_covered"] >= 9

    def test_a_partly_read_library_still_groups_what_it_can(self, clean_db, user):
        """The gate is a *majority* unread, not any unread at all."""
        for i in range(4):
            _save(clean_db, user, title=f"Gym {i}", creator="fitcoach", topics=["fitness"])
        for _ in range(2):
            _save(clean_db, user, title=None, creator=None, understood=False)
        result = rebuild_auto_collections(clean_db, user.id)
        assert result["status"] == "ok"
        assert len(result["collections"]) >= 1

    def test_thresholds_were_not_weakened_to_manufacture_output(self):
        assert grouping.MIN_MEMBERS == 3


# ─── Bug 2: appearance must not depend on an entitlement ───────────────────

class TestAppearanceIsLocalFirst:

    def test_the_apps_own_store_is_standard_not_the_app_group(self):
        """The Simulator does not enforce entitlements; a device does. On a
        profile lacking the group, the suite silently drops writes and the
        device logs a CFPrefsPlistSource failure — after which Light/Dark stops
        persisting. Appearance must not be able to fail over a capability only
        the share extension needs."""
        code = code_of("SavaShared", "AppearancePreference.swift")
        assert "public static let store: UserDefaults = .standard" in code

    def test_the_group_is_verified_by_a_round_trip_not_a_nil_check(self):
        """`UserDefaults(suiteName:)` returns an object for an unentitled group
        and silently drops everything written to it."""
        code = code_of("SavaShared", "AppearancePreference.swift")
        assert "public static let sharedStore: UserDefaults?" in code
        assert "suite.set(true, forKey: probe)" in code
        assert "suite.bool(forKey: probe)" in code

    def test_the_preference_is_mirrored_for_the_extension(self):
        code = code_of("SavaShared", "AppearancePreference.swift")
        assert "public static func publish(" in code
        assert "sharedStore?.set(" in code
        assert "AppearancePreference.publish(" in code_of("Sava", "App", "RootView.swift")

    def test_reading_falls_back_across_both_stores(self):
        code = code_of("SavaShared", "AppearancePreference.swift")
        assert "store.string(forKey: storageKey)" in code
        assert "sharedStore?.string(forKey: storageKey)" in code


# ─── Bug 4: the logo is a logo ──────────────────────────────────────────────

class TestTheMarkIsTheBrandMark:

    def test_it_uses_the_canonical_colours(self):
        """`AppIcon.png` is the canonical artwork: 82% citron #D6FF00 with a
        near-black glyph. Only the tile is a constant in code — the glyph's
        colours come from the asset itself, which is why it is drawn and not
        tinted (see the two-tone test below)."""
        code = code_of("Sava", "DesignSystem", "Components", "SavaMark.swift")
        assert "0xD6FF00" in code

    def test_it_does_not_invert_with_the_theme(self):
        """Tinting with `accent`/`onAccent` made the mark swap colours between
        appearances — a variant nobody has seen, reading as a generic symbol
        rather than as Sava. A logo is a constant."""
        code = code_of("Sava", "DesignSystem", "Components", "SavaMark.swift")
        assert "SavaColor.accent" not in code
        assert "SavaColor.onAccent" not in code

    def test_the_canonical_asset_still_says_so(self):
        """Guards the source of truth rather than the copy of it."""
        from PIL import Image
        import collections
        icon = Image.open(IOS / "Sava" / "Assets.xcassets" / "AppIcon.appiconset"
                          / "AppIcon.png").convert("RGB")
        top = collections.Counter(icon.getdata()).most_common(1)[0][0]
        assert top == (0xD6, 0xFF, 0x00), f"icon tile is {top}"

    def test_the_glyph_is_two_tone_so_it_must_not_render_as_a_template(self):
        """The asset carries detail in colour, so template rendering loses it.

        This is the actual logo defect. `.renderingMode(.template)` keeps only
        alpha and repaints every pixel one tint. The mark is a near-black body
        with a white detail inside it, both fully opaque, so a template
        flattens the two into a single blob — a generic bookmark glyph rather
        than the Sava mark.

        (An earlier version of this test looked for interior *alpha* knockouts,
        found none, and concluded the glyph was solid. Wrong measurement: the
        detail is carried in colour, not transparency.)
        """
        from PIL import Image
        im = Image.open(IOS / "Sava" / "Assets.xcassets" / "SavaMark.imageset"
                        / "SavaMark@3x.png").convert("RGBA")
        opaque = [px[:3] for px in im.getdata() if px[3] > 200]

        def lum(c):
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

        dark = [c for c in opaque if lum(c) < 60]
        light = [c for c in opaque if lum(c) > 200]
        assert dark and light, "expected a two-tone mark: dark body, light detail"
        # Neither tone is a stray anti-aliasing artefact.
        assert len(light) > len(opaque) * 0.02, "light detail is not incidental"
        assert len(dark) > len(opaque) * 0.02, "dark body is not incidental"

    def test_the_mark_renders_the_artwork_as_drawn(self):
        """Given the above, SavaMark must draw the asset, not tint it."""
        code = code_of("Sava", "DesignSystem", "Components", "SavaMark.swift")
        assert ".renderingMode(.original)" in code
        assert ".renderingMode(.template)" not in code, \
            "template rendering flattens the mark's two tones into one"
        assert ".foregroundStyle" not in code.split('Image("SavaMark")')[1][:400], \
            "tinting the mark would override the artwork's own colours"

    def test_the_asset_does_not_declare_template_intent(self):
        """A template intent in the catalog would reintroduce the flattening."""
        import json
        d = json.loads((IOS / "Sava" / "Assets.xcassets" / "SavaMark.imageset"
                        / "Contents.json").read_text())
        assert "template" not in json.dumps(d.get("properties", {}))
