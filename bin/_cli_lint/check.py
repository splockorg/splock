"""Per-CLI check runner. Glues catalog + rules + exemptions.

Per implplan §N.impl.3. Exposes:
  - lint_cli(cli_path, entry) -> list[Violation]
  - lint_all(catalog) -> list[Violation]
"""

from __future__ import annotations

from pathlib import Path

from bin._cli_lint.catalog_parser import CatalogEntry, REPO_ROOT
from bin._cli_lint.rules import (
    RULES,
    Violation,
    compute_global_violations,
)


def lint_cli(cli_path: Path, entry: CatalogEntry | None) -> list[Violation]:
    """Run every standing-rule check against a single CLI."""
    out: list[Violation] = []
    for _rule_id, _statement, fn in RULES:
        out.extend(fn(cli_path, entry))
    return out


def lint_catalog(
    catalog: list[CatalogEntry],
    bin_dir: Path | None = None,
    only_cli: str | None = None,
) -> list[Violation]:
    """Run all standing rules across every catalog entry that has a
    corresponding binary on disk. Adds REQ_F global violations once.

    Skips INHERITED entries that have no on-disk binary (those are
    verified separately by integration/test_inherited_cli_smoke.py).
    """
    target_bin = bin_dir or (REPO_ROOT / "bin")
    out: list[Violation] = []
    for entry in catalog:
        if only_cli is not None and entry.cli != only_cli:
            continue
        # Resolve the binary on disk. Catalog cli is like "bin/marker".
        rel = entry.cli
        if not rel.startswith("bin/"):
            continue
        name = rel.split("/", 1)[1]
        # Some entries point at module paths like `bin/_planner/main.py`.
        if "/" in name:
            cli_path = REPO_ROOT / "bin" / name
        else:
            cli_path = target_bin / name
        if not cli_path.exists():
            if entry.inherited:
                # INHERITED CLIs are gated separately by smoke tests.
                continue
            out.append(Violation(
                rule="CATALOG_MISSING_BINARY",
                cli=entry.cli,
                line=0,
                detail=f"catalog row exists but {cli_path} not found on disk",
            ))
            continue
        out.extend(lint_cli(cli_path, entry))
    # REQ_F global check runs once per catalog invocation.
    if only_cli is None:
        out.extend(compute_global_violations(catalog))
    else:
        # Run global check but filter to violations involving only_cli.
        out.extend(
            v for v in compute_global_violations(catalog)
            if v.cli == only_cli
        )
    return out
