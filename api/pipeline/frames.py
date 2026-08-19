"""Intelligent frame selection, OCR, and visual understanding.

A 15-second TikTok is ~450 frames. Analysing them all would be absurd and
ruinous. We pick a handful of *meaningful* ones:

  1. ffmpeg scene-change detection finds where the picture actually changes.
  2. If scene detection is thin (a single static talking-head shot), fall back
     to evenly spaced samples so we still cover the timeline.
  3. Perceptual hashing drops near-duplicates — a fixed camera on a countertop
     produces many identical frames.
  4. The survivors are capped at MAX_FRAMES_PER_VIDEO and sent to the vision
     model in ONE batched call, not one call per frame.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import FRAME_MAX_WIDTH, MAX_FRAMES_PER_VIDEO

logger = logging.getLogger(__name__)

_SCENE_THRESHOLD = 0.30


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@dataclass
class Frame:
    ts_ms: int
    path: str
    phash: Optional[str] = None
    ocr_text: Optional[str] = None
    vision_caption: Optional[str] = None

    def bytes_(self) -> bytes:
        return Path(self.path).read_bytes()


def probe_duration(video_path: str) -> Optional[float]:
    if not ffmpeg_available():
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.strip()
        return float(out)
    except Exception:
        return None


def _scene_timestamps(video_path: str, threshold: float = _SCENE_THRESHOLD) -> List[float]:
    """Timestamps where the picture changes materially."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-i", video_path, "-filter:v",
             f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
        )
        return sorted({round(float(m), 2)
                       for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)})
    except Exception as e:
        logger.debug("scene detection failed: %s", e)
        return []


def _even_timestamps(duration: float, n: int) -> List[float]:
    """Evenly spaced samples, avoiding the very first and last frames."""
    if duration <= 0 or n <= 0:
        return []
    if n == 1:
        return [duration / 2]
    step = duration / (n + 1)
    return [round(step * (i + 1), 2) for i in range(n)]


def select_timestamps(video_path: str, duration: Optional[float] = None,
                      max_frames: int = MAX_FRAMES_PER_VIDEO) -> List[float]:
    """Choose which moments are worth looking at.

    Short clips get fewer frames — a 15s TikTok does not need eight.
    """
    duration = duration or probe_duration(video_path) or 0.0
    if duration <= 0:
        return []

    if duration <= 10:
        budget = min(3, max_frames)
    elif duration <= 30:
        budget = min(5, max_frames)
    elif duration <= 90:
        budget = min(6, max_frames)
    else:
        budget = max_frames

    scenes = [t for t in _scene_timestamps(video_path) if 0.3 <= t <= duration - 0.2]

    if len(scenes) >= budget:
        # Spread the picks across the timeline rather than taking the first N,
        # which would cluster on a busy opening.
        step = len(scenes) / budget
        picks = [scenes[int(i * step)] for i in range(budget)]
    elif scenes:
        picks = list(scenes)
        need = budget - len(picks)
        for t in _even_timestamps(duration, need + 2):
            if len(picks) >= budget:
                break
            if all(abs(t - p) > max(0.8, duration * 0.05) for p in picks):
                picks.append(t)
    else:
        picks = _even_timestamps(duration, budget)

    return sorted(set(round(p, 2) for p in picks))[:budget]


def extract_frames(video_path: str, timestamps: List[float],
                   workdir: Optional[str] = None,
                   max_width: int = FRAME_MAX_WIDTH) -> List[Frame]:
    """Write the chosen frames to disk as small JPEGs."""
    if not ffmpeg_available() or not timestamps:
        return []
    out_dir = Path(workdir or tempfile.mkdtemp(prefix="sava_frames_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    frames: List[Frame] = []
    for i, ts in enumerate(timestamps):
        dest = out_dir / f"f{i:03d}_{int(ts * 1000)}.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(ts), "-i", video_path, "-frames:v", "1",
                 "-vf", f"scale={max_width}:-2", "-q:v", "4", str(dest)],
                capture_output=True, timeout=60, check=True,
            )
            if dest.exists() and dest.stat().st_size > 0:
                frames.append(Frame(ts_ms=int(ts * 1000), path=str(dest)))
        except Exception as e:
            logger.debug("frame extract failed at %.2fs: %s", ts, e)
    return frames


