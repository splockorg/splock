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
    if leading in ("test-step", "boundary", "build-briefing"):
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
        p.error("subcommand required (test-step | boundary | build-briefing)")
        return exit_codes.EXIT_USAGE

    if args.subcommand == "test-step":
        return _run_test_step(args)
    if args.subcommand == "boundary":
        return _run_boundary(args)
    if args.subcommand == "build-briefing":
        return _run_build_briefing(args)
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
        return _run_test_step(ns)
    if phase == 4:
        return exit_codes.EXIT_OK
    _emit_stderr_json(
        {"error": "usage", "detail": f"unsupported --phase value: {phase}"}
    )
    return exit_codes.EXIT_USAGE


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

    try:
        verdict = phase_boundary_review.run_boundary_review(
            plan_dir,
            slug=args.slug,
            chain_id=args.chain_id,
            boundary=args.boundary,
        )
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
