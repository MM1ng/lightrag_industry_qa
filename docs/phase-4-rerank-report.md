# Phase 4D-R2 报告：variable-size 冻结候选下的 qwen3-rerank 消融（R0 vs R1）

**日期**: 2026-08-01
**分支**: `codex/knowledge-qa-platform-design`
**阶段**: Phase 4D-R2（替代早期 Phase 4D-R / Phase 4D-R2 的固定 20 候选契约）

## 1. 阶段结论

- qwen3-rerank 完整 48 题 R1 离线评估**通过全部硬门禁与全部价值门禁**，因此按规则进入了阶段二（R0/R1 各 50 题完整答案）。
- 阶段二显示：检索、引用准确率/召回均提升，但**替换默认策略的最终硬门禁未通过**：
  - `Unsupported Citation Rate` 两组均 > 0（R1=0.6691，硬门禁要求 =0）；
  - **安全警告类别 Citation Accuracy 从 1.0000 退化为 0.8000**（硬门禁要求不得退化）。
- 最终决策：`evaluation_completed=true`、`rerank_enabled=false`、`replacement_approved=false`、`replacement_gates_passed=false`。
- 生产默认 `RERANK_ENABLED=false` 未修改。**Phase 5 允许（Phase 4D 门禁已解除），本阶段完成后立即停止，不自动进入 Phase 5。**

## 2. Git commit

- 基线：`b760f96`（此前提交链 `0288aa5` → `d578ebb` → `631270e` → `b760f96`）。
- 本阶段（R2）提交：见 Git 提交记录（`fix(phase4): support variable-size frozen rerank candidates`、`eval(phase4): complete qwen3 rerank comparison with negative queries`、`docs(phase4): finalize variable-size rerank evaluation`）。

## 3. 历史固定 20 候选契约问题

早期 Phase 4D-R 使用固定契约：

- 每题必须恰好 20 个输入候选；N001/N002 必须 0 候选；
- C007 被要求补齐到 20 个。

真实冻结池与之不符（C007=19 行、N001=20 行、N002=19 行），导致旧的"输入=20/输出=20、丢失=0"完整性门禁无法满足，Phase 4D-R 被错误阻塞。

历史阻塞状态保留于：

```text
Historical contract-blocked run
superseded by Phase 4D-R2 variable-size candidate contract
```

## 4. variable-size 候选契约

```json
{
  "candidate_count_contract": "variable_unique_candidates_up_to_candidate_k",
  "candidate_k": 20,
  "final_k": 12,
  "per_question_counts": {
    "default_answerable": 20,
    "C007": 19,
    "N001": 20,
    "N002": 19
  },
  "negative_questions_may_have_candidates": true,
  "effective_final_k_rule": "min(final_k, input_candidate_count)"
}
```

- 可答题：`1 <= input_count <= candidate_k`；
- 证据不足题：`0 <= input_count <= candidate_k`；
- `effective_final_k = min(final_k, input_count)`；
- 完整性 = 输出行数 == 输入行数、输出多重集合 == 输入多重集合、无丢失/池外/新增重复、original_rank/original_score/文本 hash/document/page/parent 不变；
- `candidate_preservation_rate = preserved_input_count / input_count`（输入 >0 时必须 =1.0；输入 =0 时 N/A 且不调用 Reranker）。

## 5. C007 重新判定

- 冻结池 C007 有 **19 行**；其中 `cchunk-pymupdf-v1-护手册-e05e769c5e5d-000-e05e769c5e5d`（page 24、同一文本 hash）在 original rank 2 和 5 出现两次，即 **18 个唯一 chunk_id**。该重复行是 Phase 4C 检索冻结阶段的既有数据，本阶段未修改、未补齐、未重新检索。
- qwen3-rerank 以 19 个输入行调用并返回 **19/19**：无丢失、无池外、无新增重复，19 行全部保留（输出多重集合 == 输入多重集合）。
- 重新判定：**valid variable-size rerank result**（不再是 completeness failure）。本次结果文件与缓存可直接复用；C007 在 R2 首轮为真实调用（旧响应不可恢复），后续运行全部缓存命中。

