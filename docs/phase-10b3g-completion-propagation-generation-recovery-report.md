# Phase 10B-3G：Completion Propagation & Generation Refusal Recovery

## 结论

Phase 10B-3G 完成了 Completion Lineage 审计、条件触发接线和一次冻结 Candidate 的 52 题真实评测。补全传播链已从 Registry → Generation Context → Grounding → Claim/Citation 接通；实际完成证据的 Claim 贡献率由此前 0% 提升为 10/10（100%）。质量门禁仍未全部通过，Candidate 不激活，不进入 Phase 10C。

## 可复现版本链

| 字段 | 值 |
|---|---|
| coordination_base_commit | `9fb882a` |
| agent_a_commit | `3db7792` |
| agent_b_commit | `02b0a7d` |
| agent_c_commit | `07e0e925a11d9dfc3947a9e8d168643deb730acc` |
| integration_commit | 集成提交见最终 Git HEAD |
| code_under_test_commit | 集成后的最终提交 |
| evaluation_run_commit | 集成后的最终提交 |
| report_commit | 集成后的最终提交 |
| final_delivery_commit | 集成后的最终提交 |
| candidate_generation_id | `5bca792c08fcf2f7b08cbaed09b6d525` |
| candidate_generation_name | `g10b3c20260803` |
| old_active_generation_id | `a2d1c77ce08b414495e9d845cc42f799` |

独立实验提交无法从历史完整证明时，已在 `evaluation/phase10b3g/experiment_commit_lineage.json` 标记为 `accepted=false`，没有把 E1–E4 未证明版本当作正式策略。

## Agent 结果

- Agent A：Development 36 + Validation 16，共 104 条 completion evidence；104/104 未绑定 AnswerPoint、Claim 或 Citation，保存结果中的 generation 标识不匹配，Registry/Provider 边界在旧结果中不可验证。
- Agent B：新增纯确定性 Conditional Completion Policy；Parent 先于 Adjacent，最多两条、同文档同 Generation、无递归、负例禁用 Adjacent。原查询链的 50/50 触发根因确认为无条件接线。
- Agent C：只读取 Development 36 题，实际 3 道 generation refusal（S007、S020、D005）；均有 Provider 调用和 selected evidence，但完整 Provider Context 未持久化，因此内容充分性、噪声、Prompt、解析和 Provider 根因均保守标记为 indeterminate，未伪造 9 道全量结论，也未增加二次 LLM 调用。

## 最终 52 题评测

- Development：36；Validation：16；Holdout：未读取。
- Candidate、Embedding、Chunking、Initial TopK、Rerank 和 Golden Set 未修改。
- Initial Chunk Recall@20：63/72 = 87.50%。
- Initial MRR：约 0.6994。
- Initial Page Recall@20：50/50 = 100%。
- Parent Trigger Rate：0/50 = 0%。
- Adjacent Trigger Rate：9/50 = 18%。
- Completion Contribution Rate：10/10 = 100%。
- Completion Evidence Precision：10/10 = 100%。
- Completion Wrong-document：0；Wrong-generation：0。
- Retrieval Trace Completeness：52/52 = 100%。
- False Rejection Rate：10/50 = 20%，未达 ≤12%。
- Negative Rejection Rate：2/2 = 100%。
- Unsupported Answer Rate：3/40 = 7.5%，未达 ≤5%。
- Question-level Citation Accuracy：37/40 = 92.5%，未达 ≥95%。
- Expected Answer-point Coverage：41/72 = 56.94%，未达 ≥90%。
- Claim-Citation Exact Mapping：280/280 = 100%。
- Evidence Panel Completeness：40/40 = 100%。

## 门禁

```json
{
  "final_metrics_valid": true,
  "phase10b3g_approved": false,
  "phase10b3a_approved": false,
  "candidate_activated": false,
  "phase10c_allowed": false,
  "production_deployment_performed": false,
  "confirmed_secret_count": 0,
  "holdout_used": false
}
```

## 验证

- `pytest --collect-only -q`：714 tests collected。
- `pytest -q`：702 passed，12 skipped，1 warning。
- `ruff check .`：通过。
- fresh 52 题运行：52/52 completed。

主要产物：

- `evaluation/phase10b3g/experiment_commit_lineage.json`
- `evaluation/phase10b3g/completion_lineage_audit.json`
- `evaluation/phase10b3g/completion_lineage_cases.jsonl`
- `evaluation/phase10b3g/completion_drop_summary.json`
- `evaluation/phase10b3g/generation_refusal_matrix.jsonl`
- `evaluation/phase10b3g/generation_refusal_summary.json`
- `evaluation/phase10b3g/generation_context_presence.jsonl`
- `evaluation/phase10b3g/conditional_completion_results.json`（若无可接受策略则记录空/拒绝）
- `evaluation/phase10b3g/experiment_results.json`
- `evaluation/phase10b3g/final_completion_metrics.json`

本阶段完成后立即停止，等待人工验收。未创建 Tag、未打包 RC、未部署生产、未进入 Phase 10C。
