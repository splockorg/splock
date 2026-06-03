"""T-D (SC-D #3) — seal-inventory translation (couples to SC-C #5).

SC-D #3: the seal inventory (``hooks/sealed_paths.txt``) is translated
for the portable plugin:

  * KEEP the framework seals (``*_orchestrator.json``, ``*_plan.json``,
    ``outstanding_issues.md``, the per-slug ``docs/plans/*`` state globs).
  * TRACK the relocated intent state under the plugin data-root
    (``${CLAUDE_PLUGIN_DATA}`` / ``.plugin-data``): the SQLite db, the
    JSONL mirror, and the settings overlay all co-locate there (SC-C #5
    relocated them off the host ``docs/intent/`` layout).
  * SHIP host secrets (``.env*``, ``~/.aws/**``, ``~/.ssh/**``) as
    generic-recommended entries.

T-C's ``test_seal_glob_lockstep.py`` asserts the data-root co-location
SHAPE and the file's existence, explicitly deferring the CONTENT rewrite
to T-D. This file asserts the rewritten CONTENT: that the relocated
state surface is actually sealed by ``is_sealed`` against the inventory.

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._hooks.sealed_paths import is_sealed, load_sealed_paths  # noqa: E402

SEAL_FILE = REPO_ROOT / "hooks" / "sealed_paths.txt"


def _patterns():
    return load_sealed_paths(SEAL_FILE)


def test_seal_inventory_exists():
    assert SEAL_FILE.exists(), f"seal inventory missing: {SEAL_FILE}"


def test_relocated_intent_state_is_sealed():
    """The relocated ``.plugin-data`` intent state surface is sealed."""
    pats = _patterns()
    for relocated in (
        ".plugin-data/intent_local.sqlite3",
        ".plugin-data/intent_local.jsonl",
        ".plugin-data/intent_settings.json",
    ):
        matched, pattern = is_sealed(relocated, pats)
        assert matched, (
            f"relocated intent-state path {relocated!r} is NOT sealed — the "
            "seal inventory did not track the SC-C path move"
        )


def test_framework_plan_seals_preserved():
    """The portable framework seals survive the translation."""
    pats = _patterns()
    for framework_path in (
        "docs/plans/myslug/myslug_plan.json",
        "docs/plans/myslug/myslug_orchestrator.json",
        "docs/plans/myslug/_state.json",
        "outstanding_issues.md",
    ):
        # outstanding_issues.md lives at docs/outstanding_issues.md in the
        # inventory; assert both the doc-rooted form and the plan globs.
        if framework_path == "outstanding_issues.md":
            matched, _ = is_sealed("docs/outstanding_issues.md", pats)
        else:
            matched, _ = is_sealed(framework_path, pats)
        assert matched, f"framework seal lost for {framework_path!r}"


def test_host_secret_entries_shipped_generic():
    """Host secrets ship as generic-recommended seal entries."""
    pats = _patterns()
    for secret_path in (".env", ".env.local", "~/.aws/credentials", "~/.ssh/id_rsa"):
        matched, _ = is_sealed(secret_path, pats)
        assert matched, f"generic secret seal missing for {secret_path!r}"


def test_no_provisional_prefix_in_seal_inventory():
    """No provisional std-/STD_ token survives in the inventory."""
    text = SEAL_FILE.read_text(encoding="utf-8")
    assert "std-" not in text and "STD_" not in text, (
        "provisional std-/STD_ token present in seal inventory"
    )
