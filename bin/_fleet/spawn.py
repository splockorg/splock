"""Headless child spawner — parent side of fleet C&C.

Forks a fresh, headless Claude Code session per task:

    claude -p "/splock:<stage> <slug>" --model … --effort … \\
        --permission-mode … --output-format json

**Transport requirement (billing-model constraint, not style):** children
are spawned as `claude` CLI subprocesses, NEVER via the Claude Agent
SDK — the SDK is API-key-only by policy, while the CLI reuses the
operator's subscription OAuth (and honors `CLAUDE_CODE_OAUTH_TOKEN` for
detached contexts). This module never reads or requires
`ANTHROPIC_API_KEY`.

The parent absorbs only the child's final JSON result: a detached
runner (`bin._fleet.spawn_runner`) waits on the child, stores the full
result under `docs/plans/_fleet/runs/<run_id>.json` (unique name — no
shared write target), and appends the completion row to the slug's
`_fleet_runs.jsonl`. The parent returns as soon as the "spawned" row is
durable.

Per-stage defaults live in `_fleet_meta.json`:

    "profiles": {
      "_defaults": {"permission_mode": "acceptEdits", …},
      "code":  {"model": "claude-fable-5", "effort": "xhigh",
                 "allowed_tools": ["Bash", "Edit", "Write"]},
      "recon": {"model": "claude-opus-4-8", "effort": "high"}
    },
    "max_concurrent": 4,
    "command_template": "/splock:{stage} {slug}"

CLI flags override the stage profile, which overrides `_defaults`.
All children draw one subscription pool, hence the `max_concurrent`
cap (verified 2026-07-18, CLI 2.1.214: per-child `--model`, `--effort
low..max`, `--permission-mode`, `--allowedTools`, `--max-budget-usd`,
`--output-format json` → `{result, session_id, total_cost_usd,
permission_denials, …}`; OAuth works headless; the ultracode keyword
does NOT activate in `-p` children — do not rely on it).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from bin import _env_paths
from bin._fleet import engine, paths, runs

DEFAULT_COMMAND_TEMPLATE = "/splock:{stage} {slug}"
DEFAULT_MAX_CONCURRENT = 4

#: Profile keys a stage profile / `_defaults` may set (everything else
#: in a profile dict is ignored, so meta stays forward-extensible).
PROFILE_KEYS = (
    "model",
    "effort",
    "permission_mode",
    "allowed_tools",
    "max_budget_usd",
)


class SpawnRefused(Exception):
    """Capacity or environment refusal; message is operator-facing."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_profile(meta: dict, stage: str, overrides: dict) -> dict:
    """CLI overrides > stage profile > `_defaults` > built-ins."""
    profiles = meta.get("profiles") or {}
    resolved: dict = {k: None for k in PROFILE_KEYS}
    for layer in (profiles.get("_defaults") or {}, profiles.get(stage) or {}):
        for k in PROFILE_KEYS:
            if layer.get(k) is not None:
                resolved[k] = layer[k]
    for k in PROFILE_KEYS:
        if overrides.get(k) is not None:
            resolved[k] = overrides[k]
    resolved["command_template"] = meta.get("command_template") or DEFAULT_COMMAND_TEMPLATE
    resolved["max_concurrent"] = meta.get("max_concurrent") or DEFAULT_MAX_CONCURRENT
    return resolved


def build_prompt(template: str, stage: str, slug: str,
                 suffix: str | None = None) -> str:
    prompt = template.format(stage=stage, slug=slug)
    if suffix:
        prompt += "\n\n" + suffix
    return prompt


def build_child_argv(prompt: str | None, profile: dict, *,
                     resume_session: str | None = None) -> list[str]:
    """The `claude` CLI invocation (the ONLY transport — see module doc)."""
    argv = ["claude", "-p"]
    if resume_session:
        argv += ["--resume", resume_session]
    if prompt is not None:
        argv.append(prompt)
    argv += ["--output-format", "json"]
    if profile.get("model"):
        argv += ["--model", str(profile["model"])]
    if profile.get("effort"):
        argv += ["--effort", str(profile["effort"])]
    if profile.get("permission_mode"):
        argv += ["--permission-mode", str(profile["permission_mode"])]
    tools = profile.get("allowed_tools")
    if tools:
        argv += ["--allowedTools", *[str(t) for t in tools]]
    if profile.get("max_budget_usd"):
        argv += ["--max-budget-usd", str(profile["max_budget_usd"])]
    return argv


def runs_artifact_dir() -> Path:
    """`docs/plans/_fleet/runs/` — full child JSONs + runner logs.

    Every file here is named by run_id, so concurrent runners never
    share a write target (the per-slug-files principle, kept).
    """
    return paths.fleet_dir() / "runs"


def _launch_runner(payload: dict) -> int:
    """Start the detached runner; returns its pid. Test seam."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "bin._fleet.spawn_runner",
         json.dumps(payload, ensure_ascii=False)],
        cwd=str(_env_paths.plugin_root()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # survives the parent session ending
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(_env_paths.project_root())},
    )
    return proc.pid


def spawn(
    slug: str,
    stage: str,
    *,
    overrides: dict,
    prompt_suffix: str | None = None,
    resume_session: str | None = None,
    resume_prompt: str | None = None,
    dry_run: bool = False,
    launcher=None,
) -> tuple[str, list[str]]:
    """Spawn one headless child (or resume one). Returns (run_id, argv).

    Raises SpawnRefused on capacity/environment refusals; the CLI maps
    them to closed-enum exit codes.
    """
    if launcher is None:
        launcher = _launch_runner
    if not paths.slug_dir(slug).is_dir():
        raise SpawnRefused(
            f"slug dir does not exist: {paths.slug_dir(slug)} — mkdir it first"
        )
    meta = engine.load_meta()
    profile = resolve_profile(meta, stage, overrides)

    if resume_session:
        prompt = resume_prompt
    else:
        prompt = build_prompt(profile["command_template"], stage, slug, prompt_suffix)
    argv = build_child_argv(prompt, profile, resume_session=resume_session)

    if dry_run:
        return "dry-run", argv

    if shutil.which("claude") is None:
        raise SpawnRefused(
            "`claude` CLI not found on PATH — fleet children are spawned as "
            "CLI subprocesses (subscription transport); install/log in first"
        )
    live = runs.live_run_count()
    if live >= profile["max_concurrent"]:
        raise SpawnRefused(
            f"live children ({live}) >= max_concurrent "
            f"({profile['max_concurrent']}) — all children draw one "
            f"subscription pool; wait or raise `max_concurrent` in "
            f"_fleet_meta.json"
        )

    ts = _now_iso()
    artifact_dir = runs_artifact_dir()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{ts.replace(':', '').replace('-', '')}-{os.getpid()}"
    payload = {
        "run_id": run_id,
        "slug": slug,
        "stage": stage,
        "argv": argv,
        "project_root": str(_env_paths.project_root()),
        "out_json_path": str(artifact_dir / f"{run_id}.json"),
        "log_path": str(artifact_dir / f"{run_id}.log"),
    }
    runner_pid = launcher(payload)

    row = {
        "ts": ts,
        "run_id": run_id,
        "slug": slug,
        "stage": stage,
        "event": "resumed" if resume_session else "spawned",
        "pid": runner_pid,
        "model": profile.get("model"),
        "effort": profile.get("effort"),
        "permission_mode": profile.get("permission_mode"),
    }
    if resume_session:
        row["session_id"] = resume_session
    runs.append_run(slug, row)
    return run_id, argv
