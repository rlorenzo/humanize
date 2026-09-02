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


# ---- Normalisation ------------------------------------------------------------


def test_normalise_strips_markup_the_word_tokenizer_used_to_see():
    """The bug normalise() exists to close: every metric must read one string.

    split_sentences always stripped fences and headings; the word tokenizer in
    analyse did not, so lexical_diversity and function_word_ratio scored markup
    that the CV metrics had already discarded.
    """
    text = "# Heading\n\nReal prose here.\n\n```\nimport os\n```\n\n| a | b |\n|---|---|\n"
    out = bc.normalise(text)
    for markup in ("Heading", "import os", "|---|"):
        assert markup not in out
    assert "Real prose here." in out


def test_normalise_unescapes_entities_so_contractions_stay_one_token():
    """HC3 carries &#39;. Left escaped, "doesn&#39;t" tokenizes as two non-words."""
    words = bc.WORD_RE.findall(bc.normalise("It doesn&#39;t work.").lower())
    assert "doesn't" in words
    assert words == ["it", "doesn't", "work"]


def test_normalise_keeps_anchor_text_and_drops_link_targets():
    out = bc.normalise("See [the docs](https://example.com/a/b?c=d) now.")
    assert "the docs" in out
    assert "example.com" not in out


def test_normalise_leaves_prose_comparisons_alone():
    """A tag needs a name character after "<"; "x < 5 and y > 3" is not markup."""
    text = "We keep x < 5 and y > 3 in every case."
    assert bc.normalise(text) == text


def test_normalise_strips_real_tags_and_comments():
    out = bc.normalise("A <br> line, <!-- note --> and <a href='u'>anchor</a>.")
    assert "<" not in out and "note" not in out
    assert "anchor" in out


def test_normalise_strips_markup_that_arrived_escaped():
    """HC3 carries escaped markup. Unescaping runs before the tag pass so it goes.

    When the order was reversed, "&lt;b&gt;" survived the tag pass as text and was
    then unescaped into a live <b> that nothing removed -- which put the word
    tokenizer and the sentence splitter back on two different strings, the exact
    bug normalise() exists to close.
    """
    out = bc.normalise("Look at &lt;b&gt;bold&lt;/b&gt; text right here in this line.")
    assert "<" not in out and ">" not in out
    assert "bold text" in out


def test_normalise_keeps_placeholders_and_generics():
    """Angle brackets are not markup by shape. Deleting prose is unrecoverable.

    A shape-based tag regex ate "<YOUR_API_KEY>" and the "<Integer>" of
    "List<Integer>", which are ordinary content in technical writing and biased
    both vocabulary metrics downward on exactly the CS-adjacent text this tool
    is pointed at.
    """
    for text in (
        "Replace <YOUR_API_KEY> with your real key before running this script.",
        "In Java, a List<Integer> holds only integers, unlike a raw List here.",
    ):
        assert bc.normalise(text) == text


def test_normalise_handles_nested_brackets_in_link_text():
    """[a [b] c](url) matched nothing, leaking the brackets and the whole URL."""
    out = bc.normalise("Check [this [nested] thing](https://example.com) now.")
    assert "example.com" not in out
    assert "this" in out and "thing" in out


def test_normalise_strips_reference_style_and_bare_urls():
    out = bc.normalise("See [the docs][1] here.\n\n[1]: https://example.com/a/b\n")
    assert "example.com" not in out
    out = bc.normalise("Read https://example.com/x?y=1 and then stop.")
    assert "example.com" not in out


def test_normalise_keeps_the_full_stop_after_a_url():
    """A URL must not take the sentence boundary with it.

    "[^\\s<>)\\]]+" happily consumed the trailing period, so a sentence ending in
    a bare link merged into the next one -- a silent, systematic corruption of
    sentence_cv, the strongest metric measured so far.
    """
    with_url = "Visit https://example.com/page. It has more info here. A third one now."
    control = "Visit the website today. It has more info here. A third one now."
    assert len(bc.split_sentences(with_url)) == len(bc.split_sentences(control))
    assert "example.com" not in bc.normalise(with_url)


def test_normalise_strips_autolinks_and_parenthesised_urls():
    assert "example.com" not in bc.normalise("Read <https://example.com/x> for detail.")
    out = bc.normalise("(see https://example.com/a) and more text follows here.")
    assert "example.com" not in out and out.count(")") == 1


