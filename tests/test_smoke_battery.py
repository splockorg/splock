"""End-to-end smoke battery — the fresh-repo success bar.

This module collects the smoke gates that were specified as prose checks and
binds each to a real, runnable assertion in the assembled tree:

  * plugin-manifest structural validity (always) + an opportunistic
    ``claude plugin validate`` run when the CLI is on PATH (skipped otherwise);
  * self-hosted-marketplace consumer round-trip — the local ``marketplace.json``
    is structurally resolvable as a self-hosted source, with an opportunistic
    ``claude plugin marketplace add`` round-trip when the CLI is present;
  * the two-call ``/plan`` -> ``/implplan`` emission gate, exercised against a
    recording mock SDK client so the "two DISTINCT messages.create calls"
    invariant is enforced by construction (no network, no API key);
  * sealed-path ``*_plan.json`` edit-denial (the sealed-path matcher refuses a
    plan-substrate path);
  * a marker schema round-trip (build a representative row, validate it against
    ``marker_v1``, and confirm a corrupted row is rejected);
  * a git-provenance ratchet: no NEW commit carries a personal identity
    (two pre-existing leaks on public main are grandfathered by SHA; the
    frozen-release single-commit assertions are retired — the repo is a
    living fork now);
  * the CLI-version doc exists and documents the minimum CLI version.

The plugin-validate + marketplace-round-trip gates degrade to a structural
JSON check when the ``claude`` CLI is unavailable (headless CI), exactly as the
operator-approved fallback prescribes; the CLI-backed assertions are then
reported as skipped rather than failing the suite.

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_MANIFEST = REPO_ROOT / ".claude-plugin" / "marketplace.json"
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"
SEALED_PATHS_TXT = REPO_ROOT / "hooks" / "sealed_paths.txt"
CLI_VERSION_DOC = REPO_ROOT / "docs" / "CLI_VERSION.md"

#: Snapshot of the operator's real plugin registry, taken before any test runs.
#: The CLI round-trip below asserts it is byte-for-byte untouched afterwards.
_REAL_REGISTRY = Path.home() / ".claude" / "plugins" / "known_marketplaces.json"
_REAL_REGISTRY_MTIME_AT_IMPORT = (
    _REAL_REGISTRY.stat().st_mtime if _REAL_REGISTRY.is_file() else None
)

# Personal-identity tokens that MUST NOT appear in git history (or anywhere).
FORBIDDEN_IDENTITY = (
    "bill@adknown.com",
    "billstagg@gmail.com",
    "Bill Stagg",
    "billstagg",
)

# ---------------------------------------------------------------------------
# History hygiene.
#
# The initial release shipped as a single squashed commit, and two further
# provenance assertions (exactly-one-commit, org-noreply author) pinned that
# FROZEN state behind a CI-on-origin gate that nothing ever enabled — no
# workflow existed, so they never ran anywhere; had CI been added unchanged,
# they would have failed by construction the moment the fork took its first
# PR. Both are retired: the repo is a living fork with a public multi-author
# history now, and that is the intended state.
#
# What remains load-bearing — and now runs EVERYWHERE, not behind a dead
# gate — is that the ORIGINAL author's personal identity never appears in
# history. Ungating it immediately found two violations already on public
# main (below), which had sat unnoticed since June precisely because the
# guard never ran. Purging them requires rewriting published history — a
# maintainer decision, not a test's — so they are grandfathered by exact
# SHA and the invariant becomes a RATCHET: no NEW commit may carry the
# identity. Do not add to this set.
# ---------------------------------------------------------------------------
KNOWN_IDENTITY_LEAKS = frozenset({
    # docs: document dev venv (2026-06-03) — authored + committed with the
    # personal gmail identity.
    "c48974941aac3095e1fba4952530bff773bb65fc",
    # fix: caller-pwd project resolution (2026-07-08) — authored with the
    # personal work identity, committed by the adopter.
    "fa1372b48002884cce890792e9e4789e981910f0",
})


def _claude_cli() -> str | None:
    """Path to the `claude` CLI, or None if not installed."""
    return shutil.which("claude")


# ---------------------------------------------------------------------------
# (3) Plugin validation: structural always; CLI when present.
# ---------------------------------------------------------------------------
def test_plugin_and_marketplace_manifests_are_valid_json() -> None:
    """Both .claude-plugin manifests parse as JSON and carry the required keys.

    This is the unconditional structural floor the operator-approved fallback
    prescribes when the `claude` CLI is unavailable.
    """
    plugin = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    assert plugin.get("name") == "splock", "plugin.json name must be 'splock'"
    assert plugin.get("version"), "plugin.json must declare a version"

    marketplace = json.loads(MARKETPLACE_MANIFEST.read_text(encoding="utf-8"))
    assert marketplace.get("name") == "splock"
    plugins = marketplace.get("plugins")
    assert isinstance(plugins, list) and plugins, "marketplace.json must list >=1 plugin"
    # The single self-hosted plugin entry points at this repo root.
    assert any(p.get("source") == "./" for p in plugins), (
        "marketplace.json must declare the self-hosted './' plugin source"
    )


def test_hooks_json_is_valid_json() -> None:
    """The hook-enforcement spine manifest parses and declares hooks."""
    hooks = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    assert "hooks" in hooks, "hooks.json must declare a 'hooks' object"


def test_claude_plugin_validate_strict_passes_when_cli_present() -> None:
    """`claude plugin validate . --strict` exits 0 (CLI gate).

    Skipped (not failed) when the CLI is not installed — the structural JSON
    checks above are the headless-CI floor.
    """
    cli = _claude_cli()
    if cli is None:
        pytest.skip("claude CLI not on PATH; structural manifest checks cover the floor")
    proc = subprocess.run(
        [cli, "plugin", "validate", ".", "--strict"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        "claude plugin validate . --strict failed:\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )


# ---------------------------------------------------------------------------
# (4) Self-hosted-marketplace consumer round-trip.
# ---------------------------------------------------------------------------
def test_marketplace_source_is_resolvable_self_hosted_root() -> None:
    """The marketplace's single plugin source resolves to this repo root.

    Structural proof of the self-hosted deliverable that does not depend on the
    CLI: the './' source plus the plugin manifest at the repo root are exactly
    what `claude plugin marketplace add <this-dir>` consumes.
    """
    marketplace = json.loads(MARKETPLACE_MANIFEST.read_text(encoding="utf-8"))
    entry = marketplace["plugins"][0]
    source = entry["source"]
    resolved = (REPO_ROOT / source).resolve()
    assert resolved == REPO_ROOT.resolve()
    assert (resolved / ".claude-plugin" / "plugin.json").exists(), (
        "self-hosted source must contain a .claude-plugin/plugin.json"
    )


def test_marketplace_consumer_roundtrip_when_cli_present(tmp_path) -> None:
    """End-to-end consumer round-trip via the real CLI, when available.

    Adds this repo as a self-hosted marketplace, installs the plugin from it,
    then UNINSTALLS + REMOVES. Skipped when the CLI is not installed.

    ISOLATION IS LOAD-BEARING. `claude plugin` writes to the CONFIG DIR, not to
    cwd, so the original `cwd=tmp_path` isolated nothing: this test added a
    marketplace named `splock` to the operator's real registry, installed over
    their real plugin, and then — in `finally` — ran
    `marketplace remove splock`, deleting their actual installation. It shares
    the name, so teardown could not tell them apart. Two operators lost their
    splock install to this before anyone noticed, because the test PASSES either
    way: it asserts on the CLI's exit codes, never on whose registry it mutated.

    `CLAUDE_CONFIG_DIR` (plus `HOME`, belt and braces) points the CLI at a
    throwaway registry under `tmp_path`. The assertions below prove the
    isolation held, not merely that the commands exited 0.
    """
    cli = _claude_cli()
    if cli is None:
        pytest.skip("claude CLI not on PATH; see structural self-hosted check above")

    fake_home = tmp_path / "home"
    config_dir = fake_home / ".claude"
    config_dir.mkdir(parents=True)

    env = {
        **os.environ,
        "HOME": str(fake_home),
        "CLAUDE_CONFIG_DIR": str(config_dir),
    }

    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [cli, "plugin", *args],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    # Nothing of the operator's may be visible from inside the sandbox.
    listed = _run("marketplace", "list")
    assert "splock" not in listed.stdout, (
        "the sandboxed registry can see the operator's marketplaces; "
        f"CLAUDE_CONFIG_DIR is not being honoured:\n{listed.stdout}"
    )

    added = _run("marketplace", "add", str(REPO_ROOT))
    try:
        assert added.returncode == 0, (
            f"marketplace add failed:\n{added.stdout}\n{added.stderr}"
        )
        installed = _run("install", "splock@splock")
        assert installed.returncode == 0, (
            f"install failed:\n{installed.stdout}\n{installed.stderr}"
        )
        # The round-trip landed in the SANDBOX...
        assert any(config_dir.rglob("*.json")), "sandbox registry was never written"
    finally:
        _run("uninstall", "splock@splock")
        _run("marketplace", "remove", "splock")

    # ...and never in the operator's real one.
    if _REAL_REGISTRY_MTIME_AT_IMPORT is not None:
        assert _REAL_REGISTRY.stat().st_mtime == _REAL_REGISTRY_MTIME_AT_IMPORT, (
            "this test mutated the operator's real plugin registry "
            f"({_REAL_REGISTRY})"
        )


# ---------------------------------------------------------------------------
# (5) Two-call /plan -> /implplan emission gate (mocked SDK).
# ---------------------------------------------------------------------------
class _RecordingMessages:
    """Records every `create(**kwargs)` and returns scripted responses."""

    def __init__(self, scripted: list[dict]) -> None:
        self._scripted = scripted
        self.calls: list[dict] = []

    def create(self, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        # Return the next scripted response in order (Call 1, then Call 2).
        idx = len(self.calls) - 1
        return self._scripted[idx]


class _RecordingClient:
    """Minimal AnthropicClient stand-in (messages.create only)."""

    def __init__(self, scripted: list[dict]) -> None:
        self.messages = _RecordingMessages(scripted)


def _minimal_valid_plan() -> dict:
    """A small object that satisfies the plan_v1 schema's required keys.

    Built from the shipped schema's `required` list so it survives schema
    evolution without hand-maintaining a fixture; enum/typed fields get a
    schema-derived placeholder.
    """
    from bin._planner import schemas

    schema = schemas.PLAN_SCHEMA_V1
    required = schema.get("required", [])
    props = schema.get("properties", {})
    obj: dict[str, Any] = {}
    for key in required:
        spec = props.get(key, {})
        obj[key] = _placeholder_for(spec)
    return obj


def _placeholder_for(spec: dict) -> Any:
    """Produce a schema-conforming placeholder for one property spec."""
    enum = spec.get("enum")
    if enum:
        return enum[0]
    t = spec.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), t[0])
    if t == "array":
        return []
    if t == "object":
        return {}
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    # string (default): honor a pattern minimally where feasible
    return "smoke"


def test_two_call_emission_makes_two_distinct_create_calls(monkeypatch) -> None:
    """invoke_planner issues TWO distinct messages.create calls; only Call 2
    carries the structured-output format. Proves single-turn dual emission is
    impossible by construction — the CI proxy for the live two-call gate.
    """
    from bin._planner import two_call

    # Disable auto-latest-Opus discovery so the mock needs no `models` API.
    monkeypatch.setenv(two_call.AUTO_LATEST_OPUS_ENV, "0")
    monkeypatch.delenv("OVERNIGHT_CHAIN_PLANNER_MODEL", raising=False)

    plan_payload = _minimal_valid_plan()
    scripted = [
        # Call 1 (reasoning) — free-form MD text.
        {"content": [{"type": "text", "text": "# reasoning scratchpad\n- a\n- b"}]},
        # Call 2 (emission) — JSON text conforming to plan_v1.
        {"content": [{"type": "text", "text": json.dumps(plan_payload)}]},
    ]
    client = _RecordingClient(scripted)

    inputs = two_call.PlannerInputs(
        recon_findings="",
        qa_findings="",
        research_findings="",
        lessons_findings="",
        repo_state_summary="clean",
        prior_plan_json=None,
        tier="Tier 1",
    )
    result = two_call.invoke_planner("example_plan", "plan", inputs, client=client)

    # Exactly two SDK round-trips.
    assert len(client.messages.calls) == 2, (
        f"expected 2 messages.create calls, got {len(client.messages.calls)}"
    )
    call1_kwargs, call2_kwargs = client.messages.calls

    # Call 1 carries NO structured-output format (free-form reasoning).
    assert "output_config" not in call1_kwargs
    assert "response_format" not in call1_kwargs

    # Call 2 carries the json_schema constrained-decoding format.
    fmt = call2_kwargs.get("output_config", {}).get("format", {})
    assert fmt.get("type") == "json_schema", (
        "Call 2 must set output_config.format.type == 'json_schema'"
    )
    assert "schema" in fmt, "Call 2 must inline the schema fragment"

    # The emitted JSON round-trips back through the result.
    assert result.call2_emitted_json == plan_payload
    assert result.call1_reasoning_md.startswith("# reasoning scratchpad")


# ---------------------------------------------------------------------------
# (6) Sealed-path plan.json edit-denial.
# ---------------------------------------------------------------------------
def test_plan_json_path_is_sealed() -> None:
    """A `<slug>_plan.json` substrate path matches the sealed-path inventory.

    The sealed-paths matcher is the shared substrate the PreToolUse Edit/Write
    hook uses to refuse writes; a positive match here is the denial.
    """
    from bin._hooks import sealed_paths

    patterns = sealed_paths.load_sealed_paths(SEALED_PATHS_TXT)
    matched, pattern = sealed_paths.is_sealed(
        "docs/plans/example_plan/example_plan_plan.json", patterns
    )
    assert matched, "a *_plan.json substrate path must be sealed (edit-denied)"
    assert pattern is not None

    # The rendered plan.md twin is sealed too.
    matched_md, _ = sealed_paths.is_sealed(
        "docs/plans/example_plan/example_plan_plan.md", patterns
    )
    assert matched_md, "the rendered plan.md twin must also be sealed"

    # A neutral source file is NOT sealed (the matcher is not over-broad).
    not_sealed, _ = sealed_paths.is_sealed("bin/_planner/two_call.py", patterns)
    assert not not_sealed, "a normal source path must not be falsely sealed"


# ---------------------------------------------------------------------------
# (7) Marker schema round-trip.
# ---------------------------------------------------------------------------
def _valid_marker_row() -> dict:
    return {
        "id": "CTM.1",
        "title": "smoke round-trip marker",
        "added_date": "2026-06-03",
        "target": "date",
        "source_plan": "example_plan",
        "module": "tests",
        "data_needed": "n/a",
        "detail_file": "docs/plans/scheduled_markers/ctm_1.md",
        "context": "smoke fixture for the marker schema round-trip",
        "status": "active",
        "emitted_by": "bin/marker",
    }


def test_marker_row_roundtrips_through_schema() -> None:
    """A representative marker row validates against marker_v1, and a corrupted
    row is rejected — the marker schema round-trip.
    """
    from bin._marker import schema as marker_schema

    row = _valid_marker_row()
    # Valid row: must not raise.
    marker_schema.validate_row(row)

    # Corrupt the enum field -> must raise SchemaError.
    bad = dict(row)
    bad["status"] = "not_a_status"
    with pytest.raises(marker_schema.SchemaError):
        marker_schema.validate_row(bad)

    # Drop a required field -> must raise SchemaError.
    missing = dict(row)
    del missing["id"]
    with pytest.raises(marker_schema.SchemaError):
        marker_schema.validate_row(missing)


# ---------------------------------------------------------------------------
# (10) Git-provenance assertions.
# ---------------------------------------------------------------------------
def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_no_new_personal_identity_in_history() -> None:
    """No commit outside the grandfathered set carries a personal identity in
    its author, committer, or message — across ALL refs, on every checkout.

    A ratchet, not a statement of purity: ungating the old blob-level check
    found two personal-identity commits already on public main (see
    KNOWN_IDENTITY_LEAKS). Any NEW leak fails here, everywhere, immediately.
    """
    rows = _git(
        "log", "--all", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x01"
    )
    leaked: list[str] = []
    for row in rows.split("\x01"):
        row = row.strip("\n")
        if not row:
            continue
        sha, _, rest = row.partition("\x00")
        if any(tok in rest for tok in FORBIDDEN_IDENTITY):
            if sha not in KNOWN_IDENTITY_LEAKS:
                leaked.append(sha[:12])
    assert not leaked, (
        f"NEW personal-identity leak(s) in git history: {leaked}. "
        "Re-author before pushing (the grandfathered set is closed — do not "
        "extend it)."
    )


# ---------------------------------------------------------------------------
# (11) CLI-version doc.
# ---------------------------------------------------------------------------
def test_cli_version_doc_exists_and_documents_minimum() -> None:
    """The CLI-version doc exists and documents a concrete minimum version
    plus the validate/load commands the smoke battery depends on.
    """
    assert CLI_VERSION_DOC.exists(), f"missing CLI-version doc: {CLI_VERSION_DOC}"
    text = CLI_VERSION_DOC.read_text(encoding="utf-8")
    assert "claude plugin validate" in text
    assert "--plugin-dir" in text
    # A concrete version pin (e.g. 2.1.160) must be present, not just prose.
    import re

    assert re.search(r"\b\d+\.\d+\.\d+\b", text), (
        "CLI-version doc must document a concrete minimum version (X.Y.Z)"
    )
