"""Triage-gesture dispatch (implplan §H.impl.6).

Each terminal gesture (reactivate / route-outstanding / route-marker /
abandon) follows the same shape:

1. Shared pre-checks (entry exists; mirror is `[pending]`; gesture-specific
   arg validation).
2. Invoke the underlying CLI(s) via subprocess (`bin/update_orchestrator`,
   `bin/route_issue`, `bin/marker`, `bin/lessons` per the gesture's table
   row in §H.impl.6).
3. On underlying success: update the mirror, emit a triage row,
   maybe-archive, regen the index.
4. On underlying failure: propagate the exit code verbatim; do NOT mutate
   the queue file; do NOT emit a triage row.

`acknowledge` is a non-terminal gesture: emits one log row and returns;
no underlying-CLI invocation; no mirror or archive mutation.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from typing import Optional

from . import entry_format, log_emit, queue_file, archive, index_regen
from .exit_codes import (
    EXIT_OK,
    EXIT_QUEUE_ENTRY_NOT_FOUND,
    EXIT_TRIAGE_DOUBLE_CLOSE,
    EXIT_ATOMIC_WRITE_FAILED,
)


def _bin_path(repo_root: pathlib.Path, name: str) -> str:
    return str(repo_root / "bin" / name)


def _lookup_entry(
    repo_root: pathlib.Path, slug: str, task_id: str
) -> Optional[tuple[pathlib.Path, entry_format.Entry]]:
    return queue_file.find_entry_across_files(repo_root, slug, task_id)


def _refuse(
    code: int, msg: str, *, json_output: bool, error_kind: str
) -> int:
    if json_output:
        print(json.dumps({"error": error_kind, "message": msg}), file=sys.stderr)
    else:
        print(msg, file=sys.stderr)
    return code


def _run_underlying(
    cmd: list[str],
    *,
    dry_run: bool,
    json_output: bool,
) -> int:
    """Run an underlying CLI. Returns exit code.

    On dry-run: print the would-be command + return 0.
    """
    if dry_run:
        if json_output:
            print(json.dumps({"dry_run": True, "cmd": cmd}))
        else:
            print("[dry-run] would invoke:", " ".join(cmd))
        return 0
    proc = subprocess.run(cmd, capture_output=False)
    return proc.returncode


def _run_underlying_capture(
    cmd: list[str],
    *,
    dry_run: bool,
    json_output: bool,
) -> tuple[int, str, str]:
    """Run underlying CLI capturing stdout (needed for route-outstanding
    where the line_id is returned on stdout)."""
    if dry_run:
        if json_output:
            print(json.dumps({"dry_run": True, "cmd": cmd}))
        else:
            print("[dry-run] would invoke:", " ".join(cmd))
        return 0, "", ""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode, proc.stdout, proc.stderr


def _post_success(
    repo_root: pathlib.Path,
    daily: pathlib.Path,
    slug: str,
    task_id: str,
    new_mirror: str,
    *,
    event_type: str,
    sub_emitter: str,
    reason: str,
    pointer: Optional[str] = None,
    transition_to: str = "deferred",
    transition_from: str = "deferred",
) -> int:
    """Common post-success path per §H.impl.6 step 1-4."""
    try:
        updated = queue_file.update_mirror_atomic(daily, task_id, new_mirror)
    except OSError as exc:
        print(f"atomic-write failed: {exc}", file=sys.stderr)
        return EXIT_ATOMIC_WRITE_FAILED
    if updated is None:
        # Race during gesture; treat as double-close.
        return EXIT_TRIAGE_DOUBLE_CLOSE

    plan_dir = repo_root / "docs" / "plans" / slug
    try:
        log_emit.emit_triage(
            plan_dir,
            slug=slug,
            task_id=task_id,
            event_type=event_type,
            sub_emitter=sub_emitter,
            reason=reason,
            pointer=pointer,
            transition_from=transition_from,
            transition_to=transition_to,
        )
    except Exception as exc:
        print(
            f"WARN: morning-review triage row emit failed: {exc}",
            file=sys.stderr,
        )

    # Archive-on-last-close.
    try:
        archive.maybe_move_if_all_closed(daily)
    except OSError as exc:
        print(f"WARN: archive move failed (continuing): {exc}", file=sys.stderr)

    # Regen index.
    try:
        index_regen.regenerate_for_slug(repo_root, slug)
    except Exception as exc:
        print(f"WARN: index regen failed: {exc}", file=sys.stderr)

    return EXIT_OK


def reactivate(
    *,
    repo_root: pathlib.Path,
    slug: str,
    task_id: str,
    reason: Optional[str],
    dry_run: bool,
    json_output: bool,
) -> int:
    """Per §H.impl.6 reactivate row."""
    found = _lookup_entry(repo_root, slug, task_id)
    if found is None:
        return _refuse(
            EXIT_QUEUE_ENTRY_NOT_FOUND,
            f"no morning-review entry matches slug={slug} task_id={task_id}",
            json_output=json_output,
            error_kind="queue_entry_not_found",
        )
    daily, entry = found
    if entry.triage_mirror != "[pending]":
        return _refuse(
            EXIT_TRIAGE_DOUBLE_CLOSE,
            f"entry {task_id} mirror is {entry.triage_mirror} (already terminal)",
            json_output=json_output,
            error_kind="triage_double_close",
        )

    cmd = [
        _bin_path(repo_root, "update_orchestrator"),
        slug,
        task_id,
        "wip",
    ]
    if reason:
        cmd.extend(["--reason", reason])

    rc = _run_underlying(cmd, dry_run=dry_run, json_output=json_output)
    if rc != 0:
        return rc
    if dry_run:
        return 0
    return _post_success(
        repo_root,
        daily,
        slug,
        task_id,
        "[reactivated]",
        event_type=log_emit.EVT_TRIAGE_REACTIVATE,
        sub_emitter=log_emit.EMIT_REACTIVATE,
        reason=(
            f"morning_review_triage_reactivate task_id={task_id}: "
            f"{reason or '(no reason)'}"
        ),
        transition_to="wip",
        transition_from="deferred",
    )


def route_outstanding(
    *,
    repo_root: pathlib.Path,
    slug: str,
    task_id: str,
    reason: Optional[str],
    dry_run: bool,
    json_output: bool,
) -> int:
    """Per §H.impl.6 route-outstanding row (mint-then-pointer order)."""
    found = _lookup_entry(repo_root, slug, task_id)
    if found is None:
        return _refuse(
            EXIT_QUEUE_ENTRY_NOT_FOUND,
            f"no morning-review entry matches slug={slug} task_id={task_id}",
            json_output=json_output,
            error_kind="queue_entry_not_found",
        )
    daily, entry = found
    if entry.triage_mirror != "[pending]":
        return _refuse(
            EXIT_TRIAGE_DOUBLE_CLOSE,
            f"entry {task_id} mirror is {entry.triage_mirror} (already terminal)",
            json_output=json_output,
            error_kind="triage_double_close",
        )

    # (1) Mint via bin/route_issue. Description = reason fallback to task_title-ish.
    description = (
        reason
        or f"morning-review routed task {task_id} (slug={slug}, "
        f"chain={entry.chain_id})"
    )
    context = f"morning-review:{task_id}:{daily.stem}"
    # Always pass --json to the mint subprocess so we get deterministic
    # structured output (per F-01 of Phase 3 integration Sonnet review
    # 2026-05-21: the non-JSON stdout shape has two lines and
    # splitlines()[-1] picked the wrong one). The mint result's JSON
    # carries line_id directly; parent triage --json flag is independent
    # of this internal call's framing.
    mint_cmd = [
        _bin_path(repo_root, "route_issue"),
        "--type",
        "outstanding",
        "--description",
        description,
        "--context",
        context,
        "--json",
    ]
    rc, stdout, _ = _run_underlying_capture(
        mint_cmd, dry_run=dry_run, json_output=json_output
    )
    if rc != 0:
        return rc
    if dry_run:
        return 0
    line_id = ""
    if stdout.strip():
        try:
            mint_result = json.loads(stdout.strip())
            line_id = mint_result.get("line_id", "")
        except json.JSONDecodeError:
            line_id = ""
    if not line_id:
        # route_issue did not return a line_id on stdout — treat as failure.
        print(
            "WARN: bin/route_issue did not emit a line_id on stdout; "
            "skipping --pointer write",
            file=sys.stderr,
        )

    # (2) Update orchestrator state to deferred with --pointer.
    upd_cmd = [
        _bin_path(repo_root, "update_orchestrator"),
        slug,
        task_id,
        "deferred",
    ]
    if line_id:
        upd_cmd.extend(["--pointer", line_id])
    if reason:
        upd_cmd.extend(["--reason", reason])

    rc = _run_underlying(upd_cmd, dry_run=False, json_output=json_output)
    if rc != 0:
        return rc

    return _post_success(
        repo_root,
        daily,
        slug,
        task_id,
        "[routed-outstanding]",
        event_type=log_emit.EVT_TRIAGE_ROUTE_OUTSTANDING,
        sub_emitter=log_emit.EMIT_ROUTE_OUTSTANDING,
        reason=(
            f"morning_review_triage_route_outstanding task_id={task_id} "
            f"line_id={line_id}: {reason or '(no reason)'}"
        ),
        pointer=line_id or None,
    )


def route_marker(
    *,
    repo_root: pathlib.Path,
    slug: str,
    task_id: str,
    prefix: str,
    reason: Optional[str],
    trigger: Optional[str],
    detail: Optional[str],
    dry_run: bool,
    json_output: bool,
) -> int:
    """Per §H.impl.6 route-marker row.

    Delegates entirely to `bin/_marker/route_marker.py::run` (the §K.impl.9
    wrapper). That module internally invokes `bin/marker create` then
    calls `bin/morning-review --internal-mark-deferred` to update the
    mirror — so this dispatcher does NOT call `_post_success` directly.
    The mirror update happens inside the K wrapper's call-back.

    The route-marker delegate may not invoke `--internal-mark-deferred`
    until §K.impl.9 ships the call-back wiring; for now we proactively
    apply the post-success path on a non-zero exit-code path to keep
    the mirror in sync. The internal call-back is idempotent.
    """
    found = _lookup_entry(repo_root, slug, task_id)
    if found is None:
        return _refuse(
            EXIT_QUEUE_ENTRY_NOT_FOUND,
            f"no morning-review entry matches slug={slug} task_id={task_id}",
            json_output=json_output,
            error_kind="queue_entry_not_found",
        )
    daily, entry = found
    if entry.triage_mirror != "[pending]":
        return _refuse(
            EXIT_TRIAGE_DOUBLE_CLOSE,
            f"entry {task_id} mirror is {entry.triage_mirror} (already terminal)",
            json_output=json_output,
            error_kind="triage_double_close",
        )

    if dry_run:
        if json_output:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "gesture": "route-marker",
                        "slug": slug,
                        "task_id": task_id,
                        "prefix": prefix,
                    }
                )
            )
        else:
            print(
                f"[dry-run] would route {task_id} to marker prefix={prefix} "
                f"via bin/_marker/route_marker.py"
            )
        return 0

    from bin._marker.route_marker import run as route_marker_run

    rc = route_marker_run(
        slug=slug,
        task_id=task_id,
        new_prefix=prefix,
        reason=reason or "",
        detail=detail,
        explicit_trigger=trigger,
        repo_root=repo_root,
        json_output=json_output,
    )
    if rc != 0:
        return rc

    # Idempotent mirror update + index regen (in case the wrapper did not
    # propagate via --internal-mark-deferred for any reason).
    return _post_success(
        repo_root,
        daily,
        slug,
        task_id,
        "[routed-marker]",
        event_type=log_emit.EVT_TRIAGE_ROUTE_MARKER,
        sub_emitter=log_emit.EMIT_ROUTE_MARKER,
        reason=(
            f"morning_review_triage_route_marker task_id={task_id} "
            f"prefix={prefix}: {reason or '(no reason)'}"
        ),
    )


def abandon(
    *,
    repo_root: pathlib.Path,
    slug: str,
    task_id: str,
    reason: str,
    dry_run: bool,
    json_output: bool,
) -> int:
    """Per §H.impl.6 abandon row.

    Pre-checks (`--confirm` + `--reason` non-empty) live in `cli.py` per
    §H.impl.8. This function assumes both have been validated.
    """
    found = _lookup_entry(repo_root, slug, task_id)
    if found is None:
        return _refuse(
            EXIT_QUEUE_ENTRY_NOT_FOUND,
            f"no morning-review entry matches slug={slug} task_id={task_id}",
            json_output=json_output,
            error_kind="queue_entry_not_found",
        )
    daily, entry = found
    if entry.triage_mirror != "[pending]":
        return _refuse(
            EXIT_TRIAGE_DOUBLE_CLOSE,
            f"entry {task_id} mirror is {entry.triage_mirror} (already terminal)",
            json_output=json_output,
            error_kind="triage_double_close",
        )

    # (1) bin/lessons add (operator-prep).
    lessons_cmd = [_bin_path(repo_root, "lessons"), "add"]
    rc, stdout, _ = _run_underlying_capture(
        lessons_cmd, dry_run=dry_run, json_output=json_output
    )
    if rc != 0:
        return rc
    if dry_run:
        return 0
    lessons_id = stdout.strip().splitlines()[-1] if stdout.strip() else ""

    # (2) update_orchestrator <slug> <task_id> abandoned --pointer <lessons_id>
    upd_cmd = [
        _bin_path(repo_root, "update_orchestrator"),
        slug,
        task_id,
        "cancelled",  # canonical 7-status enum has no "abandoned" — closest is "cancelled"
    ]
    # NOTE: per E.impl + the 7-status canonical enum, abandon maps to
    # `cancelled`. The mirror line still reads `[abandoned]` (per
    # §H.impl.4 closed enum). The underlying transition uses the canonical
    # 7-status value.
    if lessons_id:
        upd_cmd.extend(["--pointer", lessons_id])
    if reason:
        upd_cmd.extend(["--reason", reason])
    rc = _run_underlying(upd_cmd, dry_run=False, json_output=json_output)
    if rc != 0:
        return rc

    return _post_success(
        repo_root,
        daily,
        slug,
        task_id,
        "[abandoned]",
        event_type=log_emit.EVT_TRIAGE_ABANDON,
        sub_emitter=log_emit.EMIT_ABANDON,
        reason=(
            f"morning_review_triage_abandon task_id={task_id} "
            f"lessons_id={lessons_id}: {reason}"
        ),
        pointer=lessons_id or None,
        transition_to="cancelled",
        transition_from="deferred",
    )


def acknowledge(
    *,
    repo_root: pathlib.Path,
    slug: str,
    json_output: bool,
) -> int:
    """Non-terminal gesture per §H.impl.6 final paragraph.

    Writes only the `morning_review_acknowledged` row; does NOT call an
    underlying CLI; does NOT mutate mirror lines or archive.
    """
    plan_dir = repo_root / "docs" / "plans" / slug
    try:
        log_emit.emit_acknowledged(plan_dir, slug=slug)
    except Exception as exc:
        print(f"WARN: acknowledge log emit failed: {exc}", file=sys.stderr)
    if json_output:
        print(json.dumps({"ok": True, "gesture": "acknowledge", "slug": slug}))
    else:
        print(f"acknowledged: {slug}")
    return EXIT_OK


__all__ = [
    "reactivate",
    "route_outstanding",
    "route_marker",
    "abandon",
    "acknowledge",
]