## 6. N001/N002 评估设计

- 冻结候选**保留**（N001=20、N002=19），不按黄金标签清空；
- N001/N002 **不进入** Recall@K、MRR@5、Gold Page/Evidence Recall 分母；
- 两者**进入** Rerank 与拒答评估：检查干扰候选下系统能否正确拒答；
- 只有统一 Evidence Policy 判定无支持证据时才允许确定性拒答；不因黄金标签为负样本而绕过策略。

## 7. frozen index 验证

- KB `8fce4626859d44abb70a9ae5b0372cea`；generation `g5162e7fb4208635103ff4ebb`；
- chunks=453、entities=1012、relationships=1061；documents 2/2 processed，无 processing/failed/partial；
- `index_manifest.json` 标记 `index_role=phase4_frozen_index`、`experiment_only=true`；
- 全程未重新检索、未重建索引、未调用 MinerU。

## 8. candidate pool SHA256

- 文件：`evaluation/experiments/phase4/parent_expansion/frozen_child_results.jsonl`
- SHA256：`fc731efc904d9d9dca639fecf181a01e022c162ac91b67f6432d18b7619bf6a0`（未变）
- 50 题、998 行；47 题 20 候选、C007=19、N001=20、N002=19；`candidate_pool_manifest.json` 已按新契约更新（`candidate_count_contract`、`per_question_counts`、`negative_questions_may_have_candidates`、`effective_final_k_rule`）。

## 9. qwen3-rerank 模型与预检

```json
{
  "provider": "aliyun_model_studio",
  "requested_model": "qwen3-rerank",
  "model_id": "qwen3-rerank",
  "model_identity_type": "official_mainline_model_id",
  "dated_snapshot_available": false,
  "fallback_enabled": false
}
```

- 无日期快照 ID；未伪造日期模型名；allowlist 显式只含 `qwen3-rerank`（拒绝 latest/auto/未知模型）；`actual_model_version=null`（API 未提供，不编造）。
- 真实预检（S001、20 候选、top_n=20）：HTTP 200、20/20、index 0-19、无重复/丢失/池外、score 有限、request_id 非空、无 fallback、文本未变；`preflight.json` 记录 `provider_cache_hit=true`、`reused_request_id=7dc0c3af-...`、`reused_response_hash=c6c47abf...`。
- 请求体（真实 API 验证）：`{model, input:{query,documents}, parameters:{top_n, return_documents:false}}`。
- 输入长度门禁：query <=4000、document <=4000、单请求 <=120000 token；50 题全部通过，无截断、无改写。

## 10. Provider 缓存复用

- 两层缓存：Provider Request Cache（`cache/rerank.jsonl`，gitignored）+ Evaluation Result Cache（结果文件）。
- 新键 `request_payload_hash` 只依赖真实请求语义：provider、exact model、query hash、ordered candidate IDs、ordered candidate text hashes、input_count、top_n、region、request schema version。
- 报告/评估契约/Git commit 变化不会强制重调 API；旧缓存键（含 commit/config hash）保留兼容读取。
- R2 复用：47 题 + 预检从持久化结果播种（`seeded_entries=47`）；仅 C007/N001/N002 需要新真实调用（3 次，均成功）；后续全量重跑 51/51 缓存命中、`live_api_calls=0`。
- 记录：`provider_cache_hit`、`reused_request_id`、`reused_response_hash`；缓存不含 API Key / Authorization Header。

## 11. R0 指标（48 题，frozen 顺序 top-12，canonical）

| 指标 | R0 |
|---|---|
| Recall@1 | 0.5625 |
| Recall@3 | 0.6875 |
| Recall@5 | 0.7500 |
| Recall@12 | 0.7917 |
| MRR@5 | 0.6201 |
| Gold Document Recall | 1.0000 |
| Gold Page Recall | 0.8542 |
| Gold Evidence Recall | 0.7917 |
| Evidence Precision@5 | 0.2000 |
| Evidence Precision@12 | 0.1024 |
| Top-1 Document Accuracy | 1.0000 |
| Top-5 Page Coverage | 0.7917 |

