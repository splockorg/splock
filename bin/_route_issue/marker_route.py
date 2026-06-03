"""`--type marker` handler — delegate to `bin/marker create` (implplan §L.impl).

Per K.impl.13 + L.impl.11: invoke `bin/marker create` via subprocess.run,
propagating its exit code verbatim. Stamps `emitted_by: "bin/route_issue:marker"`
on the marker-entry side (matches §K.impl.3 attribution table line 1919).

Subprocess delegation is dependency-injected via `MarkerRouteAdapter` so
tests can mock without exec'ing the real CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Protocol

from . import log_emit
from .exit_codes import EXIT_OK


class MarkerRouteAdapter(Protocol):
    """Interface for the marker-route delegation seam.

    Real-prod adapter shells out to `bin/marker create`; tests substitute
    an in-memory mock.
    """

    def invoke(
        self,
        argv: List[str],
        cwd: Path,
    ) -> "subprocess.CompletedProcess[str]":
        ...


class _SubprocessAdapter:
    """Default adapter: runs `bin/marker create <args>` via subprocess.run."""

    def invoke(self, argv: List[str], cwd: Path) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )


def run(
    *,
    prefix: str,
    trigger: str,
    context: str,
    title: Optional[str] = None,
    plan: Optional[str] = None,
    module: Optional[str] = None,
    data_needed: Optional[str] = None,
    detail: Optional[str] = None,
    allow_na: bool = False,
    dry_run: bool = False,
    json_output: bool = False,
    repo_root: Path,
    adapter: Optional[MarkerRouteAdapter] = None,
    plan_slug: Optional[str] = None,
) -> int:
    """Delegate to `bin/marker create`. Returns its exit code verbatim.

    `context` becomes the marker's `--context` field; `title` falls back
    to `context` if not provided (operator can override).
    """
    adapter = adapter or _SubprocessAdapter()
    marker_title = title or context

    argv = [
        str(repo_root / "bin" / "marker"),
        "create",
        prefix,
        marker_title,
        "--trigger", trigger,
        "--context", context,
        "--emitted-by", "bin/route_issue:route-marker",
    ]
    if plan:
        argv += ["--plan", plan]
    if module:
        argv += ["--module", module]
    if data_needed:
        argv += ["--data-needed", data_needed]
    if detail:
        argv += ["--detail", detail]
    if allow_na:
        argv += ["--allow-na"]
    if dry_run:
        argv += ["--dry-run"]
    if json_output:
        argv += ["--json"]

    result = adapter.invoke(argv, cwd=repo_root)

    # Echo subprocess output to caller
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)

    # Emit our own forensic row (marker handler emits its own row already
    # under `bin/marker:create`; ours is `bin/route_issue:marker` for the
    # delegation trail).
    plan_dir = log_emit.resolve_plan_dir(None, plan or plan_slug)
    log_emit.emit_row(
        plan_dir=plan_dir,
        plan_slug=plan or plan_slug or "scheduled_markers",
        transition_from="ready",
        transition_to="deferred",
        reason=(
            f"route_issue:marker delegated: prefix={prefix} trigger={trigger} "
            f"exit={result.returncode}"
        ),
        emitted_by=log_emit.EMIT_MARKER,
        extra={"event_type": "route_marker_delegated"},
    )

    return result.returncode
