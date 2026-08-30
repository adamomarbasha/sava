"""Sava's model router.

"Sava Auto" is not a model — it is this file. The user picks intent (Auto /
Fast / Advanced) and Sava decides which model actually runs, per task, without
ever surfacing a vendor name in the product.

Routing principles, in order:
  1. Deterministic code beats a model. If a task can be done without inference,
     it is not in this table at all.
  2. Cheapest model that is good enough for the task.
  3. Escalate only on evidence — question shape, content length, source count —
     never merely because a stronger model exists.
"""
from __future__ import annotations

import logging
import os as _os
import re
from typing import Dict, Optional, Tuple

from .base import Capability, Mode, ModelSpec, TaskType
from .gemini import GeminiProvider

logger = logging.getLogger(__name__)

TEXT_JSON = frozenset({Capability.TEXT, Capability.JSON})
TEXT_JSON_VISION = frozenset({Capability.TEXT, Capability.JSON, Capability.VISION})

# Model registry. IDs were probed against the live key on 2026-08-18 — the
# published model list includes entries that 404 for new keys (every 2.5 model
# is retired), so this table contains only models verified to respond.
#
# Note on Gemini 3.x: these are reasoning models that spend hidden thinking
# tokens from the same output budget. A small max_output_tokens returns an empty
# string rather than a truncated one, so floors here are deliberately generous.
CHEAP = ModelSpec(
    provider="gemini", model="gemini-3.5-flash-lite", capabilities=TEXT_JSON_VISION,
    usd_per_1m_input=0.30, usd_per_1m_output=2.50, max_output_tokens=4096,
    notes="Commodity extraction, classification, naming. Vision-capable.",
)
BALANCED = ModelSpec(
    provider="gemini", model="gemini-3.7-flash", capabilities=TEXT_JSON_VISION,
    usd_per_1m_input=0.75, usd_per_1m_output=3.75, max_output_tokens=8192,
    notes="Multi-source synthesis, vision, long summaries. Promo pricing to 2026-12-31.",
)
# The configured key has no Pro quota (429), so Advanced resolves to the
# strongest model that actually answers. Set SAVA_STRONG_MODEL once Pro is
# available on the billing account.
STRONG = ModelSpec(
    provider="gemini",
    model=_os.getenv("SAVA_STRONG_MODEL", "gemini-3.7-flash"),
    capabilities=TEXT_JSON_VISION,
    usd_per_1m_input=float(_os.getenv("SAVA_STRONG_IN_PRICE", "0.75")),
    usd_per_1m_output=float(_os.getenv("SAVA_STRONG_OUT_PRICE", "3.75")),
    max_output_tokens=8192,
    notes="Planning, comparison, itineraries.",
)
EMBED = ModelSpec(
    provider="gemini", model="gemini-embedding-001",
    capabilities=frozenset({Capability.EMBEDDING}),
    usd_per_1m_input=0.15, usd_per_1m_output=0.0,
    notes="1536-dim Matryoshka truncation.",
)

# Degradation order. If a model is retired, over quota, or unavailable, the
# router steps down rather than failing the user's request outright.
FALLBACK_CHAIN = [BALANCED, CHEAP]

# (task, mode) -> spec. AUTO entries are the default; FAST/ADVANCED override.
_TABLE: Dict[Tuple[TaskType, Mode], ModelSpec] = {}


def _register(task: TaskType, auto: ModelSpec, fast: ModelSpec, advanced: ModelSpec) -> None:
    _TABLE[(task, Mode.AUTO)] = auto
    _TABLE[(task, Mode.FAST)] = fast
    _TABLE[(task, Mode.ADVANCED)] = advanced


_register(TaskType.CLASSIFICATION,          CHEAP,    CHEAP,    CHEAP)
_register(TaskType.SUMMARY_SHORT,           CHEAP,    CHEAP,    BALANCED)
_register(TaskType.SUMMARY_LONG,            BALANCED, CHEAP,    BALANCED)
_register(TaskType.STRUCTURED_EXTRACTION,   CHEAP,    CHEAP,    BALANCED)
# Vision reads text off a screenshot — an OCR-shaped task, not a reasoning one.
# Measured on a real iPhone screenshot: flash-lite 1.3s vs 3.7-flash 11.5s for
# an identical, correct reading. The Action Button is latency-critical, so AUTO
# uses the fast tier and only ADVANCED pays for the reasoning model.
_register(TaskType.VISION_ANALYSIS,         CHEAP,    CHEAP,    BALANCED)
_register(TaskType.OCR_CLEANUP,             CHEAP,    CHEAP,    CHEAP)
_register(TaskType.ASK_THIS_SIMPLE,         CHEAP,    CHEAP,    BALANCED)
_register(TaskType.ASK_THIS_REASONING,      BALANCED, CHEAP,    STRONG)
_register(TaskType.ASK_SAVA,                BALANCED, CHEAP,    STRONG)
_register(TaskType.ASK_SAVA_COMPLEX,        BALANCED, CHEAP,    STRONG)
_register(TaskType.COLLECTION_NAMING,       CHEAP,    CHEAP,    CHEAP)


