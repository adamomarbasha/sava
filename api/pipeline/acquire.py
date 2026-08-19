"""Media and transcript acquisition.

Two rules govern this module, both from the unit-economics audit:

  1. **Download once.** Acquisition (proxy bandwidth) is ~78% of the cost of a
     save, far more than inference. When both audio and frames are needed we
     fetch a single low-resolution file and derive both from it — never two
     downloads of the same video.
  2. **Never re-acquire for a question.** Everything here is called by the
     ingestion pipeline and persisted. Summary, Ask This, and Ask Sava read the
     database; they never reach the network.

Every fetch reports the bytes it moved so `usage_events` can price it.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import (
    DOWNLOAD_MAX_HEIGHT, YTDLP_COOKIEFILE, YTDLP_COOKIES_FROM_BROWSER,
)

logger = logging.getLogger(__name__)


@dataclass
class AcquisitionResult:
    ok: bool
    kind: str                       # metadata | audio | video
    path: Optional[str] = None
    bytes_moved: int = 0
    wall_ms: int = 0
    duration_s: Optional[float] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def _ydl_base_opts() -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "skip_unavailable_fragments": True,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
        },
    }
    # Residential proxy for platforms that block datacenter ASNs. Without this,
    # production fetches fail on the first call regardless of rate.
    proxy = os.getenv("SAVA_PROXY_URL")
    if proxy:
        opts["proxy"] = proxy
    if YTDLP_COOKIES_FROM_BROWSER:
        opts["cookiesfrombrowser"] = (YTDLP_COOKIES_FROM_BROWSER,)
    elif YTDLP_COOKIEFILE:
        opts["cookiefile"] = YTDLP_COOKIEFILE
    return opts


def _parse_json3(payload: dict) -> List[Dict[str, Any]]:
    """YouTube's json3 caption format -> our segment shape."""
    segments: List[Dict[str, Any]] = []
    for ev in (payload or {}).get("events", []) or []:
        text = "".join(s.get("utf8", "") for s in (ev.get("segs") or [])).strip()
        if not text or text == "\n":
            continue
        segments.append({
            "text": text,
            "start": float(ev.get("tStartMs", 0)) / 1000.0,
            "duration": float(ev.get("dDurationMs", 0) or 0) / 1000.0,
        })
    return segments


def fetch_captions_via_ytdlp(url: str, languages: Optional[List[str]] = None
                             ) -> AcquisitionResult:
    """Pull native/auto captions through yt-dlp.

    Preferred over `youtube-transcript-api` because it reuses the same session,
    proxy, and cookies as metadata extraction — that library is separately
    IP-blocked and has its own throttling.
    """
    started = time.monotonic()
    langs = languages or ["en", "en-US", "en-GB"]
    try:
        import requests
        import yt_dlp

        opts = _ydl_base_opts()
        opts.update({"skip_download": True, "writesubtitles": True,
                     "writeautomaticsub": True, "subtitlesformat": "json3"})
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return AcquisitionResult(False, "metadata", error="no info")

        tracks = {}
        tracks.update(info.get("subtitles") or {})           # human captions win
        auto = info.get("automatic_captions") or {}
        for lang, val in auto.items():
            tracks.setdefault(lang, val)

        chosen = next((tracks[l] for l in langs if l in tracks), None)
        if chosen is None:
            chosen = next((v for k, v in tracks.items() if k.startswith("en")), None)
        if chosen is None and tracks:
            chosen = next(iter(tracks.values()))
        if not chosen:
            return AcquisitionResult(False, "metadata", error="no caption tracks")

        entry = next((c for c in chosen if c.get("ext") == "json3"), chosen[0])
        sub_url = entry.get("url")
        if not sub_url:
            return AcquisitionResult(False, "metadata", error="caption track has no url")

        proxies = ({"http": os.environ["SAVA_PROXY_URL"],
                    "https": os.environ["SAVA_PROXY_URL"]}
                   if os.getenv("SAVA_PROXY_URL") else None)
        resp = requests.get(sub_url, timeout=30, proxies=proxies)
        resp.raise_for_status()
        segments = _parse_json3(resp.json())
        if not segments:
            return AcquisitionResult(False, "metadata", error="caption track was empty")

        return AcquisitionResult(
            True, "metadata", bytes_moved=len(resp.content),
            wall_ms=int((time.monotonic() - started) * 1000),
            duration_s=info.get("duration"),
            metadata={"segments": segments, "language": "en", "source": "captions",
                      "info": info},
        )
    except Exception as e:
        return AcquisitionResult(False, "metadata", error=str(e)[:400],
                                 wall_ms=int((time.monotonic() - started) * 1000))


