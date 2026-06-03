"""`bin/marker register-prefix` subcommand (implplan §K.impl.7).

Exit codes:

  0 ok
  2 already-registered
  3 invalid-prefix-shape
  5 flock-contention
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

from . import log_emit
from . import prefix as prefix_module


def run(
    *,
    new_prefix: str,
    domain: str,
    owner: str,
    examples: str = "",
    dry_run: bool = False,
    json_output: bool = False,
    repo_root: Optional[Path] = None,
) -> int:
    repo_root = repo_root or _repo_root()
    registry_path = repo_root / prefix_module.REGISTRY_PATH_REL

    # Validate shape
    if not re.match(r"^[A-Z]{3,5}$", new_prefix):
        msg = f"invalid-prefix-shape: `{new_prefix}` must be 3–5 uppercase letters"
        if json_output:
            print(json.dumps({"error": "invalid-prefix-shape", "prefix": new_prefix}))
        else:
            print(msg, file=sys.stderr)
        return 3

    if not registry_path.exists():
        print(
            f"registry-not-found: {registry_path} (cannot register prefix without registry)",
            file=sys.stderr,
        )
        return 3

    # Check existing
    existing = prefix_module.parse_registry(registry_path)
    for r in existing:
        if r.prefix == new_prefix:
            state = "retired" if r.retired else "active"
            if json_output:
                print(json.dumps({
                    "error": "already-registered",
                    "prefix": new_prefix,
                    "state": state,
                }))
            else:
                print(
                    f"already-registered: `{new_prefix}` is in the {state} set",
                    file=sys.stderr,
                )
            return 2

    if not domain.strip() or not owner.strip():
        print("register-prefix requires --domain and --owner", file=sys.stderr)
        return 3

    if dry_run:
        print(f"[dry-run] would register {new_prefix}")
        print(f"  domain: {domain}")
        print(f"  owner: {owner}")
        print(f"  examples: {examples}")
        return 0

    lockfile = registry_path.parent / "prefix_registry.md.lock"
    try:
        with prefix_module.flock_path(lockfile):
            try:
                new_text = prefix_module.append_active_prefix(
                    registry_path,
                    new_prefix,
                    expansion="",  # operator may edit afterwards
                    domain=domain,
                    owner=owner,
                    examples=examples,
                )
            except ValueError as e:
                # Race: another writer added it between the pre-check + flock
                if "already registered" in str(e):
                    if json_output:
                        print(json.dumps({"error": "already-registered", "prefix": new_prefix}))
                    else:
                        print(f"already-registered: {new_prefix}", file=sys.stderr)
                    return 2
                if "Invalid prefix shape" in str(e):
                    print(f"invalid-prefix-shape: {e}", file=sys.stderr)
                    return 3
                raise
            prefix_module.atomic_write(registry_path, new_text)
            log_emit.emit_prefix_registered(
                plan_dir=None,
                prefix=new_prefix,
                domain=domain,
                owner=owner,
            )
    except BlockingIOError:
        print("flock-contention: prefix_registry.md.lock held; retry", file=sys.stderr)
        return 5

    if json_output:
        print(json.dumps({
            "result": "registered",
            "prefix": new_prefix,
            "domain": domain,
            "owner": owner,
        }))
    else:
        print(f"Registered prefix {new_prefix} (domain: {domain}; owner: {owner})")
    return 0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
