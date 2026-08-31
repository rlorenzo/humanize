"""Tests for the four heuristic pattern counters in humanize_score.py.

These are the patterns a regex cannot express, which makes them the ones most
likely to be subtly wrong and the least likely to have anyone notice. All four
were untested until this file existed; writing it found three real bugs.

Every counter gets boundary cases and negative controls. The negative controls
matter more than the positives here: a detector that fires on everything is as
useless as one that fires on nothing, which is the lesson burstiness_check's
signature_score taught this project the expensive way.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCORER = REPO / "humanize_anti_slop" / "humanize_score.py"

spec = importlib.util.spec_from_file_location("humanize_score_counters", SCORER)
hs = importlib.util.module_from_spec(spec)
sys.modules["humanize_score_counters"] = hs
spec.loader.exec_module(hs)


# ---- #11 synonym cycling ------------------------------------------------------


def test_synonym_cycling_fires_on_three_variants_in_one_paragraph():
    assert (
        hs.count_synonym_cycling(
            "The problem was real. The issue persisted. The challenge remained."
        )
        == 1
    )


def test_synonym_cycling_ignores_two_variants():
    """Two synonyms is ordinary writing; the pattern is cycling, not repetition."""
    assert hs.count_synonym_cycling("The problem was real. The issue persisted.") == 0


def test_synonym_cycling_is_per_paragraph_not_per_document():
    spread = "The problem was real.\n\nThe issue persisted.\n\nThe challenge remained."
    assert hs.count_synonym_cycling(spread) == 0


def test_synonym_cycling_counts_each_group_separately():
    """A paragraph cycling two different noun groups counts twice, not once.

    Pinning this because the docstring says it "reports paragraphs that match",
    which reads as one-per-paragraph. The count is per (paragraph, group) pair.
    """
    para = (
        "The company faced a problem. The organization saw the issue. The firm named the challenge."
    )
    assert hs.count_synonym_cycling(para) == 2


def test_synonym_cycling_matches_multi_word_terms():
    para = "The hero acted. The protagonist paused. The main character returned."
    assert hs.count_synonym_cycling(para) == 1


def test_synonym_cycling_requires_word_boundaries():
    """ "issued" must not count as "issue"."""
    para = "The problem was real. The permit was issued. A challenger appeared."
    assert hs.count_synonym_cycling(para) == 0


# ---- #17 title-case headings --------------------------------------------------


def test_title_case_heading_fires():
    assert hs.count_title_case_headings("## The Detection Ceiling") == 1


def test_sentence_case_heading_does_not_fire():
    assert hs.count_title_case_headings("## The detection ceiling") == 0


def test_title_case_ignores_non_heading_lines():
    assert hs.count_title_case_headings("The Detection Ceiling Is Here") == 0


def test_title_case_skips_headings_under_two_content_words():
    """One long word cannot establish a casing convention."""
    assert hs.count_title_case_headings("## Overview") == 0


def test_title_case_counts_every_heading_level():
    doc = "# One Two Three\n\n###### Four Five Six\n"
    assert hs.count_title_case_headings(doc) == 2


def test_title_case_false_positives_on_proper_nouns():
    """KNOWN LIMITATION, pinned deliberately rather than fixed.

    A sentence-case heading naming two proper nouns trips the >50% threshold.
    The `len(w) > 2` filter makes it worse by dropping exactly the lowercase
    function words ("of", "on", "in") that distinguish title case from sentence
    case, so they never reach the denominator.

    Fixing it means telling a proper noun from a title-cased common noun without
    a dictionary. Recorded here so the behaviour is a known trade-off rather than
    a surprise, and so a future fix has a test to flip.
    """
    assert hs.count_title_case_headings("## Working with GitHub Actions") == 1
    assert hs.count_title_case_headings("## Notes on CI and Ruff") == 1


# ---- #29 fragmented headers ---------------------------------------------------


def test_fragmented_header_fires_on_a_restating_stub():
    doc = "## Overview\nOverview of the system.\n\nReal content follows here.\n"
    assert hs.count_fragmented_headers(doc) == 1


def test_fragmented_header_fires_with_a_blank_line_after_the_heading():
    doc = "## Overview\n\nOverview of the system.\n\nReal content follows here.\n"
    assert hs.count_fragmented_headers(doc) == 1


def test_fragmented_header_ignores_a_short_line_that_says_something_new():
    """The negative control the pattern exists to respect.

    A heading followed by a genuinely short sentence introducing new material is
    normal prose. Without the restatement test this fired on it.
    """
    doc = "## Overview\n\nRust makes this cheap.\n\nMore content here.\n"
    assert hs.count_fragmented_headers(doc) == 0


def test_fragmented_header_ignores_an_ordinary_wrapped_paragraph():
    """Regression: a hard-wrapped paragraph whose first line is short.

    The previous implementation required the line after the short one to be
    non-blank, which inverted the check -- it fired here and stayed silent on a
    real fragmented header.
    """
    doc = "## Results\n\nThe results were clear.\nThey showed a strong effect everywhere.\n"
    assert hs.count_fragmented_headers(doc) == 0


def test_fragmented_header_ignores_a_long_restating_line():
    doc = (
        "## Overview\n\nOverview of the system, its parts, and every one of the "
        "connections between them.\n\nMore content.\n"
    )
    assert hs.count_fragmented_headers(doc) == 0


def test_fragmented_header_ignores_a_heading_followed_by_a_heading():
    assert hs.count_fragmented_headers("## Overview\n\n### Overview Details\n\nText.\n") == 0


def test_fragmented_header_handles_a_heading_at_end_of_file():
    assert hs.count_fragmented_headers("## Overview\n") == 0
    assert hs.count_fragmented_headers("## Overview\n\n") == 0


def test_fragmented_header_fires_at_end_of_file():
    """A restating stub with nothing after it is still a restating stub."""
    assert hs.count_fragmented_headers("## Overview\n\nOverview of the system.\n") == 1


# ---- #41 polysyndetic tripleting ----------------------------------------------


def test_tripleting_fires_on_three_triplets():
    para = (
        "We shipped, tested, and documented it. It was fast, clean, and small. "
        "The code, the tests, and the docs all landed."
    )
    assert hs.count_polysyndetic_tripleting(para) == 1


def test_tripleting_ignores_two_triplets():
    para = "It was fast, clean, and small. The code, the tests, and the docs landed."
    assert hs.count_polysyndetic_tripleting(para) == 0


def test_tripleting_matches_items_carrying_determiners():
    """Regression: the old regex allowed only bare single words.

    "The code, the tests, and the docs" -- an entirely ordinary triplet -- did
    not match, so the counter missed most real occurrences in prose.
    """
    para = "The code, the tests, and the docs. Their aims, their scope, and their limits. Its form, its tone, and its length."
    assert hs.count_polysyndetic_tripleting(para) == 1


def test_tripleting_ignores_parenthetical_adverbs():
    """Regression: comma-delimited asides have identical surface punctuation.

    "it was done, however, and then we moved on" is an aside, not a triplet.
    """
    para = (
        "It was done, however, and then we moved on. Later, though, and again, we "
        "revisited it. Finally, yes, and done."
    )
    assert hs.count_polysyndetic_tripleting(para) == 0


def test_tripleting_accepts_the_non_oxford_form():
    para = "We shipped, tested and documented it. It was fast, clean and small. The code, the tests and the docs landed."
    assert hs.count_polysyndetic_tripleting(para) == 1


def test_tripleting_is_per_paragraph():
    spread = "We shipped, tested, and documented it.\n\nIt was fast, clean, and small.\n\nThe code, the tests, and the docs landed."
    assert hs.count_polysyndetic_tripleting(spread) == 0


def test_tripleting_does_not_match_a_clause_after_a_comma():
    """ "the code, then we shipped and moved on" is not a triplet."""
    para = "We wrote the code, then we shipped and moved on. " * 3
    assert hs.count_polysyndetic_tripleting(para) == 0


# ---- shared helper ------------------------------------------------------------


def test_content_words_drops_short_words_and_singularises():
    assert hs._content_words("Overview of the Systems") == {"overview", "system"}
    assert hs._content_words("a an the of on") == set()


def test_fragmented_header_skips_a_heading_with_no_content_words():
    """A heading of only short words gives the overlap test nothing to work with.

    "## The Big Cat" has no word over three characters, so heading_words is empty
    and the ratio would divide by zero. It skips instead of firing.
    """
    assert hs.count_fragmented_headers("## The Big Cat\n\nThe big cat sat.\n") == 0


def test_fragmented_header_residual_false_positives():
    """KNOWN LIMITATION. The overlap test is lexical, so it cannot tell a
    restatement from a link or a structured catalogue entry that happens to
    reuse the heading's words.

    Both shapes below still fire. They are a large improvement on the previous
    behaviour -- which fired on every hard-wrapped paragraph in the repository,
    11 times in README.md alone -- but they are not clean. Tightening further
    means guessing at more markdown heuristics without a corpus to check
    against, which is the pseudo-precision this project flags as pattern #43.
    """
    assert hs.count_fragmented_headers("## License\n\nMIT — see [LICENSE](LICENSE).\n") == 1
    assert hs.count_fragmented_headers("## Commit verbs\n\n**Problem:** Vague commit verbs.\n") == 1


def test_content_words_strips_only_one_trailing_s():
    """Regression: rstrip("s") stripped every trailing s, not the plural one.

    "boss" became "bo" and "class" became "cla" -- over-stripping, not
    singularising, and short tokens collide more easily.
    """
    assert hs._content_words("boss class analysis") == {"bos", "clas", "analysi"}
    assert hs._content_words("tests") == {"test"}
