"""bin/_intent — §P agent-session intent registry & collision management.

Per implplan §P.impl (P.impl.1-P.impl.17). Seven subcommands + auto-register
API + PreToolUse hook resolver. Single-writer contract over
`extraction.agent_sessions` / `agent_session_collision_log` / `intent_event_log`
+ `docs/intent/intent_local.jsonl`.
"""
