"""Closed-enum exit codes for `bin/route_issue` + `bin/lazy-dump-check`.

References the cross-CLI shared registry at §A.impl.3a. Codes 25–28 are
§L-allocated (v1.3); codes 0, 1, 2, 7 are shared.

| Code | Family | Source |
|---|---|---|
| 0  | success                          | universal |
| 1  | usage                            | argparse / bad CLI surface |
| 2  | origin_line_not_found            | §L (tier-promote — generic) |
| 7  | atomic_write_failed              | shared (B.impl.4) |
| 25 | escalation_trigger_fired         | §L (L.impl.4) |
| 26 | outstanding_cap_exceeded         | §L (L.impl.7) |
| 27 | tier_promote_slug_exists         | §L (L.impl.8) |
| 28 | rubric_refuse_no_category_fits   | §L (L.impl.5) |
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ORIGIN_LINE_NOT_FOUND = 2
EXIT_ATOMIC_WRITE_FAILED = 7
EXIT_ESCALATION_TRIGGER_FIRED = 25
EXIT_OUTSTANDING_CAP_EXCEEDED = 26
EXIT_TIER_PROMOTE_SLUG_EXISTS = 27
EXIT_RUBRIC_REFUSE_NO_CATEGORY_FITS = 28

ALL_CODES = frozenset(
    {
        EXIT_OK,
        EXIT_USAGE,
        EXIT_ORIGIN_LINE_NOT_FOUND,
        EXIT_ATOMIC_WRITE_FAILED,
        EXIT_ESCALATION_TRIGGER_FIRED,
        EXIT_OUTSTANDING_CAP_EXCEEDED,
        EXIT_TIER_PROMOTE_SLUG_EXISTS,
        EXIT_RUBRIC_REFUSE_NO_CATEGORY_FITS,
    }
)
