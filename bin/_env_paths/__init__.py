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


def project_root(create: bool = False) -> Path:
    """The adopter project's repository root — where ``docs/plans/`` lives.

    ``$CLAUDE_PROJECT_DIR`` when set (installed-plugin mode, pointing at the
    adopting repo); otherwise the repo-root fallback (sideloaded / in-tree
    mode, where the adopter repo IS the plugin repo). This is the writable
    per-PROJECT root for plan substrate, distinct from :func:`plugin_data_dir`
    (per-plugin state) and :func:`plugin_root` (read-only shipped assets).
    """
    env = os.environ.get(ENV_PROJECT_DIR)
    target = Path(env).resolve() if env else _DERIVED_ROOT
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def plans_dir() -> Path:
    """The adopter project's ``docs/plans/`` substrate directory.

    Defined ONCE, here, so the eight ``bin/_*`` entry points that previously
    each derived ``_REPO_ROOT / "docs" / "plans"`` from their own file
    location resolve the ADOPTER's plans tree, not the plugin install tree,
    when running as an installed plugin against a foreign project.
    """
    return project_root() / "docs" / "plans"


def schemas_dir() -> Path:
    """Read-only ``schemas/`` directory.

    Resolved under :func:`plugin_root`. This is the read-only-asset class that
    legitimately stays anchored to the plugin/repo root (NOT the data dir).
    """
    return plugin_root() / "schemas"


def load_env_file(path: Path | None = None) -> None:
    """Load ``KEY=VALUE`` pairs from a ``.env`` file into ``os.environ``.

    Defined ONCE, here, so no ``bin/_*`` entry point hard-imports
    python-dotenv: the runtime tooling is stdlib-only (README/ADOPTION), and
    dotenv is a dev-venv convenience. When python-dotenv is importable it is
    used as-is; otherwise a minimal stdlib parser handles the common
    ``.env`` subset (comments, blank lines, ``export`` prefix, single/double
    quotes). Either way existing environment variables are never overwritten,
    matching ``dotenv.load_dotenv`` defaults.

    ``path`` defaults to ``plugin_root()/.env`` — the same repo-root ``.env``
    all callers previously resolved by hand.
    """
    if path is None:
        path = plugin_root() / ".env"
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        pass
    else:
        load_dotenv(dotenv_path=path)
        return
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
