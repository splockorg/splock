"""Shared SDK-spawner adapter factory for the §F.3 test-step retry loop.

Per Tier-1 operator-direct wiring fix (2026-05-24). Previously the three
adapters (``_opus_adapter``, ``_verify_adapter``, ``_reviewer_adapter``)
that translate ``iteration_loop``'s call shape into the SDK spawners'
call shape lived as closures inside
``bin/_chain_overnight/phase_spawn.spawn_retry_loop_phase`` —
they captured ``slug`` / ``chain_id`` / ``phase`` / ``repo_root_path``
from the enclosing scope and were never importable from any other
call site.

That worked fine when the only caller was the chain-overnight driver,
but the operator-direct path (``bin/verify test-step <slug> --chain-id
manual_*`` → ``bin/_retry_loop/main.py::_run_test_step`` →
``iteration_loop.run_test_step_loop``) had no way to inject the same
adapters. Without injection, the iteration loop falls through to the
``_default_spawn_opus`` placeholder and raises ``NotImplementedError``
— exit code 2 (driver_crash).

This module is the shared seam. Both call sites (chain-driver +
operator-direct) call ``build_adapters(...)`` to get the trio, and
``hook_env_staged(...)`` to stage / restore the
``SPLOCK_PLAN_SLUG`` / ``SPLOCK_CHAIN_ID`` / ``SPLOCK_PHASE`` env vars.

Cross-references
----------------

- ``bin/_chain_overnight/phase_spawn.py::spawn_retry_loop_phase`` —
  chain-driver caller. Used to define these adapters inline.
- ``bin/_retry_loop/main.py::_run_test_step`` — operator-direct CLI
  caller. Now imports + uses this factory.
- ``bin/_retry_loop/sdk_spawners.py`` — the underlying SDK spawners
  (``spawn_opus_via_sdk`` / ``run_verify_subprocess`` /
  ``spawn_reviewer_via_sdk``) that the adapters delegate to.
- ``bin/_retry_loop/iteration_loop.py::run_iteration`` — consumer that
  calls each adapter with ``plan_dir`` / ``slug`` / ``chain_id`` /
  ``iteration_n`` / ``prior_diagnosis`` (opus) or ``prompt`` /
  ``rubric_kind`` (reviewer) or ``plan_dir`` / ``iteration_n``
  (verify).
"""

from __future__ import annotations

import contextlib
import os
import pathlib

from bin._env_paths import project_root
import subprocess
from typing import Any, Callable, Iterator


# Tuple of hook env-var names the SDK spawners read from os.environ to
# propagate into the spawned CLI subprocess via ``ClaudeAgentOptions.env``.
# Centralized so both call sites stage / restore the same set.
HOOK_ENV_VAR_NAMES = ("SPLOCK_PLAN_SLUG", "SPLOCK_CHAIN_ID", "SPLOCK_PHASE")


def _repo_root() -> pathlib.Path:
    """Adopter-repo root via the env-contract resolver.

    This is the working directory bound into the spawned SDK sessions —
    the coder edits ADOPTER files and the reviewer reads ADOPTER tests and
    diffs, so binding the plugin install tree (the historical ``parents[2]``
    derivation) would point both agents at the wrong repo in
    installed-plugin mode. Sideloaded / in-tree checkouts resolve
    identically to before.
    """
    return project_root()


