"""T-B trace-scrub early gate (SC-B), pytest-discoverable.

Wraps the single authoritative trace-grep CI script (``tests/trace_grep.sh``)
so the ``/test`` gate runs it, and adds the SC-B structural assertions:

  * binary-artifact-absence (``__pycache__`` / ``*.pyc`` / ``*.db`` empty,
    purged BEFORE the grep);
  * extension-scope + JSON ``$id``/``$defs``/examples + fixture coverage
    (the grep script scans ``*.json`` content, so a stray host token in any
    schema ``description`` / example array is caught here);
  * absence-grep gates for ``settings_registry`` (the host runtime-config
    coupling that T-C replaces with a framework-internal resolver) — asserted
    over the route_issue scrub surface (no lingering refs in what T-B scrubbed);
  * rename-exhaustiveness for the host design-slug (``standardization`` absent);
  * functional-rename smoke test: the renamed home-slug ``splock`` is what
    ``route_issue``'s log-emit fallback resolves to (``docs/plans/splock/``),
    NOT the old host slug.

NOTE on the provisional-prefix rename-exhaustiveness absence-grep named in
SC-B: the hook-script rename (the provisional ``std-*`` prefix → ``splock-*``)
and the ``STD_*`` → ``SPLOCK_*`` env-var rename were SC-D's deliverable (T-D
owned ``hooks/`` + ``hooks.json`` + the venv sites). T-B drove only the
host-IDENTITY pattern set to clean (the MUST-FIX bar) and the host design-slug
rename, deferring the provisional-prefix sweep to T-D. As of T-D that sweep is
complete (0 provisional-prefix tokens tree-wide); the authoritative
absence-grep is owned by T-F and runs over the fully-assembled tree. This file
asserts only the host-identity pattern set (below).

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACE_GREP = REPO_ROOT / "tests" / "trace_grep.sh"

# The host-identity / host-residue pattern set (MUST be absent tree-wide).
# Mirrors trace_grep.sh. The last two (std_modulize, PP_DB) are the T-F residue
# extension: a build-provenance slug + the host DB-env-var prefix, scrubbed
# in-place and pinned absent. This file is itself a GATE_SELF carve-out (see
# GATE_SELF_FILES), so listing the tokens here does not self-match.
HOST_PATTERNS = [
    "pp-extraction-automation",
    "/home/bill",
    "standardization",
    "Standardization",
    "billstagg",
    "bill@adknown",
    "adknown",
    "adsapphire",
    "Bill Stagg",
    "everybidet",
    "pp_extraction",
    "std_modulize",
    "PP_DB",
]

EXTS = ["*.py", "*.json", "*.yaml", "*.yml", "*.md", "*.txt", "*.sh", "*.example", "*.json.example"]


def _purge_binary_artifacts() -> None:
    """Remove __pycache__/, *.pyc, .pytest_cache BEFORE any grep."""
    for d in REPO_ROOT.rglob("__pycache__"):
        if ".git" in d.parts:
            continue
        subprocess.run(["rm", "-rf", str(d)], check=False)
    for f in REPO_ROOT.rglob("*.pyc"):
        if ".git" in f.parts:
            continue
        f.unlink(missing_ok=True)
    subprocess.run(["rm", "-rf", str(REPO_ROOT / ".pytest_cache")], check=False)


# This gate's own definition files + the absence-asserting smoke-battery test
# necessarily enumerate the forbidden pattern set and would self-match —
# exclude them (they carry no host identity), just as the shell script's
# GATE_SELF carve-out does. Keep this set in lockstep with trace_grep.sh.
GATE_SELF_FILES = {
    REPO_ROOT / "tests" / "trace_grep.sh",
    REPO_ROOT / "tests" / "test_trace_scrub.py",
    REPO_ROOT / "tests" / "test_smoke_battery.py",
}


def _scanned_files() -> list[Path]:
    """Extension-scope files + extensionless bin/ wrappers (excluding .git
    and this gate's own definition files)."""
    out: set[Path] = set()
    for pat in EXTS:
        for p in REPO_ROOT.rglob(pat):
            if ".git" in p.parts or p.is_dir():
                continue
            out.add(p)
    bin_dir = REPO_ROOT / "bin"
    if bin_dir.is_dir():
        for p in bin_dir.iterdir():
            if p.is_file():
                out.add(p)
    return sorted(out - GATE_SELF_FILES)


def _is_porcelain_carveout(line: str) -> bool:
    return "--porcelain" in line


def test_binary_artifact_absence() -> None:
    """After purge, no __pycache__/*.pyc/*.db may exist (SC-B)."""
    _purge_binary_artifacts()
    leftovers = []
    for p in REPO_ROOT.rglob("*"):
        if ".git" in p.parts:
            continue
        if p.name == "__pycache__" or p.suffix in (".pyc", ".db"):
            leftovers.append(str(p.relative_to(REPO_ROOT)))
    assert not leftovers, f"binary build artifacts present after purge: {leftovers}"


def test_trace_grep_script_clean() -> None:
    """The authoritative trace-grep CI script returns CLEAN (exit 0)."""
    assert TRACE_GREP.exists(), f"trace-grep script missing: {TRACE_GREP}"
    proc = subprocess.run(
        ["bash", str(TRACE_GREP)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "trace-grep gate FAILED (host traces or binary artifacts present):\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )


@pytest.mark.parametrize("pattern", HOST_PATTERNS)
def test_host_pattern_absent_in_scope(pattern: str) -> None:
    """Each host-identity pattern is absent across the explicit extension
    scope + extensionless bin/ wrappers (the git --porcelain carve-out
    excludes legitimate flag lines)."""
    _purge_binary_artifacts()
    hits: list[str] = []
    for f in _scanned_files():
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if pattern in line and not _is_porcelain_carveout(line):
                hits.append(f"{f.relative_to(REPO_ROOT)}:{i}: {line.strip()[:120]}")
    assert not hits, f"host pattern {pattern!r} found:\n" + "\n".join(hits)


def test_settings_registry_absent_in_route_issue_scrub_surface() -> None:
    """No lingering `settings_registry` refs in the route_issue scrub surface.

    Scoped to what T-B scrubbed (per the task brief) — the framework-internal
    resolver replacement of `from console import settings_registry` is T-C's
    job; here we assert T-B's own surface introduced none.
    """
    surface = REPO_ROOT / "bin" / "_route_issue"
    hits: list[str] = []
    for f in surface.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            if "settings_registry" in line:
                hits.append(f"{f.relative_to(REPO_ROOT)}:{i}")
    assert not hits, f"settings_registry refs in route_issue scrub surface: {hits}"


def test_functional_rename_smoke_route_issue_home_slug() -> None:
    """The renamed home-slug `splock` is what route_issue's log-emit resolves
    to as its fallback plan dir — NOT the old host slug `standardization`.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from bin._route_issue import log_emit  # noqa: E402

    # No explicit plan_dir, no plan_slug -> home-slug fallback.
    resolved = log_emit.resolve_plan_dir(None, None)
    assert resolved.parts[-2:] == ("plans", "splock"), (
        f"route_issue fallback resolved to {resolved} — expected .../plans/splock"
    )
    assert "standardization" not in str(resolved)

    # An explicit slug still wins when its dir exists; when it does not exist,
    # the fallback (splock) is used — confirm the literal default is the new slug.
    resolved_named = log_emit.resolve_plan_dir(None, "nonexistent_slug_xyz")
    assert resolved_named.parts[-2:] == ("plans", "splock")
