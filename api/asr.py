"""Speech-to-text providers.

Transcription is the one stage that is both expensive and slow, and the way it
was wired was the clearest thing standing between this codebase and real scale:
`faster_whisper` ran **inside the worker process, on CPU**. One minute of audio
costs roughly a minute of a core. At a hundred saves an hour that is already a
dedicated machine; at a hundred thousand users it is not a system, it is a
queue that never drains.

So transcription is now a provider interface with three implementations:

  * `LocalWhisperASR`  — development and offline testing. Explicit opt-in.
  * `HostedWhisperASR` — any OpenAI-compatible `/audio/transcriptions` endpoint.
    That shape is implemented by several vendors and by self-hosted servers, so
    choosing one is an endpoint and a key, not a code change. **Nothing is
    configured by default and no account is created by this file existing.**
  * `UnavailableASR`   — the default. Reports "no provider", which the pipeline
    treats as "this item has no transcript", not as a failure.

The last one matters: with no ASR configured, a TikTok still gets metadata, a
thumbnail, frames, OCR, understanding and embeddings. It is simply missing
speech. That degradation is deliberate and is what keeps an unconfigured
deployment honest instead of broken.
"""
from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Providers are billed per second of audio, so a ceiling here is a ceiling on
# the bill. Long-form YouTube never reaches ASR anyway — it has captions.
MAX_AUDIO_SECONDS = int(os.getenv("SAVA_ASR_MAX_SECONDS", "1800"))


@dataclass
class TranscriptionResult:
    ok: bool
    segments: List[Dict[str, Any]] = field(default_factory=list)
    language: str = "en"
    provider: str = "none"
    model: Optional[str] = None
    audio_seconds: float = 0.0
    wall_ms: int = 0
    error: Optional[str] = None

    @property
    def text(self) -> str:
        return " ".join(s.get("text", "") for s in self.segments).strip()


class ASRProvider(ABC):
    """Turns an audio file into timed segments."""

    name: str = "asr"

    @abstractmethod
    def transcribe(self, audio_path: str, *, language: Optional[str] = None
                   ) -> TranscriptionResult: ...

    @property
    def available(self) -> bool:
        return True

    @property
    def runs_in_process(self) -> bool:
        """True when transcription consumes this machine's CPU.

        The scheduler uses this to decide whether ASR needs its own concurrency
        budget: a hosted provider is an HTTP call and can be parallel, a local
        model is a core each and must not be.
        """
        return False


class UnavailableASR(ASRProvider):
    """The default. Honest about having no transcription capability."""

    name = "none"

    def transcribe(self, audio_path: str, *, language: Optional[str] = None
                   ) -> TranscriptionResult:
        return TranscriptionResult(
            ok=False, provider=self.name,
            error="no ASR provider configured (set SAVA_ASR_PROVIDER)",
        )

    @property
    def available(self) -> bool:
        return False


class LocalWhisperASR(ASRProvider):
    """Whisper in this process. Development and offline testing only.

    Kept because it makes the pipeline runnable end-to-end on a laptop with no
    credentials, which is genuinely useful. It must not be the production
    default, and `runs_in_process` says so out loud.
    """

    name = "local-whisper"

    def __init__(self, model: Optional[str] = None):
        from .config import WHISPER_MODEL
        self.model_name = model or WHISPER_MODEL

    @property
    def runs_in_process(self) -> bool:
        return True

    def transcribe(self, audio_path: str, *, language: Optional[str] = None
                   ) -> TranscriptionResult:
        started = time.monotonic()
        segments: List[Dict[str, Any]] = []
        detected = language or "en"
        try:
            try:
                from faster_whisper import WhisperModel
                from .config import WHISPER_COMPUTE_TYPE, WHISPER_DEVICE
                model = WhisperModel(self.model_name, device=WHISPER_DEVICE,
                                     compute_type=WHISPER_COMPUTE_TYPE)
                segs, info = model.transcribe(audio_path, beam_size=1, vad_filter=True)
                detected = getattr(info, "language", detected) or detected
                for s in segs:
                    segments.append({"text": s.text.strip(), "start": float(s.start),
                                     "duration": float(s.end - s.start)})
            except ImportError:
                import whisper
                model = whisper.load_model(self.model_name)
                res = model.transcribe(audio_path)
                detected = res.get("language", detected)
                for s in res.get("segments", []):
                    segments.append({
                        "text": (s.get("text") or "").strip(),
                        "start": float(s.get("start", 0)),
                        "duration": float(s.get("end", 0) - s.get("start", 0)),
                    })

            segments = [s for s in segments if s["text"]]
            return TranscriptionResult(
                ok=bool(segments), segments=segments, language=detected,
                provider=self.name, model=self.model_name,
                audio_seconds=sum(s["duration"] for s in segments),
                wall_ms=int((time.monotonic() - started) * 1000),
                error=None if segments else "transcription produced no speech",
            )
        except Exception as e:
            return TranscriptionResult(
                ok=False, provider=self.name, error=str(e)[:400],
                wall_ms=int((time.monotonic() - started) * 1000),
            )


