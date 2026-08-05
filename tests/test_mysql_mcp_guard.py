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


def test_tool_name_grading_on_suffixed_servers() -> None:
    """Only the TOOL segment is graded — a server named `mysql-shop-prod`
    must not trip on its own name, and its write tools must still trip."""
    assert statement.check_tool_name("mcp__mysql-shop-prod__query") is None
    assert statement.check_tool_name("mcp__mysql-shop-prod__insert_row") is not None


def test_split_mcp_tool_name() -> None:
    assert statement.split_mcp_tool_name("mcp__mysql__query") == ("mysql", "query")
    assert statement.split_mcp_tool_name("mcp__mysql-shop-prod__query") == (
        "mysql-shop-prod",
        "query",
    )
    assert statement.split_mcp_tool_name("mcp__mysql") == ("mysql", "")
    assert statement.split_mcp_tool_name("Bash") == ("", "Bash")


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


def test_hook_probes_the_server_the_call_targets(tmp_path) -> None:
    """A call to `mysql-shop-prod` is graded on THAT server's credential —
    a project whose only mysql server is the suffixed one must not slip
    through an inert probe of the absent `mysql` entry."""
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"mysql-shop-prod": {"command": "x"}}}),
        encoding="utf-8",
    )
    rc, out, _err = _fire_hook(
        {
            "tool_name": "mcp__mysql-shop-prod__query",
            "tool_input": {"sql": "SELECT 1"},
        },
        extra_env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert rc == 0
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"  # unverifiable, fail closed
    assert "mysql-shop-prod" in decision["permissionDecisionReason"]


def test_hooks_json_matcher_covers_mysql_prefixed_servers() -> None:
    """The matcher must be the prefix form — the narrow `mcp__mysql__.*`
    left every suffixed server (`mysql-<site>-prod`, …) unguarded."""
    block = json.loads(
        (REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    matchers = [g.get("matcher", "") for g in block["hooks"]["PreToolUse"]]
    assert "mcp__mysql.*" in matchers
    assert "mcp__mysql__.*" not in matchers


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


def test_cli_probe_server_flag_targets_named_server(tmp_path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"mysql-shop-prod": {"command": "x"}}}),
        encoding="utf-8",
    )
    # Default lane (`mysql`) is absent → inert.
    proc = _run_cli(["probe", "--project-root", str(tmp_path)])
    assert proc.returncode == EXIT_OK
    # The suffixed server, probed by name → unverifiable.
    proc = _run_cli(
        ["probe", "--project-root", str(tmp_path), "--server", "mysql-shop-prod"]
    )
    assert proc.returncode == EXIT_UNVERIFIABLE


def test_cli_probe_all_sweeps_and_reports_worst(tmp_path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "mysql": {"command": "x"},
                    "mysql-shop-prod": {"command": "x"},
                    "github": {"command": "x"},
                }
            }
        ),
        encoding="utf-8",
    )
    proc = _run_cli(["probe", "--project-root", str(tmp_path), "--all"])
    assert proc.returncode == EXIT_UNVERIFIABLE
    # Both mysql lanes reported; the non-mysql server is not swept.
    assert "[mysql]" in proc.stderr
    assert "[mysql-shop-prod]" in proc.stderr
    assert "github" not in proc.stderr + proc.stdout


def test_cli_statement_write_exits_51() -> None:
    proc = _run_cli(["statement", "--sql", "DELETE FROM users"])
    assert proc.returncode == EXIT_WRITE_CAPABLE


def test_cli_statement_read_exits_zero() -> None:
    proc = _run_cli(["statement", "--sql", "SELECT 1"])
    assert proc.returncode == EXIT_OK


# --------------------------------------------------------------------------- #
# Late-bound credentials — the Secrets-Manager / Vault / `op run` shape        #
#                                                                             #
# The failing case this section was written for: five read-only MySQL MCP     #
# lanes whose launcher fetches `mcp_ro` from AWS Secrets Manager at spawn and #
# exports MYSQL_USER/MYSQL_PASS into the server process. The `.mcp.json`      #
# blocks carry NO `env` key at all, so every lane graded `unverifiable` and   #
# the PreToolUse hook denied every call on a credential that was in fact      #
# SELECT-only. Aliasing more env names cannot fix it — the names are not on   #
# disk under any spelling. The lane declares its resolver instead.            #
# --------------------------------------------------------------------------- #

