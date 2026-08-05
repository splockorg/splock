"""Credential probe: is a `mysql*` MCP server's DB user read-only?

Layer 2 of the guard. Resolves the adopter's `.mcp.json` → the named
`mcpServers` block, resolves the credentials that server connects with,
and runs ``SHOW GRANTS FOR CURRENT_USER()`` through the adopter's own
`mysql` / `mariadb` client binary (splock is stdlib-only — no DB driver
ships).

Credential resolution has four tiers, lowest precedence first:

  1. process env      — the environment the MCP server inherits
  2. project `.env`
  3. the server block's own `env` (with ``${VAR}`` expansion)
  4. a **declared credential command** — ``SPLOCK_MYSQL_MCP_CREDENTIAL_COMMAND``
     in that server's `env` block, run by the guard, whose stdout is read as
     ``KEY=VALUE`` lines

Tier 4 exists because tiers 1–3 all assume the credential is *static and
discoverable before launch*. A launcher that fetches from Secrets Manager,
Vault, `op run`, or `aws ssm` and exports the result into the server process
defeats that assumption completely: nothing is on disk, the server block
often carries no `env` key at all, and no alias list can help. Such a lane
is **late-bound**, and the only faithful way to grade it is to run the same
resolution the launcher runs. See ADOPTION.md "Late-bound credentials".

Late binding fails in two directions, and both are closed here. Refusing a
correct lane is the visible half. The quiet half is **misattribution**: a
bare ``MYSQL_USER`` in `.env` or the ambient environment gets picked up and
graded even when the server block never named it and its launcher resolves
something else entirely — a verdict about the wrong subject, on the wrong
host, that reads exactly like a verdict about the right one. A lane whose
identity is not attributable to it refuses (``unattributed``).

Running an operator-declared command is a deliberate, bounded choice. It
grants no authority the adopter's `.mcp.json` did not already carry — that
file names the command Claude Code executes to *start* the server, with the
operator's full privileges. The declaration is read from the server's own
`env` block (never from `.env` or the ambient environment) so a multi-lane
project cannot have one lane's resolver silently grade another's.

Verdicts:

  * ``inert``          — no `.mcp.json` or no such server: nothing to guard.
  * ``ok``             — every grant is inside the read allowlist.
  * ``write_capable``  — at least one grant exceeds it; the offending grants
                         are named so the operator can narrow the user.
  * ``unverifiable``   — the server is configured but the probe could not
                         run. Fail closed per VISION §4.7 — a gate that
                         could not run is not a pass. ``Verdict.reason``
                         says *which* wall was hit (see REASON_* below);
                         policy is identical for all of them, the split
                         exists so the operator is told the truth about
                         what to fix.

``ok`` verdicts are cached for 15 minutes (keyed by the server block, the
declared resolver, and the resolved endpoint + user) so the per-call hook
does not pay a DB roundtrip each time. Refusals are never cached: fixing
the grant takes effect immediately. Credential resolution runs *before*
the cache is consulted, so a resolver that stops working can never be
rescued by a cached pass, and a rotation onto a different user re-probes
at once.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

MODE_ENV = "SPLOCK_MYSQL_MCP_GUARD"
MODES = ("halt", "warn", "off")

# Declared in the server's own `env` block in .mcp.json. Read by the guard,
# ignored by the MCP server itself.
CREDENTIAL_COMMAND_KEY = "SPLOCK_MYSQL_MCP_CREDENTIAL_COMMAND"
_CREDENTIAL_COMMAND_TIMEOUT_SECONDS = 15

# Why an `unverifiable` verdict is unverifiable. Policy does NOT differ by
# reason — every one of these refuses in halt mode (VISION §4.7). The code
# exists so the refusal message, the receipt, and the docs can name the
# actual wall instead of sending the operator down an alias-renaming dead
# end.
REASON_BAD_CONFIG = "bad_config"          # .mcp.json unreadable / not JSON
REASON_LATE_BOUND = "late_bound"          # block declares no credentials
REASON_NO_CREDENTIALS = "no_credentials"  # env present, but no user in it
REASON_UNATTRIBUTED = "unattributed"      # a user was found, but not this lane's
REASON_RESOLVER_FAILED = "resolver_failed"  # declared command did not deliver
REASON_NO_CLIENT = "no_client"            # no mysql/mariadb client on PATH
REASON_PROBE_FAILED = "probe_failed"      # SHOW GRANTS could not complete

# Privileges a read-interrogation credential may hold. EXECUTE is excluded
# (stored procedures can write); FILE obviously; everything else too.
ALLOWED_PRIVILEGES = {"USAGE", "SELECT", "SHOW VIEW", "SHOW DATABASES", "PROCESS"}

_OK_CACHE_TTL_SECONDS = 900

_GRANT_RE = re.compile(r"^\s*GRANT\s+(?P<privs>.+?)\s+ON\s+", re.IGNORECASE)
_EXPAND_RE = re.compile(r"\$\{(\w+)\}|\$(\w+)")

# Credential field → (recognized env names, default). Order is precedence
# WITHIN a tier; the tier itself is decided by _merge_layers.
_CRED_FIELDS = (
    ("host", ("MYSQL_HOST", "MYSQL_HOSTNAME", "DB_HOST"), "127.0.0.1"),
    ("port", ("MYSQL_PORT", "DB_PORT"), "3306"),
    ("user", ("MYSQL_USER", "MYSQL_USERNAME", "DB_USER"), ""),
    (
        "password",
        ("MYSQL_PASSWORD", "MYSQL_PASS", "MYSQL_PWD", "DB_PASSWORD", "DB_PASS"),
        "",
    ),
    ("database", ("MYSQL_DATABASE", "MYSQL_DB", "DB_NAME"), ""),
)

_USER_NAMES = ("MYSQL_USER", "MYSQL_USERNAME", "DB_USER")

SOURCE_PROCESS_ENV = "process-env"
SOURCE_DOTENV = ".env"
SOURCE_SERVER_ENV = "server-env"
SOURCE_RESOLVER = "credential-command"


def resolve_mode() -> str:
    """Guard mode from ``SPLOCK_MYSQL_MCP_GUARD``; unknown values fail closed."""
    raw = os.environ.get(MODE_ENV, "halt").strip().lower()
    return raw if raw in MODES else "halt"


@dataclass
class Verdict:
    status: str  # inert | ok | write_capable | unverifiable
    detail: str
    offending: list = field(default_factory=list)
    reason: str = ""  # REASON_* — set on `unverifiable` only


def load_mysql_server_block(
    project_root: Path, server: str = "mysql"
) -> Optional[dict]:
    """The named entry of `<project>/.mcp.json` `mcpServers`, or None."""
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
    block = servers.get(server)
    return block if isinstance(block, dict) else None


def list_mysql_servers(project_root: Path) -> list:
    """Every `mcpServers` name that begins with `mysql` — the guard's lane
    set (naming contract per ADOPTION.md 'MySQL MCP for /qna and /recon')."""
    mcp_path = project_root / ".mcp.json"
    if not mcp_path.is_file():
        return []
    try:
        config = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    servers = config.get("mcpServers") or {}
    return sorted(
        name
        for name, block in servers.items()
        if name.startswith("mysql") and isinstance(block, dict)
    )


def _parse_env_lines(lines: list) -> dict:
    """`KEY=VALUE` lines → dict. Tolerates `export `, quotes, comments."""
    out: dict = {}
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


def _parse_dotenv(project_root: Path) -> dict:
    env_path = project_root / ".env"
    if not env_path.is_file():
        return {}
    try:
        return _parse_env_lines(env_path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return {}


def _expanded_server_env(server_block: dict, base: dict) -> dict:
    """The server block's `env`, with ``${VAR}`` expanded against `base`."""
    server_env = server_block.get("env") or {}

    def _expand(value: str) -> str:
        return _EXPAND_RE.sub(lambda m: base.get(m.group(1) or m.group(2), ""), value)

    return {k: _expand(v) for k, v in server_env.items() if isinstance(v, str)}


