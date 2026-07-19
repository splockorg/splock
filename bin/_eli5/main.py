"""CLI entry point for `bin/eli5`.

Deliberately subject-file-only in v1: the CLI has no conversation and no
slug binding — those (plus append/reopen artifact modes and the ≥3
auto-offer) are in-Claude surface features owned by `commands/eli5.md`.
"Parity" means the core brief-generation call, not the artifact
mechanics.

Usage:
  bin/eli5 --subject-file <path> [--focus "<text>"]
           [--mode {auto,decision,informative}] [--out <path>]
           [--prompt-file]
  bin/eli5 --print-format {auto,decision,informative}
  bin/eli5 --next-prompt-path <dir>

`--print-format` prints the deterministic FORMAT_MD and exits 0 — this
is how the in-Claude driver obtains the byte-exact format for subagent
injection (never by transcribing it). `--next-prompt-path` prints the
next `_eli5_prompt_<N>.txt` path for a directory (the driver's
numbering helper — same determinism posture).

Exit codes per `bin/_eli5/exit_codes.py`:
  0  = success
  1  = usage (incl. `--prompt-file` without `--out`)
  7  = atomic write failed
  17 = SDK call failed (same code as bin/qa)
  49 = subject file missing/unreadable/empty
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from bin._eli5 import exit_codes
from bin._eli5.format import MODES, build_format
from bin._eli5.invoke import Eli5SdkFailed, invoke_eli5
from bin._eli5.promptfile import (
    build_prompt_sheet,
    count_decision_items,
    next_prompt_path,
)
from bin._eli5.subject import truncate_subject


def _emit_stderr_json(payload: dict) -> None:
    sys.stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/eli5",
        description=(
            "Plainspeak briefing generator (the eli5 lens: translation, not "
            "review, not investigation). Subject-file-only in v1; the "
            "conversation-scoped and slug-bound surfaces live in /eli5 "
            "(commands/eli5.md). See docs/feedback_eli5_terminology.md."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "--print-format",
        dest="print_format",
        choices=MODES,
        default=None,
        help="print the deterministic FORMAT_MD for the mode and exit 0",
    )
    parser.add_argument(
        "--next-prompt-path",
        dest="next_prompt_dir",
        default=None,
        help="print the next _eli5_prompt_<N>.txt path for DIR and exit 0",
    )
    parser.add_argument("--subject-file", dest="subject_file", default=None,
                        help="the material to translate (required for a run)")
    parser.add_argument("--focus", default=None,
                        help="operator focus text narrowing the briefing")
    parser.add_argument("--mode", choices=MODES, default="auto")
    parser.add_argument("--out", default=None,
                        help="also write the briefing to this path")
    parser.add_argument("--prompt-file", dest="prompt_file", action="store_true",
                        help="write the paste-able decision sheet "
                             "(requires --out; sheet lands beside it)")
    return parser


def _resolve_caller_path(raw: str) -> Path:
    """Resolve an operator-supplied path against the INVOKING directory.

    The `bin/eli5` wrapper `cd`s into the plugin/checkout root before
    exec (and exports `SPLOCK_CALLER_PWD` precisely to preserve the
    invoking dir) — so a raw relative path here would resolve against
    the splock checkout, not where the operator ran the command. Field
    defect (qum burn-in closeout, 2026-07-19, filed on the PR): a
    relative `--subject-file` exit-49'd on a file that existed, and a
    relative `--out` would have written the briefing INTO the checkout.
    """
    p = Path(raw)
    if p.is_absolute():
        return p
    caller = os.environ.get("SPLOCK_CALLER_PWD")
    return (Path(caller) / p) if caller else p


def _atomic_write(target: Path, text: str) -> None:
    tmp = Path(f"{target}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def _sheet_path_for(out: Path) -> Path:
    """The CLI decision-sheet path: beside --out, `<stem>_prompt.txt`."""
    return out.with_name(out.stem + "_prompt.txt")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exit_codes.EXIT_USAGE if exc.code != 0 else exit_codes.EXIT_OK

    # ── deterministic helper surfaces (no SDK) ────────────────────────
    if args.print_format is not None:
        sys.stdout.write(build_format(args.print_format))
        return exit_codes.EXIT_OK
    if args.next_prompt_dir is not None:
        target = _resolve_caller_path(args.next_prompt_dir)
        if not target.is_dir():
            _emit_stderr_json({"error": "usage",
                               "detail": f"not a directory: {target}"})
            return exit_codes.EXIT_USAGE
        print(next_prompt_path(target))
        return exit_codes.EXIT_OK

    # ── the run ───────────────────────────────────────────────────────
    if args.subject_file is None:
        _emit_stderr_json({
            "error": "usage",
            "detail": "--subject-file is required (the CLI is subject-file-"
                      "only in v1; conversation/slug scoping is /eli5's)",
        })
        return exit_codes.EXIT_USAGE
    if args.prompt_file and args.out is None:
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        _emit_stderr_json({
            "error": "usage",
            "detail": (
                "--prompt-file requires --out in the CLI (no conversation to "
                f"confirm a proposal in); it would have used ./eli5_prompt_{ts}.txt"
            ),
        })
        return exit_codes.EXIT_USAGE

    subject_path = _resolve_caller_path(args.subject_file)
    try:
        subject_raw = subject_path.read_text(encoding="utf-8")
    except OSError as exc:
        _emit_stderr_json({"error": "subject_unreadable",
                           "detail": f"{subject_path}: {exc}"})
        return exit_codes.EXIT_SUBJECT_UNREADABLE
    if not subject_raw.strip():
        _emit_stderr_json({"error": "subject_unreadable",
                           "detail": f"{subject_path}: empty subject"})
        return exit_codes.EXIT_SUBJECT_UNREADABLE

    subject_md, omitted = truncate_subject(subject_raw)
    if omitted:
        print(f"note: subject truncated at 8KB — {omitted} chars omitted "
              f"(tail-first at a paragraph boundary)", file=sys.stderr)

    try:
        result = invoke_eli5(subject_md, mode=args.mode, focus=args.focus)
    except Eli5SdkFailed as exc:
        _emit_stderr_json({"error": "eli5_sdk_failed", "detail": exc.detail})
        return exit_codes.EXIT_SDK_CALL_FAILED

    briefing = result.briefing_md
    if not briefing.endswith("\n"):
        briefing += "\n"
    sys.stdout.write(briefing)

    try:
        if args.out:
            out_path = _resolve_caller_path(args.out)
            _atomic_write(out_path, briefing)
            print(f"wrote {out_path}", file=sys.stderr)
        if args.prompt_file:
            decisions = count_decision_items(briefing)
            if decisions == 0:
                print("no decisions — nothing to sheet", file=sys.stderr)
            else:
                sheet = _sheet_path_for(_resolve_caller_path(args.out))
                _atomic_write(sheet, build_prompt_sheet(briefing))
                print(f"wrote decision sheet ({decisions} decisions) → {sheet}",
                      file=sys.stderr)
    except OSError as exc:
        _emit_stderr_json({"error": "atomic_write_failed", "detail": str(exc)})
        return exit_codes.EXIT_ATOMIC_WRITE_FAILED

    return exit_codes.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
