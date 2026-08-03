"""mysql-mcp-guard — the read-only gate for the `mysql` MCP surface.

The qna `mcp__mysql` grant (0.3.2) named the adopter's DB credential as the
only load-bearing read-only tier, because the Bash hook spine never sees MCP
calls. The guard (0.3.3) closes that gap deterministically:

  * Layer 1 statement filter — write-shaped tool calls denied before they
    reach the server, string-literal-safe (`SELECT 'drop table'` passes).
  * Layer 2 credential probe — SHOW GRANTS graded against a closed read
    allowlist; write-capable AND unverifiable both refuse in halt mode
    (VISION §4.7: a gate that could not run is not a pass).

No live MySQL in CI: the probe is exercised up to the client-binary seam
(monkeypatched), and the grants classifier is graded on canned output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._mysql_mcp_guard import probe as probe_mod  # noqa: E402
from bin._mysql_mcp_guard import statement  # noqa: E402
from bin._mysql_mcp_guard.exit_codes import (  # noqa: E402
    EXIT_OK,
    EXIT_UNVERIFIABLE,
    EXIT_WRITE_CAPABLE,
)

# --------------------------------------------------------------------------- #
# Layer 1 — statement filter                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM users WHERE id = 1",
        "select last_update, created_at from orders limit 5",
        "SHOW TABLES",
        "SHOW GRANTS FOR CURRENT_USER()",
        "DESCRIBE users",
        "EXPLAIN SELECT * FROM users",
        "EXPLAIN ANALYZE SELECT count(*) FROM t",
        "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent",
        "SELECT 'DROP TABLE users' AS scary_string",
        "SELECT `update` FROM weird_schema",  # backtick-quoted column
        "-- comment\nSELECT 1",
        "USE analytics",
        "SELECT 1; SELECT 2",
    ],
)
def test_read_only_sql_passes(sql: str) -> None:
    assert statement.check_sql(sql) is None, sql


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO users VALUES (1)",
        "update users set name = 'x' where id = 1",
        "DELETE FROM orders",
        "DROP TABLE users",
        "CREATE TABLE t (id INT)",
        "TRUNCATE TABLE logs",
        "GRANT ALL ON *.* TO 'x'@'%'",
        "SET GLOBAL max_connections = 1000",
        "CALL cleanup_proc()",
        "WITH cte AS (SELECT id FROM users) UPDATE users SET x=1",
        "SELECT * FROM users INTO OUTFILE '/tmp/dump.csv'",
        "SELECT * FROM users FOR UPDATE",
        "EXPLAIN ANALYZE UPDATE users SET x = 1",  # EXPLAIN ANALYZE executes
        "SELECT 1; DROP TABLE users",  # multi-statement smuggle
        "START TRANSACTION",
        "LOAD DATA INFILE 'x' INTO TABLE t",
    ],
)
def test_write_shaped_sql_denied(sql: str) -> None:
    assert statement.check_sql(sql) is not None, sql


def test_tool_name_write_shape_denied_and_read_allowed() -> None:
    assert statement.check_tool_name("mcp__mysql__insert_row") is not None
    assert statement.check_tool_name("mcp__mysql__create_table") is not None
    assert statement.check_tool_name("mcp__mysql__query") is None
    assert statement.check_tool_name("mcp__mysql__list_tables") is None
    assert statement.check_tool_name("mcp__mysql__execute_sql") is None


def test_non_sql_parameters_are_not_graded() -> None:
    """A `table` param like "users" must not trip the leading-verb check."""
    assert (
        statement.check_tool_call(
            "mcp__mysql__list_columns", {"table": "users", "limit": "10"}
        )
        is None
    )


def test_tool_call_grades_nested_sql() -> None:
    reason = statement.check_tool_call(
        "mcp__mysql__query", {"args": {"sql": "DELETE FROM users"}}
    )
    assert reason is not None


# --------------------------------------------------------------------------- #
# Layer 2 — grants classifier                                                  #
# --------------------------------------------------------------------------- #


def test_read_only_grants_pass() -> None:
    verdict = probe_mod.classify_grants(
        [
            "GRANT USAGE ON *.* TO `splock_ro`@`%`",
            "GRANT SELECT, SHOW VIEW ON `shop`.* TO `splock_ro`@`%`",
        ]
    )
    assert verdict.status == "ok"


@pytest.mark.parametrize(
    "line,expected_priv",
    [
        ("GRANT ALL PRIVILEGES ON *.* TO `root`@`%`", "ALL PRIVILEGES"),
        ("GRANT SELECT, INSERT ON `shop`.* TO `app`@`%`", "INSERT"),
        ("GRANT SELECT, UPDATE, DELETE ON `shop`.* TO `app`@`%`", "UPDATE"),
        ("GRANT EXECUTE ON `shop`.* TO `app`@`%`", "EXECUTE"),
        ("GRANT SELECT ON `shop`.* TO `a`@`%` WITH GRANT OPTION", "GRANT OPTION"),
        ("GRANT PROXY ON ''@'' TO `root`@`localhost`", "PROXY"),
    ],
)
def test_write_capable_grants_flagged(line: str, expected_priv: str) -> None:
    verdict = probe_mod.classify_grants([line])
    assert verdict.status == "write_capable"
    assert expected_priv in verdict.offending


def test_column_level_select_is_allowed() -> None:
    verdict = probe_mod.classify_grants(
        ["GRANT SELECT (id, name) ON `shop`.`users` TO `ro`@`%`"]
    )
    assert verdict.status == "ok"


# --------------------------------------------------------------------------- #
# Probe pipeline seams (no live DB)                                            #
# --------------------------------------------------------------------------- #


def _write_mcp_json(root: Path, server: dict) -> None:
    (root / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"mysql": server}}), encoding="utf-8"
    )


def test_probe_inert_without_mcp_json(tmp_path: Path) -> None:
    assert probe_mod.probe(tmp_path).status == "inert"


def test_probe_inert_without_mysql_server(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "x"}}}), encoding="utf-8"
    )
    assert probe_mod.probe(tmp_path).status == "inert"


def test_probe_unverifiable_without_resolvable_user(tmp_path: Path) -> None:
    _write_mcp_json(tmp_path, {"command": "some-mysql-mcp"})
    verdict = probe_mod.probe(tmp_path)
    assert verdict.status == "unverifiable"
    assert "MYSQL_USER" in verdict.detail


def test_probe_unverifiable_without_client_binary(tmp_path, monkeypatch) -> None:
    _write_mcp_json(
        tmp_path,
        {"command": "some-mysql-mcp", "env": {"MYSQL_USER": "ro", "MYSQL_HOST": "h"}},
    )
    monkeypatch.setattr(probe_mod.shutil, "which", lambda _name: None)
    verdict = probe_mod.probe(tmp_path, use_cache=False)
    assert verdict.status == "unverifiable"
    assert "client binary" in verdict.detail


def test_probe_classifies_via_client_output(tmp_path, monkeypatch) -> None:
    _write_mcp_json(
        tmp_path,
        {"command": "x", "env": {"MYSQL_USER": "ro", "MYSQL_PASSWORD": "secret"}},
    )
    monkeypatch.setattr(probe_mod.shutil, "which", lambda _name: "/usr/bin/mysql")

    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs.get("env", {})

        class _P:
            returncode = 0
            stdout = "GRANT SELECT, INSERT ON `shop`.* TO `ro`@`%`\n"
            stderr = ""

        return _P()

    monkeypatch.setattr(probe_mod.subprocess, "run", fake_run)
    verdict = probe_mod.probe(tmp_path, use_cache=False)
    assert verdict.status == "write_capable"
    assert "INSERT" in verdict.detail
    # Password travels via MYSQL_PWD env, never argv.
    assert captured["env"].get("MYSQL_PWD") == "secret"
    assert not any("secret" in str(a) for a in captured["argv"])


def test_env_expansion_from_dotenv(tmp_path) -> None:
    (tmp_path / ".env").write_text("DB_RO_USER=readonly\n", encoding="utf-8")
    creds = probe_mod.resolve_credentials(
        {"env": {"MYSQL_USER": "${DB_RO_USER}"}}, tmp_path
    )
    assert creds["user"] == "readonly"


# --------------------------------------------------------------------------- #
# Hook engine end-to-end (stdin envelope → deny JSON, exit 0)                  #
# --------------------------------------------------------------------------- #


def _fire_hook(envelope: dict, extra_env: dict = None, cwd: Path = None) -> tuple:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, "-m", "bin._hooks.mysql_mcp_guard_hook"],
        input=json.dumps(envelope),
        capture_output=True,
        text=True,
        cwd=str(cwd or REPO_ROOT),
        env=env,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_hook_denies_write_shaped_call(tmp_path) -> None:
    rc, out, _err = _fire_hook(
        {
            "tool_name": "mcp__mysql__query",
            "tool_input": {"sql": "DROP TABLE users"},
        },
        extra_env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert rc == 0
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "write-shaped" in decision["permissionDecisionReason"]


def test_hook_allows_read_call_when_inert(tmp_path) -> None:
    """No `mysql` MCP server in the project → probe inert, read SQL passes."""
    rc, out, _err = _fire_hook(
        {
            "tool_name": "mcp__mysql__query",
            "tool_input": {"sql": "SELECT * FROM users"},
        },
        extra_env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert rc == 0
    assert out.strip() == ""  # no deny envelope


def test_hook_ignores_other_tools() -> None:
    rc, out, _err = _fire_hook(
        {"tool_name": "mcp__github__create_issue", "tool_input": {}}
    )
    assert rc == 0
    assert out.strip() == ""


def test_hook_denies_unverifiable_in_halt_mode(tmp_path) -> None:
    """Configured server + unresolvable creds → deny (fail closed)."""
    _write_mcp_json(tmp_path, {"command": "some-mysql-mcp"})
    rc, out, _err = _fire_hook(
        {
            "tool_name": "mcp__mysql__query",
            "tool_input": {"sql": "SELECT 1"},
        },
        extra_env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert rc == 0
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"


def test_hook_warn_mode_downgrades(tmp_path) -> None:
    _write_mcp_json(tmp_path, {"command": "some-mysql-mcp"})
    rc, out, err = _fire_hook(
        {
            "tool_name": "mcp__mysql__query",
            "tool_input": {"sql": "SELECT 1"},
        },
        extra_env={
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "SPLOCK_MYSQL_MCP_GUARD": "warn",
        },
    )
    assert rc == 0
    assert out.strip() == ""  # allowed
    assert "WARN" in err


def test_hook_off_mode_skips(tmp_path) -> None:
    rc, out, _err = _fire_hook(
        {
            "tool_name": "mcp__mysql__query",
            "tool_input": {"sql": "DROP TABLE users"},
        },
        extra_env={
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "SPLOCK_MYSQL_MCP_GUARD": "off",
        },
    )
    assert rc == 0
    assert out.strip() == ""


# --------------------------------------------------------------------------- #
# CLI exit codes (the /qna spawn gate contract)                                #
# --------------------------------------------------------------------------- #


def _run_cli(args: list, extra_env: dict = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "bin._mysql_mcp_guard.cli", *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=30,
    )


def test_cli_probe_inert_exits_zero(tmp_path) -> None:
    proc = _run_cli(["probe", "--project-root", str(tmp_path)])
    assert proc.returncode == EXIT_OK
    assert "inert" in proc.stdout


def test_cli_probe_unverifiable_exits_52(tmp_path) -> None:
    _write_mcp_json(tmp_path, {"command": "some-mysql-mcp"})
    proc = _run_cli(["probe", "--project-root", str(tmp_path)])
    assert proc.returncode == EXIT_UNVERIFIABLE
    assert "REFUSE" in proc.stderr


def test_cli_probe_warn_mode_exits_zero(tmp_path) -> None:
    _write_mcp_json(tmp_path, {"command": "some-mysql-mcp"})
    proc = _run_cli(
        ["probe", "--project-root", str(tmp_path)],
        extra_env={"SPLOCK_MYSQL_MCP_GUARD": "warn"},
    )
    assert proc.returncode == EXIT_OK
    assert "WARN" in proc.stderr


def test_cli_statement_write_exits_51() -> None:
    proc = _run_cli(["statement", "--sql", "DELETE FROM users"])
    assert proc.returncode == EXIT_WRITE_CAPABLE


def test_cli_statement_read_exits_zero() -> None:
    proc = _run_cli(["statement", "--sql", "SELECT 1"])
    assert proc.returncode == EXIT_OK
