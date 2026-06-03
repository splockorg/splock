"""Python entry point for the package-safety PreToolUse hook.

Per plan §G.7.1 + implplan §G.impl.7. Refuses install commands that
violate version pinning, lockfile, package-age, or download-floor checks.

The lockfile / version-pin checks are local (no network); the package-age
check queries PyPI / npm via `bin._hooks.registry_query` (cached).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from bin._hooks.pattern_detect import (
    INSTALL_COMMAND_PATTERNS,
    extract_install_packages,
    is_install_command,
)
from bin._hooks.registry_query import age_days, query_npm, query_pypi


# Environment-var defaults (§I.impl registration target).
ENV_AGE_THRESHOLD: str = "PACKAGE_SAFETY_AGE_THRESHOLD_DAYS"
ENV_DOWNLOAD_FLOOR: str = "PACKAGE_SAFETY_DOWNLOAD_FLOOR"
DEFAULT_AGE_THRESHOLD: int = 14
DEFAULT_DOWNLOAD_FLOOR: int = 500


def _emit_deny(reason: str) -> None:
    envelope = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(envelope) + "\n")
    sys.stdout.flush()


def _hook_log(action: str, message: str) -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    binpath = repo_root / "bin" / "hook-log"
    if not binpath.exists():
        return
    try:
        subprocess.run(
            [str(binpath), "package-safety", action, message[:200]],
            timeout=5,
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _read_int_env(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _detect_package_manager(command: str) -> str:
    """Return 'pip' / 'npm' / 'pnpm' / 'yarn' / 'uv' / 'npx' / 'uvx' / ''."""
    for tool in ("pnpm", "yarn", "uv", "uvx", "npx", "pip3", "pip", "npm"):
        if re.search(rf"\b{re.escape(tool)}\b", command):
            return tool.replace("pip3", "pip")
    return ""


def _has_lockfile(repo_root: Path, manager: str) -> tuple[bool, str]:
    """Check whether the repo has a lockfile for `manager`.

    Returns (has_lockfile, expected_lockfile_name).
    """
    if manager in ("pip", "uv", "uvx"):
        # uv.lock OR requirements*.txt with at least one pinned == version.
        if (repo_root / "uv.lock").exists():
            return (True, "uv.lock")
        for req in repo_root.glob("requirements*.txt"):
            try:
                content = req.read_text(encoding="utf-8")
                if "==" in content:
                    return (True, req.name)
            except OSError:
                pass
        return (False, "uv.lock or requirements*.txt with == pins")
    if manager == "npm":
        return ((repo_root / "package-lock.json").exists(), "package-lock.json")
    if manager == "pnpm":
        return ((repo_root / "pnpm-lock.yaml").exists(), "pnpm-lock.yaml")
    if manager == "yarn":
        return ((repo_root / "yarn.lock").exists(), "yarn.lock")
    if manager == "npx":
        # npx runs without install; lockfile check N/A.
        return (True, "")
    return (True, "")


def _is_unbounded(version_spec: str) -> bool:
    """True iff version is `latest`, `*`, or unbounded range like `>=1.0` (no <)."""
    if not version_spec:
        return False
    if version_spec in ("latest", "*"):
        return True
    # >=X.Y without upper bound is unbounded.
    if re.match(r"^>=", version_spec) and "<" not in version_spec:
        return True
    if re.match(r"^>", version_spec) and "<" not in version_spec:
        return True
    return False


def _split_pkg_spec(pkg: str) -> tuple[str, str]:
    """Split `name@1.2.3` / `name>=1` / `name` into (name, spec)."""
    # npm-style: foo@1.2.3
    if "@" in pkg and not pkg.startswith("@"):
        name, spec = pkg.split("@", 1)
        return (name, spec)
    # pip-style: foo==1.2 / foo>=1
    m = re.match(r"^([A-Za-z0-9_.\-]+)\s*([<>=!~].*)?$", pkg)
    if m:
        return (m.group(1), (m.group(2) or "").strip())
    return (pkg, "")


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    tool_name = data.get("tool_name") or data.get("tool") or ""
    if tool_name != "Bash":
        _hook_log("ok", f"tool={tool_name} (out-of-scope)")
        return 0
    tool_input = data.get("tool_input", {}) if isinstance(data, dict) else {}
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

    if not is_install_command(command):
        _hook_log("ok", "not install command")
        return 0

    manager = _detect_package_manager(command)
    packages = extract_install_packages(command)
    if not packages:
        # Install-shaped but no positional packages (e.g., `npm ci`,
        # `pip install -r requirements.txt`) — these are honored if
        # lockfile present.
        has_lock, _ = _has_lockfile(Path.cwd(), manager)
        if has_lock or manager in ("npx", ""):
            _hook_log("ok", f"manager={manager} no-positional")
            return 0
        _emit_deny(
            f"package-safety refused {command[:80]!r}: no lockfile present "
            f"for {manager}. Industry baseline requires lockfile + pinned "
            f"versions per research_findings_v1.md §C three-layer dependency "
            f"hygiene."
        )
        _hook_log("blocked", f"manager={manager} reason=no_lockfile")
        return 0

    age_threshold = _read_int_env(ENV_AGE_THRESHOLD, DEFAULT_AGE_THRESHOLD)
    download_floor = _read_int_env(ENV_DOWNLOAD_FLOOR, DEFAULT_DOWNLOAD_FLOOR)

    # Lockfile precondition (skipped for npx/uvx which are runner shapes).
    if manager not in ("npx", "uvx"):
        has_lock, expected = _has_lockfile(Path.cwd(), manager)
        if not has_lock:
            _emit_deny(
                f"package-safety refused: no lockfile {expected!r} present "
                f"for {manager} install. Lockfile required per research_"
                f"findings_v1.md §C; v2.7 §5.A. Add the lockfile then retry."
            )
            _hook_log("blocked", f"manager={manager} reason=no_lockfile")
            return 0

    for pkg_token in packages:
        name, spec = _split_pkg_spec(pkg_token)
        if _is_unbounded(spec):
            _emit_deny(
                f"package-safety refused {name!r}: unbounded version "
                f"{spec!r}. Industry baseline: semantic versioning + "
                f"exact pinning + lockfile per research_findings_v1.md §C."
            )
            _hook_log("blocked", f"pkg={name} reason=unbounded_version")
            return 0

        # Age check via registry query.
        if manager in ("pip", "uv", "uvx"):
            meta = query_pypi(name, age_threshold)
            registry_label = "PyPI"
        elif manager in ("npm", "pnpm", "yarn", "npx"):
            meta = query_npm(name, age_threshold)
            registry_label = "npm"
        else:
            continue

        if not meta.first_publish_at:
            # INCONCLUSIVE — fail closed (no data = unsafe to install).
            _emit_deny(
                f"package-safety refused {name!r}: could not verify "
                f"first-publish date via {registry_label} registry. "
                f"Fail-closed per research_findings_v1.md §G real-world "
                f"incidents (Aikido react-codeshift; npm shai-hulud)."
            )
            _hook_log("blocked", f"pkg={name} reason=registry_unavailable")
            return 0

        days = age_days(meta.first_publish_at)
        if days is None:
            _emit_deny(
                f"package-safety refused {name!r}: unparseable "
                f"first-publish timestamp {meta.first_publish_at!r}. "
                f"Fail-closed per research_findings_v1.md §G."
            )
            _hook_log("blocked", f"pkg={name} reason=bad_timestamp")
            return 0

        if days < age_threshold:
            _emit_deny(
                f"package-safety refused {name!r}: package_too_young "
                f"(age={days}d, threshold={age_threshold}d). "
                f"Industry baseline package-age threshold = 14d (Snyk, "
                f"pnpm minimumReleaseAge). See v2.7 §5.A, "
                f"research_findings_v1.md §C, §D, §G."
            )
            _hook_log("blocked", f"pkg={name} reason=package_too_young age={days}")
            return 0

        if download_floor > 0 and meta.weekly_downloads:
            if meta.weekly_downloads < download_floor:
                _emit_deny(
                    f"package-safety refused {name!r}: "
                    f"download_count_below_floor (count="
                    f"{meta.weekly_downloads}, floor={download_floor}). "
                    f"Floor catches new typosquats with no installer base."
                )
                _hook_log(
                    "blocked",
                    f"pkg={name} reason=download_count_below_floor count={meta.weekly_downloads}",
                )
                return 0

    _hook_log("ok", f"manager={manager} pkgs={','.join(packages)[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
