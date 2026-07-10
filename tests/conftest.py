"""Suite-wide isolation from the operator's real Claude Code state.

Running this suite must never touch anything under the operator's `$HOME`. That
is not a hypothetical: `test_smoke_battery`'s consumer round-trip ran
`claude plugin marketplace add` / `install` / `uninstall` / `marketplace remove`
against the REAL registry, and its `finally` deleted the operator's actual splock
installation — twice, on two different machines — because the sandbox marketplace
shared the name. The test passed every time; it asserted on exit codes, never on
whose state it mutated. That is fixed at its call site.

The fixtures here close the quieter leaks, found by adversarial review of that
first fix:

* the hook and CLI structured-log emitters default to
  `Path.home() / ".claude" / "logs"` — redirected for the whole session;
* `splock-session-start.sh` unconditionally fires
  `bin._intent.doctor_trigger.trigger_background()`, which writes
  `~/.intent_doctor_last_run` on the REAL home and, when the stamp is stale,
  Popens a DETACHED `bin/intent doctor` that outlives pytest — redirected AND
  pre-claimed (an empty redirect target would still spawn: a missing stamp
  reads as stale);
* ambient operator exports (`CLAUDE_PROJECT_DIR`, `SPLOCK_CALLER_PWD`,
  `CLAUDE_PLUGIN_DATA`, `SPLOCK_INTENT_BACKEND`) flow into every test
  subprocess, and `bin/_env_paths` resolves + `mkdir`s through them VERBATIM —
  so a suite run from a shell set up for a real adopter repo wrote residue
  into that repo. Scrubbed for the whole session; tests that need them set
  them explicitly;
* nothing watched the registry at suite level — the per-test tripwire only
  fires when its one test runs. The byte-level guard below fingerprints the
  real registry before any test and asserts it is untouched at teardown.

Everything is set in `os.environ` rather than via monkeypatch so that hooks
spawned as SUBPROCESSES inherit it — which is how most of them run.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    # The console-script `pytest` does not put the invoking cwd on sys.path,
    # so module-level `from bin...` imports in test modules only resolved by
    # accident of invocation (`python -m pytest` from the repo root). Pin it:
    # conftests import before any test module.
    sys.path.insert(0, str(_REPO_ROOT))

_REDIRECTED = ("HOOK_LOG_ROOT", "CLI_LOG_ROOT")

#: Operator shell exports that redirect substrate path resolution or backend
#: selection inside test subprocesses. `project_root()` / `plugin_data_dir()`
#: take these VERBATIM (with `mkdir -p` semantics), and the session-start
#: hook's register leg honours `SPLOCK_INTENT_BACKEND` — an operator with
#: `mysql` exported would get fake test sessions in their real intent DB.
_SCRUBBED = (
    "CLAUDE_PROJECT_DIR",
    "SPLOCK_CALLER_PWD",
    "CLAUDE_PLUGIN_DATA",
    "SPLOCK_INTENT_BACKEND",
)


@pytest.fixture(scope="session", autouse=True)
def _isolate_operator_log_roots(tmp_path_factory):
    """Point the structured-log writers at a throwaway dir for the whole session."""
    sandbox = tmp_path_factory.mktemp("claude-logs")
    saved = {name: os.environ.get(name) for name in _REDIRECTED}
    for name in _REDIRECTED:
        os.environ[name] = str(sandbox)
    try:
        yield sandbox
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture(scope="session", autouse=True)
def _isolate_intent_doctor_state(tmp_path_factory):
    """Redirect the doctor rate-limit file AND pre-claim the slot.

    Redirecting to an EMPTY file is not enough: `should_trigger` reads a
    missing stamp as stale, claims the slot, and `trigger_background` then
    Popens a detached real `bin/intent doctor`. Pre-writing a fresh timestamp
    means every trigger site in the session sees a just-claimed slot and
    skips — no HOME write, no background process.
    """
    state = tmp_path_factory.mktemp("intent-doctor") / ".intent_doctor_last_run"
    now = datetime.datetime.now(datetime.timezone.utc)
    # Mirrors doctor_trigger._write_timestamp's format exactly.
    state.write_text(now.strftime("%Y-%m-%dT%H:%M:%SZ") + "\n", encoding="utf-8")
    saved = os.environ.get("INTENT_DOCTOR_STATE_FILE")
    os.environ["INTENT_DOCTOR_STATE_FILE"] = str(state)
    try:
        yield state
    finally:
        if saved is None:
            os.environ.pop("INTENT_DOCTOR_STATE_FILE", None)
        else:
            os.environ["INTENT_DOCTOR_STATE_FILE"] = saved


@pytest.fixture(scope="session", autouse=True)
def _scrub_ambient_resolution_env():
    """Drop ambient adopter-repo exports so subprocess resolution is hermetic."""
    saved = {name: os.environ.pop(name, None) for name in _SCRUBBED}
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


# --------------------------------------------------------------------------- #
# Real-registry byte guard                                                      #
# --------------------------------------------------------------------------- #

_PLUGINS_DIR = Path.home() / ".claude" / "plugins"
_GUARDED_REGISTRY_FILES = ("known_marketplaces.json", "installed_plugins.json")


def _registry_fingerprint() -> dict[str, object]:
    """SHA-256 of both registry files + the marketplace directory names.

    Covers all three artifacts the original incident destroyed — the per-test
    tripwire in `test_smoke_battery` watched only one of them, and only when
    that single test actually ran.
    """
    fp: dict[str, object] = {}
    for name in _GUARDED_REGISTRY_FILES:
        path = _PLUGINS_DIR / name
        fp[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    marketplaces = _PLUGINS_DIR / "marketplaces"
    fp["marketplaces/"] = (
        sorted(p.name for p in marketplaces.iterdir()) if marketplaces.is_dir() else None
    )
    return fp


@pytest.fixture(scope="session", autouse=True)
def _real_registry_byte_guard():
    """The suite-level backstop: the operator's registry, byte-identical after.

    A teardown failure here means SOME test in this run mutated real plugin
    state — find it before running again. (One benign false-positive exists:
    a live Claude Code session refreshing its marketplaces mid-run bumps
    `lastUpdated`. If the diff is only timestamps, that is the app, not a
    test — rerun to confirm.)
    """
    before = _registry_fingerprint()
    yield
    after = _registry_fingerprint()
    assert after == before, (
        "the operator's real plugin registry changed during this test run:\n"
        f"  before: {before}\n"
        f"  after:  {after}\n"
        "Some test mutated real Claude Code state. Do not rerun until the "
        "offending test is found (compare the keys that differ)."
    )


# Guard tests for these fixtures live in `tests/test_operator_state_isolation.py`.
# They cannot live here: pytest does not collect test functions from a conftest.
