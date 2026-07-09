"""`bin/log` — non-hook-context structured-log emitter sibling to `bin/hook-log`.

Per implplan §G.impl.11 follow-up edit #5 (v1.4-revised-2 N#4 take-back).
Per plan §N.2 requirement C: "CLIs emit structured logs via
`bin/hook-log` (or `bin/log` for non-hook contexts)."

The sibling is required for CLI emit-points that need structured
logging but aren't running inside a Claude Code hook (e.g., `bin/marker
validate` running in CI, `bin/chain-overnight` driver-shell loops,
`bin/route_issue` cron-mode invocations).

Output: `~/.claude/logs/cli-<YYYY-MM-DD>.jsonl` (sibling to `hooks-`).
Key `emitter` (vs `hook` for hook-log) — distinct file so a `jq` over
`hooks-` doesn't pull in CLI rows.

The KNOWN_WRITERS allowlist is enforced for `bin/log` (§G.impl.11
"<emitter> KNOWN_WRITERS value — exact-match against §C.impl.3 enum"):
unknown emitter → exit 4.
"""

from __future__ import annotations
