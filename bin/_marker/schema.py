"""JSON Schema loader + per-row validation (implplan §K.impl.4).

`schemas/marker_v1.schema.json` is the sole source of truth for the 13-field
row contract. This module loads it once per process and exposes a
`validate_row(row)` callable that raises `SchemaError` with a per-field
diagnostic on any violation.

Implementation note: we use `jsonschema` if available. If not (lightweight
test env), we fall back to a hand-rolled checker that covers the same
field-presence + enum + pattern constraints. The fallback is intentionally
strict — same refusals fire — so test results agree across environments.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional


class SchemaError(ValueError):
    """Raised when a row fails schema validation. Message format:
    `<field>: <reason>` for single-field failures, or multiline for multi-field."""


_SCHEMA_CACHE: Optional[Dict[str, Any]] = None


def schema_path() -> Path:
    return _repo_root() / "schemas" / "marker_v1.schema.json"


def load_schema() -> Dict[str, Any]:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        _SCHEMA_CACHE = json.loads(schema_path().read_text(encoding="utf-8"))
    return _SCHEMA_CACHE


def validate_row(row: Dict[str, Any]) -> None:
    """Validate a single marker row against marker_v1.

    Two-phase: try `jsonschema` first; fall back to hand-rolled checks if
    not installed. Either way, raises `SchemaError` on the first failure.
    """
    schema = load_schema()
    try:
        import jsonschema  # type: ignore[import-untyped]
        try:
            jsonschema.validate(instance=row, schema=schema)
            return
        except jsonschema.ValidationError as e:
            field = ".".join(str(p) for p in e.absolute_path) or "<root>"
            raise SchemaError(f"{field}: {e.message}") from e
    except ImportError:
        pass

    # Fallback hand-rolled validator
    errors = list(_hand_validate(row, schema))
    if errors:
        raise SchemaError("\n".join(errors))


def _hand_validate(row: Dict[str, Any], schema: Dict[str, Any]):
    """Yield field-violation messages."""
    required = set(schema.get("required", []))
    props = schema.get("properties", {})

    # Required-field presence
    for field in required:
        if field not in row:
            yield f"{field}: required field missing"

    # Per-field checks
    for field, value in row.items():
        spec = props.get(field)
        if spec is None:
            # additionalProperties: false — unknown field is an error
            if not schema.get("additionalProperties", True):
                yield f"{field}: unknown field (additionalProperties=false)"
            continue
        types = spec.get("type")
        if types is not None:
            if not _type_ok(value, types):
                yield f"{field}: type mismatch (expected {types})"
                continue
        if value is None:
            continue
        if isinstance(value, str):
            pat = spec.get("pattern")
            if pat is not None and not re.match(pat, value):
                yield f"{field}: does not match pattern {pat}"
            min_len = spec.get("minLength")
            if min_len is not None and len(value) < min_len:
                yield f"{field}: length {len(value)} < minLength {min_len}"
            max_len = spec.get("maxLength")
            if max_len is not None and len(value) > max_len:
                yield f"{field}: length {len(value)} > maxLength {max_len}"
            enum = spec.get("enum")
            if enum is not None and value not in enum:
                yield f"{field}: value '{value}' not in enum {enum}"

    # status-conditional checks (allOf if/then)
    status = row.get("status")
    if status == "closed":
        for f in ("closed_date", "closure_resolution"):
            if f not in row or not row[f]:
                yield f"{f}: required when status=closed"
    elif status == "active":
        if "closed_date" in row:
            yield "closed_date: must not be present when status=active"


def _type_ok(value: Any, types) -> bool:
    if isinstance(types, str):
        types = [types]
    py_map = {
        "string": str,
        "integer": int,
        "boolean": bool,
        "number": (int, float),
        "array": list,
        "object": dict,
        "null": type(None),
    }
    for t in types:
        if t in py_map and isinstance(value, py_map[t]):
            return True
    return False


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