def perceptual_hash(image_path: str, hash_size: int = 8) -> Optional[str]:
    """dHash — cheap, no extra dependency beyond Pillow."""
    try:
        from PIL import Image
        with Image.open(image_path) as im:
            im = im.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
            px = list(im.getdata())
        bits = []
        for row in range(hash_size):
            base = row * (hash_size + 1)
            for col in range(hash_size):
                bits.append(px[base + col] > px[base + col + 1])
        return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"
    except Exception as e:
        logger.debug("phash failed: %s", e)
        return None


def _hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return 64


def deduplicate(frames: List[Frame], max_distance: int = 6) -> List[Frame]:
    """Drop near-identical frames. A static shot should cost one frame, not six."""
    kept: List[Frame] = []
    for f in frames:
        f.phash = f.phash or perceptual_hash(f.path)
        if f.phash is None:
            kept.append(f)
            continue
        if any(k.phash and _hamming(f.phash, k.phash) <= max_distance for k in kept):
            continue
        kept.append(f)
    return kept


# ─── Vision understanding ────────────────────────────────────────────────────

_VISION_SYSTEM = """You are reading frames sampled from a short social video.
For EACH frame return what is actually visible. Be literal and specific.

Return STRICT JSON, no markdown fences:
{"frames":[{"i":0,"ocr":"exact on-screen text, verbatim, empty string if none",
"caption":"one sentence describing what is shown",
"objects":["visible object or product"],
"place":"venue/location name if legibly shown, else empty"}]}

Rules:
- `ocr` must be text that is literally rendered on screen (captions, overlays,
  packaging, signage, price tags). Do not transcribe speech. Do not invent text.
- Do not identify or name individual people. Describe them generically.
- If a frame shows nothing useful, return empty strings rather than guessing."""


def analyze_frames(frames: List[Frame], *, router, mode=None,
                   content_hint: Optional[str] = None) -> Tuple[List[Frame], Any]:
    """One batched vision call for all selected frames.

    Returns (frames_with_ocr_and_captions, completion). Batching matters: eight
    separate calls would repeat the system prompt eight times.
    """
    from ..ai.base import Mode, TaskType

    if not frames:
        return frames, None

    hint = f"\nThis appears to be {content_hint} content." if content_hint else ""
    prompt = (
        f"{len(frames)} frames follow, in chronological order "
        f"(indices 0..{len(frames)-1}).{hint}\n"
        "Return one entry per frame."
    )
    images = [f.bytes_() for f in frames]

    completion = router.complete(
        TaskType.VISION_ANALYSIS,
        system=_VISION_SYSTEM,
        prompt=prompt,
        mode=mode or Mode.AUTO,
        json_mode=True,
        images=images,
        temperature=0.1,
        max_output_tokens=4096,
    )

    try:
        data = json.loads(completion.text or "{}")
        for entry in data.get("frames", []):
            i = int(entry.get("i", -1))
            if 0 <= i < len(frames):
                ocr = (entry.get("ocr") or "").strip()
                cap = (entry.get("caption") or "").strip()
                extra = []
                for obj in (entry.get("objects") or [])[:8]:
                    if obj:
                        extra.append(str(obj))
                place = (entry.get("place") or "").strip()
                if place:
                    extra.append(place)
                frames[i].ocr_text = ocr or None
                frames[i].vision_caption = (
                    cap + (f" [visible: {', '.join(extra)}]" if extra else "")
                ).strip() or None
    except Exception as e:
        logger.warning("vision JSON parse failed: %s", e)

    return frames, completion


def collect_visual_text(frames: List[Frame]) -> str:
    """Flatten frame findings into text for embedding and summarisation."""
    parts: List[str] = []
    for f in frames:
        ts = f"{f.ts_ms // 60000}:{(f.ts_ms // 1000) % 60:02d}"
        if f.ocr_text:
            parts.append(f"[{ts}] on-screen: {f.ocr_text}")
        if f.vision_caption:
            parts.append(f"[{ts}] {f.vision_caption}")
    return "\n".join(parts)


def cleanup_frames(frames: List[Frame]) -> None:
    for f in frames:
        try:
            p = Path(f.path)
            if p.exists() and "sava_" in str(p.parent):
                p.unlink()
        except Exception:
            pass
