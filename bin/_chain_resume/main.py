"""CLI entry point for `bin/chain-resume` (CCOR.1 T-6).

Per implplan §T-6 + design_resolutions R-exit-codes + R-orphan-detection +
R-inject-size + R-stamp-before-release + R-pause-stays-durable-if-no-resume.
The operator runs:

    bin/chain-resume --slug <slug> [--inject "free-text"]

The CLI:

1. Resolves `plan_dir = docs/plans/<slug>/`; exits 2 if missing.
2. Reads the pause sentinel via `pause_sentinel.read`; exits
   `EXIT_NOT_PAUSED=22` if absent.
3. Reads the H.9 1C running sentinel via
   `bin._chain_overnight.sentinel.read_sentinel`; if absent OR if the
   recorded `driver_pid` is dead, exits 22 with the "crashed-during-pause"
   diagnostic. **Does NOT release the pause sentinel** in this path —
   the forensic state is preserved for the operator to investigate (per
   R-orphan-detection).
4. Verifies that the pause sentinel's `chain_id` matches the running
   lock's `chain_id`; exits 2 on mismatch.
5. If `--inject` provided:
   a. Validates the text is UTF-8 encodable.
   b. Validates the encoded size is ≤ `INJECT_MAX_BYTES` (default
      10240; settings-resolvable via `chain.pause.inject_max_bytes`).
   c. Refuses if `_operator_inject.md` already exists in `plan_dir`
      (exit 2, sentinel NOT released — the operator must hand-resolve
      the prior file).
   d. Writes the framed inject text via `write_atomic`:
      `<!-- operator-inject schema=1 written_at=<iso> -->\\n<operator-inject>\\n{text}\\n</operator-inject>\\n`
6. Calls `manifest.stamp_pause_end(plan_dir, chain_id, resumed_at)` —
   captures the pause-end delta into `total_paused_seconds`.
7. Calls `pause_sentinel.release(plan_dir, chain_id)` — verifies the
   chain_id matches and removes the sentinel.
8. Emits a `chain_resumed` orchestrator_log row (includes
   `inject_size_bytes` if `--inject` was provided).
9. Prints operator confirmation.

**Operation order is load-bearing (R-stamp-before-release).** Steps 6 →
7 → 8 happen in that exact order so that:

- If the stamp fails, the sentinel is still in place; a retry of
  `bin/chain-resume` will pick up where the stamp left off.
- If the stamp succeeds but the release fails, the manifest's
  `total_paused_seconds` is up-to-date; the orphaned sentinel can be
  cleared via `bin/chain-overnight --release-lock`.
- If the stamp + release succeed but log emission fails, the resume
  itself is durable (driver wakes up on its next pause-probe poll);
  log-row loss is forensic-only.

Exit codes (per `bin/_chain_overnight/exit_codes.py`):

- 0 (`EXIT_OK`) — pause-end stamped, sentinel released, log row emitted.
- 2 (`EXIT_DRIVER_CRASH`) — argument-shape error, plan-dir missing,
  chain_id mismatch, inject validation failure (oversize / non-UTF-8 /
  already-exists).
- 22 (`EXIT_NOT_PAUSED`) — pause sentinel absent OR orphan-paused
  (sentinel present but running lock missing / driver dead).

The CLI does NOT mutate `_state.json` or `_orchestrator_log.jsonl`
directly outside of the single `append_row` call (per plan §C.1 +
R-cli-lint-conformance sole-writer discipline).
"""

from __future__ import annotations

import argparse
import datetime
import errno
import logging
import os
import socket
import sys
from pathlib import Path
from typing import Sequence

# Module imports. Each module is single-purpose so failure modes are
# easy to reason about. Note: `manifest_mod` is imported as a thin alias
# so the test-side order-tracking spy can patch
# `chain_resume_main.manifest_mod.stamp_pause_end` cleanly.
from bin._chain_overnight import exit_codes
from bin._chain_overnight import manifest as manifest_mod
from bin._chain_overnight import sentinel as running_sentinel
from bin._chain_pause import sentinel as pause_sentinel
from bin._jsonl_log.writer import append_row
from bin._render_plan.atomic_write import write_atomic


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Repo root anchor: `bin/_chain_resume/main.py` → parents[2] = repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLANS_DIR = _REPO_ROOT / "docs" / "plans"

