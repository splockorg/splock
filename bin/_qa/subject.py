"""Closed-enum review SUBJECT for `bin/qa`.

`/qa` is an adversarial-review CLI. Historically its review subject was
hard-wired to `<slug>_recon.md`. This module introduces the closed enum
that generalizes the subject to one of four predecessor-artifact kinds,
mirroring the shape of `bin._qa.exit_codes` (a small standalone module
of module-level constants + a frozenset that locks the enum so a
chain-driver / CLI caller can reason about the allowed set uniformly).

The four kinds map one-to-one to the slug-output artifacts under
`docs/plans/<slug>/` (see `docs/plans/CLAUDE.md`):

- ``recon``    → ``<slug>_recon.md``    (the historical default)
- ``qna``      → ``<slug>_qna.md``
- ``research`` → ``<slug>_research.md``
- ``plan``     → ``<slug>_plan.md``     (the rendered MD twin, NOT the
                                          ``_plan.json`` substrate)

The path-resolution shape is uniform: ``<slug>_<subject>.md``. The
``--subject`` flag defaults to ``recon`` so a no-flag invocation resolves
the exact recon path the CLI resolved before this enum existed
(backward-compat is load-bearing).

`Subject` is a plain ``str`` alias rather than an ``enum.Enum`` so the
value can be used directly as the filename stem
(``f"{slug}_{subject}.md"``) and threaded through argparse ``choices=...``
without an ``.value`` unwrap — exactly as ``exit_codes`` uses bare ``int``
constants rather than an ``IntEnum``.
"""

from __future__ import annotations

from typing import Final

# Subject is a str alias: each value below is a valid Subject. Kept as a
# bare alias (not enum.Enum) so it doubles as the filename stem and slots
# straight into argparse `choices=`.
Subject = str

SUBJECT_RECON: Final[Subject] = "recon"
SUBJECT_QNA: Final[Subject] = "qna"
SUBJECT_RESEARCH: Final[Subject] = "research"
SUBJECT_PLAN: Final[Subject] = "plan"

DEFAULT_SUBJECT: Final[Subject] = SUBJECT_RECON
"""The default review subject when ``--subject`` is omitted. ``recon``
preserves the CLI's historical behavior (review ``<slug>_recon.md``)."""

ALL_SUBJECTS: Final[frozenset[Subject]] = frozenset(
    {
        SUBJECT_RECON,
        SUBJECT_QNA,
        SUBJECT_RESEARCH,
        SUBJECT_PLAN,
    }
)
"""Frozen, closed set of every valid review subject. Used to lock the
argparse ``choices=`` list and to validate downstream lookups (e.g. the
per-subject rubric block table introduced in a later task). New subjects
require an operator-side code edit here + a corresponding test."""

# Stable, deterministic ordering for argparse `choices=` and for any
# caller that needs to iterate subjects in a fixed order. `recon` first
# so the help text leads with the default.
SUBJECT_CHOICES: Final[tuple[Subject, ...]] = (
    SUBJECT_RECON,
    SUBJECT_QNA,
    SUBJECT_RESEARCH,
    SUBJECT_PLAN,
)
"""Deterministically-ordered tuple of every valid subject (``recon``
first). ``frozenset(SUBJECT_CHOICES) == ALL_SUBJECTS`` — the tuple gives
a stable order for ``argparse`` help text + path-resolution iteration;
the frozenset gives membership-test semantics."""


def subject_artifact_name(slug: str, subject: Subject) -> str:
    """Return the predecessor-artifact filename for ``subject``.

    The path shape is uniform: ``<slug>_<subject>.md``. For ``plan`` this
    resolves to ``<slug>_plan.md`` (the rendered MD twin) and NOT
    ``<slug>_plan.json`` — the qa reviewer reviews prose, not the schema
    substrate.

    Raises
    ------
    ValueError
        If ``subject`` is not in :data:`ALL_SUBJECTS`.
    """
    if subject not in ALL_SUBJECTS:
        raise ValueError(
            f"unknown qa subject {subject!r}; "
            f"must be one of {sorted(ALL_SUBJECTS)}"
        )
    return f"{slug}_{subject}.md"


__all__ = [
    "ALL_SUBJECTS",
    "DEFAULT_SUBJECT",
    "SUBJECT_CHOICES",
    "SUBJECT_PLAN",
    "SUBJECT_QNA",
    "SUBJECT_RECON",
    "SUBJECT_RESEARCH",
    "Subject",
    "subject_artifact_name",
]