class HostedWhisperASR(ASRProvider):
    """Any OpenAI-compatible `/audio/transcriptions` endpoint.

    Deliberately generic. Several hosted providers and several self-hosted
    servers speak this shape, so the choice of vendor is `SAVA_ASR_BASE_URL` +
    `SAVA_ASR_API_KEY` and nothing else. Sava does not prefer, recommend or
    contract with any of them from here.
    """

    name = "hosted-whisper"

    def __init__(self, *, base_url: str, api_key: str, model: str,
                 timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model
        self.timeout = timeout

    def transcribe(self, audio_path: str, *, language: Optional[str] = None
                   ) -> TranscriptionResult:
        import requests

        started = time.monotonic()
        path = Path(audio_path)
        if not path.is_file():
            return TranscriptionResult(ok=False, provider=self.name,
                                       error="audio file missing")
        try:
            with path.open("rb") as fh:
                data = {"model": self.model_name, "response_format": "verbose_json"}
                if language:
                    data["language"] = language
                response = requests.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files={"file": (path.name, fh)}, data=data, timeout=self.timeout,
                )
            response.raise_for_status()
            payload = response.json()

            segments = [
                {"text": (s.get("text") or "").strip(),
                 "start": float(s.get("start") or 0),
                 "duration": float((s.get("end") or 0) - (s.get("start") or 0))}
                for s in (payload.get("segments") or [])
            ]
            segments = [s for s in segments if s["text"]]

            # Some endpoints return only flat text. A transcript without
            # timings is still worth having; it just cannot be cited.
            if not segments and (payload.get("text") or "").strip():
                segments = [{"text": payload["text"].strip(), "start": 0.0,
                             "duration": float(payload.get("duration") or 0)}]

            return TranscriptionResult(
                ok=bool(segments), segments=segments,
                language=payload.get("language") or language or "en",
                provider=self.name, model=self.model_name,
                audio_seconds=float(payload.get("duration") or 0)
                or sum(s["duration"] for s in segments),
                wall_ms=int((time.monotonic() - started) * 1000),
                error=None if segments else "empty transcription",
            )
        except Exception as e:
            return TranscriptionResult(
                ok=False, provider=self.name, error=str(e)[:400],
                wall_ms=int((time.monotonic() - started) * 1000),
            )


_provider: Optional[ASRProvider] = None


def get_asr() -> ASRProvider:
    """The configured provider.

    `SAVA_ASR_PROVIDER` = `hosted` | `local` | `none` (default `none`).
    """
    global _provider
    if _provider is not None:
        return _provider

    choice = (os.getenv("SAVA_ASR_PROVIDER") or "none").strip().lower()

    if choice == "hosted":
        base = os.getenv("SAVA_ASR_BASE_URL")
        key = os.getenv("SAVA_ASR_API_KEY")
        model = os.getenv("SAVA_ASR_MODEL", "whisper-1")
        if base and key:
            _provider = HostedWhisperASR(base_url=base, api_key=key, model=model)
            logger.info("ASR provider: hosted (%s, model=%s)", base, model)
            return _provider
        logger.error("SAVA_ASR_PROVIDER=hosted but SAVA_ASR_BASE_URL/"
                     "SAVA_ASR_API_KEY are not set; ASR disabled")

    elif choice == "local":
        _provider = LocalWhisperASR()
        logger.warning("ASR provider: LOCAL WHISPER — CPU-bound and in-process. "
                       "Development only; do not run this on an API host under load.")
        return _provider

    _provider = UnavailableASR()
    logger.info("ASR provider: none. Items with no captions will have no "
                "transcript; every other stage still runs.")
    return _provider


def reset_asr() -> None:
    """Tests only."""
    global _provider
    _provider = None
