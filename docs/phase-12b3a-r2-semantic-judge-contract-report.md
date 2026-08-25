# Phase 12B-3A-R2 Semantic Judge Output Contract Stabilization

## Status: OUTPUT_CONTRACT_FAIL

R2 只改变 Judge 输出 serialization contract，未改变 Semantic Support 定义、Runtime candidate matrix、模型、检索或线上链路。

## Protocol Gate

- LLM calls：33
- valid batches：12
- invalid batches：21
- expected pairs：566
- valid judged pairs：162
- valid batch rate：0.3636
- valid pair coverage：0.2862
- gate：`False`

Provider structured output：not used; OpenAI SDK 2.46.0 exposes a generic response_format parameter, but the current Qwen-compatible project call has no existing provider-validated JSON Schema path; R2 uses strict JSON prompt plus deterministic validation

Protocol Gate 未通过，未计算 Semantic Citation Quality 指标。

## R1 Audit Boundary

R1 未保存 provider 原始响应；R1 的 26 个 invalid batch 只能依据已记录的 deterministic parser error 审计，原始响应相关字段保持 null。

## Non-target Verification

- 未执行 Retrieval、Rerank、Context assembly 或 Generation。
- 未接入 Runtime API。
- 未读取 Validation、Holdout 或 Golden label 作为 Judge 输入。
- R2 每个 batch 只调用一次，不执行 retry。
