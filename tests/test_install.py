"""End-to-end tests for install.sh, in both layouts the README documents.

The clone-into-place layout (`git clone ... ~/.claude/skills/humanize`) makes the
repo its own install destination, so several copies have the same file as source
and target. That aborted the whole install under `set -e` and installed nothing.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "install.sh"

# What a successful install must place, relative to CLAUDE_HOME.
EXPECTED = [
    "skills/humanize/SKILL.md",
    "skills/humanize/patterns/core.md",
    "skills/humanize/scripts/humanize_score.py",
    "skills/humanize/scripts/burstiness_check.py",
    "agents/humanizer-reviewer.md",
    "rules/10-anti-slop.md",
    "hooks/humanize-post-write.sh",
]

# Only the files install.sh actually reads; copying the whole repo would drag in
# .git and the scratch venvs.
SOURCES = [
    "SKILL.md",
    "install.sh",
    "patterns/core.md",
    "humanize_anti_slop/humanize_score.py",
    "humanize_anti_slop/burstiness_check.py",
    "agents/humanizer-reviewer.md",
    "hooks/humanize-post-write.sh",
    "rules/10-anti-slop.md",
]


def _stage_repo(destination: Path) -> Path:
    for relative in SOURCES:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, target)
    (destination / "install.sh").chmod(0o755)
    return destination


def _run_install(repo: Path, claude_home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(repo / "install.sh")],
        capture_output=True,
        text=True,
        env={"CLAUDE_HOME": str(claude_home), "PATH": "/usr/bin:/bin:/usr/local/bin"},
        check=False,
    )


@pytest.fixture()
def layouts(tmp_path):
    """(separate, in_place) -> (repo, CLAUDE_HOME) pairs for both documented installs."""
    separate_home = tmp_path / "separate" / "home"
    separate_repo = _stage_repo(tmp_path / "separate" / "repo")

    # The README's file-based install: the repo *is* CLAUDE_HOME/skills/humanize.
    in_place_home = tmp_path / "in-place" / "home"
    in_place_repo = _stage_repo(in_place_home / "skills" / "humanize")

    return {
        "separate": (separate_repo, separate_home),
        "in_place": (in_place_repo, in_place_home),
    }


@pytest.mark.parametrize("layout", ["separate", "in_place"])
def test_install_places_every_component(layouts, layout):
    repo, claude_home = layouts[layout]
    proc = _run_install(repo, claude_home)
    assert proc.returncode == 0, f"install.sh failed:\n{proc.stdout}\n{proc.stderr}"
    missing = [rel for rel in EXPECTED if not (claude_home / rel).is_file()]
    assert not missing, f"{layout} install did not place: {missing}"


@pytest.mark.parametrize("layout", ["separate", "in_place"])
def test_install_is_idempotent(layouts, layout):
    # The header claims "Idempotent. Re-run to update."
    repo, claude_home = layouts[layout]
    assert _run_install(repo, claude_home).returncode == 0
    second = _run_install(repo, claude_home)
    assert second.returncode == 0, f"re-run failed:\n{second.stdout}\n{second.stderr}"
    assert all((claude_home / rel).is_file() for rel in EXPECTED)


def test_installed_scorer_actually_runs(layouts, tmp_path):
    repo, claude_home = layouts["in_place"]
    assert _run_install(repo, claude_home).returncode == 0
    draft = tmp_path / "draft.md"
    draft.write_text("Studies show that this delves into the intricate landscape.\n")
    proc = subprocess.run(
        [
            "python3",
            str(claude_home / "skills/humanize/scripts/humanize_score.py"),
            "--json",
            str(draft),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode in (0, 1), proc.stderr
    assert '"score"' in proc.stdout


def test_install_fails_if_hook_is_missing_the_placeholder(layouts):
    # Regression: the Python substitution used str.replace(), which is a
    # silent no-op when the token isn't found. That let install.sh exit 0
    # and place a hook that never resolves an absolute scorer path, so it
    # always takes the "not absolute" branch and does nothing, silently.
    repo, claude_home = layouts["separate"]
    hook_source = repo / "hooks" / "humanize-post-write.sh"
    hook_source.write_text(
        hook_source.read_text(encoding="utf-8").replace(
            "@@HUMANIZE_INSTALLED_SCORER@@", "/already/baked/path"
        ),
        encoding="utf-8",
    )
    proc = _run_install(repo, claude_home)
    assert proc.returncode != 0
    assert "@@HUMANIZE_INSTALLED_SCORER@@" in proc.stderr


def test_installer_lives_at_the_repo_root():
    # README tells users to run ~/.claude/skills/humanize/install.sh.
    assert INSTALLER.is_file()
    assert "install.sh" in (REPO / "README.md").read_text()


@pytest.mark.parametrize("layout", ["separate", "in_place"])
def test_installed_hook_executes_the_baked_scorer(layouts, layout, tmp_path):
    # Run the INSTALLED copy (not the repo copy): its baked path plus the
    # containment check are the only runtime path a file-based install has.
    # HOME points at a directory with no .claude, so the empty-CLAUDE_HOME_REAL
    # branch is exercised too and must not widen trust to "/*".
    repo, claude_home = layouts[layout]
    assert _run_install(repo, claude_home).returncode == 0
    prose = tmp_path / "note.md"
    prose.write_text(
        "Studies show that this delves into the intricate landscape.\n", encoding="utf-8"
    )
    payload = json.dumps({"tool_input": {"file_path": str(prose)}})
    proc = subprocess.run(
        ["bash", str(claude_home / "hooks" / "humanize-post-write.sh")],
        input=payload,
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path / "home-without-claude"),
            # The scorer needs a modern python3; put this interpreter first.
            "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin",
            "HUMANIZE_DEBUG": "1",
        },
    )
    assert proc.returncode == 0
    assert "[humanize:debug]" not in proc.stderr, proc.stderr
    assert "hookSpecificOutput" in proc.stdout


@pytest.mark.parametrize("layout", ["separate", "in_place"])
def test_installed_hook_has_baked_path_and_no_env_override(layouts, layout):
    # The hook must never read HUMANIZE_SCORER/CLAUDE_HOME at run time: both
    # are settable by a cloned repo's .claude/settings.json env block, which
    # would get arbitrary Python executed on the first Write/Edit.
    repo, claude_home = layouts[layout]
    assert _run_install(repo, claude_home).returncode == 0
    installed_hook = (claude_home / "hooks" / "humanize-post-write.sh").read_text()
    expected_scorer = (
        claude_home.resolve() / "skills" / "humanize" / "scripts" / "humanize_score.py"
    )
    assert str(expected_scorer) in installed_hook
    assert "@@HUMANIZE_INSTALLED_SCORER@@" not in installed_hook
    # Not read as a live variable at hook runtime (a comment may still name it,
    # and CLAUDE_HOME_REAL is an unrelated local used only for path containment).
    assert not re.search(r"\$\{?HUMANIZE_SCORER", installed_hook)
    assert not re.search(r"\$\{?CLAUDE_HOME(?!_REAL)", installed_hook)