_LAUNCHER = r"""#!/usr/bin/env bash
# Stand-in for a Secrets-Manager launcher: nothing is on disk before it runs.
set -euo pipefail
if [ -f "$(dirname "$0")/broken" ]; then
  echo "secret store unreachable" >&2
  exit 1
fi
if [ "${1:-}" = "--print-credentials" ]; then
  printf 'MYSQL_HOST=db.internal\nMYSQL_PORT=3306\n'
  printf 'MYSQL_USER=mcp_ro\nMYSQL_PASSWORD=s3cret\n'
  exit 0
fi
exec fake-mysql-mcp-server
"""

_RO_GRANTS = "GRANT USAGE ON *.* TO `mcp_ro`@`%`\nGRANT SELECT, SHOW VIEW ON `pp`.* TO `mcp_ro`@`%`\n"  # noqa: E501


def _write_launcher(root: Path) -> Path:
    path = root / "mysql_mcp.sh"
    path.write_text(_LAUNCHER, encoding="utf-8")
    os.chmod(path, 0o755)
    return path


def _late_bound_block(launcher: Path, declare: bool = True) -> dict:
    block = {"command": "bash", "args": [str(launcher), "pp"]}
    if declare:
        block["env"] = {
            probe_mod.CREDENTIAL_COMMAND_KEY: f"bash {launcher} --print-credentials"
        }
    return block


def _fake_client(monkeypatch, grants: str, path: str = "/usr/bin/mysql") -> dict:
    """Fake the `mysql` client seam ONLY — the credential command must still
    run for real, or the test would not exercise late binding at all."""
    real_run = subprocess.run
    captured: dict = {}

    monkeypatch.setattr(
        probe_mod.shutil,
        "which",
        lambda name: path if name in ("mysql", "mariadb") else None,
    )

    def dispatch(argv, **kwargs):
        if argv and str(argv[0]) == path:
            captured["argv"] = list(argv)
            captured["env"] = kwargs.get("env", {})

            class _P:
                returncode = 0
                stdout = grants
                stderr = ""

            return _P()
        return real_run(argv, **kwargs)

    monkeypatch.setattr(probe_mod.subprocess, "run", dispatch)
    return captured


def test_late_bound_block_is_diagnosed_as_late_bound(tmp_path: Path) -> None:
    """A block with no `env` must not be told to check its alias spelling."""
    _write_mcp_json(tmp_path, {"command": "bash", "args": ["launch.sh", "pp"]})
    verdict = probe_mod.probe(tmp_path, use_cache=False)
    assert verdict.status == "unverifiable"
    assert verdict.reason == probe_mod.REASON_LATE_BOUND
    assert probe_mod.CREDENTIAL_COMMAND_KEY in verdict.detail
    assert "no alias will help" in verdict.detail


def test_credential_command_verifies_a_late_bound_lane(tmp_path, monkeypatch) -> None:
    launcher = _write_launcher(tmp_path)
    _write_mcp_json(tmp_path, _late_bound_block(launcher))
    captured = _fake_client(monkeypatch, _RO_GRANTS)

    verdict = probe_mod.probe(tmp_path, use_cache=False)

    assert verdict.status == "ok"
    assert "mcp_ro@db.internal" in verdict.detail
    assert probe_mod.SOURCE_RESOLVER in verdict.detail
    # The resolved password still travels via MYSQL_PWD, never on argv.
    assert captured["env"].get("MYSQL_PWD") == "s3cret"
    assert not any("s3cret" in str(a) for a in captured["argv"])
    assert "--user=mcp_ro" in captured["argv"]


def test_credential_command_does_not_excuse_a_write_capable_grant(
    tmp_path, monkeypatch
) -> None:
    """The fix makes correct setups verifiable — it is not a way through."""
    launcher = _write_launcher(tmp_path)
    _write_mcp_json(tmp_path, _late_bound_block(launcher))
    _fake_client(monkeypatch, "GRANT SELECT, INSERT ON `pp`.* TO `mcp_ro`@`%`\n")

    verdict = probe_mod.probe(tmp_path, use_cache=False)

    assert verdict.status == "write_capable"
    assert "INSERT" in verdict.offending


