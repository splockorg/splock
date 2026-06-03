---
name: test
description: Run the bounded test-step retry loop against a slug's declared test mechanisms via bin/verify. Use when the user says "test X", "run the tests for X", "/test X", or to clear an orchestrator test_gate junction. Runs the union of the slug's tasks' tests_enabled with bounded retries.
---

# test

Bounded test-step retry loop for a slug. Runs `bin/verify test-step <slug>`.

Operator entry: `/test <slug>`.

Executes the union of the slug's tasks' `tests_enabled` mechanisms with a
bounded retry loop (`bin/_retry_loop`). Used to clear a `test_gate` junction
between phases.
