"""Backend-pluggable DAL surface for ``bin/intent``.

The intent / collision registry MUST run with zero database, zero host
DAL, zero ``console/`` import in a fresh repo. Default backend is SQLite
(WAL + BEGIN IMMEDIATE + busy_timeout~5000ms via stdlib ``sqlite3``);
MySQL is an opt-in adapter. Backend selection is via the
``SPLOCK_INTENT_BACKEND`` env var.

Atomicity contract preserved across both backends:

  * MySQL — ``SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE`` +
    ``START TRANSACTION`` + ``SELECT ... FOR UPDATE``.
  * SQLite — ``PRAGMA journal_mode=WAL`` + ``PRAGMA busy_timeout`` +
    ``BEGIN IMMEDIATE`` around the check + insert. BEGIN IMMEDIATE
    promotes the connection to the write-lock holder at txn open
    (rather than waiting for the first INSERT), so the second
    concurrent registrant blocks on the busy_timeout window
    instead of racing to a duplicate insert. WAL keeps reader
    concurrency wide while serializing writers — same end-state
    invariant as the MySQL FOR-UPDATE range lock.

Path routing (SC-C #4): the SQLite file lives at
``${CLAUDE_PLUGIN_DATA}/intent_local.sqlite3`` (fallback
``${CLAUDE_PROJECT_DIR}/.plugin-data/intent_local.sqlite3``,
fallback CWD-relative ``.plugin-data/intent_local.sqlite3``). NEVER
under ``parents[2]`` (the ephemeral cache dir wiped ~7 days
post-update). The path is exposed via :func:`intent_sqlite_path`
for tests + the seal-glob lockstep audit.

Degradation contract (SC-C #6): ``sqlite3.OperationalError`` raised
on busy-timeout expiry maps to :class:`SQLiteBusy` which the caller
catches the SAME WAY as :class:`MySQLUnavailable` — flips the local
JSONL row to ``sync_pending=true`` + exit 0. Atomicity holds at the
cost of write-serialization.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import pathlib
import sqlite3
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

BACKEND_ENV = "SPLOCK_INTENT_BACKEND"
DEFAULT_BACKEND = "sqlite"
VALID_BACKENDS = ("sqlite", "jsonl", "mysql")


def selected_backend() -> str:
    """Return the active backend name (``sqlite`` / ``jsonl`` / ``mysql``).

    Resolution order:
      1. ``SPLOCK_INTENT_BACKEND`` env var.
      2. :data:`DEFAULT_BACKEND` (``sqlite``).

    Unknown values fall through to the default.
    """
    raw = os.environ.get(BACKEND_ENV) or DEFAULT_BACKEND
    raw = raw.strip().lower()
    if raw not in VALID_BACKENDS:
        return DEFAULT_BACKEND
    return raw


# ---------------------------------------------------------------------------
# Path routing — ${CLAUDE_PLUGIN_DATA} -> ${CLAUDE_PROJECT_DIR}/.plugin-data
# ---------------------------------------------------------------------------

SQLITE_FILE_NAME = "intent_local.sqlite3"
PLUGIN_DATA_DIR_NAME = ".plugin-data"


def resolve_data_root(data_root: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Resolve the directory that holds intent state.

    Precedence:
      1. Explicit ``data_root`` argument (tests + caller-injected paths).
      2. ``$CLAUDE_PLUGIN_DATA`` env var (Claude Code plugin runtime
         contract; cache-swap survives because this dir is operator-owned,
         not the ``parents[2]`` cache).
      3. ``$CLAUDE_PROJECT_DIR/.plugin-data`` (project-local fallback).
      4. ``./.plugin-data`` (CWD-relative last resort).

    The returned directory is **not** created here — callers that write
    must mkdir on demand (we don't want a pure resolver to have side
    effects on read paths).
    """
    if data_root is not None:
        return pathlib.Path(data_root)
    env = os.environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    if env:
        return pathlib.Path(env)
    project = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if project:
        return pathlib.Path(project) / PLUGIN_DATA_DIR_NAME
    return pathlib.Path.cwd() / PLUGIN_DATA_DIR_NAME