def test_normalise_tag_match_survives_a_quoted_angle_bracket():
    """A ">" inside an attribute value ended the match and stranded the rest."""
    out = bc.normalise('<b class="x" data-y="1 > 2">bold text</b> stays visible?')
    assert out.strip() == "bold text stays visible?"


def test_normalise_handles_a_url_containing_parentheses():
    """Wikipedia disambiguation targets end at the first ")" without nesting."""
    out = bc.normalise("See [this article](https://en.wikipedia.org/wiki/X_(disambiguation)) here.")
    assert "wikipedia" not in out.lower()
    assert ")" not in out


def test_analyse_normalises_exactly_once():
    """normalise() cannot be idempotent, so analyse must not apply it twice.

    "&amp;lt;" unescapes to "&lt;" on one pass and "<" on the next, so no ordering
    makes a second application a no-op. analyse therefore calls the private
    splitters on already-normalised text; if it ever goes back to calling the
    public ones, the word list and the sentence list diverge again.
    """
    text = "Prose about &amp;lt;b&amp;gt; markup, written out at some length here.\n"
    normalised = bc.normalise(text)
    assert bc.normalise(normalised) != normalised  # the property that forces the design

    result = bc.analyse(text)
    assert result["metrics"]["sentence_count"] == len(bc._sentences_from(normalised))
    assert result["metrics"]["paragraph_count"] == len(bc._paragraphs_from(normalised))
    assert result["metrics"]["word_count"] == len(bc.WORD_RE.findall(normalised.lower()))


