"""Acceptance test suite for the splock substrate.

Per `docs/plans/splock/splock_acceptance_inventory.md`.
This conftest provides the shared fixtures + the per-session Markdown
findings report flag.

# Constraints (per inventory §1)

- Every plan-slug fixture lives under `tmp_path` (test-isolation gap §F
  in the §10 report is real; residue at `docs/plans/synthetic-chain-slug/`
  + `test-retry-loop-slug/` keeps recurring without discipline).
- No real LLM calls; subagent invocations use recorded fixtures.
- Settings-shape tests use `canonical_settings_json` fixture (settings.json
  is gitignored; live file differs across clones).
- `bin/intent` operates in local-JSONL-only mode (§7.1 DB migration not
  run; sync_pending fallback is the expected behavior).

# Custom flag

`pytest tests/acceptance/ --acceptance-report=findings.md`
emits a per-session Markdown report categorizing PASS/FAIL/XFAIL/XPASS/SKIP
+ a Block K xfail-watcher status section.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import pytest

pytestmark = pytest.mark.acceptance


# -----------------------------------------------------------------------------
# pytest plugin hooks
# -----------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--acceptance-report",
        action="store",
        default=None,
        metavar="PATH",
        help="Write a Markdown findings report to PATH at end of session "
             "(acceptance suite only).",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "acceptance: splock acceptance test (per inventory)",
    )


# -----------------------------------------------------------------------------
# §F isolation-gap cleanup (Pass 6 hygiene fixture)
# -----------------------------------------------------------------------------
#
# Some acceptance tests inevitably escape tmp_path because the underlying
# substrate hardcodes paths from `__file__` or `$SCRIPT_DIR/../..` (e.g.,
# bin/_lessons/writer.py::_PLANS_DIR, bin/_retry_loop/halt_handoff.py).
# The proper fix is the `bound_hooks_repo` + `subprocess_cli_runner`
# fixture work (Pass 6 items 2+3) OR substrate-side env-var overrides.
# Until that lands, this autouse session-scoped fixture defensively nukes
# the known residue paths at session start AND end so `git status` stays
# clean across acceptance runs.
#
# Per the §10 report §F note + the in-flight orchestrator agent's
# coordination handoff: synthetic-chain-slug / test-retry-loop-slug are
# pre-existing leak surfaces from `test_retry_loop` integration tests
# (NOT acceptance-suite-owned). They're included here as defense in
# depth — if you reach into them, they'll be there; if you don't, this
# fixture cleans them silently.

_ACCEPTANCE_RESIDUE_PATTERNS: tuple[str, ...] = (
    "docs/plans/_acceptance_*",                     # owned by this suite's tests
    "docs/plans/acceptance_*",                      # owned by Pass 7 tests (slug schema forbids leading `_`)
    "docs/plans/synthetic-chain-slug",              # pre-existing test_retry_loop leak
    "docs/plans/test-retry-loop-slug",              # pre-existing test_retry_loop leak
    "docs/plans/scheduled_markers/morning-review",  # marker-route leak target
    "docs/plans/splock/morning-review",    # halt_handoff leak target
)


def _repo_root_path() -> Path:
    """Absolute path to the real repo root (3 parents up from this file)."""
    return Path(__file__).resolve().parents[2]


def _purge_residue() -> list[str]:
    """Delete known §F residue paths. Returns list of paths actually removed."""
    import shutil

    repo_root = _repo_root_path()
    removed: list[str] = []
    for pattern in _ACCEPTANCE_RESIDUE_PATTERNS:
        for path in repo_root.glob(pattern):
            if not path.exists():
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    path.unlink()
                except OSError:
                    pass
            if not path.exists():
                removed.append(str(path.relative_to(repo_root)))
    return removed


@pytest.fixture(scope="session", autouse=True)
def _acceptance_residue_cleanup():
    """Nuke known §F-gap residue at session start AND end.

    Per the test-isolation gap surfaced in Pass 2-5 findings + the
    in-flight orchestrator agent's coordination handoff. Defensive
    cleanup until `bound_hooks_repo` + `subprocess_cli_runner` fixture
    enhancements + substrate env-var overrides land.
    """
    start_removed = _purge_residue()
    if start_removed:
        print(
            f"\n[acceptance-cleanup] purged {len(start_removed)} residue paths at session start"
        )
    yield
    end_removed = _purge_residue()
    if end_removed:
        print(
            f"\n[acceptance-cleanup] purged {len(end_removed)} residue paths at session end"
        )


# --------------------------------------------------------------------------- #
# Fork adaptation — artifacts this repo does not ship                           #
#                                                                               #
# A handful of these tests assert against documents that live in the SOURCE     #
# repo's plan history (its userguide, implplan, design-resolution records, CLI  #
# catalog) or against `.claude/settings.json`, which is gitignored here and     #
# differs across clones. They are not repo-specific in intent — they would be   #
# meaningful in any adopter that HAS those artifacts — so they are skipped when #
# the artifact is absent rather than deleted or edited.                         #
#                                                                               #
# Keeping this in one place is deliberate: the 114 test files stay              #
# byte-comparable with upstream apart from mechanical de-hosting, so a future   #
# re-sync is a diff and not an archaeology exercise.                            #
# --------------------------------------------------------------------------- #

_REPO = _repo_root_path()
_SETTINGS_JSON = _REPO / ".claude" / "settings.json"
_PLAN_DOCS = _REPO / "docs" / "plans" / "splock"
_MARKER_LIST = _REPO / "docs" / "plans" / "scheduled_markers" / "list.md"


def _has_marker(name: str) -> bool:
    return _MARKER_LIST.is_file() and name in _MARKER_LIST.read_text(encoding="utf-8")


def _recorded_sdk_fixtures_present() -> bool:
    return any(
        (_REPO / rel).is_dir() and any((_REPO / rel).iterdir())
        for rel in (
            "tests/test_subagents/fixtures/recorded_responses",
            "tests/test_retry_loop/fixtures/recorded_sonnet",
        )
    )


#: test name -> (is-runnable predicate, why it is skipped otherwise)
_ARTIFACT_REQUIREMENTS: dict[str, tuple[Any, str]] = {
    # `.claude/settings.json` — gitignored here; the live file differs per clone.
    **{
        name: (_SETTINGS_JSON.is_file, ".claude/settings.json is not shipped (gitignored)")
        for name in (
            "test_critical_sealed_state_paths_have_settings_deny_coverage",
            "test_settings_deny_intentional_asymmetries_documented",
            "test_settings_hooks_match_canonical_anthropic_shape",
            "test_every_hook_command_resolves_on_disk",
            "test_no_loophole_writes_to_substrate_dirs",
            "test_roster_json_is_sealed_via_agents_glob",
            "test_substrate_self_mod_deny_rules_present",
        )
    },
    # The source repo's own plan documents.
    **{
        name: (
            (_PLAN_DOCS / "splock_userguide.md").is_file,
            "docs/plans/splock/splock_userguide.md is the source repo's plan history",
        )
        for name in (
            "test_userguide_documents_needs_human_halt_semantics",
            "test_userguide_132_refusal_classes_all_covered",
            "test_chain_exit_constants_documented_in_userguide_when_operator_facing",
            "test_userguide_133_codes_align_with_exit_constants",
            "test_slug_pattern_constraint_documented",
        )
    },
    "test_every_exit_constant_in_master_registry": (
        (_PLAN_DOCS / "splock_implplan.md").is_file,
        "docs/plans/splock/splock_implplan.md is the source repo's plan history",
    ),
    "test_K10_design_resolutions_r_ids_present": (
        (_REPO / "docs" / "plans" / "ccor_1" / "design_resolutions.md").is_file,
        "docs/plans/ccor_1/design_resolutions.md is the source repo's plan history",
    ),
    "test_K10_cli_catalog_carries_chain_pause_and_chain_resume_rows": (
        (_REPO / "docs" / "cli_tooling_catalog.md").is_file,
        "docs/cli_tooling_catalog.md is not shipped",
    ),
    # This repo's marker registry is a scaffold: "_No markers yet._"
    "test_srr_1_marker_active": (
        lambda: _has_marker("SRR.1"),
        "no markers minted yet in docs/plans/scheduled_markers/list.md",
    ),
    # Recorded SDK responses live with the subagent/retry-loop suites, not here.
    "test_every_recorded_fixture_parseable": (
        _recorded_sdk_fixtures_present,
        "recorded SDK response fixtures are not part of this suite's port",
    ),
}


def pytest_collection_modifyitems(config, items):
    """Skip a test when the artifact it asserts against is not shipped here."""
    for item in items:
        requirement = _ARTIFACT_REQUIREMENTS.get(getattr(item, "originalname", None) or item.name)
        if requirement is None:
            continue
        is_runnable, reason = requirement
        if not is_runnable():
            item.add_marker(pytest.mark.skip(reason=f"fork: {reason}"))


_RESULTS: dict[str, list[dict[str, Any]]] = defaultdict(list)


def pytest_runtest_logreport(report):
    """Accumulate per-test outcomes for the Markdown report."""
    if report.when != "call" and not (report.when == "setup" and report.outcome == "skipped"):
        return
    if "tests/acceptance/" not in str(report.nodeid):
        return

    # Block ID derived from filename: test_acceptance_<BLOCK>_*.py
    nodeid = report.nodeid
    block = _block_from_nodeid(nodeid)
    outcome = report.outcome  # "passed" / "failed" / "skipped"
    if outcome == "skipped" and hasattr(report, "wasxfail"):
        outcome = "xfailed"
    if outcome == "passed" and hasattr(report, "wasxfail"):
        outcome = "xpassed"
    _RESULTS[block].append({
        "nodeid": nodeid,
        "outcome": outcome,
        "longrepr": str(report.longrepr) if report.failed else "",
    })


def pytest_sessionfinish(session, exitstatus):
    report_path = session.config.getoption("--acceptance-report")
    if not report_path:
        return
    _write_acceptance_report(Path(report_path))


def _block_from_nodeid(nodeid: str) -> str:
    """Extract block ID (A/B/C/D/E/F/G/H/I/J/K) from test filename."""
    fname = nodeid.split("::")[0].rsplit("/", 1)[-1]
    if fname.startswith("test_acceptance_") and len(fname) > 16:
        return fname[16]  # the letter after "test_acceptance_"
    return "?"


def _write_acceptance_report(path: Path) -> None:
    """Emit a Markdown findings report grouped by block."""
    lines = [
        "# splock Acceptance Test Report",
        "",
        "Generated by `pytest tests/acceptance/ "
        "--acceptance-report=...`.",
        "",
        "Source-of-truth inventory: "
        "`docs/plans/splock/splock_acceptance_inventory.md`",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Block | Total | Pass | Fail | XFail | XPass | Skip |",
        "|---|---|---|---|---|---|---|",
    ]
    grand = defaultdict(int)
    for block in sorted(_RESULTS.keys()):
        counts = defaultdict(int)
        for r in _RESULTS[block]:
            counts[r["outcome"]] += 1
            grand[r["outcome"]] += 1
        total = sum(counts.values())
        lines.append(
            f"| {block} | {total} | {counts['passed']} | {counts['failed']} | "
            f"{counts['xfailed']} | {counts['xpassed']} | {counts['skipped']} |"
        )
    total = sum(grand.values())
    lines.append(
        f"| **All** | **{total}** | **{grand['passed']}** | "
        f"**{grand['failed']}** | **{grand['xfailed']}** | "
        f"**{grand['xpassed']}** | **{grand['skipped']}** |"
    )
    lines.append("")

    # Failures section
    failures = [
        (block, r)
        for block in sorted(_RESULTS.keys())
        for r in _RESULTS[block]
        if r["outcome"] == "failed"
    ]
    if failures:
        lines += ["---", "", "## Failures", ""]
        for block, r in failures:
            lines.append(f"### Block {block} — `{r['nodeid']}`")
            lines.append("")
            lines.append("```")
            lines.append(r["longrepr"][:2000])
            if len(r["longrepr"]) > 2000:
                lines.append(f"... ({len(r['longrepr']) - 2000} more chars truncated)")
            lines.append("```")
            lines.append("")

    # Block K xfail-watcher status
    k_results = _RESULTS.get("K", [])
    if k_results:
        lines += ["---", "", "## Block K — Follow-up regression watchers", ""]
        lines += [
            "| Test | Status | Note |",
            "|---|---|---|",
        ]
        for r in k_results:
            note = ""
            if r["outcome"] == "xpassed":
                note = "**FOLLOW-UP LANDED** — remove @pytest.mark.xfail + flip assertion direction"
            elif r["outcome"] == "xfailed":
                note = "Expected (residual still present per §7.4)"
            else:
                note = f"Outcome: {r['outcome']}"
            test_name = r["nodeid"].split("::")[-1]
            lines.append(f"| `{test_name}` | {r['outcome']} | {note} |")
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# -----------------------------------------------------------------------------
# Path fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def repo_root() -> Path:
    """Absolute path to the real repo root (for finding bin/ + .claude/)."""
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def tmp_slug_dir(tmp_path: Path) -> Path:
    """A per-test plan-slug dir scoped to tmp_path.

    Per inventory §1.2: every plan-slug fixture MUST live under tmp_path.
    Returns the directory; caller adds files as needed.
    """
    slug_dir = tmp_path / "docs" / "plans" / "_acceptance_test_slug"
    slug_dir.mkdir(parents=True)
    return slug_dir


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A tmp directory shaped like a repo root for tests that cd into it.

    Provides docs/plans/ + docs/plans/scheduled_markers/{list.md,prefix_registry.md}
    skeletons so marker + plan CLIs find expected structure.
    """
    (tmp_path / "docs" / "plans" / "scheduled_markers").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "scheduled_markers" / "list.md").write_text(
        "# Scheduled markers\n\n## Active entries\n\n## Closed entries\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "plans" / "scheduled_markers" / "prefix_registry.md").write_text(
        "# Marker-prefix registry\n\n## Active prefixes\n\n"
        "| Prefix | Domain | Owner | Examples |\n"
        "|---|---|---|---|\n",
        encoding="utf-8",
    )
    return tmp_path


