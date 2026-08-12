# EFDS OneDrive Filesystem Institutional Memory V1

## Boundary

The local OneDrive-synchronized folder is a source archive. The backend stores
relative source paths, current metadata, immutable extracted versions, and
source change history. It never exposes the local absolute path to website
users and does not upload or mutate source files.

```text
EFDS_FILES_ROOT → discovery/exclusions → SHA-256 source version
                 → existing extractors → documents + document_versions
                 → admin-only website archive
```

`documents` remains the compatibility table for meetings and older generic
documents. For `source_type = onedrive_filesystem`, each row is a stable
logical source instance identified by `source_root + normalized_relative_path`.
`document_versions` identifies content versions with
`document_id + content_hash`. Identical content at two paths remains two
source rows and is shown as an exact duplicate group.

## Root and exclusions

Set the backend environment variable:

```text
EFDS_FILES_ROOT=C:\Users\slee7\OneDrive - Imperial College London\Imperial EFDS Society 26-27
```

The repository-root `filesystem_ingestion.toml` excludes development trees
under `12_Technology`, including `efds-site`, `efds-knowledge-base`, and
`icu-crawler`. It also excludes `.git`, `node_modules`, `.next`, virtual
environments, caches, build outputs, `.env*`, private-key patterns, database
files, Office lock files, and partial download files. Additive CLI exclusions
are available with `--exclude`.

The synchronizer discovers actual top-level areas and stores a normalized
`source_area` such as `01_governance`; it does not require a hardcoded list of
areas.

## Commands

```powershell
python scripts/sync_filesystem.py --dry-run
python scripts/sync_filesystem.py --area 01_Governance --dry-run
python scripts/sync_filesystem.py --area 01_Governance
python scripts/sync_filesystem.py --full
python scripts/sync_filesystem.py
```

`--full` is the authoritative reconciliation mode. Only a complete full scan
can mark previously known files missing. A failed, limited, area-scoped, or
interrupted discovery never marks all unobserved files missing.

Unchanged files are skipped using size/mtime first, then content hash when
needed. A changed same-path file keeps its source row and creates a new
`document_versions` row. Missing files remain queryable; a later appearance
creates a restored event. A same-hash disappearance and appearance in one
complete scan is conservatively treated as a rename/move only when the match
is one-to-one. Ambiguous matches remain missing plus created.

## Extraction and OneDrive placeholders

TXT, Markdown, DOCX, PDF, PPTX, XLSX, EML, and HTML reuse the existing
extractors. Unsupported files get a source row and `extraction_status =
unsupported`. Parser errors get `failed`; permission, cloud-only, or hydration
errors get `unavailable`. Cloud-only OneDrive files are not deliberately
hydrated or downloaded. A Windows recall-on-data-access attribute is recorded
as unavailable while allowing the rest of the scan to continue.

## Watch mode and correctness

```powershell
python scripts/sync_filesystem.py --watch
```

The V1 watcher is a low-dependency polling watcher with a five-second change
settling interval. It compares relative path, size, and mtime snapshots and
only runs a reconciliation when the snapshot changes. It must remain running
on the local machine; it cannot observe changes while the laptop is off.
Run a periodic full reconciliation (hourly or daily) to catch missed events,
OneDrive reconnects, and deletions. A future watchdog/Graph implementation can
reuse the same source/version tables; Microsoft Graph `drive_id` and `item_id`
can later be added to metadata without changing the local path model.

## Privacy

Raw OneDrive filesystem rows, versions, extracted text, and source changes are
admin-only through website authorization and RLS. No member, committee, public,
or agent route receives this archive by default. Publication is a separate
future decision and does not happen during ingestion.

## Troubleshooting

- Root missing: set `EFDS_FILES_ROOT` or pass `--root`.
- No files: check the root, OneDrive hydration, and exclusion configuration.
- Files missing after a run: confirm the run used `--full` and completed without
  discovery errors.
- Unsupported: inspect the extension; V1 does not add fragile new parsers.
- Unavailable: hydrate the selected OneDrive file manually if its content is
  needed, then rerun the synchronizer.
