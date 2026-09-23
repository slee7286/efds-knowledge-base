"""Add member profile photos in a private, owner-scoped Storage bucket."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0021_profile_photos"
down_revision: str | None = "0020_committee_ticket_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("avatar_path", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_profiles_avatar_owner",
        "profiles",
        "avatar_path IS NULL OR ("
        "avatar_path ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}\\.webp$' "
        "AND split_part(avatar_path, '/', 1) = auth_user_id::text)",
    )

    op.execute("""
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('efds-profile-photos', 'efds-profile-photos', false, 2097152, ARRAY['image/webp'])
ON CONFLICT (id) DO UPDATE SET
  public = false,
  file_size_limit = EXCLUDED.file_size_limit,
  allowed_mime_types = EXCLUDED.allowed_mime_types;

CREATE POLICY efds_profile_photos_select_own
ON storage.objects FOR SELECT TO authenticated
USING (
  bucket_id = 'efds-profile-photos'
  AND (storage.foldername(name))[1] = (SELECT auth.uid()::text)
  AND (SELECT public.has_efds_role('viewer'))
);

CREATE POLICY efds_profile_photos_insert_own
ON storage.objects FOR INSERT TO authenticated
WITH CHECK (
  bucket_id = 'efds-profile-photos'
  AND (storage.foldername(name))[1] = (SELECT auth.uid()::text)
  AND storage.extension(name) = 'webp'
  AND (SELECT public.has_efds_role('viewer'))
);

CREATE POLICY efds_profile_photos_delete_own
ON storage.objects FOR DELETE TO authenticated
USING (
  bucket_id = 'efds-profile-photos'
  AND (storage.foldername(name))[1] = (SELECT auth.uid()::text)
  AND (SELECT public.has_efds_role('viewer'))
);
    """)


def downgrade() -> None:
    op.execute("DROP POLICY efds_profile_photos_delete_own ON storage.objects")
    op.execute("DROP POLICY efds_profile_photos_insert_own ON storage.objects")
    op.execute("DROP POLICY efds_profile_photos_select_own ON storage.objects")
    op.drop_constraint("ck_profiles_avatar_owner", "profiles", type_="check")
    op.drop_column("profiles", "avatar_path")
    # Preserve uploaded files if there are any; never delete member photos here.
    op.execute("""
DELETE FROM storage.buckets b
WHERE b.id = 'efds-profile-photos'
  AND NOT EXISTS (SELECT 1 FROM storage.objects o WHERE o.bucket_id = b.id);
    """)
