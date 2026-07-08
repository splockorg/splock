"""F8 regression: `bin/render_plan --kind state` must accept the canonical
schema-less, dict-form `_state.json` that `bin/update_orchestrator` writes.

The CLI `--kind state` path previously validated the RAW dict and fast-failed
(exit 4, SchemaRejectedError on the missing top-level `schema_version`), while
the in-process render path (`render_invoker._render_state_inner`) already
normalized via `_adapt_dict_tasks_to_list`. F8 routes the CLI through the same
adapter so the two entrypoints accept byte-identical inputs. No prior test
exercised this path — that gap is what let the divergence ship.
"""

from __future__ import annotations

import json

from bin._render_plan import main as render_main


def _write_dict_form_state(slug_dir) -> None:
    """The exact shape bin/update_orchestrator's state_writer emits: dict-form
    `tasks`, and NO top-level `schema_version` / `slug` / `phase`."""
    slug_dir.mkdir(parents=True, exist_ok=True)
    (slug_dir / "_state.json").write_text(
        json.dumps(
            {
                "lifecycle": "active",
                "tasks": {"T1": {"status": "done"}, "T2": {"status": "wip"}},
            }
        ),
        encoding="utf-8",
    )


def test_render_state_kind_accepts_dict_form_schemaless_state(tmp_path, monkeypatch):
    plans = tmp_path / "docs" / "plans"
    _write_dict_form_state(plans / "demo_slug")
    # Redirect the plans dir at the tmp fixture; the render uses the real
    # shipped `.claude/templates/state_md_canonical.md.template`. `--dry-run`
    # validates + renders without writing the MD twin.
    monkeypatch.setattr(render_main, "_PLANS_DIR", plans)
    rc = render_main.main(["demo_slug", "--kind", "state", "--dry-run"])
    assert rc == 0, f"expected exit 0 for dict-form schema-less state, got {rc}"
