# Splock Agent Guidelines — Antigravity workspace pointer

**The canonical agent guidelines live at [`../AGENTS.md`](../AGENTS.md) in the
repository root. Read that file.**

This directory (`.agents/`) is the Antigravity (`agy`) workspace convention —
it is where that host looks for `hooks.json` and `skills/`. The root
`AGENTS.md` is what Claude Code and Codex read. Keeping the directives in one
place and pointing here is deliberate: per `docs/VISION.md` §4.5, state that is
duplicated rather than derived will rot, and two copies of the standing rules
would drift apart the first time one was edited.

Start with `../AGENTS.md`, then `docs/VISION.md` (first-principles reference)
and `docs/IMPLEMENTATION_STATUS.md` (what is actually built).