# ─── Question-shape heuristics for Sava Auto ─────────────────────────────────
# Deterministic, free, and inspectable. A model call to decide which model to
# call would be self-defeating.

# Strong cues: the question inherently requires synthesis, comparison, or
# planning. These always escalate.
_STRONG_REASONING = re.compile(
    r"\b(compare|comparison|versus|vs\.?|difference|differences|rank|ranking|"
    r"itinerary|plan|planning|schedule|should i|pros and cons|trade[- ]?offs?|"
    r"why|how come|explain|analy[sz]e|analysis|synthesi[sz]e|combine|"
    r"build me|put together|walk me through|strategy|decide|"
    r"across (?:all|my|everything)|from everything|all the .* i(?:'ve)? saved|"
    r"main ideas|key ideas|key points|takeaways|main points|themes|"
    r"overview of|patterns|trends|what do .* have in common)\b",
    re.IGNORECASE,
)

# Weak cues: often appear in plainly factual lookups ("what laptop did he
# recommend?"), so they only escalate when the question is not a short
# fact-shaped one.
_WEAK_REASONING = re.compile(
    r"\b(recommend|recommendation|recommendations|best|worst|worth it|"
    r"opinion|thoughts)\b",
    re.IGNORECASE,
)

# Fact-shaped openers: single-value retrieval from the retrieved context.
_SIMPLE_PATTERNS = re.compile(
    r"^\s*(what|which|who|where|when|how (?:much|many|long|hot|cold|old)|"
    r"did|does|do|is|are|was|were|can|name|list)\b",
    re.IGNORECASE,
)


def classify_question(question: str, *, source_count: int = 1) -> bool:
    """True when the question warrants an escalated (reasoning) path.

    Deterministic and cheap on purpose — spending a model call to decide which
    model to call would defeat the point.
    """
    q = (question or "").strip()
    if not q:
        return False
    words = len(q.split())

    if _STRONG_REASONING.search(q):
        return True

    is_fact_shaped = bool(_SIMPLE_PATTERNS.match(q))

    # A short, fact-shaped question stays cheap even with a weak cue in it.
    if is_fact_shaped and words <= 14:
        return False

    if _WEAK_REASONING.search(q):
        return True
    if words >= 22:
        return True
    if source_count >= 8 and words >= 12:
        return True
    return words >= 16


def resolve_task(
    base: TaskType,
    *,
    question: Optional[str] = None,
    source_count: int = 1,
    mode: Mode = Mode.AUTO,
) -> TaskType:
    """Pick the concrete task variant. Only AUTO inspects the question."""
    if mode is not Mode.AUTO:
        return base
    if base is TaskType.ASK_THIS_SIMPLE and question:
        return TaskType.ASK_THIS_REASONING if classify_question(
            question, source_count=source_count) else base
    if base is TaskType.ASK_SAVA and question:
        return TaskType.ASK_SAVA_COMPLEX if classify_question(
            question, source_count=source_count) else base
    return base


