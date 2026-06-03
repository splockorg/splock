---
name: wrap
description: Sanitize and delimit an operator directive (or other external input) into a closed-enum WrapKind envelope before it is injected into a planning/coding prompt — the data-not-instructions boundary. Use when external/operator free-text must be safely embedded in a prompt, when the user says "wrap this directive", or as the pre-injection step inside /code, /qna, /research, and /plan. Enforces an 8KB cap and refuses unknown kinds.
---

# wrap

Operator-directive (and external-input) sanitization helper. Wraps free-text in
a closed-enum `WrapKind` envelope with a data-not-instructions delimiter, so
untrusted input is treated as data, never as instructions.

Operator/CLI entry: `bin/wrap --kind <wrap-kind> --content "<text>"` (or via
stdin). Routes to `python -m bin._wrap.main`, backed by
`bin/_planner/external_input_sanitize.py` (the `WrapKind` closed enum +
`DELIMITER_INSTRUCTION` + `wrap()`).

Enforces an 8KB content cap and refuses unrecognized kinds. Invoked as the
pre-injection step by `/code`, `/qna`, `/research`, and the planner.
