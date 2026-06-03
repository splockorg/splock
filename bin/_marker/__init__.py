"""bin/_marker — scheduled-markers CLI implementation.

Per splock implplan §K.impl. The `bin/marker` POSIX shell
wrapper invokes `python -m bin._marker.main`. Public surface is exposed
through the wrapper; this package's modules are not intended for
import from outside the CLI.

Sub-modules:
- `main`             — argparse entry; dispatches to subcommand modules
- `parser`           — round-trip read/write of list.md (preserves order)
- `schema`           — JSON Schema load + validation against marker_v1
- `trigger_parser`   — three trigger-shape grammar (edit / date / condition)
- `refusal`          — closed-enum anti-pattern refusal table (§K.impl.5)
- `prefix`           — prefix_registry.md reader; sequence allocator
- `edit_block`       — detail-file writer per §K.impl.6 template
- `log_emit`         — thin delegate to bin._jsonl_log.writer.append_row
- `create`           — create subcommand
- `close`            — close subcommand
- `list_cmd`         — list subcommand
- `show`             — show subcommand
- `validate`         — validate subcommand
- `register_prefix`  — register-prefix subcommand
- `route_marker`     — morning-review wrapper hand-off entry point
"""
