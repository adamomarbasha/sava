"""Provider-neutral AI interfaces.

Sava is the product; models are infrastructure. Nothing above this layer names a
vendor. Swapping Gemini for OpenAI should be a registry change, not a rewrite of
every feature.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional, Sequence


class TaskType(str, Enum):
    CLASSIFICATION = "classification"
    SUMMARY_SHORT = "summary_short"
    SUMMARY_LONG = "summary_long"
    STRUCTURED_EXTRACTION = "structured_extraction"
    VISION_ANALYSIS = "vision_analysis"
    OCR_CLEANUP = "ocr_cleanup"
    ASK_THIS_SIMPLE = "ask_this_simple"
    ASK_THIS_REASONING = "ask_this_reasoning"
    ASK_SAVA = "ask_sava"
    ASK_SAVA_COMPLEX = "ask_sava_complex"
    COLLECTION_NAMING = "collection_naming"
    EMBEDDING = "embedding"


class Mode(str, Enum):
    """User-facing selector. Deliberately not model names."""
    AUTO = "auto"
    FAST = "fast"
    ADVANCED = "advanced"


class Capability(str, Enum):
    TEXT = "text"
    JSON = "json"
    VISION = "vision"
    EMBEDDING = "embedding"


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str
    capabilities: frozenset
    usd_per_1m_input: float
    usd_per_1m_output: float
    max_output_tokens: int = 2048
    notes: str = ""

    def supports(self, cap: Capability) -> bool:
        return cap in self.capabilities


@dataclass
class Completion:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    wall_ms: int = 0
    raw: Any = None

    @property
    def estimated_usd(self) -> float:
        return getattr(self, "_usd", 0.0)


@dataclass
class CompletionChunk:
    """One piece of a streaming completion.

    `text` is a *delta* — the new characters since the previous chunk, not the
    accumulated answer. Providers differ on this and getting it wrong produces
    an answer that repeats itself with every chunk, so the contract is stated
    here rather than left to each implementation.

    The final chunk carries `done=True` and whatever usage the provider
    reported; everything before it carries text and nothing else.
    """
    text: str = ""
    done: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    raw: Any = None


@dataclass
class EmbeddingResult:
    vectors: List[Sequence[float]]
    provider: str
    model: str
    input_tokens: int = 0
    wall_ms: int = 0
    dim: int = 0


class AIProvider(abc.ABC):
    """Minimal surface every provider must implement."""

    name: str = "abstract"

    @abc.abstractmethod
    def is_available(self) -> bool:
        ...

    @abc.abstractmethod
    def complete(
        self,
        *,
        spec: ModelSpec,
        system: Optional[str],
        prompt: str,
        json_mode: bool = False,
        temperature: float = 0.3,
        max_output_tokens: Optional[int] = None,
        history: Optional[List[Dict[str, str]]] = None,
        images: Optional[List[bytes]] = None,
    ) -> Completion:
        ...

    @abc.abstractmethod
    def complete_stream(
        self,
        *,
        spec: "ModelSpec",
        system: Optional[str],
        prompt: str,
        temperature: float = 0.3,
        max_output_tokens: Optional[int] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Iterator["CompletionChunk"]:
        """Yield the answer as it is generated.

        Optional. A provider that cannot stream should not pretend to: the
        router falls back to `complete()` and emits the whole answer as a single
        chunk, which is honest about there being no streaming rather than
        dribbling a finished string out on a timer.
        """
        raise NotImplementedError

    def embed(
        self,
        *,
        model: str,
        texts: Sequence[str],
        dim: int,
        task_type: str = "retrieval_document",
    ) -> EmbeddingResult:
        ...


class ProviderError(RuntimeError):
    """Raised when a provider fails in a way the caller may want to fall back on."""

    def __init__(self, message: str, *, provider: str = "", retryable: bool = True):
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
