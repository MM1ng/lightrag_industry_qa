# Phase 10B-3J-J1S: Single-pass Structured Citation Generation

## Status and provenance

- Parent commit: `6c5f8c581b93140430dda234cd051442555463aa`
- Specification content commit: `d55027610bdcec8b469e21f1ba8aaed4ba004db8`
- Remote branch: `codex/knowledge-qa-platform-design`
- Remote head before this specification: `dc5c46440fa29489a3056f038e01bab733d6e288`
- Candidate generation: `5bca792c08fcf2f7b08cbaed09b6d525`
- Old Active generation: `a2d1c77ce08b414495e9d845cc42f799`

This user-approved specification replaces the failed J1 runtime semantic
attribution route. It authorizes a single existing Qwen call with a
request-scoped source registry and deterministic ID validation. It does not
authorize Validation, Holdout access, Candidate activation, Phase 10C,
Supplemental Retrieval, retrieval reconfiguration, Golden runtime input,
chunking, embeddings, or Candidate mutation.

## Feature and disabled controls

`QA_STRUCTURED_CITATION_OUTPUT_ENABLED=false` is the only new experiment
control. It is fail-closed and appears in Settings, `/version`, evaluation
configuration, config SHA, and the retrieval trace. The following controls are
false during J1S: `QA_CLAIM_CITATION_PRUNING_ENABLED`,
`QA_CLAIM_EVIDENCE_ATTRIBUTION_ENABLED`, `QA_CITATION_REBINDING_ENABLED`,
`QA_MINIMAL_CITATION_SELECTION_ENABLED`,
`QA_UNSUPPORTED_CLAIM_ENFORCEMENT_ENABLED`,
`QA_GROUNDING_FALSE_NEGATIVE_RECOVERY_ENABLED`,
`QA_COVERAGE_AWARE_SELECTION_ENABLED`, `QA_PARTIAL_GENERATION_ENABLED`, and
`QA_SUPPLEMENTAL_RETRIEVAL_ENABLED`.

When the new flag is false, the service must not build a source JSON prompt,
enable JSON mode, run a structured validator, or alter the existing J0 prompt,
answer, citations, trace, or public response semantics.

## Immutable request registries

After the existing selection path, the service constructs two immutable,
request-local registries in provider-context order.

`S1`, `S2`, ... map to only real child evidence. Each entry contains the
source ID, evidence ID, real child chunk ID, document name, page, fixed
Candidate generation ID, child content, and SHA-256 of exactly the content
sent to the provider. A Parent/context entry must be expanded into real child
Sources before it enters this registry. If no direct public Child exists, the
source is invalid and cannot be cited. Parent text is never supplied as the
body of a Source that is later cited as a Child.

`R1`, `R2`, ... is the immutable request Requirement Registry. Requirement
IDs are internal-only and may only appear in the structured provider output and
trace. A provider may reference only an ID in this registry, without repeats.

## One-call provider contract

With the flag true, the existing `backend.generate` call is made exactly once
with Qwen JSON mode, `response_format={"type":"json_object"}`. Its prompt
contains the exact instruction: `请只输出合法JSON，不要输出Markdown代码块或其他文字。`
It receives the request-local Sources, never database chunk IDs.

The only supported provider object is:

```json
{
  "status": "success|partial_answer|insufficient_evidence",
  "answer_points": [{"text": "string", "source_ids": ["S1"]}],
  "unresolved_requirement_ids": ["R2"]
}
```

Each point must have one or two distinct sources. A source must be directly
supporting and a point without supporting evidence must not be emitted. No
second LLM call, second retrieval, or generated repair is allowed.

## Status semantics

The validator derives final status from the validated object rather than
trusting a contradictory provider status:

| Answer points | Unresolved requirements | Required status |
| --- | --- | --- |
| empty | any | `insufficient_evidence` |
| nonempty | nonempty | `partial_answer` |
| nonempty | empty | `success` |

Therefore success never carries unresolved requirements,
insufficient-evidence never carries answer points, and partial-answer always
carries both at least one point and at least one unresolved requirement.

## Atomic deterministic validation and fallback

The local validator checks JSON parsing, root object, `answer_points` list and
objects, nonempty string `text`, no more than two distinct source IDs, Source
and Requirement Registry membership, Child identity, fixed generation, and
Parent-to-Child mapping. It does no runtime semantic entailment, ID repair, or
point-by-point deletion.

The entire query may use `fallback_to_j0_postprocessing` only when JSON has a
valid root and determinate nonempty text for every listed point, and every
failure is citation-related: unknown/duplicate/excess source, wrong generation,
unmappable Parent, or registry identity failure. The generated texts are then
sent through the existing J0 post-processing, grounding, and response path
without another generation call. Trace records the fallback.

Malformed JSON, non-object roots, missing/non-list `answer_points`, missing or
non-string text, indeterminate point structure, and all core schema damage use
`safe_failure_no_second_generation`. The public result is safe
`insufficient_evidence`; no answer is guessed from a partial string. Unknown or
duplicated Requirement IDs trigger an atomic deterministic fallback, not a
partial reconstruction.

## Citation and trace boundaries

Successful Source IDs convert through the registry to existing AnswerPoints and
public child Citations. Public markers `[1]`, `[2]` follow first answer
appearance, not Source ordering; reuse of a Source reuses its public number.
Existing citation payloads display document, page, chunk ID, and source text.
Generation ID is Admin-only.

Internal trace records: structured flag, JSON-mode enabled, source Registry
count/SHA, Requirement Registry count/SHA, provider raw-response SHA, parsed
output SHA, output validity, fallback flag/mode/reason,
`backend_generate_call_count`, and `backend_second_query_called`. Normal
responses expose none of Registry data, generation IDs, config SHA, raw model
content, or failure diagnostics.

## TDD and gated execution

Before production code, tests must fail for status invariants; Source and Child
identity; valid conversion; citation-related and Requirement fallbacks; core
schema safe failure; first-use public numbering/reuse; exactly one generation;
no second retrieval; flag-off J0 identity; public trace secrecy; Active
preservation; and a runner refusal to run 36 Development questions before
preflight passes.

The execution order is J1S-0 flag-off non-regression, J1S-1 three-question
preflight, J1S-2 historical citation failure subsets, then exactly one
36-question Development run. Preflight must report one generate call, JSON
mode, source registry validity, Candidate correctness, no second query, and
unchanged Active pointer for all three questions. Validation, Holdout, Candidate
activation, and Phase 10C remain prohibited until an accepted Development gate.
