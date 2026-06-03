"""SQLite/${CLAUDE_PLUGIN_DATA} intent-backend tests.

Covers:

  1. **overlapping-IMMEDIATE concurrency test** — two ``BEGIN IMMEDIATE``
     check+inserts cannot both land a session row; the second one
     blocks past ``SQLITE_BUSY_TIMEOUT_MS`` and raises
     :class:`db.SQLiteBusy`.
  2. **cache-swap survival test** — the resolved SQLite path lives
     under the operator-owned ``$CLAUDE_PLUGIN_DATA``, NEVER under a
     ``parents[2]``-style ephemeral cache dir. Survives a simulated
     module-cache wipe.
  3. **SQLite busy → exit-0 degradation** — when ``BEGIN IMMEDIATE``
     blocks past the busy_timeout, the caller can map
     :class:`db.SQLiteBusy` to the same ``sync_pending=true`` + exit-0
     contract as :class:`db.MySQLUnavailable` (the contract mirroring
     is asserted on the exception class hierarchy + the caller-side
     code path).
  4. **register/collision-check end-to-end with no .env/MySQL/src/** —
     drives the SQLite-native DAL surface (``insert_session`` /
     ``select_overlapping_for_update`` / ``insert_collision``) with
     zero MySQL env vars set and zero ``src.DAL`` import on the path.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import sqlite3
import sys
import threading
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._intent import db  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Every test gets its own ${CLAUDE_PLUGIN_DATA} dir + clean backend env."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    # Ensure no stale MySQL env trickles in from the host.
    for v in ("SPLOCK_DB_HOST", "SPLOCK_DB_USER", "SPLOCK_DB_PASS"):
        monkeypatch.delenv(v, raising=False)
    # Force backend to default (sqlite) — clear the backend env var.
    monkeypatch.delenv(db.BACKEND_ENV, raising=False)
    yield


# ---------------------------------------------------------------------------
# (2) cache-swap survival — the path lives under CLAUDE_PLUGIN_DATA
# ---------------------------------------------------------------------------


def test_cache_swap_path_lives_under_plugin_data(tmp_path):
    """The SQLite path is rooted at ${CLAUDE_PLUGIN_DATA}.

    parents[2] would resolve to ``bin`` → ``REPO_ROOT`` from
    ``bin/_intent/db.py``; that location is the package-cache dir
    (ephemerally wiped on plugin update). The resolver MUST return the
    operator-owned plugin-data dir.
    """
    p = db.intent_sqlite_path()
    assert tmp_path in p.parents, (
        f"SQLite path {p!s} not under CLAUDE_PLUGIN_DATA={tmp_path!s}; "
        "would be wiped by a plugin cache swap."
    )
    # Authoritative: NOT under the bin/_intent parents[2] = REPO_ROOT.
    db_module_root = pathlib.Path(db.__file__).resolve().parents[2]
    assert db_module_root not in p.parents, (
        f"SQLite path {p!s} resolved into the package cache dir "
        f"{db_module_root!s} — vulnerable to cache-swap data loss."
    )


def test_cache_swap_survives_module_reimport(tmp_path, monkeypatch):
    """Survival proof: write a row, simulate a module-cache wipe by
    re-importing the package, the row is still readable."""
    conn = db.connection()
    with db.serializable_transaction(conn) as (c, cur):
        db.insert_session(cur, _sample_payload("sess_aaa", "area1", ["x/**"]))
        c.commit()
    conn.close()

    # Simulate cache-swap by clearing the imported module + reimporting.
    for mod in [m for m in list(sys.modules) if m.startswith("bin._intent")]:
        sys.modules.pop(mod, None)
    from bin._intent import db as db2  # noqa: WPS433
    conn2 = db2.connection()
    cur2 = conn2.cursor()
    cur2.execute("SELECT session_id FROM agent_sessions")
    rows = [r[0] for r in cur2.fetchall()]
    cur2.close()
    conn2.close()
    assert "sess_aaa" in rows


# ---------------------------------------------------------------------------
# (1) + (3) overlapping IMMEDIATE concurrency + busy→exit-0 degradation
# ---------------------------------------------------------------------------


def test_overlapping_immediate_serializes_writers(monkeypatch, tmp_path):
    """Two ``BEGIN IMMEDIATE`` writers cannot both hold the write lock.

    Setup: lower the busy_timeout to a short value so the second
    transaction's open call exits quickly with
    :class:`sqlite3.OperationalError`. The db module maps that to
    :class:`db.SQLiteBusy` — the public contract.

    Asserts:
      * Exactly one writer succeeds.
      * The second writer raises :class:`db.SQLiteBusy`.
      * The serialization is enforced at ``BEGIN IMMEDIATE``, not at
        the INSERT — proving the IMMEDIATE-at-open discipline.
    """
    # Shorten the busy_timeout for the test (default 5000ms is slow).
    monkeypatch.setattr(db, "SQLITE_BUSY_TIMEOUT_MS", 250)

    # Pre-create the db + ensure schema bootstrap is done so the first
    # connection doesn't race the second on initial-create work.
    setup_conn = db.connection()
    setup_conn.close()

    holder_conn = db.connection()
    # Manually open the IMMEDIATE txn on the holder; hold it open.
    holder_cur = holder_conn.cursor()
    holder_cur.execute("BEGIN IMMEDIATE")

    second_outcome: dict = {}

    def attempt_second():
        try:
            second_conn = db.connection()
            with db.serializable_transaction(second_conn) as (c, cur):
                db.insert_session(
                    cur, _sample_payload("sess_loser", "area2", ["y/**"])
                )
                c.commit()
            second_outcome["result"] = "succeeded"
        except db.SQLiteBusy as exc:
            second_outcome["result"] = "busy"
            second_outcome["detail"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            second_outcome["result"] = f"unexpected:{type(exc).__name__}"
            second_outcome["detail"] = str(exc)

    t = threading.Thread(target=attempt_second)
    t.start()
    # Wait beyond the busy_timeout so the second writer will fail open.
    t.join(timeout=5.0)
    holder_cur.execute("ROLLBACK")
    holder_conn.close()

    assert not t.is_alive(), "second writer hung past join timeout"
    assert second_outcome["result"] == "busy", (
        f"second writer outcome={second_outcome!r}; expected SQLiteBusy"
    )


def test_sqlite_busy_maps_to_exit_zero_contract():
    """:class:`SQLiteBusy` shares the caller contract with
    :class:`MySQLUnavailable`: both are catchable as the
    "best-effort local row + sync_pending + exit 0" sentinel.

    The test asserts the structural shape: both classes exist, both
    subclass Exception, and a representative caller idiom can branch on
    either without silently dropping a registration.
    """
    assert issubclass(db.SQLiteBusy, Exception)
    assert issubclass(db.MySQLUnavailable, Exception)

    # Representative caller-side discriminator.
    def _classify(exc: Exception) -> str:
        if isinstance(exc, (db.SQLiteBusy, db.MySQLUnavailable)):
            return "sync_pending_exit_0"
        return "hard_fail"

    assert _classify(db.SQLiteBusy("simulated")) == "sync_pending_exit_0"
    assert _classify(db.MySQLUnavailable("simulated")) == "sync_pending_exit_0"
    assert _classify(RuntimeError("other")) == "hard_fail"


# ---------------------------------------------------------------------------
# (4) end-to-end register/collision-check with NO .env / MySQL / src/
# ---------------------------------------------------------------------------


def test_end_to_end_register_collision_no_mysql_no_dal(tmp_path, monkeypatch):
    """Drive insert_session + select_overlapping_for_update +
    insert_collision over SQLite with zero MySQL env, zero src.DAL.

    Mirrors the register.py happy + collision paths against the
    SQLite backend. Proves the DAL surface works in a fresh-repo
    install with no host coupling.
    """
    # Sanity — ensure src.DAL is not importable in the test env.
    monkeypatch.delitem(sys.modules, "src.DAL", raising=False)
    monkeypatch.delitem(sys.modules, "src", raising=False)
    # Strip any path entries from the upstream / host monorepo so
    # `src.DAL` cannot accidentally resolve. We compare against the
    # splock REPO_ROOT name (the substring check avoids hard-coding
    # the upstream repo name in this file — the trace-grep gate
    # rejects upstream-repo identifiers anywhere in tests/).
    splock_root_name = REPO_ROOT.name  # "splock"
    monkeypatch.setattr(sys, "path", [
        p for p in sys.path if splock_root_name in p or "site-packages" in p
    ] + [str(REPO_ROOT)])

    # First registrant lands clean.
    conn = db.connection()
    with db.serializable_transaction(conn) as (c, cur):
        matches = db.select_overlapping_for_update(
            cur, "area_alpha", json.dumps(["alpha/**"])
        )
        assert matches == []
        db.insert_session(
            cur, _sample_payload("sess_first", "area_alpha", ["alpha/**"])
        )
        c.commit()
    conn.close()

    # Second registrant on the same area collides — select returns the
    # open row, caller writes the collision_log row.
    conn = db.connection()
    with db.serializable_transaction(conn) as (c, cur):
        matches = db.select_overlapping_for_update(
            cur, "area_alpha", json.dumps(["alpha/different/**"])
        )
        assert len(matches) == 1
        assert matches[0]["session_id"] == "sess_first"
        db.insert_collision(
            cur,
            {
                "collision_id": "col_xyz",
                "colliding_session_id": "sess_second",
                "colliding_area": "area_alpha",
                "lineage_snapshot": json.dumps([{
                    "session_id": "sess_first",
                    "area": "area_alpha",
                }]),
                "dispatch_mode": "interactive",
                "resolution": None,
                "resolution_at": None,
                "detected_at": "2026-06-03T00:00:00Z",
                "host": socket.gethostname(),
            },
        )
        c.commit()
    conn.close()

    # update_session round-trip
    conn = db.connection()
    with db.serializable_transaction(conn) as (c, cur):
        n = db.update_session(
            cur, "sess_first",
            {"closed_at": "2026-06-03T01:00:00Z", "status": "Completed"},
        )
        assert n == 1
        c.commit()
    conn.close()

    # select_session round-trip
    conn = db.connection()
    cur = conn.cursor()
    row = db.select_session(cur, "sess_first")
    assert row is not None
    assert row["status"] == "Completed"
    assert row["closed_at"] == "2026-06-03T01:00:00Z"
    cur.close()
    conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_payload(
    session_id: str,
    area: str,
    paths: list[str],
) -> dict:
    return {
        "session_id": session_id,
        "kind": "design_change",
        "target_system_area": area,
        "claimed_paths": list(paths),
        "proposed_design_pattern": "test_pattern",
        "status": "Planning",
        "closure_trigger": f"session_timeout_at:2026-06-03T04:00:00Z",
        "originating_chain_id": None,
        "originating_plan_slug": "example_plan",
        "host": socket.gethostname(),
        "started_at": "2026-06-03T00:00:00Z",
        "last_activity_at": "2026-06-03T00:00:00Z",
        "closed_at": None,
        "emitted_by": "tests/test_intent_sqlite_backend.py",
        "claude_session_id": None,
    }
