"""I.2 — Subagent / slash-command separation contract.

v2 (2026-05-24) — updated for the dual-purpose design that shipped under
the splock v2.7 §1.C extension. The original "recon is
subagent-only" assertion no longer holds: `recon`, `qa`, `qna`, and
`research` are now dual-purpose roles, available BOTH as subagents
(Agent-dispatched, in `.claude/agents/`) AND as operator-facing slash
commands (`/recon`, `/qa`, `/qna`, `/research`, in `commands/`).

The pure-subagent roles (`coder`, `reviewer`, `verifier`, `planner`)
remain Agent-dispatched only and MUST NOT have a corresponding
slash-command file.

This test enforces:
  - pure-subagent roles have no commands/<name>.md
  - the documented standard slash commands (/plan, /implplan,
    /develop-plan) DO exist
  - recon.md exists in .claude/agents/ (the subagent surface remains
    even when /recon also exists as a slash command)
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


# Subagent roles that MUST NOT have a slash-command file under the same
# name — these subagent role-names are invoked only via the Agent
# dispatcher. Operator-facing slash commands that *trigger* these
# subagents use distinct verb-form names (e.g., `/code` invokes the
# `coder` subagent; `/review` invokes the `reviewer` subagent), so the
# noun-form role-name files never appear under commands/.
PURE_SUBAGENT_NAMES = ["coder", "reviewer", "verifier", "planner"]

# Slash commands that MUST exist as files (project standard slash-command set).
EXPECTED_SLASH_COMMANDS = ["plan", "implplan", "develop-plan"]

# Dual-purpose roles: BOTH .claude/agents/<name>.md AND
# commands/<name>.md are allowed. Each is BOTH a subagent
# (Agent-dispatched) AND a slash command (operator-typed).
DUAL_PURPOSE_NAMES = ["recon", "qa", "qna", "research"]


def test_subagent_names_have_no_slash_command_file(repo_root):
    """I.2a: pure-subagent roles + reserved tokens MUST NOT have slash-command files."""
    commands_dir = repo_root / "commands"
    if not commands_dir.is_dir():
        pytest.skip("commands/ directory missing")

    unexpected = []
    for name in PURE_SUBAGENT_NAMES:
        cmd_path = commands_dir / f"{name}.md"
        if cmd_path.exists():
            unexpected.append(name)
    assert not unexpected, (
        f"Pure-subagent role-names should NOT have slash-command files; "
        f"found: {unexpected}. (Dual-purpose roles {DUAL_PURPOSE_NAMES} "
        f"are permitted to have both.)"
    )


def test_expected_slash_commands_exist(repo_root):
    """I.2b: the documented standard slash commands are present."""
    commands_dir = repo_root / "commands"
    missing = [n for n in EXPECTED_SLASH_COMMANDS
               if not (commands_dir / f"{n}.md").exists()]
    assert not missing, f"Expected slash commands missing: {missing}"


def test_recon_exists_as_subagent(repo_root):
    """I.2c: recon.md exists in .claude/agents/ regardless of whether the
    dual-purpose slash-command also exists at commands/recon.md."""
    agents_recon = repo_root / "agents" / "recon.md"
    assert agents_recon.exists(), "recon.md missing from .claude/agents/"