# `emitted_by` constant. Must match the KNOWN_WRITERS entry registered
# in `bin/_jsonl_log/writers.py` (T-5 added "bin/chain-resume").
EMITTED_BY = "bin/chain-resume"

# Event-type discriminator. Per R-event-types: `chain_resumed` joins
# the four CCOR.1 event types added in T-8's schema enum extension.
EVENT_TYPE = "chain_resumed"

# Operator-inject file basename written under `plan_dir`. Single-shot;
# T-7's driver-side `_consume_operator_inject_if_present` deletes the
# file after the next planner/opus spawn reads it.
OPERATOR_INJECT_FILENAME = "_operator_inject.md"

# R-inject-size: default 10240 bytes (10 KB). Settings-resolvable via
# `chain.pause.inject_max_bytes`. The default is the source-of-truth
# literal passed to `settings_registry.resolve` per the project's
# standing "default constant at the call site" convention (per CLAUDE.md
# memory + bin/_intent/register.py:234 precedent).
INJECT_MAX_BYTES = 10240


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the strict argparse parser.

    Per R-cli-lint-conformance (mirroring T-5's `bin/chain-pause`):
    - `allow_abbrev=False` — operators cannot abbreviate `--slug` etc.
    - Unknown flags refuse with argparse's standard exit-2 + stderr msg.
    - All flags use explicit `dest=` for clarity.
    """
    p = argparse.ArgumentParser(
        prog="bin/chain-resume",
        description=(
            "Wake a paused overnight chain at <slug>. Reads "
            "_chain_paused.lock under docs/plans/<slug>/, stamps the "
            "pause-end delta into the manifest's total_paused_seconds, "
            "and releases the pause sentinel. Optionally writes "
            "_operator_inject.md so the next planner/opus spawn picks "
            "up framed correction text (single-shot)."
        ),
        allow_abbrev=False,
    )
    p.add_argument(
        "--slug",
        required=True,
        dest="slug",
        help="Plan slug (must exist as docs/plans/<slug>/).",
    )
    p.add_argument(
        "--inject",
        default=None,
        dest="inject",
        help=(
            "Free-text correction note to feed into the next "
            "planner/opus spawn. Max 10 KB UTF-8. Refused if "
            "_operator_inject.md already exists in the plan dir."
        ),
    )
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso_z() -> str:
    """ISO-8601 UTC with trailing Z, second resolution.

    Matches the format used by `bin/_chain_overnight/sentinel.py` and
    `bin/_chain_pause/sentinel.py` so timestamps round-trip cleanly.
    """
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _resolve_plan_dir(slug: str) -> Path:
    """Resolve `docs/plans/<slug>/`; raise FileNotFoundError if missing."""
    plan_dir = _PLANS_DIR / slug
    if not plan_dir.exists():
        raise FileNotFoundError(f"plan directory does not exist: {plan_dir}")
    if not plan_dir.is_dir():
        raise NotADirectoryError(f"{plan_dir} is not a directory")
    return plan_dir


def _is_pid_alive(pid: int) -> bool:
    """Check whether `pid` is alive on the local machine.

    Defensive: signal 0 doesn't deliver anything; it triggers the kernel's
    permission/existence check only.

    Returns:
        True if the process exists (or exists but is owned by another user
        — EPERM). False on ESRCH or any other OSError.
    """
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            # Process exists but we don't own it; treat as alive.
            return True
        return False
    return True


def _resolve_inject_max_bytes() -> int:
    """Resolve `chain.pause.inject_max_bytes` via the framework-internal
    resolver.

    SC-C #3 — routes through :mod:`bin._intent.settings`, which has no
    MySQL / ``src.DAL`` / ``console`` dependency. Resolution order:

    1. ``SPLOCK_SETTING__chain__pause__inject_max_bytes`` env var
       (and the legacy ``SPLOCK_SETTING__`` alias) — operator override.
    2. JSON overlay at ``${CLAUDE_PLUGIN_DATA}/intent_settings.json``
       (5-minute process cache mirrors the original 5-min TTL).
    3. Static fallback ``INJECT_MAX_BYTES = 10240`` — the source of
       truth at the call site.

    Never raises.
    """
    try:
        from bin._intent import settings as intent_settings  # noqa: WPS433
        return int(intent_settings.resolve(
            "chain.pause.inject_max_bytes",
            INJECT_MAX_BYTES,
        ))
    except Exception:  # noqa: BLE001
        return INJECT_MAX_BYTES


def _frame_inject_text(text: str, written_at: str) -> str:
    """Compose the operator-inject file body.

    Per R-inject-framing: HTML-comment + custom-tag wrapping. The
    framing is load-bearing — T-7's driver-side consumer
    (`_consume_operator_inject_if_present`) reads the wrapped form into
    the next planner/opus prompt as-is, and the spawn-prompt-build
    layer relies on the `<operator-inject>...</operator-inject>`
    delimiters to scope the inject text (defense-in-depth analogue to
    `<research-findings>` / `<lessons-findings>` per R-no-subagent-prompt-edits
    + userguide §3.3).

    Args:
        text: Operator-supplied text. Already UTF-8 validated; raw
            characters land between the open + close tags.
        written_at: ISO-8601 UTC timestamp; stamped into the HTML
            comment for forensic traceability.

    Returns:
        The full framed body (trailing newline included).
    """
    return (
        f"<!-- operator-inject schema=1 written_at={written_at} -->\n"
        f"<operator-inject>\n{text}\n</operator-inject>\n"
    )


# ---------------------------------------------------------------------------
# Log emission
# ---------------------------------------------------------------------------


def _emit_chain_resumed_row(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    session_id: str,
    resumed_at: str,
    pause_delta_seconds: float,
    inject_size_bytes: int | None,
) -> None:
    """Emit a `chain_resumed` row to `_orchestrator_log.jsonl`.

    Per R-event-types: event_type discriminator is `chain_resumed`.
    Per R-cli-lint-conformance: this is the ONLY orchestrator-log write
    in the chain-resume CLI surface (sole-writer discipline).

    The row's `transition.from`/`transition.to` are both `wip` because
    resume is an intra-chain observability event — the chain's seven-status
    state does not change.

    Args:
        plan_dir: Plan directory containing the JSONL.
        slug: Plan slug.
        chain_id: Live chain identifier.
        session_id: Operator-CLI session id (placeholder fallback if not
            running inside a Claude Code session).
        resumed_at: ISO-8601 UTC timestamp the CLI used for the
            stamp_pause_end call.
        pause_delta_seconds: The delta returned by stamp_pause_end (for
            forensic readability — same value will appear in
            total_paused_seconds on the next manifest read).
        inject_size_bytes: Byte-count of the (UTF-8-encoded) inject text
            if `--inject` was provided; None otherwise.
    """
    reason_text = (
        f"chain_resumed pause_delta_seconds={pause_delta_seconds:.3f}"
    )
    if inject_size_bytes is not None:
        reason_text += f"; inject_size_bytes={inject_size_bytes}"
    row: dict = {
        "session_id": session_id,
        "plan_slug": slug,
        "chain_id": chain_id,
        "task_id": None,
        "transition": {"from": "wip", "to": "wip"},
        "mode_at_transition": {"overnight": True, "guardrail": False},
        "reason": reason_text,
        # Payload-level discriminator (per R-event-types). The full enum
        # surface is closed in T-8's schema update.
        "event_type": EVENT_TYPE,
        # Additive payload fields for forensic inspection.
        "pause_delta_seconds": pause_delta_seconds,
        "resumed_at": resumed_at,
    }
    if inject_size_bytes is not None:
        row["inject_size_bytes"] = inject_size_bytes
    append_row(plan_dir, row, emitted_by=EMITTED_BY)


def _operator_session_id() -> str:
    """Derive a session_id for the operator-CLI invocation.

    Per `bin/_update_orchestrator/log_emit.py::session_id`: reads
    `$CLAUDE_SESSION_ID` if set (interactive Claude Code session), else
    falls back to `sess_00000000` (placeholder used by other CLI
    surfaces when running outside a Claude session).
    """
    return os.environ.get("CLAUDE_SESSION_ID") or "sess_00000000"


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Top-level CLI entry; returns exit code.

    Returns:
        0 on successful resume (stamp + release + log emitted).
        2 on argument-shape / plan-dir / inject-validation / chain_id
          mismatch refusal.
        22 if no pause sentinel exists, OR the chain is in orphan-paused
          state (sentinel present + running lock missing / driver dead).
    """
    logging.basicConfig(
        level=os.environ.get("CHAIN_RESUME_LOG_LEVEL", "INFO"),
        format="%(asctime)s [chain-resume] %(levelname)s %(message)s",
    )

    parser = _build_parser()
    # argparse refuses unknown flags with exit 2 + stderr message; no
    # extra defensive handling needed here.
    args = parser.parse_args(argv)

    # Step 1: resolve plan_dir.
    try:
        plan_dir = _resolve_plan_dir(args.slug)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"chain-resume: {exc}", file=sys.stderr)
        return exit_codes.EXIT_DRIVER_CRASH

    # Step 2: read the pause sentinel. Absent → exit 22 (EXIT_NOT_PAUSED).
    try:
        pause_payload = pause_sentinel.read(plan_dir)
    except pause_sentinel.PauseSentinelCorruptError as exc:
        # Sentinel present but malformed — hand-resolve. Surface and
        # exit driver-crash so the operator inspects the file.
        print(
            f"chain-resume: pause sentinel exists but is corrupt at "
            f"{exc.sentinel_path}: {exc.reason}. Manually inspect/remove "
            f"the file before retrying.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_DRIVER_CRASH

    if pause_payload is None:
        print(
            f"chain-resume: no pause sentinel at {plan_dir}; nothing to "
            f"resume. If you intended to start a fresh chain, use "
            f"bin/chain-overnight.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_NOT_PAUSED

    paused_chain_id = pause_payload.get("chain_id")
    if not isinstance(paused_chain_id, str):
        print(
            f"chain-resume: pause sentinel at {plan_dir} missing or "
            f"malformed chain_id field; refusing to resume an "
            f"unidentifiable chain.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_DRIVER_CRASH

    # Step 3: read the running sentinel + orphan-detection check.
    # Per R-orphan-detection: if the pause sentinel is present but the
    # running lock is absent OR its driver_pid is dead, the chain
    # crashed during the pause window. Exit 22 with the
    # "crashed-during-pause" diagnostic; DO NOT release the pause
    # sentinel (forensic state preserved for the operator to
    # investigate — they can use `bin/chain-overnight --release-lock`
    # for a forensic reset if intended).
    running_payload = running_sentinel.read_sentinel(plan_dir)
    if running_payload is None:
        print(
            f"chain-resume: orphan-paused state detected at {plan_dir}: "
            f"pause sentinel is present but _chain_running.lock is missing. "
            f"This is a crashed-during-pause state, not a resumable pause. "
            f"Investigate _orchestrator_log.jsonl, then use "
            f"bin/chain-overnight --release-lock <chain_id> <slug> if a "
            f"forensic reset is intended. The pause sentinel is preserved.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_NOT_PAUSED

    running_chain_id = running_payload.get("chain_id")
    if not isinstance(running_chain_id, str):
        print(
            f"chain-resume: running lock at {plan_dir} present but "
            f"missing or malformed chain_id field; refusing to resume an "
            f"unidentifiable chain.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_DRIVER_CRASH

    driver_pid = running_payload.get("driver_pid")
    driver_host = running_payload.get("driver_host")
    if isinstance(driver_pid, int) and isinstance(driver_host, str):
        local_host = socket.gethostname()
        if driver_host == local_host and not _is_pid_alive(driver_pid):
            print(
                f"chain-resume: orphan-paused state detected at {plan_dir}: "
                f"pause sentinel is present but driver_pid={driver_pid} is "
                f"dead. This is a crashed-during-pause state, not a "
                f"resumable pause; the driver crashed while the chain was "
                f"paused. Investigate _orchestrator_log.jsonl, then use "
                f"bin/chain-overnight --release-lock <chain_id> <slug> if "
                f"a forensic reset is intended. The pause sentinel is "
                f"preserved.",
                file=sys.stderr,
            )
            return exit_codes.EXIT_NOT_PAUSED
        # Cross-host: best-effort warning; allow proceed (operator may
        # know the remote chain is alive; we can't probe it from here).
        if driver_host != local_host:
            logger.warning(
                "driver_host=%r differs from local_host=%r; cannot "
                "verify PID liveness across hosts. Proceeding with "
                "resume; if the driver is actually dead, the next "
                "pause-probe poll on the remote side will simply not "
                "happen and the operator will need to investigate.",
                driver_host, local_host,
            )

    # Step 4: chain_id cross-check. The pause sentinel and the running
    # lock must agree on chain_id. A mismatch implies cross-chain
    # confusion (or stale state from a prior chain) — refuse with exit 2
    # so the operator investigates rather than silently resuming the
    # wrong chain.
    if paused_chain_id != running_chain_id:
        print(
            f"chain-resume: chain_id mismatch between pause sentinel "
            f"({paused_chain_id!r}) and running lock ({running_chain_id!r}) "
            f"at {plan_dir}. Refusing to resume — this looks like cross-chain "
            f"state. Investigate before proceeding.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_DRIVER_CRASH

    # Step 5: optional inject validation + write. We validate BEFORE any
    # state mutation (stamp / release) so an inject failure doesn't
    # leave the chain half-resumed.
    inject_size_bytes: int | None = None
    if args.inject is not None:
        inject_text = args.inject
        # 5a: UTF-8 encodability. argparse hands us a str (already
        # decoded by Python from the OS arg), so encode-failures are
        # surrogate-related. Use `errors="strict"` to surface them.
        try:
            encoded = inject_text.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            print(
                f"chain-resume: --inject must be valid UTF-8; got "
                f"UnicodeEncodeError: {exc}",
                file=sys.stderr,
            )
            return exit_codes.EXIT_DRIVER_CRASH

        # 5b: Size cap. The byte-length is what matters for downstream
        # prompt-construction (token budgets are byte-shape-sensitive).
        max_bytes = _resolve_inject_max_bytes()
        encoded_len = len(encoded)
        if encoded_len > max_bytes:
            print(
                f"chain-resume: inject too large ({encoded_len} bytes > "
                f"{max_bytes}); split into focused notes or use "
                f"bin/route_issue for the broader correction.",
                file=sys.stderr,
            )
            return exit_codes.EXIT_DRIVER_CRASH

        # 5c: No-overwrite. A pre-existing `_operator_inject.md` means a
        # prior `bin/chain-resume --inject` ran and the driver hasn't
        # consumed the file yet. Refuse rather than overwrite — the
        # operator should hand-resolve (read the prior file, merge,
        # delete, retry). Per R-inject-no-overwrite.
        inject_path = plan_dir / OPERATOR_INJECT_FILENAME
        if inject_path.exists():
            print(
                f"chain-resume: {OPERATOR_INJECT_FILENAME} already exists "
                f"at {inject_path}; the driver hasn't consumed the prior "
                f"inject yet. Read the file, merge if needed, delete it, "
                f"and retry. The pause sentinel is preserved.",
                file=sys.stderr,
            )
            return exit_codes.EXIT_DRIVER_CRASH

        # 5d: Write the framed inject body via the shared atomic helper.
        # R-inject-framing: HTML-comment + custom-tag.
        written_at = _now_iso_z()
        framed = _frame_inject_text(inject_text, written_at)
        try:
            write_atomic(inject_path, framed)
        except OSError as exc:
            # write_atomic raises AtomicWriteError (an OSError subclass)
            # on temp/rename failures. Surface and exit driver-crash;
            # the sentinel is preserved (we haven't called stamp_pause_end
            # or release yet).
            print(
                f"chain-resume: failed to write {inject_path}: {exc}",
                file=sys.stderr,
            )
            return exit_codes.EXIT_DRIVER_CRASH
        inject_size_bytes = encoded_len

    # Step 6: stamp the pause-end delta into the manifest BEFORE
    # releasing the sentinel (R-stamp-before-release). If stamp_pause_end
    # raises after the sentinel has been released, the pause window is
    # never accounted for in `total_paused_seconds` — the wall-clock
    # cap math would NOT subtract it on the next phase boundary. By
    # stamping first, a stamp failure leaves the sentinel intact and
    # the operator can retry `bin/chain-resume`.
    resumed_at = _now_iso_z()
    try:
        pause_delta_seconds = manifest_mod.stamp_pause_end(
            plan_dir,
            chain_id=paused_chain_id,
            resumed_at=resumed_at,
        )
    except (manifest_mod.ManifestNotFoundError, ValueError) as exc:
        # Manifest missing or stamp_pause_end refusing (no active pause /
        # chain_id mismatch on the manifest side). Surface to stderr; the
        # pause sentinel is preserved so the operator can investigate.
        print(
            f"chain-resume: failed to stamp pause-end into manifest: "
            f"{exc}. The pause sentinel is preserved; investigate "
            f"_chain_sessions.json and retry.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_DRIVER_CRASH

    # Step 7: release the pause sentinel. The release call verifies
    # chain_id matches (pause_sentinel.release raises
    # PauseChainIdMismatchError without removing on mismatch). We've
    # already checked chain_id cross-lock at step 4, so a mismatch here
    # would be a race (operator manually overwrote the sentinel between
    # step 2 and step 7) — exit 2 with diagnostic.
    try:
        pause_sentinel.release(plan_dir, chain_id=paused_chain_id)
    except pause_sentinel.PauseChainIdMismatchError as exc:
        print(
            f"chain-resume: pause sentinel chain_id mismatch on release: "
            f"{exc}. The sentinel was NOT removed. The pause-end delta "
            f"has already been stamped into the manifest; you may need "
            f"to manually reconcile.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_DRIVER_CRASH
    except pause_sentinel.PauseSentinelCorruptError as exc:
        # The sentinel went corrupt between step 2's read and step 7's
        # release — concurrent modification race. The stamp already
        # happened; the sentinel will need hand-removal.
        print(
            f"chain-resume: pause sentinel corrupt on release: "
            f"{exc.sentinel_path}: {exc.reason}. The pause-end delta "
            f"has been stamped; manually remove the sentinel file.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_DRIVER_CRASH
    except FileNotFoundError:
        # Race: sentinel removed between our read (step 2) and release
        # (step 7). Treat as success — the post-condition (sentinel
        # gone) is met. Log for forensic visibility but do not refuse.
        logger.warning(
            "pause sentinel removed between read and release at %s; "
            "treating as success (race / external removal)",
            plan_dir,
        )

    # Step 8: emit the chain_resumed orchestrator-log row. Sole-writer
    # discipline (per R-cli-lint-conformance) — this is the ONE write to
    # the JSONL that the chain-resume CLI performs.
    session_id = _operator_session_id()
    try:
        _emit_chain_resumed_row(
            plan_dir,
            slug=args.slug,
            chain_id=paused_chain_id,
            session_id=session_id,
            resumed_at=resumed_at,
            pause_delta_seconds=pause_delta_seconds,
            inject_size_bytes=inject_size_bytes,
        )
    except Exception as exc:  # noqa: BLE001 — log row failure is non-fatal
        # The stamp + release already succeeded; log-row emission failure
        # is forensic-only. Surface to stderr but still exit OK — the
        # resume itself is durable.
        logger.error(
            "chain_resumed log row emission failed: %s; the resume "
            "itself succeeded (sentinel released + manifest stamped), "
            "but the audit row is missing.",
            exc,
        )

    # Step 9: operator confirmation.
    parts = [
        f"chain {paused_chain_id} resumed at {resumed_at}",
        f"pause delta {pause_delta_seconds:.1f}s",
    ]
    if inject_size_bytes is not None:
        parts.append(f"inject {inject_size_bytes} bytes queued")
    print("; ".join(parts) + ".")
    return exit_codes.EXIT_OK


if __name__ == "__main__":  # pragma: no cover — module entry
    sys.exit(main(sys.argv[1:]))