def test_analyse_scores_markdown_and_its_stripped_equivalent_alike():
    """Same prose, with and without markup, must not land in different bands."""
    prose = HUMAN
    dressed = f"# A heading\n\n{prose}\n\n```python\nx = [1, 2, 3]\n```\n"
    assert bc.analyse(dressed)["metrics"]["function_word_ratio"] == pytest.approx(
        bc.analyse(prose)["metrics"]["function_word_ratio"], abs=0.02
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


def test_lexical_diversity_is_undefined_below_the_window():
    """Under 50 words MATTR-50 has no meaning, so the metric declines to guess.

    It used to return whole-document TTR here. TTR falls as a document grows and
    MATTR does not, so that made one name carry two incomparable quantities, both
    checked against a single band.
    """
    assert bc.lexical_diversity(["a", "b", "a", "b"]) is None
    assert bc.lexical_diversity([]) is None
    assert bc.lexical_diversity([f"w{i}" for i in range(bc.MATTR_WINDOW - 1)]) is None
    # Exactly at the window it becomes defined.
    assert bc.lexical_diversity([f"w{i}" for i in range(bc.MATTR_WINDOW)]) == 1.0


def test_undefined_metric_contributes_no_flag():
    """A metric measuring None is skipped rather than flagged."""
    values = {
        "sentence_cv": None,
        "paragraph_cv": None,
        "lexical_diversity": None,
        "function_word_ratio": 0.28,
        "subordinate_density": 0.04,
    }
    assert bc.apply_checks(values, bc.resolve_targets("default")) == []


def test_cv_becomes_defined_at_exactly_two_units():
    """The null/defined boundary, both sides of it.

    One sentence is undefined; two is measurable. Pinned because the change from
    0.0 to None is what stopped one-sentence files being flagged for uniform
    pacing, and an off-by-one here would silently restore that.
    """
    two_sentences = bc.analyse("A first sentence sits here. A second sentence follows it.")
    assert two_sentences["metrics"]["sentence_cv"] is not None

    para = "This paragraph carries well over five words in it."
    assert bc.analyse(para)["metrics"]["paragraph_cv"] is None
    assert bc.analyse(f"{para}\n\n{para}")["metrics"]["paragraph_cv"] is not None


def test_single_sentence_text_is_undefined_not_uniform():
    """One sentence has no length variation to measure, so it must not flag.

    coefficient_of_variation returns 0.0 for a single value, which reads as
    perfect uniformity -- the same degeneracy that made paragraph_cv untestable
    on both calibration corpora.
    """
    result = bc.analyse("A single solitary sentence sits here all by itself alone.")
    assert result["metrics"]["sentence_cv"] is None
    assert result["flags"] == []


def test_short_text_reports_lexical_diversity_as_none():
    result = bc.analyse("Short note. Not much here.")
    assert result["metrics"]["lexical_diversity_mattr50"] is None
    assert not any("lexical_diversity" in f for f in result["flags"])


def test_lexical_diversity_long_text_uses_moving_window():
    # 200 distinct words -> every 50-word window is all-unique -> MATTR 1.0.
    assert bc.lexical_diversity([f"w{i}" for i in range(200)]) == 1.0
    # A single word repeated 200 times -> every window has one type.
    assert bc.lexical_diversity(["same"] * 200) == pytest.approx(1 / 50)


def test_function_word_ratio():
    assert bc.function_word_ratio([]) == 0.0
    assert bc.function_word_ratio(["the", "and", "parser", "exploded"]) == 0.5


def test_function_words_include_contractions():
    """The tokenizer is [A-Za-z']+, so contractions arrive whole.

    Without these entries the commonest function words in natural prose counted
    against the ratio instead of toward it.
    """
    for token in ["don't", "it's", "they're", "i'm", "can't", "let's"]:
        assert token in bc.FUNCTION_WORDS, token
    assert bc.function_word_ratio(["it's", "not", "ready", "yet"]) == 0.75


def test_function_words_cover_the_closed_classes():
    """Spot-check each grammatical class the inventory claims to cover."""
    for token in [
        "the",  # determiner
        "themselves",  # pronoun
        "throughout",  # preposition
        "whereas",  # subordinator
        "might",  # modal auxiliary
        "not",  # negator
    ]:
        assert token in bc.FUNCTION_WORDS, token
    # Content words stay out.
    for token in ["parser", "manuscript", "calibrate", "burstiness"]:
        assert token not in bc.FUNCTION_WORDS, token


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


def test_only_validated_metrics_carry_a_threshold():
    """The composite's retirement, pinned.

    Four of the five metrics failed corpus validation (see the module docstring
    for the AUCs) and now carry no band. Re-adding one silently would restore the
    original bug -- lexical_diversity's inherited [0.40, 0.65] fires on every
    document of ordinary prose, which runs 0.78-0.85 on MATTR-50.
    """
    assert {check.metric for check in bc.CHECKS} == {"sentence_cv"}
    assert set(bc.resolve_targets("default")) == {"sentence_cv"}
    assert set(bc.UNBANDED_METRICS) == {
        "paragraph_cv",
        "lexical_diversity",
        "function_word_ratio",
        "subordinate_density",
    }
    # Derived, not a second hand-maintained list: every metric is banded or not.
    assert set(bc.METRICS) == {c.metric for c in bc.CHECKS} | set(bc.UNBANDED_METRICS)


def test_esl_profile_loosens_only_sentence_cv():
    default, esl = bc.resolve_targets("default"), bc.resolve_targets("esl")
    assert default["sentence_cv"] == 0.55
    assert esl["sentence_cv"] == 0.50
    assert {k: v for k, v in default.items() if k != "sentence_cv"} == {
        k: v for k, v in esl.items() if k != "sentence_cv"
    }


def test_every_offered_profile_changes_something():
    """A profile that resolves to `default` is a name promising a tuning that is
    not there. `academic`, `blog` and `docs` did exactly that for three releases
    and were removed in 2.0.0; this fails if one is added back inert.
    """
    default = bc.resolve_targets("default")
    assert bc.PROFILES[0] == "default"
    for profile in bc.PROFILES[1:]:
        assert bc.resolve_targets(profile) != default, f"{profile} is a no-op profile"


def test_retired_profile_is_rejected_not_silently_defaulted():
    """The removed names must fail loudly. Falling through to default targets is
    how an inert profile survives removal: analyse() would echo "academic" back
    while applying the default bar.
    """
    for profile in ("academic", "blog", "docs", ""):
        with pytest.raises(ValueError):
            bc.resolve_targets(profile)
    with pytest.raises(ValueError):
        bc.analyse("One sentence. And another one here.", profile="academic")


def test_apply_checks_silent_when_every_metric_is_in_band():
    passing = {
        "sentence_cv": 0.80,
        "paragraph_cv": 0.60,
        "lexical_diversity": 0.50,
        "function_word_ratio": 0.45,
        "subordinate_density": 0.15,
    }
    assert bc.apply_checks(passing, bc.resolve_targets("default")) == []


def test_apply_checks_flags_only_sentence_cv():
    """Values that used to raise four flags now raise one.

    lexical_diversity 0.85 and function_word_ratio 0.28 are ordinary for English
    prose; under the retired bands both were flagged, and between them they
    supplied most of the composite.
    """
    values = {
        "sentence_cv": 0.30,  # 0.25 below target -> the one real flag
        "paragraph_cv": 0.05,
        "lexical_diversity": 0.85,
        "function_word_ratio": 0.28,
        "subordinate_density": 0.04,
    }
    flags = bc.apply_checks(values, bc.resolve_targets("default"))
    assert [f.split(" (")[0] for f in flags] == ["sentence_cv too low"]


def test_metric_exactly_at_target_is_not_flagged():
    targets = bc.resolve_targets("default")
    at_target = {
        "sentence_cv": targets["sentence_cv"],
        "paragraph_cv": 0.0,
        "lexical_diversity": 0.0,
        "function_word_ratio": 0.0,
        "subordinate_density": 0.0,
    }
    assert bc.apply_checks(at_target, targets) == []


def test_undefined_sentence_cv_target_is_gone():
    """verdict_for and the penalty field went with the composite."""
    assert not hasattr(bc, "verdict_for")
    check = bc.Check("sentence_cv", "sentence_cv", "hint")
    assert not hasattr(check, "penalty")
    assert not hasattr(check, "bound")  # upper bounds went with the retired ceilings


@pytest.mark.parametrize(
    ("fail_on", "flags", "expected"),
    [
        ("any", [], False),
        ("any", ["sentence_cv too low (x); hint"], True),
        ("cv", ["sentence_cv too low (x); hint"], True),
        ("cv", ["lexical_diversity too low (x); hint"], False),
        ("never", ["sentence_cv too low (x); hint"], False),
    ],
)
def test_should_fail_honours_the_gate(fail_on, flags, expected):
    assert bc.should_fail(flags, fail_on) is expected


# ---- Analysis -----------------------------------------------------------------


def test_analyse_empty_text_does_not_crash():
    result = bc.analyse("")
    assert result["metrics"]["sentence_count"] == 0
    assert result["metrics"]["word_count"] == 0
    assert result["flags"] == []


def test_analyse_no_longer_reports_a_composite():
    """The breaking part of the contract, pinned so it cannot creep back."""
    result = bc.analyse(UNIFORM)
    assert "signature_score" not in result
    assert "verdict" not in result
    assert set(result) == {"profile", "metrics", "targets", "unbanded", "flags"}


def test_adjacent_block_tags_stay_two_sentences():
    """No source whitespace between the tags -- the case an empty replacement ate.

    "<p>One.</p><p>Two.</p>" collapsing to "One.Two." is one sentence where the
    prose gives two, which silently biases sentence_cv, the only validated metric.
    """
    assert len(bc.split_sentences("<p>First sentence.</p><p>Second sentence here.</p>")) == 2
    assert len(bc.split_sentences("<div>First sentence.</div><div>Second one.</div>")) == 2
    assert len(bc.split_sentences("Line one.<br>Line two.")) == 2
    # A paragraph needs five words to count, so these run longer than the above.
    assert (
        len(
            bc.split_paragraphs(
                "<p>The first paragraph runs on a while.</p>"
                "<p>The second paragraph does too, at length.</p>"
            )
        )
        == 2
    )


def test_comments_go_whether_they_arrived_raw_or_escaped():
    """The comment pass has to run after html.unescape, like the tag pass.

    Stripping comments first left "&lt;!-- note --&gt;" alone, and the unescape
    then produced a live comment nothing removed -- so its words reached the
    tokenizer and the markers survived as text.
    """
    out = bc.normalise("Real prose here. &lt;!-- hidden reviewer note --&gt; More prose.")
    for leaked in ("hidden", "reviewer", "note", "<!--", "-->"):
        assert leaked not in out
    assert "Real prose here." in out and "More prose." in out

    # The raw form must still go; moving the pass must not trade one for the other.
    assert "note" not in bc.normalise("Real prose. <!-- note --> More prose.")


def test_script_and_style_bodies_go_with_their_tags():
    """Raw-text elements carry code, not prose.

    Stripping only the tags left "var x=1;function f(){return 42;}" in the word
    stream as var/x/function/f/return -- a run of unique non-words that inflates
    lexical_diversity and depresses function_word_ratio, which is the same reason
    code fences are removed.
    """
    out = bc.normalise("Real prose here. <script>var x=1;function f(){return 42;}</script> More.")
    for leaked in ("var", "function", "return", "42"):
        assert leaked not in out
    assert "Real prose here." in out and "More." in out

    out = bc.normalise("Real prose here. <style>.cls{color:#fff;margin:0 auto}</style> More.")
    assert "color" not in out and "margin" not in out

    # Arriving escaped, as HC3 markup does -- the strip runs after html.unescape.
    out = bc.normalise("Prose. &lt;script&gt;var y = 2;&lt;/script&gt; Tail prose.")
    assert "var" not in out and "Tail prose." in out


def test_unclosed_script_takes_its_body_with_it():
    """No closing tag: the body still must not reach the tokenizer.

    Leaving _BLOCK_TAGS to catch this is not enough -- that pass removes the tag
    and leaves "var x = 1;" behind, which is the entire defect. An unclosed
    element consumes to end of text, which is what a browser does with one.
    """
    for text in ("Prose here. <script>var x = 1;", "Prose here. &lt;script&gt;var x = 1;"):
        out = bc.normalise(text)
        assert "var" not in out, out
        assert "Prose here." in out


def test_inline_tags_still_close_up_and_placeholders_survive():
    """The block fix must not turn inline markup into a boundary, or start
    deleting the angle-bracket constructs the tag list was written to protect.
    """
    assert bc.normalise("Keep <strong>inline</strong> tags joined.") == ("Keep inline tags joined.")
    assert bc.normalise("A <YOUR_API_KEY> and List<Integer> survive.") == (
        "A <YOUR_API_KEY> and List<Integer> survive."
    )


def test_unbanded_entries_are_keys_into_metrics():
    """`unbanded` is only useful if it indexes `metrics`. lexical_diversity is
    reported as lexical_diversity_mattr50, so the internal name would KeyError
    for a caller iterating the array to find the diagnostic-only entries.
    """
    result = bc.analyse(HUMAN)
    assert result["unbanded"], "nothing unbanded; the array lost its contents"
    for name in result["unbanded"]:
        assert name in result["metrics"], f"{name} is not a key in metrics"


def test_analyse_uniform_prose_raises_the_pacing_flag():
    flags = bc.analyse(UNIFORM)["flags"]
    assert [f.split(" (")[0] for f in flags] == ["sentence_cv too low"]


def test_analyse_varied_prose_raises_no_flags():
    assert bc.analyse(HUMAN)["flags"] == []


def test_analyse_reports_the_profile_and_its_targets():
    result = bc.analyse(HUMAN, profile="esl")
    assert result["profile"] == "esl"
    assert result["targets"]["sentence_cv"] == 0.50


def test_esl_profile_never_flags_more_than_default():
    assert len(bc.analyse(UNIFORM, "esl")["flags"]) <= len(bc.analyse(UNIFORM)["flags"])


def test_retired_metrics_are_still_measured():
    """Retired means unbanded, not unreported -- they stay as diagnostics."""
    m = bc.analyse(HUMAN)["metrics"]
    for key in (
        "paragraph_cv",
        "lexical_diversity_mattr50",
        "function_word_ratio",
        "subordinate_density",
        "shannon_entropy_bits",
    ):
        assert key in m


# ---- CLI ----------------------------------------------------------------------


def test_cli_json_output_and_fail_on_exit_codes(tmp_path, capsys):
    target = tmp_path / "draft.md"
    target.write_text(UNIFORM, encoding="utf-8")

    assert bc.main([str(target), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["path"] == str(target)
    assert payload["flags"]
    assert "signature_score" not in payload

    assert bc.main([str(target), "--fail-on", "never"]) == 0
    capsys.readouterr()
    assert bc.main([str(target), "--fail-on", "cv"]) == 1
    capsys.readouterr()


def test_cli_clean_file_exits_zero(tmp_path, capsys):
    target = tmp_path / "clean.md"
    target.write_text(HUMAN, encoding="utf-8")
    assert bc.main([str(target)]) == 0
    assert "flags:           none" in capsys.readouterr().out


def test_cli_threshold_is_accepted_but_deprecated(tmp_path, capsys):
    """.github/smoke-console-scripts.sh passes --threshold 100; it must not die.

    The flag is accepted and ignored for one minor version, with a warning on
    stderr, rather than removed -- an unrecognised argument would exit 2 and
    break that CI job.
    """
    target = tmp_path / "draft.md"
    target.write_text(UNIFORM, encoding="utf-8")
    assert bc.main([str(target), "--threshold", "100"]) == 1
    captured = capsys.readouterr()
    assert "--threshold is deprecated" in captured.err
    assert "signature_score" not in captured.out


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
