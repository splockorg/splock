"""Closed regex sets for hook pattern detection.

Per implplan §G.impl.7 (`INSTALL_COMMAND_PATTERNS`),
§G.impl.8 (`DDL_COMMAND_PATTERNS`),
§G.impl.6 (`TEST_PATH_GLOBS`),
§G.impl.9 (`scan_settings_content`),
§G.impl.4 (`SUPPRESSION_PATTERNS`).

Single source of truth — hooks import these instead of duplicating the
regex set inline.
"""

from __future__ import annotations

import fnmatch
import re


# ----------------------------------------------------------------------
# Suppression patterns (§G.impl.4) — chain-suppression-block.sh
# ----------------------------------------------------------------------
# These mirror `bin/_retry_loop/reversibility.py::all_suppression_patterns`
# but the canonical chain-suppression-block path lives in the §F-shipped
# hook; this set is here for completeness and for `bin/hook-lint`
# fixture generation.
SUPPRESSION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("py-sys-exit-zero", re.compile(r"\bsys\.exit\s*\(\s*(0|EXIT_SUCCESS)\s*\)")),
    ("py-os-exit-zero", re.compile(r"\bos\._exit\s*\(\s*0\s*\)")),
    ("py-pytest-skip", re.compile(r"@pytest\.mark\.(skip|xfail)\b|\bpytest\.skip\s*\(")),
    ("java-disabled", re.compile(r"@(Disabled|Ignore)\b")),
    ("js-skip", re.compile(r"\.(skip|xfail)\s*\(|\b(it|describe)\.skip\s*\(")),
    ("cucumber-tag-skip", re.compile(r"~@wip\b|@skip\b")),
    ("node-process-exit-zero", re.compile(r"\bprocess\.exit\s*\(\s*0\s*\)")),
)


def scan_suppression(content: str) -> list[tuple[str, int]]:
    """Return list of (pattern_id, line_no) matches in `content`.

    line_no is 1-based.  Empty list = clean.
    """
    if not content:
        return []
    matches: list[tuple[str, int]] = []
    lines = content.splitlines()
    for line_no, line in enumerate(lines, start=1):
        for pattern_id, regex in SUPPRESSION_PATTERNS:
            if regex.search(line):
                matches.append((pattern_id, line_no))
    return matches


# ----------------------------------------------------------------------
# Install command patterns (§G.impl.7) — package-safety.sh
# ----------------------------------------------------------------------
INSTALL_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(pip3?|uv\s+pip)\s+install\b"),
    re.compile(r"\buv\s+add\b"),
    re.compile(r"\bnpm\s+(install|i|ci)\b"),
    re.compile(r"\bpnpm\s+(add|install)\b"),
    re.compile(r"\byarn\s+(add|install)\b"),
    re.compile(r"\bnpx\s+\S+"),
    re.compile(r"\buvx\s+\S+"),
)


def is_install_command(command: str) -> bool:
    """True iff command contains a package-install verb."""
    if not command:
        return False
    return any(p.search(command) for p in INSTALL_COMMAND_PATTERNS)


def extract_install_packages(command: str) -> list[str]:
    """Best-effort extract package names from an install-command shape.

    Handles:
        pip install foo bar
        pip install -r requirements.txt   (returns empty — file install)
        npm install foo@1.2 --save-dev    (filters flags)
        uv add foo
        npx some-tool                      (returns ["some-tool"])
        uvx some-tool

    Flags (anything starting with `-`) and the install verbs themselves
    are filtered out.
    """
    if not command:
        return []
    tokens = command.split()
    # Find the install verb position.
    install_verbs = {
        "install", "i", "ci", "add",
    }
    pkg_tokens: list[str] = []
    seen_verb = False
    seen_runner = False
    for tok in tokens:
        if tok in {"pip", "pip3", "uv", "npm", "pnpm", "yarn"}:
            continue
        if tok in {"npx", "uvx"}:
            seen_runner = True
            continue
        if tok in install_verbs:
            seen_verb = True
            continue
        if not (seen_verb or seen_runner):
            continue
        if tok.startswith("-"):
            continue
        # Heuristic: requirements-file install — skip the file token, return empty.
        if tok.endswith(".txt") or tok.endswith(".lock") or tok.endswith(".toml"):
            return []
        pkg_tokens.append(tok)
        if seen_runner:
            # npx/uvx invocations run a single package; stop after first.
            break
    return pkg_tokens


