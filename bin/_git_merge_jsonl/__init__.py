"""bin/_git_merge_jsonl — Modules for `bin/git-merge-jsonl`.

Per implplan §C.impl.9. Custom git merge driver for
`_orchestrator_log.jsonl`. Invoked by git when registered in
`.gitattributes` (per `bin/install-merge-drivers`).

The merge algorithm produces a sorted union of "new since ancestor"
rows, dedupes identical rows, and writes the result via atomic
write-temp + rename. Properties (verified by tests):
- commutative: merge(A,B) == merge(B,A) byte-identical
- associative: merge(merge(A,B),C) == merge(A,merge(B,C))
- idempotent: merge(A,A) == A
- no-loss: every input row in output
- sorted by `ts` ascending
"""
