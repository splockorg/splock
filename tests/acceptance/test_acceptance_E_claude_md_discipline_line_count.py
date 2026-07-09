"""E.10 — `claude-md-discipline.sh` refuses when root CLAUDE.md exceeds 200 lines.

Pre-commit-fixture test (per Sonnet M-6).
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


@pytest.mark.skip(
    reason=(
        "claude-md-discipline.sh resolves REPO_ROOT from its script location "
        "(real repo) and runs `git diff --cached` against the test cwd. The "
        "real CLAUDE.md (199 lines) is what gets inspected, not the staged "
        "tmp_repo content. Needs a fixture that bind-mounts the real "
        "hooks/ into the tmp git repo OR a real `git commit -m` "
        "invocation; track as Pass 4 fixture enhancement (companion to "
        "the E.11 + E.9 fixture limitations)."
    )
)
def test_claude_md_over_200_lines_refused(precommit_repo, repo_root):
    """E.10: a 250-line CLAUDE.md in staged diff is refused by the hook."""
    hook = repo_root / "hooks" / "claude-md-discipline.sh"
    if not hook.exists():
        pytest.skip("claude-md-discipline.sh missing")

    # Generate a 250-line CLAUDE.md.
    big_text = "# CLAUDE.md\n" + "\n".join(
        f"Line {i} — some content here" for i in range(1, 251)
    )
    precommit_repo.stage("CLAUDE.md", big_text)

    result = precommit_repo.run_hook(hook)
    # Hook may not find bin/hook-log in tmp_path; skip-with-finding if so.
    if "command not found" in result.stderr.lower() or "bin/hook-log" in result.stderr:
        pytest.skip(
            f"Hook requires bin/hook-log on PATH (Pass 4 fixture work). "
            f"stderr={result.stderr!r}"
        )

    # Hook should exit non-zero to refuse the commit.
    refused = (
        result.returncode != 0 or
        "deny" in result.stdout.lower() or
        "refus" in result.stdout.lower() or
        "200" in result.stdout
    )
    assert refused, (
        f"claude-md-discipline.sh did not refuse 250-line CLAUDE.md\n"
        f"rc={result.returncode}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
