"""Write-shape scanner for `mysql` MCP tool calls.

Layer 1 of the guard: pure text analysis, no DB connection. Applied by the
PreToolUse hook to the tool NAME and to every string value in the tool
input that looks like SQL. Fail-closed on anything that is not clearly a
read (VISION §4.7); false positives are downgradeable via
``SPLOCK_MYSQL_MCP_GUARD=warn``, never by editing this list mid-run.

String literals, backtick identifiers, and comments are stripped before
any verb check, so ``SELECT 'drop table users'`` and a ``last_update``
column never trip the scanner — same discipline as
``bin._hooks.pattern_detect`` uses for .sql file content.
"""

from __future__ import annotations

import re
from typing import Any, Iterator, Optional

# Leading verbs that begin a read-only statement.
READ_LEADING = {
    "SELECT",
    "SHOW",
    "DESCRIBE",
    "DESC",
    "EXPLAIN",
    "HELP",
    "USE",
    "TABLE",   # MySQL 8.0 `TABLE t` ≡ SELECT * FROM t
    "VALUES",  # `VALUES ROW(...)` table-value constructor
}

# Verbs that begin (or, inside a CTE, embed) a write / DDL / admin /
# transaction-control statement. `SET` covers session/system vars; read
# interrogation needs none of them.
WRITE_VERBS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "REPLACE",
    "ALTER",
    "CREATE",
    "DROP",
    "TRUNCATE",
    "RENAME",
    "GRANT",
    "REVOKE",
    "LOAD",
    "CALL",
    "DO",
    "SET",
    "LOCK",
    "UNLOCK",
    "START",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT",
    "XA",
    "FLUSH",
    "RESET",
    "KILL",
    "PURGE",
    "INSTALL",
    "UNINSTALL",
    "OPTIMIZE",
    "REPAIR",
    "ANALYZE",
    "HANDLER",
    "IMPORT",
    "CHANGE",
    "STOP",
    "PREPARE",
    "EXECUTE",
    "DEALLOCATE",
}

_ALL_LEADING = READ_LEADING | WRITE_VERBS | {"WITH"}

# Tool-NAME tokens that mark a write-shaped MCP tool regardless of input
# (many MySQL MCP servers expose e.g. `insert`, `update`, `create_table`
# as distinct tools beside the generic query runner).
WRITE_SHAPED_TOOL_TOKENS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "rename",
    "grant",
    "revoke",
    "write",
    "ddl",
    "dml",
    "admin",
    "migrate",
    "seed",
    "import",
}

_COMMENT_RE = re.compile(r"/\*.*?\*/|--[^\n]*|#[^\n]*", re.DOTALL)
_LITERAL_RE = re.compile(
    r"'(?:[^'\\]|\\.|'')*'"      # single-quoted string (with escapes / '')
    r"|\"(?:[^\"\\]|\\.|\"\")*\""  # double-quoted string
    r"|`[^`]*`",                  # backtick identifier
    re.DOTALL,
)
_WORD_RE = re.compile(r"[A-Za-z_]+")
_INTO_FILE_RE = re.compile(r"\bINTO\s+(?:OUTFILE|DUMPFILE)\b", re.IGNORECASE)
_WRITE_LOCK_RE = re.compile(
    r"\bFOR\s+UPDATE\b|\bFOR\s+SHARE\b|\bLOCK\s+IN\s+SHARE\s+MODE\b",
    re.IGNORECASE,
)
_EXPLAIN_MODIFIER_RE = re.compile(
    r"^(?:ANALYZE\s+|EXTENDED\s+|PARTITIONS\s+|FORMAT\s*=\s*\w+\s+)*",
    re.IGNORECASE,
)


def _strip(sql: str) -> str:
    """Remove comments, string literals, and backtick identifiers."""
    return _LITERAL_RE.sub(" ", _COMMENT_RE.sub(" ", sql))


