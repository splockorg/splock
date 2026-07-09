"""H.2 — `bin/hook-lint` passes against shipped `hooks/` directory."""

from __future__ import annotations

import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


def test_hook_lint_clean_against_shipped_hooks(repo_root):
    """H.2: hook-lint emits zero violations against the live hooks directory."""
    from bin._hooks import hook_lint

    hook_dir = repo_root / "hooks"
    hooks = hook_lint.list_hooks(hook_dir)
    assert hooks, "No hook scripts found"

    all_violations: list = []
    for hook_path in hooks:
        for check_fn_name in (
            "check_naming_kebab",
            "check_hook_log_call",
            "check_stop_hook_active",
            "check_posttool_no_deny",
        ):
            check_fn = getattr(hook_lint, check_fn_name, None)
            if check_fn is None:
                continue
            try:
                v = check_fn(hook_path)
            except Exception as exc:  # noqa: BLE001
                all_violations.append((hook_path.name, check_fn_name,
                                       f"check raised: {type(exc).__name__}: {exc}"))
                continue
            if v is not None:
                all_violations.append((hook_path.name, check_fn_name, repr(v)))

    if all_violations:
        msg = "\n".join(f"  {h}: {fn} → {v}" for h, fn, v in all_violations)
        # This is a substrate finding — surface it if real.
        pytest.fail(
            f"hook-lint violations against shipped hooks/:\n{msg}"
        )
