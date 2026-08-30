"""humanize-anti-slop — AI-writing pattern detection for Claude Code.

Ships as the ``humanize_anti_slop`` package; see pyproject's wheel ``sources``
remap. The repo keeps the directory named ``scripts/`` because the skill, the
hook, and install.sh all reference ``scripts/humanize_score.py`` by path.
"""

__all__ = ["burstiness_check", "humanize_score"]
