"""Required-field validation for `bin/lessons add` (per implplan §M.impl.5).

Two-stage validation:

1. `validate_required_fields(args_dict)` — checks all CLI args are
   non-empty strings BEFORE rendering the markdown. Returns a list of
   missing field names; empty list = pass. Raises `MissingRequiredFieldError`
   in `validate_or_raise(...)` if any missing — caller exits with
   `EXIT_LESSONS_REQUIRED_FIELD_MISSING` (36).

2. `validate_schema(entry)` — JSON-schema validate the rendered entry
   against `schemas/lessons_v1.schema.json`. Raises
   `SchemaValidationError` — caller exits with `EXIT_SCHEMA_REJECTED` (4).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .parser import LessonEntry


from bin._env_paths import schemas_dir as _env_paths_schemas_dir

# `schemas/` is a read-only PLUGIN asset — the one root that legitimately stays
# anchored to the plugin, not the adopter (see `_env_paths.schemas_dir`).
_SCHEMA_PATH = _env_paths_schemas_dir() / "lessons_v1.schema.json"


class MissingRequiredFieldError(ValueError):
    """One or more required `bin/lessons add` arguments missing or empty."""

    def __init__(self, missing: list[str]):
        super().__init__(
            f"missing or empty required field(s): {missing}. "
            f"All of --task --title --approach --failure --rejection --reattempt "
            f"--source must be supplied with non-empty values."
        )
        self.missing = missing


class SchemaValidationError(ValueError):
    """Rendered entry fails JSON-schema validation."""

    def __init__(self, message: str, *, path: str | None = None):
        super().__init__(message)
        self.path = path


# CLI arg → JSON field mapping for required-field check.
REQUIRED_ARG_TO_FIELD: tuple[tuple[str, str], ...] = (
    ("task", "task"),
    ("title", "title"),
    ("approach", "approach"),
    ("failure", "failure_mode"),
    ("rejection", "rejection"),
    ("reattempt", "reattempt"),
    ("source", "source"),
)


def validate_required_fields(args: dict[str, Any]) -> list[str]:
    """Return list of missing/empty CLI-arg names. Empty = pass."""
    missing: list[str] = []
    for arg_name, _json_field in REQUIRED_ARG_TO_FIELD:
        val = args.get(arg_name)
        if val is None or (isinstance(val, str) and not val.strip()):
            missing.append(arg_name)
    return missing


def validate_or_raise(args: dict[str, Any]) -> None:
    """Raise `MissingRequiredFieldError` if any required arg is missing."""
    missing = validate_required_fields(args)
    if missing:
        raise MissingRequiredFieldError(missing)


def _load_schema() -> dict[str, Any]:
    """Load the JSON-schema, with a fail-loud error if missing."""
    if not _SCHEMA_PATH.exists():
        raise SchemaValidationError(
            f"schema not found at {_SCHEMA_PATH}; lessons substrate broken"
        )
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_schema(entry: LessonEntry) -> None:
    """Validate a `LessonEntry` against `lessons_v1.schema.json`.

    Raises `SchemaValidationError` on failure.
    """
    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover — env-broken case
        raise SchemaValidationError(
            f"jsonschema not installed in venv: {exc}"
        ) from exc

    schema = _load_schema()
    try:
        jsonschema.validate(
            instance=entry.to_dict(),
            schema=schema,
            cls=jsonschema.Draft202012Validator,
        )
    except jsonschema.ValidationError as exc:
        raise SchemaValidationError(
            f"schema validation failed: {exc.message}",
            path=".".join(str(p) for p in exc.absolute_path) or None,
        ) from exc


__all__ = [
    "MissingRequiredFieldError",
    "SchemaValidationError",
    "REQUIRED_ARG_TO_FIELD",
    "validate_required_fields",
    "validate_or_raise",
    "validate_schema",
]