# -----------------------------------------------------------------------------
# Hook event injection
# -----------------------------------------------------------------------------

@pytest.fixture
def hook_event_injector():
    """Helper to invoke a hook script with synthetic event JSON via stdin.

    Returns a callable: invoke(hook_path, event_dict) -> CompletedProcess.
    """
    def _invoke(hook_path: Path, event: dict[str, Any], *,
                env_overlay: Optional[dict[str, str]] = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        if env_overlay:
            env.update(env_overlay)
        # Ensure venv python is available; the hook scripts source venv themselves.
        return subprocess.run(
            ["/usr/bin/env", "bash", str(hook_path)],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
    return _invoke


@pytest.fixture
def pretool_use_event():
    """Builder for canonical PreToolUse event JSON.

    Per Anthropic Claude Code hook event spec.
    """
    def _build(tool: str, tool_input: dict[str, Any], *,
               session_id: str = "test-session-id",
               cwd: Optional[str] = None) -> dict[str, Any]:
        event = {
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "tool_name": tool,
            "tool_input": tool_input,
        }
        if cwd:
            event["cwd"] = cwd
        return event
    return _build


@pytest.fixture
def posttool_use_event():
    """Builder for canonical PostToolUse event JSON."""
    def _build(tool: str, tool_input: dict[str, Any], *,
               tool_response: Optional[dict[str, Any]] = None,
               session_id: str = "test-session-id",
               cwd: Optional[str] = None) -> dict[str, Any]:
        event = {
            "hook_event_name": "PostToolUse",
            "session_id": session_id,
            "tool_name": tool,
            "tool_input": tool_input,
            "tool_response": tool_response or {},
        }
        if cwd:
            event["cwd"] = cwd
        return event
    return _build


@pytest.fixture
def session_start_event():
    """Builder for canonical SessionStart event JSON."""
    def _build(*, source: str = "startup",
               session_id: str = "test-session-id",
               cwd: Optional[str] = None) -> dict[str, Any]:
        event = {
            "hook_event_name": "SessionStart",
            "session_id": session_id,
            "source": source,
        }
        if cwd:
            event["cwd"] = cwd
        return event
    return _build


@pytest.fixture
def stop_event():
    """Builder for canonical Stop event JSON (per Anthropic #55754)."""
    def _build(*, stop_hook_active: bool = False,
               session_id: str = "test-session-id",
               cwd: Optional[str] = None) -> dict[str, Any]:
        event = {
            "hook_event_name": "Stop",
            "session_id": session_id,
            "stop_hook_active": stop_hook_active,
        }
        if cwd:
            event["cwd"] = cwd
        return event
    return _build


# -----------------------------------------------------------------------------
# Pre-commit fixture (per Sonnet M-6 architectural split)
# -----------------------------------------------------------------------------

@pytest.fixture
def precommit_repo(tmp_path):
    """A tmp git repo for pre-commit hook tests.

    Pre-commit hooks read `git diff --cached --name-only`, not Claude
    Code event JSON. This fixture provides:

    - An initialized git repo at tmp_path
    - A `stage(rel_path, content)` helper to write + `git add` a file
    - A `repo_root` Path

    Hook scripts can then be invoked directly via subprocess with
    `cwd=repo` so `git diff --cached` resolves correctly.
    """
    import subprocess as _sp

    repo = tmp_path / "_acceptance_precommit_repo"
    repo.mkdir()

    # Initialize a quiet, isolated git repo.
    _sp.run(["git", "init", "-q"], cwd=repo, check=True)
    _sp.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    _sp.run(["git", "config", "user.name", "Test Author"], cwd=repo, check=True)
    _sp.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)

    # Bootstrap initial commit so HEAD exists.
    (repo / "README.md").write_text("init", encoding="utf-8")
    _sp.run(["git", "add", "README.md"], cwd=repo, check=True)
    _sp.run(["git", "commit", "-m", "init", "-q"], cwd=repo, check=True)

    class _PrecommitRepo:
        def __init__(self, root):
            self.root = root

        def stage(self, rel_path: str, content: str) -> Path:
            target = self.root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            _sp.run(["git", "add", rel_path], cwd=self.root, check=True)
            return target

        def staged_paths(self) -> list[str]:
            r = _sp.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=self.root, capture_output=True, text=True, check=True,
            )
            return [l for l in r.stdout.splitlines() if l]

        def run_hook(self, hook_path: Path, *, env_overlay=None, timeout=15):
            env = os.environ.copy()
            if env_overlay:
                env.update(env_overlay)
            return _sp.run(
                ["/usr/bin/env", "bash", str(hook_path)],
                cwd=self.root, capture_output=True, text=True,
                env=env, timeout=timeout,
            )

    return _PrecommitRepo(repo)


