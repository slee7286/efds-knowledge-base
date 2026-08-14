"""Batch and idempotently create embeddings for eligible retrieval units."""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass

from sqlalchemy import text

from efds.config import get_settings
from efds.db.session import session_scope
from efds.retrieval.embeddings import (
    EmbeddingProviderError,
    embedding_input,
    embedding_input_hash,
    get_embedding_provider,
    is_embedding_eligible,
    vector_literal,
)


@dataclass(frozen=True, slots=True)
class Unit:
    id: uuid.UUID
    source_type: str
    title: str
    content: str
    source_area: str | None
    topic: str | None
    channel: str | None
    is_current: bool
    is_stale: bool
    is_deleted: bool
    review_status: str | None
    source_parent_id: str | None = None
    relative_path: str | None = None
    metadata: dict | None = None


def _load_units(source: str | None, area: str | None, limit: int | None) -> list[Unit]:
    with session_scope() as session:
        query = """
          SELECT id, source_type, title, content, source_area, topic, channel,
                 is_current, is_stale, is_deleted, review_status,
                 source_parent_id, relative_path, metadata
          FROM retrieval_units
          WHERE is_current AND NOT is_stale AND NOT is_deleted
        """
        params: dict[str, object] = {}
        if source:
            query += " AND source_type = :source"
            params["source"] = source
        if area:
            query += " AND lower(source_area) = :area"
            params["area"] = area.casefold()
        query += " ORDER BY source_type, id"
        if limit:
            query += " LIMIT :limit"
            params["limit"] = limit
        rows = session.execute(text(query), params).mappings()
        return [Unit(**row) for row in rows]


def _missing_units(units: list[Unit], provider_name: str, model: str, model_version: str, *, force: bool) -> list[tuple[Unit, str]]:
    settings = get_settings()
    eligible = [(unit, embedding_input_hash(unit)) for unit in units if is_embedding_eligible(unit, settings)]
    if force or not eligible:
        return eligible
    ids = [str(unit.id) for unit, _ in eligible]
    with session_scope() as session:
        rows = session.execute(text("""
          SELECT retrieval_unit_id::text AS retrieval_unit_id, input_hash, dimension
          FROM retrieval_embeddings
          WHERE provider = :provider AND model = :model AND model_version = :model_version
            AND retrieval_unit_id = ANY(CAST(:ids AS uuid[]))
        """), {"provider": provider_name, "model": model, "model_version": model_version, "ids": ids}).mappings()
        current = {(str(row["retrieval_unit_id"]), row["input_hash"]) for row in rows}
    return [(unit, input_hash) for unit, input_hash in eligible if (str(unit.id), input_hash) not in current]


def _store_batch(batch: list[tuple[Unit, str]], vectors: list[list[float]], provider_name: str, model: str, model_version: str, dimension: int) -> None:
    with session_scope() as session:
        for (unit, input_hash), vector in zip(batch, vectors, strict=True):
            session.execute(text("""
              INSERT INTO retrieval_embeddings
                (id, retrieval_unit_id, provider, model, model_version, dimension, input_hash, embedding)
              VALUES (:id, :unit_id, :provider, :model, :model_version, :dimension, :input_hash, CAST(:embedding AS vector))
              ON CONFLICT (retrieval_unit_id, provider, model, model_version)
              DO UPDATE SET dimension = EXCLUDED.dimension, input_hash = EXCLUDED.input_hash,
                            embedding = EXCLUDED.embedding, updated_at = now()
            """), {"id": str(uuid.uuid4()), "unit_id": str(unit.id), "provider": provider_name,
                   "model": model, "model_version": model_version, "dimension": dimension,
                   "input_hash": input_hash, "embedding": vector_literal(vector)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create versioned embeddings for eligible EFDS retrieval units")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source")
    parser.add_argument("--area", help="Normalized source area, for example 01_governance")
    parser.add_argument("--missing-only", action="store_true", help="Skip units with the same input/model hash")
    parser.add_argument("--force", action="store_true", help="Re-embed matching units")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    if args.batch_size < 1 or args.batch_size > 100:
        parser.error("--batch-size must be between 1 and 100")
    try:
        settings = get_settings()
        provider = None if args.dry_run else get_embedding_provider(settings)
        provider_name = settings.embedding_provider if provider is None else provider.spec.provider
        model = settings.embedding_model if provider is None else provider.spec.model
        model_version = model if provider is None else provider.spec.model_version
        dimension = 1536 if provider is None and model == "text-embedding-3-small" else (0 if provider is None else provider.spec.dimension)
        units = _load_units(args.source, args.area, args.limit)
        targets = _missing_units(units, provider_name, model, model_version, force=args.force)
        excluded = len(units) - sum(1 for unit in units if is_embedding_eligible(unit, settings))
        chars = sum(len(embedding_input(unit)) for unit, _ in targets)
        estimated_tokens = max(1, chars // 4) if targets else 0
        if len(targets) > 1000 and not args.force:
            raise RuntimeError(f"Refusing an unforced job of {len(targets)} units; review --dry-run and pass --force")
        eligible_units = [unit for unit in units if is_embedding_eligible(unit, settings)]
        area_breakdown: dict[str, dict[str, int]] = {}
        for unit in units:
            key = unit.source_area or "(none)"
            bucket = area_breakdown.setdefault(key, {"eligible": 0, "excluded": 0})
            bucket["eligible" if is_embedding_eligible(unit, settings) else "excluded"] += 1
        estimated_cost = None
        if settings.embedding_cost_per_million_tokens_usd is not None:
            estimated_cost = round(estimated_tokens / 1_000_000 * settings.embedding_cost_per_million_tokens_usd, 6)
        print({"eligible_candidates": len(targets), "eligible_units": len(eligible_units), "excluded_by_policy": excluded,
               "area_breakdown": area_breakdown, "estimated_tokens": estimated_tokens,
               "estimated_cost_usd": estimated_cost,
               "provider": provider_name, "model": model, "dimension": dimension,
               "dry_run": args.dry_run})
        if args.dry_run or not targets:
            return 0
        created = 0
        for start in range(0, len(targets), args.batch_size):
            batch = targets[start:start + args.batch_size]
            assert provider is not None
            vectors = provider.embed_documents([embedding_input(unit) for unit, _ in batch])
            if len(vectors) != len(batch):
                raise EmbeddingProviderError("Provider returned a different number of vectors than inputs")
            _store_batch(batch, vectors, provider_name, model, model_version, len(vectors[0]))
            created += len(batch)
            print({"embedded": created, "remaining": len(targets) - created})
    except Exception as error:
        print(f"Embedding job failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
