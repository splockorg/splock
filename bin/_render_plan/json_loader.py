"""JSON read + schema validation dispatch.

Per implplan §B.impl.4 steps 2-4 (lines 1110-1113). The caller (`main.py`)
maps the raised exceptions to closed-enum exit codes:

- `PlanNotFoundError` → exit 2 (`EXIT_PLAN_NOT_FOUND`)
- `JsonMalformedError` → exit 3 (`EXIT_JSON_MALFORMED`)
- `SchemaRejectedError` → exit 4 (`EXIT_SCHEMA_REJECTED`)
- `UnsupportedSchemaVersion` (re-raised from schema_registry) → exit 5

Schema-version forward-compat refusal happens BEFORE content validation
so the operator sees the version-bump signal clearly rather than a
misleading schema-violation dump (per implplan §B.impl.6 lines 1253-1257).

Supported `kind` values (mirrors `bin/_render_plan/schema_registry.SchemaKind`):

- `"plan"` — `<slug>_plan.json` substrate (LLM-authored via Structured Outputs).
- `"orchestrator"` — `<slug>_orchestrator.json` substrate.
- `"state"` — `_state.json` per v2.7 §E.2 (canonical task-state ledger;
  orch_status_render T1). Added with the `state` filename-stem override
  in schema_registry — no body change needed here because validation
  dispatch is kind-driven via `resolve_schema`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as _ValidationError

from .schema_registry import (
    SchemaKind,
    UnsupportedSchemaVersion,
    resolve_schema,
)


class PlanNotFoundError(FileNotFoundError):
    """Plan JSON file not present at the expected path."""


@dataclass
class JsonMalformedError(Exception):
    """JSON parse error; carries line/column for the structured stderr."""

    path: str
    line: int
    col: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.col}: {self.message}"

    def as_stderr_payload(self) -> dict:
        return {
            "error": "json_malformed",
            "path": self.path,
            "line": self.line,
            "col": self.col,
            "message": self.message,
        }


@dataclass
class SchemaRejectedError(Exception):
    """JSON Schema validation produced one or more violations."""

    path: str
    violations: list[dict] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.path}: {len(self.violations)} schema violation(s)"

    def as_stderr_payload(self) -> dict:
        return {
            "error": "schema_rejected",
            "path": self.path,
            "violations": self.violations,
        }


def load_plan_json(path: Path) -> dict:
    """Read + parse + return the JSON dict.

    Does NOT validate against schema; only ensures parseability. Caller
    runs `validate_against_schema` next.
    """
    path = Path(path)
    if not path.exists():
        raise PlanNotFoundError(str(path))
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanNotFoundError(str(path)) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise JsonMalformedError(
            path=str(path),
            line=exc.lineno,
            col=exc.colno,
            message=exc.msg,
        ) from exc


def validate_against_schema(
    payload: dict,
    kind: SchemaKind,
    *,
    source_path: str = "<inline>",
) -> dict:
    """Run schema-version dispatch + Draft 2020-12 validation.

    Returns the schema dict on success (for callers that want to reuse
    it). Raises on failure; the caller maps the exception type to an
    exit code.
    """
    # Step 1: schema_version field exists and is an int. Missing/wrong-type
    # is a schema rejection (per implplan §B.impl.6 line 1232) not a
    # forward-compat refusal.
    version = payload.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise SchemaRejectedError(
            path=source_path,
            violations=[
                {
                    "path": "/schema_version",
                    "message": (
                        "missing or non-integer `schema_version` — "
                        "required by every splock substrate"
                    ),
                }
            ],
        )

    # Step 2: forward-compat refusal (re-raises UnsupportedSchemaVersion).
    schema = resolve_schema(kind, version)

    # Step 3: content validation.
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    if errors:
        violations = [_format_validation_error(e) for e in errors]
        raise SchemaRejectedError(path=source_path, violations=violations)

    return schema


def _format_validation_error(err: _ValidationError) -> dict:
    """Reduce a jsonschema ValidationError to a stable JSON-serializable shape."""
    return {
        "path": "/" + "/".join(str(p) for p in err.absolute_path),
        "message": err.message,
        "validator": err.validator,
    }


__all__ = [
    "PlanNotFoundError",
    "JsonMalformedError",
    "SchemaRejectedError",
    "UnsupportedSchemaVersion",
    "load_plan_json",
    "validate_against_schema",
]