def _merge_layers(layers: list) -> dict:
    """[(source, mapping), …] lowest precedence first → {key: (value, source)}."""
    merged: dict = {}
    for source, mapping in layers:
        for key, value in mapping.items():
            if isinstance(value, str):
                merged[key] = (value, source)
    return merged


def declared_credential_command(server_block: dict, project_root: Path) -> str:
    """The credential command declared in THIS server's `env` block, or "".

    Deliberately not read from `.env` or the process environment: the
    resolver describes one lane, and a project-wide value would silently
    grade every `mysql*` server with one server's credentials.
    """
    base = dict(os.environ)
    base.update(_parse_dotenv(project_root))
    return _expanded_server_env(server_block, base).get(
        CREDENTIAL_COMMAND_KEY, ""
    ).strip()


def _run_credential_command(command: str, project_root: Path) -> tuple:
    """Run the declared resolver; return (env_map, error). One is always None.

    stdout is parsed as ``KEY=VALUE`` lines and never logged or echoed — it
    carries the password. stderr is surfaced (truncated) because that is
    where a resolver reports why it could not reach the secret store.
    """
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return None, f"declaration is not a parseable command line: {exc}"
    if not argv:
        return None, "declaration is empty"
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_CREDENTIAL_COMMAND_TIMEOUT_SECONDS,
            cwd=str(project_root),
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"{argv[0]}: {exc}"
    if proc.returncode != 0:
        excerpt = " ".join((proc.stderr or "").split())[:200]
        return None, f"exit {proc.returncode}: {excerpt}"
    return _parse_env_lines((proc.stdout or "").splitlines()), None


