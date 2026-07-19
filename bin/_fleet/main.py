"""CLI entry point for `bin/fleet`.

Subcommands:
  update <slug> [--stage S] [--status S] [--next N] [--blockers B]
                [--piece P] [--wave W] [--actor A] [--note N]
                [--spawn-directive TEXT] [--render]
  render  [--write]
  state   <slug>
  init    [--hub <existing .md>]
  seed    --from <seed.json> [--force] [--events]
  migrate [--<zone>-start S --<zone>-end E]... [--dry-run]
  stage   (start|finish) <slug> --stage S [--status S] [--next N]
          [--actor A] [--note N]
  spawn   <slug> --stage S [--model M] [--effort E]
          [--permission-mode P] [--allowed-tools T...]
          [--max-budget-usd B] [--prompt-suffix TEXT] [--dry-run]
  board   [--json]
  resume  <slug> [--session SID] [--directive TEXT] [--model M]
          [--effort E] [--permission-mode P] [--allowed-tools T...]
          [--max-budget-usd B] [--dry-run]

`stage` is the auto-integration verb the stage commands run: it is a
SILENT NO-OP (exit 0) when the project has not opted in, so it is safe
to call unconditionally. The other mutating subcommands refuse with
exit 45 until `bin/fleet init` has been run.

`spawn`/`board`/`resume` are the headless C&C surface: one parent
screen forks fresh `claude -p` children (CLI subprocess — subscription
transport, never the SDK), absorbs only their final JSON, and re-enters
any child by session id. See docs/FLEET.md §Headless C&C.

Invoked via the POSIX shell wrapper at `bin/fleet`, which delegates to
`python -m bin._fleet.main`.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys

from bin import _env_paths
from bin._fleet import auto, board as board_mod, engine, exit_codes, hub, paths
from bin._fleet import runs as runs_mod
from bin._fleet import seed as seed_mod
from bin._fleet import spawn as spawn_mod


def _emit_stderr_json(payload: dict) -> None:
    sys.stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _require_initialized(subcommand: str) -> bool:
    if paths.enabled():
        return True
    # Name the invoking directory: the classic failure is running from a
    # non-fleet cwd, where a silent empty answer would be wrong data
    # (first field deployment, 2026-07-19). SPLOCK_CALLER_PWD is the
    # pre-cd invoking dir the bin/fleet wrapper preserves.
    caller = os.environ.get("SPLOCK_CALLER_PWD") or os.getcwd()
    _emit_stderr_json({
        "error": "fleet_not_initialized",
        "subcommand": subcommand,
        "detail": (
            f"no fleet meta found from {caller}: fleet is opt-in per "
            f"project and {paths.meta_path()} does not exist. Run "
            f"`bin/fleet init` (or `bin/fleet init --hub <existing .md>`) "
            f"in the adopter repo first."
        ),
    })
    return False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bin/fleet",
        description=(
            "Concurrency-safe multi-slug lifecycle tracker: per-slug state "
            "files + a generated status hub. See docs/FLEET.md."
        ),
        allow_abbrev=False,
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    u = sub.add_parser("update", help="set a slug's state + append an event",
                       allow_abbrev=False)
    u.add_argument("slug")
    u.add_argument("--stage")
    u.add_argument("--status", help=f"one of {sorted(engine.VALID_STATUS)}")
    u.add_argument("--next", dest="next_action")
    u.add_argument("--blockers")
    u.add_argument("--piece")
    u.add_argument("--wave", type=int)
    u.add_argument("--actor")
    u.add_argument("--note")
    u.add_argument("--spawn-directive", dest="spawn_directive",
                   help="per-slug operator context `fleet spawn` appends to "
                        "the child prompt (one-shot: cleared when the stage "
                        'that consumed it finishes; "" clears by hand)')
    u.add_argument("--render", action="store_true",
                   help="also regenerate the hub zones")

    r = sub.add_parser("render", help="project per-slug files → hub .md zones",
                       allow_abbrev=False)
    r.add_argument("--write", action="store_true", help="write in place (else print)")

    s = sub.add_parser("state", help="print a slug's current state",
                       allow_abbrev=False)
    s.add_argument("slug")

    i = sub.add_parser("init", help="opt this project in (writes _fleet_meta.json)",
                       allow_abbrev=False)
    i.add_argument("--hub", default=None,
                   help="register an EXISTING hub .md (project-root-relative) "
                        "instead of scaffolding docs/plans/_fleet/fleet.md")

    sd = sub.add_parser("seed", help="one-time: author per-slug state from a JSON file",
                        allow_abbrev=False)
    sd.add_argument("--from", dest="source", required=True,
                    help="seed document (see bin/_fleet/seed.py for the shape)")
    sd.add_argument("--force", action="store_true",
                    help="overwrite existing _fleet.json (default: skip)")
    sd.add_argument("--events", action="store_true",
                    help="also append the seed events")

    m = sub.add_parser(
        "migrate",
        help="one-time: wire the FLEET:* zones + protocol into the registered hub",
        allow_abbrev=False,
    )
    for zone in engine.MARKERS:
        m.add_argument(f"--{zone}-start", dest=f"{zone}_start", default=None,
                       help=f"start anchor for the {zone} zone (kept verbatim)")
        m.add_argument(f"--{zone}-end", dest=f"{zone}_end", default=None,
                       help=f"end anchor for the {zone} zone (kept verbatim)")
    m.add_argument("--dry-run", action="store_true")

    st = sub.add_parser(
        "stage",
        help="auto-integration verb for stage commands (silent no-op unless opted in)",
        allow_abbrev=False,
    )
    st.add_argument("phase", choices=["start", "finish"])
    st.add_argument("slug")
    st.add_argument("--stage", required=True, dest="stage_name")
    st.add_argument("--status", default=None,
                    help="finish only; default ready")
    st.add_argument("--next", dest="next_action", default=None,
                    help="finish only; default = the canonical next stage")
    st.add_argument("--actor", default=None)
    st.add_argument("--note", default=None)

    def _add_child_config_flags(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--model", default=None,
                        help="child model id (else stage profile / CLI default)")
        sp.add_argument("--effort", default=None,
                        choices=["low", "medium", "high", "xhigh", "max"])
        sp.add_argument("--permission-mode", dest="permission_mode", default=None,
                        help="child permission mode (headless default DENIES "
                             "unapproved actions and the child reports them)")
        sp.add_argument("--allowed-tools", dest="allowed_tools", nargs="+",
                        default=None,
                        help='tool patterns, e.g. Bash "Bash(git diff *)" Edit')
        sp.add_argument("--max-budget-usd", dest="max_budget_usd", default=None,
                        help="per-child spend ceiling (claude --max-budget-usd)")
        sp.add_argument("--dry-run", action="store_true",
                        help="print the child argv; launch nothing")

    sp = sub.add_parser(
        "spawn",
        help="fork a fresh headless child for one stage (C&C; subscription "
             "CLI transport)",
        allow_abbrev=False,
    )
    sp.add_argument("slug")
    sp.add_argument("--stage", required=True, dest="stage_name")
    sp.add_argument("--prompt-suffix", dest="prompt_suffix", default=None,
                    help="extra prompt text appended after the stage command")
    _add_child_config_flags(sp)

    b = sub.add_parser("board", help="the C&C view: states + live children + "
                                     "resume handles + cost",
                       allow_abbrev=False)
    b.add_argument("--json", action="store_true", dest="json_output")

    rs = sub.add_parser(
        "resume",
        help="re-enter a slug's newest headless session with its context intact",
        allow_abbrev=False,
    )
    rs.add_argument("slug")
    rs.add_argument("--session", default=None,
                    help="explicit session id (default: newest in the runs ledger)")
    rs.add_argument("--directive", default=None,
                    help="operator directive — wrapped via bin/wrap "
                         "(data-not-instructions envelope) before injection")
    _add_child_config_flags(rs)

    return p


def _cmd_update(args: argparse.Namespace) -> int:
    if not _require_initialized("update"):
        return exit_codes.EXIT_FLEET_NOT_INITIALIZED
    try:
        state = engine.update(
            args.slug,
            stage=args.stage,
            status=args.status,
            next_action=args.next_action,
            blockers=args.blockers,
            piece=args.piece,
            wave=args.wave,
            actor=args.actor,
            note=args.note,
            spawn_directive=args.spawn_directive,
        )
    except ValueError as exc:
        _emit_stderr_json({"error": "usage", "detail": str(exc)})
        return exit_codes.EXIT_USAGE
    except OSError as exc:
        _emit_stderr_json({"error": "atomic_write_failed", "detail": str(exc)})
        return exit_codes.EXIT_ATOMIC_WRITE_FAILED
    print(
        f"fleet: {args.slug} → {state.get('stage')}/{state.get('status')} "
        f"@ {state.get('updated')}"
    )
    if args.render:
        return _render_write()
    return exit_codes.EXIT_OK


def _render_write() -> int:
    try:
        n_slugs, n_events = engine.render_hub_write()
    except engine.HubMarkersMissing as exc:
        _emit_stderr_json({"error": "hub_markers_missing", "detail": str(exc)})
        return exit_codes.EXIT_HUB_ANCHOR_MISSING
    except FileNotFoundError as exc:
        _emit_stderr_json({
            "error": "usage",
            "detail": f"registered hub .md not found: {exc.filename}",
        })
        return exit_codes.EXIT_USAGE
    except OSError as exc:
        _emit_stderr_json({"error": "atomic_write_failed", "detail": str(exc)})
        return exit_codes.EXIT_ATOMIC_WRITE_FAILED
    meta = engine.load_meta()
    print(f"fleet: rendered {n_slugs} slugs / {n_events} events → {paths.hub_path(meta)}")
    return exit_codes.EXIT_OK


def _cmd_render(args: argparse.Namespace) -> int:
    # BOTH modes require initialization. Print mode used to run
    # uninitialized "for convenience" — field-falsified on the first
    # deployment (qum, 2026-07-19): from a wrong cwd it rendered an
    # EMPTY universe with exit 0, so a scripted parity check would
    # conclude the fleet is empty instead of being told "not a fleet
    # repo". Silent success with wrong data is the exact failure class
    # `spawn`'s guard already refuses; render now matches it.
    if not _require_initialized("render --write" if args.write else "render"):
        return exit_codes.EXIT_FLEET_NOT_INITIALIZED
    if not args.write:
        for zone, body in engine.render_zones().items():
            print(f"\n===== {zone} =====\n{body}")
        return exit_codes.EXIT_OK
    return _render_write()


def _cmd_state(args: argparse.Namespace) -> int:
    st = engine.load_state(args.slug)
    print(json.dumps(st, indent=2, ensure_ascii=False) if st else f"(no state for {args.slug})")
    return exit_codes.EXIT_OK


def _cmd_init(args: argparse.Namespace) -> int:
    try:
        created, hub_abs = hub.init(hub=args.hub)
    except FileNotFoundError as exc:
        _emit_stderr_json({"error": "usage", "detail": str(exc)})
        return exit_codes.EXIT_USAGE
    except OSError as exc:
        _emit_stderr_json({"error": "atomic_write_failed", "detail": str(exc)})
        return exit_codes.EXIT_ATOMIC_WRITE_FAILED
    if created:
        print(f"fleet: initialized — meta {paths.meta_path()}, hub {hub_abs}")
        if args.hub:
            print("fleet: next, wire the zones into the existing hub: bin/fleet migrate")
    else:
        print(f"fleet: already initialized ({paths.meta_path()}) — no-op")
    return exit_codes.EXIT_OK


def _cmd_seed(args: argparse.Namespace) -> int:
    if not _require_initialized("seed"):
        return exit_codes.EXIT_FLEET_NOT_INITIALIZED
    try:
        wrote, skipped, appended = seed_mod.seed_from_file(
            args.source, force=args.force, events=args.events,
        )
    except (OSError, json.JSONDecodeError, seed_mod.SeedInputError) as exc:
        _emit_stderr_json({"error": "usage", "detail": f"seed input: {exc}"})
        return exit_codes.EXIT_USAGE
    print(f"seed: wrote {wrote}, skipped {skipped} existing", end="")
    print(f", appended {appended} events" if args.events else "")
    return exit_codes.EXIT_OK


def _cmd_migrate(args: argparse.Namespace) -> int:
    if not _require_initialized("migrate"):
        return exit_codes.EXIT_FLEET_NOT_INITIALIZED
    anchors: dict[str, tuple[str, str]] = {}
    for zone in engine.MARKERS:
        start = getattr(args, f"{zone}_start")
        end = getattr(args, f"{zone}_end")
        if (start is None) != (end is None):
            _emit_stderr_json({
                "error": "usage",
                "detail": f"--{zone}-start and --{zone}-end must be given together",
            })
            return exit_codes.EXIT_USAGE
        if start is not None:
            anchors[zone] = (start, end)
    try:
        message = hub.migrate(anchors, dry_run=args.dry_run)
    except ValueError as exc:
        _emit_stderr_json({"error": "usage", "detail": str(exc)})
        return exit_codes.EXIT_USAGE
    except hub.AnchorsMissing as exc:
        _emit_stderr_json({
            "error": "hub_anchor_missing",
            "missing": exc.missing,
            "detail": str(exc),
        })
        return exit_codes.EXIT_HUB_ANCHOR_MISSING
    except FileNotFoundError as exc:
        _emit_stderr_json({
            "error": "usage",
            "detail": f"registered hub .md not found: {exc}",
        })
        return exit_codes.EXIT_USAGE
    except OSError as exc:
        _emit_stderr_json({"error": "atomic_write_failed", "detail": str(exc)})
        return exit_codes.EXIT_ATOMIC_WRITE_FAILED
    print(f"fleet: {message}")
    return exit_codes.EXIT_OK


def _cmd_stage(args: argparse.Namespace) -> int:
    """The stage-command hook. Deliberately forgiving: exit 0 always,
    unless the arguments themselves are invalid."""
    if args.status is not None and args.status not in engine.VALID_STATUS:
        _emit_stderr_json({
            "error": "usage",
            "detail": f"--status must be one of {sorted(engine.VALID_STATUS)}",
        })
        return exit_codes.EXIT_USAGE
    if not paths.enabled() or not paths.slug_dir(args.slug).is_dir():
        print("fleet: not initialized for this project — skipping (opt-in)",
              file=sys.stderr)
        return exit_codes.EXIT_OK
    actor = args.actor or f"{args.stage_name}-agent"
    if args.phase == "start":
        auto.stage_started(args.slug, args.stage_name, actor=actor, note=args.note)
    else:
        auto.stage_finished(
            args.slug, args.stage_name,
            status=args.status or "ready",
            next_action=args.next_action,
            actor=actor, note=args.note,
        )
    return exit_codes.EXIT_OK


def _child_overrides(args: argparse.Namespace) -> dict:
    return {
        "model": args.model,
        "effort": args.effort,
        "permission_mode": args.permission_mode,
        "allowed_tools": args.allowed_tools,
        "max_budget_usd": args.max_budget_usd,
    }


def _cmd_spawn(args: argparse.Namespace) -> int:
    if not _require_initialized("spawn"):
        return exit_codes.EXIT_FLEET_NOT_INITIALIZED
    try:
        run_id, argv = spawn_mod.spawn(
            args.slug,
            args.stage_name,
            overrides=_child_overrides(args),
            prompt_suffix=args.prompt_suffix,
            dry_run=args.dry_run,
        )
    except spawn_mod.SpawnRefused as exc:
        _emit_stderr_json({"error": "spawn_refused", "detail": str(exc)})
        return exit_codes.EXIT_SPAWN_REFUSED
    if args.dry_run:
        print(f"dry-run: {shlex.join(argv)}")
        return exit_codes.EXIT_OK
    print(
        f"fleet: spawned {args.slug} [{args.stage_name}] run {run_id} — "
        f"detached; result lands in {runs_mod.runs_path(args.slug).name}; "
        f"watch with `bin/fleet board`"
    )
    return exit_codes.EXIT_OK


def _cmd_board(args: argparse.Namespace) -> int:
    # Same silent-empty class as the render wrong-cwd defect (qum
    # burn-in closeout, 2026-07-19): an uninitialized/wrong-cwd board
    # showed "0 slugs · 0 live · $0" with exit 0 — wrong data dressed as
    # a quiet fleet. Refuse like spawn/render do.
    if not _require_initialized("board"):
        return exit_codes.EXIT_FLEET_NOT_INITIALIZED
    b = board_mod.build_board()
    print(board_mod.render_json(b) if args.json_output else board_mod.render_text(b))
    return exit_codes.EXIT_OK


def _wrap_directive(directive: str) -> str:
    """Route operator prose through the canonical data-not-instructions
    envelope (`bin/wrap --kind operator-directive`)."""
    wrap_bin = _env_paths.plugin_root() / "bin" / "wrap"
    proc = subprocess.run(
        [str(wrap_bin), "--kind", "operator-directive"],
        input=directive.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise spawn_mod.SpawnRefused(
            "bin/wrap refused the directive "
            f"(exit {proc.returncode}): {proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    return proc.stdout.decode("utf-8")


def _cmd_resume(args: argparse.Namespace) -> int:
    if not _require_initialized("resume"):
        return exit_codes.EXIT_FLEET_NOT_INITIALIZED
    session = args.session or runs_mod.latest_session_id(args.slug)
    if not session:
        _emit_stderr_json({
            "error": "no_session",
            "detail": f"no session recorded for {args.slug} in "
                      f"{runs_mod.runs_path(args.slug)} — spawn one first",
        })
        return exit_codes.EXIT_NO_SESSION

    state = engine.load_state(args.slug) or {}
    stage = state.get("stage") or "resume"
    try:
        if args.directive:
            prompt = _wrap_directive(args.directive)
        else:
            prompt = ("Continue the blocked work for this task. Re-check the "
                      "blocker you reported, resolve it if now possible, and "
                      "finish the stage per the fleet protocol.")
        run_id, argv = spawn_mod.spawn(
            args.slug,
            stage,
            overrides=_child_overrides(args),
            resume_session=session,
            resume_prompt=prompt,
            dry_run=args.dry_run,
        )
    except spawn_mod.SpawnRefused as exc:
        _emit_stderr_json({"error": "spawn_refused", "detail": str(exc)})
        return exit_codes.EXIT_SPAWN_REFUSED
    if args.dry_run:
        print(f"dry-run: {shlex.join(argv)}")
        return exit_codes.EXIT_OK
    print(
        f"fleet: resumed {args.slug} (session {session[:8]}…) run {run_id} — "
        f"detached; watch with `bin/fleet board`"
    )
    return exit_codes.EXIT_OK


_DISPATCH = {
    "update": _cmd_update,
    "render": _cmd_render,
    "state": _cmd_state,
    "init": _cmd_init,
    "seed": _cmd_seed,
    "migrate": _cmd_migrate,
    "stage": _cmd_stage,
    "spawn": _cmd_spawn,
    "board": _cmd_board,
    "resume": _cmd_resume,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exit_codes.EXIT_USAGE if exc.code != 0 else exit_codes.EXIT_OK
    try:
        return _DISPATCH[args.subcommand](args)
    except Exception as exc:  # noqa: BLE001 — closed-enum driver crash
        _emit_stderr_json({
            "error": "driver_crash",
            "detail": f"{type(exc).__name__}: {exc}",
        })
        return exit_codes.EXIT_DRIVER_CRASH


if __name__ == "__main__":
    sys.exit(main())
