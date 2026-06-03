"""Layout / import smoke test.

Proves the package layout is internally consistent: for every shipped
``bin/`` wrapper, the ``python -m bin._<pkg>.<mod>`` module target it execs
MUST be present/resolvable in the tree, and every dependency-free package
MUST import cleanly.

Two modules (``bin/_intent`` and ``bin/_chain_resume``) are PRESENCE-checked
(the module file resolves) rather than imported, because they may pull in
optional adapters at import time; they are not stubbed here.

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Every shipped wrapper -> the `python -m <module>` target it execs.
# Shell-only wrappers (chain-overnight-release-lock, install-merge-drivers) that
# alias another wrapper instead of exec'ing python are intentionally omitted.
WRAPPER_MODULE_TARGETS: dict[str, str] = {
    "build_briefing": "bin._retry_loop.main",
    "chain-overnight": "bin._chain_overnight.main",
    "chain-pause": "bin._chain_pause.main",
    "chain-resume": "bin._chain_resume.main",
    "cli-lint": "bin._cli_lint.main",
    "develop-plan-bypass-status": "bin._update_orchestrator.bypass_status",
    "eval-baseline": "bin._eval_baseline.main",
    "eval-gate": "bin._eval_gate.main",
    "eval-trend": "bin._eval_trend.main",
    "git-merge-jsonl": "bin._git_merge_jsonl.main",
    "hook-lint": "bin._hooks.hook_lint",
    "hook-log": "bin._hooks.main",
    "implplan": "bin._planner.main",
    "intent": "bin._intent.main",
    "lazy-dump-check": "bin._route_issue.lazy_dump_check_cli",
    "marker": "bin._marker.main",
    "migrate_plan": "bin._render_plan.migrate",
    "morning-review": "bin._morning_review.main",
    "orchestrator-next-ready": "bin._orchestrator_query.main",
    "plan": "bin._planner.main",
    "regression-replay": "bin._regression_replay.main",
    "render_log": "bin._render_log.main",
    "render_status_tree": "bin._render_status_tree.main",
    "route_issue": "bin._route_issue.main",
    "sealed-rm": "bin._sealed_rm.main",
    "update_orchestrator": "bin._update_orchestrator.main",
    "verify": "bin._retry_loop.main",
    "verify_plan": "bin._render_plan.verify",
    "wrap": "bin._wrap.main",
    # hook-dispatch wrappers (.sh) that exec python -m:
    "security-dispatch.sh": "bin._hooks.security_dispatch",
    "plan-render-on-edit.sh": "bin._hooks.plan_render_on_edit",
}

# Targets whose package may pull in optional adapters at import time; here we
# only assert the module FILE resolves rather than importing it.
HOST_COUPLED_PRESENCE_ONLY: set[str] = {
    "bin._intent.main",
    "bin._chain_resume.main",
}


def test_all_wrapper_targets_exist_in_bin() -> None:
    """Every wrapper named in the map is actually shipped in bin/."""
    bin_dir = REPO_ROOT / "bin"
    missing = [w for w in WRAPPER_MODULE_TARGETS if not (bin_dir / w).exists()]
    assert not missing, f"wrappers referenced by smoke test but absent from bin/: {missing}"


@pytest.mark.parametrize("wrapper,module", sorted(WRAPPER_MODULE_TARGETS.items()))
def test_wrapper_module_target_resolvable(wrapper: str, module: str) -> None:
    """The module each wrapper execs via `python -m` resolves in the copied tree.

    ``find_spec`` proves the module file is present and importable-by-path
    WITHOUT executing it. (Executing modules that pull in optional adapters
    may fail without those adapters; that is out of scope here.)
    """
    spec = importlib.util.find_spec(module)
    assert spec is not None, (
        f"wrapper {wrapper!r} execs `python -m {module}` but that module does "
        f"not resolve in the copied tree (missing package / missing transitive "
        f"intra-bin dependency)"
    )


def test_host_dep_free_targets_actually_import() -> None:
    """Dependency-FREE wrapper targets import cleanly.

    Excludes the two presence-only targets. Importing the rest proves the
    transitive intra-bin closure (e.g. _jsonl_log, _orchestrator_query,
    _eval_baseline, _chain_pause, _verify_plan, _render_status_tree) is present.
    """
    import_failures: dict[str, str] = {}
    for module in sorted(set(WRAPPER_MODULE_TARGETS.values())):
        if module in HOST_COUPLED_PRESENCE_ONLY:
            continue
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - surface every failure together
            import_failures[module] = f"{type(exc).__name__}: {exc}"
    assert not import_failures, (
        "host-dep-free wrapper targets failed to import (indicates a dropped "
        f"transitive intra-bin dependency): {import_failures}"
    )
