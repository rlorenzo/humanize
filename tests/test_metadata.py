"""Guards on repo metadata: version sync, plugin manifests, and shipped assets.

The version lives in four places by necessity — Python packaging, the Claude Code
plugin manifest, the marketplace listing, and the skill frontmatter. Nothing but
this test stops them from drifting apart.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / ".claude-plugin" / "plugin.json"
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"


def _pyproject_version() -> str:
    match = re.search(r'^version = "([^"]+)"', (REPO / "pyproject.toml").read_text(), re.M)
    assert match, "pyproject.toml has no version"
    return match.group(1)


def _skill_version() -> str:
    # Frontmatter is a plain block at the top of SKILL.md; no YAML dependency.
    match = re.search(r'^\s*version:\s*"?([0-9][^"\s]*)"?', (REPO / "SKILL.md").read_text(), re.M)
    assert match, "SKILL.md frontmatter has no version"
    return match.group(1)


def test_version_is_identical_across_every_manifest():
    versions = {
        "pyproject.toml": _pyproject_version(),
        "plugin.json": json.loads(PLUGIN.read_text())["version"],
        "SKILL.md": _skill_version(),
    }
    assert len(set(versions.values())) == 1, f"version drift: {versions}"


@pytest.mark.parametrize("manifest", [PLUGIN, MARKETPLACE])
def test_plugin_manifests_are_valid_json(manifest):
    assert isinstance(json.loads(manifest.read_text()), dict)


def test_hook_manifest_points_at_a_script_that_exists():
    hooks = json.loads((REPO / "hooks" / "hooks.json").read_text())
    commands = re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/(\S+?\.sh)", json.dumps(hooks))
    assert commands, "hooks.json registers no plugin-root script"
    for relative in commands:
        assert (REPO / relative).is_file(), f"hooks.json points at missing {relative}"


@pytest.mark.parametrize("script", ["humanize_score.py", "burstiness_check.py"])
def test_install_script_ships_every_scorer(script):
    # A scorer the installer forgets is a scorer no user ever runs.
    assert script in (REPO / "scripts" / "install.sh").read_text()


@pytest.mark.parametrize(
    "relative",
    [
        "hooks/humanize-post-write.sh",
        "scripts/install.sh",
        "scripts/humanize_score.py",
        "scripts/burstiness_check.py",
    ],
)
def test_shebanged_files_are_executable(relative):
    # Every one of these is documented as directly runnable; a 644 mode in git
    # means a fresh clone or plugin install hands the user "permission denied".
    path = REPO / relative
    assert path.read_text().startswith("#!"), f"{relative} lost its shebang"
    assert path.stat().st_mode & 0o111, f"{relative} is not executable"
