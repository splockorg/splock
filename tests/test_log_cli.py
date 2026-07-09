"""`bin/log` — the non-hook CLI structured-log emitter.

`bin/_hooks/log_emit.emit()` has always supported ``mode="cli"`` (writing
``cli-<date>.jsonl`` with an ``emitter`` key instead of ``hooks-<date>.jsonl``
with a ``hook`` key), but until `bin/_log` was backported nothing in this tree
called it. These tests pin the CLI contract that the writer promises:

- argc is exactly 3 (``<emitter> <action> "<message>"``) -> else exit 1;
- ``<action>`` is closed over ``HOOK_LOG_ACTIONS`` -> else exit 4;
- ``<emitter>`` is exact-match against ``KNOWN_WRITERS`` -> else exit 4;
- an accepted row lands in ``cli-<date>.jsonl`` and NEVER in
  ``hooks-<date>.jsonl`` — the file split is what lets `jq` over hook logs
  ignore CLI rows, and it is the only reason `bin/log` exists separately
  from `bin/hook-log`.

The log roots are redirected via the ``CLI_LOG_ROOT`` / ``HOOK_LOG_ROOT``
overrides so no test ever writes to the operator's real ``~/.claude/logs``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bin._hooks import HOOK_LOG_ACTIONS
from bin._jsonl_log.writers import KNOWN_WRITERS
from bin._log.main import main

# Pinned rather than sampled from the frozenset: if this emitter is ever
# retired from KNOWN_WRITERS the guard below says so, instead of the suite
# silently re-pointing at some other member.
_VALID_EMITTER = "bin/chain-overnight"


@pytest.fixture()
def log_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect both log roots at a tmp dir; yield it."""
    monkeypatch.setenv("CLI_LOG_ROOT", str(tmp_path))
    monkeypatch.setenv("HOOK_LOG_ROOT", str(tmp_path))
    return tmp_path


def _cli_rows(root: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(root.glob("cli-*.jsonl")):
        rows += [json.loads(ln) for ln in path.read_text().splitlines() if ln]
    return rows


def test_valid_emitter_is_registered() -> None:
    assert _VALID_EMITTER in KNOWN_WRITERS


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["only-one"],
        [_VALID_EMITTER, "ok"],
        [_VALID_EMITTER, "ok", "msg", "extra"],
    ],
)
def test_wrong_argc_is_usage_error(argv: list[str], log_roots: Path) -> None:
    assert main(argv) == 1
    assert _cli_rows(log_roots) == []


def test_action_outside_closed_enum_is_rejected(log_roots: Path) -> None:
    assert "sideways" not in HOOK_LOG_ACTIONS
    assert main([_VALID_EMITTER, "sideways", "msg"]) == 4
    assert _cli_rows(log_roots) == []


def test_emitter_outside_known_writers_is_rejected(log_roots: Path) -> None:
    assert "bin/not-a-real-writer" not in KNOWN_WRITERS
    assert main(["bin/not-a-real-writer", "ok", "msg"]) == 4
    assert _cli_rows(log_roots) == []


@pytest.mark.parametrize("action", sorted(HOOK_LOG_ACTIONS))
def test_every_closed_action_is_accepted(action: str, log_roots: Path) -> None:
    assert main([_VALID_EMITTER, action, f"message for {action}"]) == 0
    rows = _cli_rows(log_roots)
    assert len(rows) == 1
    assert rows[0]["action"] == action


def test_accepted_row_uses_emitter_key_and_carries_message(log_roots: Path) -> None:
    assert main([_VALID_EMITTER, "ok", "hello"]) == 0
    (row,) = _cli_rows(log_roots)
    assert row["emitter"] == _VALID_EMITTER
    assert row["message"] == "hello"
    # `emitter` is the cli-mode key; `hook` belongs to hook-mode rows only.
    assert "hook" not in row


def test_cli_rows_never_land_in_the_hook_log(log_roots: Path) -> None:
    assert main([_VALID_EMITTER, "ok", "hello"]) == 0
    assert list(log_roots.glob("cli-*.jsonl"))
    assert list(log_roots.glob("hooks-*.jsonl")) == []


def test_appends_rather_than_truncates(log_roots: Path) -> None:
    assert main([_VALID_EMITTER, "ok", "first"]) == 0
    assert main([_VALID_EMITTER, "error", "second"]) == 0
    rows = _cli_rows(log_roots)
    assert [r["message"] for r in rows] == ["first", "second"]