def test_failed_resolver_is_unverifiable_and_no_cached_ok_rescues_it(
    tmp_path, monkeypatch
) -> None:
    """A resolver that stops working must re-refuse immediately: resolution
    runs before the cache is consulted, so an earlier pass cannot stand in
    for a credential nobody can read any more."""
    launcher = _write_launcher(tmp_path)
    _write_mcp_json(tmp_path, _late_bound_block(launcher))
    _fake_client(monkeypatch, _RO_GRANTS)

    assert probe_mod.probe(tmp_path, use_cache=True).status == "ok"  # caches ok
    (tmp_path / "broken").write_text("", encoding="utf-8")  # secret store down

    verdict = probe_mod.probe(tmp_path, use_cache=True)
    assert verdict.status == "unverifiable"
    assert verdict.reason == probe_mod.REASON_RESOLVER_FAILED
    assert "secret store unreachable" in verdict.detail


def test_cache_key_covers_the_declared_resolver() -> None:
    """Repointing a lane at another resolver must not hit the old verdict."""
    creds = {"host": "h", "port": "3306", "user": "mcp_ro"}
    block = {"command": "bash"}
    assert probe_mod._cache_key("mysql", block, creds, "resolve-pp") != (
        probe_mod._cache_key("mysql", block, creds, "resolve-prod")
    )


def test_resolver_declaration_is_per_lane_not_project_wide(tmp_path: Path) -> None:
    """A .env-level declaration would grade every lane with one lane's
    credential — refuse, and say where the declaration belongs."""
    (tmp_path / ".env").write_text(
        f"{probe_mod.CREDENTIAL_COMMAND_KEY}=echo MYSQL_USER=mcp_ro\n",
        encoding="utf-8",
    )
    _write_mcp_json(tmp_path, {"command": "bash", "args": ["launch.sh"]})

    verdict = probe_mod.probe(tmp_path, use_cache=False)

    assert verdict.status == "unverifiable"
    assert "server's own `env` block" in verdict.detail


def test_resolver_that_prints_no_user_is_unverifiable(tmp_path, monkeypatch) -> None:
    _write_mcp_json(
        tmp_path,
        {
            "command": "x",
            "env": {probe_mod.CREDENTIAL_COMMAND_KEY: "echo MYSQL_HOST=db.internal"},
        },
    )
    _fake_client(monkeypatch, _RO_GRANTS)
    verdict = probe_mod.probe(tmp_path, use_cache=False)
    assert verdict.status == "unverifiable"
    assert verdict.reason == probe_mod.REASON_RESOLVER_FAILED


def test_missing_client_binary_has_its_own_reason_and_remedy(
    tmp_path, monkeypatch
) -> None:
    """Same shape as late binding — a correct setup that cannot be verified —
    but a different remedy, so it must not share the message."""
    _write_mcp_json(tmp_path, {"command": "x", "env": {"MYSQL_USER": "ro"}})
    monkeypatch.setattr(probe_mod.shutil, "which", lambda _name: None)
    verdict = probe_mod.probe(tmp_path, use_cache=False)
    assert verdict.reason == probe_mod.REASON_NO_CLIENT
    assert "install a client" in verdict.detail


def test_ambient_credential_is_not_attributable_to_the_lane(
    tmp_path, monkeypatch
) -> None:
    """The quiet half of late binding: a user the server block never named
    is graded anyway, and the pass reads as if it were about this lane."""
    monkeypatch.setenv("MYSQL_USER", "whoever")
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    _write_mcp_json(tmp_path, {"command": "bash", "args": ["launch.sh"]})
    _fake_client(monkeypatch, "GRANT SELECT ON `pp`.* TO `whoever`@`%`\n")

    verdict = probe_mod.probe(tmp_path, use_cache=False)

    assert verdict.status == "unverifiable"
    assert verdict.reason == probe_mod.REASON_UNATTRIBUTED
    # Both remedies named — the block may well be inheriting it legitimately.
    assert "MYSQL_USER" in verdict.detail
    assert probe_mod.CREDENTIAL_COMMAND_KEY in verdict.detail


