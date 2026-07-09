"""F.5 — No `bin/eval-*` invocation produces a `scorer_id` like `operator_audit_*`.

Per orchestrator §4a.2: the operator IS the termination layer. No
meta-scorer above the operator's ground-truth labels. Sonnet drop-D1
noted the direct case is pinned in `test_calibration_thresholds.py`;
F.5 covers the inferred case (no scorer_id naming pattern that would
imply meta-scoring of the operator).
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


# Patterns that would indicate meta-scoring above the operator.
META_SCORER_PATTERNS = [
    "operator_audit",  # would imply auditing the operator's labels
    "label_audit",      # similar — auditing of labels
    "meta_score",
    "operator_meta",
    "audit_audit",  # double-meta
]


def test_no_meta_scorer_id_in_scorer_registry(repo_root):
    """F.5a: bin/_eval_common/scorer_registry.py has no meta-scorer ids."""
    scorer_registry_path = repo_root / "bin" / "_eval_common" / "scorer_registry.py"
    if not scorer_registry_path.exists():
        pytest.skip("scorer_registry.py missing")
    text = scorer_registry_path.read_text(encoding="utf-8")

    found = [p for p in META_SCORER_PATTERNS if p in text]
    assert not found, (
        f"scorer_registry.py contains meta-scorer pattern(s): {found}\n"
        "Per orchestrator §4a.2: operator-as-terminator — no scorers above "
        "operator labels."
    )


def test_no_meta_scorer_id_in_eval_emit_callsites(repo_root):
    """F.5b: bin/_eval_*/ source has no emit calls using meta-scorer ids."""
    bin_dir = repo_root / "bin"
    eval_dirs = ["_eval_baseline", "_eval_common", "_eval_gate",
                 "_eval_trend", "_regression_replay"]
    found_lines: list[tuple[str, str]] = []
    for eval_dir in eval_dirs:
        dir_path = bin_dir / eval_dir
        if not dir_path.is_dir():
            continue
        for py_file in dir_path.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for pattern in META_SCORER_PATTERNS:
                if pattern in text:
                    found_lines.append((str(py_file.relative_to(repo_root)), pattern))
    assert not found_lines, (
        f"Meta-scorer patterns appear in eval substrate:\n"
        + "\n".join(f"  {f}: {p!r}" for f, p in found_lines)
    )
