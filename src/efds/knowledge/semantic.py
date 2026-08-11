"""Optional semantic extraction boundary.

The default implementation is deliberately inert: deterministic extraction and
database lifecycle work without credentials or a paid model. A future provider
can implement ``SemanticExtractor`` and return normalized candidates without
changing the persistence layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SemanticResult:
    requirements: tuple[dict, ...] = ()
    timing_rules: tuple[dict, ...] = ()
    processes: tuple[dict, ...] = ()
    relevance: str | None = None
    topics: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()


class SemanticExtractor(Protocol):
    provider: str
    model: str | None

    def extract(self, *, title: str, text: str) -> SemanticResult:
        ...


class NullSemanticExtractor:
    provider = "none"
    model = None

    def extract(self, *, title: str, text: str) -> SemanticResult:
        del title, text
        return SemanticResult()
