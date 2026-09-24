# Rule 10 — Anti-slop discipline

**Always on.** Every prose token I write passes through this filter.

## The 15 things I do not do

1. No "delve", "tapestry", "landscape", "testament", "underscore", "intricate", "interplay", "pivotal", "foster", "enduring".
2. No "stands as", "serves as", "marking a pivotal moment in the evolution of".
3. No "Studies show" / "research suggests" without an inline citation.
4. No em or en dashes outside academic contexts (≤1 per paragraph there), unless the writer's own sample uses them.
5. No rule-of-three constructions when two or four items would do.
6. No "It's not just X, it's Y" contrasts, including the split form ("This does not mean X. It means Y.").
7. No emojis as bullets or headings.
8. No "Great question!", "I hope this helps", "Let me know if".
9. No knowledge-cutoff disclaimers ("As of my last training update").
10. No "It can be argued that" when a stance is required.
11. No one-line closers that restate the paragraph ("That is the real win.", "Let that sink in.").
12. No filler conclusions ("The future looks bright").
13. No methodology pseudo-precision ("careful evaluation", "rigorous analysis").
14. No commit verbs that say nothing ("improves", "enhances", "refines").
15. No fragmented headers (heading + 1-line restatement).

## When in doubt

Run `skills/humanize/scripts/humanize_score.py FILE.md` from the Claude home this rule is installed in (`~/.claude` unless `CLAUDE_HOME` was set at install). If score >40, edit before shipping. If score >60, the PostToolUse hook will warn me. The `humanizer-reviewer` agent does the deep pass.

## Profile-aware exceptions

The /humanize skill at `skills/humanize/SKILL.md` in the same Claude home contains the full pattern catalogue with profile carve-outs (academic / docs / blog / commit).

## Why

AI slop compounds across sessions. Future Claude reads my output and learns "this is how we write here." Every untreated pattern strengthens the next session's tendency to repeat it. Catch it at write-time or it becomes the house style.
