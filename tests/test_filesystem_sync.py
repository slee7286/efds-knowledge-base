from pathlib import Path
from types import SimpleNamespace

from efds.ingestion.filesystem import (
    FilesystemConfig,
    _extract,
    discover_files,
    normalize_relative_path,
)


def test_filesystem_discovery_excludes_repositories_secrets_and_temp_files(tmp_path: Path) -> None:
    (tmp_path / "01_Governance").mkdir()
    (tmp_path / "12_Technology" / "efds-site" / "node_modules").mkdir(parents=True)
    (tmp_path / "12_Technology" / "efds-knowledge-base").mkdir(parents=True)
    (tmp_path / "01_Governance" / "policy.txt").write_text("policy", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    (tmp_path / "01_Governance" / "~$policy.docx").write_bytes(b"temp")
    (tmp_path / "12_Technology" / "efds-site" / "README.md").write_text("repo", encoding="utf-8")
    files, excluded, errors, complete = discover_files(tmp_path, FilesystemConfig())
    assert [item.relative_path for item in files] == ["01_Governance/policy.txt"]
    assert excluded >= 3
    assert errors == []
    assert complete is True


def test_paths_are_relative_and_case_insensitive() -> None:
    root = Path("C:/EFDS")
    assert normalize_relative_path(root, root / "03_Committee" / "Plan.docx") == ("03_Committee/Plan.docx", "03_committee/plan.docx")


def test_area_and_unsupported_files_are_discovered_without_extraction(tmp_path: Path) -> None:
    (tmp_path / "01_Governance").mkdir()
    (tmp_path / "04_Finance").mkdir()
    (tmp_path / "01_Governance" / "policy.txt").write_text("policy", encoding="utf-8")
    (tmp_path / "01_Governance" / "budget.zip").write_bytes(b"zip")
    (tmp_path / "04_Finance" / "accounts.txt").write_text("accounts", encoding="utf-8")
    files, _, _, complete = discover_files(tmp_path, FilesystemConfig(), area="01_Governance")
    assert {item.relative_path for item in files} == {"01_Governance/policy.txt", "01_Governance/budget.zip"}
    assert complete is True


def test_unsupported_and_extraction_failure_are_non_fatal(tmp_path: Path) -> None:
    unsupported = tmp_path / "file.zip"
    unsupported.write_bytes(b"zip")
    status, text, metadata, error = _extract(unsupported, "hash", "application/zip", None)
    assert (status, text, metadata, error) == ("unsupported", None, {}, None)

    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a document")
    status, text, metadata, error = _extract(broken, "hash", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", None)
    assert status == "failed"
    assert text is None
    assert isinstance(error, str)


def test_cloud_only_file_attribute_is_detected() -> None:
    from efds.ingestion.filesystem import _is_cloud_only, WINDOWS_RECALL_ON_DATA_ACCESS

    assert _is_cloud_only(SimpleNamespace(st_file_attributes=WINDOWS_RECALL_ON_DATA_ACCESS)) is True
