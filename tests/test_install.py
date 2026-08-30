"""End-to-end tests for install.sh, in both layouts the README documents.

The clone-into-place layout (`git clone ... ~/.claude/skills/humanize`) makes the
repo its own install destination, so several copies have the same file as source
and target. That aborted the whole install under `set -e` and installed nothing.
"""

from __future__ import annotations

import shutil
import subprocess
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


def test_installer_lives_at_the_repo_root():
    # README tells users to run ~/.claude/skills/humanize/install.sh.
    assert INSTALLER.is_file()
    assert "install.sh" in (REPO / "README.md").read_text()
