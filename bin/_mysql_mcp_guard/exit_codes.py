"""Closed-enum exit codes for `bin/mysql-mcp-guard`.

Continues the shared splock registry (…45/46 fleet, 47/48 C&C, 49 eli5,
50 fleet close). Codes 0 / 1 are shared with the rest of the CLI surface.
"""

from __future__ import annotations

EXIT_OK = 0
"""Clean: no `mysql` MCP server configured (inert), read-only credential
verified, statement clean, or guard mode `warn`/`off` downgraded the finding
to a warning."""

EXIT_USAGE = 1
"""Argparse usage error."""

EXIT_WRITE_CAPABLE = 51
"""The `mysql` MCP credential holds privileges beyond the read allowlist
(or, for `statement`, the scanned input is write-shaped). Refuse until the
MySQL user is narrowed to SELECT-class grants."""

EXIT_UNVERIFIABLE = 52
"""A `mysql` MCP server is configured but the guard could not evaluate the
credential (no mysql/mariadb client, unresolvable credentials, connection
failure). A gate that could not run is not a pass (VISION §4.7) — halt mode
refuses; `SPLOCK_MYSQL_MCP_GUARD=warn` downgrades."""
