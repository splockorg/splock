"""CLI entry point for `bin/wrap`.

Per std_command_operator_extensions task TF: a deterministic helper that
slash-command MDs without Python CLI substrates (`/recon`, `/research`,
`/qna`) can invoke via Bash to wrap operator-directive text in the
canonical `<operator-directive>...</operator-directive>` delimiter
shape before threading it into a main-agent Agent-tool spawn prompt.

The closed `--kind` enum is sourced from
`bin._planner.external_input_sanitize.WrapKind`, so future additions to
the wrap-discipline inventory propagate to this helper without code
change (`test_wrap_closed_enum_alignment.py` pins forward-compat).

CLI dispatch:
- `python -m bin._wrap.main --kind <closed-enum> --content <str>`
- `python -m bin._wrap.main --kind <closed-enum> < /dev/stdin`

Exit codes:
- 0 = success (wrapped content emitted to stdout)
- 1 = usage error (unknown kind, missing --kind, size cap exceeded)

Size cap: 8192 bytes (UTF-8 encoded). Per the std_command_operator_extensions
contract SC10 — operator-authored directives larger than 8KB indicate a
copy-paste mistake rather than intent; the helper refuses with exit 1
and a clear stderr message instead of silently truncating.
"""

from __future__ import annotations

import argparse
import sys
from typing import get_args

from bin._planner.external_input_sanitize import WrapKind, wrap

_DIRECTIVE_BYTE_CAP = 8192
"""Per SC10: operator directives MUST NOT exceed 8KB. Beyond this size,
the content is overwhelmingly likely to be a paste error rather than
deliberate guidance — refuse loudly instead of silently truncating."""

# Materialize the closed enum once at import for argparse + tests.
_ALLOWED_KINDS: tuple[str, ...] = get_args(WrapKind)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/wrap",
        description=(
            "Wrap external content in the canonical delimiter pair for the "
            "named kind (closed enum sourced from bin._planner."
            "external_input_sanitize.WrapKind). Used by slash-command MDs "
            "without Python CLI substrates to prepare operator-authored "
            "directives for Agent-tool spawn prompts."
        ),
    )
    parser.add_argument(
        "--kind",
        required=True,
        choices=_ALLOWED_KINDS,
        help=(
            "Delimiter kind. Closed enum — adding a kind requires updating "
            "bin._planner.external_input_sanitize.WrapKind."
        ),
    )
    parser.add_argument(
        "--content",
        default=None,
        help=(
            "Content to wrap. If omitted, reads content from stdin. "
            "Either source must be present."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 on usage error; map to our exit 1.
        if exc.code in (0, None):
            return 0
        return 1

    if args.content is not None:
        content = args.content
    else:
        # Read content from stdin (preserves exact bytes; rstrip the
        # single trailing newline operators get from a here-string or
        # `echo` invocation so the wrap output isn't padded with a stray
        # blank line — `wrap()` already adds its own newline padding).
        raw = sys.stdin.read()
        if raw.endswith("\n"):
            content = raw[:-1]
        else:
            content = raw

    # SC10 — size cap on the operator-authored content. Measure UTF-8 bytes
    # (consistent with how the directive is transmitted across the SDK).
    byte_len = len(content.encode("utf-8"))
    if byte_len > _DIRECTIVE_BYTE_CAP:
        print(
            f"bin/wrap: content exceeds {_DIRECTIVE_BYTE_CAP}-byte cap "
            f"({byte_len} bytes); refuse to wrap. If this is intentional, "
            f"split the directive into smaller pieces.",
            file=sys.stderr,
        )
        return 1

    wrapped = wrap(content, args.kind)
    sys.stdout.write(wrapped)
    # Trailing newline so the output is line-terminated when redirected
    # to a file or piped through line-oriented tools.
    if not wrapped.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
