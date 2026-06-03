# splock JSON Schemas

This directory holds the canonical JSON Schemas (Draft 2020-12) for the
JSON-canonical plan substrate authored by `bin/render_plan` and
`bin/verify_plan` (per splock implplan §B.impl.3).

## Files at v2.7 ship

| Path | Purpose |
|---|---|
| `plan_v1.schema.json` | `<slug>_plan.json` schema |
| `orchestrator_v1.schema.json` | `<slug>_orchestrator.json` schema |
| `process_graph.schema.json` | (Pre-existing, unrelated to §B.) |

## Version policy

Per implplan §B.impl.3 lines 1090-1101:

1. **Schema bumps are additive.** Add a new file
   `plan_v<N>.schema.json` (or `orchestrator_v<N>.schema.json`). Do NOT
   mutate an existing version in-place. Legacy versions are retained for
   forward-compat loader fallback.
2. **Each version maintains a deprecation window.** Defer to §M post-policy
   for the specific window length.
3. **`schema_registry.py` enumerates known versions.** Unknown future
   versions are rejected per implplan §B.impl.6 with exit code 5 and a
   structured stderr JSON envelope.
4. **Schema bump rolls out via the Pillar 3.G "take back to v2.7" mechanism.**
   No in-place schema mutation across versions.

## Forward-compat refusal envelope

Per implplan §B.impl.6 (lines 1219-1262): both `bin/render_plan` and
`bin/verify_plan` route through `bin/_render_plan/schema_registry.py` for
the version check. On unsupported future version:

```json
{"error":"unsupported_schema_version","kind":"plan","seen":2,"supported":[1]}
```

On too-old:

```json
{"error":"schema_version_too_old","kind":"plan","seen":0,"supported":[1]}
```

Both emit exit code 5 (`EXIT_UNSUPPORTED_SCHEMA_VERSION`).

## Sealed-state inventory pointer

Per implplan §B.impl.10 (lines 1384-1396): two paths are added to the
cross-cutting sealed-state inventory consumed by §G.impl's
`chain-sealed-state-delete-block` PreToolUse hook:

- `docs/plans/<slug>/<slug>_plan.json`
- `docs/plans/<slug>/<slug>_orchestrator.json`

The derived MD files (`<slug>_plan.md`, `<slug>_orchestrator.md`) are
**not** sealed — operator anchor-block edits must survive re-render per
implplan §B.impl.5. §G.impl will register these paths when it lands;
this build (Phase 1 §B) does not author `.claude/hooks/`.

## Per-phase planner integration

Per implplan §B.impl.7: the planner subagent (built in §D.impl) emits
JSON under Anthropic Structured Outputs. The schema files here are read
verbatim and passed as the SDK `output_format` parameter. The driver
writes the JSON to `<slug>_plan.json` (or `<slug>_orchestrator.json`)
via `bin/_render_plan/atomic_write.write_atomic`; the subagent itself
does not write to disk. Tested by
`tests/splock/test_plan_substrate/integration/test_two_call_planner_handshake.py`.