R0 与冻结期望逐项一致（误差 <=1e-6）。C007 使用 19 行中的原始前 12，未人为补候选。

## 12. R1 完整指标（48 题，qwen3-rerank 后取前 min(12, n)）

| 指标 | R0 | R1 | Δ |
|---|---|---|---|
| Recall@1 | 0.5625 | 0.7500 | +0.1875 |
| Recall@3 | 0.6875 | 0.8542 | +0.1667 |
| Recall@5 | 0.7500 | 0.8542 | +0.1042 |
| Recall@12 | 0.7917 | 0.8750 | +0.0833 |
| MRR@5 | 0.6201 | 0.7951 | +0.1750 |
| Gold Document Recall | 1.0000 | 1.0000 | 0.0000 |
| Gold Page Recall | 0.8542 | 0.9375 | +0.0833 |
| Gold Evidence Recall | 0.7917 | 0.8750 | +0.0833 |
| Evidence Precision@5 | 0.2000 | 0.2333 | +0.0333 |
| Evidence Precision@12 | 0.1024 | 0.1215 | +0.0191 |
| Top-1 Document Accuracy | 1.0000 | 1.0000 | 0.0000 |
| Top-5 Page Coverage | 0.7917 | 0.9167 | +0.1250 |

48/48 完整（含 C007 19 候选题）；N001/N002 不在分母。

## 13. Rank Movement（完整 48 题）

- mean absolute rank movement = 6.008；median = 5；P95 = 14
- relevant promoted = 34；relevant demoted = 14
- irrelevant promoted = 419；irrelevant demoted = 412
- top-1 changed = 21；top-3 membership changed = 48；top-5 membership changed = 48；top-12 membership changed = 48
- 逐题（gold evidence 是否进入 top-12）：improved = 4（S016、D016、D018、C002）、regressed = 0、unchanged = 44
- 人工重点核查：
  - 参数数值证据未被降级（参数类无退化，见 §19）；
  - 安全警告证据在 C 系列中有一个问题发生退化（见 §19/§22）；
  - 故障原因/措施证据整体提升（故障诊断 Citation Accuracy 0.6667 → 1.0000）；
  - C007 19 候选排序完整保留，无池外/丢失。

## 14. 候选完整性

```json
{
  "answerable_request_count": 48,
  "answerable_success_count": 48,
  "negative_request_count": 2,
  "negative_success_count": 2,
  "error_count": 0,
  "candidate_preservation_rate": 1.0,
  "pool_out_count": 0,
  "duplicate_count": 0,
  "lost_count": 0,
  "fallback_count": 0,
  "passed": true
}
```

- 每题的 `output_count == input_count`，输出多重集合 == 输入多重集合；
- original rank/score、文本 hash、document、page、parent_id 全部未变；
- 冻结池既有重复行（C007 等 C 系列）如实记录在 `completeness.json` 的 `input_duplicate_chunk_ids` 与 notes 中，不属于 Reranker 引入。

## 15. 离线门禁

- 硬门禁：全部通过（Recall@5、Gold Page、Gold Evidence、MRR@5、Top-1 Doc 降幅 <=0.02；error=0；preservation=1.0；pool_out=0；duplicate=0；lost=0；fallback=0）。
- 价值门禁：全部满足（Recall@5 +0.1042、MRR@5 +0.1750、Gold Page +0.0833、Gold Evidence +0.0833、EvP@5 +0.0333、失败转成功 4 题 >=2、净提升 4 >=2）。
- `stage2_allowed = true` → 进入阶段二。

## 16. 是否进入完整答案阶段

**是**。R0/R1 各 50 题，固定 qwen-plus-2025-07-28、fallback=false、thinking=false、parent_expansion=none、同一 Answer Prompt、同一 Evidence Policy、同一候选池、同一 effective_final_k 规则、同一 retry/timeout。

## 17. 完整答案指标（阶段二）

### 引用（48 题可答题）

| 指标 | R0 | R1 |
|---|---|---|
| Citation Accuracy | 0.8333 | 0.8958 |
| Citation Precision | 0.3299 | 0.3368 |
| Citation Recall | 0.7212 | 0.7899 |
| Citation Traceability | 1.0000 | 1.0000 |
| Unsupported Citation Rate | 0.6838 | 0.6691 |
| Unsupported Citation Rows | 8 | 5 |

