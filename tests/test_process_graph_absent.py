"""T-A grep assertion: ``process_graph`` is absent from the shipped ``schemas/``.

SC-A DROPs ``schemas/process_graph.schema.json``. This asserts (a) the file is
gone, and (b) no remaining schema FILE NAME references process_graph. (A broad
content-grep for the substring ``process_graph`` across schema bodies is NOT
done here: that belongs to the T-F authoritative trace gate, which owns the
single reusable grep over the whole tree. T-A's scope is the schemas-dir file
inventory.)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = REPO_ROOT / "schemas"


def test_no_process_graph_schema_file() -> None:
    offenders = [p.name for p in SCHEMAS_DIR.glob("*.json") if "process_graph" in p.name]
    assert not offenders, f"process_graph schema file(s) present in shipped schemas/: {offenders}"


def test_schemas_dir_nonempty_sanity() -> None:
    # Guard against a wildcard mistake having emptied the dir.
    assert len(list(SCHEMAS_DIR.glob("*.schema.json"))) >= 14, (
        "shipped schemas/ unexpectedly sparse — expected the framework subset"
    )


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
