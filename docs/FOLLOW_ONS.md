# Follow-ons — named deferred scope

splock v1 ships a deliberately bounded surface. Scope that was considered and
intentionally left out is recorded here as a **named** follow-on so nothing is
silently dropped. Each entry states what it is, why it was deferred, and the
condition under which it should be picked up. None of these are dangling TODOs
in the shipped code — they are explicit future work.

---

## FO-1 — Privileged plumbing-admin surface ("super skill")

**What.** A privileged, human-only way to modify splock's own deterministic
plumbing — its hooks, the sealed-path inventory, and related enforcement
files — from inside a session, via vetted scripts rather than ad-hoc edits.

**Why deferred.** The plumbing is intentionally sealed to the agent in v1 (it is
the enforcement spine; an agent must not be able to weaken it). Adding a
controlled write path is a meaningful extension of the hook substrate and
carries its own threat model: it must be armed only on turn 1 of an
*interactive* human session, refused for subagents and headless runs, and
gated by an out-of-band confirmation at apply time. That enforcement must live
in a hook, not in skill prose, which makes it a feature in its own right rather
than a tweak.

**Pick-up condition.** After v1 is published and the hook substrate is stable.
Designed and built under its own initiative, with the interactive-turn-1
arming hook and the apply-time confirmation as the out-of-box gate, plus
deployment-hardening guidance (root-owned plumbing readable but not writable by
the agent uid; a password-gated write helper) as the documented hard boundary.

---

## FO-2 — Non-pytest gate commands wired into the test surface

**What.** First-class, separately-invocable gate commands for the non-pytest
checks (manifest validation, the host-trace grep, marker round-trips), so a
single test invocation can run the full gate set rather than only the pytest
suite.

**Why deferred.** The authoritative pytest suite plus the standalone
`tests/trace_grep.sh` and `claude plugin validate --strict` already cover the
gates; wiring them as one unified command is convenience, not correctness.

**Pick-up condition.** When the project adopts a single CI entry point and wants
one command to fan out to every gate.

---

## FO-3 — Operator console / dashboard

**What.** A browser or TUI surface for watching chains run, inspecting state,
and staging operator decisions.

**Why deferred.** The lifecycle is fully drivable through Claude Code slash
commands and the `bin/` CLIs. A console is a downstream presentation layer, not
part of the plugin's core contract, and would couple the plugin to a UI stack.

**Pick-up condition.** A separate project, consuming splock's JSON state and CLI
surface as its API.

---

## How to adopt a follow-on

Open an issue referencing the `FO-` id, design it plan-first (splock's own
lifecycle applies to splock), and keep any new enforcement in hooks/CLI exit
codes per the project's deterministic-enforcement rule.
