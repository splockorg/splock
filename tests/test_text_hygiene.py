"""The glyph tripwire: mojibake in emitted MD warns; clean text stays silent.

Context: "planner glyph corruption on emit" — model text written to the MD
files sometimes carries UTF-8-read-as-CP1252 artifacts (`→` becomes `â†'`,
`§` becomes `Â§`), which operators were catching by proofreading. The plan
JSON is immune (`json.dumps` ensure_ascii-escapes every glyph), so the two MD
seams are the whole detection surface.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._text_hygiene import find_mojibake, warn_mojibake  # noqa: E402


# --------------------------------------------------------------------------- #
# Clean text must never warn — the intended glyphs are not mojibake.           #
# --------------------------------------------------------------------------- #


def test_intended_glyphs_are_not_flagged() -> None:
    """The exact characters the corruption mangles are, unmangled, fine."""
    clean = (
        "# Plan §4 — retrieval\n"
        "step 1 → step 2 → done…\n"
        "necessity ≥ 0.4; “quoted”, ‘single’, • bullet, ± tolerance\n"
        "café, naïve, Zürich — legitimate accents stay legal\n"
    )
    assert find_mojibake(clean) == []


def test_plain_ascii_is_not_flagged() -> None:
    assert find_mojibake("just ascii, nothing to see") == []


# --------------------------------------------------------------------------- #
# Each corruption family is caught, with the right line number.                #
# --------------------------------------------------------------------------- #


def test_the_classic_families_are_caught() -> None:
    mangled = (
        "clean line\n"
        "step 1 â†' step 2\n"        # → misdecoded
        "per Â§4 of the plan\n"       # § misdecoded
        "donâ€™t worry â€” fine\n"    # ’ and — misdecoded
        "cafÃ© rendezvous\n"          # é misdecoded
        "hard fail � here\n"     # replacement char
    )
    findings = find_mojibake(mangled)
    lines_hit = {lineno for lineno, _, _ in findings}
    assert lines_hit == {2, 3, 4, 5, 6}


def test_one_report_per_line_and_marker() -> None:
    findings = find_mojibake("â€™ and â€œ and â€™ again")
    # One line, one marker family ("â€"), reported once.
    assert len(findings) == 1
    lineno, marker, excerpt = findings[0]
    assert (lineno, marker) == (1, "â€")
    assert excerpt.startswith("â€™")


# --------------------------------------------------------------------------- #
# The warn wrapper: stderr lines, a count, a cap, and it never raises.         #
# --------------------------------------------------------------------------- #


def test_warn_writes_pointed_stderr_lines() -> None:
    out = io.StringIO()
    count = warn_mojibake("ok\nbroken â†' arrow\n", "docs/plans/x/x_plan.md", stream=out)
    assert count == 1
    text = out.getvalue()
    assert "glyph-lint: docs/plans/x/x_plan.md:2:" in text
    assert "â†" in text


def test_warn_is_silent_on_clean_text() -> None:
    out = io.StringIO()
    assert warn_mojibake("all clean → really", "x.md", stream=out) == 0
    assert out.getvalue() == ""


def test_warn_caps_the_report_but_returns_the_true_count() -> None:
    out = io.StringIO()
    mangled = "\n".join("bad â€” line" for _ in range(30))
    count = warn_mojibake(mangled, "x.md", stream=out)
    assert count == 30
    reported = [ln for ln in out.getvalue().splitlines() if ": suspected mojibake" in ln]
    assert len(reported) == 20
    assert "and 10 more" in out.getvalue()


def test_warn_never_raises_even_on_a_broken_stream() -> None:
    class _Boom:
        def write(self, *_a):  # pragma: no cover - exercised via print
            raise OSError("stream gone")

    assert warn_mojibake("bad â€” line", "x.md", stream=_Boom()) == 0


# --------------------------------------------------------------------------- #
# The seams are actually wired.                                                #
# --------------------------------------------------------------------------- #


def test_both_emit_seams_call_the_tripwire() -> None:
    """Guards the wiring, not just the module: the two places model text
    hits disk as MD must invoke `warn_mojibake`. (Same spirit as the
    shipped-surfaces guard — a detector nothing calls detects nothing.)
    """
    for rel in ("bin/_planner/main.py", "bin/_render_plan/main.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "warn_mojibake" in src, f"{rel} no longer calls the glyph tripwire"
