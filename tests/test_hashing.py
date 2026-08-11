from pathlib import Path

from efds.ingestion.hashing import sha256_bytes, sha256_file


def test_identical_files_have_identical_hashes(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"same content")
    second.write_bytes(b"same content")
    assert sha256_file(first) == sha256_file(second)


def test_one_byte_difference_changes_hash(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"abc")
    first_hash = sha256_file(path)
    path.write_bytes(b"abd")
    assert first_hash != sha256_file(path)
    assert sha256_bytes(b"abd") == sha256_file(path)

