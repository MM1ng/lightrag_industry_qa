# Phase 10B-3 KB-scoped Product Query, Evidence Completion & Evidence Panel

## 状态

实现已停止在 Phase 10B-3，等待人工验收。由于本阶段质量门禁使用的是 Phase 10B-2 冻结的 36 题 development + 16 题 validation 基线，且未重新执行完整端到端评测，`phase10b3_approved=false`。未进入 Phase 10C。

## 版本与提交

- Git HEAD：`2486239`
- 任务提交：
  - `28311d2 feat(ui): switch chat to knowledge-base scoped queries`
  - `c0c4bb2 fix(api): preserve partial answer status end to end`
  - `d24b201 fix(grounding): map claims to exact evidence citations`
  - `8183d17 feat(retrieval): add bounded evidence completion`
  - `2d516ce feat(ui): render user-facing evidence panel`
- 设计提交：`7b92856 docs(phase10b3): design kb-scoped evidence experience`

## 已实现

- Streamlit 普通问答使用 `/v1/knowledge-bases/{kb_id}/query`，服务身份只读；Legacy `/v1/query` 保留兼容。
- 服务端返回的 `partial_answer`、`safety_blocked` 状态贯穿 API Client、Chat State、P3 adapter 与 UI 状态栏。
- Claim 使用 `AnswerPoint.evidence_ids` 精确映射 citation；未知 Evidence ID 不回退为全部 citations。Legacy 无 grounding 结果保留兼容行为。
- 新增公共 `EvidenceResponse`，excerpt 限制 600 字符；UI 展示文档、页码、Chunk、来源类型、支撑答案点，不显示 score 或内部 Trace。
- 新增确定性、有界的同文档同 Generation Parent/Adjacent/Table registry completion 原语，最大补充数量为 2；不扫描 PDF、不改 Embedding/Qdrant。
- Streamlit 增加 KB 选择、Generation 摘要、Claim 面板、Evidence 面板和安全文本高亮模型，并建立 pages/components/utils 边界。

## 验证

- Ruff：`All checks passed!`
- 针对性 pytest：`87 passed, 1 warning`。
- Development/Validation 产物：`evaluation/phase10b3/development_results.jsonl`、`validation_results.jsonl`，来源和是否重新执行见 `evaluation_provenance.json`。
- Holdout：未重新运行；历史 Holdout 逐题内容未用于调参。
- Secret：沿用 Phase 10B-2 `confirmed_secret_count=0` 结果；本阶段未把密钥写入新增产物。

## 冻结基线指标（52题，继承而非本次重新执行）

- False Rejection Rate：20.00%（10/50，未达到 ≤12%）。
- Unsupported Answer Rate：2.50%（1/40，通过 ≤5%）。
- Question-level Citation Accuracy：97.50%（39/40，通过 ≥95%）。
- Answer-point Evidence Coverage：70.92%（261/368，未达到目标）。
- Multi-evidence Complete Coverage：85.00%（34/40，未达到目标）。
- Negative Rejection Rate：100%（2/2）。
- Citation Trace Completeness：100%（52/52）。

因此不能声称本阶段已达到全部质量门禁；应在可运行真实 KB 环境中重新执行完整普通 POST + 证据响应验收后再决定是否批准。

## 结论字段

```json
{
  "phase10b3_approved": false,
  "phase10c_allowed": false,
  "production_deployment_performed": false,
  "holdout_rerun": false,
  "tag_created": false,
  "rc_repackaged": false
}
```

## 已知限制

- 当前提交提供了 completion registry 原语和 Trace 字段，但尚未在真实 Generation Context Registry 上完成 52 题重新运行，因此 Parent/Adjacent/Table 的端到端改善不能虚报。
- UI 结构化验收结果已生成，但未在本轮启动真实 Streamlit 服务并采集截图。
- 仍需后续人工验收和真实运行数据，才可判断 Phase 10B-3 是否通过；本阶段不自动进入 Phase 10C。
