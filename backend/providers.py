"""Provider abstraction for LLM generation.

Defines a small, typed interface for generating a single model response and a
concrete OpenRouter implementation. Future providers can be added without
changing the council orchestration layer: they only need to satisfy the
:class:`LLMProvider` interface.

No provider framework or deep inheritance is introduced. The interface is a
structural protocol so any class with a matching ``generate`` method qualifies.
"""

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from .candidates import Candidate
from .openrouter import query_model_detailed, _image_to_data_url
from .config import OPENROUTER_API_KEY, OPENROUTER_API_URL

import time
import uuid


@runtime_checkable
class LLMProvider(Protocol):
    """Structural interface for an LLM provider.

    Providers are responsible for turning a model identifier and a list of
    messages into a :class:`Candidate`, capturing timing, usage, and errors so
    callers never need to catch provider-specific exceptions.
    """

    name: str

    async def generate(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> Candidate:
        """Generate a single response for a model.

        Returns a Candidate that always records success/failure, duration, and
        any error. Provider internals (API keys, raw error bodies) never leak
        into the returned Candidate.
        """

    ...


class OpenRouterProvider:
    """OpenRouter-backed LLM provider.

    Reuses the existing OpenRouter HTTP client (``query_model``) for
    authentication, timeouts, and error handling rather than duplicating it.
    """

    name = "openrouter"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key
        self.api_url = api_url or OPENROUTER_API_URL
        self.timeout = timeout

    async def generate(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> Candidate:
        """Query a single OpenRouter model and wrap the result in a Candidate."""
        start = time.monotonic()
        try:
            result, error = await query_model_detailed(model, messages, timeout=self.timeout)
            duration_ms = int((time.monotonic() - start) * 1000)

            if result is None:
                reason = f"{error.category}: {error.message}" if error else "Provider request failed"
                return Candidate(
                    candidate_id=uuid.uuid4().hex[:8],
                    provider=self.name,
                    model=model,
                    success=False,
                    duration_ms=duration_ms,
                    error=reason,
                    error_category=(error.category if error else None),
                )

            content = result.get("content")
            token_usage = result.get("usage")

            return Candidate(
                candidate_id=uuid.uuid4().hex[:8],
                provider=self.name,
                model=model,
                response=content,
                duration_ms=duration_ms,
                token_usage=token_usage,
                success=True,
            )
        except Exception:
            duration_ms = int((time.monotonic() - start) * 1000)
            return Candidate(
                candidate_id=uuid.uuid4().hex[:8],
                provider=self.name,
                model=model,
                success=False,
                duration_ms=duration_ms,
                error="Provider request failed",
            )
