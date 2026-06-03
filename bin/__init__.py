"""Top-level package marker for bin/.

Per cross-cutting conventions (implplan §B.impl + §C.impl scaffolding): the
`bin/` subdirectories that hold Python implementation modules (`bin/_*/`)
are imported as packages (`bin._jsonl_log`, `bin._render_log`, etc.) by
their POSIX shell wrappers. Empty package marker; no module-level code.
"""
