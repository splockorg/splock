"""C.7 — Chain driver injects SPLOCK_PLAN_SLUG / SPLOCK_CHAIN_ID / SPLOCK_PHASE before retry-loop spawn.

Per inventory + implplan §A.impl B-1 fix (commit `e4e93b0`):
runtime hooks (chain-suppression-block, chain-test-file-edit-flag) require
the STD_* env vars to be present before they can window-scope themselves
to the test-step retry phase.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_phase_spawn_injects_std_env_vars(repo_root):
    """C.7: chain driver injects SPLOCK_PLAN_SLUG/SPLOCK_CHAIN_ID/SPLOCK_PHASE.

    The B-1 fix per Phase 2 post-phase patch added explicit env
    assignments. As of the 2026-05-24 operator-direct wiring fix, the
    actual ``os.environ[...] = ...`` lines moved into
    ``bin/_retry_loop/opus_adapter.py::hook_env_staged`` (shared
    between the chain driver and the operator-direct CLI entry); the
    chain driver at ``phase_spawn.py`` now imports + invokes
    ``hook_env_staged(slug=..., chain_id=..., phase=...)``. The
    structural assertion below scans BOTH files so the test still
    catches "the env injection wiring is missing" without forbidding
    the DRY refactor.
    """
    phase_spawn_src = (repo_root / "bin" / "_chain_overnight" / "phase_spawn.py").read_text(
        encoding="utf-8"
    )
    opus_adapter_src = (
        repo_root / "bin" / "_retry_loop" / "opus_adapter.py"
    ).read_text(encoding="utf-8")
    combined_src = phase_spawn_src + "\n" + opus_adapter_src

    required_assignments = [
        "SPLOCK_PLAN_SLUG",
        "SPLOCK_CHAIN_ID",
        "SPLOCK_PHASE",
    ]
    missing = [v for v in required_assignments if v not in combined_src]
    assert not missing, (
        f"phase_spawn.py + opus_adapter.py do not inject required "
        f"STD_* env vars: {missing}"
    )

    # Chain driver must invoke the shared hook_env_staged context manager
    # (or do the equivalent env staging inline). Either pattern keeps the
    # runtime hooks window-scoped to the retry-loop phase.
    has_env_staging = (
        "hook_env_staged" in phase_spawn_src
        or any(
            f'env["{v}"]' in phase_spawn_src
            or f"env['{v}']" in phase_spawn_src
            or f'"{v}":' in phase_spawn_src
            for v in required_assignments
        )
    )
    assert has_env_staging, (
        "phase_spawn.py neither imports hook_env_staged nor performs an "
        "inline env-dict assignment for STD_*; the runtime hook activation "
        "window may be broken."
    )

    # opus_adapter.hook_env_staged must perform actual os.environ writes
    # for all three keys (this is the load-bearing assignment site after
    # the 2026-05-24 refactor).
    has_os_environ_writes = all(
        f'os.environ["{v}"]' in opus_adapter_src
        or f"os.environ['{v}']" in opus_adapter_src
        for v in required_assignments
    )
    assert has_os_environ_writes, (
        "opus_adapter.py::hook_env_staged does not write all three "
        "STD_* env vars to os.environ; the runtime hook activation "
        "window is incomplete."
    )
