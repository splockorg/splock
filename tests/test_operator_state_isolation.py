"""Running the suite must not touch the operator's real Claude Code state.

The leaks below were each found only after real operator state was damaged or
dirtied — every one of them passed its tests while it happened:

1. `test_smoke_battery`'s consumer round-trip ran `claude plugin marketplace add`
   / `install` / `uninstall` / `marketplace remove` against the REAL registry.
   `cwd=tmp_path` isolated nothing — the CLI writes to the config dir. Its
   `finally` block then ran `marketplace remove splock`, deleting the operator's
   actual installation, because the sandbox marketplace shared the name. Fixed
   at the call site with `CLAUDE_CONFIG_DIR` + a byte-snapshot assertion, and
   backstopped suite-wide by `tests/conftest.py::_real_registry_byte_guard`.

2. The hook and CLI structured-log writers default to
   `Path.home() / ".claude" / "logs"`, so every test that spawned a hook appended
   rows to the operator's forensic log. `tests/conftest.py` redirects both roots
   for the whole session — but a test that builds a MINIMAL subprocess env
   silently drops the redirect, which is exactly what one of them did.

3. `splock-session-start.sh` unconditionally fires the intent-doctor trigger,
   which wrote `~/.intent_doctor_last_run` on the REAL home and could Popen a
   detached `bin/intent doctor` outliving pytest. `tests/conftest.py` redirects
   the state file AND pre-claims the rate-limit slot.

4. Ambient operator exports (`CLAUDE_PROJECT_DIR` and friends) flowed into every
   test subprocess, and the substrate `mkdir`s + writes through them verbatim —
   into whatever real adopter repo the invoking shell was set up for.

The scan at the bottom is the general guard for (2): a subprocess env literal
must carry the log roots, or inherit `os.environ`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from bin._env_paths import plugin_root
from bin._hooks.log_emit import _resolve_root

_REAL_LOG_DIR = Path.home() / ".claude" / "logs"
_TESTS_DIR = plugin_root() / "tests"


@pytest.mark.parametrize("mode", ["hook", "cli"])
def test_log_root_is_redirected_away_from_the_operators_home(mode: str) -> None:
    """Guards `tests/conftest.py`'s autouse fixture. A typo there restores the leak."""
    resolved = _resolve_root(mode)
    assert resolved != _REAL_LOG_DIR, (
        f"{mode} log root is the operator's real dir: {resolved}"
    )
    assert not str(resolved).startswith(str(Path.home() / ".claude")), resolved


def test_the_real_log_dir_is_not_writable_through_the_defaults(monkeypatch) -> None:
    """Without the redirect the default really is the operator's home.

    Pinning this makes the fixture's necessity explicit rather than folkloric.
    """
    monkeypatch.delenv("HOOK_LOG_ROOT", raising=False)
    monkeypatch.delenv("CLI_LOG_ROOT", raising=False)
    assert _resolve_root("hook") == _REAL_LOG_DIR


def test_intent_doctor_state_is_redirected_and_preclaimed() -> None:
    """Guards `_isolate_intent_doctor_state`.

    Redirected: the state file must not be the operator's real
    `~/.intent_doctor_last_run`. Pre-claimed: it must already hold a fresh
    timestamp, because an EMPTY redirect target still spawns a detached
    real `bin/intent doctor` (missing stamp reads as stale).
    """
    override = os.environ.get("INTENT_DOCTOR_STATE_FILE", "")
    assert override, "INTENT_DOCTOR_STATE_FILE is not set; the hook writes real HOME"
    state = Path(override)
    assert state != Path.home() / ".intent_doctor_last_run"
    assert not str(state).startswith(str(Path.home())), state
    assert state.is_file() and state.read_text(encoding="utf-8").strip(), (
        "the redirect target is empty — the first session-start hook fired in a "
        "test would claim the slot and spawn a detached background doctor"
    )

    from bin._intent import doctor_trigger

    assert not doctor_trigger.should_trigger(), (
        "the pre-claimed slot did not suppress the trigger; a hook-spawning "
        "test would fork a real background doctor"
    )


