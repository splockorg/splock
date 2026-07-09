"""Span dataclass + closed-enum validation (per §J.impl.3).

Per OpenInference shape, with the 7-value `span_kind` closed enum
defined in plan §J.3.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


SPAN_KINDS: frozenset[str] = frozenset(
    {"chain", "agent", "tool", "llm", "evaluator", "guardrail", "hook"}
)

SPAN_STATUSES: frozenset[str] = frozenset({"ok", "error", "unset"})

SPAN_ROOT_PARENT = "span_root"


class InvalidSpanError(ValueError):
    pass


@dataclass
class Span:
    trace_id: str
    parent_span_id: str
    span_kind: str
    name: str
    start_ts: str
    status: str
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    end_ts: str | None = None
    schema_version: int = 1
    # Populated post-derivation via stable hash.
    span_id: str = ""

    def __post_init__(self) -> None:
        if self.span_kind not in SPAN_KINDS:
            raise InvalidSpanError(
                f"span_kind={self.span_kind!r} not in {sorted(SPAN_KINDS)}"
            )
        if self.status not in SPAN_STATUSES:
            raise InvalidSpanError(
                f"status={self.status!r} not in {sorted(SPAN_STATUSES)}"
            )
        if not self.span_id:
            self.span_id = derive_span_id(
                self.trace_id, self.start_ts, self.span_kind, self.name
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "span_kind": self.span_kind,
            "name": self.name,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "status": self.status,
            "attributes": dict(self.attributes),
            "events": list(self.events),
        }


def derive_span_id(trace_id: str, start_ts: str, span_kind: str, name: str) -> str:
    """Deterministic stable hash → `span_<16hex>`.

    Stability is load-bearing for byte-identical render output across runs.
    """
    seed = f"{trace_id}|{start_ts}|{span_kind}|{name}".encode("utf-8")
    return "span_" + hashlib.blake2b(seed, digest_size=8).hexdigest()


__all__ = [
    "SPAN_KINDS",
    "SPAN_STATUSES",
    "SPAN_ROOT_PARENT",
    "InvalidSpanError",
    "Span",
    "derive_span_id",
]
