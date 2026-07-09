"""J.17 — `.env`-shaped file refused by BOTH driver-side AND agent-side layers.

Per inventory + userguide §6.4 (driver-side `pre_stage.py` scans every
shell `git add` for credential-shaped paths) + userguide §5.1 (agent-
side `sealed-paths.sh` PreToolUse refuses sealed-state paths) +
plan §G.7.2 (dual-altitude defense).

The two layers exist because they fire on DIFFERENT process boundaries:
  - **Layer 1 (driver-side)**: `git add` invocations the chain driver
    makes via subprocess.run never trip PreToolUse hooks (the agent
    isn't involved). `pre_stage.scan_blocklist` is the only defense.
  - **Layer 2 (agent-side)**: when an agent calls Write or Edit with
    `file_path: .env`, the PreToolUse `sealed-paths.sh` hook fires
    against the proposed write before the tool runs.

This test exercises each layer in isolation so a regression in either
side is caught.
"""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


# Canonical .env shapes that should be refused at BOTH layers.
ENV_PATH_VARIANTS: tuple[str, ...] = (
    ".env",
    ".env.local",
    ".env.prod",
    ".env.production",
    "config/.env",
)


def _has_deny(stdout: str) -> bool:
    if not stdout.strip():
        return False
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        for line in reversed(stdout.strip().splitlines()):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        else:
            return False
    if not isinstance(payload, dict):
        return False
    if payload.get("permissionDecision") == "deny":
        return True
    hso = payload.get("hookSpecificOutput")
    return isinstance(hso, dict) and hso.get("permissionDecision") == "deny"


# ---------------------------------------------------------------------------
# Layer 1 — driver-side pre_stage scan
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env_path", ENV_PATH_VARIANTS)
def test_layer1_driver_side_pre_stage_refuses_env_variants(repo_root, env_path):
    """J.17.L1: pre_stage.scan_blocklist refuses each .env-shaped path.

    Per userguide §6.4: the chain driver's git add invocations don't
    fire PreToolUse hooks; pre_stage is the only line of defense.
    """
    from bin._chain_overnight import pre_stage

    blocklist = pre_stage.load_blocklist(repo_root)
    assert blocklist, "sealed_paths.txt is empty — load_blocklist parse failure"

    candidates = ("src/main.py", env_path)
    result = pre_stage.scan_blocklist(candidates, blocklist=blocklist)
    assert result.verdict == "refuse", (
        f"pre_stage failed to refuse '{env_path}' — driver-side credential "
        f"scan is broken. Got: verdict={result.verdict!r}, "
        f"matched_paths={result.matched_paths!r}"
    )
    assert env_path in result.matched_paths, (
        f"pre_stage matched something but not '{env_path}': "
        f"matched_paths={result.matched_paths!r}"
    )


def test_layer1_pre_stage_permits_non_env_path(repo_root):
    """J.17.L1b: control — pre_stage permits a benign path."""
    from bin._chain_overnight import pre_stage

    blocklist = pre_stage.load_blocklist(repo_root)
    result = pre_stage.scan_blocklist(("src/main.py",), blocklist=blocklist)
    assert result.verdict == "pass", (
        f"pre_stage should permit non-credential paths; got "
        f"verdict={result.verdict!r}, matched_paths={result.matched_paths!r}"
    )


# ---------------------------------------------------------------------------
# Layer 2 — agent-side sealed-paths PreToolUse hook
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env_path", ENV_PATH_VARIANTS)
def test_layer2_agent_side_sealed_paths_refuses_env_write(
    repo_root, hook_event_injector, pretool_use_event, env_path,
):
    """J.17.L2: sealed-paths.sh refuses Write tool against each .env variant."""
    hook = repo_root / "hooks" / "sealed-paths.sh"
    assert hook.exists()
    event = pretool_use_event(
        tool="Write",
        tool_input={
            "file_path": env_path,
            "content": "FAKE_KEY=fake_value\n",
        },
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)
    assert result.returncode == 0, (
        f"sealed-paths.sh expected exit 0 (deny envelope on stdout); got "
        f"{result.returncode}; stderr: {result.stderr!r}"
    )
    assert _has_deny(result.stdout), (
        f"sealed-paths.sh should refuse agent-side Write to '{env_path}'; "
        f"got stdout={result.stdout!r}"
    )


def test_layer2_agent_side_sealed_paths_permits_non_env_path(
    repo_root, hook_event_injector, pretool_use_event,
):
    """J.17.L2b: control — sealed-paths permits a benign Write."""
    hook = repo_root / "hooks" / "sealed-paths.sh"
    event = pretool_use_event(
        tool="Write",
        tool_input={
            "file_path": "src/main.py",
            "content": "print('hi')\n",
        },
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)
    assert result.returncode == 0
    assert not _has_deny(result.stdout)


# ---------------------------------------------------------------------------
# Defense-in-depth claim — both layers are wired to refuse .env independently
# ---------------------------------------------------------------------------

def test_both_layers_independently_refuse_env(
    repo_root, hook_event_injector, pretool_use_event,
):
    """J.17.dual: both layers refuse `.env` even if the OTHER layer is bypassed.

    The dual-altitude defense's whole point is that disabling one layer
    cannot make a `.env` write succeed. We verify each layer's refusal
    is independent — neither requires the other to function.
    """
    from bin._chain_overnight import pre_stage

    # Layer 1 in isolation (driver-side scan only).
    blocklist = pre_stage.load_blocklist(repo_root)
    l1_result = pre_stage.scan_blocklist((".env",), blocklist=blocklist)
    assert l1_result.verdict == "refuse"

    # Layer 2 in isolation (agent-side hook only).
    hook = repo_root / "hooks" / "sealed-paths.sh"
    event = pretool_use_event(
        tool="Write",
        tool_input={"file_path": ".env", "content": "K=V\n"},
        cwd=str(repo_root),
    )
    l2_result = hook_event_injector(hook, event)
    assert l2_result.returncode == 0
    assert _has_deny(l2_result.stdout)
