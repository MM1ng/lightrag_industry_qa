# Phase 10B-3J：Runtime Lineage、Citation Precision 与 Post-Retrieval Coverage Recovery

## 结论

本阶段代码集成和自动化测试通过，但 Development 真实运行门禁被暂存运行态阻塞，不能标记质量通过，不能运行 Validation，不能激活 Candidate，也不允许进入 Phase 10C。

阻塞是可复现的运行事实：在当前提交临时启动的 API 上，以固定 Candidate `5bca792c08fcf2f7b08cbaed09b6d525` 执行 36 道 Development 查询，36/36 返回 HTTP 409、错误码 `generation_invalid_state`。因此没有生成可用的答案质量指标，也没有把旧 R2 指标冒充为 J 阶段结果。原始逐题记录见 `evaluation/phase10b3j/development_runtime_attempt.jsonl`，汇总见 `evaluation/phase10b3j/development_runtime_summary.json`。

## 固定实验边界

| 项目 | 值 |
|---|---|
| Candidate | `5bca792c08fcf2f7b08cbaed09b6d525` |
| Generation name | `g10b3c20260803` |
| Model | `qwen-plus-2025-07-28` |
| mode / top_k / chunk_top_k | `naive / 12 / 20` |
| rerank / cache / fallback | `false / false / false` |
| embedding | `text-embedding-v4 / 1024` |
| supplemental | `QA_SUPPLEMENTAL_RETRIEVAL_ENABLED=false` |
| Development | attempted once, 36 questions |
| Validation / Holdout | not run |

## 多 Agent 交付对账

所有 Agent 均从 base `a8c4bd4d2194745bb1b1b9464704ab200bbb0173` 的独立 worktree 工作，主分支实际合并提交如下：

| Agent | 分支/worktree | commit | 交付与验证 |
|---|---|---|---|
| A | `codex/phase10b3j-agent-a` / `.worktrees/phase10b3j-agent-a` | `b5d6ae8105c8ba8caf1c096009369b098c965756` | runtime lineage 字段、指标单位对账；focused tests 4 passed |
| B | `codex/phase10b3j-agent-b` / `.worktrees/phase10b3j-agent-b` | `a8c202487d8eefd764944a0761adade7a21782c5` | 确定性 Claim citation pruning；7 passed |
| C | `codex/phase10b3j-agent-c` / `.worktrees/phase10b3j-agent-c` | `d46c0b2f1d623a12b517f4d32c5224313a8fccb0` | 有界 post-retrieval recovery；12 passed |
| D | `codex/phase10b3j-agent-d` / `.worktrees/phase10b3j-agent-d` | `2b44200` | 主查询接线、API Claim 映射、runner 校验；63 passed |

主分支 HEAD 将在本报告提交后写入交付清单；本报告不预写不可验证的提交号。D 的主查询接线只修改 `lightrag_service.py` 与 `api.py`，A/B/C 未并行修改这两个文件。

## 实现证据

### Runtime lineage

`RetrievalExecutionTrace` 增加并由同一次真实查询链填充：

- `provider_evidence_ids`、primary/completed/supplemental IDs；
- `provider_context_order`、context SHA-256、数量、截断标记、token estimate；
- `backend_second_query_called`；
- `coverage_before`、`coverage_after_parent_adjacent`、selected/generated/grounding-retained coverage；
- grounding answer-point identity、support candidates、retained/removed、removal reason 和 false-negative diagnostics。

普通 QueryResponse 不暴露这些内部审计字段；只有 admin retrieval trace 序列化路径读取。`provider_supplemental_evidence_ids=[]` 且第二次查询标志为 false，符合 H3 关闭约束。

### Citation pruning

`src/industrial_rag/claim_citation_pruning.py` 对每个 AnswerPoint 仅保留真实 evidence 映射到的 citation：同一 chunk 去重、unknown evidence 和跨 generation 映射拒绝、共享 evidence 可被多个 Claim 稳定复用；KB-scoped 路径不再无条件把全部 citations 回退到每个 Claim。Legacy `/v1/query` 保留兼容回退。

### Bounded recovery

`src/industrial_rag/post_retrieval_recovery.py` 是纯确定性、有界、同 KB/Generation 的恢复函数：最多使用已有候选，不发起第二次检索，不扫描 PDF，不放宽全局 grounding 阈值；负样本和跨 generation 候选不会被恢复。

## 指标单位对账（冻结 R2 基线）

旧 R2 的唯一有效 Development 基线仍为：36 questions、39 expected points、21 covered、23 emitted points、198 claims。具体结果：False Rejection `3/36=8.33%`；Supporting Citation Recall `21/23=91.30%`；Citation Precision `8.166667/23=35.51%`；Overcitation `20/23=86.96%`；Claim Semantic Support `21/23=91.30%`；Question Unsupported Answer `12/33=36.36%`；Question Citation Accuracy `31/33=93.94%`；Expected Answer-point Coverage `21/39=53.85%`；Identity Resolution `198/198=100%`；Trace Completeness `36/36=100%`。

Funnel：exact 1、overcitation 20、emitted without supporting citation 1、generation omitted 6、generation refusal 3、grounding false negative 5、recalled not selected 3、retrieval missing 0。

`evaluation/phase10b3j/metric_unit_reconciliation.json` 明确记录：用户提供的 `39 = 21 + 1 + 6 + 3 + 5 + 3 - 3` 右侧为 36，数学上无效；不可把 `recalled_not_selected` 诊断子集再次从互斥桶中扣除。可审计的互斥分区是 `39 = 21 + 1 + 6 + 3 + 5 + 3`，并保留 `recalled_not_selected=3` 作为归因诊断，未篡改任何结果。

## Development 运行门禁

真实执行结果（固定配置、只读 Development）：

```json
{
  "question_count": 36,
  "completed_count": 0,
  "http_status_counts": {"409": 36},
  "error_code_counts": {"generation_invalid_state": 36},
  "validation_run": false,
  "holdout_run": false,
  "candidate_activated": false,
  "gate": "blocked"
}
```

这不是质量失败率，也不是模型结论；是固定 Candidate 在当前 staging runtime 不可用的前置运行阻塞。修复运行态后必须重新执行完整 36 个普通 POST，并从每个 request_id 读取 admin trace；不得只补 GET 或复用本次失败记录。

## 工程验证

- focused Phase 10A/10B-3J tests：`15 passed`；
- 全量 pytest：`750 passed, 12 skipped, 1 warning`；
- Ruff：`All checks passed`；
- 未运行 Validation 或 Holdout；
- 未修改 Golden Set、Chunking、Embedding、TopK、Rerank、Prompt 或全局 Grounding 阈值；
- 未激活 Candidate、创建 Tag、打包 RC、部署生产、进入 Phase 10C；
- Secret 未写入代码、Trace、公开 API 或报告。

## 状态字段

`phase10b3j_approved=false`（Development 前置运行门禁阻塞）；`phase10c_allowed=false`；`candidate_activation_performed=false`；`production_deployment_performed=false`；`validation_run=false`；`holdout_run=false`。

下一步唯一允许动作是恢复当前暂存运行态中的固定 Candidate 可读性，然后重新执行 Development 36 题并重新生成全部 trace/指标；在 Development 门禁通过前停止。
