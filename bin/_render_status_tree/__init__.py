"""Render `<slug>_orchestrator_status.md` — DAG tree + status glyphs.

Combines:
  - `<slug>_orchestrator.json` (DAG structure: tasks[], depends_on, junctions[])
  - `_state.json` (canonical task status per splock v2.7 §E.2)

Output: `<slug>_orchestrator_status.md` with an ASCII-tree showing every
task in its dependency hierarchy, each tagged with its current status
glyph + inline junction markers gating each subtree's children.

Per operator request 2026-05-24. The existing `_orchestrator.md` is a
flat status table (glyphs only); `<slug>_orchestrator.md` is the static
DAG MD twin (no status). This is the third view: tree + status combined.
"""
