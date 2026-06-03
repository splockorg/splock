"""Baseline manifest writer (§J.impl.7).

Manifest JSON Schema lives at `schemas/baseline_manifest_v1.schema.json`.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import subprocess
from typing import Optional


BASELINE_NAME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}_[a-zA-Z0-9_-]+$")


class InvalidBaselineNameError(ValueError):
    pass


def _now_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def validate_name(name: str) -> None:
    if not BASELINE_NAME_RE.match(name):
        raise InvalidBaselineNameError(
            f"baseline_name={name!r} must match {BASELINE_NAME_RE.pattern}"
        )


def baseline_dir(plan_dir: pathlib.Path, name: str) -> pathlib.Path:
    return plan_dir / "_baseline" / name


def list_baselines(plan_dir: pathlib.Path) -> list[str]:
    root = plan_dir / "_baseline"
    if not root.exists():
        return []
    names = sorted(p.name for p in root.iterdir() if p.is_dir())
    return names


def latest_baseline(plan_dir: pathlib.Path) -> Optional[str]:
    names = list_baselines(plan_dir)
    if not names:
        return None
    # Date prefix ensures lexical sort matches chronological order.
    return names[-1]


def _git_head_sha(repo_root: pathlib.Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode("ascii").strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unknown"


def build_manifest(
    *,
    name: str,
    case_ids: list[str],
    repo_root: pathlib.Path,
    system_config_refs: Optional[dict] = None,
) -> dict:
    return {
        "schema_version": 1,
        "baseline_name": name,
        "minted_at": _now_iso_z(),
        "minted_by": "bin/eval-baseline:mint",
        "commit_sha": _git_head_sha(repo_root),
        "case_ids": list(case_ids),
        "system_config_refs": system_config_refs or {},
        "notes_path": "notes.md",
    }


def write_manifest(target: pathlib.Path, manifest: dict) -> None:
    from bin._render_plan.atomic_write import write_atomic

    body = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False)
    write_atomic(target, body + "\n")


__all__ = [
    "BASELINE_NAME_RE",
    "InvalidBaselineNameError",
    "validate_name",
    "baseline_dir",
    "list_baselines",
    "latest_baseline",
    "build_manifest",
    "write_manifest",
]
