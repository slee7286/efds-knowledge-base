"""Add stable OneDrive filesystem sources, versions, and change history.

The legacy documents table remains the compatibility surface for meetings and
older generic ingestion. OneDrive rows in that table become stable logical
source instances; document_versions stores immutable content versions.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_filesystem_institutional_memory"
down_revision: str | None = "0007_slack_institutional_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid = postgresql.UUID(as_uuid=True)
jsonb = postgresql.JSONB(astext_type=sa.Text())


def _json_default(value: str) -> sa.TextClause:
    return sa.text(f"'{value}'::jsonb")


def _admin_policy(table: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_select_admin
        ON public.{table} FOR SELECT TO authenticated
        USING (public.has_efds_role('admin'));
        GRANT SELECT ON public.{table} TO authenticated;
        REVOKE ALL ON public.{table} FROM anon;
        """
    )


def upgrade() -> None:
    # The initial documents table treated hashes as logical identity. Existing
    # meeting/document references remain valid, but filesystem source instances
    # must be allowed to share content hashes.
    op.execute("ALTER TABLE public.documents DROP CONSTRAINT IF EXISTS documents_content_hash_key")
    op.alter_column("documents", "content_hash", existing_type=sa.Text(), nullable=True)
    op.add_column("documents", sa.Column("source_root", sa.Text()))
    op.add_column("documents", sa.Column("relative_path", sa.Text()))
    op.add_column("documents", sa.Column("normalized_relative_path", sa.Text()))
    op.add_column("documents", sa.Column("source_area", sa.Text()))
    op.add_column("documents", sa.Column("filesystem_created_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("filesystem_modified_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.add_column("documents", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.add_column("documents", sa.Column("last_synced_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("last_changed_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("first_missing_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("is_missing", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("documents", sa.Column("is_unavailable", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("documents", sa.Column("extraction_status", sa.Text()))
    op.add_column("documents", sa.Column("extraction_error", sa.Text()))
    op.create_index("ix_documents_onedrive_area", "documents", ["source_type", "source_area"])
    op.create_index("ix_documents_onedrive_status", "documents", ["source_type", "is_missing", "is_unavailable"])
    op.create_index("ix_documents_onedrive_hash", "documents", ["source_type", "content_hash"])

    op.create_table(
        "document_versions",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", uuid, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("raw_text", sa.Text()),
        sa.Column("extraction_status", sa.Text(), nullable=False),
        sa.Column("extraction_error", sa.Text()),
        sa.Column("mime_type", sa.Text()),
        sa.Column("source_modified_at", sa.DateTime(timezone=True)),
        sa.Column("file_size_bytes", sa.BigInteger()),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
        sa.UniqueConstraint("document_id", "content_hash", name="uq_document_version_hash"),
    )
    op.create_index("ix_document_versions_document", "document_versions", ["document_id", "ingested_at"])
    op.create_index("ix_document_versions_hash", "document_versions", ["content_hash"])
    op.create_index("ix_document_versions_extraction", "document_versions", ["extraction_status"])

    op.add_column("documents", sa.Column("current_version_id", uuid))
    op.create_foreign_key("fk_documents_current_version", "documents", "document_versions", ["current_version_id"], ["id"], ondelete="SET NULL")
    op.create_unique_constraint("uq_documents_source_path", "documents", ["source_type", "source_root", "normalized_relative_path"])

    op.create_table(
        "document_source_changes",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", uuid, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("change_type", sa.Text(), nullable=False),
        sa.Column("previous_path", sa.Text()),
        sa.Column("new_path", sa.Text()),
        sa.Column("previous_content_hash", sa.Text()),
        sa.Column("new_content_hash", sa.Text()),
        sa.Column("previous_modified_at", sa.DateTime(timezone=True)),
        sa.Column("new_modified_at", sa.DateTime(timezone=True)),
        sa.Column("previous_size", sa.BigInteger()),
        sa.Column("new_size", sa.BigInteger()),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("ingestion_run_id", uuid, sa.ForeignKey("ingestion_runs.id", ondelete="SET NULL")),
        sa.Column("metadata", jsonb, nullable=False, server_default=_json_default("{}")),
    )
    op.create_index("ix_document_source_changes_document", "document_source_changes", ["document_id", "detected_at"])
    op.create_index("ix_document_source_changes_type", "document_source_changes", ["change_type", "detected_at"])

    # Preserve committee access to legacy documents while keeping raw
    # OneDrive filesystem documents admin-only.
    op.execute("DROP POLICY IF EXISTS documents_select_committee ON public.documents")
    op.execute(
        """
        CREATE POLICY documents_select_committee
        ON public.documents FOR SELECT TO authenticated
        USING (source_type IS DISTINCT FROM 'onedrive_filesystem'
               AND public.has_efds_role('committee'));
        CREATE POLICY documents_select_admin
        ON public.documents FOR SELECT TO authenticated
        USING (public.has_efds_role('admin'));
        """
    )
    _admin_policy("document_versions")
    _admin_policy("document_source_changes")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS document_source_changes_select_admin ON public.document_source_changes")
    op.execute("ALTER TABLE public.document_source_changes DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON public.document_source_changes FROM authenticated, anon")
    op.drop_index("ix_document_source_changes_type", table_name="document_source_changes")
    op.drop_index("ix_document_source_changes_document", table_name="document_source_changes")
    op.drop_table("document_source_changes")

    op.execute("DROP POLICY IF EXISTS document_versions_select_admin ON public.document_versions")
    op.execute("ALTER TABLE public.document_versions DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON public.document_versions FROM authenticated, anon")
    op.drop_constraint("fk_documents_current_version", "documents", type_="foreignkey")
    op.drop_constraint("uq_documents_source_path", "documents", type_="unique")
    op.drop_index("ix_document_versions_extraction", table_name="document_versions")
    op.drop_index("ix_document_versions_hash", table_name="document_versions")
    op.drop_index("ix_document_versions_document", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_column("documents", "current_version_id")
    op.drop_index("ix_documents_onedrive_hash", table_name="documents")
    op.drop_index("ix_documents_onedrive_status", table_name="documents")
    op.drop_index("ix_documents_onedrive_area", table_name="documents")
    for column in (
        "extraction_error", "extraction_status", "is_unavailable", "is_missing",
        "first_missing_at", "last_changed_at", "last_synced_at", "last_seen_at",
        "first_seen_at", "filesystem_modified_at", "filesystem_created_at",
        "source_area", "normalized_relative_path", "relative_path", "source_root",
    ):
        op.drop_column("documents", column)
    op.execute("DROP POLICY IF EXISTS documents_select_admin ON public.documents")
    op.execute("DROP POLICY IF EXISTS documents_select_committee ON public.documents")
    op.execute(
        """
        CREATE POLICY documents_select_committee
        ON public.documents FOR SELECT TO authenticated
        USING (public.has_efds_role('committee'));
        """
    )
    op.execute("ALTER TABLE public.documents ADD CONSTRAINT documents_content_hash_key UNIQUE (content_hash)")
    op.alter_column("documents", "content_hash", existing_type=sa.Text(), nullable=False)
