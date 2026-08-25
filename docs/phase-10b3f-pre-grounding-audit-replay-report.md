# Phase 10B-3F：Pre-Grounding Audit Capture & Replay Enablement

## 结论

Phase 10B-3F 审计采集和 Replay 门禁均通过，已自动恢复 Phase 10B-3E 的实验顺序。Candidate 仍未激活，未进入 Phase 10C。

- candidate_generation_id：`5bca792c08fcf2f7b08cbaed09b6d525`
- candidate_generation_name：`g10b3c20260803`
- old_active_generation_id：`a2d1c77ce08b414495e9d845cc42f799`
- code_under_test_commit：实施提交后记录
- report_commit：本阶段提交记录
- final_delivery_commit：见最终 Git HEAD

## 数据链路审计

受控检查了一个正常成功案例（S001）、一个 Grounding 类误拒答候选（S006）和一个负例拒答（N001）。Phase 10B-3A 的 Trace 中已有 `answer_plan`，Admin schema 能返回该字段；真正丢失的是 `backend.generate` 返回值到 Trace/评测采集之间的 `pre_grounding_answer`，因此旧结果无法证明模型原始输出是拒答还是被 Grounding 删除。

新增 `GroundingAudit`，仅在显式 `QA_GROUNDING_AUDIT_ENABLED=true` 时挂入新 Trace，普通 QueryResponse、EvidenceResponse、Streamlit 和普通日志不包含原始答案。审计字段包含截断/脱敏标记、输入分句、Point decisions、removed/retained points、输出状态和 Replay eligibility。

Evidence 文本不复制进 Trace；Replay 从 Candidate Context Registry 读取，并记录 `content_sha256` 身份校验。Generation DB ID 与 Registry generation name 的映射已在完整性检查中显式验证。

## 52 题审计采集

- development：36
- validation：16
- total：52
- 完成：52/52
- 新 Trace `phase10b3f-grounding-audit-v1`：52/52
- generation_invoked 缺失原始答案：0
- Grounding rejection 缺少 input_fragments：0
- Evidence identity unresolved：0
- wrong-generation：0
- Context Registry SHA mismatch：0
- raw answer public exposure：0
- Holdout used：false
- confirmed_secret_count：0

10 道旧 False Rejection 的新分类：

- `generation_refusal`：9
- `grounding_rejection`：1（S006）
- `evidence_gate_refusal`：0
- `selection_failure`：0
- `runtime_failure`：0

这证明旧的“9 道 Grounding 误判”分类不成立，只有 S006 进入 Grounding Replay。

## Replay

- replayable false rejection：1
- recovered false rejection：1
- replayable total：41
- Context Registry mismatch：0
- Replay final_metrics_valid：true
- Phase 10B-3E eligible：true

E1 Replay 对 S006 将原始答案恢复为 `partial_answer`，保留两个有证据支持的答案点，删除两个无支持元话语点；没有使用 Golden Set 生成答案，没有新模型调用。9 道 generation_refusal 不进入 Grounding E1。

## 阶段门禁

```json
{
  "phase10b3f_approved": true,
  "phase10b3e_approved": false,
  "phase10b3a_approved": false,
  "phase10c_allowed": false,
  "candidate_activated": false,
  "holdout_used": false,
  "production_deployment_performed": false
}
```

## 产物

- `evaluation/phase10b3f/grounding_data_path_audit.json`
- `evaluation/phase10b3f/grounding_data_path_cases.jsonl`
- `evaluation/phase10b3f/development_audit_capture.jsonl`
- `evaluation/phase10b3f/validation_audit_capture.jsonl`
- `evaluation/phase10b3f/audit_capture_summary.json`
- `evaluation/phase10b3f/replay_input_integrity.json`
- `evaluation/phase10b3f/replay_results.json`
- `evaluation/phase10b3f/secret_scan.json`

下一步按既定顺序执行 E1、E2、E3、E4；每个实验保持变量隔离，最终组合冻结后才允许重新执行完整 52 题质量评测。