### 拒答（N001/N002 分母 = 2）

| 指标 | R0 | R1 |
|---|---|---|
| Insufficient Evidence Rejection Rate | 1.0000 | 1.0000 |
| Unsupported Answer Rate | 0.0000 | 0.0000 |
| False Rejection Rate（48 可答题） | 0.3125 | 0.2917 |

- Answer Correctness / Faithfulness：**N/A**（未运行 LLM Judge，不编造）。

## 18. 负样本拒答分析

| 题 | R0 候选 | R1 候选 | R0 拒答 | R1 拒答 | R0/R1 Unsupported Answer | R0 LLM 调用 | R1 LLM 调用 |
|---|---|---|---|---|---|---|---|
| N001 | 20 | 20 | 是（evidence_policy_rejected） | 是（evidence_policy_rejected） | 否/否 | 否 | 否 |
| N002 | 19 | 19 | 是 | 是 | 否/否 | 是（模型自行拒答） | 是（模型自行拒答） |

- N001：Evidence Policy 直接拒绝，零答案调用；
- N002：策略选中 1 条干扰证据（t1739cn.pdf p21），LLM 被调用但按系统提示输出标准拒答语；两组均无 Unsupported Answer；
- Rerank 未使负样本产生错误回答（R1 同样正确拒答）。

## 19. 分类结果（Citation Accuracy / Citation Recall）

| 类别（题数） | R0 Acc | R1 Acc | R0 Recall | R1 Recall |
|---|---|---|---|---|
| 参数查询（20） | 0.9000 | 0.9000 | 0.9000 | 0.9000 |
| 表格查询（3） | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| 操作步骤（9） | 0.7778 | 1.0000 | 0.6111 | 0.8333 |
| 故障诊断（3） | 0.6667 | 1.0000 | 0.6667 | 1.0000 |
| 安全警告（5） | 1.0000 | **0.8000** | 0.7400 | 0.7000 |
| 普通事实（2） | 0.5000 | 0.5000 | 0.5000 | 0.5000 |
| 跨页问题（6） | 0.6667 | 0.8333 | 0.2361 | 0.3194 |

- 表格查询：两组均满分；
- 故障诊断：Rerank 显著提升；
- **安全警告：1 道题退化（1.0000 → 0.8000），是最终替换门禁失败的主因之一**；
- 跨页问题：提升明显（0.6667 → 0.8333）；
- 参数查询：数值证据未因 Rerank 降权（0.9 → 0.9）。

## 20. 配对统计（48 题，1000 次，seed=20260801，95% CI）

### 离线检索指标

| 指标 | mean Δ | 95% CI | 跨 0 |
|---|---|---|---|
| Recall@5 | +0.1042 | [0.0208, 0.1875] | 否 |
| MRR@5 | +0.1750 | [0.0688, 0.2896] | 否 |
| Gold Page Recall | +0.0833 | [0.0208, 0.1667] | 否 |
| Gold Evidence Recall | +0.0833 | [0.0208, 0.1667] | 否 |
| Evidence Precision@5 | +0.0333 | [0.0083, 0.0625] | 否 |

### 阶段二引用指标

| 指标 | mean Δ | 95% CI | 跨 0 |
|---|---|---|---|
| Citation Accuracy | +0.0625 | [-0.0208, 0.1458] | **是** |
| Citation Recall | +0.0896 | [0.0167, 0.1729] | 否 |

- 结论：检索类提升稳定；**Citation Accuracy 观察到提升但置信区间跨 0 —— 当前 50 题黄金集不足以证明稳定优势**。

## 21. Token、延迟与费用

### Rerank（qwen3-rerank）

- 本轮新增真实调用：3（C007、N001、N002），全部成功；
- 其余 47 题 + 预检全部复用已有响应（本轮 `cache_hits=51`、`live_api_calls=0`）；
- Rerank API 不返回 Token/费用 → **费用 N/A**（不编造）；
- Rerank 延迟：均值约 0.002s（缓存命中）；真实调用历史单次约 0.25-0.75s。

