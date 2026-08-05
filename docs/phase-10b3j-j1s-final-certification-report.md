# Phase 10B-3J-J1S-R final certification

The only J1S Development run (`phase10b3j-j1s-development-20260805-1`) was
frozen and reconciled from immutable SQLite Trace records. No model,
retrieval, Validation, or Holdout request was made during reconciliation.

The historical Trace version remains `phase10a-retrieval-trace-v1`. It was not
renamed. The Admin-only projection now preserves J1S audit fields, and the
future runner reads persisted Trace by `trace_id`, but neither change alters
the historical records.

Result: J1S is rejected. The final 36-question result has 35 direct structured
outputs and one D014 deterministic fallback. Quality gates fail for supporting
citation recall, expected coverage, citation precision, question citation
accuracy, unsupported-answer non-regression, and conservatively certifiable
claim semantic support. The saved ordinary responses also expose
`generation_id`, which conflicts with the approved Admin-only boundary and
causes strict response-to-Trace linkage to fail.

J1S remains disabled and the system remains on J0. J2, Validation, Candidate
activation, and Phase 10C are not allowed. The complete frozen evidence,
metrics, linkage matrix, D014 audit, and governance fixes are in
`evaluation/phase10b3j_j1s/reconciliation/`.

The files outside `reconciliation/` preserve the original run and intermediate
runner observations. Only the reconciliation directory is the final J1S-R
certification source of truth; no results from different runs were combined.

Verification: J1S-R focused tests pass (22 tests) and `ruff check .` passes.
The full suite result is 790 passed, 12 skipped, 5 failed, and one warning;
the five failures are pre-existing unavailable parser/PDF/RC-package assets,
not J1S-R behavior.