@contextlib.contextmanager
def hook_env_staged(
    *,
    slug: str,
    chain_id: str,
    phase: int,
) -> Iterator[None]:
    """Context manager: stage the §G hook env vars; restore on exit.

    Per Phase 2 post-phase B-1 fix (now lifted out of phase_spawn.py
    so the operator-direct path can share it): the SDK spawners
    (``spawn_opus_via_sdk`` / ``spawn_reviewer_via_sdk``) read
    ``SPLOCK_PLAN_SLUG`` / ``SPLOCK_CHAIN_ID`` / ``SPLOCK_PHASE`` from
    ``os.environ`` and propagate them into the spawned CLI subprocess
    via ``ClaudeAgentOptions.env``. The §G runtime hooks
    (``chain-suppression-block.sh`` + ``chain-test-file-edit-flag.sh``)
    activate during the test-step retry window when these env vars are
    visible.

    Without restoration on exit, a phase 4 → phase 5 → phase-6 boundary
    review chain (or a subsequent operator-direct invocation in the
    same process) would carry leaked env values across phases / calls.

    The chain-driver call site uses phase=4 or phase=5; the
    operator-direct call site uses phase=5 (the test-step retry loop
    is structurally a phase=5 invocation regardless of who's calling).
    """
    prior_env = {k: os.environ.get(k) for k in HOOK_ENV_VAR_NAMES}

    os.environ["SPLOCK_PLAN_SLUG"] = slug
    os.environ["SPLOCK_CHAIN_ID"] = chain_id
    os.environ["SPLOCK_PHASE"] = str(phase)
    try:
        yield
    finally:
        for k, v in prior_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def build_adapters(
    *,
    slug: str,
    chain_id: str,
    phase: int,
    repo_root_path: pathlib.Path | None = None,
) -> tuple[
    Callable[..., dict],
    Callable[..., subprocess.CompletedProcess],
    Callable[..., dict],
]:
    """Build the three SDK-spawner adapters for a given (slug, chain_id, phase).

    Returns ``(opus_adapter, verify_adapter, reviewer_adapter)`` —
    three callables that match the call shapes ``iteration_loop`` uses
    (``plan_dir`` / ``slug`` / ``chain_id`` / ``iteration_n`` /
    ``prior_diagnosis`` for opus; ``plan_dir`` / ``iteration_n`` for
    verify; ``plan_dir`` / ``prompt`` / ``rubric_kind`` for reviewer)
    and translate them to the SDK spawners' call shapes (``prompt`` /
    ``cwd`` / ``hook_env``).

    The adapters own:

    - Coder briefing construction via
      ``bin._retry_loop.briefing.build_coder_briefing``.
    - Orchestrator-path derivation
      (``plan_dir / <slug>_orchestrator.json``).
    - ``cwd`` resolution (repo root — where ``.claude/agents/`` lives).
    - ``hook_env`` passthrough — None on the opus path so the SDK
      spawner reads ``os.environ`` directly (staged via
      ``hook_env_staged``); explicit dict on the reviewer path so the
      env values flow even if the spawner's defaults change.

    The ``rubric_kind`` kwarg passed to the reviewer adapter selects the
    reviewer's bound output schema and is forwarded to
    ``spawn_reviewer_via_sdk`` (which defaults it to ``"test_step"``).
    ``iteration_loop`` passes ``rubric_kind="test_step"``; the
    phase-boundary gate passes ``"plan_to_implplan"`` /
    ``"implplan_to_code"``, whose rubrics carry the load-bearing
    ``terminal_shape`` field. It was previously ACCEPTED-BUT-IGNORED
    (``spawner_signature_coordination`` §6.1) — which crashed every
    operator-direct ``bin/verify boundary`` run (``terminal_shape=None``);
    the schema is now resolved via ``rubric.resolve_schema(rubric_kind)``.

    Parameters
    ----------
    slug : str
        Plan slug — captured by the adapters for use in briefing
        construction + reviewer hook env.
    chain_id : str
        Chain id — captured by the adapters for reviewer hook env.
    phase : int
        Phase index (4 or 5 for chain-driver use; 5 for operator-
        direct use). Captured for reviewer hook env's
        ``SPLOCK_PHASE`` value.
    repo_root_path : pathlib.Path | None
        Optional override for the repo root. Defaults to the parent of
        the parent of this file (the canonical repo-root resolution).
        Tests inject this so they can route SDK calls into a tmp tree.

    Returns
    -------
    tuple of three callables
        ``(opus_adapter, verify_adapter, reviewer_adapter)``.
    """
    # Lazy-import SDK spawners + briefing builder so this module
    # imports cleanly when claude_agent_sdk is not installed. The
    # adapters themselves only reference the SDK at call time, but the
    # imports below have to be evaluated SOMEWHERE — doing it inside
    # the factory matches the lazy-import discipline the SDK spawners
    # already enforce.
    from bin._retry_loop.briefing import build_coder_briefing
    from bin._retry_loop.sdk_spawners import (
        run_verify_subprocess,
        spawn_opus_via_sdk,
        spawn_reviewer_via_sdk,
    )

    if repo_root_path is None:
        repo_root_path = _repo_root()
    # Bind to a stable name the closures can capture. Don't shadow the
    # parameter name — closures should see the resolved Path, not the
    # default-None.
    bound_repo_root: pathlib.Path = repo_root_path

    def _opus_adapter(
        *,
        plan_dir: pathlib.Path,
        slug: str,
        chain_id: str,
        iteration_n: int,
        prior_diagnosis: dict | None,
        inject_text: str | None = None,
    ) -> dict:
        prompt = build_coder_briefing(
            slug=slug,
            plan_dir=plan_dir,
            iteration_n=iteration_n,
            prior_diagnosis=prior_diagnosis,
            chain_id=chain_id,
        )
        if inject_text is not None:
            prompt = inject_text + "\n\n" + prompt
        return spawn_opus_via_sdk(
            prompt=prompt,
            cwd=bound_repo_root,
        )

    def _verify_adapter(
        *,
        plan_dir: pathlib.Path,
        iteration_n: int,
    ) -> subprocess.CompletedProcess:
        orchestrator_path = plan_dir / f"{slug}_orchestrator.json"
        return run_verify_subprocess(
            slug=slug,
            plan_dir=plan_dir,
            orchestrator_path=orchestrator_path,
            iteration_n=iteration_n,
        )

    def _reviewer_adapter(
        *,
        plan_dir: pathlib.Path,
        prompt: str,
        rubric_kind: str,
    ) -> dict:
        # rubric_kind selects the reviewer's bound output schema: test_step
        # vs the phase-boundary rubrics (plan_to_implplan / implplan_to_code),
        # which carry a terminal_shape field. Previously ignored — that broke
        # every operator-direct `bin/verify boundary` run (terminal_shape=None
        # → exit 2). spawn_reviewer_via_sdk defaults to "test_step".
        return spawn_reviewer_via_sdk(
            prompt=prompt,
            cwd=bound_repo_root,
            rubric_kind=rubric_kind,
            hook_env={
                "SPLOCK_PLAN_SLUG": os.environ.get("SPLOCK_PLAN_SLUG", slug),
                "SPLOCK_CHAIN_ID": os.environ.get("SPLOCK_CHAIN_ID", chain_id),
                "SPLOCK_PHASE": os.environ.get("SPLOCK_PHASE", str(phase)),
            },
        )

    return _opus_adapter, _verify_adapter, _reviewer_adapter


__all__ = [
    "HOOK_ENV_VAR_NAMES",
    "build_adapters",
    "hook_env_staged",
]