### 答案（qwen-plus-2025-07-28）

| 项 | R0 | R1 |
|---|---|---|
| LLM 调用数 | 49 | 49 |
| input tokens | 43,250 | 45,037 |
| output tokens | 4,196 | 3,771 |
| total tokens | 47,446 | 48,808 |
| Answer 延迟 mean / P50 / P95 (s) | 1.776 / 1.484 / 4.109 | 1.384 / 1.438 / 2.891 |
| 总延迟 P95 (s) | 4.109 | 2.891 |
| LLM cache hits / misses | 0 / 49 | 8 / 41 |
| 错误数 | 0 | 0 |

- 合计：98 次答案调用（90 次真实 + 8 次缓存命中），总 tokens 96,254；retry=0；model_mismatch=0；error=0。
- 人民币费用：SDK 未提供 → N/A，仅记录真实 Token。

## 22. 最终决策

```json
{
  "evaluation_completed": true,
  "rerank_enabled": false,
  "replacement_approved": false,
  "replacement_gates_passed": false,
  "rerank_model": "qwen3-rerank",
  "selection_reason": "qwen3-rerank did not pass Phase 4D replacement gates"
}
```

最终替换门禁判定：

- 硬门禁通过项：Citation Accuracy 下降 <=0.02、Citation Traceability=1.0、Unsupported Answer Rate=0、Rejection 不下降、False Rejection 恶化 <=0.05、Gold Page Recall 下降 <=0.02、参数类下降 <=0.05、Candidate Preservation=1.0、Rerank error=0、P95 <=2×基线。
- **硬门禁失败项**：
  - `Unsupported Citation Rate == 0` 失败（R1=0.6691）；
  - `安全警告类别不得退化` 失败（1.0000 → 0.8000）。
- 价值门禁满足 5/6（Recall@5、MRR@5、Gold Evidence、Citation Accuracy、Citation Recall 均 +0.02；False Rejection 降低 0.05 不满足，仅改善 0.0208）。

结论：**qwen3-rerank 在检索与多数引用指标上优于基线，但未通过替换默认策略的全部硬门禁，因此不启用。**

## 23. 是否替换默认

**否**。生产默认 `RERANK_ENABLED=false` 未修改；未修改任何生产环境变量。Rerank 结果仅保留为实验产物。

## 24. 测试与 Ruff

```text
python -m pytest --collect-only -q
python -m pytest -q
python -m ruff check .
```

实际结果：

```text
454 tests collected
442 passed, 12 skipped, 0 failed
ruff check . -> All checks passed
```

新增/更新测试覆盖：variable-size 候选与 effective_final_k、C007 19→19 多重集合完整性、N001/N002 负样本保留与分母排除、request_payload_hash 缓存复用/隔离、provider 空候选跳过与 variable-size 响应、离线门禁、bootstrap Gold Page 口径、候选池 manifest 新契约字段。skip 12 项均为真实外部 opt-in（MinerU/Qdrant E2E），非新增。

## 25. 是否允许进入 Phase 5

**允许**（Phase 4D 评估已完成：`evaluation_completed=true`）。本阶段按要求**立即停止，不自动进入 Phase 5**。

## 已知限制

- 冻结池中 C007/C003/C004/C008 存在既有重复 chunk_id 行（C007=19 行/18 唯一，其余 20 行/19 唯一），本阶段按多重集合完整性处理并如实记录，未修改冻结输入；
- Unsupported Citation Rate 高（两组均 ~0.67）来自确定性 Evidence Policy 对部分问题选入非黄金页证据，非 LLM 幻觉；该指标定义为本报告的自定义审计口径（错误引用页数 / 总引用页数）；
- qwen3-rerank 无日期快照 ID，`actual_model_version=null`；
- API 不返回人民币费用，费用 N/A；
- Citation Accuracy 的配对 bootstrap 置信区间跨 0，50 题样本不足以证明引用准确率的稳定优势；
- 阶段二仅使用确定性证据选择（limit=3）+ 固定 Answer Prompt，未运行 LLM Judge，Answer Correctness/Faithfulness=N/A。
