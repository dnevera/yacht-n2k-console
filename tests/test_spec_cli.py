"""Tests for scripts/spec.py — the Spec-Driven Development helper CLI.

Pure filesystem unit tests: every run works inside pytest's tmp_path, so the
real specs/ directory is never touched.

Mini-prompt: extend here when adding new spec.py subcommands or required sections.
"""
import importlib.util
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_CLI_PATH = REPO_ROOT / "scripts" / "spec.py"


def _load_cli():
    """Load scripts/spec.py by path — scripts/ is not an importable package."""
    spec = importlib.util.spec_from_file_location("spec_cli", SPEC_CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


@pytest.fixture()
def specs_root(tmp_path):
    """Isolated specs/ tree with the real templates copied in."""
    root = tmp_path / "specs"
    (root / "active").mkdir(parents=True)
    (root / "completed").mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "specs" / "templates", root / "templates")
    return root


def run(specs_root, *argv):
    return cli.main(["--specs-dir", str(specs_root), *argv])


def test_templates_pass_validation():
    """All shipped templates must contain every required section."""
    templates = sorted((REPO_ROOT / "specs" / "templates").glob("*.md"))
    assert templates
    assert cli.main(["validate", *[str(p) for p in templates]]) == 0


def test_create_generates_numbered_file(specs_root):
    """create writes NNN-slug.md into active/ and fills metadata."""
    assert run(specs_root, "create", "--type", "feature", "--title", "Tank Level Alarm") == 0
    created = list((specs_root / "active").glob("*.md"))
    assert len(created) == 1
    assert created[0].name == "001-tank-level-alarm.md"
    text = created[0].read_text(encoding="utf-8")
    assert text.startswith("# Tank Level Alarm")
    assert "- id: 001" in text
    assert "YYYY-MM-DD" not in text


def test_create_twice_allocates_new_number(specs_root):
    """A repeated create must not overwrite the previous spec."""
    run(specs_root, "create", "--title", "Alpha")
    run(specs_root, "create", "--title", "Beta")
    names = sorted(p.name for p in (specs_root / "active").glob("*.md"))
    assert names == ["001-alpha.md", "002-beta.md"]


def test_create_bugfix_template(specs_root):
    """--type bugfix uses the bugfix template."""
    run(specs_root, "create", "--type", "bugfix", "--title", "Spin Loop")
    text = (specs_root / "active" / "001-spin-loop.md").read_text(encoding="utf-8")
    assert "- type: bugfix" in text


def test_list_reports_active_and_completed(specs_root, capsys):
    """list prints both buckets and a total counter."""
    run(specs_root, "create", "--title", "Alpha")
    assert run(specs_root, "list") == 0
    out = capsys.readouterr().out
    assert "[active]" in out
    assert "[completed]" in out
    assert "Alpha" in out
    assert "total: 1" in out


def test_list_filtered_by_status(specs_root, capsys):
    """--status completed hides the active bucket."""
    run(specs_root, "create", "--title", "Alpha")
    assert run(specs_root, "list", "--status", "completed") == 0
    out = capsys.readouterr().out
    assert "[active]" not in out
    assert "total: 0" in out


def test_validate_ok_for_created_spec(specs_root):
    """A freshly created spec passes validation."""
    run(specs_root, "create", "--title", "Alpha")
    assert run(specs_root, "validate") == 0


def test_validate_fails_on_missing_section(specs_root, capsys):
    """Missing required section -> exit code 1 and a readable message."""
    broken = specs_root / "active" / "009-broken.md"
    broken.write_text("# Broken\n\n## Metadata\n\n- status: draft\n", encoding="utf-8")
    assert run(specs_root, "validate") == 1
    out = capsys.readouterr().out
    assert "missing sections" in out
    assert "Verification" in out


def test_validate_missing_file(specs_root, capsys):
    """A non-existent path fails without a traceback."""
    assert run(specs_root, "validate", str(specs_root / "active" / "nope.md")) == 1
    assert "file not found" in capsys.readouterr().out


def test_archive_moves_spec_and_sets_status(specs_root):
    """archive moves the file to completed/ and rewrites status."""
    run(specs_root, "create", "--title", "Alpha")
    source = specs_root / "active" / "001-alpha.md"
    assert run(specs_root, "archive", str(source)) == 0
    assert not source.exists()
    archived = specs_root / "completed" / "001-alpha.md"
    assert archived.exists()
    assert "- status: completed" in archived.read_text(encoding="utf-8")


def test_archive_missing_path(specs_root, capsys):
    """Archiving a non-existent spec reports an error, no traceback."""
    assert run(specs_root, "archive", str(specs_root / "active" / "404-none.md")) == 1
    assert "spec not found" in capsys.readouterr().err


def test_number_continues_after_archive(specs_root):
    """Archived numbers are never reused."""
    run(specs_root, "create", "--title", "Alpha")
    run(specs_root, "archive", str(specs_root / "active" / "001-alpha.md"))
    run(specs_root, "create", "--title", "Beta")
    assert (specs_root / "active" / "002-beta.md").exists()
