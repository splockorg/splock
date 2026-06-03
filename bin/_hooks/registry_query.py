"""PyPI / npm metadata query + cache for `package-safety.sh`.

Per implplan §G.impl.7 (`registry_query.pypi_first_publish`,
`npm_first_publish`, cache at `~/.claude/cache/package-safety/`).

Cache TTL = `PACKAGE_SAFETY_AGE_THRESHOLD_DAYS` (invalidates around the
same window as the age threshold per §G.impl.7 closing paragraph).

NOTE: the hook is invoked synchronously from a PreToolUse PreToolUse
event; network calls in that path are a latency risk. The cache mitigates
repeat calls; first-call latency is operator-tolerable per the v2.7
substrate philosophy (defense > speed).

This module is DI-friendly — tests inject a fake HTTP client via
``query_pypi(http_client=...)`` / ``query_npm(http_client=...)`` to
avoid live network calls in CI.
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request


CACHE_ROOT_ENV: str = "PACKAGE_SAFETY_CACHE_ROOT"
DEFAULT_CACHE_ROOT: Path = Path.home() / ".claude" / "cache" / "package-safety"


@dataclass(frozen=True)
class PackageMetadata:
    """Per-package metadata returned by a registry query.

    `first_publish_at` is the ISO-8601 timestamp of the earliest
    release. `weekly_downloads` is best-effort (PyPI doesn't expose this
    directly; the hook may fall back to 0 and skip the floor check).
    """
    name: str
    first_publish_at: str  # ISO-8601
    weekly_downloads: int  # 0 if unknown
    cached: bool = False


def cache_root() -> Path:
    """Resolve the cache directory; honors $PACKAGE_SAFETY_CACHE_ROOT for tests."""
    override = os.environ.get(CACHE_ROOT_ENV, "").strip()
    if override:
        return Path(override)
    return DEFAULT_CACHE_ROOT


def cache_path(registry: str, package: str) -> Path:
    """Return cache file path: `<root>/<registry>/<safe-pkg>.json`."""
    safe = package.replace("/", "_").replace("@", "_at_")
    return cache_root() / registry / f"{safe}.json"


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _is_fresh(cache_file: Path, ttl_days: int) -> bool:
    """True iff `cache_file` exists AND mtime < ttl_days ago."""
    if not cache_file.exists():
        return False
    mtime = datetime.datetime.fromtimestamp(
        cache_file.stat().st_mtime, tz=datetime.timezone.utc,
    )
    age = _now_utc() - mtime
    return age.days < ttl_days


def _read_cache(cache_file: Path) -> Optional[dict]:
    try:
        return json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(cache_file: Path, data: dict) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")


HttpFetcher = Callable[[str], dict]


def _default_http_get_json(url: str, timeout: float = 10.0) -> dict:
    """Live HTTP GET returning parsed JSON. Used only when no DI client given."""
    req = urllib_request.Request(url, headers={"User-Agent": "splock/hook"})
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def query_pypi(
    package: str,
    ttl_days: int,
    http_client: HttpFetcher | None = None,
) -> PackageMetadata:
    """Look up a PyPI package's first-publish date + weekly downloads.

    Cached. Uses `https://pypi.org/pypi/<pkg>/json`.
    """
    cache_file = cache_path("pypi", package)
    if _is_fresh(cache_file, ttl_days):
        data = _read_cache(cache_file)
        if data:
            return PackageMetadata(
                name=package,
                first_publish_at=str(data.get("first_publish_at", "")),
                weekly_downloads=int(data.get("weekly_downloads", 0)),
                cached=True,
            )
    fetcher = http_client or _default_http_get_json
    try:
        payload = fetcher(f"https://pypi.org/pypi/{package}/json")
    except (urllib_error.URLError, OSError, json.JSONDecodeError) as exc:
        # Network failures resolve to "no first-publish data" which means
        # the age check is INCONCLUSIVE — caller's policy is to refuse
        # on inconclusive (fail-closed).
        return PackageMetadata(
            name=package, first_publish_at="", weekly_downloads=0, cached=False,
        )
    releases = payload.get("releases", {}) if isinstance(payload, dict) else {}
    earliest = ""
    for _ver, files in releases.items():
        if not isinstance(files, list):
            continue
        for f in files:
            if isinstance(f, dict):
                ut = f.get("upload_time_iso_8601") or f.get("upload_time")
                if ut and (not earliest or ut < earliest):
                    earliest = ut
    meta = PackageMetadata(
        name=package, first_publish_at=earliest, weekly_downloads=0, cached=False,
    )
    _write_cache(cache_file, {
        "first_publish_at": meta.first_publish_at,
        "weekly_downloads": meta.weekly_downloads,
        "ts": _now_utc().isoformat(),
    })
    return meta


def query_npm(
    package: str,
    ttl_days: int,
    http_client: HttpFetcher | None = None,
) -> PackageMetadata:
    """Look up an npm package's first-publish date.

    Cached. Uses `https://registry.npmjs.org/<pkg>`.
    """
    cache_file = cache_path("npm", package)
    if _is_fresh(cache_file, ttl_days):
        data = _read_cache(cache_file)
        if data:
            return PackageMetadata(
                name=package,
                first_publish_at=str(data.get("first_publish_at", "")),
                weekly_downloads=int(data.get("weekly_downloads", 0)),
                cached=True,
            )
    fetcher = http_client or _default_http_get_json
    try:
        payload = fetcher(f"https://registry.npmjs.org/{package}")
    except (urllib_error.URLError, OSError, json.JSONDecodeError):
        return PackageMetadata(
            name=package, first_publish_at="", weekly_downloads=0, cached=False,
        )
    time_block = payload.get("time", {}) if isinstance(payload, dict) else {}
    earliest = time_block.get("created", "") or ""
    meta = PackageMetadata(
        name=package, first_publish_at=earliest, weekly_downloads=0, cached=False,
    )
    _write_cache(cache_file, {
        "first_publish_at": meta.first_publish_at,
        "weekly_downloads": meta.weekly_downloads,
        "ts": _now_utc().isoformat(),
    })
    return meta


def age_days(first_publish_at: str) -> int | None:
    """Compute age in days from ISO-8601 first-publish timestamp.

    Returns None if the timestamp is empty / unparseable.
    """
    if not first_publish_at:
        return None
    try:
        # Tolerate trailing Z + .NNNNNN microseconds.
        ts = first_publish_at.rstrip("Z")
        if "." in ts:
            ts = ts.split(".", 1)[0]
        dt = datetime.datetime.fromisoformat(ts).replace(tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        return None
    delta = _now_utc() - dt
    return delta.days


__all__ = [
    "PackageMetadata",
    "cache_root",
    "cache_path",
    "query_pypi",
    "query_npm",
    "age_days",
]
