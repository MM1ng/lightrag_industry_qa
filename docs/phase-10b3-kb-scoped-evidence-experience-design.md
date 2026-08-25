# Phase 10B-3 KB-scoped Product Query, Evidence Completion & User-facing Evidence Panel

## Scope

This phase changes the product query path from legacy `/v1/query` to KB-scoped
`/v1/knowledge-bases/{kb_id}/query`, preserves `partial_answer`, exposes a
backward-compatible public evidence registry, and adds bounded same-generation
evidence completion. Holdout is not rerun.

## Contracts

The API keeps all existing QueryResponse fields and adds `evidence` with bounded
excerpts. ClaimResponse gains `evidence_ids`; CitationResponse gains optional
evidence/document/generation fields. Evidence IDs are server-created per
request and map only to selected or bounded-completion chunks in the active
Generation. Unknown IDs are rejected and never become public citations.

## Completion policy

Completion is deterministic and bounded: at most two additional contexts, at
most one previous and one next chunk per initial child, same document and
Generation only, no PDF scans and no embedding/Qdrant mutation. Parent,
adjacent and table-header context remains context-only unless it directly
supports an answer point. The policy records source type, context role,
completion reason and coverage status in the admin trace and public evidence
card.

## UI architecture

The 735-line Streamlit entrypoint is split into API/state adapters, chat page,
knowledge-base selector, answer/claim/status components and evidence cards.
The entrypoint performs configuration and page assembly only. Service keys are
read server-side; ordinary chat uses SERVICE_API_KEY.

## Acceptance boundary

Development and validation only. The phase must prove KB isolation, exact
claim-citation mapping, partial-answer preservation, bounded completion,
evidence-panel completeness, 100% trace completeness, no fabricated or
wrong-generation citations, and no secret leakage. If any gate fails, the
report records the failure and `phase10c_allowed=false`.
