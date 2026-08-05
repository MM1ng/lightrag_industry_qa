# Phase 10B-3J-J1S: Single-pass Structured Citation Generation

## Status and authority

This specification is approved by the user on 2026-08-05. It supersedes the
failed J1 runtime claim--evidence pruning path for future J1 work only. The
failed J1 commit remains reverted and the J0 baseline remains the default.

## Scope and invariants

- One existing Qwen generation call produces answer points and request-local
  source IDs together. No checker model, second generation, second retrieval,
  Supplemental Retrieval, Golden runtime input, Validation, or Holdout access
  is permitted.
- Candidate is fixed at `5bca792c08fcf2f7b08cbaed09b6d525`; its data,
  embeddings, chunks, collection, TopK and Active pointer are immutable.
- `QA_STRUCTURED_CITATION_OUTPUT_ENABLED` is fail-closed and defaults to
  `false`. All existing J1/J2/J3/J4 and legacy attribution/pruning flags stay
  disabled for this experiment.
- When disabled, the existing J0 prompt, provider mode, answer, citations and
  public response semantics remain unchanged.

## Request-local source registry

After the existing retrieval and selection phases, create an immutable registry
in provider-context order. Its public-to-internal mapping is stable for one
request only:

```text
S1 -> E1 -> real child chunk
S2 -> E2 -> real child chunk
```

Each source holds `source_id`, `evidence_id`, child `chunk_id`, document name,
page, Candidate generation ID, content, and a content SHA-256. The provider is
shown only `S<n>` plus source metadata/content; it must never emit a database
chunk ID. Registry identity and count are stored only in the internal trace.

Parent/context evidence is converted to a real child citation before registry
construction. If no real child mapping exists, it is not a valid output source.

## Single-pass provider contract

With the flag enabled, the existing generation call uses Qwen JSON mode
(`response_format={"type":"json_object"}`) and one explicit prompt section:

> 请只输出合法JSON，不要输出Markdown代码块或其他文字。

The response schema is:

```json
{
  "status": "success|partial_answer|insufficient_evidence",
  "answer_points": [{"text": "string", "source_ids": ["S1"]}],
  "unresolved_requirement_ids": ["R2"]
}
```

Each nonempty point has one or two source IDs. A source is only added when it
directly supports the point; insufficient points are omitted. At least one
supported point yields `partial_answer`; no supported point yields
`insufficient_evidence`.

## Deterministic validation and rendering

Pydantic validates the object and the local validator verifies every point in
one atomic query decision: parseability, schema, nonempty text, 1--2 distinct
source IDs, registry membership, Candidate generation, and a real child
mapping. The validator never performs semantic entailment or repairs IDs.

On success, source IDs become existing `AnswerPoint.evidence_ids` and public
`Citation` records. The answer is rendered with stable local markers such as
`[1]`; the existing citation payload remains the source of document/page/chunk
details. Generation ID and registry data remain Admin/trace-only.

## Two-level fallback

1. If JSON parses but a citation/schema constraint fails, preserve the
   generated point text and route it through the existing J0 post-processing,
   grounding, and response construction path. Mark
   `structured_citation_fallback=true` and
   `fallback_mode=fallback_to_j0_postprocessing`. This is not regeneration.
2. If JSON cannot be parsed or a core object is absent, return a safe
   `insufficient_evidence` response. Mark
   `structured_citation_fallback=true`,
   `fallback_mode=safe_failure_no_second_generation`, and a non-secret failure
   reason plus original response SHA. Never infer text from malformed output.

In both branches, `backend.generate_call_count` remains exactly one. Invalid
individual source IDs trigger the whole-query fallback; the implementation does
not delete selected points and reassemble the rest.

## Trace, configuration, and public boundary

The flag is exposed in Settings, sanitized `/version`, evaluation config,
config SHA, and retrieval trace. The trace records JSON-mode enabled state,
registry SHA/count, validity, fallback flag/mode/reason, and generation-call
count. Normal query responses do not expose these fields or generation IDs.

## TDD and experiment sequence

Tests are written and observed failing before code for: disabled J0 identity,
stable source numbering, child mapping, one generation call, legal conversion,
invalid source/wrong generation/too-many sources whole-query fallback,
unparseable safe failure, no internal public fields, Active preservation, and
no second retrieval. The implementation also has runner guards preventing a
36-question Development run unless J1S-0, J1S-1 (three preflight questions),
and J1S-2 failure subsets all pass.

Only after the three-question preflight passes may one complete 36-question
Development run occur. It must meet the user-approved J1S gates: recall at
least 89.66%, coverage at least 66.67%, precision at least 80%, question
citation accuracy at least 95%, overcitation at most 20%, false rejection at
most 11.11%, JSON validity at least 95%, fallback at most 10%, and all stated
safety/trace/secret gates. Validation and Candidate activation remain forbidden
until an accepted Development result authorizes them.
