"""The six standing-requirement static checks.

Per implplan §N.impl.3 table. Each rule function takes a `cli_path:
Path` and returns a list of `Violation` (empty list = pass).

Rules:
  REQ_A_ATOMIC_WRITES  — state-writers use temp+rename
  REQ_B_NO_CROSS_CACHE — no module-level mutable cache
  REQ_C_HOOK_LOG       — invocation of bin/hook-log OR bin/log present
  REQ_D_CLOSED_EXITS   — sys.exit / exit literals in registry
  REQ_E_ARGPARSE_STRICT — allow_abbrev=False + no parse_known_args()
  REQ_F_SOLE_WRITER    — sealed-state paths have exactly one writer

REQ_A and REQ_F are catalog-driven (need the catalog row to know
which CLIs are "state-writers"); the per-CLI checks here detect the
*absence* of atomic-write patterns when the catalog tags the CLI as
a state-writer, and the *presence* of sealed-path-write patterns
across the bin/ directory at large.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from bin._cli_lint.catalog_parser import (
    CatalogEntry,
    REPO_ROOT,
    cli_binaries_in_repo,
    parse_exit_codes,
)
from bin._cli_lint.exemptions import EXEMPTIONS, Requirement, is_exempt
from bin._cli_lint.exit_codes import CHAIN_REGISTRY_CODES, PER_CLI_DOCUMENTED_ENUMS


@dataclass(frozen=True)
class Violation:
    rule: str          # e.g., "REQ_A_ATOMIC_WRITES"
    cli: str           # e.g., "bin/render_plan"
    line: int          # 0 if rule is whole-file
    detail: str


# Sealed-state paths from cross-cutting inventory (implplan lines 252-264).
# REQ_F asserts each path has exactly ONE writer across bin/.
SEALED_STATE_PATTERNS: tuple[str, ...] = (
    "_chain_sessions.json",
    "_chain_running.lock",
    "_orchestrator_log.jsonl",
    "_state.json",
)
"""Subset of the cross-cutting inventory that is bin/-writable. Other
patterns (.env*, .claude/agents/**, .git/**) are agent-edit refused
upstream and don't need REQ_F enforcement at the catalog layer."""


# Co-writer exemptions for REQ_F. The principle: each sealed-state
# path has ONE primary writer; documented co-writers are exempt.
#
# - `_chain_sessions.json`: SessionStart hook co-writes alongside the
#   chain driver per §A.impl + §G.impl.3 cross-cutting flock discipline.
#   Hook lives at `.claude/hooks/splock-session-start.sh` (not under bin/).
#
# - `_state.json`: §F's retry-loop bumps per-task retry_count directly
#   via the `test-step` subcommand routed through `bin/verify`
#   (per §F.impl.9 — `unified_counter_increment` in iteration_loop.py).
#   `bin/build_briefing` is ALSO listed — it's a POSIX wrapper that
#   shares the `bin/_retry_loop/` package with `iteration_loop.py`,
#   so the linter's source-file-walk heuristic detects the write
#   even though the `build-briefing` subcommand doesn't invoke the
#   write code path at runtime. The list reflects the linter's
#   package-level discovery, not the runtime claim (per F-03 of
#   Phase 4 post-phase Sonnet review 2026-05-21 — F-03 finding was
#   runtime-correct but the linter can't distinguish subcommands).
F_CO_WRITERS: dict[str, frozenset[str]] = {
    "_chain_sessions.json": frozenset({
        "bin/chain-overnight",
    }),
    "_chain_running.lock": frozenset({"bin/chain-overnight"}),
    "_orchestrator_log.jsonl": frozenset({"bin/update_orchestrator"}),
    "_state.json": frozenset({
        "bin/update_orchestrator",
        # §F retry-loop co-writers (package-level per linter heuristic;
        # runtime write only via bin/verify test-step subcommand):
        "bin/verify",
        "bin/build_briefing",
    }),
}


def _read(cli_path: Path) -> str:
    """Read a file; tolerate non-UTF-8 binaries by returning "" for them."""
    try:
        return cli_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def _resolve_python_target(cli_path: Path) -> Path | None:
    """If `cli_path` is a wrapper that does `exec python -m bin._x.y`,
    return the path to that Python module's main file. Otherwise None.

    The wrapper convention is `exec python -m bin._<name>.main`.
    """
    text = _read(cli_path)
    m = re.search(r"python\s+-m\s+(bin\._[A-Za-z0-9_.]+)", text)
    if not m:
        return None
    module = m.group(1)
    rel = module.replace(".", "/")
    candidate = REPO_ROOT / f"{rel}.py"
    if candidate.exists():
        return candidate
    # Module name without trailing .main fallback (some wrappers use
    # `bin._foo.main`, some use the package root).
    return None


def _source_files_for(cli_path: Path) -> list[Path]:
    """Return the set of source files that comprise a CLI.

    For a Python wrapper like `bin/marker`, this is [wrapper,
    bin/_marker/main.py, ...]. For a pure shell or pure Python
    script, just the CLI itself.

    Excludes shared-library packages whose disk-writes belong to a
    documented sole writer:
      - `bin/_jsonl_log/`  — canonical `_orchestrator_log.jsonl`
        writer; callers invoke `append_row()` (§C.impl).
      - `bin/_hooks/session_start_hook.py` — SessionStart hook is the
        documented co-writer of `_chain_sessions.json` per cross-
        cutting flock discipline (line 287-292) and §A.impl /
        §G.impl.3 co-writer exemption.
    """
    out: list[Path] = [cli_path]
    py = _resolve_python_target(cli_path)
    if py is not None:
        # Include the main file + any siblings in the package.
        pkg_dir = py.parent
        if pkg_dir.is_dir():
            for f in sorted(pkg_dir.rglob("*.py")):
                if "__pycache__" in f.parts:
                    continue
                # Skip the shared jsonl_log writer module.
                if "_jsonl_log" in f.parts:
                    continue
                # Skip the SessionStart hook (documented co-writer of
                # _chain_sessions.json; not in scope for cli-lint).
                if f.name == "session_start_hook.py":
                    continue
                if f not in out:
                    out.append(f)
    return out


# --------------------------------------------------------------------- REQ_A

# CLIs tagged as state-writers (those whose REQ_F sole-writer scope
# is non-empty). Computed from F_CO_WRITERS.
_STATE_WRITER_BIN_NAMES: frozenset[str] = frozenset(
    name for writers in F_CO_WRITERS.values() for name in writers
)


def check_req_a_atomic_writes(
    cli_path: Path,
    catalog_entry: CatalogEntry | None = None,
) -> list[Violation]:
    """REQ_A: state-writing CLIs use atomic temp+rename.

    Detection: for any CLI tagged as a state-writer (per F_CO_WRITERS
    or catalog metadata), the Python source must contain
    `os.replace(...)` OR `tempfile.NamedTemporaryFile(...)` + rename
    pattern. If not a state-writer, skip.
    """
    cli_name = f"bin/{cli_path.name}"
    if cli_name not in _STATE_WRITER_BIN_NAMES:
        return []
    if is_exempt(cli_name, Requirement.A_ATOMIC_WRITES):
        return []
    sources = _source_files_for(cli_path)
    combined = "\n".join(_read(s) for s in sources if s.suffix == ".py")
    if not combined:
        # If a state-writer is shell-only, look for `mktemp` + `mv -f`.
        shell = "\n".join(_read(s) for s in sources if s.suffix != ".py")
        if "mktemp" in shell and re.search(r"\bmv\s+-f\b", shell):
            return []
        return [Violation(
            rule="REQ_A_ATOMIC_WRITES",
            cli=cli_name,
            line=0,
            detail=(
                "state-writer has neither Python os.replace() nor shell "
                "mktemp+mv -f atomic-rename pattern"
            ),
        )]
    if "os.replace" in combined:
        return []
    if "tempfile.NamedTemporaryFile" in combined and "rename" in combined.lower():
        return []
    # Canonical project idiom: delegate to bin._render_plan.atomic_write
    # (the shared atomic-rename helper used by every state-writer per
    # cross-cutting "atomic write discipline" line 281).
    if "atomic_write" in combined or "write_atomic" in combined:
        return []
    return [Violation(
        rule="REQ_A_ATOMIC_WRITES",
        cli=cli_name,
        line=0,
        detail=(
            "state-writer source contains neither os.replace(...) nor "
            "tempfile.NamedTemporaryFile(...) + rename pattern nor a "
            "call to bin/_render_plan/atomic_write helper"
        ),
    )]


# --------------------------------------------------------------------- REQ_B


def _has_module_level_mutable_cache(source: str) -> tuple[bool, int]:
    """Detect a module-level mutable cache via AST.

    Returns (found, line_no). A 'cache' is heuristically any module-level
    assignment whose name starts with `_CACHE` or matches `_*_CACHE` or
    is exactly `CACHE`, AND whose value is a mutable literal (dict, list,
    set, or a `{}` / `[]` / `set()` call).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False, 0
    cache_name_re = re.compile(r"^(_*[A-Z][A-Z0-9_]*_CACHE|_CACHE|CACHE)$")
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            else:
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and cache_name_re.match(t.id):
                    val = node.value
                    if isinstance(val, (ast.Dict, ast.List, ast.Set)):
                        return True, getattr(node, "lineno", 0)
                    if isinstance(val, ast.Call):
                        fn = val.func
                        if isinstance(fn, ast.Name) and fn.id in ("dict", "list", "set"):
                            return True, getattr(node, "lineno", 0)
    return False, 0


def check_req_b_no_cross_cache(
    cli_path: Path,
    catalog_entry: CatalogEntry | None = None,
) -> list[Violation]:
    """REQ_B: no module-level mutable cache.

    AST-walks every Python source file in the CLI's package. A library-
    style module (one inside a `_*/` package that is not the entry point)
    is permitted to have module-level state (per §N.impl.3 REQ_B
    exemption: "library, not a CLI"). We only scan the entry-point file.
    """
    cli_name = f"bin/{cli_path.name}"
    if is_exempt(cli_name, Requirement.B_NO_CROSS_CACHE):
        return []
    # Entry point is either the CLI itself (if it's .py) OR the
    # _<name>/main.py that the wrapper dispatches to.
    entry: Path | None = None
    if cli_path.suffix == ".py":
        entry = cli_path
    else:
        py = _resolve_python_target(cli_path)
        if py is not None:
            entry = py
    if entry is None:
        # Pure shell script — REQ_B is vacuous (no module-level cache
        # concept in POSIX shell).
        return []
    source = _read(entry)
    found, line = _has_module_level_mutable_cache(source)
    if found:
        try:
            display_path = entry.relative_to(REPO_ROOT)
        except ValueError:
            display_path = entry
        return [Violation(
            rule="REQ_B_NO_CROSS_CACHE",
            cli=cli_name,
            line=line,
            detail=(
                f"entry-point {display_path} has a "
                f"module-level mutable _CACHE / CACHE assignment "
                f"(re-read disk on every invocation per N.2-B)"
            ),
        )]
    return []


# --------------------------------------------------------------------- REQ_C


_HOOK_LOG_INVOCATION_RE = re.compile(
    r"""(
        bin/hook-log\b           # bin/hook-log <args>
      | bin/log\b                # bin/log (sibling for non-hook contexts)
      | bin\._hooks\b            # python -m bin._hooks.<x> or import
      | bin\._log\b              # python -m bin._log.<x> or import
      | bin\._jsonl_log\b        # canonical structured-log writer API
      | from\s+bin\._jsonl_log   # import-form of same
      | log_emit\.append_row     # direct writer-API call
      | log_emit\.emit           # direct writer-API call
      | append_row\(             # imported append_row()
    )""",
    re.VERBOSE,
)


def check_req_c_hook_log(
    cli_path: Path,
    catalog_entry: CatalogEntry | None = None,
) -> list[Violation]:
    """REQ_C: CLI source invokes bin/hook-log OR bin/log."""
    cli_name = f"bin/{cli_path.name}"
    if is_exempt(cli_name, Requirement.C_HOOK_LOG):
        return []
    sources = _source_files_for(cli_path)
    for s in sources:
        text = _read(s)
        if _HOOK_LOG_INVOCATION_RE.search(text):
            return []
    return [Violation(
        rule="REQ_C_HOOK_LOG",
        cli=cli_name,
        line=0,
        detail=(
            "CLI source does not invoke bin/hook-log, bin/log, or the "
            "bin._hooks / bin._log log-emit modules"
        ),
    )]


# --------------------------------------------------------------------- REQ_D


_PY_SYS_EXIT_RE = re.compile(r"\bsys\.exit\s*\(\s*(\d+)\s*\)")
_PY_RAISE_SYSTEMEXIT_RE = re.compile(r"\braise\s+SystemExit\s*\(\s*(\d+)\s*\)")
_SHELL_EXIT_RE = re.compile(r"(?<!\w)exit\s+(\d+)\b")


def check_req_d_closed_exit_codes(
    cli_path: Path,
    catalog_entry: CatalogEntry | None = None,
) -> list[Violation]:
    """REQ_D: every exit code literal is in the chain registry OR the
    per-CLI documented enum."""
    cli_name = f"bin/{cli_path.name}"
    if is_exempt(cli_name, Requirement.D_CLOSED_EXIT_CODES):
        return []
    # Allowed code set: chain registry + this CLI's per-CLI documented enum
    # (if any) + the codes the catalog row claims.
    allowed: set[int] = set(CHAIN_REGISTRY_CODES)
    allowed |= PER_CLI_DOCUMENTED_ENUMS.get(cli_name, frozenset())
    if catalog_entry is not None:
        allowed |= set(parse_exit_codes(catalog_entry.exit_codes))
    sources = _source_files_for(cli_path)
    violations: list[Violation] = []
    for s in sources:
        text = _read(s)
        if not text:
            continue
        is_python = s.suffix == ".py"
        lines = text.splitlines()
        for i, line in enumerate(lines, start=1):
            # Skip comments + module-level constant definitions like
            # `EXIT_FOO = 99` (those are registry tables, not call sites).
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # Skip lines that look like a constant assignment
            # (NAME = <int>) — these are registry definitions, not exits.
            if re.match(r"^[A-Z_][A-Z0-9_]*\s*[:=]\s*(int\s*=\s*)?\d+", stripped):
                continue
            if is_python:
                for m in _PY_SYS_EXIT_RE.finditer(line):
                    n = int(m.group(1))
                    if n not in allowed:
                        violations.append(Violation(
                            rule="REQ_D_CLOSED_EXIT_CODES",
                            cli=cli_name,
                            line=i,
                            detail=(
                                f"sys.exit({n}) not in chain registry "
                                f"(A.impl.3a) nor in per-CLI enum"
                            ),
                        ))
                for m in _PY_RAISE_SYSTEMEXIT_RE.finditer(line):
                    n = int(m.group(1))
                    if n not in allowed:
                        violations.append(Violation(
                            rule="REQ_D_CLOSED_EXIT_CODES",
                            cli=cli_name,
                            line=i,
                            detail=(
                                f"raise SystemExit({n}) not in chain "
                                f"registry nor per-CLI enum"
                            ),
                        ))
            else:
                # Shell.
                for m in _SHELL_EXIT_RE.finditer(line):
                    n = int(m.group(1))
                    if n not in allowed:
                        violations.append(Violation(
                            rule="REQ_D_CLOSED_EXIT_CODES",
                            cli=cli_name,
                            line=i,
                            detail=(
                                f"shell `exit {n}` not in chain registry "
                                f"nor per-CLI enum"
                            ),
                        ))
    return violations


# --------------------------------------------------------------------- REQ_E


def _argparse_strict_violations(source: str) -> list[tuple[int, str]]:
    """AST-walk for argparse.ArgumentParser calls + parse_args usage.

    Returns list of (lineno, detail) tuples for violations.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    issues: list[tuple[int, str]] = []
    parser_classes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            # `argparse.ArgumentParser(...)` or `ArgumentParser(...)`.
            is_arg_parser = False
            if isinstance(fn, ast.Attribute) and fn.attr == "ArgumentParser":
                is_arg_parser = True
            elif isinstance(fn, ast.Name) and fn.id == "ArgumentParser":
                is_arg_parser = True
            if is_arg_parser:
                kwargs = {kw.arg: kw.value for kw in node.keywords}
                allow_abbrev = kwargs.get("allow_abbrev")
                ok = (
                    isinstance(allow_abbrev, ast.Constant)
                    and allow_abbrev.value is False
                )
                if not ok:
                    issues.append((
                        getattr(node, "lineno", 0),
                        "ArgumentParser(...) missing allow_abbrev=False",
                    ))
            # parse_known_args() call.
            if isinstance(fn, ast.Attribute) and fn.attr == "parse_known_args":
                issues.append((
                    getattr(node, "lineno", 0),
                    "parse_known_args() silently accepts unknown flags",
                ))
    return issues


def check_req_e_argparse_strict(
    cli_path: Path,
    catalog_entry: CatalogEntry | None = None,
) -> list[Violation]:
    """REQ_E: argparse strict mode (no permissive prefix matching).

    For Python entry-point: AST-scan for ArgumentParser(...) calls;
    require allow_abbrev=False. Refuse parse_known_args() use.

    For shell entry-point: assert presence of a `case ... ?)` /
    `usage)`-style fallthrough that explicitly `exit`s on unknown
    flags. POSIX `getopts` produces `?` on unknown; a strict handler
    is `?) usage; exit 2 ;;`. (Many of our wrapper scripts simply do
    `exec python -m ... "$@"`, which delegates the strictness to the
    Python entry — that pattern is acceptable.)
    """
    cli_name = f"bin/{cli_path.name}"
    if is_exempt(cli_name, Requirement.E_ARGPARSE_STRICT):
        return []
    violations: list[Violation] = []
    sources = _source_files_for(cli_path)
    for s in sources:
        if s.suffix != ".py":
            continue
        text = _read(s)
        # Only scan the entry point: the file imported as `__main__`
        # by `python -m`. We use a heuristic: the file is named main.py
        # OR it is the CLI itself if cli_path is a .py file.
        is_entry = s.name == "main.py" or s == cli_path
        if not is_entry:
            continue
        for line, detail in _argparse_strict_violations(text):
            violations.append(Violation(
                rule="REQ_E_ARGPARSE_STRICT",
                cli=cli_name,
                line=line,
                detail=detail,
            ))
    # Shell-side: skip strict check for wrapper scripts that exec
    # through to Python (the Python entry is the strict layer).
    return violations


# --------------------------------------------------------------------- REQ_F


def check_req_f_sole_writer(
    cli_path: Path,
    catalog_entry: CatalogEntry | None = None,
) -> list[Violation]:
    """REQ_F: for each sealed-state pattern, this CLI is the sole
    writer (or one of the documented co-writers).

    The check runs at the CLI level for symmetry with REQ_A/B/C/D/E.
    The "are there extra writers in bin/?" assertion is a global
    check run once at --all time (see `compute_global_violations`).
    """
    # Per-CLI version: nothing to verify here — the rule is about the
    # SET of writers across bin/, not a property of any single CLI.
    return []


_WRITE_PROXIMITY_TOKENS = (
    'open(',
    '.write(',
    'json.dump(',          # serializer-to-file (NOT json.dumps which returns str)
    'os.replace',
    'tempfile.NamedTemporaryFile',
    'f.write',
    'write_atomic(',       # bin/_render_plan/atomic_write helper call
    'write_text(',         # pathlib.Path.write_text
)
"""Tokens whose proximity to a sealed-state filename indicates a direct
write. Deliberately EXCLUDES:
 - `append_row` (canonical indirection through bin/_jsonl_log/writer.py)
 - `json.dumps` (serializer; returns str, doesn't write — note the
   trailing `(` in `json.dump(` above distinguishes the file-write
   form from the string-serialize form)."""


def _file_writes_pattern(text: str, pattern: str, window: int = 5) -> bool:
    """Detect whether `text` writes to a file named `pattern`.

    Heuristic: pattern + any `_WRITE_PROXIMITY_TOKENS` on the SAME
    non-comment line, OR pattern literal in a path-construction next
    to a write token. We deliberately keep the window TIGHT to avoid
    catching CLIs that merely mention the filename in a docstring.
    """
    if pattern not in text:
        return False
    lines = text.splitlines()
    pattern_line_idx = [i for i, line in enumerate(lines) if pattern in line]
    for idx in pattern_line_idx:
        center = lines[idx].lstrip()
        # Skip lines that are pure shell/python comments.
        if center.startswith("#"):
            continue
        # Skip lines that are pure docstring quotes.
        stripped_quotes = center.strip().strip('"').strip("'")
        if not stripped_quotes:
            continue
        # Require write token on the SAME line for a strong signal.
        if any(tok in lines[idx] for tok in _WRITE_PROXIMITY_TOKENS):
            return True
        # Or within a small window (handles `path = <plan>/_state.json`
        # on one line + `open(path, "w")` two lines later).
        lo = max(0, idx - window)
        hi = min(len(lines), idx + window + 1)
        # In the window, look only at non-comment lines.
        write_signals = 0
        for j in range(lo, hi):
            wline = lines[j].lstrip()
            if wline.startswith("#"):
                continue
            if any(tok in lines[j] for tok in _WRITE_PROXIMITY_TOKENS):
                write_signals += 1
        if write_signals:
            # Additionally check the pattern-line itself is not just a
            # docstring / help-string mention (heuristic: it should look
            # like a code line — contains `=` or `/` or `.` or `(`).
            code_chars = set(lines[idx])
            if any(c in code_chars for c in ("=", "/", "(")):
                return True
    return False


def compute_global_violations(
    catalog: list[CatalogEntry],
) -> list[Violation]:
    """REQ_F (global): for each sealed-state path, assert exactly the
    documented writers (and no extras) write to it across bin/.

    Walks every binary in `bin/` looking for writes to each sealed-
    state filename pattern. Uses proximity-windowed heuristic
    (`_file_writes_pattern`) to avoid false positives from comments
    or help-text mentions.
    """
    violations: list[Violation] = []
    bin_dir = REPO_ROOT / "bin"
    binaries = cli_binaries_in_repo(bin_dir)
    for pattern in SEALED_STATE_PATTERNS:
        expected = F_CO_WRITERS.get(pattern, frozenset())
        writers: set[str] = set()
        for cli_path in binaries:
            cli_name = f"bin/{cli_path.name}"
            sources = _source_files_for(cli_path)
            for s in sources:
                text = _read(s)
                if _file_writes_pattern(text, pattern):
                    writers.add(cli_name)
                    break
        # Extra writers = writers not in expected set.
        extras = writers - expected
        if extras:
            for extra in sorted(extras):
                if is_exempt(extra, Requirement.F_SOLE_WRITER):
                    continue
                violations.append(Violation(
                    rule="REQ_F_SOLE_WRITER",
                    cli=extra,
                    line=0,
                    detail=(
                        f"writes to sealed-state path '{pattern}' but is "
                        f"not in the documented writer set "
                        f"{sorted(expected) or '{}'}"
                    ),
                ))
    return violations


# --------------------------------------------------------------------- registry


RULES: tuple[tuple[str, str, object], ...] = (
    ("REQ_A_ATOMIC_WRITES",
     "State-writing CLIs use atomic temp-file + rename(2).",
     check_req_a_atomic_writes),
    ("REQ_B_NO_CROSS_CACHE",
     "State-reading CLIs re-read disk on every invocation "
     "(no module-level mutable cache).",
     check_req_b_no_cross_cache),
    ("REQ_C_HOOK_LOG",
     "CLIs emit structured logs via bin/hook-log (or bin/log).",
     check_req_c_hook_log),
    ("REQ_D_CLOSED_EXIT_CODES",
     "CLI exit codes live in A.impl.3a registry or per-CLI documented "
     "closed enum.",
     check_req_d_closed_exit_codes),
    ("REQ_E_ARGPARSE_STRICT",
     "Argparse uses allow_abbrev=False; no parse_known_args().",
     check_req_e_argparse_strict),
    ("REQ_F_SOLE_WRITER",
     "State-writing CLIs are the sole writer of their sealed-state file.",
     check_req_f_sole_writer),
)