# ----------------------------------------------------------------------
# DDL command patterns (§G.impl.8) — safe-ddl.sh
# ----------------------------------------------------------------------
DDL_KEYWORDS: tuple[str, ...] = (
    "ALTER", "CREATE", "DROP", "RENAME", "TRUNCATE",
)

DDL_COMMAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mysql-inline",
     re.compile(
         r"\bmysql\b.*-e\s+[\"']?\s*(ALTER|CREATE|DROP|RENAME|TRUNCATE)\b",
         re.IGNORECASE,
     )),
    ("psql-inline",
     re.compile(
         r"\bpsql\b.*-c\s+[\"']?\s*(ALTER|CREATE|DROP|RENAME|TRUNCATE)\b",
         re.IGNORECASE,
     )),
    ("mysql-file-redirect",
     re.compile(r"\bmysql\b[^|]*<\s+(\S+\.sql)\b")),
    ("psql-file-flag",
     re.compile(r"\bpsql\b[^|]*-f\s+(\S+\.sql)\b")),
)


def scan_ddl_command(command: str) -> tuple[str, str] | None:
    """Return (pattern_id, sanitized_excerpt) if DDL match, else None.

    For inline shapes (mysql -e / psql -c), the pattern_id is the
    inline-shape id and excerpt is the first 80 chars of the command.
    For file-shapes (mysql < .sql / psql -f .sql), pattern_id is
    `mysql-file-redirect` / `psql-file-flag` and excerpt is the .sql
    filename — the hook then reads the file and checks for DDL content
    via `sql_file_has_ddl`.
    """
    if not command:
        return None
    for pattern_id, regex in DDL_COMMAND_PATTERNS:
        match = regex.search(command)
        if match:
            if pattern_id in ("mysql-file-redirect", "psql-file-flag"):
                return (pattern_id, match.group(1))
            return (pattern_id, command[:80])
    return None


def sql_file_has_ddl(content: str) -> bool:
    """True iff `content` contains a DDL keyword OUTSIDE string literals.

    Used by safe-ddl.sh when the trigger is a `mysql < migration.sql` or
    `psql -f migration.sql` shape — read the file content and check.
    """
    if not content:
        return False
    # Strip string literals — both single-quote and double-quote with
    # simple SQL escape rules. We do not need a full SQL parser; the
    # goal is to avoid false-positives on something like
    # ``INSERT INTO t (k) VALUES ('DROP me')``.
    stripped = re.sub(r"'(?:''|[^'])*'", "''", content)
    stripped = re.sub(r'"(?:""|[^"])*"', '""', stripped)
    # Also strip SQL comments.
    stripped = re.sub(r"--[^\n]*", "", stripped)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    for keyword in DDL_KEYWORDS:
        if re.search(rf"\b{keyword}\b", stripped, re.IGNORECASE):
            return True
    return False


# ----------------------------------------------------------------------
# Test-path globs (§G.impl.6) — chain-test-file-edit-flag.sh
# ----------------------------------------------------------------------
# These mirror `bin/_retry_loop/reversibility.py::is_test_file` but are
# enumerated here for hook-internal use + lint fixtures.
TEST_PATH_GLOBS: tuple[str, ...] = (
    "tests/**",
    "test_*.py",
    "*_test.py",
    "*.test.js",
    "*.spec.ts",
    "*.spec.tsx",
    "*.test.ts",
    "*_test.go",
    "*Test.java",
    "*Spec.java",
    "__tests__/**",
)