def _dir_bytes(path: Path) -> int:
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except Exception:
        return 0


def fetch_metadata(url: str) -> AcquisitionResult:
    """Platform metadata without downloading the media itself."""
    started = time.monotonic()
    try:
        import yt_dlp
        opts = _ydl_base_opts()
        opts["skip_download"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return AcquisitionResult(False, "metadata", error="no metadata returned")
        meta = {
            "title": info.get("title"),
            "description": info.get("description"),
            "duration": info.get("duration"),
            "uploader": info.get("uploader") or info.get("channel"),
            "uploader_id": info.get("uploader_id") or info.get("channel_id"),
            "thumbnail": info.get("thumbnail"),
            "upload_date": info.get("upload_date"),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "comment_count": info.get("comment_count"),
            "tags": info.get("tags") or [],
            "categories": info.get("categories") or [],
            "webpage_url": info.get("webpage_url") or url,
            "extractor": info.get("extractor"),
            "subtitles": sorted((info.get("subtitles") or {}).keys()),
            "automatic_captions": sorted((info.get("automatic_captions") or {}).keys())[:5],
        }
        return AcquisitionResult(
            True, "metadata",
            bytes_moved=len(json.dumps(meta, default=str).encode()),  # request cost is small; body is a proxy for it
            wall_ms=int((time.monotonic() - started) * 1000),
            duration_s=info.get("duration"),
            metadata=meta,
        )
    except Exception as e:
        return AcquisitionResult(
            False, "metadata", error=str(e)[:400],
            wall_ms=int((time.monotonic() - started) * 1000),
        )


def download_audio(url: str, workdir: Optional[str] = None) -> AcquisitionResult:
    """Audio-only download for transcription.

    The prior implementation used `format: 'best'` — a full-resolution video
    pulled through a paid proxy in order to transcribe speech. Audio-only is
    roughly 4.5x fewer bytes on short-form content.
    """
    started = time.monotonic()
    tmp = Path(workdir or tempfile.mkdtemp(prefix="sava_audio_"))
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        import yt_dlp
        opts = _ydl_base_opts()
        opts.update({
            "format": "bestaudio[ext=m4a]/bestaudio/worstvideo+bestaudio/worst",
            "outtmpl": str(tmp / "audio.%(ext)s"),
        })
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        files = [p for p in tmp.iterdir() if p.is_file()]
        if not files:
            return AcquisitionResult(False, "audio", error="download produced no file")
        path = max(files, key=lambda p: p.stat().st_size)
        return AcquisitionResult(
            True, "audio", path=str(path), bytes_moved=_dir_bytes(tmp),
            wall_ms=int((time.monotonic() - started) * 1000),
            duration_s=(info or {}).get("duration"),
            metadata={"ext": path.suffix.lstrip(".")},
        )
    except Exception as e:
        return AcquisitionResult(False, "audio", error=str(e)[:400],
                                 wall_ms=int((time.monotonic() - started) * 1000))


def download_video_lowres(url: str, workdir: Optional[str] = None,
                          max_height: int = DOWNLOAD_MAX_HEIGHT) -> AcquisitionResult:
    """One low-resolution download that serves BOTH frame extraction and audio.

    Used only when the platform strategy has decided visual analysis is needed.
    """
    started = time.monotonic()
    tmp = Path(workdir or tempfile.mkdtemp(prefix="sava_video_"))
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        import yt_dlp
        opts = _ydl_base_opts()
        opts.update({
            "format": (
                f"best[height<={max_height}][ext=mp4]/"
                f"best[height<={max_height}]/worst[ext=mp4]/worst"
            ),
            "outtmpl": str(tmp / "video.%(ext)s"),
        })
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        files = [p for p in tmp.iterdir() if p.is_file()]
        if not files:
            return AcquisitionResult(False, "video", error="download produced no file")
        path = max(files, key=lambda p: p.stat().st_size)
        return AcquisitionResult(
            True, "video", path=str(path), bytes_moved=_dir_bytes(tmp),
            wall_ms=int((time.monotonic() - started) * 1000),
            duration_s=(info or {}).get("duration"),
        )
    except Exception as e:
        return AcquisitionResult(False, "video", error=str(e)[:400],
                                 wall_ms=int((time.monotonic() - started) * 1000))


# ─── Transcript acquisition ──────────────────────────────────────────────────

def fetch_native_captions(url: str, languages: Optional[List[str]] = None) -> AcquisitionResult:
    """YouTube's own captions. Free, instant, and the reason YouTube is cheap.

    Reuses the existing `transcript_service`, so the behaviour the current app
    already depends on is unchanged.
    """
    primary = fetch_captions_via_ytdlp(url, languages)
    if primary.ok:
        return primary

    started = time.monotonic()
    try:
        from ..transcript_service import get_youtube_transcript
        result = get_youtube_transcript(url, languages=languages or ["en"])
        if result.get("success") and result.get("transcript"):
            segs = result["transcript"]
            total = sum(float(s.get("duration", 0) or 0) for s in segs if isinstance(s, dict))
            return AcquisitionResult(
                True, "metadata", bytes_moved=len(json.dumps(segs, default=str).encode()),
                wall_ms=int((time.monotonic() - started) * 1000),
                duration_s=total,
                metadata={"segments": segs, "language": result.get("language", "en"),
                          "source": "captions"},
            )
        return AcquisitionResult(False, "metadata",
                                 error=result.get("error") or "no captions available",
                                 wall_ms=int((time.monotonic() - started) * 1000))
    except Exception as e:
        return AcquisitionResult(False, "metadata", error=str(e)[:400],
                                 wall_ms=int((time.monotonic() - started) * 1000))


def transcribe_audio(audio_path: str) -> AcquisitionResult:
    """Local Whisper transcription.

    Kept local because this deployment has no hosted-ASR credential. The audit
    recommends a hosted endpoint (Groq whisper-large-v3-turbo at $0.04/hr) once
    a key exists; swapping this function is the only change required.
    """
    started = time.monotonic()
    try:
        from ..config import WHISPER_COMPUTE_TYPE, WHISPER_DEVICE, WHISPER_MODEL
        segments: List[Dict[str, Any]] = []
        language = "en"
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE,
                                 compute_type=WHISPER_COMPUTE_TYPE)
            segs, info = model.transcribe(audio_path, beam_size=1, vad_filter=True)
            language = getattr(info, "language", "en") or "en"
            for s in segs:
                segments.append({"text": s.text.strip(), "start": float(s.start),
                                 "duration": float(s.end - s.start)})
        except ImportError:
            import whisper
            model = whisper.load_model(WHISPER_MODEL)
            res = model.transcribe(audio_path)
            language = res.get("language", "en")
            for s in res.get("segments", []):
                segments.append({"text": (s.get("text") or "").strip(),
                                 "start": float(s.get("start", 0)),
                                 "duration": float(s.get("end", 0) - s.get("start", 0))})

        segments = [s for s in segments if s["text"]]
        total = sum(s["duration"] for s in segments)
        return AcquisitionResult(
            True, "audio", wall_ms=int((time.monotonic() - started) * 1000),
            duration_s=total,
            metadata={"segments": segments, "language": language, "source": "asr",
                      "model": WHISPER_MODEL},
        )
    except Exception as e:
        return AcquisitionResult(False, "audio", error=str(e)[:400],
                                 wall_ms=int((time.monotonic() - started) * 1000))


def extract_audio_from_video(video_path: str, out_path: Optional[str] = None) -> Optional[str]:
    """Pull the audio track out of an already-downloaded video (no re-fetch)."""
    if not shutil.which("ffmpeg"):
        return None
    out = out_path or str(Path(video_path).with_suffix(".m4a"))
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "copy", out],
            check=True, capture_output=True, timeout=180,
        )
        return out
    except Exception:
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "aac", "-b:a", "64k", out],
                check=True, capture_output=True, timeout=300,
            )
            return out
        except Exception as e:
            logger.warning("audio extraction failed: %s", e)
            return None


def cleanup(path: Optional[str]) -> None:
    """Remove a temp working directory. Media is never retained after ingest."""
    if not path:
        return
    p = Path(path)
    target = p.parent if p.is_file() else p
    try:
        if target.exists() and "sava_" in target.name:
            shutil.rmtree(target, ignore_errors=True)
    except Exception:
        pass
