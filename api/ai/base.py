"""Provider-neutral AI interfaces.

Sava is the product; models are infrastructure. Nothing above this layer names a
vendor. Swapping Gemini for OpenAI should be a registry change, not a rewrite of
every feature.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence


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
