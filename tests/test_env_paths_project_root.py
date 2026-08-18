"""`bin._env_paths.project_root` resolution-chain contract.

Covers the three-rung fallback documented in docs/PLUGIN_ENV_CONTRACT.md:
$CLAUDE_PROJECT_DIR -> invoking-dir walk-up to a docs/plans/ marker
(starting at $SPLOCK_CALLER_PWD when set, since the bin/* wrappers cd into
the plugin root before exec) -> derived repo root. The derived-root rung
keeps sideloaded / in-tree behavior byte-identical to the historical
parents[2] derivation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin import _env_paths  # noqa: E402


def _clear_resolution_env(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("SPLOCK_CALLER_PWD", raising=False)


def test_env_var_wins_verbatim(monkeypatch, tmp_path):
    # Tier 1 is authoritative — no docs/plans/ marker check.
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("SPLOCK_CALLER_PWD", str(tmp_path / "ignored"))
    assert _env_paths.project_root() == tmp_path.resolve()


def test_cwd_walkup_finds_marker(monkeypatch, tmp_path):
    _clear_resolution_env(monkeypatch)
    project = tmp_path / "adopter"
    (project / "docs" / "plans").mkdir(parents=True)
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert _env_paths.project_root() == project.resolve()


def test_caller_pwd_beats_process_cwd(monkeypatch, tmp_path):
    # The bin/* wrappers cd into the plugin root before exec; they export
    # SPLOCK_CALLER_PWD so the walk starts from the invoking directory.
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    project = tmp_path / "adopter"
    (project / "docs" / "plans").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("SPLOCK_CALLER_PWD", str(project / "docs"))
    assert _env_paths.project_root() == project.resolve()


def test_derived_root_fallback(monkeypatch, tmp_path):
    # No env vars, no docs/plans anywhere up the tmp tree -> derived root,
    # exactly what parents[2] resolved to before the walk-up existed.
    _clear_resolution_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert _env_paths.project_root() == _env_paths._DERIVED_ROOT


def test_in_tree_resolution_is_unchanged(monkeypatch):
    # From inside the framework repo (which carries docs/plans/), the walk
    # resolves to the repo root — byte-identical to the old derivation.
    _clear_resolution_env(monkeypatch)
    monkeypatch.chdir(REPO_ROOT)
    assert _env_paths.project_root() == REPO_ROOT


def test_plans_dir_hangs_off_project_root(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert _env_paths.plans_dir() == tmp_path.resolve() / "docs" / "plans"


def test_create_flag_still_honoured(monkeypatch, tmp_path):
    ghost = tmp_path / "ghost"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(ghost))
    assert _env_paths.project_root() == ghost.resolve()
    assert not ghost.exists()
    assert _env_paths.project_root(create=True) == ghost.resolve()
    assert ghost.is_dir()


# --- load_env_file() default resolution -------------------------------------
#
# Regression cover for the 0.3.7 fix: the default was plugin_root()/.env, i.e.
# the plugin's own install directory. Under an installed plugin no adopter .env
# is ever read, so SPLOCK_DB_* never reach the process, the MySQL intent backend
# raises MySQLUnavailable, every call site catches it and falls back to a local
# JSONL row -- and the framework reports success while the agent_sessions mirror
# silently stops filling. The failure is invisible, so it needs a real guard.


def test_load_env_file_defaults_to_the_project_root(monkeypatch, tmp_path):
    project = tmp_path / "adopter"
    project.mkdir()
    (project / ".env").write_text("SPLOCK_TEST_REAL=from-project\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.delenv("SPLOCK_TEST_REAL", raising=False)
    try:
        _env_paths.load_env_file()
        assert os.environ.get("SPLOCK_TEST_REAL") == "from-project"
    finally:
        os.environ.pop("SPLOCK_TEST_REAL", None)


def test_load_env_file_ignores_the_plugin_install_dir(monkeypatch, tmp_path):
    # The exact 0.3.6 bug: a .env sitting in the plugin install dir must never
    # be preferred over -- or read instead of -- the adopter's.
    plugin = tmp_path / "plugin-install"
    plugin.mkdir()
    (plugin / ".env").write_text("SPLOCK_TEST_DECOY=from-plugin-dir\n", encoding="utf-8")
    project = tmp_path / "adopter"
    project.mkdir()
    (project / ".env").write_text("SPLOCK_TEST_REAL=from-project\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    for key in ("SPLOCK_TEST_DECOY", "SPLOCK_TEST_REAL"):
        monkeypatch.delenv(key, raising=False)
    try:
        _env_paths.load_env_file()
        assert os.environ.get("SPLOCK_TEST_REAL") == "from-project"
        assert "SPLOCK_TEST_DECOY" not in os.environ
    finally:
        for key in ("SPLOCK_TEST_DECOY", "SPLOCK_TEST_REAL"):
            os.environ.pop(key, None)


def test_intent_entry_points_use_the_default_resolution():
    # Both hardcoded Path(__file__)...parents[3]/.env, which is the plugin tree
    # and therefore defeats the fix above regardless of what the default does.
    for rel in ("bin/_intent/hook_writer.py", "bin/_intent/backfill_from_jsonl.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "load_env_file()" in src, f"{rel}: must call load_env_file() with no argument"
        assert "load_env_file(Path(" not in src, f"{rel}: still hardcodes a __file__-derived .env"
