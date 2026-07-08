"""T-C (SC-C #5) — seal-glob-tracks-new-path test.

SC-C #5: "Move the seal glob in lockstep (intent_local.jsonl on seal
list ``settings.json:21-22``, ``sealed_paths.txt``)." The intent
local-JSONL relocation (and the new SQLite db) must remain covered
by the seal inventory after the path move.

This test asserts the **lockstep** invariant: whatever directory
:func:`bin._intent.db.resolve_data_root` returns at runtime, the seal
inventory at ``hooks/sealed_paths.txt`` covers the intent state files
under that directory.

Two complementary checks:

  1. The seal inventory file exists and is readable (the file's own
     existence is the seal-glob substrate; if it disappears, the
     dual-altitude seal defense collapses).

  2. The data-root + path constants are consistent: the SQLite file
     name + JSON overlay file name + JSONL mirror file name all live
     in the same dir (so a single seal-glob over that dir covers
     them). Currently this means ``intent_local.sqlite3``,
     ``intent_settings.json``, and (by convention) ``intent_local.jsonl``
     all resolve under :func:`resolve_data_root`.

Per build_decisions.md, the actual ``hooks/sealed_paths.txt`` rewrite
to track the relocated path is T-D's scope (T-D owns ``hooks/``).
This test asserts the **shape** of the lockstep on T-C's side so
T-D's rewrite has a fixed target.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._intent import db, jsonl_writer, settings as intent_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _plugin_data(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    yield


def test_sqlite_jsonl_overlay_share_one_data_root():
    """A single seal-glob entry over the data-root dir suffices — the SQLite
    db, the settings overlay, AND the JSONL mirror all co-locate there."""
    root = db.resolve_data_root()
    sqlite_path = db.intent_sqlite_path()
    overlay = intent_settings.overlay_path()
    jsonl = jsonl_writer.intent_jsonl_path()
    assert sqlite_path.parent == root
    assert overlay.parent == root
    # The JSONL mirror is now routed through db.resolve_data_root() too, so a
    # single `.plugin-data/**` seal-glob covers all three. This assertion is
    # the regression guard for the jsonl-path re-thread: a revert to the old
    # `docs/intent/` layout would fail here (and re-leak state into the repo).
    assert jsonl.parent == root


def test_data_root_env_override_is_honored(tmp_path, monkeypatch):
    """SC-C #4 — ``$CLAUDE_PLUGIN_DATA`` wins over the fallback chain."""
    custom = tmp_path / "custom_root"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(custom))
    assert db.resolve_data_root() == custom
    assert db.intent_sqlite_path().parent == custom


def test_data_root_fallback_to_project_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    # Fallback chain — no $CLAUDE_PLUGIN_DATA → use $CLAUDE_PROJECT_DIR/.plugin-data
    expected = tmp_path / db.PLUGIN_DATA_DIR_NAME
    assert db.resolve_data_root() == expected


def test_data_root_explicit_argument_overrides_env(tmp_path, monkeypatch):
    """The explicit ``data_root`` argument wins over every env var."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "/should/lose")
    custom = tmp_path / "explicit"
    assert db.resolve_data_root(custom) == custom
    assert db.intent_sqlite_path(custom).parent == custom


def test_data_root_never_lands_in_module_parents2():
    """SC-C #4 hazard — parents[2] from ``bin/_intent/db.py`` is the
    package cache dir wiped ~7 days post-update. The resolver MUST
    NOT return that location under any env setting.

    This test guards against a future refactor reintroducing the
    legacy behavior.
    """
    import os
    # Drop both env vars to force the fall-through to CWD-relative.
    saved_plugin = os.environ.pop("CLAUDE_PLUGIN_DATA", None)
    saved_project = os.environ.pop("CLAUDE_PROJECT_DIR", None)
    try:
        resolved = db.resolve_data_root()
        module_parents2 = pathlib.Path(db.__file__).resolve().parents[2]
        assert resolved != module_parents2, (
            f"resolve_data_root returned {resolved!s} which equals "
            f"parents[2] of db.py — vulnerable to cache-swap data loss"
        )
    finally:
        if saved_plugin is not None:
            os.environ["CLAUDE_PLUGIN_DATA"] = saved_plugin
        if saved_project is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = saved_project


def test_seal_inventory_file_exists():
    """The seal-glob substrate file must exist for the dual-altitude
    seal defense to function.

    NOTE: the actual rewrite of ``hooks/sealed_paths.txt`` to track
    the relocated ``${CLAUDE_PLUGIN_DATA}/intent_local.*`` glob is
    T-D's scope (T-D owns ``hooks/``). This test only asserts the
    file's presence; the content rewrite is gated on T-D.
    """
    inventory = REPO_ROOT / "hooks" / "sealed_paths.txt"
    assert inventory.exists(), (
        f"seal inventory {inventory!s} missing — dual-altitude seal "
        "defense collapses without it"
    )
