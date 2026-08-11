"""Build the structured EFDS operational layer from synced ICU articles."""

from __future__ import annotations

import argparse
import sys

from efds.db.session import session_scope
from efds.knowledge.service import extract_icu_knowledge


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract reviewable, source-linked operational knowledge from ICU PostgreSQL articles."
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse and count without database writes")
    parser.add_argument("--article-id", help="Freshdesk external article ID or knowledge article UUID")
    parser.add_argument("--limit", type=int, help="Process at most N articles")
    parser.add_argument("--force", action="store_true", help="Reprocess articles whose current hash was already extracted")
    parser.add_argument("--deterministic-only", action="store_true", help="Disable any configured semantic provider")
    parser.add_argument("--reprocess-stale", action="store_true", help="Re-extract articles with stale derived rows")
    parser.add_argument("--relevance-min", choices=("low", "medium", "high", "critical"), help="Only process articles at or above this relevance")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit is not None and args.limit < 0:
        print("--limit must be non-negative", file=sys.stderr)
        return 2
    try:
        with session_scope() as session:
            summary = extract_icu_knowledge(
                session,
                article_id=args.article_id,
                limit=args.limit,
                force=args.force,
                deterministic_only=args.deterministic_only,
                reprocess_stale=args.reprocess_stale,
                relevance_min=args.relevance_min,
                dry_run=args.dry_run,
            )
    except Exception as error:
        print(f"ICU knowledge extraction failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Run {summary.run_id or '(dry run)'}: {summary.status}; "
        f"articles={summary.article_count}, deterministic={summary.deterministic_count}, "
        f"semantic={summary.semantic_count}, proposed={summary.proposed_count}, "
        f"skipped={summary.skipped_unchanged}, failed={summary.failed_count}"
    )
    if summary.errors:
        for error in summary.errors[:10]:
            print(f"{error.get('article_id', 'batch')}: {error['error_type']}: {error['message']}", file=sys.stderr)
    return 1 if summary.failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
