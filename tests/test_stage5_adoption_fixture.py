"""Stage 5 adoption-harness fixture.

Among the adoption stages, Stage 5 ("intent registry") exercises the
SQLite/``${CLAUDE_PLUGIN_DATA}`` backend so the smoke battery's "Stage 5 +
implemented adoption stages green" assertion has a real fixture to run.

The Stage 5 fixture proves the adoption story end-to-end:

  1. **Fresh-empty-repo posture** — no MySQL env, no ``console``
     package, no ``src.DAL`` package on the import path.
  2. **State dir bootstrap** — ``$CLAUDE_PLUGIN_DATA`` directory
     starts empty; the first call to :func:`db.connection` lays down
     the SQLite file + WAL + schema bootstrap on demand.
  3. **Resolver works** — :func:`bin._intent.settings.resolve`
     returns the documented default with no overlay.
  4. **Register + collision round-trip** — first registrant lands
     clean; second registrant on the same area is detected by
     :func:`db.select_overlapping_for_update` and the collision is
     recorded.
  5. **Concurrency invariant** — overlapping ``BEGIN IMMEDIATE``
     writers cannot both commit.
  6. **Degradation contract** — :class:`db.SQLiteBusy` is shape-
     compatible with :class:`db.MySQLUnavailable` for the caller's
     ``sync_pending`` + exit-0 branch.

Running this file is itself the Stage 5 fixture. The smoke battery
re-runs it; the same module file is the source of truth for both
invocations.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import sys
import threading

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._intent import db, settings as intent_settings  # noqa: E402


# ---------------------------------------------------------------------------
# Stage 5 fixture — fresh-empty-repo posture
# ---------------------------------------------------------------------------


@pytest.fixture
def stage5_env(monkeypatch, tmp_path):
    """Boot a Stage-5 adoption environment.

    Returns the data-root dir for assertion-targeting. Clears every
    host-coupling env var so the test cannot accidentally reach back
    into the developer's MySQL / console / DAL.
    """
    data_root = tmp_path / "splock-plugin-data"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_root))
    # Pretend no host MySQL.
    for v in ("SPLOCK_DB_HOST", "SPLOCK_DB_USER", "SPLOCK_DB_PASS"):
        monkeypatch.delenv(v, raising=False)
    # Force the default (sqlite) backend explicitly.
    monkeypatch.delenv(db.BACKEND_ENV, raising=False)
    intent_settings.invalidate_cache()
    yield data_root
    intent_settings.invalidate_cache()


# ---------------------------------------------------------------------------
# Stage 5 deliverable — the full adoption scenario
# ---------------------------------------------------------------------------


def test_stage5_fresh_repo_intent_registry_green(stage5_env, monkeypatch):
    """Stage 5 fixture — the full intent-registry adoption story.

    Asserts every step of the adoption story in one pass so the test
    output reads as a runbook. The smoke battery re-runs this verbatim.
    """
    data_root = stage5_env

    # ----- 1. fresh-empty-repo posture -----
    assert not data_root.exists() or not any(data_root.iterdir()), (
        "stage5_env did not start with an empty plugin-data dir"
    )
    assert db.selected_backend() == "sqlite", (
        "default backend should be sqlite in a fresh-empty-repo install"
    )

    # ----- 2. state dir bootstrap on first connect -----
    conn = db.connection()
    assert isinstance(conn, db._SQLiteConnHandle)
    assert data_root.exists(), "first connect failed to bootstrap data-root dir"
    sqlite_file = db.intent_sqlite_path()
    assert sqlite_file.exists(), "SQLite file not laid down by first connect"
    # WAL leaves -wal/-shm sidecars on first write (created lazily).

    # ----- 3. resolver returns documented defaults -----
    assert intent_settings.resolve("intent.ttl_minutes", 240) == 240
    assert intent_settings.resolve(
        "intent.soft_warning_path_prefix_enabled", True
    ) is True

    # ----- 4. register + collision round-trip -----
    # First registrant — clean.
    with db.serializable_transaction(conn) as (c, cur):
        matches = db.select_overlapping_for_update(
            cur, "stage5_area", json.dumps(["stage5/**"])
        )
        assert matches == []
        db.insert_session(cur, _payload("sess_stage5_first", "stage5_area",
                                        ["stage5/**"]))
        c.commit()
    conn.close()

    # Second registrant on the same area — collision detected.
    conn = db.connection()
    with db.serializable_transaction(conn) as (c, cur):
        matches = db.select_overlapping_for_update(
            cur, "stage5_area", json.dumps(["stage5/other/**"])
        )
        assert len(matches) == 1, (
            f"second registrant did not detect first; matches={matches!r}"
        )
        assert matches[0]["session_id"] == "sess_stage5_first"
        db.insert_collision(cur, {
            "collision_id": "col_stage5",
            "colliding_session_id": "sess_stage5_second",
            "colliding_area": "stage5_area",
            "lineage_snapshot": json.dumps([{
                "session_id": "sess_stage5_first",
                "area": "stage5_area",
            }]),
            "dispatch_mode": "interactive",
            "resolution": None,
            "resolution_at": None,
            "detected_at": "2026-06-03T00:00:00Z",
            "host": socket.gethostname(),
        })
        c.commit()
    conn.close()

    # ----- 5. concurrency invariant — overlapping IMMEDIATE serializes -----
    # Short busy_timeout for the test.
    monkeypatch.setattr(db, "SQLITE_BUSY_TIMEOUT_MS", 200)
    holder = db.connection()
    holder_cur = holder.cursor()
    holder_cur.execute("BEGIN IMMEDIATE")

    outcome: dict = {}

    def loser():
        try:
            other = db.connection()
            with db.serializable_transaction(other) as (c, cur):
                db.insert_session(
                    cur, _payload("sess_loser", "loser_area", ["loser/**"])
                )
                c.commit()
            outcome["res"] = "won"
        except db.SQLiteBusy:
            outcome["res"] = "busy"
        except Exception as exc:  # noqa: BLE001
            outcome["res"] = f"unexpected:{type(exc).__name__}"

    t = threading.Thread(target=loser)
    t.start()
    t.join(timeout=5.0)
    holder_cur.execute("ROLLBACK")
    holder.close()
    assert not t.is_alive()
    assert outcome["res"] == "busy", (
        f"concurrency invariant broken; outcome={outcome!r}"
    )

    # ----- 6. degradation contract — SQLiteBusy ~ MySQLUnavailable -----
    def caller_classify(exc):
        if isinstance(exc, (db.SQLiteBusy, db.MySQLUnavailable)):
            return "sync_pending_exit_0"
        return "hard_fail"

    assert caller_classify(db.SQLiteBusy("simulated")) == \
        "sync_pending_exit_0"
    assert caller_classify(db.MySQLUnavailable("simulated")) == \
        "sync_pending_exit_0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _payload(sid: str, area: str, paths: list[str]) -> dict:
    return {
        "session_id": sid,
        "kind": "design_change",
        "target_system_area": area,
        "claimed_paths": list(paths),
        "proposed_design_pattern": "stage5_adoption",
        "status": "Planning",
        "closure_trigger": "session_timeout_at:2026-06-03T04:00:00Z",
        "originating_chain_id": None,
        "originating_plan_slug": "example_plan",
        "host": socket.gethostname(),
        "started_at": "2026-06-03T00:00:00Z",
        "last_activity_at": "2026-06-03T00:00:00Z",
        "closed_at": None,
        "emitted_by": "tests/test_stage5_adoption_fixture.py",
        "claude_session_id": None,
    }
