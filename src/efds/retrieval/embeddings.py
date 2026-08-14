"""Provider-neutral embedding inputs, policy, and OpenAI implementation."""

from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from efds.config import Settings, get_settings


DEFAULT_SAFE_SOURCE_TYPES = frozenset({
    "icu_article", "knowledge_requirement", "knowledge_timing_rule",
    "knowledge_process", "knowledge_process_step", "knowledge_resource",
    "knowledge_contact", "operational_decision", "operational_action",
    "operational_commitment", "operational_question", "operational_status",
    "document",
})

DEFAULT_DOCUMENT_AREAS = frozenset({"01_governance"})
DEFAULT_SLACK_CHANNEL_IDS = frozenset()
SENSITIVE_NAME_PATTERN = re.compile(
    r"(?:^|[\\/._-])(?:\.env|credentials?|secrets?|tokens?|private[_ -]?keys?|.*\.(?:pem|key|p12|pfx|sqlite|db|dump|sql))(?:$|[\\/._-])",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class EmbeddingSpec:
    provider: str
    model: str
    model_version: str
    dimension: int


class EmbeddingProvider(Protocol):
    spec: EmbeddingSpec

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class EmbeddingConfigurationError(RuntimeError):
    pass


class EmbeddingProviderError(RuntimeError):
    pass


class OpenAIEmbeddingProvider:
    """Small adapter; the OpenAI SDK is imported only when embeddings are used."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small") -> None:
        if not api_key:
            raise EmbeddingConfigurationError("OPENAI_API_KEY is missing")
        try:
            from openai import OpenAI
        except ImportError as error:  # pragma: no cover - exercised in deployment
            raise EmbeddingConfigurationError("Install the backend embedding extra before running embedding commands") from error
        self.client = OpenAI(api_key=api_key)
        self.spec = EmbeddingSpec("openai", model, model, 1536 if model == "text-embedding-3-small" else 0)

    def _request(self, texts: Sequence[str]) -> list[list[float]]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.client.embeddings.create(model=self.spec.model, input=list(texts), encoding_format="float")
                values = [list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)]
                break
            except Exception as error:  # SDK errors vary by version/provider
                last_error = error
                if attempt == 2:
                    raise EmbeddingProviderError("The embedding provider request failed after bounded retries") from error
                time.sleep(2**attempt)
        else:  # pragma: no cover
            raise EmbeddingProviderError("The embedding provider request failed") from last_error
        if not values:
            raise EmbeddingProviderError("The embedding provider returned no vectors")
        dimension = len(values[0])
        if any(len(value) != dimension or not all(math.isfinite(number) for number in value) for value in values):
            raise EmbeddingProviderError("The embedding provider returned invalid vector dimensions")
        if self.spec.dimension == 0:
            object.__setattr__(self.spec, "dimension", dimension)
        if dimension != self.spec.dimension:
            raise EmbeddingProviderError("The embedding provider returned an unexpected vector dimension")
        return values

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._request(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._request([text])[0]


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    settings = settings or get_settings()
    if settings.embedding_provider != "openai":
        raise EmbeddingConfigurationError(f"Unsupported embedding provider: {settings.embedding_provider}")
    return OpenAIEmbeddingProvider(settings.embedding_api_key or "", settings.embedding_model)


def enabled_source_types(settings: Settings | None = None) -> frozenset[str]:
    settings = settings or get_settings()
    return frozenset(settings.embedding_source_types) if settings.embedding_source_types else DEFAULT_SAFE_SOURCE_TYPES


def enabled_document_areas(settings: Settings | None = None) -> frozenset[str]:
    settings = settings or get_settings()
    return frozenset(value.casefold() for value in settings.embedding_document_areas) if settings.embedding_document_areas else DEFAULT_DOCUMENT_AREAS


def enabled_slack_channel_ids(settings: Settings | None = None) -> frozenset[str]:
    settings = settings or get_settings()
    return frozenset(settings.embedding_slack_channel_ids) if settings.embedding_slack_channel_ids else DEFAULT_SLACK_CHANNEL_IDS


def _unit_metadata(unit: Any) -> dict[str, Any]:
    value = getattr(unit, "metadata", None)
    return value if isinstance(value, dict) else {}


def is_sensitive_embedding_unit(unit: Any) -> bool:
    candidates = [getattr(unit, "relative_path", None), getattr(unit, "title", None),
                  _unit_metadata(unit).get("relative_path"), _unit_metadata(unit).get("normalized_relative_path")]
    return any(value and SENSITIVE_NAME_PATTERN.search(str(value)) for value in candidates)


def is_embedding_eligible(unit: Any, settings: Settings | None = None) -> bool:
    """Apply privacy and state policy before content can leave EFDS infrastructure."""

    if unit.source_type not in enabled_source_types(settings):
        return False
    if not unit.is_current or unit.is_stale or unit.is_deleted:
        return False
    if not str(getattr(unit, "content", "") or "").strip() or is_sensitive_embedding_unit(unit):
        return False
    # ICU-derived structured knowledge is safe to index as a derived search
    # representation even while it is proposed. Retrieval authorization still
    # decides whether proposed records are visible to the caller. Operational
    # truth is different: it must be approved before leaving EFDS storage.
    if unit.source_type.startswith("operational_"):
        return unit.review_status == "approved"
    if unit.source_type.startswith("knowledge_"):
        return True
    if unit.source_type == "document":
        return str(getattr(unit, "source_area", "") or "").casefold() in enabled_document_areas(settings)
    if unit.source_type == "slack_message":
        return str(getattr(unit, "source_parent_id", "") or "") in enabled_slack_channel_ids(settings)
    if unit.source_type in {"meeting_transcript", "meeting_summary", "meeting_notes"}:
        return True
    return unit.source_type == "icu_article"


def embedding_input(unit: Any) -> str:
    """Build stable semantic text without IDs, hashes, absolute paths, or secrets."""

    fields = [f"Title: {unit.title}", f"Source type: {unit.source_type}"]
    for name, value in (("Area", unit.source_area), ("Topic", unit.topic), ("Channel", unit.channel)):
        if value:
            fields.append(f"{name}: {value}")
    fields.append(f"Content:\n{unit.content.strip()}")
    return "\n".join(fields).strip()


def embedding_input_hash(unit: Any) -> str:
    return hashlib.sha256(embedding_input(unit).encode("utf-8")).hexdigest()


def vector_literal(values: Sequence[float]) -> str:
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError("embedding vector must contain finite values")
    return "[" + ",".join(format(value, ".9g") for value in values) + "]"
