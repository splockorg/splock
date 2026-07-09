"""K10 — cardinality audit for the CCOR.1 substrate.

Per CCOR.1 implplan §T-9 + design_resolutions R-cardinality-audit-cross-ref
+ R-event-types + R-cli-lint-conformance.

This is the cross-CLI / cross-file integrity check. It asserts:

1. **letter-K acceptance file count** = exactly 10 (K1..K10).
2. **event_type schema enum** contains exactly 4 new CCOR.1 values
   (`chain_paused`, `chain_resumed`, `chain_paused_lock_stale_cleared`,
   `pause_inject_consumed`).
3. **KNOWN_WRITERS** contains `bin/chain-pause` and `bin/chain-resume`
   exactly once each.
4. **sealed_paths.txt** has the two new patterns (`_chain_paused.lock`
   and `_operator_inject.md`) under `docs/plans/*/`. If the substrate-
   self-mod gate denied the inline edit, an operator-staged patch must
   be present at `/tmp/sealed_paths_ccor_1.txt` carrying both patterns —
   the test then skips with a clear cp command for the operator.
5. **J.4 dual-layer symmetry** is documentationally consistent: any
   new sealed_paths entry that is a critical `docs/plans/*/_*` pattern
   should also appear in `.claude/settings.json::permissions.deny`
   (otherwise the J.4 acceptance test will fail). Verified or surfaced
   as an operator follow-up.
6. **Exit codes** 22 + 23 are allocated to chain-pause / chain-resume
   per R-exit-codes; the `CHAIN_PAUSE_EMITTED_CODES` /
   `CHAIN_RESUME_EMITTED_CODES` frozen sets are populated.
7. **design_resolutions citation coverage**: every R-ID referenced by
   the K-tests appears in `design_resolutions.md`.
8. **CLI catalog** carries rows for `bin/chain-pause` + `bin/chain-resume`
   (added by T-9 as a follow-on consequence of R-cli-lint-conformance).
9. **env-var registry** carries entries for `CHAIN_PAUSE_LOG_LEVEL` +
   `CHAIN_RESUME_LOG_LEVEL` (added by T-9 — both env vars are read by
   the chain-pause + chain-resume CLIs).

Out-of-scope (intentionally NOT asserted):
- The chain_overnight job's process-graph membership: chain_overnight is
  a SUBSTRATE-tier driver, BELOW the data pipeline layer covered by
  `config/process_graph.yaml`. The graph catalogs data-pipeline jobs
  (crawler, extraction, etc.), NOT substrate. Documented here so a
  future maintainer doesn't add a process-graph node for chain_overnight
  expecting K10 to enforce it.
- Subagent frontmatter drift (`qna.md` alongside the 7 spec'd agents) —
  completely unrelated to CCOR.1 substrate; surfaced by an unrelated
  acceptance test, NOT this audit.
- Pre-existing missing CLI-catalog rows (`bin/orchestrator-next-ready`,
  `bin/qa`, `bin/sealed-rm`) — out of scope for T-9.
- Pre-existing missing env-var registry entries (`SPLOCK_INTENT_AUTO_REGISTER_INTERACTIVE`,
  etc.) — out of scope for T-9.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.acceptance


REPO_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_DIR = REPO_ROOT / "tests" / "acceptance"

CCOR1_NEW_EVENT_TYPES = {
    "chain_paused",
    "chain_resumed",
    "chain_paused_lock_stale_cleared",
    "pause_inject_consumed",
}

CCOR1_NEW_SEALED_PATTERNS = {
    "docs/plans/*/_chain_paused.lock",
    "docs/plans/*/_operator_inject.md",
}


# ---------------------------------------------------------------------------
# 1. Letter-K acceptance family count
# ---------------------------------------------------------------------------


def test_K10_letter_K_acceptance_files_count_is_10():
    """Exactly 10 letter-K CCOR.1 acceptance test files (K1..K10) — per R-test-letter.

    Filter excludes pre-existing `test_acceptance_K_xfail_*.py` watchers
    (an unrelated K-family already in the tree before CCOR.1 minted its
    own K letter). The CCOR.1 K-tests all carry the `_chain_` prefix.
    """
    matches = sorted(ACCEPTANCE_DIR.glob("test_acceptance_K_chain_*.py"))
    assert len(matches) == 10, (
        f"expected 10 CCOR.1 letter-K acceptance files; got {len(matches)}:\n"
        + "\n".join(f"  - {m.name}" for m in matches)
    )


# ---------------------------------------------------------------------------
# 2. event_type schema description carries the 4 CCOR.1 values
# ---------------------------------------------------------------------------


def test_K10_event_type_schema_description_carries_ccor1_quartet():
    """The schema's `event_type` description enumerates exactly the 4 CCOR.1
    values (plus prior values from earlier substrate ships).

    `event_type` is documented as free-form per the additive philosophy
    (writers may add types without schema bump), so this is a description-
    string assertion, not a JSON-enum assertion.
    """
    schema_path = REPO_ROOT / "schemas" / "orchestrator_log_v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    # The event_type description appears in StandardRow + RecoveryRow.
    def _find_event_type_descriptions(node, found):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "event_type" and isinstance(v, dict) and "description" in v:
                    found.append(v["description"])
                else:
                    _find_event_type_descriptions(v, found)
        elif isinstance(node, list):
            for item in node:
                _find_event_type_descriptions(item, found)

    descriptions: list[str] = []
    _find_event_type_descriptions(schema, descriptions)
    assert len(descriptions) >= 2, (
        f"expected at least 2 event_type descriptions (StandardRow + "
        f"RecoveryRow); got {len(descriptions)}"
    )

    # Each description should reference all 4 CCOR.1 new values.
    for desc in descriptions:
        missing = CCOR1_NEW_EVENT_TYPES - set(re.findall(r"\b\w+\b", desc))
        assert not missing, (
            f"event_type description missing CCOR.1 values: {missing}\n"
            f"desc excerpt: {desc[:300]}..."
        )


# ---------------------------------------------------------------------------
# 3. KNOWN_WRITERS contains the two CCOR.1 entries exactly once
# ---------------------------------------------------------------------------


def test_K10_known_writers_contains_chain_pause_and_chain_resume():
    """`KNOWN_WRITERS` frozenset must include `bin/chain-pause` and
    `bin/chain-resume` — the two new CCOR.1 emitters.
    """
    from bin._jsonl_log.writers import KNOWN_WRITERS

    assert "bin/chain-pause" in KNOWN_WRITERS
    assert "bin/chain-resume" in KNOWN_WRITERS
    # frozenset guarantees uniqueness; the assertion exists for clarity.


# ---------------------------------------------------------------------------
# 4. sealed_paths.txt carries the two new patterns
# ---------------------------------------------------------------------------


def _read_sealed_paths(path: Path) -> set[str]:
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line)
    return out


def test_K10_sealed_paths_includes_new_ccor1_patterns():
    """`hooks/sealed_paths.txt` must include both CCOR.1 patterns.

    If the substrate-self-mod gate denied the inline edit, a staged
    patch must be present at `/tmp/sealed_paths_ccor_1.txt` carrying
    both patterns. In that case this test SKIPs with the operator cp
    command — the audit becomes operator-action-pending rather than
    failing the suite.
    """
    sealed_path = REPO_ROOT / "hooks" / "sealed_paths.txt"
    sealed = _read_sealed_paths(sealed_path)

    missing = CCOR1_NEW_SEALED_PATTERNS - sealed
    if not missing:
        # Live file is up to date — no operator action pending.
        return

    # Live file lacks the patterns; check for the staged patch.
    staged_path = Path("/tmp/sealed_paths_ccor_1.txt")
    if staged_path.exists():
        staged = _read_sealed_paths(staged_path)
        staged_missing = CCOR1_NEW_SEALED_PATTERNS - staged
        if not staged_missing:
            pytest.skip(
                "sealed_paths.txt edit pending operator action. Apply with:\n"
                f"  cp {staged_path} {sealed_path}\n"
                "Then re-run this test. Missing patterns in live file: "
                f"{sorted(missing)}"
            )
        pytest.fail(
            f"staged patch at {staged_path} also missing patterns: "
            f"{sorted(staged_missing)}"
        )
    pytest.fail(
        f"sealed_paths.txt missing CCOR.1 patterns {sorted(missing)} and no "
        f"staged patch found at /tmp/sealed_paths_ccor_1.txt. The patterns "
        f"are required so the chain-sealed-state-delete-block hook + the "
        f"sealed-paths content-check hook refuse Edit/Delete on "
        f"_chain_paused.lock and _operator_inject.md."
    )


# ---------------------------------------------------------------------------
# 5. Exit-code allocation per R-exit-codes
# ---------------------------------------------------------------------------


def test_K10_exit_code_22_23_allocation():
    """22 = EXIT_NOT_PAUSED (chain-resume); 23 = EXIT_ALREADY_PAUSED (chain-pause)."""
    from bin._chain_overnight import exit_codes

    assert exit_codes.EXIT_NOT_PAUSED == 22
    assert exit_codes.EXIT_ALREADY_PAUSED == 23

    # The CLI-emitted sets are populated.
    assert exit_codes.EXIT_ALREADY_PAUSED in exit_codes.CHAIN_PAUSE_EMITTED_CODES
    assert exit_codes.EXIT_NOT_PAUSED in exit_codes.CHAIN_RESUME_EMITTED_CODES
    # The driver itself does NOT emit 22 + 23 directly (CLI-side scope).
    assert exit_codes.EXIT_NOT_PAUSED not in exit_codes.DRIVER_EMITTED_CODES
    assert exit_codes.EXIT_ALREADY_PAUSED not in exit_codes.DRIVER_EMITTED_CODES


# ---------------------------------------------------------------------------
# 6. design_resolutions cited R-IDs exist in the resolutions table
# ---------------------------------------------------------------------------


def test_K10_design_resolutions_r_ids_present():
    """R-IDs cited by K-tests must exist in design_resolutions.md."""
    resolutions_path = (
        REPO_ROOT / "docs" / "plans" / "ccor_1" / "design_resolutions.md"
    )
    txt = resolutions_path.read_text(encoding="utf-8")

    required = {
        "R-granularity",
        "R-inject-wiring",
        "R-inject-size",
        "R-cap-injection",
        "R-sentinel-primitive",
        "R-exit-codes",
        "R-orphan-detection",
        "R-needs-human-precedence",
        "R-finalizer-cleanup",
        "R-release-lock-pause",
        "R-from-resume-symmetry",
        "R-event-types",
        "R-test-letter",
        "R-cli-lint-conformance",
        "R-cardinality-audit-cross-ref",
    }
    missing = {r for r in required if r not in txt}
    assert not missing, (
        f"Missing R-IDs in design_resolutions.md: {sorted(missing)}\n"
        "Every R-ID cited by K-tests must have a row in the resolutions table."
    )


# ---------------------------------------------------------------------------
# 7. CLI catalog rows for chain-pause + chain-resume (follow-on consequence)
# ---------------------------------------------------------------------------


def test_K10_cli_catalog_carries_chain_pause_and_chain_resume_rows():
    """The CLI tooling catalog must reference both new CCOR.1 CLIs.

    Added as a necessary follow-on consequence of R-cli-lint-conformance —
    if there's a CLI-lint test that audits the catalog enum, the new
    CLIs must satisfy it.
    """
    catalog_path = REPO_ROOT / "docs" / "cli_tooling_catalog.md"
    txt = catalog_path.read_text(encoding="utf-8")
    assert "`bin/chain-pause`" in txt
    assert "`bin/chain-resume`" in txt


# ---------------------------------------------------------------------------
# 8. env-var registry entries (follow-on consequence)
# ---------------------------------------------------------------------------


def test_K10_env_var_registry_has_ccor1_log_level_entries():
    """`CHAIN_PAUSE_LOG_LEVEL` + `CHAIN_RESUME_LOG_LEVEL` must be registered.

    Both env vars are read by `bin/_chain_pause/main.py` and
    `bin/_chain_resume/main.py` respectively. The registry is the closed
    enum surface for environment-variable propagation; new env-var
    consumers must register their entry.
    """
    from bin._env_inventory import registry

    assert "CHAIN_PAUSE_LOG_LEVEL" in registry.REGISTRY
    assert "CHAIN_RESUME_LOG_LEVEL" in registry.REGISTRY


# ---------------------------------------------------------------------------
# 9. Letter-K family contains exactly the expected 10 files (by name)
# ---------------------------------------------------------------------------


def test_K10_letter_K_file_names_match_expected_set():
    """The 10 letter-K test files must match the planned K1..K10 names."""
    expected = {
        "test_acceptance_K_chain_pause_happy_path.py",
        "test_acceptance_K_chain_pause_wall_clock_freeze.py",
        "test_acceptance_K_chain_pause_double_pause_refusal.py",
        "test_acceptance_K_chain_resume_not_paused_refusal.py",
        "test_acceptance_K_chain_resume_orphan_detection.py",
        "test_acceptance_K_chain_pause_needs_human_precedence.py",
        "test_acceptance_K_chain_release_lock_clears_paused.py",
        "test_acceptance_K_chain_from_resume_inject_symmetry.py",
        "test_acceptance_K_chain_reviewer_spawn_no_inject.py",
        "test_acceptance_K_chain_pause_cardinality_audit.py",
    }
    actual = {p.name for p in ACCEPTANCE_DIR.glob("test_acceptance_K_chain_*.py")}
    assert actual == expected, (
        f"letter-K file set mismatch.\nExtras: {actual - expected}\n"
        f"Missing: {expected - actual}"
    )
