"""Suite-wide isolation from the operator's real Claude Code state.

Running this suite must never touch anything under the operator's `$HOME`. That
is not a hypothetical: `test_smoke_battery`'s consumer round-trip ran
`claude plugin marketplace add` / `install` / `uninstall` / `marketplace remove`
against the REAL registry, and its `finally` deleted the operator's actual splock
installation — twice, on two different machines — because the sandbox marketplace
shared the name. The test passed every time; it asserted on exit codes, never on
whose state it mutated. That is fixed at its call site.

This file closes the quieter half: the hook and CLI structured-log emitters
default to `Path.home() / ".claude" / "logs"`, so any test that spawns a hook
appended rows to the operator's forensic log. Both roots are redirected at the
environment for the whole session, which subprocesses inherit.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_REDIRECTED = ("HOOK_LOG_ROOT", "CLI_LOG_ROOT")


@pytest.fixture(scope="session", autouse=True)
def _isolate_operator_log_roots(tmp_path_factory):
    """Point the structured-log writers at a throwaway dir for the whole session.

    Set in `os.environ` rather than via monkeypatch so that hooks spawned as
    SUBPROCESSES inherit it — which is how most of them run.
    """
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


# The fixture's own guards live in `tests/test_operator_state_isolation.py`.
# They cannot live here: pytest does not collect test functions from a conftest.