class ModelRouter:
    """Chooses a provider+model for a task and executes it."""

    def __init__(self):
        self._providers = {}
        gem = GeminiProvider()
        if gem.is_available():
            self._providers["gemini"] = gem

    # -- introspection -------------------------------------------------------
    def is_available(self) -> bool:
        return bool(self._providers)

    def provider_for(self, spec: ModelSpec):
        p = self._providers.get(spec.provider)
        if p is None:
            raise RuntimeError(
                f"Provider '{spec.provider}' is not configured. "
                f"Configured: {sorted(self._providers) or 'none'}"
            )
        return p

    def spec_for(self, task: TaskType, mode: Mode = Mode.AUTO) -> ModelSpec:
        if task is TaskType.EMBEDDING:
            return EMBED
        spec = _TABLE.get((task, mode))
        if spec is None:
            spec = _TABLE.get((task, Mode.AUTO), CHEAP)
        return spec

    # -- execution -----------------------------------------------------------
    def complete(
        self,
        task: TaskType,
        *,
        prompt: str,
        system: Optional[str] = None,
        mode: Mode = Mode.AUTO,
        json_mode: bool = False,
        temperature: float = 0.3,
        max_output_tokens: Optional[int] = None,
        history=None,
        images=None,
        spec: Optional[ModelSpec] = None,
    ):
        chosen = spec or self.spec_for(task, mode)
        if images and not chosen.supports(Capability.VISION):
            chosen = BALANCED

        # Degrade rather than fail: a retired model, an exhausted quota, or a
        # provider outage steps down the chain instead of erroring the request.
        attempts = [chosen] + [s for s in FALLBACK_CHAIN if s.model != chosen.model]
        last_err = None
        completion = None
        for i, cand in enumerate(attempts):
            try:
                provider = self.provider_for(cand)
                completion = provider.complete(
                    spec=cand, system=system, prompt=prompt, json_mode=json_mode,
                    temperature=temperature, max_output_tokens=max_output_tokens,
                    history=history, images=images,
                )
                chosen = cand
                if i:
                    logger.warning("model fell back to %s after %s", cand.model, last_err)
                break
            except Exception as e:
                last_err = e
                if i == len(attempts) - 1:
                    raise
                continue
        usd = (completion.input_tokens * chosen.usd_per_1m_input / 1e6
               + completion.output_tokens * chosen.usd_per_1m_output / 1e6)
        setattr(completion, "_usd", usd)
        return completion

    def complete_stream(
        self,
        task: TaskType,
        *,
        prompt: str,
        system: Optional[str] = None,
        mode: Mode = Mode.AUTO,
        temperature: float = 0.3,
        max_output_tokens: Optional[int] = None,
        history=None,
        spec: Optional[ModelSpec] = None,
    ):
        """Yield `CompletionChunk`s, falling back through the same chain.

        ── Why the fallback is *not* per-chunk ──────────────────────────────
        The non-streaming path can retry a failed model transparently because
        nothing has been shown to anyone yet. Streaming cannot: once a token has
        left the building, retrying on a different model would either duplicate
        text or contradict what the user already read. So a model is chosen and
        may fall back *before the first chunk*, and after that a failure is a
        failure — reported as one, mid-answer.

        A provider with no streaming support is not faked. It runs `complete()`
        and emits one chunk, which shows up as an answer that arrives all at
        once. That is the truth about that provider.
        """
        from .base import CompletionChunk, ProviderError

        chosen = spec or self.spec_for(task, mode)
        attempts = [chosen] + [s for s in FALLBACK_CHAIN if s.model != chosen.model]

        last_err = None
        for i, cand in enumerate(attempts):
            provider = self.provider_for(cand)
            try:
                iterator = provider.complete_stream(
                    spec=cand, system=system, prompt=prompt,
                    temperature=temperature, max_output_tokens=max_output_tokens,
                    history=history,
                )
                first = next(iterator)          # forces the request to open
            except NotImplementedError:
                completion = provider.complete(
                    spec=cand, system=system, prompt=prompt, json_mode=False,
                    temperature=temperature, max_output_tokens=max_output_tokens,
                    history=history, images=None,
                )
                usd = (completion.input_tokens * cand.usd_per_1m_input / 1e6
                       + completion.output_tokens * cand.usd_per_1m_output / 1e6)
                yield CompletionChunk(text=completion.text)
                chunk = CompletionChunk(done=True,
                                        input_tokens=completion.input_tokens,
                                        output_tokens=completion.output_tokens)
                setattr(chunk, "_usd", usd)
                setattr(chunk, "_model", cand.model)
                yield chunk
                return
            except Exception as e:
                last_err = e
                if i == len(attempts) - 1:
                    raise
                logger.warning("stream falling back from %s: %s", cand.model, e)
                continue

            if i:
                logger.warning("stream fell back to %s after %s", cand.model, last_err)

            yield first
            for chunk in iterator:
                if chunk.done:
                    usd = (chunk.input_tokens * cand.usd_per_1m_input / 1e6
                           + chunk.output_tokens * cand.usd_per_1m_output / 1e6)
                    setattr(chunk, "_usd", usd)
                    setattr(chunk, "_model", cand.model)
                yield chunk
            return

    def embed(self, texts, *, task_type: str = "retrieval_document", dim: Optional[int] = None):
        from ..config import EMBED_DIM
        spec = EMBED
        provider = self.provider_for(spec)
        result = provider.embed(
            model=spec.model, texts=list(texts), dim=dim or EMBED_DIM, task_type=task_type
        )
        setattr(result, "_usd", result.input_tokens * spec.usd_per_1m_input / 1e6)
        return result


_router: Optional[ModelRouter] = None


def get_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router


def describe_modes() -> list:
    """Provider-neutral copy for the client's picker. No vendor names."""
    return [
        {"id": "auto", "title": "Sava Auto", "subtitle": "Best model automatically",
         "is_default": True},
        {"id": "fast", "title": "Fast", "subtitle": "Quick everyday questions"},
        {"id": "advanced", "title": "Advanced", "subtitle": "Deeper reasoning"},
    ]
