"""T-D (SC-D #4) — adopter-config roundtrip.

SC-D #4: the single adopter config surface is ``.splock.toml`` (a
documented template). It declares the portable knobs an adopter tunes:
``project.name``, optional ``project.venv_path``, ``intent.backend``
(sqlite default), optional MySQL ``intent.mysql.*``, the model-pin
defaults, and the ``templating.domain_example_placeholders`` knob.

This test is a structural roundtrip: the shipped template MUST parse as
TOML and MUST carry the documented keys with their documented default
values. It does NOT couple to a runtime TOML reader (the framework reads
env vars + the JSON overlay at runtime); the .splock.toml is the
operator-facing config template, so the roundtrip is parse + key-shape.

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:  # Python 3.11+
    import tomllib  # type: ignore
except ModuleNotFoundError:  # Python 3.10 fallback
    import tomli as tomllib  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[1]
SPLOCK_TOML = REPO_ROOT / ".splock.toml"


@pytest.fixture(scope="module")
def cfg() -> dict:
    assert SPLOCK_TOML.exists(), f"adopter config template missing: {SPLOCK_TOML}"
    return tomllib.loads(SPLOCK_TOML.read_text(encoding="utf-8"))


def test_splock_toml_parses_as_toml(cfg):
    assert isinstance(cfg, dict) and cfg, ".splock.toml parsed empty"


def test_project_section(cfg):
    project = cfg.get("project")
    assert isinstance(project, dict), "[project] section missing"
    assert project.get("name") == "splock", (
        f"project.name should default to 'splock', got {project.get('name')!r}"
    )
    # venv_path is OPTIONAL — commented out by default, so absent here.
    assert "venv_path" not in project or isinstance(project["venv_path"], str)


def test_intent_section_defaults(cfg):
    intent = cfg.get("intent")
    assert isinstance(intent, dict), "[intent] section missing"
    assert intent.get("backend") == "sqlite", (
        "intent.backend must default to 'sqlite' (zero-dependency backend)"
    )
    assert intent.get("collision_halt_action") == "halt", (
        "intent.collision_halt_action must default to 'halt'"
    )


def test_model_pin_defaults(cfg):
    models = cfg.get("models")
    assert isinstance(models, dict), "[models] section missing"
    assert models.get("planner_model") == "claude-opus-4-8"
    # reviewer/coder ship as aliases by default.
    assert models.get("reviewer_model") == "sonnet"
    assert models.get("coder_model") == "opus"
    # The verifier pin is intentionally NOT an adopter-tunable key.
    assert "verifier_model" not in models, (
        "verifier model must NOT be exposed as an adopter knob — it is a "
        "REQUIRED frontmatter pin"
    )


def test_domain_example_placeholders_present(cfg):
    """SC-D #4 — domain_example_placeholders is a real templating knob,
    preserved as a (possibly empty) table the adopter fills in."""
    templating = cfg.get("templating")
    assert isinstance(templating, dict), "[templating] section missing"
    placeholders = templating.get("domain_example_placeholders")
    assert isinstance(placeholders, dict), (
        "templating.domain_example_placeholders must be a table (knob preserved)"
    )


def test_roundtrip_is_stable(cfg):
    """A parse of the template is internally consistent (no surprise types)."""
    # backend is a member of the documented closed set.
    assert cfg["intent"]["backend"] in ("sqlite", "jsonl", "mysql")
    assert cfg["intent"]["collision_halt_action"] in ("halt", "warn", "log_only")
