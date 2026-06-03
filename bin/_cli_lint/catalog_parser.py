"""Parser for `docs/cli_tooling_catalog.md`.

Per implplan §N.impl.6 + §N.impl.4: the markdown catalog is the
source of truth. This parser reads the catalog's main table and
returns one `CatalogEntry` per row.

Hand-maintained markdown (not generated) per §N.impl.9 #1 RATIFIED
2026-05-21 — matches the `docs/process_graph_catalog.md` precedent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CATALOG_PATH = REPO_ROOT / "docs" / "cli_tooling_catalog.md"


@dataclass(frozen=True)
class CatalogEntry:
    """One row of docs/cli_tooling_catalog.md."""

    cli: str                    # e.g., "bin/route_issue"
    purpose: str
    required_args: str
    exit_codes: str             # raw "0,2,3,7,25,26,27,28" string
    spec_home: str
    standing_compliance: str    # raw "A/B/C/D/E/F" or "A/B[exempt:...]" string
    inherited: bool = False     # row tagged INHERITED


def _strip_backticks(s: str) -> str:
    """Strip surrounding backticks if present."""
    s = s.strip()
    if s.startswith("`") and s.endswith("`") and len(s) >= 2:
        return s[1:-1]
    return s


def _split_row(row: str) -> list[str]:
    """Split a markdown table row by unescaped `|`; trim outer pipes +
    whitespace. Escaped `\\|` (markdown convention for a literal pipe
    inside a cell) is preserved as a literal `|`."""
    # Use a sentinel to protect escaped pipes during split.
    SENTINEL = "\x00PIPE\x00"
    protected = row.replace("\\|", SENTINEL)
    parts = protected.split("|")
    # Drop leading/trailing empty strings from the wrapping `|`s.
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [p.replace(SENTINEL, "|").strip() for p in parts]


def parse_catalog(path: Path | None = None) -> list[CatalogEntry]:
    """Parse the catalog markdown; return one CatalogEntry per row.

    Raises FileNotFoundError if catalog missing.
    """
    target = path or CATALOG_PATH
    if not target.exists():
        raise FileNotFoundError(f"catalog not found at {target}")
    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()

    entries: list[CatalogEntry] = []
    in_table = False
    header_seen = False

    for raw in lines:
        line = raw.rstrip()
        # Detect table header: starts with `|` and contains `CLI` + `Purpose`.
        if not in_table:
            if line.startswith("|") and "CLI" in line and "Purpose" in line:
                in_table = True
                header_seen = True
                continue
            continue
        # Inside table.
        if not line.startswith("|"):
            # Table ended; latch state but keep header_seen so the final
            # sanity-check still recognizes we DID parse a table.
            in_table = False
            continue
        cells = _split_row(line)
        # Skip the separator row `|---|---|...`.
        if cells and set("".join(cells)) <= set("- :"):
            continue
        if len(cells) < 6:
            continue
        cli_raw, purpose, req_args, exits, spec_home, compliance = cells[:6]
        cli = _strip_backticks(cli_raw)
        # Skip rows where the CLI column is non-binary (e.g., "Notes").
        if not cli.startswith("bin/"):
            continue
        inherited = "INHERITED" in spec_home.upper() or "INHERITED" in compliance.upper()
        entries.append(
            CatalogEntry(
                cli=cli,
                purpose=purpose,
                required_args=req_args,
                exit_codes=exits,
                spec_home=spec_home,
                standing_compliance=compliance,
                inherited=inherited,
            )
        )

    if not header_seen:
        raise ValueError(
            f"catalog at {target} has no parseable CLI table — expected a "
            f"markdown table with columns CLI | Purpose | ... | "
            f"Standing-req compliance"
        )
    return entries


def parse_exit_codes(raw: str) -> list[int]:
    """Parse a catalog `exit_codes` cell into a list of integers.

    Tolerant of common formats:
      - "0,2,5"
      - "0,2,10–17"  (with en-dash range)
      - "0,2,10-17"  (with hyphen range)
      - "0,2,5 (A.impl.3a)"  (trailing prose)
      - "0,2,<cli_lint_violation>"  (named token)

    Named tokens like `<cli_lint_violation>` resolve to their numeric
    value via a small alias map.
    """
    # Strip trailing parenthetical commentary.
    s = re.sub(r"\s*\(.*?\)\s*", "", raw).strip()
    # Replace named tokens.
    aliases = {
        "<cli_lint_violation>": "38",
    }
    for k, v in aliases.items():
        s = s.replace(k, v)
    # Split into atoms.
    out: list[int] = []
    for atom in re.split(r"\s*,\s*", s):
        atom = atom.strip()
        if not atom:
            continue
        # Range: 10–17 or 10-17.
        m = re.match(r"^(\d+)\s*[\-–]\s*(\d+)$", atom)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            out.extend(range(lo, hi + 1))
            continue
        if atom.isdigit():
            out.append(int(atom))
            continue
        # Unrecognized; ignore (REQ_D will re-validate the source code).
    return out


def cli_binaries_in_repo(bin_dir: Path | None = None) -> list[Path]:
    """Enumerate live `bin/*` entries that look like CLIs.

    Excludes the `_*` Python packages (internal modules) and __init__.py.
    Includes both executable wrappers and *.sh scripts.
    """
    target = bin_dir or (REPO_ROOT / "bin")
    if not target.exists():
        return []
    out: list[Path] = []
    for entry in sorted(target.iterdir()):
        name = entry.name
        if name.startswith("_") or name.startswith("."):
            continue
        if name == "__init__.py" or name == "__pycache__":
            continue
        # Skip Python package directories.
        if entry.is_dir():
            continue
        out.append(entry)
    return out
