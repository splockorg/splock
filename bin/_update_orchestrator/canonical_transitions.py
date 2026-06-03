"""7-status transition validation + `done → wip` operator gate (implplan §E.impl.5).

The 7-status enum (per plan §1.E) is the canonical state machine used by
every `_state.json` task entry. This module exposes:

- `validate_transition(from_status, to_status, override_active)`:
  returns a `TransitionVerdict` indicating whether the transition is
  permitted and (when refused) which exit-code family applies.

The only currently-gated transition is `done → wip`: per plan §E.5 it
requires `OPERATOR_OVERRIDE_STATE=1`. Other transitions are not refused
by this module (they may still be refused upstream by §A's chain
driver or by phase-boundary gates; `--from-develop-plan` is in scope
here).
"""

from __future__ import annotations

import dataclasses
from typing import Literal

SEVEN_STATUS = frozenset(
    {"ready", "wip", "done", "deferred", "blocked", "cancelled", "unknown"}
)

# Verdict kinds — closed enum
VERDICT_ALLOW = "allow"
VERDICT_REFUSE_DONE_WIP_NO_OVERRIDE = "refuse_done_wip_no_override"
VERDICT_REFUSE_UNKNOWN_STATUS = "refuse_unknown_status"


@dataclasses.dataclass(frozen=True)
class TransitionVerdict:
    kind: str  # one of VERDICT_*
    from_status: str
    to_status: str
    override_active: bool

    @property
    def allowed(self) -> bool:
        return self.kind == VERDICT_ALLOW


def validate_transition(
    from_status: str,
    to_status: str,
    override_active: bool,
) -> TransitionVerdict:
    """Validate a 7-status transition.

    Parameters
    ----------
    from_status, to_status : str
        Must both be in the 7-status enum, else `refuse_unknown_status`.
    override_active : bool
        Whether `OPERATOR_OVERRIDE_STATE=1` is set in the calling env.

    Returns
    -------
    TransitionVerdict
        `allowed == True` if the transition is permitted; otherwise the
        `kind` discriminates the refusal reason.
    """
    if from_status not in SEVEN_STATUS or to_status not in SEVEN_STATUS:
        return TransitionVerdict(
            kind=VERDICT_REFUSE_UNKNOWN_STATUS,
            from_status=from_status,
            to_status=to_status,
            override_active=override_active,
        )
    # `done → wip` is gated.
    if from_status == "done" and to_status == "wip" and not override_active:
        return TransitionVerdict(
            kind=VERDICT_REFUSE_DONE_WIP_NO_OVERRIDE,
            from_status=from_status,
            to_status=to_status,
            override_active=override_active,
        )
    return TransitionVerdict(
        kind=VERDICT_ALLOW,
        from_status=from_status,
        to_status=to_status,
        override_active=override_active,
    )
