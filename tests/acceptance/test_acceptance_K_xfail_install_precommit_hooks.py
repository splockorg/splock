"""K.7 (xfail) — `bin/install-precommit-hooks` script exists.

Per inventory + §10 §7.4 (Phase 4 carry-forward):
the script that wires `.git/hooks/pre-commit` to the pre-commit hook
scripts under `hooks/*-pre-commit.sh` was never shipped. Without
it, the 4 pre-commit hooks (marker-validate / claude-md-discipline /
escalation-trigger / eval-gate) never fire.

Companion to J.3 (`.git/hooks/pre-commit` wiring): K.7 watches the
installer script existence; J.3 watches the wiring result.
"""

from __future__ import annotations

import os
import pytest


pytestmark = pytest.mark.acceptance


@pytest.mark.xfail(
    reason="bin/install-precommit-hooks not shipped per §10 §7.4 Phase 4 carry-forward",
    strict=False,
)
def test_install_precommit_hooks_script_exists_and_executable(repo_root):
    """K.7: bin/install-precommit-hooks exists + is executable."""
    script = repo_root / "bin" / "install-precommit-hooks"
    assert script.exists(), (
        "bin/install-precommit-hooks missing — Phase 4 carry-forward pending"
    )
    assert os.access(script, os.X_OK), (
        "bin/install-precommit-hooks not executable — `chmod +x` it or ship the bit"
    )