def matches_test_path(path: str) -> bool:
    """True iff `path` matches any test-path glob."""
    if not path:
        return False
    basename = path.rsplit("/", 1)[-1]
    for glob in TEST_PATH_GLOBS:
        if "/" in glob:
            # Directory-style: check prefix or any-depth segment.
            if glob.endswith("/**"):
                prefix = glob[:-3]
                if path == prefix or path.startswith(prefix + "/"):
                    return True
                # Any-depth segment match (e.g., `__tests__/**`).
                if f"/{prefix}/" in f"/{path}":
                    return True
        else:
            if fnmatch.fnmatch(basename, glob):
                return True
            if fnmatch.fnmatch(path, glob):
                return True
    return False


# ----------------------------------------------------------------------
# Settings content check (§G.impl.9 / §G.7.3) — sealed-paths.sh
# ----------------------------------------------------------------------
ENABLE_ALL_MCP_REGEX: re.Pattern[str] = re.compile(
    r'"enableAllProjectMcpServers"\s*:\s*true\b'
)


def scan_settings_content(content: str) -> bool:
    """True iff `content` contains `"enableAllProjectMcpServers": true`.

    Per §G.7.3 CVE-2025-59536. Whitespace-tolerant.
    """
    if not content:
        return False
    return bool(ENABLE_ALL_MCP_REGEX.search(content))


# ----------------------------------------------------------------------
# Delete-shaped Bash commands (§G.impl.5) — chain-sealed-state-delete-block.sh
# ----------------------------------------------------------------------
DELETE_SHAPED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rm",
     re.compile(r"\brm\s+(?:-[a-zA-Z]+\s+)*(?P<path>\S+)")),
    ("find-delete",
     re.compile(r"\bfind\b.*-delete\s+|\bfind\b\s+(?P<path>\S+)[^|]*-delete\b")),
    ("redirect-truncate",
     re.compile(r"(?:^|[\s;|&])(?:>|:\s*>)\s+(?P<path>\S+)")),
    ("truncate-cmd",
     re.compile(r"\btruncate\s+-s\s+0\s+(?P<path>\S+)")),
    ("mv-devnull",
     re.compile(r"\bmv\s+(?P<path>\S+)\s+/dev/null\b")),
)


def extract_delete_targets(command: str) -> list[str]:
    """Best-effort extract paths from delete-shaped Bash commands.

    Returns a list of candidate paths; caller matches them against
    `sealed_paths.txt`. May return false-positive paths (e.g., flag
    values misread); the sealed-paths matcher filters non-sealed paths.
    """
    if not command:
        return []
    targets: list[str] = []
    # `rm` may have multiple positional paths.
    for pattern_id, regex in DELETE_SHAPED_PATTERNS:
        for m in regex.finditer(command):
            path = m.group("path") if "path" in m.groupdict() else None
            if path and not path.startswith("-"):
                # `rm a b c` — grab all subsequent non-flag tokens too.
                if pattern_id == "rm":
                    after = command[m.end():]
                    targets.append(path)
                    for tok in after.split():
                        if tok.startswith("-") or any(
                            sep in tok for sep in ("|", "&", ";", ">", "<")
                        ):
                            break
                        targets.append(tok)
                else:
                    targets.append(path)
    # Dedup while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ----------------------------------------------------------------------
# LLM-emission signature heuristics (§M.impl.3) — claude-md-discipline.sh
# ----------------------------------------------------------------------
# Per implplan §M.impl.3 LLM-emission table. Hand-authored CLAUDE.md
# files do not exhibit these shapes. When the staged CLAUDE.md content
# matches one or more patterns, the pre-commit hook refuses unless the
# commit message carries the `[force-claude-md]` override token.

