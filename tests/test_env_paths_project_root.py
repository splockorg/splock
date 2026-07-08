"""`bin._env_paths.project_root` resolution-chain contract.

Covers the three-rung fallback documented in docs/PLUGIN_ENV_CONTRACT.md:
$CLAUDE_PROJECT_DIR -> invoking-dir walk-up to a docs/plans/ marker
(starting at $SPLOCK_CALLER_PWD when set, since the bin/* wrappers cd into
the plugin root before exec) -> derived repo root. The derived-root rung
keeps sideloaded / in-tree behavior byte-identical to the historical
parents[2] derivation.
"""

from __future__ import annotations

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