def intent_sqlite_path(data_root: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Resolve the SQLite database file location.

    Co-located with the intent_local.jsonl mirror under the same
    data-root (SC-C #5: the seal-glob tracks the same directory).
    """
    return resolve_data_root(data_root) / SQLITE_FILE_NAME


# ---------------------------------------------------------------------------
# Error contract
# ---------------------------------------------------------------------------


class MySQLUnavailable(Exception):
    """MySQL client missing, env vars unset, or connect failed. Caller
    flips the local-JSONL row to ``sync_pending`` and exits 0 per
    P.impl.8."""


class SQLiteBusy(Exception):
    """SQLite ``BEGIN IMMEDIATE`` / write step blocked past the
    ``busy_timeout`` window. Per SC-C #6 mapped to the same caller
    contract as :class:`MySQLUnavailable` — best-effort local row +
    ``sync_pending`` + exit 0 (atomicity holds at cost of
    write-serialization).
    """


# ---------------------------------------------------------------------------
# SQLite schema (idempotent first-connect bootstrap)
# ---------------------------------------------------------------------------

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    target_system_area TEXT NOT NULL,
    claimed_paths TEXT NOT NULL,
    proposed_design_pattern TEXT,
    status TEXT NOT NULL,
    closure_trigger TEXT,
    originating_chain_id TEXT,
    originating_plan_slug TEXT,
    host TEXT NOT NULL,
    started_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    closed_at TEXT,
    emitted_by TEXT NOT NULL,
    claude_session_id TEXT
);
CREATE INDEX IF NOT EXISTS agent_sessions_area_idx
    ON agent_sessions (target_system_area, closed_at);
CREATE INDEX IF NOT EXISTS agent_sessions_host_idx
    ON agent_sessions (host, closed_at);

CREATE TABLE IF NOT EXISTS agent_session_collision_log (
    collision_id TEXT PRIMARY KEY,
    colliding_session_id TEXT NOT NULL,
    colliding_area TEXT NOT NULL,
    lineage_snapshot TEXT NOT NULL,
    dispatch_mode TEXT NOT NULL,
    resolution TEXT,
    resolution_at TEXT,
    detected_at TEXT NOT NULL,
    host TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intent_event_log (
    event_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    session_id TEXT,
    collision_id TEXT,
    payload TEXT,
    emitted_by TEXT NOT NULL,
    host TEXT NOT NULL,
    emitted_at TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------


SQLITE_BUSY_TIMEOUT_MS = 5000


def _sqlite_connect(path: pathlib.Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL + busy_timeout + schema bootstrap."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # ``isolation_level=None`` puts the connection in autocommit so we
    # can issue ``BEGIN IMMEDIATE`` explicitly per-transaction; the
    # stdlib default opens an implicit DEFERRED txn before every DML
    # statement, which would defeat the IMMEDIATE-at-open contract.
    conn = sqlite3.connect(
        str(path),
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1000.0,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.executescript(SQLITE_SCHEMA)
    finally:
        cur.close()
    return conn


# ---------------------------------------------------------------------------
# MySQL backend (opt-in)
# ---------------------------------------------------------------------------


def _admin_creds() -> tuple[str, str, str]:
    host = os.environ.get("SPLOCK_DB_HOST", "")
    user = os.environ.get("SPLOCK_DB_USER", "")
    password = os.environ.get("SPLOCK_DB_PASS", "")
    if not host or not user:
        raise MySQLUnavailable(
            "SPLOCK_DB_HOST / SPLOCK_DB_USER not set; cannot connect to MySQL"
        )
    return host, user, password


def _mysql_connect():
    try:
        import mysql.connector  # type: ignore[import-untyped]
    except ImportError as exc:
        raise MySQLUnavailable(
            f"mysql.connector not installed: {exc}"
        ) from exc
    host, user, password = _admin_creds()
    try:
        return mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            connection_timeout=10,
            autocommit=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise MySQLUnavailable(f"connect failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Unified connection / transaction surface
# ---------------------------------------------------------------------------


class _SQLiteConnHandle:
    """Wrapper exposing a uniform ``cursor(dictionary=True)`` /
    ``commit`` / ``rollback`` / ``close`` surface across backends.

    SQLite's ``sqlite3.Connection`` already provides these methods
    (modulo the ``dictionary=True`` cursor kwarg used by the MySQL
    connector); the wrapper accepts + ignores that kwarg so call sites
    stay backend-agnostic.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self.backend = "sqlite"

    def cursor(self, dictionary: bool = False):  # noqa: ARG002
        return self._conn.cursor()

    def commit(self) -> None:
        # In autocommit mode the explicit COMMIT terminates the
        # IMMEDIATE-opened txn.
        try:
            self._conn.execute("COMMIT")
        except sqlite3.OperationalError:
            # No txn open; harmless.
            pass

    def rollback(self) -> None:
        try:
            self._conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass

    def close(self) -> None:
        self._conn.close()

    @property
    def raw(self) -> sqlite3.Connection:
        return self._conn

    # Context-manager protocol so ``with db.connection() as conn:`` works
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc:
            self.rollback()
        self.close()
        return False


def connection(data_root: Optional[pathlib.Path] = None):
    """Return a backend-appropriate connection handle.

    Backend dispatch via :func:`selected_backend`. SQLite returns a
    :class:`_SQLiteConnHandle` wrapper; MySQL returns the raw
    ``mysql.connector`` connection. Both implement context-manager +
    ``commit``/``rollback``/``close``.

    ``data_root`` is honored on the SQLite path (override the resolved
    plugin-data dir). Ignored on MySQL (host comes from env).
    """
    backend = selected_backend()
    if backend == "mysql":
        return _mysql_connect()
    if backend == "jsonl":
        # JSONL backend has no SQL surface; raise to push callers to
        # the local-JSONL fast path (mirrors MySQLUnavailable contract).
        raise SQLiteBusy(
            "intent backend=jsonl — caller must use jsonl_writer directly"
        )
    # default = sqlite
    try:
        raw = _sqlite_connect(intent_sqlite_path(data_root))
    except sqlite3.OperationalError as exc:
        raise SQLiteBusy(f"sqlite connect failed: {exc}") from exc
    return _SQLiteConnHandle(raw)


@contextlib.contextmanager
def serializable_transaction(
    conn=None,
    *,
    data_root: Optional[pathlib.Path] = None,
) -> Iterator[tuple]:
    """Open a backend-appropriate write transaction.

    On SQLite: ``BEGIN IMMEDIATE`` (the busy_timeout window applies).
    On MySQL: ``SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE``
    + ``START TRANSACTION``.

    Yields ``(conn, cursor)``. Caller is responsible for ``commit()``;
    the context manager rolls back + closes on exception, closes the
    cursor on exit, and closes the connection only if it was opened
    here.

    SQLite-specific: ``sqlite3.OperationalError`` from BEGIN IMMEDIATE
    (busy_timeout expiry) is mapped to :class:`SQLiteBusy` so the
    caller's degradation path mirrors :class:`MySQLUnavailable`.
    """
    own_conn = conn is None
    if own_conn:
        conn = connection(data_root=data_root)

    backend = (
        "sqlite" if isinstance(conn, _SQLiteConnHandle) else "mysql"
    )
    cursor = conn.cursor(dictionary=True) if backend == "mysql" else conn.cursor()
    try:
        if backend == "sqlite":
            try:
                cursor.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise SQLiteBusy(
                    f"BEGIN IMMEDIATE blocked beyond busy_timeout: {exc}"
                ) from exc
        else:
            cursor.execute(
                "SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE"
            )
            cursor.execute("START TRANSACTION")
        yield conn, cursor
    except Exception:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        try:
            cursor.close()
        except Exception:  # noqa: BLE001
            pass
        if own_conn:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# DML helpers — backend-aware SQL dispatch
# ---------------------------------------------------------------------------


def _is_sqlite_cursor(cursor) -> bool:
    """Return True when ``cursor`` is a stdlib sqlite3 cursor."""
    return isinstance(cursor, sqlite3.Cursor)


# Column order shared across both backends (lets us render the INSERT
# the same way regardless of paramstyle).
_SESSION_COLUMNS = (
    "session_id",
    "kind",
    "target_system_area",
    "claimed_paths",
    "proposed_design_pattern",
    "status",
    "closure_trigger",
    "originating_chain_id",
    "originating_plan_slug",
    "host",
    "started_at",
    "last_activity_at",
    "closed_at",
    "emitted_by",
    "claude_session_id",
)


def _coerce_payload_value(value: Any) -> Any:
    """Coerce list/dict values to JSON text for SQLite columns."""
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return value


def insert_session(cursor, payload: dict) -> None:
    """INSERT one row into ``agent_sessions``.

    Backend-aware: on SQLite the table lives at top-level; on MySQL
    under ``extraction.``. Both paths normalize ``claude_session_id``
    to NULL when missing so legacy callers stay green.
    """
    payload = dict(payload)
    payload.setdefault("claude_session_id", None)
    if _is_sqlite_cursor(cursor):
        cols = ", ".join(_SESSION_COLUMNS)
        placeholders = ", ".join("?" * len(_SESSION_COLUMNS))
        values = tuple(
            _coerce_payload_value(payload.get(c)) for c in _SESSION_COLUMNS
        )
        cursor.execute(
            f"INSERT INTO agent_sessions ({cols}) VALUES ({placeholders})",
            values,
        )
        return
    cursor.execute(
        """
        INSERT INTO extraction.agent_sessions
            (session_id, kind, target_system_area, claimed_paths,
             proposed_design_pattern, status, closure_trigger,
             originating_chain_id, originating_plan_slug,
             host, started_at, last_activity_at, closed_at, emitted_by,
             claude_session_id)
        VALUES
            (%(session_id)s, %(kind)s, %(target_system_area)s,
             %(claimed_paths)s, %(proposed_design_pattern)s,
             %(status)s, %(closure_trigger)s,
             %(originating_chain_id)s, %(originating_plan_slug)s,
             %(host)s, %(started_at)s, %(last_activity_at)s,
             %(closed_at)s, %(emitted_by)s,
             %(claude_session_id)s)
        """,
        payload,
    )


def select_overlapping_for_update(
    cursor, area: str, paths_json: str
) -> list[dict]:
    """SELECT open sessions whose area matches OR claimed_paths overlap.

    Backend-aware SQL dispatch:
      * MySQL — ``SELECT ... FOR UPDATE`` + ``JSON_OVERLAPS``.
      * SQLite — plain ``SELECT`` over open rows whose area matches
        OR whose stored JSON contains any of the requested paths via
        a substring containment heuristic; the precise glob ∩ glob
        overlap check happens post-fetch in
        :func:`bin._intent.register.filter_python_glob_overlap`
        (identical to the MySQL-side caveat handling).

    The IMMEDIATE-locked txn already serializes writers, so on SQLite
    we don't need ``FOR UPDATE``.
    """
    if _is_sqlite_cursor(cursor):
        cursor.execute(
            """
            SELECT session_id, kind, target_system_area, claimed_paths,
                   proposed_design_pattern, last_activity_at, host
            FROM agent_sessions
            WHERE closed_at IS NULL
            """,
        )
        rows = [dict(r) for r in cursor.fetchall()]
        # The caller post-filters via filter_python_glob_overlap; we
        # return the full open set so glob ∩ glob overlap is detected
        # client-side (same semantic as MySQL's JSON_OVERLAPS caveat).
        try:
            requested = json.loads(paths_json)
        except (json.JSONDecodeError, TypeError):
            requested = []
        # Tighten the candidate set with a cheap server-side filter:
        # keep rows where target_system_area matches OR claimed_paths
        # contains any of the requested literal path strings.
        if not requested:
            return [r for r in rows if r.get("target_system_area") == area]
        out: list[dict] = []
        for r in rows:
            if r.get("target_system_area") == area:
                out.append(r)
                continue
            cps_text = r.get("claimed_paths") or ""
            if any(p and p in cps_text for p in requested):
                out.append(r)
        return out
    cursor.execute(
        """
        SELECT session_id, kind, target_system_area, claimed_paths,
               proposed_design_pattern, last_activity_at, host
        FROM extraction.agent_sessions
        WHERE closed_at IS NULL
          AND (target_system_area = %s OR JSON_OVERLAPS(claimed_paths, %s))
        FOR UPDATE
        """,
        (area, paths_json),
    )
    return list(cursor.fetchall())


def insert_collision(cursor, payload: dict) -> None:
    if _is_sqlite_cursor(cursor):
        cols = (
            "collision_id", "colliding_session_id", "colliding_area",
            "lineage_snapshot", "dispatch_mode", "resolution",
            "resolution_at", "detected_at", "host",
        )
        placeholders = ", ".join("?" * len(cols))
        values = tuple(_coerce_payload_value(payload.get(c)) for c in cols)
        cursor.execute(
            f"INSERT INTO agent_session_collision_log "
            f"({', '.join(cols)}) VALUES ({placeholders})",
            values,
        )
        return
    cursor.execute(
        """
        INSERT INTO extraction.agent_session_collision_log
            (collision_id, colliding_session_id, colliding_area,
             lineage_snapshot, dispatch_mode, resolution, resolution_at,
             detected_at, host)
        VALUES
            (%(collision_id)s, %(colliding_session_id)s, %(colliding_area)s,
             %(lineage_snapshot)s, %(dispatch_mode)s, %(resolution)s,
             %(resolution_at)s, %(detected_at)s, %(host)s)
        """,
        payload,
    )


def insert_event(cursor, payload: dict) -> None:
    if _is_sqlite_cursor(cursor):
        cols = (
            "event", "session_id", "collision_id", "payload",
            "emitted_by", "host", "emitted_at",
        )
        placeholders = ", ".join("?" * len(cols))
        values = tuple(_coerce_payload_value(payload.get(c)) for c in cols)
        cursor.execute(
            f"INSERT INTO intent_event_log "
            f"({', '.join(cols)}) VALUES ({placeholders})",
            values,
        )
        return
    cursor.execute(
        """
        INSERT INTO extraction.intent_event_log
            (event, session_id, collision_id, payload, emitted_by,
             host, emitted_at)
        VALUES
            (%(event)s, %(session_id)s, %(collision_id)s, %(payload)s,
             %(emitted_by)s, %(host)s, %(emitted_at)s)
        """,
        payload,
    )


def update_session(cursor, session_id: str, fields: dict) -> int:
    """UPDATE agent_sessions SET ... WHERE session_id = ...

    Returns the affected row count.
    """
    if not fields:
        return 0
    if _is_sqlite_cursor(cursor):
        set_clauses = ", ".join(f"{k} = ?" for k in fields)
        params = list(_coerce_payload_value(v) for v in fields.values())
        params.append(session_id)
        cursor.execute(
            f"UPDATE agent_sessions SET {set_clauses} WHERE session_id = ?",
            params,
        )
        return cursor.rowcount
    set_clauses = ", ".join(f"{k} = %({k})s" for k in fields)
    params = dict(fields)
    params["session_id"] = session_id
    cursor.execute(
        f"UPDATE extraction.agent_sessions SET {set_clauses} "
        f"WHERE session_id = %(session_id)s",
        params,
    )
    return cursor.rowcount


def select_session(cursor, session_id: str) -> Optional[dict]:
    if _is_sqlite_cursor(cursor):
        cursor.execute(
            """
            SELECT session_id, kind, target_system_area, claimed_paths,
                   proposed_design_pattern, status, closure_trigger,
                   originating_chain_id, originating_plan_slug, host,
                   started_at, last_activity_at, closed_at, emitted_by
            FROM agent_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        return rows[0] if rows else None
    cursor.execute(
        """
        SELECT session_id, kind, target_system_area, claimed_paths,
               proposed_design_pattern, status, closure_trigger,
               originating_chain_id, originating_plan_slug, host,
               started_at, last_activity_at, closed_at, emitted_by
        FROM extraction.agent_sessions
        WHERE session_id = %s
        """,
        (session_id,),
    )
    rows = list(cursor.fetchall())
    return rows[0] if rows else None


__all__ = [
    "BACKEND_ENV",
    "BACKEND_ENV_LEGACY",
    "DEFAULT_BACKEND",
    "VALID_BACKENDS",
    "MySQLUnavailable",
    "SQLiteBusy",
    "SQLITE_BUSY_TIMEOUT_MS",
    "SQLITE_SCHEMA",
    "selected_backend",
    "resolve_data_root",
    "intent_sqlite_path",
    "connection",
    "serializable_transaction",
    "insert_session",
    "select_overlapping_for_update",
    "insert_collision",
    "insert_event",
    "update_session",
    "select_session",
]