# `bullet-run-parallel`: ≥ 6 consecutive bullet lines with matching
# first-word prefix length (parallel structure indicator). Implemented
# in `scan_llm_emission_signature` below since this requires line-window
# analysis rather than a single regex.

_LLM_EMISSION_LINE_REGEXES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("let-me-explain",
     re.compile(
         r"^(Let me explain|To clarify|In other words|It is important to note)\b",
         re.IGNORECASE,
     )),
    ("verbose-obvious-convention",
     re.compile(
         r"^This (file|section) (contains|describes|outlines)\b",
         re.IGNORECASE,
     )),
    ("agentic-self-narration",
     re.compile(
         r"\b(I (will|am going to|have)|As an AI|As Claude)\b",
     )),
    # Match bullet lines that start with an emoji at the bullet position.
    # The Python 're' module doesn't support \U codepoint ranges directly
    # in character classes, so we scan for any non-ASCII high-codepoint
    # right after the bullet marker.
    ("markdown-emoji-bullets",
     re.compile(r"^[\*\-]\s+[\U0001F300-\U0001FAFF☀-➿]")),
)


def _scan_bullet_runs(lines: list[str]) -> list[tuple[str, int]]:
    """Detect ≥ 6 consecutive bullet lines with parallel-prefix structure.

    Heuristic: identify runs of lines that look like bullets
    (start with `* ` or `- `), then within each run check whether at
    least 6 consecutive bullets share the same first-word prefix.

    Returns a list of (pattern_id, start_line_no) for each matching run.
    """
    matches: list[tuple[str, int]] = []
    i = 0
    n = len(lines)
    bullet_re = re.compile(r"^[\*\-]\s+(\S+)")
    while i < n:
        line = lines[i]
        m = bullet_re.match(line)
        if not m:
            i += 1
            continue
        run_start = i
        first_word = m.group(1)
        # Walk consecutive bullets.
        j = i
        run: list[str] = []
        while j < n:
            mj = bullet_re.match(lines[j])
            if not mj:
                break
            run.append(mj.group(1))
            j += 1
        if len(run) >= 6:
            # Check identical-first-word OR identical-prefix-length.
            same_first = sum(1 for w in run if w == first_word) >= 6
            same_prefix_len = (
                len({len(w) for w in run[:6]}) == 1
            )
            if same_first or same_prefix_len:
                matches.append(("bullet-run-parallel", run_start + 1))
        i = max(j, i + 1)
    return matches


def scan_llm_emission_signature(content: str) -> list[tuple[str, int]]:
    """Return list of (pattern_id, line_no) LLM-emission matches.

    Empty list = clean. Caller (claude-md-discipline.sh / the Python
    backing module) uses pattern_ids to render the refusal stderr per
    implplan §M.impl.3.
    """
    if not content:
        return []
    lines = content.splitlines()
    out: list[tuple[str, int]] = []
    # Per-line single-regex matches.
    for line_no, line in enumerate(lines, start=1):
        for pattern_id, regex in _LLM_EMISSION_LINE_REGEXES:
            if regex.search(line):
                out.append((pattern_id, line_no))
    # Bullet-run window detection.
    out.extend(_scan_bullet_runs(lines))
    # Sort by line number for stable output.
    out.sort(key=lambda r: (r[1], r[0]))
    return out


__all__ = [
    "SUPPRESSION_PATTERNS",
    "scan_suppression",
    "INSTALL_COMMAND_PATTERNS",
    "is_install_command",
    "extract_install_packages",
    "DDL_KEYWORDS",
    "DDL_COMMAND_PATTERNS",
    "scan_ddl_command",
    "sql_file_has_ddl",
    "TEST_PATH_GLOBS",
    "matches_test_path",
    "ENABLE_ALL_MCP_REGEX",
    "scan_settings_content",
    "DELETE_SHAPED_PATTERNS",
    "extract_delete_targets",
    "scan_llm_emission_signature",
]
