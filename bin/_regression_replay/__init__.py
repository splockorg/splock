"""Regression-case replay (`bin/regression-replay`, §J.impl.6).

v2.7 ships side-by-side diff for operator review only. The auto-grader
meta-scorer that would judge replay outputs is deferred behind marker
AGR (Auto-GRader) per §J.impl.13 #1 + §J.impl.15 #4.

OPERATOR-FOLLOWUP: AGR.1 marker mint requires operator authorization
per orchestrator §9 #6. No `auto_grader.py` module ships in v2.7.

Mint command preview (operator runs):

    bin/marker register-prefix AGR --domain "Auto-grading for bin/regression-replay" \\
        --owner "§J.impl"
    bin/marker create AGR.1 "Activate replay auto-grader" \\
        --trigger "condition:exists:_scores.jsonl AND labeled_replay_count > 100"
"""
