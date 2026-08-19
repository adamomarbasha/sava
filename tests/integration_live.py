"""Live end-to-end verification against real content and the real provider.

Not part of the unit suite — it costs money and needs the network.

    python tests/integration_live.py

Proves the properties that only a real run can:
  * a real YouTube video ingests end to end,
  * a second user saving the same URL reuses the canonical record (no second
    download, no second transcription, no second summary),
  * Ask This answers from persisted chunks with zero network acquisition,
  * repeated questions never re-acquire media,
  * the cost ledger records what actually happened.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp(prefix='sava_live_'))/'live.db'}"
os.environ["SAVA_INLINE_JOBS"] = "0"

from api.db import SessionLocal, engine          # noqa: E402
from api.migrations import run_migrations        # noqa: E402
from api.models import (                          # noqa: E402
    Bookmark, CanonicalContent, ContentChunk, ContentEmbedding,
    ContentTranscript, ContentUnderstanding, User, UsageEvent,
)

# Short, caption-bearing, stable.
TEST_URL = os.getenv("SAVA_TEST_URL", "https://www.youtube.com/watch?v=aircAruvnKk")
TEST_URL_VARIANT = os.getenv("SAVA_TEST_URL_VARIANT",
                             "https://youtu.be/aircAruvnKk?si=variant&utm_source=x")

OK, FAIL = "  \033[32mPASS\033[0m", "  \033[31mFAIL\033[0m"
results = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"{OK if cond else FAIL}  {label}" + (f"  — {detail}" if detail else ""))
    results.append((label, cond))
    return cond


def main() -> int:
    run_migrations(engine)
    db = SessionLocal()

    print("\n" + "=" * 78)
    print("LIVE END-TO-END  •  real content, real provider")
    print("=" * 78)

    alice = User(email="alice@live.test", password_hash="x")
    bob = User(email="bob@live.test", password_hash="x")
    db.add_all([alice, bob])
    db.commit()

    # ── 1. First user saves ─────────────────────────────────────────────────
    print("\n[1] First user saves a real YouTube video")
    from api.pipeline.ingest import process_content, resolve_or_create_canonical

    bm_a = Bookmark(user_id=alice.id, url=TEST_URL, platform="youtube", raw="{}")
    db.add(bm_a)
    db.commit()
    cc, created = resolve_or_create_canonical(db, TEST_URL, "youtube")
    bm_a.canonical_content_id = cc.id
    db.commit()
    check("canonical content created", created, cc.content_key)

    t0 = time.monotonic()
    result = process_content(cc.id, db, user_id=alice.id)
    elapsed = time.monotonic() - t0
    print(f"      pipeline finished in {elapsed:.1f}s")
    for stage, status in (result.get("stages") or {}).items():
        print(f"        {stage:16} {status}")

    check("pipeline succeeded", result.get("ok") is True, result.get("error", ""))
    db.refresh(cc)
    check("state is ready or partial", cc.processing_state in ("ready", "partial"),
          cc.processing_state)
    check("metadata captured", bool(cc.title), (cc.title or "")[:60])
    check("duration known", bool(cc.duration_seconds), f"{cc.duration_seconds}s")
    check("content classified", bool(cc.content_type),
          f"{cc.content_type} (visual_dependency={cc.visual_dependency})")

    tr = db.query(ContentTranscript).filter(
        ContentTranscript.canonical_content_id == cc.id).first()
    check("transcript PERSISTED", tr is not None,
          f"source={tr.source}, {len(tr.text)} chars" if tr else "none")

    n_chunks = db.query(ContentChunk).filter(
        ContentChunk.canonical_content_id == cc.id).count()
    check("chunks embedded", n_chunks > 0, f"{n_chunks} chunks")

    if tr and cc.duration_seconds:
        last = (db.query(ContentChunk)
                .filter(ContentChunk.canonical_content_id == cc.id)
                .order_by(ContentChunk.chunk_index.desc()).first())
        coverage = (last.end_s or 0) / cc.duration_seconds if last else 0
        check("full duration indexed (no truncation)", coverage > 0.9,
              f"chunks cover {coverage:.0%} of {cc.duration_seconds}s")

    doc = db.query(ContentEmbedding).filter(
        ContentEmbedding.canonical_content_id == cc.id).first()
    check("document vector stored", doc is not None and doc.embedding is not None,
          f"dim={doc.dim}" if doc else "none")

    und = db.query(ContentUnderstanding).filter(
        ContentUnderstanding.canonical_content_id == cc.id).first()
    check("structured understanding stored", und is not None,
          (und.tl_dr or "")[:70] if und else "none")

    check("YouTube used free captions, not paid ASR",
          tr is not None and tr.source == "captions",
          f"source={tr.source}" if tr else "")
    check("YouTube skipped visual analysis by default",
          "skipped" in str(result["stages"].get("vision", "")),
          str(result["stages"].get("vision")))

    # ── 2. Second user, different URL shape ─────────────────────────────────
    print("\n[2] Second user saves the SAME video via a different URL")
    bm_b = Bookmark(user_id=bob.id, url=TEST_URL_VARIANT, platform="youtube", raw="{}")
    db.add(bm_b)
    db.commit()
    cc2, created2 = resolve_or_create_canonical(db, TEST_URL_VARIANT, "youtube")
    bm_b.canonical_content_id = cc2.id
    db.commit()

    check("URL variant resolved to the SAME canonical row", cc2.id == cc.id,
          f"{cc.content_key}")
    check("no duplicate canonical row created", created2 is False)
    check("exactly one canonical row exists",
          db.query(CanonicalContent).count() == 1)

    events_before = db.query(UsageEvent).count()
    t0 = time.monotonic()
    result2 = process_content(cc2.id, db, user_id=bob.id)
    reuse_elapsed = time.monotonic() - t0
    events_after = db.query(UsageEvent).count()

    check("second save was a CACHE HIT", result2.get("cache_hit") is True,
          f"{reuse_elapsed*1000:.0f}ms vs {elapsed*1000:.0f}ms first time")
    check("second save cost zero new AI/acquisition events",
          events_after == events_before, f"{events_after - events_before} new events")
    check("still exactly one transcript",
          db.query(ContentTranscript).count() == 1)

    # ── 3. Ask This must not touch the network ──────────────────────────────
    print("\n[3] Ask This — repeated questions, zero re-acquisition")
    from api.pipeline import acquire
    from api.services import intelligence

    calls = {"n": 0}
    originals = {}
    for fn in ("download_audio", "download_video_lowres", "fetch_metadata",
               "fetch_native_captions", "transcribe_audio"):
        originals[fn] = getattr(acquire, fn)

        def _forbidden(*a, _name=fn, **k):
            calls["n"] += 1
            raise AssertionError(f"{_name} called during Ask This")

        setattr(acquire, fn, _forbidden)

    try:
        questions = [
            "What is this video about?",
            "What are the main concepts explained?",
            "How does it describe the structure being discussed?",
        ]
        answers = []
        for q in questions:
            res = intelligence.ask_this(db, bm_a, q, user_id=alice.id)
            answers.append(res)
            status = "ok" if res.get("ok") else res.get("reason")
            print(f"      Q: {q[:52]:<52} -> {status}, "
                  f"{res.get('grounded_in', 0)} chunks")
        check("all questions answered", all(a.get("ok") for a in answers))
        check("answers are grounded in stored chunks",
              all(a.get("grounded_in", 0) > 0 for a in answers))
        check("answers carry citations",
              all(a.get("citations") for a in answers))
        check("ZERO media re-acquisition across 3 questions", calls["n"] == 0,
              f"{calls['n']} acquisition calls")
        if answers and answers[0].get("ok"):
            print(f"\n      sample answer: {answers[0]['answer'][:220]}...")
    finally:
        for fn, orig in originals.items():
            setattr(acquire, fn, orig)

    # ── 4. Summary cache ────────────────────────────────────────────────────
    print("\n[4] Summary caching")
    s1 = intelligence.get_or_create_summary(db, bm_a, user_id=alice.id)
    s2 = intelligence.get_or_create_summary(db, bm_b, user_id=bob.id)
    check("summary available", s1.get("available") is True)
    check("summary served from cache", s1.get("cached") is True)
    check("second user gets the SAME cached summary (cross-user reuse)",
          s2.get("available") is True and s2.get("cached") is True)
    check("summary has key points", bool(s1.get("key_points")),
          f"{len(s1.get('key_points') or [])} points")
    check("summary has topics", bool(s1.get("topics")),
          ", ".join(s1.get("topics") or [])[:60])

    # ── 5. Search / related / Ask Sava ──────────────────────────────────────
    print("\n[5] Retrieval surfaces")
    from api.services import retrieval
    hits = retrieval.search_library(db, alice.id, (cc.title or "neural network")[:40])
    check("search returns the save", any(h.bookmark_id == bm_a.id for h in hits),
          f"{len(hits)} results")

    rel = retrieval.related_saves(db, alice.id, cc.id)
    check("related saves runs without error (single-item library)", isinstance(rel, list),
          f"{len(rel)} related")

    ask = intelligence.ask_sava(db, alice.id, "What have I saved about this topic?")
    check("Ask Sava answers from the library", ask.get("ok") is True,
          f"grounded in {ask.get('grounded_in')} saves")
    check("Ask Sava cites real saves",
          all(s["id"] in (bm_a.id,) for s in ask.get("sources", [])))

    # ── 6. Cost ledger ──────────────────────────────────────────────────────
    print("\n[6] Cost telemetry")
    from api.ai import telemetry
    summary = telemetry.summarize(db, days=1)
    print(f"      events={summary['events']}  "
          f"est=${summary['estimated_usd']:.6f}  "
          f"tokens={summary['input_tokens']}in/{summary['output_tokens']}out  "
          f"proxy={summary['proxy_bytes']/1024:.0f}KB  "
          f"cache_hit_rate={summary['cache_hit_rate']:.0%}")
    for row in summary["by_operation"]:
        print(f"        {row['operation']:28} n={row['n']:<3} ${row['usd']:.6f}")
    check("usage events recorded", summary["events"] > 0)
    check("cost attributed to operations", len(summary["by_operation"]) > 0)
    check("total cost of one real save is under 2 cents",
          summary["estimated_usd"] < 0.02, f"${summary['estimated_usd']:.6f}")

    # ── Result ──────────────────────────────────────────────────────────────
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print("\n" + "=" * 78)
    print(f"{passed}/{total} checks passed")
    if passed < total:
        print("\nFailed:")
        for label, ok in results:
            if not ok:
                print(f"  - {label}")
    print("=" * 78)
    db.close()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