@pytest.fixture
def subprocess_cli_runner(monkeypatch, tmp_path):
    """Run CLI modules with `_PLANS_DIR` redirected to tmp_path.

    Per Pass 5 findings Pass 6 fixture #2: several CLIs (most notably
    `bin/_lessons/writer.py`, `bin/_chain_overnight/manifest.py` in some
    paths) resolve `_PLANS_DIR` from `__file__` rather than honoring an
    operator-provided override or cwd. That hardcoding leaks
    test-fixture data into the real `docs/plans/` tree (§F isolation gap).

    This fixture monkey-patches the per-module `_PLANS_DIR` constant for
    the duration of the test. CLIs that resolve `_PLANS_DIR` AT IMPORT
    TIME (most do) are patched after-the-fact; for those, the redirect
    works because subsequent reads of the module-level constant see the
    monkey-patched value.

    Returns the redirected `docs/plans/` Path; callers can write
    fixture data underneath it (e.g., `<plans_dir>/<slug>/...`).
    """
    plans_dir = tmp_path / "docs" / "plans"
    plans_dir.mkdir(parents=True)

    # Modules with hardcoded _PLANS_DIR. Extend this list as new
    # acceptance tests hit additional CLIs with the same pattern.
    HARDCODED_PLANS_DIR_MODULES = (
        "bin._lessons.writer",
        # Add more as needed; each must have a module-level `_PLANS_DIR`.
    )

    for modname in HARDCODED_PLANS_DIR_MODULES:
        try:
            mod = __import__(modname, fromlist=["_PLANS_DIR"])
        except ImportError:
            continue
        if hasattr(mod, "_PLANS_DIR"):
            monkeypatch.setattr(mod, "_PLANS_DIR", plans_dir)

    return plans_dir


