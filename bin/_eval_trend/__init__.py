"""Calibration trend surfacer (`bin/eval-trend`, §J.impl.10).

ANCHOR — operator-as-terminator (§4a.2). This module emits DIAGNOSTIC
metrics. The operator reads threshold breaches in morning-review and
decides whether to revise the scorer's prompt, accept drift, or
escalate. There is no meta-scorer above the operator's labels.
"""
