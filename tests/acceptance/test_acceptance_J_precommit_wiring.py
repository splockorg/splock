"""J.3 — `.git/hooks/pre-commit` exists and dispatches the declared pre-commit hooks.

Per inventory:
- Source: §10 §7.4 (Phase-4 carry-forward `bin/install-precommit-hooks`) +
  Opus filesystem-inspection finding that `.git/hooks/` is empty.
- Expected outcome: `.git/hooks/pre-commit` exists and references each
  pre-commit-declared hook script.

**This test is expected to fail today** until `bin/install-precommit-hooks`
ships + is run. Block K.7 watches the script existence; this one watches
the wiring.
"""

from __future__ import annotations

import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


# Scripts that ship with pre-commit semantics per quickstart Hooks table +
# implplan §G.impl.13 settings.json registration.
PRECOMMIT_HOOKS = [
    "hooks/marker-validate-pre-commit.sh",
    "hooks/claude-md-discipline.sh",
    "hooks/escalation-trigger-precommit.sh",
    "hooks/eval-gate-pre-commit.sh",
]


@pytest.mark.xfail(
    reason="bin/install-precommit-hooks not yet shipped per §10 §7.4 + Block K.7",
    strict=False,
)
def test_git_precommit_dispatches_declared_hooks(repo_root):
    """J.3: .git/hooks/pre-commit exists and references pre-commit hook scripts."""
    precommit = repo_root / ".git" / "hooks" / "pre-commit"
    assert precommit.exists(), (
        f".git/hooks/pre-commit missing — bin/install-precommit-hooks not run. "
        f"Pre-commit hooks ({len(PRECOMMIT_HOOKS)} scripts) never fire in this clone."
    )

    text = precommit.read_text(encoding="utf-8")
    unreferenced = [h for h in PRECOMMIT_HOOKS if h not in text]
    assert not unreferenced, (
        "Pre-commit hook script not referenced in .git/hooks/pre-commit:\n"
        + "\n".join(f"  - {h}" for h in unreferenced)
    )