def test_dotenv_credential_is_not_attributable_either(tmp_path, monkeypatch) -> None:
    """Field shape: several `mysql*` lanes, no `env` on any of them, one bare
    credential in `.env` — every lane graded that one user, on one host, and
    reported read-only. `.env` is no more attributable than the shell is."""
    (tmp_path / ".env").write_text(
        "MYSQL_USER=app_rw\nMYSQL_HOST=app-db\n", encoding="utf-8"
    )
    _write_mcp_json(tmp_path, {"command": "bash", "args": ["launch.sh", "prod"]})
    _fake_client(monkeypatch, "GRANT SELECT ON `pp`.* TO `app_rw`@`%`\n")

    verdict = probe_mod.probe(tmp_path, use_cache=False)

    assert verdict.status == "unverifiable"
    assert verdict.reason == probe_mod.REASON_UNATTRIBUTED
    assert "app_rw@app-db" in verdict.detail


def test_server_block_declaring_its_user_is_attributed(tmp_path, monkeypatch) -> None:
    """The documented shape stays green — and `${VAR}` indirection through
    `.env` is still a declaration, because the block is what names it."""
    (tmp_path / ".env").write_text("DB_RO_USER=ro\n", encoding="utf-8")
    _write_mcp_json(
        tmp_path,
        {"command": "x", "env": {"MYSQL_USER": "${DB_RO_USER}", "MYSQL_HOST": "h"}},
    )
    _fake_client(monkeypatch, "GRANT SELECT ON `pp`.* TO `ro`@`%`\n")

    verdict = probe_mod.probe(tmp_path, use_cache=False)

    assert verdict.status == "ok"
    assert "ro@h" in verdict.detail


# --- end to end: real launcher, real credential command, fake mysql client -- #


def _fake_mysql_on_path(root: Path, grants: str) -> Path:
    bindir = root / "fakebin"
    bindir.mkdir(exist_ok=True)
    client = bindir / "mysql"
    client.write_text(
        "#!/usr/bin/env bash\ncat <<'EOF'\n" + grants + "EOF\n", encoding="utf-8"
    )
    os.chmod(client, 0o755)
    return bindir


def test_hook_denies_the_late_bound_lane_without_a_declaration(tmp_path) -> None:
    """The regression, stated as the hook sees it: correct read-only setup,
    every MCP call denied, because the credential is unreadable from here."""
    launcher = _write_launcher(tmp_path)
    _write_mcp_json(tmp_path, _late_bound_block(launcher, declare=False))
    bindir = _fake_mysql_on_path(tmp_path, _RO_GRANTS)

    rc, out, _err = _fire_hook(
        {"tool_name": "mcp__mysql__query", "tool_input": {"sql": "SELECT 1"}},
        extra_env={
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        },
    )
    assert rc == 0
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    reason = decision["permissionDecisionReason"]
    assert probe_mod.REASON_LATE_BOUND in reason
    # The refusal must not be dressed up as a grant finding.
    assert "limited to SELECT-class grants" not in reason


def test_hook_allows_the_same_lane_once_it_declares_its_resolver(tmp_path) -> None:
    launcher = _write_launcher(tmp_path)
    _write_mcp_json(tmp_path, _late_bound_block(launcher))
    bindir = _fake_mysql_on_path(tmp_path, _RO_GRANTS)

    rc, out, err = _fire_hook(
        {"tool_name": "mcp__mysql__query", "tool_input": {"sql": "SELECT 1"}},
        extra_env={
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        },
    )
    assert rc == 0, err
    assert out.strip() == ""  # allowed — no deny envelope


def test_cli_probe_all_passes_a_declared_multi_lane_project(tmp_path) -> None:
    """The /qna + /recon spawn gate on a Secrets-Manager project: several
    late-bound lanes, each declaring its own resolver, sweep clean."""
    launcher = _write_launcher(tmp_path)
    bindir = _fake_mysql_on_path(tmp_path, _RO_GRANTS)
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "mysql": _late_bound_block(launcher),
                    "mysql-shop-prod": _late_bound_block(launcher),
                }
            }
        ),
        encoding="utf-8",
    )

    proc = _run_cli(
        ["probe", "--project-root", str(tmp_path), "--all", "--no-cache"],
        extra_env={"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"},
    )

    assert proc.returncode == EXIT_OK, proc.stderr
    assert "[mysql]: ok" in proc.stdout
    assert "[mysql-shop-prod]: ok" in proc.stdout
