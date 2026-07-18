"""CLI entry for `bin/verify` and `bin/build_briefing`.

Per splock implplan §F.impl.2 file tree: this module backs
both shell wrappers + the chain driver's `subprocess.Popen([...,
"-m", "bin._retry_loop.main", ...])` dispatch.

Subcommands
-----------

- ``test-step <slug> --chain-id <id>`` — run the bounded test-step
  retry loop (§F.3 + §F.impl.3). Spawns Opus + bin/verify + reviewer
  iterations bounded by the unified retry counter; emits transition
  rows + morning-review entry on halt. Exit codes per
  `exit_codes.py`.

- ``boundary <slug> --boundary <plan_to_implplan|implplan_to_code>``
  — run the runtime §F.9 phase-boundary review-gate loop
  (`phase_boundary_review.run_boundary_review`). Returns READY (exit
  0), NEEDS_REVISION (exit 0 — controlled by chain driver loop), or
  HALT (exit 10 / `EXIT_PHASE_BOUNDARY_HALT`).

- ``build-briefing <slug> --rubric-kind <kind> [--iteration N] [--out
  PATH]`` — deterministically construct the runtime reviewer prompt
  (`briefing.build_briefing`) and write to stdout or `--out`. Anchor
  §4a.3 element 3: the briefing is built EXCLUSIVELY from CLI-passed
  data and on-disk artifacts; NEVER from agent narrative. This is the
  surface that `bin/build_briefing` POSIX wrapper invokes.

- ``junction <slug> --junction <J-id>`` — junction-time collect-check
  (real_tests_at_junctions SC4): consolidates the junction's covering
  set (SC6 ``covers[]`` / default), classifies every covered
  ``tests_enabled`` entry via the ``pytest --collect-only`` oracle
  (typed gate commands recognized), prints the structured verdict as
  JSON. ``test_gate`` junctions ONLY. Exit 0 = advance-ok; exit 10
  (`EXIT_PHASE_BOUNDARY_HALT`) = refuse advance (empty union or
  non-collectable entries); exit 38
  (`EXIT_JUNCTION_KIND_NOT_APPLICABLE`) = the junction is a
  ``review_gate``/``phase_boundary``, which clears by operator action,
  not test collection (issue #39's fail-open).

POSIX wrapper compatibility
---------------------------

The shell wrappers ``bin/verify`` and ``bin/build_briefing`` activate
the project venv then exec ``python -m bin._retry_loop.main ...``.

Phase-spawn integration (§A.impl.9)
-----------------------------------

The chain driver `phase_spawn.spawn_retry_loop_phase` passes
``--phase 4`` or ``--phase 5`` plus ``--chain-id``. The CLI maps
phase=5 to the ``test-step`` subcommand; phase=4 is currently a
no-op pass (the `/code` step doesn't iterate in v2.7; iteration is
test-driven).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from . import (
    briefing as briefing_mod,
    exit_codes,
    halt_handoff,
    iteration_loop,
    phase_boundary_review,
    rubric as rubric_mod,
)

logger = logging.getLogger(__name__)

from bin._env_paths import plans_dir as _env_paths_plans_dir
from bin._fleet import auto as fleet_auto

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLANS_DIR = _env_paths_plans_dir()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bin/verify",
        description=(
            "splock §F runtime — test-step retry loop + "
            "phase-boundary review gates. Used by `bin/chain-overnight` "
            "as subprocess; also dispatchable directly for testing."
        ),
    )

    sub = p.add_subparsers(dest="subcommand", required=False)

    # test-step subcommand — explicit invocation.
    sp_test = sub.add_parser(
        "test-step", help="Run the bounded test-step retry loop (§F.3)."
    )
    sp_test.add_argument("slug", help="Plan slug.")
    sp_test.add_argument(
        "--chain-id", required=True, help="Chain id for forensic logging."
    )
    sp_test.add_argument(
        "--max-retries", type=int, default=None,
        help="Override OVERNIGHT_TEST_MAX_RETRIES (range 1-5; default 3).",
    )

    # boundary subcommand — runtime §F.9.
    sp_boundary = sub.add_parser(
        "boundary",
        help="Run the runtime §F.9 phase-boundary review gate.",
    )
    sp_boundary.add_argument("slug", help="Plan slug.")
    sp_boundary.add_argument(
        "--chain-id", required=True, help="Chain id for forensic logging."
    )
    sp_boundary.add_argument(
        "--boundary",
        required=True,
        choices=["plan_to_implplan", "implplan_to_code"],
        help="Phase boundary being reviewed.",
    )

    # build-briefing subcommand — deterministic prompt construction.
    sp_brief = sub.add_parser(
        "build-briefing",
        help=(
            "Deterministically build the runtime reviewer prompt from "
            "CLI artifacts. NEVER agent-authored (anchor §4a.3 element 3)."
        ),
    )
    sp_brief.add_argument("slug", help="Plan slug.")
    sp_brief.add_argument(
        "--rubric-kind",
        required=True,
        choices=["test_step", "plan_to_implplan", "implplan_to_code"],
        help="Which rubric the briefing should be built for.",
    )
    sp_brief.add_argument(
        "--iteration", type=int, default=1,
        help="Iteration number (test-step) or reviewer round (boundary). Default 1.",
    )
    sp_brief.add_argument(
        "--out", type=Path, default=None,
        help="Output file. Default: stdout.",
    )

    # junction subcommand — real_tests_at_junctions SC4 junction-time hook.
    sp_junction = sub.add_parser(
        "junction",
        help=(
            "Collect-check a test_gate junction's covering set before "
            "advance (real_tests_at_junctions SC4): unions the covered "
            "tasks' tests_enabled, probes selectors via `pytest "
            "--collect-only` (typed gate_cmd: entries recognized, not "
            "probed), prints a JSON verdict. Exit 0 = advance-ok; exit 10 "
            "(EXIT_PHASE_BOUNDARY_HALT) = refuse advance (empty union or "
            "non-collectable entries); exit 38 "
            "(EXIT_JUNCTION_KIND_NOT_APPLICABLE) = junction is a "
            "review_gate/phase_boundary — clears by operator action, not "
            "test collection; exit 1 = usage (unknown slug/junction, "
            "malformed orchestrator)."
        ),
    )
    sp_junction.add_argument("slug", help="Plan slug.")
    sp_junction.add_argument(
        "--junction",
        required=True,
        help="Junction id from the orchestrator's junctions[] (e.g. "
             "J1_test_gate_...).",
    )

    return p


def _split_argv_for_phase_spawn(argv: list[str]) -> tuple[bool, dict[str, str]]:
    """Detect phase-spawn fallback invocation shape.

    `bin/_chain_overnight/phase_spawn.spawn_retry_loop_phase` invokes:
        python -m bin._retry_loop.main <slug> --phase N --chain-id ID

    The leading positional is NOT a subcommand name. This helper returns
    (True, {"slug": ..., "phase": ..., "chain_id": ...}) if the argv
    matches the phase-spawn shape, else (False, {}).
    """
    if not argv:
        return False, {}
    leading = argv[0]
    if leading in ("test-step", "boundary", "build-briefing", "junction"):
        return False, {}
    if leading.startswith("-"):
        return False, {}
    # First arg is a candidate slug; look for --phase + --chain-id.
    rest = argv[1:]
    out: dict[str, str] = {"slug": leading}
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--phase" and i + 1 < len(rest):
            out["phase"] = rest[i + 1]
            i += 2
        elif tok == "--chain-id" and i + 1 < len(rest):
            out["chain_id"] = rest[i + 1]
            i += 2
        else:
            return False, {}
    if "phase" not in out or "chain_id" not in out:
        return False, {}
    return True, out


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    # Phase-spawn fallback path — detect BEFORE argparse so the
    # subparser-style argv (`<slug> --phase N --chain-id ID`) doesn't
    # get misinterpreted as `<subcommand> ...`.
    is_phase_spawn, parsed = _split_argv_for_phase_spawn(argv)
    if is_phase_spawn:
        return _phase_spawn_dispatch_kwargs(
            slug=parsed["slug"],
            phase=int(parsed["phase"]),
            chain_id=parsed["chain_id"],
        )

    p = _build_parser()
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return exit_codes.EXIT_USAGE if exc.code != 0 else exit_codes.EXIT_OK

    if args.subcommand is None:
        p.error(
            "subcommand required (test-step | boundary | build-briefing | "
            "junction)"
        )
        return exit_codes.EXIT_USAGE

    if args.subcommand == "test-step":
        return _run_test_step_tracked(args)
    if args.subcommand == "boundary":
        return _run_boundary_tracked(args)
    if args.subcommand == "build-briefing":
        return _run_build_briefing(args)
    if args.subcommand == "junction":
        return _run_junction(args)
    p.error(f"unknown subcommand: {args.subcommand!r}")
    return exit_codes.EXIT_USAGE


# ----------------------------------------------------------------------
# Subcommand dispatchers
# ----------------------------------------------------------------------

def _resolve_plan_dir(slug: str) -> Path:
    plan_dir = _PLANS_DIR / slug
    if not plan_dir.exists() or not plan_dir.is_dir():
        _emit_stderr_json(
            {"error": "usage", "detail": f"plan dir does not exist: {plan_dir}"}
        )
        raise _UsageError(f"plan dir does not exist: {plan_dir}")
    return plan_dir


def _phase_spawn_dispatch_kwargs(
    *,
    slug: str,
    phase: int,
    chain_id: str,
) -> int:
    """Phase-spawn dispatch: `--phase 5` → test-step; `--phase 4` → no-op pass.

    Per §A.impl.9 + plan §F.3: phase 5 is `/test`, where iteration runs.
    Phase 4 (`/code`) does NOT iterate in v2.7; iteration is test-driven.
    """
    if phase == 5:
        ns = argparse.Namespace(
            subcommand="test-step",
            slug=slug,
            chain_id=chain_id,
            max_retries=None,
        )
        return _run_test_step_tracked(ns)
    if phase == 4:
        return exit_codes.EXIT_OK
    _emit_stderr_json(
        {"error": "usage", "detail": f"unsupported --phase value: {phase}"}
    )
    return exit_codes.EXIT_USAGE


def _run_test_step_tracked(args: argparse.Namespace) -> int:
    """`_run_test_step` + fleet auto-integration (docs/FLEET.md).

    Fleet hooks are silent no-ops unless the adopter project ran
    `bin/fleet init`, and never raise into the loop. Verdict-carrying
    halts (retry cap exhausted, HALT) flip the slug to `blocked`;
    infrastructure errors append an event without a status flip.
    """
    fleet_auto.stage_started(args.slug, "test", actor="retry-loop")
    rc = _run_test_step(args)
    if rc == exit_codes.EXIT_OK:
        fleet_auto.stage_finished(
            args.slug, "test", actor="retry-loop", note="test-step green"
        )
    elif rc in (exit_codes.EXIT_RETRY_EXCEEDED, exit_codes.EXIT_PHASE_BOUNDARY_HALT):
        fleet_auto.stage_blocked(
            args.slug, "test", actor="retry-loop",
            note=f"test-step halted (exit {rc})",
        )
    else:
        fleet_auto.stage_event(
            args.slug, "test", actor="retry-loop",
            note=f"test-step errored (exit {rc})",
        )
    return rc


def _run_boundary_tracked(args: argparse.Namespace) -> int:
    """`_run_boundary` + fleet auto-integration (docs/FLEET.md).

    The next action follows from the junction the review cleared
    (`plan_to_implplan` → /implplan, `implplan_to_code` → /code).
    """
    fleet_auto.stage_started(
        args.slug, "review", actor="retry-loop",
        note=f"{args.boundary} review started",
    )
    rc = _run_boundary(args)
    if rc == exit_codes.EXIT_OK:
        fleet_auto.stage_finished(
            args.slug, "review", actor="retry-loop",
            next_action=fleet_auto.BOUNDARY_NEXT.get(args.boundary),
            note=f"{args.boundary} review READY",
        )
    elif rc in (exit_codes.EXIT_RETRY_EXCEEDED, exit_codes.EXIT_PHASE_BOUNDARY_HALT):
        fleet_auto.stage_blocked(
            args.slug, "review", actor="retry-loop",
            note=f"{args.boundary} review halted (exit {rc})",
        )
    else:
        fleet_auto.stage_event(
            args.slug, "review", actor="retry-loop",
            note=f"{args.boundary} review errored (exit {rc})",
        )
    return rc


def _run_test_step(args: argparse.Namespace) -> int:
    """Run the bounded test-step retry loop and return an exit code.

    Operator-direct CLI entry: this is what ``bin/verify test-step
    <slug> --chain-id manual_*`` (and the ``/test`` slash command)
    eventually dispatch to. Per Tier-1 wiring fix (2026-05-24): the
    operator-direct path now injects the same SDK-spawner adapters
    that the chain-overnight driver injects via
    ``bin/_chain_overnight/phase_spawn.spawn_retry_loop_phase``.

    The previous behavior (no DI'd spawners → fall through to
    ``iteration_loop._default_spawn_opus`` placeholder →
    ``NotImplementedError`` → exit code 2 driver_crash) is gone.
    Both code paths now share ``bin._retry_loop.opus_adapter`` as the
    single source of truth for SDK wiring.

    Behavior summary
    ----------------

    1. Resolve the plan dir from ``args.slug``.
    2. Smoke-check the SDK (``smoke_check_sdk_available``). If it
       returns ``(False, msg)``, short-circuit to exit code 16
       (``EXIT_VERIFY_PLAN_REJECTED``-family) with the diagnostic
       surfaced via the stderr JSON envelope — mirrors the chain-
       driver's short-circuit behavior so the operator gets the same
       triage signal regardless of entry point.
    3. Stage hook env vars (``SPLOCK_PLAN_SLUG`` / ``SPLOCK_CHAIN_ID`` /
       ``SPLOCK_PHASE``) via ``opus_adapter.hook_env_staged`` — same
       staging that the chain driver does. Phase is hard-coded to 5
       because the test-step retry loop is structurally a phase=5
       invocation regardless of who's calling.
    4. Build the SDK spawner adapters via ``opus_adapter.build_adapters``
       and pass them as kwargs to ``run_test_step_loop``. Parity with
       chain-driver mode is the design default per the Tier-1 brief.
    5. Map the iteration-loop result to an exit code via the existing
       halt-handoff path (unchanged behavior — only the spawner-
       injection wiring is new).
    """
    try:
        plan_dir = _resolve_plan_dir(args.slug)
    except _UsageError:
        return exit_codes.EXIT_USAGE

    # Pre-flight: refuse fast if the slug's tests_enabled union has no
    # runnable pytest selectors (every entry is design-prose or names a
    # file not yet on disk). Without this guard the loop splats the prose
    # at pytest → exit 4 at collection → 3 Opus iterations of wheel-spin →
    # exit 17 + a spurious ~160 KB halt entry, burning the full retry
    # budget on a structurally-knowable refusal. Mirrors the SDK-smoke
    # short-circuit below; runs before any SDK load or coder spawn so no
    # budget is consumed. The helpers are SDK-free. See outstanding-issues
    # #11 / #12.
    from bin._retry_loop.sdk_spawners import (
        partition_runnable_selectors,
        read_tests_enabled_union,
    )

    try:
        _union = read_tests_enabled_union(
            plan_dir / f"{args.slug}_orchestrator.json"
        )
        _runnable, _skipped = partition_runnable_selectors(_union)
        _preflight_blocks = not _runnable
    except Exception:  # noqa: BLE001 — best-effort: on any read/parse
        # failure fall through to the normal loop (which has its own
        # ValueError / driver-crash handling). Never mask a different
        # failure as "no runnable tests".
        _preflight_blocks = False
        _runnable = []
        _skipped = []

    if _preflight_blocks:
        _emit_stderr_json(
            {
                "error": "no_runnable_tests",
                "slug": args.slug,
                "skipped": _skipped,
                "hint": (
                    "tests_enabled contains no runnable pytest selectors "
                    "(node-IDs / paths that exist on disk). The listed "
                    "entries are design-prose, non-pytest gates, or name "
                    "files not yet authored. Build the slug and author its "
                    "pytest files (or wire non-pytest checks as separate "
                    "gate commands) before /test. Refused before spawning "
                    "any coder — no retry budget consumed."
                ),
            }
        )
        return exit_codes.EXIT_USAGE

    # real_tests_at_junctions T5 (SC4): collect-only oracle, layered
    # AFTER the cheap shape/on-disk pre-flight above (the pre-flight
    # stays first; the oracle adds collectability truth — plan SC4
    # "pure upgrade" contract). A selector that passes shape but does
    # NOT collect (`pytest --collect-only` exit 5, or a phantom node-ID
    # inside an existing file) can never go green, so refuse loudly
    # before spawning any coder — same posture and envelope shape as
    # the no_runnable_tests refusal above. COLLECT_ERROR (import-broken
    # module) deliberately passes through: that is a REAL failure
    # surface the retry loop can iterate on.
    from bin._retry_loop.sdk_spawners import (
        COLLECT_NOT_COLLECTABLE,
        collect_only_probe,
    )

    _not_collectable: list[str] = []
    try:
        for _sel in _runnable:
            if collect_only_probe(_sel) == COLLECT_NOT_COLLECTABLE:
                _not_collectable.append(_sel)
    except Exception:  # noqa: BLE001 — best-effort symmetry with the
        # union-read guard above: a probe crash (timeout, missing
        # pytest) must never mask itself as "selector not collectable".
        _not_collectable = []

    if _not_collectable:
        _emit_stderr_json(
            {
                "error": "not_collectable_tests",
                "slug": args.slug,
                "not_collectable": _not_collectable,
                "hint": (
                    "tests_enabled entries pass the shape/on-disk "
                    "pre-flight but `pytest --collect-only` collects "
                    "nothing for them (exit 5 / unrecognized node-ID). "
                    "These selectors can never pass — fix the node-ID or "
                    "author the missing test before /test. Refused before "
                    "spawning any coder — no retry budget consumed."
                ),
            }
        )
        return exit_codes.EXIT_USAGE

    # Lazy-import: the SDK + the adapter factory both touch
    # claude_agent_sdk indirectly. Keep the imports inside the function
    # so this module loads cleanly in environments without the SDK
    # (the smoke check below catches the missing-SDK case BEFORE any
    # of the SDK-dependent surfaces are touched at call time).
    from bin._retry_loop.opus_adapter import build_adapters, hook_env_staged
    from bin._retry_loop.sdk_spawners import (
        ReviewerEmissionExhausted,
        smoke_check_sdk_available,
    )

    # Step 1: SDK smoke check — same short-circuit the chain driver
    # uses at bin/_chain_overnight/phase_spawn.spawn_retry_loop_phase.
    # If the SDK is missing/broken, exit 16 with the diagnostic so the
    # operator sees the same triage signal regardless of entry point.
    sdk_ok, sdk_msg = smoke_check_sdk_available()
    if not sdk_ok:
        _emit_stderr_json(
            {
                "error": "sdk_smoke_failed",
                "detail": sdk_msg,
            }
        )
        return exit_codes.EXIT_VERIFY_PLAN_REJECTED

    # Step 2 + 3 + 4: stage hook env vars, build adapters, run the
    # bounded retry loop with the adapters injected. Mirrors the chain
    # driver's flow at bin/_chain_overnight/phase_spawn.spawn_retry_loop_phase.
    # Phase=5 is structurally correct for the test-step retry loop
    # regardless of who's calling (the chain driver also passes 4 or 5
    # depending on /code vs /test, but the retry-loop surface here is
    # the /test path).
    try:
        with hook_env_staged(slug=args.slug, chain_id=args.chain_id, phase=5):
            _opus_adapter, _verify_adapter, _reviewer_adapter = build_adapters(
                slug=args.slug,
                chain_id=args.chain_id,
                phase=5,
            )
            # T7 of verifier_sdk_wiring: run_test_step_loop returns a
            # 3-tuple (result, records, total_cost_usd). main.py emits
            # exit codes only and doesn't propagate cost (the in-
            # process chain-driver path at
            # phase_spawn.spawn_retry_loop_phase does propagate cost
            # via PhaseResult.cost_usd).
            result, records, _total_cost_usd = iteration_loop.run_test_step_loop(
                plan_dir,
                slug=args.slug,
                chain_id=args.chain_id,
                max_retries=getattr(args, "max_retries", None),
                spawn_opus_fn=_opus_adapter,
                run_verify_fn=_verify_adapter,
                spawn_reviewer_fn=_reviewer_adapter,
                # Verify-first reorder (real_tests_at_junctions
                # follow-up, 2026-06-10): the operator-direct test-step
                # entry is structurally the same phase-5 context as the
                # chain driver's /test dispatch — a green suite returns
                # PASSED without spawning an idle repair coder.
                verify_first=True,
            )
    except ReviewerEmissionExhausted as exc:
        # Reviewer SDK retry exhaustion → exit 16. Mirrors the chain
        # driver's mapping at phase_spawn.spawn_retry_loop_phase Step 4.
        _emit_stderr_json(
            {
                "error": "reviewer_emission_exhausted",
                "subtype": getattr(exc, "subtype", "(no subtype)"),
            }
        )
        return exit_codes.EXIT_VERIFY_PLAN_REJECTED
    except rubric_mod.UnsupportedRubricVersionError as exc:
        _emit_stderr_json(
            {
                "error": "unsupported_schema_version",
                "kind": exc.kind,
                "version": exc.version,
                "supported": exc.supported,
            }
        )
        return exit_codes.EXIT_UNSUPPORTED_SCHEMA_VERSION
    except Exception as exc:  # noqa: BLE001
        _emit_stderr_json({"error": "driver_crash", "detail": str(exc)})
        return exit_codes.EXIT_DRIVER_CRASH

    if result == iteration_loop.IterationResult.PASSED:
        return exit_codes.EXIT_OK

    # Halt path — write morning-review entry, return mapped exit code.
    halt_reason: halt_handoff.HaltReason
    if result == iteration_loop.IterationResult.HALT_TAMPERING:
        halt_reason = "tampering_detected"
        rc = exit_codes.EXIT_RETRY_EXCEEDED
    elif result == iteration_loop.IterationResult.HALT_CAP_EXHAUSTED:
        halt_reason = "retry_exceeded"
        rc = exit_codes.EXIT_RETRY_EXCEEDED
    else:
        halt_reason = "retry_exceeded"
        rc = exit_codes.EXIT_RETRY_EXCEEDED

    try:
        halt_handoff.write_halt_entry(
            plan_dir,
            slug=args.slug,
            chain_id=args.chain_id,
            halt_reason=halt_reason,
            iteration_records=records,
        )
    except Exception as exc:  # noqa: BLE001
        _emit_stderr_json(
            {"error": "atomic_write_failed", "detail": str(exc)}
        )
        return exit_codes.EXIT_ATOMIC_WRITE_FAILED
    return rc


def _run_boundary(args: argparse.Namespace) -> int:
    """Run the runtime §F.9 phase-boundary review gate."""
    try:
        plan_dir = _resolve_plan_dir(args.slug)
    except _UsageError:
        return exit_codes.EXIT_USAGE

    # Operator-direct reviewer wiring (2026-06-10). Mirrors
    # _run_test_step: without an injected spawn_reviewer_fn,
    # run_boundary_review falls back to
    # iteration_loop._default_spawn_reviewer — a placeholder that HALTs
    # every operator-direct `bin/verify boundary` run (exit 10) without
    # ever spawning a reviewer. The chain has no separate boundary
    # driver (phase_spawn dispatches phases 2-5 only), so this CLI is
    # the sole boundary entry point and must carry real wiring.
    from bin._retry_loop.opus_adapter import build_adapters, hook_env_staged
    from bin._retry_loop.sdk_spawners import (
        ReviewerEmissionExhausted,
        smoke_check_sdk_available,
    )

    sdk_ok, sdk_msg = smoke_check_sdk_available()
    if not sdk_ok:
        _emit_stderr_json({"error": "sdk_smoke_failed", "detail": sdk_msg})
        return exit_codes.EXIT_VERIFY_PLAN_REJECTED

    try:
        # phase=6 per the chain comment at phase_spawn.py ("phase 4 →
        # phase 5 → phase-6 boundary review"); only the reviewer
        # adapter is consumed — boundary reviews spawn no coder.
        with hook_env_staged(slug=args.slug, chain_id=args.chain_id, phase=6):
            _o, _v, _reviewer_adapter = build_adapters(
                slug=args.slug,
                chain_id=args.chain_id,
                phase=6,
            )
            verdict = phase_boundary_review.run_boundary_review(
                plan_dir,
                slug=args.slug,
                chain_id=args.chain_id,
                boundary=args.boundary,
                spawn_reviewer_fn=_reviewer_adapter,
            )
    except ReviewerEmissionExhausted as exc:
        _emit_stderr_json(
            {
                "error": "reviewer_emission_exhausted",
                "subtype": getattr(exc, "subtype", "(no subtype)"),
            }
        )
        return exit_codes.EXIT_VERIFY_PLAN_REJECTED
    except rubric_mod.UnsupportedRubricVersionError as exc:
        _emit_stderr_json(
            {
                "error": "unsupported_schema_version",
                "kind": exc.kind,
                "version": exc.version,
                "supported": exc.supported,
            }
        )
        return exit_codes.EXIT_UNSUPPORTED_SCHEMA_VERSION
    except Exception as exc:  # noqa: BLE001
        _emit_stderr_json({"error": "driver_crash", "detail": str(exc)})
        return exit_codes.EXIT_DRIVER_CRASH

    if verdict.terminal_shape == phase_boundary_review.TerminalShape.READY:
        return exit_codes.EXIT_OK
    if verdict.terminal_shape == phase_boundary_review.TerminalShape.NEEDS_REVISION:
        # NEEDS_REVISION at CLI termination means the chain driver should
        # re-spawn the prior step then re-invoke this subcommand. We
        # return EXIT_OK but emit a structured envelope on stdout so the
        # driver can dispatch.
        print(json.dumps({
            "verdict": "NEEDS_REVISION",
            "rubric": verdict.rubric,
        }))
        return exit_codes.EXIT_OK
    # HALT path — morning-review entry already written by run_boundary_review.
    if verdict.counter_exhausted:
        return exit_codes.EXIT_RETRY_EXCEEDED
    return exit_codes.EXIT_PHASE_BOUNDARY_HALT


def _run_build_briefing(args: argparse.Namespace) -> int:
    """Deterministically build the runtime reviewer prompt."""
    try:
        plan_dir = _resolve_plan_dir(args.slug)
    except _UsageError:
        return exit_codes.EXIT_USAGE

    try:
        prompt = briefing_mod.build_briefing(
            slug=args.slug,
            iteration_n=args.iteration,
            rubric_kind=args.rubric_kind,
            plan_dir=plan_dir,
        )
    except Exception as exc:  # noqa: BLE001
        _emit_stderr_json({"error": "driver_crash", "detail": str(exc)})
        return exit_codes.EXIT_DRIVER_CRASH

    if args.out is not None:
        try:
            args.out.write_text(prompt, encoding="utf-8")
        except OSError as exc:
            _emit_stderr_json(
                {"error": "atomic_write_failed", "detail": str(exc)}
            )
            return exit_codes.EXIT_ATOMIC_WRITE_FAILED
    else:
        print(prompt)
    return exit_codes.EXIT_OK


def _run_junction(args: argparse.Namespace) -> int:
    """Run the junction-time collect-check (real_tests_at_junctions SC4).

    Operator-direct surface: ``bin/verify junction <slug> --junction
    <J-id>``. The /code step-6.1 junction-halt notice for ``test_gate``
    junctions points the operator here before/with ``/test``.

    Exit-code convention (documented in the subparser help text):

    - 0 (`EXIT_OK`) — advance-ok: the covering set's consolidated
      ``tests_enabled`` union is non-empty and every entry is a
      collectable pytest selector or a recognized typed gate command.
    - 10 (`EXIT_PHASE_BOUNDARY_HALT`) — refuse advance: empty union or
      non-collectable entries. Reuses the §F gate-HALT slot — a junction
      test_gate refusing advance is structurally the same operator
      signal as a phase-boundary HALT.
    - 38 (`EXIT_JUNCTION_KIND_NOT_APPLICABLE`) — the junction exists but
      is a ``review_gate``/``phase_boundary``, not a ``test_gate``. The
      collect-check does not apply; the gate clears by operator action.
      Distinct from 0 AND 10 so a driver never reads "not applicable"
      as "cleared" (issue #39's fail-open) or as "failed".
    - 1 (`EXIT_USAGE`) — unknown slug/junction id, malformed
      orchestrator, or covering-set resolution failure.
    - 2 (`EXIT_DRIVER_CRASH`) — unexpected exception.

    The structured verdict prints to stdout as JSON either way so the
    operator (or a driver) can read `entries[].classification` per
    selector.
    """
    try:
        plan_dir = _resolve_plan_dir(args.slug)
    except _UsageError:
        return exit_codes.EXIT_USAGE

    # SDK-free imports — the junction hook never spawns an agent.
    from bin._orchestrator_query.orchestrator_loader import (
        OrchestratorJsonMalformedError,
        OrchestratorJsonMissingError,
        SlugNotFoundError,
    )
    from bin._retry_loop.sdk_spawners import (
        JunctionKindNotApplicableError,
        junction_collect_check,
    )

    try:
        verdict = junction_collect_check(
            plan_dir, slug=args.slug, junction_id=args.junction
        )
    except JunctionKindNotApplicableError as exc:
        _emit_stderr_json(
            {
                "error": "junction_kind_not_applicable",
                "junction_id": exc.junction_id,
                "junction_kind": exc.kind,
                "detail": str(exc),
            }
        )
        return exit_codes.EXIT_JUNCTION_KIND_NOT_APPLICABLE
    except (
        ValueError,
        KeyError,
        FileNotFoundError,
        SlugNotFoundError,
        OrchestratorJsonMissingError,
        OrchestratorJsonMalformedError,
    ) as exc:
        _emit_stderr_json({"error": "usage", "detail": str(exc)})
        return exit_codes.EXIT_USAGE
    except Exception as exc:  # noqa: BLE001
        _emit_stderr_json({"error": "driver_crash", "detail": str(exc)})
        return exit_codes.EXIT_DRIVER_CRASH

    print(json.dumps(verdict, indent=2, sort_keys=True))
    if verdict["advance_ok"]:
        return exit_codes.EXIT_OK
    return exit_codes.EXIT_PHASE_BOUNDARY_HALT


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

class _UsageError(ValueError):
    """argparse-equivalent for programmatic usage errors."""


def _emit_stderr_json(payload: dict[str, Any]) -> None:
    """Emit structured-error JSON envelope to stderr."""
    print(json.dumps(payload), file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
