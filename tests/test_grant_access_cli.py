import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

spec = importlib.util.spec_from_file_location(
    "grant_access_cli", Path(__file__).parents[1] / "scripts" / "grant_access.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
build_parser = module.build_parser
match_active_officer = module.match_active_officer


def officer(name: str, year: str, *, active: bool = True):
    return SimpleNamespace(id=uuid4(), name=name, role="Events Lead", academic_year=year, active=active)


def test_exact_roster_name_can_be_matched_without_a_uuid() -> None:
    target = officer("Alex Example", "2026/27")
    assert match_active_officer([target], name=" alex EXAMPLE ") is target
    args = build_parser().parse_args([
        "alex@imperial.ac.uk", "--role", "committee", "--officer-name", "Alex Example",
    ])
    assert args.officer_name == "Alex Example"


def test_duplicate_roster_names_require_an_academic_year() -> None:
    previous = officer("Alex Example", "2025/26")
    current = officer("Alex Example", "2026/27")
    with pytest.raises(ValueError, match="More than one active roster entry"):
        match_active_officer([previous, current], name="Alex Example")
    assert match_active_officer(
        [previous, current], name="Alex Example", academic_year="2026/27"
    ) is current


def test_inactive_and_unknown_roster_names_cannot_be_linked() -> None:
    with pytest.raises(LookupError, match="No active officer"):
        match_active_officer([officer("Alex Example", "2026/27", active=False)], name="Alex Example")
    with pytest.raises(ValueError, match="must not be empty"):
        match_active_officer([], name=" ")


def test_roster_name_and_id_cannot_be_selected_together() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([
            "alex@imperial.ac.uk", "--role", "committee", "--officer-name", "Alex Example",
            "--officer-id", str(uuid4()),
        ])
