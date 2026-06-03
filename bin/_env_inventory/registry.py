"""Master env-var registry (implplan §I.impl.3).

Single source-of-truth Python dict of every env var any chain-related code
path consults. v1.4-parallel-revised-2 ships 28 rows per §I.impl.3 table;
delete_usage_caps retired 5 budget-cap rows, leaving the current count.

Consumers MUST import the name constant (not write ad-hoc
`os.environ.get(...)`) and call `propagation.resolve(name)` for class-aware
read discipline. Bare `os.environ.get("OVERNIGHT_WALL_CLOCK_SECONDS")` calls
outside this module are flagged by `test_registry_completeness.py`.

Schema validation runs at import time against
`schemas/env_inventory_v1.schema.json` per §I.impl.2 + §B.impl.6
forward-compat (unknown `schema_version` refused with exit code 5).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from bin._env_inventory.propagation import (
    MODEL_PIN_RE,
    PropagationClass,
)


_SUPPORTED_SCHEMA_VERSIONS = (1,)


@dataclass(frozen=True)
class EnvVarSpec:
    """Typed entry for one env var in the master registry.

    Per §I.impl.3 Registry shape (Python). `type` is the logical type
    label ('string' / 'int' / 'float' / 'numeric'); `min` / `max` apply
    only to numeric types. `regex` (e.g., MODEL_PIN_RE.pattern) applies
    only to string types. `valid_values` is an optional closed enum.
    """

    type: str
    default: Any
    propagation_class: PropagationClass
    owner_section: str
    consumed_by: tuple[str, ...]
    plan_citation: str
    min: Optional[float] = None
    max: Optional[float] = None
    regex: Optional[str] = None
    valid_values: Optional[tuple[str, ...]] = None


# --- Name constants -------------------------------------------------------
# Per §I.impl.3: consumers import the name constant, not the bare string.
# Sorted alphabetically; one name constant per REGISTRY key.

CHAIN_PAUSE_LOG_LEVEL = "CHAIN_PAUSE_LOG_LEVEL"
CHAIN_RESUME_LOG_LEVEL = "CHAIN_RESUME_LOG_LEVEL"
CHAIN_STATUS_LOG_LEVEL = "CHAIN_STATUS_LOG_LEVEL"
CRAWLER_FORCE_PROXY_TIER = "CRAWLER_FORCE_PROXY_TIER"
EVAL_FAILURE_RETENTION_DAYS = "EVAL_FAILURE_RETENTION_DAYS"
EVAL_GATE_OVERRIDE = "EVAL_GATE_OVERRIDE"
EVAL_GATE_OVERRIDE_REASON = "EVAL_GATE_OVERRIDE_REASON"
EVAL_GATE_STRICT_THRESHOLD = "EVAL_GATE_STRICT_THRESHOLD"
ESCALATION_BLAST_RADIUS_FILES = "ESCALATION_BLAST_RADIUS_FILES"
GUARDRAIL_MODE = "GUARDRAIL_MODE"
INVESTIGATOR_GATE_PROMPT_ENABLED = "INVESTIGATOR_GATE_PROMPT_ENABLED"
INVESTIGATOR_INTAKE_CONTEXT = "INVESTIGATOR_INTAKE_CONTEXT"
LAZY_DUMP_CAP = "LAZY_DUMP_CAP"
OPERATOR_OVERRIDE = "OPERATOR_OVERRIDE"
OPERATOR_OVERRIDE_STATE = "OPERATOR_OVERRIDE_STATE"
OVERNIGHT_CHAIN_PLANNER_MODEL = "OVERNIGHT_CHAIN_PLANNER_MODEL"
OVERNIGHT_DEBUG_RETRY_PROMPT = "OVERNIGHT_DEBUG_RETRY_PROMPT"
OVERNIGHT_MODE = "OVERNIGHT_MODE"
OVERNIGHT_ORPHAN_GRACE_SECONDS = "OVERNIGHT_ORPHAN_GRACE_SECONDS"
OVERNIGHT_SONNET_REVIEW_MODEL = "OVERNIGHT_SONNET_REVIEW_MODEL"
OVERNIGHT_TEST_DEFER_THRESHOLD = "OVERNIGHT_TEST_DEFER_THRESHOLD"
OVERNIGHT_TEST_MAX_RETRIES = "OVERNIGHT_TEST_MAX_RETRIES"
OVERNIGHT_VERIFIER_MODEL = "OVERNIGHT_VERIFIER_MODEL"
PACKAGE_SAFETY_AGE_THRESHOLD_DAYS = "PACKAGE_SAFETY_AGE_THRESHOLD_DAYS"
PACKAGE_SAFETY_DOWNLOAD_FLOOR = "PACKAGE_SAFETY_DOWNLOAD_FLOOR"
SPLOCK_CHAIN_ID = "SPLOCK_CHAIN_ID"
SPLOCK_INTENT_AREA = "SPLOCK_INTENT_AREA"
SPLOCK_INTENT_AUTO_REGISTER_INTERACTIVE = "SPLOCK_INTENT_AUTO_REGISTER_INTERACTIVE"
SPLOCK_INTENT_COLLISION_HALT_ACTION = "SPLOCK_INTENT_COLLISION_HALT_ACTION"
SPLOCK_INTENT_SESSION_ID = "SPLOCK_INTENT_SESSION_ID"
SPLOCK_INTENT_SUMMARY = "SPLOCK_INTENT_SUMMARY"
SPLOCK_PHASE = "SPLOCK_PHASE"
SPLOCK_PLAN_SLUG = "SPLOCK_PLAN_SLUG"


# --- Registry -------------------------------------------------------------

REGISTRY: dict[str, EnvVarSpec] = {
    CHAIN_PAUSE_LOG_LEVEL: EnvVarSpec(
        type="string",
        default="INFO",
        valid_values=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        propagation_class=PropagationClass.OPERATOR_SET_DEBUG_TOGGLE,
        owner_section="CCOR.1 T-5",
        consumed_by=("bin/_chain_pause/main.py logging.basicConfig",),
        plan_citation="CCOR.1 §T-5 + design_resolutions R-cli-lint-conformance",
    ),
    CHAIN_RESUME_LOG_LEVEL: EnvVarSpec(
        type="string",
        default="INFO",
        valid_values=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        propagation_class=PropagationClass.OPERATOR_SET_DEBUG_TOGGLE,
        owner_section="CCOR.1 T-6",
        consumed_by=("bin/_chain_resume/main.py logging.basicConfig",),
        plan_citation="CCOR.1 §T-6 + design_resolutions R-cli-lint-conformance",
    ),
    CHAIN_STATUS_LOG_LEVEL: EnvVarSpec(
        type="string",
        default="INFO",
        valid_values=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        propagation_class=PropagationClass.OPERATOR_SET_DEBUG_TOGGLE,
        owner_section="CCOR.1 T-8",
        consumed_by=("bin/_chain_status/main.py logging.basicConfig",),
        plan_citation="CCOR.1 §T-8 + design_resolutions R-cli-lint-conformance",
    ),
    CRAWLER_FORCE_PROXY_TIER: EnvVarSpec(
        type="string",
        default=None,
        propagation_class=PropagationClass.INHERITED_FROM_BROADER_SYSTEM,
        owner_section="broader system (CLAUDE.md)",
        consumed_by=("crawler runtime", "chain step agent (inherited)"),
        plan_citation="plan §I.4",
    ),
    EVAL_FAILURE_RETENTION_DAYS: EnvVarSpec(
        type="int",
        default=30,
        min=7,
        max=365,
        propagation_class=PropagationClass.OPERATOR_SET_RUNTIME_TUNABLE,
        owner_section="J.impl",
        consumed_by=("bin/_eval_common/failure_gc.py",),
        plan_citation="§J.impl.5 + §J.impl.15 #2 RATIFIED 2026-05-21",
    ),
    EVAL_GATE_OVERRIDE: EnvVarSpec(
        type="string",
        default=None,
        valid_values=("1",),
        propagation_class=PropagationClass.OPERATOR_SET_PER_ACTION,
        owner_section="J.impl",
        consumed_by=("eval-gate pre-commit hook",),
        plan_citation="plan §I.2c",
    ),
    EVAL_GATE_OVERRIDE_REASON: EnvVarSpec(
        type="string",
        default=None,
        propagation_class=PropagationClass.OPERATOR_SET_PER_ACTION,
        owner_section="J.impl",
        consumed_by=("eval-gate pre-commit hook (loud-log reason text)",),
        plan_citation="§J.impl.9 — paired with EVAL_GATE_OVERRIDE; loud-log discipline",
    ),
    EVAL_GATE_STRICT_THRESHOLD: EnvVarSpec(
        type="int",
        default=0,
        min=0,
        max=10,
        propagation_class=PropagationClass.OPERATOR_SET_RUNTIME_TUNABLE,
        owner_section="J.impl",
        consumed_by=("eval-gate fire",),
        plan_citation="plan §I.2c",
    ),
    ESCALATION_BLAST_RADIUS_FILES: EnvVarSpec(
        type="int",
        default=15,
        min=1,
        max=200,
        propagation_class=PropagationClass.OPERATOR_SET_RUNTIME_TUNABLE,
        owner_section="L.impl",
        consumed_by=(
            "bin/route_issue --check-scope",
            ".claude/hooks/escalation-trigger-precommit.sh",
        ),
        plan_citation="§L.impl.4 + plan §L.3 (NEW v1.3-revised)",
    ),
    GUARDRAIL_MODE: EnvVarSpec(
        type="string",
        default=None,
        valid_values=("1",),
        propagation_class=PropagationClass.OPERATOR_SET_MODE_FLAG,
        owner_section="A.impl + hooks",
        consumed_by=("every hook fire", "lazy-dump cap override", "manifest stamp"),
        plan_citation="plan §I.1 + §I.4a",
    ),
    INVESTIGATOR_GATE_PROMPT_ENABLED: EnvVarSpec(
        type="string",
        default="true",
        valid_values=("true", "false"),
        propagation_class=PropagationClass.INHERITED_FROM_BROADER_SYSTEM,
        owner_section="broader system (CLAUDE.md)",
        consumed_by=("investigator runtime",),
        plan_citation="plan §I.4",
    ),
    INVESTIGATOR_INTAKE_CONTEXT: EnvVarSpec(
        type="string",
        default="production",
        valid_values=("production", "agent_driven"),
        propagation_class=PropagationClass.INHERITED_FROM_BROADER_SYSTEM,
        owner_section="broader system (CLAUDE.md)",
        consumed_by=("investigator runtime", "manifest stamp per I.4b"),
        plan_citation="plan §I.4 + §I.4b",
    ),
    LAZY_DUMP_CAP: EnvVarSpec(
        type="int",
        default=6,
        min=1,
        max=100,
        propagation_class=PropagationClass.OPERATOR_SET_RUNTIME_TUNABLE,
        owner_section="repo-wide",
        consumed_by=("lazy-dump hook fire",),
        plan_citation="plan §I.2",
    ),
    OPERATOR_OVERRIDE: EnvVarSpec(
        type="string",
        default=None,
        valid_values=("1",),
        propagation_class=PropagationClass.OPERATOR_SET_PER_ACTION,
        owner_section="E.impl + hooks",
        consumed_by=("per-tool-call override",),
        plan_citation="plan §I.1",
    ),
    OPERATOR_OVERRIDE_STATE: EnvVarSpec(
        type="string",
        default=None,
        valid_values=("1",),
        propagation_class=PropagationClass.OPERATOR_SET_PER_ACTION,
        owner_section="E.impl (state-recovery override)",
        consumed_by=("state-recovery surface",),
        plan_citation="plan §I.1",
    ),
    OVERNIGHT_CHAIN_PLANNER_MODEL: EnvVarSpec(
        type="string",
        default="claude-opus-4-7-20260517",
        regex=MODEL_PIN_RE.pattern,
        propagation_class=PropagationClass.OPERATOR_SET_MODEL_PIN,
        owner_section="D.impl",
        consumed_by=(
            "bin/_planner/two_call.py at planner spawn",
            "stamped on manifest",
        ),
        plan_citation="plan §I.2a + §D.impl.8",
    ),
    OVERNIGHT_DEBUG_RETRY_PROMPT: EnvVarSpec(
        type="string",
        default=None,
        valid_values=("1",),
        propagation_class=PropagationClass.OPERATOR_SET_DEBUG_TOGGLE,
        owner_section="F.impl",
        consumed_by=("bin/_retry_loop/prompt_construct.py",),
        plan_citation="§F.impl.6 + §F.impl.12 #1 RATIFIED 2026-05-20",
    ),
    OVERNIGHT_MODE: EnvVarSpec(
        type="string",
        default=None,
        valid_values=("1",),
        propagation_class=PropagationClass.OPERATOR_SET_MODE_FLAG,
        owner_section="A.impl + hooks",
        consumed_by=(
            "every hook fire",
            "every CLI invocation",
            "manifest stamp",
        ),
        plan_citation="plan §I.1 + §I.4a",
    ),
    OVERNIGHT_ORPHAN_GRACE_SECONDS: EnvVarSpec(
        type="int",
        default=300,
        min=60,
        max=3600,
        propagation_class=PropagationClass.OPERATOR_SET_RUNTIME_TUNABLE,
        owner_section="A.impl",
        consumed_by=("pre-spawn orphan-state scan",),
        plan_citation="plan §I.2",
    ),
    OVERNIGHT_SONNET_REVIEW_MODEL: EnvVarSpec(
        type="string",
        default="claude-sonnet-4-6-20260101",
        regex=MODEL_PIN_RE.pattern,
        propagation_class=PropagationClass.OPERATOR_SET_MODEL_PIN,
        owner_section="D.impl + F.impl",
        consumed_by=(
            "reviewer subagent SDK invocation at Sonnet review boundaries",
        ),
        plan_citation="plan §I.2a + §D.impl.8",
    ),
    OVERNIGHT_TEST_DEFER_THRESHOLD: EnvVarSpec(
        type="float",
        default=0.25,
        min=0.0,
        max=1.0,
        propagation_class=PropagationClass.OPERATOR_SET_RUNTIME_TUNABLE,
        owner_section="A.impl",
        consumed_by=("code → test boundary evaluation in chain driver",),
        plan_citation="plan §I.2",
    ),
    OVERNIGHT_TEST_MAX_RETRIES: EnvVarSpec(
        type="int",
        default=3,
        min=1,
        max=5,
        propagation_class=PropagationClass.OPERATOR_SET_RUNTIME_TUNABLE,
        owner_section="F.impl",
        consumed_by=("retry-loop entry in chain driver",),
        plan_citation="plan §I.2 + §F.impl",
    ),
    OVERNIGHT_VERIFIER_MODEL: EnvVarSpec(
        type="string",
        default="claude-haiku-4-5-20251001",
        regex=MODEL_PIN_RE.pattern,
        propagation_class=PropagationClass.OPERATOR_SET_MODEL_PIN,
        owner_section="D.impl",
        consumed_by=("bin/ralph-check verifier subagent SDK invocation",),
        plan_citation="plan §I.2a + §D.impl.4",
    ),
    PACKAGE_SAFETY_AGE_THRESHOLD_DAYS: EnvVarSpec(
        type="int",
        default=14,
        min=7,
        max=60,
        propagation_class=PropagationClass.OPERATOR_SET_RUNTIME_TUNABLE,
        owner_section="G.impl",
        consumed_by=(".claude/hooks/package-safety.sh",),
        plan_citation="§G.impl.7 + plan §G.7.1",
    ),
    PACKAGE_SAFETY_DOWNLOAD_FLOOR: EnvVarSpec(
        type="int",
        default=500,
        min=0,
        max=None,
        propagation_class=PropagationClass.OPERATOR_SET_RUNTIME_TUNABLE,
        owner_section="G.impl",
        consumed_by=(".claude/hooks/package-safety.sh",),
        plan_citation="§G.impl.7 + plan §G.7.1",
    ),
    SPLOCK_CHAIN_ID: EnvVarSpec(
        type="string",
        default=None,
        regex=r"^chain_[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
        propagation_class=PropagationClass.DRIVER_SET_CHAIN_CONTEXT,
        owner_section="A.impl",
        consumed_by=("every chain-scoped hook in §G.impl + §F.impl",),
        plan_citation="plan §I.3",
    ),
    SPLOCK_INTENT_AREA: EnvVarSpec(
        type="string",
        default=None,
        propagation_class=PropagationClass.OPERATOR_SET_PER_ACTION,
        owner_section="P.impl (intent registry fast-path)",
        consumed_by=(
            "bin/_intent/settings.py::resolve_area",
            "bin/_intent/register.py (precedence over --area CLI flag)",
        ),
        plan_citation="§P.impl T3 intent_session_auto_register + plan §P",
    ),
    SPLOCK_INTENT_AUTO_REGISTER_INTERACTIVE: EnvVarSpec(
        type="string",
        default=None,
        valid_values=("1", "true", "yes", "on"),
        propagation_class=PropagationClass.OPERATOR_SET_DEBUG_TOGGLE,
        owner_section="P.impl (intent registry auto-register gate)",
        consumed_by=("bin/_intent/register.py::_interactive_auto_register_enabled",),
        plan_citation="§P.impl + plan §P",
    ),
    SPLOCK_INTENT_COLLISION_HALT_ACTION: EnvVarSpec(
        type="string",
        default=None,
        valid_values=("halt", "warn", "log_only"),
        propagation_class=PropagationClass.OPERATOR_SET_PER_ACTION,
        owner_section="P.impl (intent registry collision policy)",
        consumed_by=(
            "bin/_intent/register.py::_resolve_collision_halt_action",
            "SessionStart hook subprocess (forces log_only per recon §6.11(b))",
        ),
        plan_citation="§P.impl.10 + plan §P",
    ),
    SPLOCK_INTENT_SESSION_ID: EnvVarSpec(
        type="string",
        default=None,
        regex=r"^sess_[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z_[A-Za-z0-9]{4}$",
        propagation_class=PropagationClass.DRIVER_SET_CHAIN_CONTEXT,
        owner_section="A.impl (auto-register at chain spawn per §P.impl.10)",
        consumed_by=(
            "§P.impl.9 intent-on-first-edit.sh hook (path a)",
        ),
        plan_citation="§P.impl.9 + §A.impl.5b (v1.4-parallel-revised-2)",
    ),
    SPLOCK_INTENT_SUMMARY: EnvVarSpec(
        type="string",
        default=None,
        propagation_class=PropagationClass.OPERATOR_SET_PER_ACTION,
        owner_section="P.impl (intent registry fast-path)",
        consumed_by=(
            "bin/_intent/settings.py::resolve_summary",
            "bin/_intent/register.py (precedence over --summary CLI flag)",
        ),
        plan_citation="§P.impl T3 intent_session_auto_register + plan §P",
    ),
    SPLOCK_PHASE: EnvVarSpec(
        type="int",
        default=None,
        min=2,
        max=5,
        propagation_class=PropagationClass.DRIVER_SET_CHAIN_CONTEXT,
        owner_section="A.impl",
        consumed_by=("every chain-scoped hook in §G.impl + §F.impl",),
        plan_citation="plan §I.3",
    ),
    SPLOCK_PLAN_SLUG: EnvVarSpec(
        type="string",
        default=None,
        regex=r"^[a-z0-9][a-z0-9_-]*$",
        propagation_class=PropagationClass.DRIVER_SET_CHAIN_CONTEXT,
        owner_section="A.impl",
        consumed_by=(
            "every chain-scoped hook in §G.impl + §F.impl",
            "SessionStart hook → _chain_sessions.json",
        ),
        plan_citation="plan §I.3",
    ),
}


# --- Schema validation on import -----------------------------------------


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _schema_path() -> Path:
    return _repo_root() / "schemas" / "env_inventory_v1.schema.json"


def _registry_as_dict() -> dict[str, Any]:
    """Project REGISTRY → schema-shaped dict for validation."""
    entries: dict[str, Any] = {}
    for name, spec in REGISTRY.items():
        entry: dict[str, Any] = {
            "type": spec.type,
            "default": spec.default,
            "propagation_class": spec.propagation_class.value,
            "owner_section": spec.owner_section,
            "consumed_by": list(spec.consumed_by),
            "plan_citation": spec.plan_citation,
        }
        if spec.min is not None:
            entry["min"] = spec.min
        if spec.max is not None:
            entry["max"] = spec.max
        if spec.regex is not None:
            entry["regex"] = spec.regex
        if spec.valid_values is not None:
            entry["valid_values"] = list(spec.valid_values)
        entries[name] = entry
    return {"schema_version": 1, "entries": entries}


def _validate_against_schema() -> None:
    """Validate REGISTRY against schemas/env_inventory_v1.schema.json.

    Per §B.impl.6 forward-compat: unknown `schema_version` refused with
    exit code 5 + structured stderr. Standard JSON-Schema violations
    raise at import time so registry drift is loud.
    """
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    instance = _registry_as_dict()

    seen_version = instance.get("schema_version")
    if seen_version not in _SUPPORTED_SCHEMA_VERSIONS:
        sys.stderr.write(
            json.dumps(
                {
                    "error": "unsupported_schema_version",
                    "kind": "env_inventory",
                    "seen": seen_version,
                    "supported": list(_SUPPORTED_SCHEMA_VERSIONS),
                }
            )
            + "\n"
        )
        raise SystemExit(5)

    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError:
        # Fallback: minimal structural check.
        _fallback_validate(instance, schema)
        return
    jsonschema.validate(instance=instance, schema=schema)


def _fallback_validate(instance: dict[str, Any], schema: dict[str, Any]) -> None:
    """Hand-rolled structural check matching the schema's required fields.

    Mirrors `bin/_marker/schema.py::_hand_validate` posture: strict-fail,
    same refusals as `jsonschema` would emit for the field-presence /
    enum aspects we care about.
    """
    allowed_classes = {
        c.value for c in PropagationClass
    }
    for name, entry in instance["entries"].items():
        for field_name in (
            "type",
            "default",
            "propagation_class",
            "owner_section",
            "consumed_by",
            "plan_citation",
        ):
            if field_name not in entry:
                raise ValueError(
                    f"REGISTRY[{name!r}]: missing required field {field_name!r}"
                )
        if entry["type"] not in ("string", "int", "float", "numeric"):
            raise ValueError(
                f"REGISTRY[{name!r}]: unknown type {entry['type']!r}"
            )
        if entry["propagation_class"] not in allowed_classes:
            raise ValueError(
                f"REGISTRY[{name!r}]: propagation_class "
                f"{entry['propagation_class']!r} not in closed enum"
            )


# Run schema validation at import time.
_validate_against_schema()


__all__ = [
    "EnvVarSpec",
    "REGISTRY",
    # Name constants — one per registry entry
    "CHAIN_PAUSE_LOG_LEVEL",
    "CHAIN_RESUME_LOG_LEVEL",
    "CHAIN_STATUS_LOG_LEVEL",
    "CRAWLER_FORCE_PROXY_TIER",
    "EVAL_FAILURE_RETENTION_DAYS",
    "EVAL_GATE_OVERRIDE",
    "EVAL_GATE_OVERRIDE_REASON",
    "EVAL_GATE_STRICT_THRESHOLD",
    "ESCALATION_BLAST_RADIUS_FILES",
    "GUARDRAIL_MODE",
    "INVESTIGATOR_GATE_PROMPT_ENABLED",
    "INVESTIGATOR_INTAKE_CONTEXT",
    "LAZY_DUMP_CAP",
    "OPERATOR_OVERRIDE",
    "OPERATOR_OVERRIDE_STATE",
    "OVERNIGHT_CHAIN_PLANNER_MODEL",
    "OVERNIGHT_DEBUG_RETRY_PROMPT",
    "OVERNIGHT_MODE",
    "OVERNIGHT_ORPHAN_GRACE_SECONDS",
    "OVERNIGHT_SONNET_REVIEW_MODEL",
    "OVERNIGHT_TEST_DEFER_THRESHOLD",
    "OVERNIGHT_TEST_MAX_RETRIES",
    "OVERNIGHT_VERIFIER_MODEL",
    "PACKAGE_SAFETY_AGE_THRESHOLD_DAYS",
    "PACKAGE_SAFETY_DOWNLOAD_FLOOR",
    "SPLOCK_CHAIN_ID",
    "SPLOCK_INTENT_AREA",
    "SPLOCK_INTENT_AUTO_REGISTER_INTERACTIVE",
    "SPLOCK_INTENT_COLLISION_HALT_ACTION",
    "SPLOCK_INTENT_SESSION_ID",
    "SPLOCK_INTENT_SUMMARY",
    "SPLOCK_PHASE",
    "SPLOCK_PLAN_SLUG",
]