@pytest.fixture
def concurrent_writer_simulator():
    """Fire N writes near-simultaneously via ThreadPoolExecutor.

    Per Pass 5 findings Pass 6 fixture #3: J.10 (completion-summary
    collision case) needs a way to trigger two writes at nearly the same
    wall-clock time so the atomic-write discipline (write-temp + rename)
    is genuinely race-tested, not just sequentially exercised.

    Returns a callable: `simulate(writer_fn, args_list, max_workers=2)`
    that maps `writer_fn(*args)` across `args_list` concurrently and
    returns the list of return values (or exceptions if any raised).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _simulate(writer_fn, args_list, *, max_workers: int = 2,
                  timeout_seconds: float = 5.0):
        results: list[Any] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(lambda a=a: writer_fn(*a)) for a in args_list]
            for fut in as_completed(futures, timeout=timeout_seconds):
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    results.append(exc)
        return results

    return _simulate


@pytest.fixture
def chain_runtime_env():
    """Helper returning a chain-runtime STD_* env overlay (SPLOCK_PHASE=5)."""
    def _build(*, slug: str = "_acceptance_chain_slug",
               chain_id: str = "chain_2026-05-22T12:00:00Z_test0000",
               phase: int = 5,
               iteration_n: int = 1) -> dict[str, str]:
        return {
            "SPLOCK_PLAN_SLUG": slug,
            "SPLOCK_CHAIN_ID": chain_id,
            "SPLOCK_PHASE": str(phase),
            "SPLOCK_ITERATION_N": str(iteration_n),
        }
    return _build
