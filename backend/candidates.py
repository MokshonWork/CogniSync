"""Typed result structures for council candidates and council runs.

These structures keep the council orchestration decoupled from any single
provider. A :class:`Candidate` captures one model's response (or failure), and
:class:`CouncilResult` aggregates several independent candidates.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Candidate:
    """A single model response (or failure) from the council.

    Attributes:
        candidate_id: Stable identifier for this candidate within a council run.
        provider: Provider name (e.g. ``"openrouter"``).
        model: Provider-specific model identifier.
        response: Response text when successful, otherwise ``None``.
        duration_ms: Wall-clock execution time in milliseconds, when available.
        token_usage: Provider-reported token usage, when available.
        success: Whether the call completed successfully.
        error: Concise error description when the call failed. Never contains
            raw provider payloads or secrets.
        error_category: Coarse failure class (rate_limit | quota |
            model_unavailable | invalid_key | ...) used by fallback policy;
            ``None`` when successful or unknown.
    """

    candidate_id: str
    provider: str
    model: str
    response: Optional[str] = None
    duration_ms: Optional[int] = None
    token_usage: Optional[Dict[str, Any]] = None
    success: bool = True
    error: Optional[str] = None
    error_category: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain-dict copy suitable for JSON serialization."""
        return asdict(self)


@dataclass
class CouncilResult:
    """Collected results from running several models independently."""

    candidates: List[Candidate]
    total_duration_ms: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None

    @property
    def successful(self) -> List[Candidate]:
        """Candidates that completed successfully."""
        return [c for c in self.candidates if c.success]

    @property
    def failed(self) -> List[Candidate]:
        """Candidates that failed."""
        return [c for c in self.candidates if not c.success]

    @property
    def success_count(self) -> int:
        return len(self.successful)

    @property
    def failure_count(self) -> int:
        return len(self.failed)

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain-dict copy suitable for JSON serialization."""
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "total_duration_ms": self.total_duration_ms,
            "metadata": self.metadata,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }
