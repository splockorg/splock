"""J.5 — `KNOWN_WRITERS` frozenset entries match actual emitter call sites in source.

Per inventory:
- Source: Risk 3 + §10 §4.2 finding #7 (KNOWN_WRITERS at v5 vs v4 — additive
  growth absorbed correctly).
- Expected outcome: every emitter ID in `bin/_jsonl_log/writers.py::KNOWN_WRITERS`
  appears as a string literal in at least one source file under `bin/`;
  every `append_row(..., emitted_by=...)` call site uses an ID present in
  KNOWN_WRITERS.
"""

from __future__ import annotations

import pytest
import re
from pathlib import Path


pytestmark = pytest.mark.acceptance


def test_every_known_writer_appears_in_source(repo_root):
    """J.5a: every KNOWN_WRITERS entry is used as an emitted_by string somewhere."""
    from bin._jsonl_log.writers import KNOWN_WRITERS

    assert KNOWN_WRITERS, "KNOWN_WRITERS frozenset is empty — unexpected"

    bin_dir = repo_root / "bin"
    py_files = list(bin_dir.rglob("*.py"))
    # Concatenate source as a single text blob for grep.
    all_source = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in py_files)

    unused: list[str] = []
    for writer_id in KNOWN_WRITERS:
        # Match the writer_id as a string literal anywhere.
        if f'"{writer_id}"' not in all_source and f"'{writer_id}'" not in all_source:
            unused.append(writer_id)

    assert not unused, (
        "KNOWN_WRITERS entries that don't appear as string literals in bin/*.py:\n"
        + "\n".join(f"  - {w}" for w in unused)
        + "\n(Either remove from KNOWN_WRITERS or wire up the missing emit site.)"
    )


def test_no_emitted_by_callsite_uses_unknown_writer(repo_root):
    """J.5b: every emitted_by=<literal> in source uses a value present in KNOWN_WRITERS.

    Greps for `emitted_by="..."` and `emitted_by='...'` call sites,
    extracts the literal, asserts it's in KNOWN_WRITERS.
    """
    from bin._jsonl_log.writers import KNOWN_WRITERS

    bin_dir = repo_root / "bin"
    py_files = list(bin_dir.rglob("*.py"))
    # Skip the writers.py file itself (defines KNOWN_WRITERS).
    py_files = [p for p in py_files if p.name != "writers.py"]

    pattern = re.compile(r'emitted_by\s*=\s*["\']([a-zA-Z0-9_:/.\-]+)["\']')
    unknown_emit_sites: list[tuple[str, str]] = []
    for path in py_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in pattern.finditer(text):
            writer_id = match.group(1)
            if writer_id not in KNOWN_WRITERS:
                rel = path.relative_to(repo_root)
                unknown_emit_sites.append((str(rel), writer_id))

    assert not unknown_emit_sites, (
        "Source emits emitted_by=<value> not present in KNOWN_WRITERS:\n"
        + "\n".join(f"  {p}: emitted_by={w!r}" for p, w in unknown_emit_sites)
    )
