"""Running the suite must not touch the operator's real Claude Code state.

Two leaks, both found only because an operator's plugin install disappeared:

1. `test_smoke_battery`'s consumer round-trip ran `claude plugin marketplace add`
   / `install` / `uninstall` / `marketplace remove` against the REAL registry.
   `cwd=tmp_path` isolated nothing — the CLI writes to the config dir. Its
   `finally` block then ran `marketplace remove splock`, deleting the operator's
   actual installation, because the sandbox marketplace shared the name. The
   test passed every time: it asserted on exit codes, never on whose state it
   mutated. Fixed at the call site with `CLAUDE_CONFIG_DIR` + an assertion that
   the real registry's mtime is unchanged.

2. The hook and CLI structured-log writers default to
   `Path.home() / ".claude" / "logs"`, so every test that spawned a hook appended
   rows to the operator's forensic log. `tests/conftest.py` redirects both roots
   for the whole session — but a test that builds a MINIMAL subprocess `env={...}`
   silently drops the redirect, which is exactly what one of them did.

The last test below is the general guard for (2): a subprocess env literal must
carry the log roots, or inherit `os.environ`.
"""

from __future__ import annotations

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


#: `env={` as a real kwarg. The lookbehind rejects `hook_env={`, which is a
#: payload passed to a spawner, not a subprocess environment.
_ENV_KWARG = re.compile(r"(?<![A-Za-z0-9_])env=\{")


def _subprocess_env_literals() -> list[tuple[str, int, str]]:
    """Every `env={...}` dict literal in the test suite, with its file and line."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue  # this file discusses `env={...}` in prose
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if not _ENV_KWARG.search(line):
                continue
            # Collect until the closing brace of the literal (or a sane cap).
            block = []
            for j in range(i, min(i + 16, len(lines))):
                block.append(lines[j])
                if lines[j].strip().startswith("}"):
                    break
            found.append((str(path.relative_to(plugin_root())), i + 1, "\n".join(block)))
    return found


def test_the_scan_finds_some_env_literals_at_all() -> None:
    """Guards the guard: a broken scan would make the next test vacuous."""
    assert _subprocess_env_literals(), "env={...} scan found nothing; regex is broken"


def test_no_subprocess_env_literal_drops_the_log_root_redirect() -> None:
    """A minimal `env={...}` silently un-isolates the child.

    Either inherit `os.environ`, or carry HOOK_LOG_ROOT / CLI_LOG_ROOT explicitly.
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
