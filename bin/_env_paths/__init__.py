"""Plugin path/data interface contract — the single resolver for the two
Claude Code plugin environment variables the framework binds to.

A stable, documented resolution surface so that the state backend and the
config/venv/hooks layers can depend on it WITHOUT each re-reading the raw env
var ad hoc (and without reading the var before the fallback logic exists).

Two distinct roots — do NOT conflate them:

* ``CLAUDE_PLUGIN_ROOT`` — the READ-ONLY install directory of the plugin
  (where ``agents/``, ``commands/``, ``hooks/``, ``bin/``, ``schemas/`` live).
  Claude Code may relocate or refresh this directory on plugin update, so it is
  NOT a safe place to write runtime state. Use it only for resolving shipped,
  read-only assets from shell/hook scripts.

* ``CLAUDE_PLUGIN_DATA`` — the PERSISTENT per-plugin data directory. This is the
  correct home for all mutable runtime state (the SQLite intent DB, the JSONL
  mirror, sealed local state). It survives plugin updates. (By contrast, the
  ``parents[2]`` repo-root derivation historically used for state is an
  ephemeral cache dir that Claude Code can wipe ~7 days post-update — see
  SC-C #4 — so state MUST move here, while ``parents[2]`` is retained ONLY for
  read-only ``schemas/`` + ``_roster.json`` resolution.)

Fallback semantics (defined ONCE, here):

* ``plugin_root()``  -> ``$CLAUDE_PLUGIN_ROOT`` if set, else the repository root
  derived from this module's location (``parents[2]``). The repo-root fallback
  makes the framework usable both as an installed plugin and as a sideloaded /
  in-tree checkout (``claude --plugin-dir ./``).
* ``plugin_data_dir()`` -> ``$CLAUDE_PLUGIN_DATA`` if set, else
  ``$CLAUDE_PROJECT_DIR`` if set, else the repository root. The chosen directory
  is created (``mkdir -p`` semantics) on first resolution so callers can write
  immediately.

NOTE (scope boundary): this module defines the CONTRACT only. T-C threads
``plugin_data_dir()`` through ``intent_jsonl_path()`` and the SQLite path; T-D
wires ``plugin_root()`` into the venv helper / hook path resolution. T-A neither
rewires the existing ``parents[2]`` call sites nor changes any storage backend.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repository / plugin-install root derived from this file's location:
#   bin/_env_paths/__init__.py -> parents[2] == repo root (the dir holding
#   agents/, commands/, hooks/, bin/, schemas/).
_DERIVED_ROOT = Path(__file__).resolve().parents[2]

ENV_PLUGIN_ROOT = "CLAUDE_PLUGIN_ROOT"
ENV_PLUGIN_DATA = "CLAUDE_PLUGIN_DATA"
ENV_PROJECT_DIR = "CLAUDE_PROJECT_DIR"


def plugin_root() -> Path:
    """Read-only plugin install root.

    ``$CLAUDE_PLUGIN_ROOT`` when set (installed-plugin mode); otherwise the
    repo-root fallback (sideloaded / in-tree mode). Never write runtime state
    under this path — use :func:`plugin_data_dir`.
    """
    env = os.environ.get(ENV_PLUGIN_ROOT)
    if env:
        return Path(env).resolve()
    return _DERIVED_ROOT


def plugin_data_dir(create: bool = True) -> Path:
    """Persistent per-plugin data directory for mutable runtime state.

    Resolution order: ``$CLAUDE_PLUGIN_DATA`` -> ``$CLAUDE_PROJECT_DIR`` ->
    repo-root fallback. When ``create`` is True (default) the directory is
    created if missing so callers can write immediately.
    """
    env = os.environ.get(ENV_PLUGIN_DATA) or os.environ.get(ENV_PROJECT_DIR)
    target = Path(env).resolve() if env else _DERIVED_ROOT
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def schemas_dir() -> Path:
    """Read-only ``schemas/`` directory.

    Resolved under :func:`plugin_root`. This is the read-only-asset class that
    legitimately stays anchored to the plugin/repo root (NOT the data dir).
    """
    return plugin_root() / "schemas"
