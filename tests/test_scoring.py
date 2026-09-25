"""Tests for humanize_score.py: pattern catalogue, scoring, CLI, and hook mode."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCORER = REPO / "humanize_anti_slop" / "humanize_score.py"
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

# Exemplar sentences that must trigger each regex-based pattern
# (pattern name -> (id, text)), numbered as in the v3.0.0 re-sync.
EXEMPLARS = {
    "not_x_but_y": (1, "This does not mean every choice is equal. It means none is checked."),
    "one_line_closers": (2, "Then it arrived. No aesthetic prior. No nostalgia at all."),
    "deep_sayings": (3, "Symmetry is the language of trust."),
    "staged_runup": (4, "Honestly? It depends on usage."),
    "arguing_with_no_one": (5, "A tempting approach would be to restart the service."),
    "forced_triads": (6, "It covers innovation, inspiration, and insights."),
    "dashes": (8, "The policy -- announced without warning -- applies now."),
    "stacked_qualifiers": (9, "It could potentially affect outcomes."),
    "hyphenated_pairs": (10, "The report is high-quality."),
    "passive_voice": (11, "The results were preserved automatically."),
    "ai_vocabulary": (12, "We delve into the details."),
    "inflated_significance": (13, "Despite these challenges, the town continues to thrive."),
    "vague_connection": (14, "He is associated with the Rajhans Orchestra."),
    "shallow_ing": (15, "The colors are blue and gold, symbolizing the bluebonnets."),
    "sales_language": (16, "The town is nestled in the hills."),
    "borrowed_authority": (17, "Her views have been cited in The Hindu."),
    "copula_avoidance": (18, "Gallery 825 serves as the exhibition space."),
    "bold_decoration": (19, "It blends **OKRs** and **KPIs**."),
    "restated_bold_labels": (19, "- **Performance:** Performance has improved."),
    "decorative_emojis": (20, "\U0001f680 Launch phase starts in Q3."),
    "curly_quotes": (21, "He said \u201cthe project is on track\u201d yesterday."),
    "chatbot_residue": (22, "Great question! The answer is four."),
    "cutoff_disclaimer": (23, "Her early life is not publicly available."),
    "previous_version_writing": (25, "This replaces the previous approach of iterating."),
    "citation_laundering": (26, "Studies show that results improved."),
    "manuscript_boilerplate": (27, "To the best of our knowledge, nothing exists."),
    "tutorial_scaffolding": (28, "Let's walk through how the pipeline works."),
    "stat_parade": (29, "The difference was significant (p < 0.001)."),
    "temporal_hedges": (30, "Currently, the field is evolving."),
    "ai_commit_verbs": (32, "feat: improves robustness and enhances functionality"),
    "methodology_pseudo": (33, "A careful evaluation was performed."),
    "dissertation_hedging": (34, "It can be argued that this has advantages."),
}


def test_pattern_ids_are_1_to_34_with_unique_names():
    catalogue = [(p.pid, p.name) for p in hs.PATTERNS] + [h[:2] for h in hs.HEURISTICS]
    assert {pid for pid, _ in catalogue} == set(range(1, 35))
    # #19 and #20 each have two parts; every other id has one.
    assert len(catalogue) == 36
    assert len({name for _, name in catalogue}) == 36


@pytest.mark.parametrize(
    "text", ["It is a high-quality report.", "It is a high-quality, data-driven report."]
)
def test_hyphenated_pair_before_a_noun_is_not_flagged(text):
    assert "hyphenated_pairs" not in hs.score_text(text)["breakdown"]


def test_hyphenated_pair_before_a_comma_in_predicate_is_flagged():
    text = "The team is cross-functional, and the report is late."
    assert hs.score_text(text)["breakdown"].get("hyphenated_pairs") == 1


@pytest.mark.parametrize("text", ["That is the real win.", "That\u2019s the real win."])
def test_one_line_closer_watch_phrase(text):
    assert hs.score_text(text)["breakdown"].get("one_line_closers") == 1


def test_en_dash_in_a_number_range_is_not_flagged():
    assert "dashes" not in hs.score_text("The survey ran 1990–2000.")["breakdown"]


def test_every_regex_pattern_has_an_exemplar():
    assert {p.name for p in hs.PATTERNS} == set(EXEMPLARS)


@pytest.mark.parametrize("name", sorted(EXEMPLARS))
def test_pattern_fires_on_exemplar(name):
    pid, text = EXEMPLARS[name]
    pattern = next(p for p in hs.PATTERNS if p.name == name)
    assert pattern.pid == pid
    assert hs.score_text(text)["breakdown"].get(name, 0) >= 1


def test_polysyndetic_tripleting_needs_three_triplets_in_one_paragraph():
    para = (
        "The tool is fast, robust, and scalable. It serves researchers, clinicians, "
        "and educators. The code is open, transparent, and reproducible."
    )
    assert hs.score_text(para)["breakdown"].get("polysyndetic_tripleting", 0) >= 1
    assert (
        "polysyndetic_tripleting" not in hs.score_text("It is fast, small, and free.")["breakdown"]
    )


@pytest.mark.parametrize(
    "text",
    ["It is not just fast, but cheap.", "This is not only a parser but a linter."],
)
def test_not_x_but_y_matches_real_sentences(text):
    # The regex once required a literal "X" where the first clause goes.
    assert hs.score_text(text)["breakdown"].get("not_x_but_y", 0) >= 1


@pytest.mark.parametrize(
    "name,text",
    [
        ("not_x_but_y", "It\u2019s not just about speed."),
        ("chatbot_residue", "You\u2019re absolutely right."),
        ("chatbot_residue", "That\u2019s a great idea."),
        ("tutorial_scaffolding", "Let\u2019s walk through the setup."),
    ],
)
def test_curly_apostrophes_match_like_straight_ones(name, text):
    assert hs.score_text(text)["breakdown"].get(name, 0) >= 1
    assert hs.score_text(text.replace("\u2019", "'"))["breakdown"].get(name, 0) >= 1


@pytest.mark.parametrize(
    "text",
    [
        "Studies show this works (Smith et al., 2020).",
        "Studies show this works (Smith et al. 2020).",
        "Studies show this works [12].",
    ],
)
def test_cited_claims_are_not_citation_laundering(text):
    assert "citation_laundering" not in hs.score_text(text)["breakdown"]


@pytest.mark.parametrize("end", [".", "!", "?"])
def test_citation_in_a_later_sentence_does_not_count(end):
    text = f"Studies show this works{end} We ran it again in 2021."
    assert hs.score_text(text)["breakdown"].get("citation_laundering", 0) == 1


@pytest.mark.parametrize(
    "phrase,name",
    [
        ("It serves as a hub.", "copula_avoidance"),
        ("It stands as a reminder.", "copula_avoidance"),
        ("This represents a significant advance.", "copula_avoidance"),
        ("It is a testament to them.", "ai_vocabulary"),
        ("The evolving landscape changed.", "ai_vocabulary"),
        ("Marking a pivotal moment for us.", "ai_vocabulary"),
        ("Underscoring its importance here.", "shallow_ing"),
        ("Underscoring\nits importance here.", "shallow_ing"),
        ("Fostering growth matters.", "ai_vocabulary"),
        ("It could be argued that it works.", "dissertation_hedging"),
        ("One might suggest that it works.", "dissertation_hedging"),
        ("Let's walk through the setup.", "tutorial_scaffolding"),
    ],
)
def test_a_phrase_is_counted_by_one_pattern_only(phrase, name):
    # These phrases each sat in two pattern lists, so one phrase scored twice.
    regex_hits = [p.name for p in hs.PATTERNS if p.regex.search(phrase)]
    assert regex_hits == [name]


@pytest.mark.parametrize(
    "text",
    [
        "But posting more isn't a plan. It's more noise.",
        "They're not scrolling Facebook. They're typing into Google.",
        "You're not behind. You're early.",
        "That doesn't look like an ad. It looks like pride.",
        "This does not mean every choice is equal. It means none is checked.",
    ],
)
def test_not_x_but_y_split_across_sentences(text):
    assert hs.score_text(text)["breakdown"].get("not_x_but_y", 0) >= 1


def test_negation_followed_by_an_unrelated_sentence_is_not_flagged():
    text = "The file is not cached. The next call fetches it."
    assert "not_x_but_y" not in hs.score_text(text)["breakdown"]


def test_hyperlink_is_not_a_vague_connection():
    text = "A reader sees your headline linked to your page."
    assert "vague_connection" not in hs.score_text(text)["breakdown"]
    assert hs.score_text("The rise is linked to rates.")["breakdown"].get("vague_connection") == 1


def test_deep_sayings_requires_copula():
    text = "The architecture of the plugin is described in three files."
    assert "deep_sayings" not in hs.score_text(text)["breakdown"]


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
        ("my-thesis.md", "academic"),
        ("paper_draft.md", "academic"),
        ("hypothesis.md", "blog"),
        ("wallpaper.md", "blog"),
        ("paperwork.md", "blog"),
        ("docs/guide.md", "docs"),
        ("./docs/guide.md", "docs"),
        ("/repo/docs/guide.md", "docs"),
        ("STAGE3/notes.md", "docs"),
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


def test_cli_prints_what_the_score_does_not_mean(tmp_path, capsys):
    """A "clean" verdict must not read as a detection-evasion guarantee.

    The scope line is the only thing standing between "humanize_score: 8.7/100
    (clean)" and a user concluding the text will pass a classifier. It points at
    DETECTION_ROBUSTNESS.md, which argues the opposite.
    """
    f = tmp_path / "clean.md"
    f.write_text("The cat sat. Rain fell all afternoon, and nobody minded.", encoding="utf-8")
    assert hs.main([str(f)]) == 0
    out = capsys.readouterr().out
    assert "scope:" in out
    assert "DETECTION_ROBUSTNESS.md" in out
    assert "not detector evasion" in out


def test_json_output_carries_no_scope_line(tmp_path, capsys):
    """Machines do not misread a verdict; people do. --json stays a data contract."""
    f = tmp_path / "clean.md"
    f.write_text("The cat sat. Rain fell all afternoon, and nobody minded.", encoding="utf-8")
    assert hs.main(["--json", str(f)]) == 0
    raw = capsys.readouterr().out
    assert "DETECTION_ROBUSTNESS.md" not in raw
    assert "scope" not in json.loads(raw)


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
    # No HUMANIZE_SCORER override: HOOK lives at REPO/hooks/, so the sibling
    # lookup already resolves to REPO/humanize_anti_slop/humanize_score.py.
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=hook_payload(f),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert proc.returncode == 0
    assert "additionalContext" in json.loads(proc.stdout)["hookSpecificOutput"]


# ---- Hook guardrails: size cap and the debug channel ---------------------------


def test_hook_skips_files_over_the_size_cap(tmp_path):
    # The hook runs on every write, so cost is paid interactively: ~6.7s on a 9MB
    # file before the cap existed.
    big = tmp_path / "big.md"
    big.write_text("Studies show that this delves into the intricate landscape. " * 40000)
    assert big.stat().st_size > hs.MAX_HOOK_BYTES
    reason = hs.hook_skip_reason(big, str(big))
    assert reason is not None and "MAX_HOOK_BYTES" in reason
    # And it stays silent rather than warning about a file it never scored.
    assert run_hook_mode(hook_payload(big)).stdout.strip() == ""


def test_hook_scores_a_file_just_under_the_cap(tmp_path):
    small = tmp_path / "small.md"
    small.write_text("Studies show that this delves into the intricate landscape.\n")
    assert small.stat().st_size < hs.MAX_HOOK_BYTES
    assert hs.hook_skip_reason(small, str(small)) is None


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("notes.py", "not prose"),
        ("missing.md", "not an existing file"),
    ],
)
def test_hook_skip_reason_explains_itself(tmp_path, filename, expected):
    assert expected in hs.hook_skip_reason(tmp_path / filename, str(tmp_path / filename))


def test_hook_skip_reason_rejects_an_empty_payload_path():
    assert "no tool_input.file_path" in hs.hook_skip_reason(Path(""), "")


def test_debug_is_silent_unless_enabled(capsys, monkeypatch):
    monkeypatch.delenv("HUMANIZE_DEBUG", raising=False)
    hs.debug("should not appear")
    assert capsys.readouterr().err == ""

    monkeypatch.setenv("HUMANIZE_DEBUG", "1")
    hs.debug("should appear")
    err = capsys.readouterr().err
    assert "should appear" in err and "[humanize:debug]" in err


def test_debug_output_goes_to_stderr_not_the_json_contract(tmp_path):
    # stdout carries the hook JSON; anything printed there would corrupt it.
    f = tmp_path / "draft.md"
    f.write_text(SLOP)
    proc = run_hook_mode(hook_payload(f), {"HUMANIZE_DEBUG": "1"})
    json.loads(proc.stdout)  # raises if debug text leaked into stdout


def test_debug_reports_why_a_clean_file_produced_no_warning(tmp_path):
    f = tmp_path / "draft.md"
    f.write_text("I rewrote the parser last night, and it works.\n")
    proc = run_hook_mode(hook_payload(f), {"HUMANIZE_DEBUG": "1"})
    assert proc.stdout.strip() == ""
    assert "at or under threshold" in proc.stderr


def test_bash_hook_passes_debug_through_but_swallows_it_otherwise(tmp_path):
    # The wrapper sends stderr to /dev/null normally; a silently broken scorer is
    # exactly how this project's hook bug went unnoticed for months.
    #
    # There is no env override to point the hook at a broken scorer anymore, so
    # this mirrors the trusted repo/plugin layout the sibling lookup resolves at
    # runtime: a copy of the hook next to a deliberately broken sibling scorer.
    f = tmp_path / "draft.md"
    f.write_text(SLOP)
    fake_root = tmp_path / "fake_plugin"
    (fake_root / "hooks").mkdir(parents=True)
    (fake_root / "humanize_anti_slop").mkdir()
    hook_copy = fake_root / "hooks" / "humanize-post-write.sh"
    shutil.copy2(HOOK, hook_copy)
    (fake_root / "humanize_anti_slop" / "humanize_score.py").write_text(
        "this is not valid python (\n"
    )

    quiet = subprocess.run(
        ["bash", str(hook_copy)],
        input=hook_payload(f),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert quiet.returncode == 0
    assert quiet.stdout.strip() == "" and quiet.stderr.strip() == ""

    loud = subprocess.run(
        ["bash", str(hook_copy)],
        input=hook_payload(f),
        capture_output=True,
        text=True,
        env={**os.environ, "HUMANIZE_DEBUG": "1"},
    )
    assert loud.returncode == 0
    assert "SyntaxError" in loud.stderr


def test_bash_hook_ignores_humanize_scorer_env_override(tmp_path):
    # Regression: HUMANIZE_SCORER used to let a cloned repo's checked-in
    # .claude/settings.json env block point the hook at arbitrary Python,
    # executed with the developer's privileges on the first Write/Edit.
    f = tmp_path / "draft.md"
    f.write_text(SLOP)
    marker = tmp_path / "pwned"
    evil = tmp_path / "evil.py"
    evil.write_text(f"open({str(marker)!r}, 'w').close()\n")

    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=hook_payload(f),
        capture_output=True,
        text=True,
        env={**os.environ, "HUMANIZE_SCORER": str(evil)},
    )
    assert proc.returncode == 0
    assert not marker.exists(), "HUMANIZE_SCORER must not be honored at hook runtime"


def test_bash_hook_survives_unset_home(tmp_path):
    # Regression: CLAUDE_HOME_REAL used to expand $HOME unguarded under
    # `set -u`, so an environment with HOME unset raised "unbound variable"
    # on stderr even though the hook still exited 0.
    f = tmp_path / "draft.md"
    f.write_text(SLOP)
    env = {k: v for k, v in os.environ.items() if k != "HOME"}

    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=hook_payload(f),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0
    assert "unbound variable" not in proc.stderr
