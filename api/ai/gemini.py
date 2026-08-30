"""Google Gemini provider.

The only provider wired today, because GEMINI_API_KEY is the only credential
this deployment has. It sits behind `AIProvider` so adding OpenAI later means
writing one sibling module and registering it — no feature code changes.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Sequence

from ..config import GEMINI_API_KEY
from .base import AIProvider, Completion, EmbeddingResult, ModelSpec, ProviderError

logger = logging.getLogger(__name__)


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or GEMINI_API_KEY
        self._configured = False

    def _ensure(self) -> bool:
        if self._configured:
            return True
        if not self._api_key:
            return False
        try:
            import google.generativeai as genai
            genai.configure(api_key=self._api_key)
            self._configured = True
            return True
        except Exception as e:
            logger.error("Gemini configure failed: %s", e)
            return False

    def is_available(self) -> bool:
        return self._ensure()

    # ── Generation ───────────────────────────────────────────────────────────
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
        if not self._ensure():
            raise ProviderError("Gemini is not configured", provider=self.name, retryable=False)

        import google.generativeai as genai

        started = time.monotonic()
        gen_cfg: Dict[str, object] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens or spec.max_output_tokens,
        }
        if json_mode:
            gen_cfg["response_mime_type"] = "application/json"

        try:
            model = genai.GenerativeModel(spec.model, system_instruction=system)
            parts: List[object] = []
            if images:
                for blob in images:
                    parts.append({"mime_type": "image/jpeg", "data": blob})
            parts.append(prompt)

            if history:
                convo = [
                    {"role": "user" if h.get("role") == "user" else "model",
                     "parts": [h.get("content", "")]}
                    for h in history[-8:]
                ]
                convo.append({"role": "user", "parts": parts})
                resp = model.generate_content(convo, generation_config=gen_cfg)
            else:
                resp = model.generate_content(parts, generation_config=gen_cfg)

            text = _extract_text(resp)
            usage = getattr(resp, "usage_metadata", None)
            return Completion(
                text=text,
                provider=self.name,
                model=spec.model,
                input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
                output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
                wall_ms=int((time.monotonic() - started) * 1000),
                raw=resp,
            )
        except ProviderError:
            raise
        except Exception as e:
            retryable = not any(
                s in str(e).lower() for s in ("api key", "permission", "not found", "invalid")
            )
            raise ProviderError(f"Gemini generation failed: {e}",
                                provider=self.name, retryable=retryable) from e

    # ── Streaming ────────────────────────────────────────────────────────────

    def complete_stream(
        self,
        *,
        spec: ModelSpec,
        system: Optional[str],
        prompt: str,
        temperature: float = 0.3,
        max_output_tokens: Optional[int] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ):
        """Real token streaming via `generate_content(stream=True)`.

        The SDK yields partial `GenerateContentResponse` objects as the model
        produces them. Each one carries only the *new* text, so the chunks are
        already deltas and are passed straight through — accumulating here and
        yielding the running total is the classic way to make an answer repeat
        itself on screen.

        Usage metadata only appears on the final chunk, so it is read at the end
        rather than per chunk.
        """
        if not self._ensure():
            raise ProviderError("Gemini is not configured", provider=self.name,
                                retryable=False)

        import google.generativeai as genai
        from .base import CompletionChunk

        gen_cfg: Dict[str, object] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens or spec.max_output_tokens,
        }

        try:
            model = genai.GenerativeModel(spec.model, system_instruction=system)
            if history:
                convo = [
                    {"role": "user" if h.get("role") == "user" else "model",
                     "parts": [h.get("content", "")]}
                    for h in history[-8:]
                ]
                convo.append({"role": "user", "parts": [prompt]})
                stream = model.generate_content(convo, generation_config=gen_cfg,
                                                stream=True)
            else:
                stream = model.generate_content(prompt, generation_config=gen_cfg,
                                                stream=True)

            last = None
            for piece in stream:
                last = piece
                delta = _extract_text(piece)
                if delta:
                    yield CompletionChunk(text=delta)

            usage = getattr(last, "usage_metadata", None) if last is not None else None
            yield CompletionChunk(
                done=True,
                input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
                output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
                raw=last,
            )
        except ProviderError:
            raise
        except Exception as e:
            retryable = not any(
                s in str(e).lower() for s in ("api key", "permission", "not found", "invalid")
            )
            raise ProviderError(f"Gemini streaming failed: {e}",
                                provider=self.name, retryable=retryable) from e

    # ── Embeddings ───────────────────────────────────────────────────────────
    def embed(
        self,
        *,
        model: str,
        texts: Sequence[str],
        dim: int,
        task_type: str = "retrieval_document",
    ) -> EmbeddingResult:
        if not self._ensure():
            raise ProviderError("Gemini is not configured", provider=self.name, retryable=False)
        if not texts:
            return EmbeddingResult(vectors=[], provider=self.name, model=model, dim=dim)

        import google.generativeai as genai

        started = time.monotonic()
        vectors: List[Sequence[float]] = []
        try:
            # The SDK accepts a list for batching; fall back to per-item on error
            # so one malformed input cannot fail an entire chunk set.
            try:
                result = genai.embed_content(
                    model=f"models/{model}",
                    content=list(texts),
                    task_type=task_type,
                    output_dimensionality=dim,
                )
                emb = result["embedding"]
                vectors = emb if isinstance(emb[0], (list, tuple)) else [emb]
            except Exception:
                for t in texts:
                    r = genai.embed_content(
                        model=f"models/{model}",
                        content=t,
                        task_type=task_type,
                        output_dimensionality=dim,
                    )
                    vectors.append(r["embedding"])

            approx_tokens = sum(max(1, len(t) // 4) for t in texts)
            return EmbeddingResult(
                vectors=vectors,
                provider=self.name,
                model=model,
                input_tokens=approx_tokens,
                wall_ms=int((time.monotonic() - started) * 1000),
                dim=len(vectors[0]) if vectors else dim,
            )
        except Exception as e:
            raise ProviderError(f"Gemini embedding failed: {e}", provider=self.name) from e


def _extract_text(resp) -> str:
    """Pull text out of a response without exploding on safety blocks."""
    try:
        return (resp.text or "").strip()
    except Exception:
        pass
    try:
        for cand in getattr(resp, "candidates", []) or []:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", []) or []:
                t = getattr(part, "text", None)
                if t:
                    return t.strip()
    except Exception:
        pass
    return ""