def resolve_credentials(
    server_block: dict, project_root: Path, resolver_env: dict = None
) -> dict:
    """Resolve the connection credentials, recording where each came from.

    Precedence (lowest first): process env < project `.env` < server `env`
    block (``${VAR}``-expanded) < the declared credential command's output.
    """
    dotenv = _parse_dotenv(project_root)
    base = dict(os.environ)
    base.update(dotenv)

    layers = [
        (SOURCE_PROCESS_ENV, dict(os.environ)),
        (SOURCE_DOTENV, dotenv),
        (SOURCE_SERVER_ENV, _expanded_server_env(server_block, base)),
    ]
    if resolver_env:
        layers.append((SOURCE_RESOLVER, resolver_env))
    merged = _merge_layers(layers)

    creds: dict = {"sources": {}}
    for field_name, names, default in _CRED_FIELDS:
        value, source = "", ""
        for name in names:
            if merged.get(name) and merged[name][0]:
                value, source = merged[name]
                break
        creds[field_name] = value or default
        creds["sources"][field_name] = source
    return creds


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


def _cache_key(server: str, server_block: dict, creds: dict, resolver: str) -> str:
    payload = json.dumps(
        [server, server_block, resolver, creds["host"], creds["port"], creds["user"]],
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


def _declaration_hint(server: str) -> str:
    return (
        f'Declare how this lane resolves its credentials, in the `{server}` '
        f'block of .mcp.json:\n'
        f'    "env": {{ "{CREDENTIAL_COMMAND_KEY}": '
        f'"<command printing MYSQL_USER=… MYSQL_PASSWORD=… MYSQL_HOST=…>" }}\n'
        f'See ADOPTION.md "Late-bound credentials".'
    )


def _no_user_verdict(
    server: str, server_block: dict, project_root: Path, had_resolver: bool
) -> Verdict:
    """Distinguish a misconfigured lane from a late-bound one (VISION §4.7).

    Both refuse. They do not get the same message: telling the operator of a
    Secrets-Manager launcher to check the alias list sends them to a fix that
    cannot work, because the names are not on disk under any spelling.
    """
    if had_resolver:
        return Verdict(
            "unverifiable",
            f"the `{server}` credential command ran but printed no "
            "MYSQL_USER=… line (expected KEY=VALUE lines on stdout: "
            "MYSQL_USER, MYSQL_PASSWORD, MYSQL_HOST, MYSQL_PORT)",
            reason=REASON_RESOLVER_FAILED,
        )

    misplaced = ""
    ambient = dict(os.environ)
    ambient.update(_parse_dotenv(project_root))
    if ambient.get(CREDENTIAL_COMMAND_KEY):
        misplaced = (
            f"\n{CREDENTIAL_COMMAND_KEY} is set in .env / the environment, but "
            f"it is only read from the `{server}` server's own `env` block — a "
            "project-wide value would grade every mysql lane with one lane's "
            "credentials."
        )

    if not server_block.get("env"):
        return Verdict(
            "unverifiable",
            f"the `{server}` server block declares no `env`, and no "
            "MYSQL_USER / MYSQL_USERNAME / DB_USER is set in .env or the "
            "environment. If this server's command resolves credentials at "
            "launch (Secrets Manager, Vault, `op run`, `aws ssm`), no alias "
            "will help — the names are not on disk under any spelling.\n"
            + _declaration_hint(server)
            + misplaced,
            reason=REASON_LATE_BOUND,
        )
    return Verdict(
        "unverifiable",
        f"could not resolve a MySQL user from the `{server}` server env / "
        ".env (recognized names: MYSQL_USER, MYSQL_USERNAME, DB_USER). If "
        "the credential is fetched at launch rather than stored, declare a "
        "credential command instead.\n" + _declaration_hint(server) + misplaced,
        reason=REASON_NO_CREDENTIALS,
    )


def _identity(creds: dict) -> str:
    """`[user@host:port via <source>]` — the receipt's subject line."""
    source = creds["sources"].get("user") or "unknown"
    return f" [{creds['user']}@{creds['host']}:{creds['port']} via {source}]"


def _declares_user(server_block: dict) -> bool:
    env = server_block.get("env") or {}
    return any(env.get(name) for name in _USER_NAMES)


def _unattributed_verdict(server: str, creds: dict) -> Verdict:
    """A user was resolved, but nothing ties it to THIS lane.

    The dangerous half of the late-binding problem. When a server block
    names no credential of its own, a bare `MYSQL_USER` in `.env` or the
    ambient environment is picked up and graded — and if the block's
    command resolves its own credentials at launch, that grade belongs to
    a different user, possibly on a different host. Field case: five
    `mysql*` lanes pointing at five hosts through a Secrets-Manager
    launcher, all five reported read-only off one `.env` credential that
    none of them uses.

    So it refuses. A verdict about the wrong subject is a vacuous green,
    which VISION §4.7 names as a defect class of its own, and §4.14 makes
    an overstated gate a defect of the same class as a test that passes
    when it shouldn't. The remedy is one line and it is named in full.
    """
    return Verdict(
        "unverifiable",
        f"a MySQL user was found in {creds['sources']['user']} "
        f"({creds['user']}@{creds['host']}), but the `{server}` server block "
        "declares no credential of its own, so nothing ties that user to this "
        "lane — if its command resolves credentials at launch, this would "
        "grade the wrong user, on the wrong host. Say which it is:\n"
        f"  · the server really does inherit it → name it in the `{server}` "
        'block\'s `env` ("MYSQL_USER": "...", or "${YOUR_VAR}")\n'
        f"  · the command resolves it at launch → declare a credential "
        f"command\n{_declaration_hint(server)}",
        reason=REASON_UNATTRIBUTED,
    )


def probe(
    project_root: Path, server: str = "mysql", use_cache: bool = True
) -> Verdict:
    """Full probe pipeline for ONE named server. Never raises; every
    failure is a Verdict. The per-call hook passes the server the tool
    call actually targets, so each lane is graded on its own credential."""
    server_block = load_mysql_server_block(project_root, server)
    if server_block is None:
        return Verdict(
            "inert", f"no `{server}` MCP server configured — nothing to guard"
        )
    if server_block.get("__unparseable__"):
        return Verdict(
            "unverifiable",
            ".mcp.json exists but is not valid JSON",
            reason=REASON_BAD_CONFIG,
        )

    # Tier 4 first: a declared resolver is the authority on this lane, and it
    # runs BEFORE the cache is consulted so a resolver that has stopped
    # working can never be rescued by an earlier pass.
    resolver = declared_credential_command(server_block, project_root)
    resolver_env = None
    if resolver:
        resolver_env, error = _run_credential_command(resolver, project_root)
        if resolver_env is None:
            return Verdict(
                "unverifiable",
                f"the `{server}` credential command failed — {error}",
                reason=REASON_RESOLVER_FAILED,
            )

    creds = resolve_credentials(server_block, project_root, resolver_env)
    if not creds["user"]:
        return _no_user_verdict(
            server, server_block, project_root, had_resolver=bool(resolver)
        )

    if not resolver and not _declares_user(server_block):
        return _unattributed_verdict(server, creds)

    identity = _identity(creds)
    key = _cache_key(server, server_block, creds, resolver)
    if use_cache and _cached_ok(key):
        return Verdict("ok", "credential is read-only (cached verdict)" + identity)

    client = shutil.which("mysql") or shutil.which("mariadb")
    if not client:
        return Verdict(
            "unverifiable",
            "no `mysql` or `mariadb` client binary on PATH to run SHOW "
            "GRANTS. The guard needs one even though the MCP server does "
            "not — install a client (`mysql-client` / `mariadb-client`) or "
            "put it on the PATH the hook runs with",
            reason=REASON_NO_CLIENT,
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
        return Verdict(
            "unverifiable",
            f"SHOW GRANTS probe failed to run: {exc}{identity}",
            reason=REASON_PROBE_FAILED,
        )
    if proc.returncode != 0:
        excerpt = " ".join((proc.stderr or "").split())[:200]
        return Verdict(
            "unverifiable",
            f"SHOW GRANTS failed (client exit {proc.returncode}): "
            f"{excerpt}{identity}",
            reason=REASON_PROBE_FAILED,
        )

    verdict = classify_grants(proc.stdout.splitlines())
    verdict.detail += identity
    if verdict.status == "ok" and use_cache:
        _cache_ok(key)
    return verdict
