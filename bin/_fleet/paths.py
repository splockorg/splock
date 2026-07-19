"""Path resolution for fleet — the splock generalization of the
reference implementation's three hardcoded constants.

The qum reference pinned `PLANS_DIR`, `LAUNCHER_MD` and `META_PATH` to
one repo (overridable via `FLEET_ROOT`). Here every path resolves
through `bin._env_paths` PER CALL — never cached at module level — so
the CLI and the `auto` hooks operate on whatever adopter project the
current invocation targets (`$CLAUDE_PROJECT_DIR` /
`$SPLOCK_CALLER_PWD` walk-up / derived root), including tests that
repoint the project between calls.

Opt-in contract: fleet is ACTIVE for a project iff
`docs/plans/_fleet/_fleet_meta.json` exists (created by
`bin/fleet init`). Everything else — per-slug state, the hub — hangs
off that single presence check, so an un-initialized project is
byte-for-byte unaffected by the feature.
"""

from __future__ import annotations

from pathlib import Path

from bin import _env_paths

FLEET_DIR_NAME = "_fleet"
META_NAME = "_fleet_meta.json"
STATE_NAME = "_fleet.json"
LOG_NAME = "_fleet_log.jsonl"
DEFAULT_HUB_NAME = "fleet.md"


def plans_dir() -> Path:
    """The adopter project's `docs/plans/` tree (resolved per call)."""
    return _env_paths.plans_dir()


def fleet_dir() -> Path:
    """`docs/plans/_fleet/` — home of the meta file and the default hub."""
    return plans_dir() / FLEET_DIR_NAME


def meta_path() -> Path:
    return fleet_dir() / META_NAME


def enabled() -> bool:
    """The single opt-in switch: the meta file exists."""
    return meta_path().is_file()


def slug_dir(slug: str) -> Path:
    return plans_dir() / slug


def state_path(slug: str) -> Path:
    return slug_dir(slug) / STATE_NAME


def log_path(slug: str) -> Path:
    return slug_dir(slug) / LOG_NAME


def default_hub_path() -> Path:
    return fleet_dir() / DEFAULT_HUB_NAME


def hub_path(meta: dict) -> Path:
    """The hub .md the render targets.

    `meta["hub"]` is a project-root-relative path (recorded by
    `bin/fleet init`); absent, the default scaffold location.
    """
    rel = meta.get("hub")
    if rel:
        return _env_paths.project_root() / rel
    return default_hub_path()
