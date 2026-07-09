"""bin/_state_divergence — Modules for `bin/state-divergence-check`.

Per implplan §C.impl.4 (Hole H.13 resolution). CLI entry is `main.py`;
log → derived state replay is `replay.py`; derived-vs-on-disk diff is
in `compare.py` (collapsed into `replay.py` here since the diff logic
is small enough not to need a separate module).
"""
