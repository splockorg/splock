"""mysql-mcp-guard — deterministic read-only gate for the `mysql` MCP surface.

Provenance: operator request 2026-08-03, follow-on to the qna `mcp__mysql`
grant (commit 535c9c2). The grant's read-only story named the adopter's DB
credential as the only load-bearing tier because the Bash hook spine never
sees MCP calls. This package closes that gap with two deterministic layers
(VISION §4.1: prose cannot enforce a boundary; §4.9: name the tiers):

  1. **Statement filter** (`statement.py`) — a PreToolUse hook on every
     MCP server whose name begins with ``mysql`` (matcher
     ``mcp__mysql.*``) denies write-shaped tool calls (non-read leading
     verbs, INTO OUTFILE, write-lock clauses, write-shaped tool names)
     before they reach the server.
  2. **Credential probe** (`probe.py`) — ``SHOW GRANTS FOR CURRENT_USER()``
     through the adopter's own `mysql` client, against the SPECIFIC
     server a call targets; any privilege beyond the read allowlist
     refuses the run until the MySQL user is narrowed (VISION §4.7:
     fail closed, fail loudly).

The credential itself remains the hard floor — a hook only runs where hooks
are installed. The probe exists to make a write-capable credential a loud
configuration error instead of a silent one.

Mode knob: ``SPLOCK_MYSQL_MCP_GUARD`` ∈ ``halt`` (default) / ``warn`` /
``off`` — §4.12 discipline: on by default, configuration turns it off.
"""
