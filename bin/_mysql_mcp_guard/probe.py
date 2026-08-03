"""Credential probe: is the `mysql` MCP server's DB user read-only?

Layer 2 of the guard. Resolves the adopter's `.mcp.json` → `mcpServers.mysql`
block, resolves its credentials (server `env` block over project `.env` over
process env, with ``${VAR}`` expansion), and runs
``SHOW GRANTS FOR CURRENT_USER()`` through the adopter's own `mysql` /
`mariadb` client binary (splock is stdlib-only — no DB driver ships).

Verdicts:

  * ``inert``          — no `.mcp.json` or no `mysql` server: nothing to guard.
  * ``ok``             — every grant is inside the read allowlist.
  * ``write_capable``  — at least one grant exceeds it; the offending grants
                         are named so the operator can narrow the user.
  * ``unverifiable``   — a `mysql` server is configured but the probe could
                         not run (no client binary, unresolvable credentials,
                         connection failure). Fail closed per VISION §4.7 —
                         a gate that could not run is not a pass.

``ok`` verdicts are cached for 15 minutes (keyed by the server block +
resolved endpoint) so the per-call hook does not pay a DB roundtrip each
time. Refusals are never cached: fixing the grant takes effect immediately.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

MODE_ENV = "SPLOCK_MYSQL_MCP_GUARD"
MODES = ("halt", "warn", "off")

# Privileges a read-interrogation credential may hold. EXECUTE is excluded
# (stored procedures can write); FILE obviously; everything else too.
ALLOWED_PRIVILEGES = {"USAGE", "SELECT", "SHOW VIEW", "SHOW DATABASES", "PROCESS"}

_OK_CACHE_TTL_SECONDS = 900

_GRANT_RE = re.compile(r"^\s*GRANT\s+(?P<privs>.+?)\s+ON\s+", re.IGNORECASE)
_EXPAND_RE = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def resolve_mode() -> str:
    """Guard mode from ``SPLOCK_MYSQL_MCP_GUARD``; unknown values fail closed."""
    raw = os.environ.get(MODE_ENV, "halt").strip().lower()
    return raw if raw in MODES else "halt"


@dataclass
class Verdict:
    status: str  # inert | ok | write_capable | unverifiable
    detail: str
    offending: list = field(default_factory=list)


def load_mysql_server_block(project_root: Path) -> Optional[dict]:
    """The `mysql` entry of `<project>/.mcp.json` `mcpServers`, or None."""
    mcp_path = project_root / ".mcp.json"
    if not mcp_path.is_file():
        return None
    try:
        config = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Unreadable config with unknown content — treat as configured but
        # unverifiable rather than silently inert.
        return {"__unparseable__": True}
    servers = config.get("mcpServers") or {}
    block = servers.get("mysql")
    return block if isinstance(block, dict) else None


def _parse_dotenv(project_root: Path) -> dict:
    env_path = project_root / ".env"
    out: dict = {}
    if not env_path.is_file():
        return out
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if key:
            out[key] = value
    return out


def resolve_credentials(server_block: dict, project_root: Path) -> dict:
    """Merge process env < project `.env` < server `env` block; expand ${VAR}."""
    base = dict(os.environ)
    base.update(_parse_dotenv(project_root))
    server_env = server_block.get("env") or {}

    def _expand(value: str) -> str:
        return _EXPAND_RE.sub(
            lambda m: base.get(m.group(1) or m.group(2), ""), value
        )

    merged = dict(base)
    for key, value in server_env.items():
        if isinstance(value, str):
            merged[key] = _expand(value)

    def _first(*names: str) -> str:
        for name in names:
            if merged.get(name):
                return str(merged[name])
        return ""

    return {
        "host": _first("MYSQL_HOST", "MYSQL_HOSTNAME", "DB_HOST") or "127.0.0.1",
        "port": _first("MYSQL_PORT", "DB_PORT") or "3306",
        "user": _first("MYSQL_USER", "MYSQL_USERNAME", "DB_USER"),
        "password": _first(
            "MYSQL_PASSWORD", "MYSQL_PASS", "MYSQL_PWD", "DB_PASSWORD", "DB_PASS"
        ),
        "database": _first("MYSQL_DATABASE", "MYSQL_DB", "DB_NAME"),
    }


def classify_grants(lines: list) -> Verdict:
    """Grade `SHOW GRANTS` output lines against ALLOWED_PRIVILEGES."""
    offending: list = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if "WITH GRANT OPTION" in line.upper():
            offending.append("GRANT OPTION")
        m = _GRANT_RE.match(line)
        if not m:
            continue
        # Drop column-level lists (`SELECT (id, name)`) BEFORE splitting on
        # commas — their inner commas are not privilege separators.
        privs_text = re.sub(r"\([^)]*\)", "", m.group("privs"))
        for priv in privs_text.split(","):
            priv = " ".join(priv.split()).upper()
            if priv and priv not in ALLOWED_PRIVILEGES:
                offending.append(priv)
    if offending:
        deduped = sorted(set(offending))
        return Verdict(
            "write_capable",
            "credential holds privileges beyond the read allowlist: "
            + ", ".join(deduped),
            deduped,
        )
    return Verdict("ok", "credential is read-only (grants within allowlist)")


def _cache_path(key: str) -> Path:
    return Path(tempfile.gettempdir()) / f"splock-mysql-mcp-guard-{key}.json"


def _cache_key(server_block: dict, creds: dict) -> str:
    payload = json.dumps(
        [server_block, creds["host"], creds["port"], creds["user"]],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cached_ok(key: str) -> bool:
    path = _cache_path(key)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (
            data.get("verdict") == "ok"
            and time.time() - float(data.get("ts", 0)) < _OK_CACHE_TTL_SECONDS
        )
    except (OSError, ValueError):
        return False


def _cache_ok(key: str) -> None:
    try:
        _cache_path(key).write_text(
            json.dumps({"verdict": "ok", "ts": time.time()}), encoding="utf-8"
        )
    except OSError:
        pass


def probe(project_root: Path, use_cache: bool = True) -> Verdict:
    """Full probe pipeline. Never raises; every failure is a Verdict."""
    server_block = load_mysql_server_block(project_root)
    if server_block is None:
        return Verdict("inert", "no `mysql` MCP server configured — nothing to guard")
    if server_block.get("__unparseable__"):
        return Verdict("unverifiable", ".mcp.json exists but is not valid JSON")

    creds = resolve_credentials(server_block, project_root)
    if not creds["user"]:
        return Verdict(
            "unverifiable",
            "could not resolve a MySQL user from the `mysql` server env / .env "
            "(recognized names: MYSQL_USER, MYSQL_USERNAME, DB_USER)",
        )

    key = _cache_key(server_block, creds)
    if use_cache and _cached_ok(key):
        return Verdict("ok", "credential is read-only (cached verdict)")

    client = shutil.which("mysql") or shutil.which("mariadb")
    if not client:
        return Verdict(
            "unverifiable",
            "no `mysql` or `mariadb` client binary on PATH to run SHOW GRANTS",
        )

    argv = [
        client,
        "--batch",
        "--skip-column-names",
        f"--host={creds['host']}",
        f"--port={creds['port']}",
        f"--user={creds['user']}",
        "--execute=SHOW GRANTS FOR CURRENT_USER()",
    ]
    if creds["database"]:
        argv.append(f"--database={creds['database']}")
    env = dict(os.environ)
    if creds["password"]:
        env["MYSQL_PWD"] = creds["password"]  # off argv, off ps output
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=15, env=env, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Verdict("unverifiable", f"SHOW GRANTS probe failed to run: {exc}")
    if proc.returncode != 0:
        excerpt = " ".join((proc.stderr or "").split())[:200]
        return Verdict(
            "unverifiable",
            f"SHOW GRANTS failed (client exit {proc.returncode}): {excerpt}",
        )

    verdict = classify_grants(proc.stdout.splitlines())
    if verdict.status == "ok" and use_cache:
        _cache_ok(key)
    return verdict
