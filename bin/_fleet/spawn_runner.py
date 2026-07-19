"""Detached runner for one headless fleet child.

`python -m bin._fleet.spawn_runner '<payload json>'` — launched by
`bin._fleet.spawn` with `start_new_session=True`, so the child outlives
the operator's parent session. It:

1. runs the `claude -p … --output-format json` subprocess (cwd = the
   adopter project root, so the child reads the project's CLAUDE.md and
   inherits the fleet protocol);
2. stores the child's full stdout at
   `docs/plans/_fleet/runs/<run_id>.json` (unique name — no shared
   write target) and its stderr at `…/<run_id>.log`;
3. appends the completion row ({session_id, total_cost_usd, is_error,
   denials, …}) to the slug's `_fleet_runs.jsonl`;
4. best-effort re-renders the hub.

Total by construction: an unparseable child result, a crashed child, or
a broken hub degrade to a "failed" ledger row / a stderr note — never a
lost run.
"""

from __future__ import annotations

import json
import subprocess
import sys


def run(payload: dict, *, claude_runner=None) -> int:
    """Execute the payload. `claude_runner` is the test seam: a callable
    (argv, cwd, log_path) -> (exit_code, stdout_str)."""
    # The runner may start before `bin` is importable state-wise; do the
    # project-scoped imports after CLAUDE_PROJECT_DIR is pinned (spawn
    # sets it in our env) so every ledger write resolves the right repo.
    from bin._fleet import engine, runs

    run_id = payload["run_id"]
    slug = payload["slug"]

    def _finish(row_extra: dict) -> None:
        row = {
            "ts": runs._now_iso(),
            "run_id": run_id,
            "slug": slug,
            "stage": payload.get("stage"),
            **row_extra,
        }
        try:
            runs.append_run(slug, row)
        except Exception as exc:  # noqa: BLE001 — last-resort surface
            print(f"fleet runner: ledger append failed: {exc}", file=sys.stderr)

    if claude_runner is None:
        def claude_runner(argv, cwd, log_path):  # pragma: no cover - real spawn
            with open(log_path, "w", encoding="utf-8") as log:
                proc = subprocess.run(
                    argv, cwd=cwd, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=log,
                )
            return proc.returncode, proc.stdout.decode("utf-8", "replace")

    try:
        exit_code, stdout = claude_runner(
            payload["argv"], payload["project_root"], payload["log_path"],
        )
    except Exception as exc:  # noqa: BLE001 — the spawn itself failed
        _finish({"event": "failed", "exit_code": -1,
                 "result_snippet": f"runner: {type(exc).__name__}: {exc}"})
        return 1

    try:
        with open(payload["out_json_path"], "w", encoding="utf-8") as f:
            f.write(stdout)
    except OSError as exc:
        print(f"fleet runner: could not store child JSON: {exc}", file=sys.stderr)

    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        _finish({
            "event": "failed",
            "exit_code": exit_code,
            "result_snippet": (stdout or "")[:200] or "(no stdout)",
        })
        return 1

    _finish({
        "event": "completed",
        "exit_code": exit_code,
        "session_id": result.get("session_id"),
        "total_cost_usd": result.get("total_cost_usd"),
        "is_error": bool(result.get("is_error")),
        "subtype": result.get("subtype"),
        "num_turns": result.get("num_turns"),
        "denials": len(result.get("permission_denials") or []),
        "result_snippet": str(result.get("result") or ""),
    })

    try:
        engine.render_hub_write()
    except Exception as exc:  # noqa: BLE001 — board is derived, never fatal
        print(f"fleet runner: render skipped ({exc})", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m bin._fleet.spawn_runner '<payload json>'",
              file=sys.stderr)
        return 1
    return run(json.loads(argv[0]))


if __name__ == "__main__":
    sys.exit(main())
