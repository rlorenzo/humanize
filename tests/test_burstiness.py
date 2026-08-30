"""Tests for burstiness_check.py: metrics, threshold checks, analysis, and CLI."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "humanize_anti_slop" / "burstiness_check.py"

spec = importlib.util.spec_from_file_location("burstiness_check", CHECKER)
bc = importlib.util.module_from_spec(spec)
sys.modules["burstiness_check"] = bc  # dataclass decorator needs the module registered
spec.loader.exec_module(bc)

# Uniform, comma-free, noun-heavy prose: trips every check.
UNIFORM = "\n\n".join(["The system processes the data reliably every single time."] * 6)

# Varied sentence and paragraph lengths with subordinate clauses and asides.
HUMAN = (
    "I rewrote the parser last night.\n\n"
    "It had been limping along for months, and every time someone added a feed "
    "the thing sprouted another special case, until nobody -- me included -- "
    "could say what it actually did with a malformed date. So I threw it out.\n\n"
    "The replacement is shorter, dumber, and it fails loudly, which is the only "
    "property that mattered.\n\n"
    "Good enough."
)


# ---- Text splitting -----------------------------------------------------------


def test_split_sentences_strips_fenced_code():
    text = "First one here.\n\n```\nnot a sentence. nor this one.\n```\n\nSecond one here."
    assert bc.split_sentences(text) == ["First one here.", "Second one here."]


def test_split_sentences_ignores_short_fragments():
    # Fragments under the minimum word count are dropped, so stray "Ok." is not a sentence.
    assert bc.split_sentences("Ok. This sentence has enough words to count.") == [
        "This sentence has enough words to count."
    ]


def test_split_paragraphs_splits_on_blank_lines():
    text = "one two three four five\n\nsix seven eight nine ten\n\n\neleven twelve thirteen fourteen fifteen"
    assert len(bc.split_paragraphs(text)) == 3


def test_split_paragraphs_drops_stubs_under_five_words():
    # Headings and one-line stubs would otherwise dominate paragraph-length variance.
    assert bc.split_paragraphs(
        "# Heading\n\nfour words is short\n\nthis paragraph has five words"
    ) == ["this paragraph has five words"]


# ---- Metrics ------------------------------------------------------------------


@pytest.mark.parametrize("values", [[], [5]])
def test_coefficient_of_variation_needs_two_values(values):
    assert bc.coefficient_of_variation(values) == 0.0


def test_coefficient_of_variation_zero_mean_does_not_divide_by_zero():
    assert bc.coefficient_of_variation([0, 0, 0]) == 0.0


def test_coefficient_of_variation_uniform_is_zero_varied_is_positive():
    assert bc.coefficient_of_variation([10, 10, 10]) == 0.0
    assert bc.coefficient_of_variation([2, 20, 5, 40]) > 0.5


def test_lexical_diversity_short_text_is_plain_ttr():
    assert bc.lexical_diversity(["a", "b", "a", "b"]) == 0.5
    assert bc.lexical_diversity([]) == 0.0


def test_lexical_diversity_long_text_uses_moving_window():
    # 200 distinct words -> every 50-word window is all-unique -> MATTR 1.0.
    assert bc.lexical_diversity([f"w{i}" for i in range(200)]) == 1.0
    # A single word repeated 200 times -> every window has one type.
    assert bc.lexical_diversity(["same"] * 200) == pytest.approx(1 / 50)


def test_function_word_ratio():
    assert bc.function_word_ratio([]) == 0.0
    assert bc.function_word_ratio(["the", "and", "parser", "exploded"]) == 0.5


def test_subordinate_density_counts_commas_and_semicolons():
    assert bc.subordinate_density("a, b; c", 0) == 0.0
    assert bc.subordinate_density("a, b; c", 4) == 0.5


def test_shannon_word_entropy():
    assert bc.shannon_word_entropy([]) == 0.0
    assert bc.shannon_word_entropy(["same"] * 8) == 0.0
    assert bc.shannon_word_entropy(["a", "b", "c", "d"]) == pytest.approx(2.0)


# ---- Threshold checks ---------------------------------------------------------


def test_every_check_targets_a_real_threshold():
    targets = bc.resolve_targets("default")
    for check in bc.CHECKS:
        assert check.target in targets, f"{check.metric} points at missing target {check.target}"
        assert check.bound in {"min", "max"}


def test_esl_profile_loosens_only_sentence_cv():
    default, esl = bc.resolve_targets("default"), bc.resolve_targets("esl")
    assert default["sentence_cv"] == 0.55
    assert esl["sentence_cv"] == 0.50
    assert {k: v for k, v in default.items() if k != "sentence_cv"} == {
        k: v for k, v in esl.items() if k != "sentence_cv"
    }


def test_apply_checks_silent_when_every_metric_is_in_band():
    passing = {
        "sentence_cv": 0.80,
        "paragraph_cv": 0.60,
        "lexical_diversity": 0.50,
        "function_word_ratio": 0.45,
        "subordinate_density": 0.15,
    }
    flags, deviations = bc.apply_checks(passing, bc.resolve_targets("default"))
    assert flags == []
    assert deviations == 0.0


def test_apply_checks_flags_both_directions_with_weighted_deviation():
    values = {
        "sentence_cv": 0.55,  # exactly at target -> no flag
        "paragraph_cv": 0.30,  # 0.10 below -> 10.0
        "lexical_diversity": 0.75,  # 0.10 above max -> 20.0
        "function_word_ratio": 0.45,  # in band
        "subordinate_density": 0.15,  # in band
    }
    flags, deviations = bc.apply_checks(values, bc.resolve_targets("default"))
    assert deviations == pytest.approx(30.0)
    assert [f.split(" (")[0] for f in flags] == [
        "paragraph_cv too low",
        "lexical_diversity too high",
    ]


def test_metric_exactly_at_target_is_not_flagged():
    targets = bc.resolve_targets("default")
    at_target = {
        "sentence_cv": targets["sentence_cv"],
        "paragraph_cv": targets["paragraph_cv"],
        "lexical_diversity": targets["lexical_diversity_min"],
        "function_word_ratio": targets["function_word_ratio_min"],
        "subordinate_density": targets["subordinate_density_min"],
    }
    assert bc.apply_checks(at_target, targets) == ([], 0.0)


@pytest.mark.parametrize(
    ("score", "verdict"),
    [
        (0.0, "human-like"),
        (14.9, "human-like"),
        (15.0, "borderline"),
        (34.9, "borderline"),
        (35.0, "AI-uniform"),
        (59.9, "AI-uniform"),
        (60.0, "heavy-AI-signature"),
        (100.0, "heavy-AI-signature"),
    ],
)
def test_verdict_boundaries(score, verdict):
    assert bc.verdict_for(score) == verdict


# ---- Analysis -----------------------------------------------------------------


def test_analyse_empty_text_does_not_crash():
    result = bc.analyse("")
    assert result["metrics"]["sentence_count"] == 0
    assert result["metrics"]["word_count"] == 0
    assert result["signature_score"] <= 100.0


def test_analyse_uniform_prose_reads_as_ai():
    assert bc.analyse(UNIFORM)["verdict"] == "heavy-AI-signature"


def test_analyse_varied_prose_scores_below_uniform_prose():
    assert bc.analyse(HUMAN)["signature_score"] < bc.analyse(UNIFORM)["signature_score"]


def test_analyse_score_is_capped_at_100():
    assert bc.analyse("Data. " * 200)["signature_score"] <= 100.0


def test_analyse_reports_the_profile_and_its_targets():
    result = bc.analyse(HUMAN, profile="esl")
    assert result["profile"] == "esl"
    assert result["targets"]["sentence_cv"] == 0.50


def test_esl_profile_never_scores_worse_than_default():
    assert bc.analyse(UNIFORM, "esl")["signature_score"] <= bc.analyse(UNIFORM)["signature_score"]


# ---- CLI ----------------------------------------------------------------------


def test_cli_json_output_and_threshold_exit_codes(tmp_path, capsys):
    target = tmp_path / "draft.md"
    target.write_text(UNIFORM, encoding="utf-8")

    assert bc.main([str(target), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["path"] == str(target)
    assert payload["signature_score"] > 35

    # A threshold above the score flips the exit code to success.
    assert bc.main([str(target), "--threshold", "100"]) == 0
    assert "signature_score:" in capsys.readouterr().out


def test_cli_missing_file_exits_two(tmp_path, capsys):
    assert bc.main([str(tmp_path / "nope.md")]) == 2
    assert "is not a file" in capsys.readouterr().err


def test_cli_runs_as_a_subprocess(tmp_path):
    target = tmp_path / "draft.md"
    target.write_text(HUMAN, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(CHECKER), str(target), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode in (0, 1)
    assert json.loads(proc.stdout)["profile"] == "default"