@pytest.mark.parametrize(
    "name",
    ["CLAUDE_PROJECT_DIR", "SPLOCK_CALLER_PWD", "CLAUDE_PLUGIN_DATA", "SPLOCK_INTENT_BACKEND"],
)
def test_ambient_resolution_env_is_scrubbed(name: str) -> None:
    """Guards `_scrub_ambient_resolution_env`.

    With any of these inherited from the operator's shell, subprocesses
    resolve plan/data dirs into a REAL adopter repo (`project_root()` takes
    the value verbatim, with mkdir), or point the intent backend at a real DB.
    """
    assert name not in os.environ, (
        f"{name} leaked into the test session from the operator's shell; "
        "tests that need it must set it explicitly"
    )


def test_the_registry_byte_guard_is_armed() -> None:
    """Guards `_real_registry_byte_guard`: its fingerprint covers all three
    artifacts the original incident destroyed."""
    from tests.conftest import _registry_fingerprint

    fp = _registry_fingerprint()
    assert set(fp) == {"known_marketplaces.json", "installed_plugins.json", "marketplaces/"}


#: `env={` as a kwarg OR `env = {` as an assignment later passed via `env=env`
#: — the assignment form is the suite's dominant idiom, and the original
#: kwarg-only regex matched exactly ONE site in the whole tree (the one that
#: had already been fixed by hand). The lookbehind rejects `hook_env={`, which
#: is a payload passed to a spawner, not a subprocess environment.
_ENV_KWARG = re.compile(r"(?<![A-Za-z0-9_])env\s*=\s*\{")


def _subprocess_env_literals() -> list[tuple[str, int, str]]:
    """Every `env = {...}` dict literal in the test suite, with file and line.

    The block is collected by brace balance, not a fixed window — a one-line
    literal yields exactly that line (the old 16-line cap swept in unrelated
    trailing lines that could satisfy the pass-condition by accident), and a
    long literal is followed to its real closing brace.
    """
    found: list[tuple[str, int, str]] = []
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue  # this file discusses `env={...}` in prose
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if not _ENV_KWARG.search(line):
                continue
            depth = 0
            block = []
            for j in range(i, min(i + 64, len(lines))):
                block.append(lines[j])
                depth += lines[j].count("{") - lines[j].count("}")
                if depth <= 0:
                    break
            found.append((str(path.relative_to(plugin_root())), i + 1, "\n".join(block)))
    return found


def test_the_scan_finds_some_env_literals_at_all() -> None:
    """Guards the guard: a broken scan would make the next test vacuous.

    Pinning the assignment-form site specifically: the original regex matched
    only inline `env={` kwargs — exactly ONE site in the tree, the one already
    fixed by hand — while the suite's dominant `env = {...}` + `env=env` idiom
    was invisible. A regression back to the narrow form must fail here.
    """
    literals = _subprocess_env_literals()
    assert literals, "env-literal scan found nothing; regex is broken"
    files = {path for path, _, _ in literals}
    assert "tests/test_framework_internal_resolver.py" in files, (
        "the scan no longer sees the assignment-form `env = {` literal in "
        "test_framework_internal_resolver.py; the regex has regressed to "
        "kwarg-only matching"
    )


def test_no_subprocess_env_literal_drops_the_log_root_redirect() -> None:
    """A minimal `env = {...}` silently un-isolates the child.

    Either inherit `os.environ`, or carry HOOK_LOG_ROOT / CLI_LOG_ROOT
    explicitly. (Substring check — a comment naming HOOK_LOG_ROOT inside the
    literal would pass; the scan is a tripwire for honest mistakes, not an
    adversary-proof parser.)
    """
    offenders = [
        f"{path}:{line}"
        for path, line, block in _subprocess_env_literals()
        if "os.environ" not in block and "HOOK_LOG_ROOT" not in block
    ]
    assert not offenders, (
        "these subprocess env literals drop the session log-root redirect, so the "
        f"child writes into the operator's ~/.claude/logs: {offenders}"
    )
