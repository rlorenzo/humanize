"""Tests for humanize_score.py: pattern catalogue, scoring, CLI, and hook mode."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCORER = REPO / "scripts" / "humanize_score.py"
HOOK = REPO / "hooks" / "humanize-post-write.sh"

spec = importlib.util.spec_from_file_location("humanize_score", SCORER)
hs = importlib.util.module_from_spec(spec)
sys.modules["humanize_score"] = hs  # dataclass decorator needs the module registered
spec.loader.exec_module(hs)

SLOP = (
    "Studies show that this delves into the intricate landscape, marking a pivotal "
    "moment. Additionally, it serves as a testament to the vibrant tapestry of "
    "innovation, inspiration, and industry insights. The future looks bright."
)

# Exemplar sentences that must trigger each regex-based pattern added or
# renumbered in the v2.11.2 re-sync (pattern id -> (name, text)).
EXEMPLARS = {
    30: ("previous_version_writing", "This replaces the previous approach of iterating."),
    31: ("forced_punchlines", "Then it arrived. No aesthetic prior. No nostalgia at all."),
    32: ("formulaic_sayings", "Symmetry is the language of trust."),
    33: ("fake_candid_openers", "Honestly? It depends on usage."),
    34: ("shadowboxing", "I'm not saying documentation is useless."),
    35: ("fake_alternatives", "A tempting approach would be to restart the service."),
    36: ("citation_laundering", "Studies show that results improved."),
    37: ("manuscript_boilerplate", "To the best of our knowledge, nothing exists."),
    38: ("tutorial_scaffolding", "Let's walk through how the pipeline works."),
    39: ("stat_parade", "The difference was significant (p < 0.001)."),
    40: ("temporal_hedges", "Currently, the field is evolving."),
    42: ("ai_commit_verbs", "feat: improves robustness and enhances functionality"),
    43: ("methodology_pseudo", "A careful evaluation was performed."),
    44: ("dissertation_hedging", "It can be argued that this has advantages."),
}


def test_pattern_ids_are_1_to_44_with_unique_names():
    pids = [p.pid for p in hs.PATTERNS]
    names = [p.name for p in hs.PATTERNS]
    assert pids == list(range(1, 45))
    assert len(set(names)) == 44


@pytest.mark.parametrize("pid", sorted(EXEMPLARS))
def test_pattern_fires_on_exemplar(pid):
    name, text = EXEMPLARS[pid]
    pattern = next(p for p in hs.PATTERNS if p.pid == pid)
    assert pattern.name == name
    assert hs.score_text(text)["breakdown"].get(name, 0) >= 1


def test_polysyndetic_tripleting_needs_three_triplets_in_one_paragraph():
    para = (
        "The tool is fast, robust, and scalable. It serves researchers, clinicians, "
        "and educators. The code is open, transparent, and reproducible."
    )
    assert hs.score_text(para)["breakdown"].get("polysyndetic_tripleting", 0) >= 1
    assert "polysyndetic_tripleting" not in hs.score_text("It is fast, small, and free.")["breakdown"]


def test_formulaic_sayings_requires_copula():
    text = "The architecture of the plugin is described in three files."
    assert "formulaic_sayings" not in hs.score_text(text)["breakdown"]


def test_clean_prose_scores_clean():
    text = (
        "We measured the parser on 40 files. It failed twice, both on CRLF input. "
        "The fix handles both cases and the suite passes now."
    )
    result = hs.score_text(text)
    assert result["score"] < 20
    assert result["verdict"] == "clean"


def test_slop_scores_heavy():
    assert hs.score_text(SLOP)["score"] > 60


def test_commit_profile_zeroes_citation_laundering_weight():
    result = hs.score_text("Studies show that results improved.", profile="commit")
    assert result["weighted"].get("citation_laundering", 0) == 0


@pytest.mark.parametrize(
    "filename,profile",
    [
        ("MANUSCRIPT_v2.md", "academic"),
        ("chapter.tex", "academic"),
        ("README.md", "docs"),
        ("notes.commit", "commit"),
        ("COMMIT_EDITMSG", "commit"),
        ("post.md", "blog"),
    ],
)
def test_detect_profile(filename, profile):
    assert hs.detect_profile(Path(filename)) == profile


def test_cli_json_and_threshold_exit_codes(tmp_path, capsys):
    f = tmp_path / "slop.md"
    f.write_text(SLOP, encoding="utf-8")
    assert hs.main(["--json", "--threshold=100", str(f)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["score"] > 60
    assert hs.main(["--json", "--threshold=10", str(f)]) == 1


def run_hook_mode(payload: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, str(SCORER), "--hook"],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )


def hook_payload(path: Path) -> str:
    return json.dumps({"tool_input": {"file_path": str(path)}})


def test_hook_mode_warns_on_slop(tmp_path):
    f = tmp_path / "slop.md"
    f.write_text(SLOP, encoding="utf-8")
    proc = run_hook_mode(hook_payload(f))
    assert proc.returncode == 0
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]
    assert ctx["hookEventName"] == "PostToolUse"
    assert str(f) in ctx["additionalContext"]


def test_hook_mode_silent_on_clean_nonprose_and_missing(tmp_path):
    clean = tmp_path / "clean.md"
    clean.write_text("Short factual note. Nothing fancy here.", encoding="utf-8")
    code = tmp_path / "script.py"
    code.write_text("print('hi')", encoding="utf-8")
    for payload in (
        hook_payload(clean),
        hook_payload(code),
        hook_payload(tmp_path / "missing.md"),
        "not json at all",
        "{}",
    ):
        proc = run_hook_mode(payload)
        assert proc.returncode == 0
        assert proc.stdout == ""


def test_hook_mode_threshold_env(tmp_path):
    f = tmp_path / "slop.md"
    f.write_text(SLOP, encoding="utf-8")
    # A garbage threshold falls back to 60 and still warns on heavy slop.
    proc = run_hook_mode(hook_payload(f), {"HUMANIZE_THRESHOLD": "banana"})
    assert proc.returncode == 0
    assert "additionalContext" in proc.stdout
    # An impossible threshold silences the warning.
    proc = run_hook_mode(hook_payload(f), {"HUMANIZE_THRESHOLD": "1000"})
    assert proc.stdout == ""


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
@pytest.mark.parametrize("filename", ["slop.md", "SLOP.MD"])
def test_bash_hook_end_to_end(tmp_path, filename):
    f = tmp_path / filename
    f.write_text(SLOP, encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=hook_payload(f),
        capture_output=True,
        text=True,
        env={**os.environ, "HUMANIZE_SCORER": str(SCORER)},
    )
    assert proc.returncode == 0
    assert "additionalContext" in json.loads(proc.stdout)["hookSpecificOutput"]