def _leading_word(text: str) -> str:
    m = _WORD_RE.search(text)
    return m.group(0).upper() if m else ""


def looks_like_sql(value: str) -> bool:
    """True when the string plausibly carries a SQL statement.

    Non-SQL parameters (table names, limits, hostnames) are skipped by the
    scanner; only values whose leading word is a known SQL verb are graded.
    """
    return _leading_word(_strip(value)) in _ALL_LEADING


def check_sql(sql: str) -> Optional[str]:
    """Return a refusal reason for write-shaped SQL, or None when read-only.

    Multi-statement input is graded per statement; the first offender wins.
    """
    stripped = _strip(sql)
    for stmt in stripped.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        reason = _check_statement(stmt)
        if reason:
            return reason
    return None


def _check_statement(stmt: str) -> Optional[str]:
    verb = _leading_word(stmt)
    if not verb:
        return None
    if verb in WRITE_VERBS:
        return f"write-shaped statement (leading verb {verb})"
    if verb == "WITH":
        # CTE prologue can front UPDATE/DELETE in MySQL 8 — deep-scan.
        for word in _WORD_RE.findall(stmt):
            if word.upper() in WRITE_VERBS:
                return f"write verb {word.upper()} inside WITH statement"
        return _check_read_clauses(stmt)
    if verb in ("EXPLAIN", "DESCRIBE", "DESC"):
        # EXPLAIN ANALYZE *executes* the inner statement — grade it.
        rest = stmt[stmt.upper().index(verb) + len(verb):].lstrip()
        rest = _EXPLAIN_MODIFIER_RE.sub("", rest)
        if _leading_word(rest) in _ALL_LEADING:
            return _check_statement(rest)
        return None  # `DESCRIBE <table>` form
    if verb in READ_LEADING:
        return _check_read_clauses(stmt)
    return f"unrecognized leading verb {verb} (fail-closed)"


def _check_read_clauses(stmt: str) -> Optional[str]:
    if _INTO_FILE_RE.search(stmt):
        return "SELECT ... INTO OUTFILE/DUMPFILE writes server-side files"
    if _WRITE_LOCK_RE.search(stmt):
        return "write-lock clause (FOR UPDATE / FOR SHARE / LOCK IN SHARE MODE)"
    return None


def split_mcp_tool_name(tool_name: str) -> tuple:
    """`mcp__<server>__<tool>` → (server, tool). A name with no tool segment
    yields an empty tool; a name with no `mcp__` prefix yields ("", name)."""
    if not tool_name.startswith("mcp__"):
        return "", tool_name
    rest = tool_name[len("mcp__"):]
    server, sep, tool = rest.partition("__")
    return (server, tool) if sep else (server, "")


def check_tool_name(tool_name: str) -> Optional[str]:
    """Return a refusal reason for a write-shaped MCP tool NAME, or None.

    Only the TOOL segment is graded — the server segment
    (`mysql-shop-prod`, …) is routing, not intent.
    """
    server, tool = split_mcp_tool_name(tool_name)
    target = tool or server
    tokens = {t for t in re.split(r"[_\-]+", target.lower()) if t}
    hit = tokens & WRITE_SHAPED_TOOL_TOKENS
    if hit:
        return f"write-shaped mysql MCP tool name ({', '.join(sorted(hit))})"
    return None


def _iter_strings(value: Any, depth: int = 0) -> Iterator[str]:
    if depth > 6:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _iter_strings(v, depth + 1)


def check_tool_call(tool_name: str, tool_input: Any) -> Optional[str]:
    """Grade a full MCP tool call: name first, then every SQL-looking input."""
    reason = check_tool_name(tool_name)
    if reason:
        return reason
    for scanned, text in enumerate(_iter_strings(tool_input)):
        if scanned >= 64:
            break
        if looks_like_sql(text):
            reason = check_sql(text)
            if reason:
                return reason
    return None
