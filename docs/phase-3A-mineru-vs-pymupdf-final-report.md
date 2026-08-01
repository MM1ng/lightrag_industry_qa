# Phase 3A 最终报告：MinerU Online 与 PyMuPDF 公平 RAG 对比（Closeout）

**报告日期**: 2026-08-01
**分支**: `codex/knowledge-qa-platform-design`
**Phase 3 commit**: `6eae939` … `ff6afec`（见 [phase-3-qdrant-final-report.md](phase-3-qdrant-final-report.md) §12）
**实验 commit**: `8a53d3b`（Phase 3A-R-Paid 正式结果）

---

## 1. 最终阶段结论

- **Phase 3A complete**；**Phase 4 allowed**（不自动实施）。
- 正式结论基于 **Phase 3A-R-Paid** 付费固定模型实验（P0/P1 均完成完整索引与各 50 题），是唯一当前结论。
- 固定模型 `qwen-plus-2025-07-28`，fallback=false，thinking=false；P0/P1 全部 LLM 调用 requested_model == actual_model，0 重试、0 错误。
- 最终解析器决策：**C. PyMuPDF 默认，MinerU 由用户手动选择**（详见 §16）。
- 历史非固定模型实验已归档（见 §13），不参与正式公平对比。
- 未修改生产默认解析器；未重新解析正式 KB；未进入 Rerank；未实施 Phase 4。

---

## 2. 实验环境

| 项 | 值 |
|---|---|
| Python | 3.11.15（`industrial-rag` conda env） |
| PyMuPDF | 1.28.0 |
| LightRAG | 1.5.4 |
| qdrant-client | 1.18.0 |
| Qdrant Server | v1.13.6（容器 `ira-phase3-qdrant-test`，127.0.0.1:16333） |
| 实验机器 | 本机（同一台） |
| Git commit | `8a53d3b` |

### PDF 事实（未修改）

| PDF | 大小 | SHA256 | 页数 | 加密 |
|---|---|---|---|---|
| 2196-ANSI-Manual-Chinese.pdf | 1,561,387 | `e0f80874dd923d03ea15584f4fe25046ba184675062d6d16e1decafa2a6c8700` | 55 | 否 |
| t1739cn.pdf | 4,532,306 | `77fd7ebf86ef6c574de11eac446dc321de04fd0773cd7d844287da7fa4d6c4ae` | 62 | 否 |

两页空白（2196 p2/p4、t1739cn p2）经 PyMuPDF 文本长度核验为无文本页，两组解析覆盖率一致（53/55、61/62）。

---

## 3. 唯一独立变量与固定变量

唯一独立变量：**parser_pipeline**

```json
{
  "only_independent_variable": "parser_pipeline",
  "p0_parser_pipeline": "pymupdf_standard_adapter",
  "p1_parser_pipeline": "mineru_online_clean_adapter"
}
```

从 StructuredChunker 开始完全一致：Chunker 配置、Parent/Child 参数、Tokenizer、overlap、Embedding 模型与维度、索引/查询 LLM、Prompt bundle、query_mode、top_k、chunk_top_k、evidence_limit、Qdrant schema、distance、timeout/retry/并发、黄金集、评估代码、LightRAG 版本、实验代码 commit。

正式固定配置：

| 项 | 值 |
|---|---|
| model / index_llm / query_llm | `qwen-plus-2025-07-28` |
| fallback / thinking | false / false |
| embedding | `text-embedding-v4`，1024 维 |
| query_mode / top_k / chunk_top_k / rerank | `mix` / 12 / 20 / false |
| qdrant_distance | COSINE |

八项配置 hash（chunk/embedding/index_llm/query_llm/prompt_bundle/retrieval/qdrant_schema/golden_set）P0=P1；冻结产物 25 项 SHA256 固化于 `manifests/phase3a_frozen_artifacts_manifest.json`。

---

## 4. MinerU 真实解析结果

### 网络核验

- 代理变量未设置；Windows 系统代理关闭。
- 初查发现 Clash TUN 网卡仍在运行并劫持 DNS（fake-IP），用户退出 Clash 后 DNS/TLS 恢复正常。
- 全程 `verify=True`，无证书绕过。

### 真实调用（两份 PDF 全本，无 fallback）

| 项 | 2196 | t1739cn |
|---|---|---|
| task_id | `3c8243c8-15ea-4c60-a2cb-c2347c83d769` | `bd399f71-256c-49e5-9ffc-5b709689d03e` |
| poll 次数 | 2 | 13 |
| result.zip 字节 / SHA256 | 5,390,193 / `cc71e3b6…` | 10,007,889 / `52577d60…` |
| content_list SHA256 | `93e7509f…` | `c66996fc…` |
| 原始页面 | 53 | 61 |
| 总耗时 | 5.5 s | 39.9 s |

两份 P1 原始 manifest 均满足 `parser_used=mineru_online`、`fallback_used=false`。原始 ZIP/content_list/pages 保留且未修改（冻结 hash 核验通过）。

---

## 5. P1-clean Adapter

P1-clean 是 **MinerU 标准解析管线的一部分**（`mineru_online_clean_adapter`），不是下游检索优化。清洗仅含：基于真实 block type 的确定性过滤、空块处理、重复版式脚注处理、HTML 表格确定性文本化、Unicode/空白规范化；不含 LLM 清洗、OCR 纠错、参数修正、检索/Prompt 调参、Rerank。

细节见 [phase-3A-mineru-adapter-cleanup-report.md](phase-3A-mineru-adapter-cleanup-report.md)。

### 正式解析统计（P1-clean）

| 指标 | 2196 | t1739cn |
|---|---|---|
| 字符数 | 30,149 | 22,180 |
| Token | 22,748 | 16,722 |
| ParsedBlock | 385 | 600 |
| Parent | 59 | 90 |
| Child | 89 | 95 |
| 重复 Chunk | 0 | 0 |
| >700 Token Chunk | 0 | 0 |
| 表格保留 | 39 | 15（另有 9 个图片式表格明确记录） |
| 有效页 | 51 | 55 |

### Adapter 消融：P1-raw（非正式组）

`superseded / ablation only / not used for final comparison`

| 指标 | 2196 raw | t1739cn raw |
|---|---|---|
| 字符数 / Token | 68,146 / 36,992 | 46,989 / 26,346 |
| Parent / Child | 234 / 278 | 136 / 159 |
| 重复 Chunk id（出现次数） | 46（58） | 0 |
| >700 Token Child | 8 | 3 |
| HTML 标记 Token | 25,095 | 10,045 |

清洗效果：token_reduction 38.5%（2196）/ 36.5%（t1739cn）；chunk_reduction 68.0% / 40.2%。

---

## 6. 正式付费运行

- 用户开启阿里云百炼按量付费并明确授权；门禁 `IRA_PHASE3A_PAID_RUN=1`、`LLM_MODEL=qwen-plus-2025-07-28`、`MODEL_FALLBACK_ENABLED=false` 设置并通过 readiness。
- 旧 P1 partial 资源按登记记录精确清理（kb `027e9eb3…` / generation `g1cea29d…`；Qdrant 当时 0 残留）。
- 每次 LLM 调用记录 requested/actual model、input/output/total tokens、latency、retry、status；每 50 次调用保存 monitor；精确缓存与两级 checkpoint 启用。
- P0：KB `4e1b9915…` / generation `g746baf3…` / prefix `ira_p3ar_c28fd9a8`
- P1：KB `b3885f95…` / generation `g9fff457…` / prefix `ira_p3ar_43c94775`

---

## 7. P0/P1 索引完整性

| 检查 | P0 | P1 |
|---|---|---|
| chunks / entities / relationships points | 453 / 1,024 / 1,082 | 184 / 774 / 661 |
| 文档状态 | 2/2 processed | 2/2 processed |
| processing / failed / partial | 0 | 0 |
| generation verify | ✅ | ✅ |
| 跨 KB 数据 | 无（collection 完全隔离） | 无 |

---

## 8. 正式检索指标

### 指标定义表

| 指标 | 分母 | 说明 |
|---|---|---|
| Evidence Mapping（exact/fuzzy/unmapped） | **70 条黄金证据**（逐条） | 每条黄金证据独立映射到 child；exact=页+文本匹配，fuzzy=跨页文本覆盖匹配 |
| Gold Evidence Recall | **48 道可回答问题**（每题） | 任意 rank 命中该题任一 mapped child 即计 1 |
| Recall@1/3/5 | **48 道题** | top-K 内命中任一 mapped child |
| MRR | **48 道题** | 首个命中 rank 的倒数（top5 内；未命中记 0） |
| Gold Page Recall / Top-5 页面覆盖率 | **48 道题** | top5 检索项的 (file, page) 与黄金页相交 |
| Evidence Precision@5 | **48 道题** | top5 中 mapped child 占比（按题平均） |
| Citation Accuracy / Precision / Recall | **48 道题** | 引用集合与黄金 (file, page) 集合匹配 |
| Insufficient Evidence Rejection | **2 题**（N001/N002） | 无据问题拒答率 |
| False Rejection Rate | **48 题** | 有据问题被拒答 |
| Unsupported Answer Rate | **2 题** | 无据问题给出答案 |

说明：N001/N002 不参与 Recall@K/MRR 等检索指标（`evidence_case_count=48`）；fuzzy mapping 计入 mapped 集合并参与 Recall；unmapped 不直接记为失败，仅不提供该条证据的命中点（若某题全部证据 unmapped 则其 evidence recall 只能为 0）。**4 条 unmapped 与 Gold Evidence Recall 0.9375 不冲突**（分母不同：70 条证据 vs 48 道题）。

### 正式检索指标（P0 vs P1）

| 指标 | P0 | P1 |
|---|---|---|
| Recall@1 | **0.5625** | 0.5208 |
| Recall@3 | 0.6875 | 0.6875 |
| Recall@5 | **0.7500** | 0.7292 |
| MRR | **0.6167** | 0.6111 |
| Gold Document Recall | 1.0000 | 1.0000 |
| Gold Page Recall | **0.7917** | 0.6667 |
| Gold Evidence Recall | 0.9167 | **0.9375** |
| Evidence Precision@5 | 0.1958 | 0.1833 |
| 无结果率 | 0 | 0 |
| 错误文档召回率 | 0 | 0 |
| Top-1 文档正确率 | **1.0000** | 0.9792 |
| Top-5 页面覆盖率 | **0.7917** | 0.6667 |

### Evidence Mapping

| | P0 | P1 |
|---|---|---|
| 黄金证据总数 | 70 | 70 |
| 精确映射 | 70 | 61 |
| 模糊映射 | 0 | 5 |
| unmapped | 0 | 4 |

---

## 9. 正式引用与拒答指标

| 指标 | P0 | P1 |
|---|---|---|
| Citation Accuracy | **0.8958** | 0.7917 |
| Citation Precision | **0.3507** | 0.3090 |
| Citation Recall | **0.7785** | 0.6941 |
| Citation Traceability | 1.0000 | 1.0000 |
| Unsupported Citation Rate | 0 | 0 |
| Insufficient Evidence Rejection | 1.0000（2/2） | 1.0000（2/2） |
| False Rejection Rate | 0.2917 | 0.3333 |
| Unsupported Answer Rate | 0 | 0 |

Answer Correctness / Faithfulness：**N/A**（为避免额外 Judge 费用未运行 LLM Judge；检索与确定性引用指标完整）。

---

## 10. 正式分类对比

黄金集 canonical 分类（见 `manifests/golden_categories.json`，含 primary_category 与 secondary_tags）：参数查询 20、表格查询 3、操作步骤 9、故障诊断 3、安全警告 5、普通事实 2、跨页问题 6、证据不足 2。

> 历史章节曾出现 安全警告=4、跨页=7：旧统计把 C007（维修前安全措施）归入跨页问题；canonical 分类将 C007 归入安全警告（primary），跨页作为 secondary tag。黄金集未修改。

| 分类 | P0 page / evidence | P1 page / evidence |
|---|---|---|
| 参数查询 (20) | **0.9000 / 0.8000** | 0.6500 / 0.7500 |
| 表格查询 (3) | 1.0000 / 1.0000 | 1.0000 / 1.0000 |
| 操作步骤 (9) | **0.6667 / 0.6667** | 0.5556 / 0.6667 |
| 故障诊断 (3) | **0.6667 / 0.6667** | 0.3333 / 0.3333 |
| 安全警告 (5) | **0.8000 / 0.8000** | 0.6000 / 0.6000 |
| 普通事实 (2) | 0.5000 / 0.5000 | **1.0000 / 1.0000** |
| 跨页问题 (6) | 0.6667 / 0.6667 | **0.8333 / 0.8333** |
| 证据不足 (2) | 拒答 ✅ | 拒答 ✅ |

重点结论：

1. MinerU-clean 表格结构未提升表格类召回（两组均 1.0）；
2. MinerU-clean 故障诊断表未提升故障类召回（0.33 vs 0.67，页属性偏移所致）；
3. OCR 错误（如 `ACO`/`$1 5 0 ^ { \circ }$`）与参数查询页召回下降相关（0.65 vs 0.90）；
4. 页眉页脚清洗后两组 Evidence Precision 均较低（0.196/0.183），清洗未带来 precision 优势；
5. MinerU-clean 在普通事实与跨页问题上占优；
6. 操作步骤与安全警告未见 MinerU 收益（P0 更好或持平）；
7. MinerU-clean 的 Token/调用数显著更低（见 §11）。

---

## 11. 性能、Token 和成本

| 项 | P0 | P1 |
|---|---|---|
| LLM 调用数 | 594 | 296 |
| input / output / total tokens | 1,053,531 / 175,852 / **1,229,383** | 499,956 / 102,220 / **602,176** |
| cache hit / miss | 0 / 594（首次运行无缓存） | 0 / 296 |
| retry / error / mismatch | 0 / 0 / 0 | 0 / 0 / 0 |
| 索引耗时 | ≈18.4 min | ≈11 min |
| 查询延迟 avg / P50 / P95 | 3.78 / 3.75 / 5.66 s | 3.70 / 3.44 / 6.09 s |
| 人民币费用 | N/A（SDK 未提供，不编造） | N/A |

MinerU-clean 相对 P0：Token −51.0%、LLM 调用 −50.2%、索引时间 −40%，但核心检索/引用指标整体略降。

---

## 12. OCR 与页码元数据问题

### OCR 错误（保留未修复，可追溯）

- 2196 p14 表3：`150°F` → `$1 5 0 ^ { \circ }$`；“表2 所示信息”丢“表”字；
- 2196 p15 表4：`AC0` → `ACO`；美孚 DTE 行单元格错位；
- 影响：参数查询类别 evidence recall 0.75 vs P0 0.80、page recall 0.65 vs 0.90。

### 页级 provenance 元数据归属问题（技术债 MINERU-PROVENANCE-001）

“当前 MinerU-clean 解析管线的页级 provenance 元数据归属问题”：heading 分组形成跨页 parent（如 p22-24、p27-39），部分 child 的 `page_start` 使用 parent 起始页，导致页级映射偏移。明确区分：

1. **内容召回**：未丢失（pages_clean 与人工复核确认）；
2. **页码归属**：child page_start 偏移（受影响页 2196 p23/24/28/29、t1739cn p22）；
3. **Citation 页码准确性**：引用页码与黄金页码不一致，拉低 Gold Page Recall（−12.5pp）与 Citation Accuracy（−10.4pp）。

登记：[MINERU-PROVENANCE-001](evaluation/experiments/parser_backend/tech_debt/MINERU-PROVENANCE-001.md)。**不阻塞以 PyMuPDF 为默认解析器的 Phase 4**；重新评估 MinerU 前应修复。

---

## 13. 历史非固定模型实验

`historical / superseded / not used for final comparison`

归档目录：`evaluation/experiments/parser_backend/historical/previous_p0_non_fixed_model/`（旧 P0 完整结果）与 `previous_p1_partial/`（旧 P1 部分索引，2196 60/278 处因配额中断）。

- 旧实验索引/查询过程中发生模型降级（qwen3.6-plus → qwen3.6-flash → qwen-turbo → qwen3.5-flash-2026-02-23），不参与正式公平比较；
- 旧 P0 指标（如 Recall@5 0.7917、Gold Page Recall 0.8542、Citation Accuracy 0.9375）**只属于历史基线**，不得与正式 P1 比较；
- 正式比较唯一使用 §8-§10 的固定模型数据。

---

## 14. 测试与 Ruff

```text
python -m pytest --collect-only -q   -> 417 collected
python -m pytest -q                  -> 405 passed, 12 skipped, 0 failed
python -m ruff check .               -> All checks passed
```

实际 12 项 skip 分类：

- 11 项：真实 Qdrant 集成 opt-in（`IRA_QDRANT_INTEGRATION=1`，外部服务）；
- 1 项：真实 MinerU API opt-in（`IRA_MINERU_REAL=1`，避免重复消耗配额/费用）。

> 历史报告中“3 项因 P1 检索结果待生成而 skipped”已修正：P1 结果已生成，相关 3 项测试改为指向正式 `fixed_model` 结果并已实跑通过。

---

## 15. 资源清理

- 6 个登记 collection（P0/P1 各 3 个）已按精确名称删除；Qdrant 现存 0 collection；
- 临时 workspace（`tmp/full_0`、`tmp/full_1`）已删除；清理清单见 `manifests/paid_run_cleanup_manifest.json`；
- 保留：逐题结果、指标、LLM 调用日志、monitor、精确缓存、checkpoint、冻结产物（均在磁盘/仓库）；
- 未删除正式 KB、正式 Nano workspace、MinerU 原始文件、黄金集。

---

## 16. 最终解析器决策

**C. PyMuPDF 默认，MinerU 由用户手动选择**

依据：

- P0 Recall@5 略高（0.7500 vs 0.7292）；
- P0 Gold Page Recall 明显更高（0.7917 vs 0.6667）；
- P0 Citation Accuracy 更高（0.8958 vs 0.7917）；
- P0 参数、故障、安全类别更稳定；
- P1 Token 成本低约 51%，Gold Evidence Recall 略高（0.9375 vs 0.9167）；
- P1 跨页与普通事实类别有优势；
- P1 当前存在 OCR 与 provenance 元数据问题；
- 表格类两组均为 1.0，未证明 MinerU 有检索优势。

准确表述：**当前实现和当前黄金集下，MinerU-clean 的结构及成本优势没有转化为整体检索和引用优势。** 默认解析器保持 PyMuPDF；生产默认值未修改。

---

## 17. 已知限制

- Answer Correctness / Faithfulness 标记 N/A（未运行 LLM Judge）；
- 标题/步骤/警告统计为行级启发式，人工抽查 14 页；
- MinerU-clean 页级 provenance 归属问题（MINERU-PROVENANCE-001）影响其页码指标；
- Evidence Mapping 的 4 条 unmapped 与 5 条 fuzzy 集中在受影响页面；
- 人民币费用 SDK 未提供，标记 N/A；
- 预检响应缓存未持久化（正式运行精确缓存自运行期生效，本次两组合计 890 次调用均为首次 miss）；
- 本报告为静态快照；复现需新建 KB/generation（checkpoint 作审计记录，不能恢复已清理的临时资源）。

---

## 18. Phase 4 基线

基线文件：`evaluation/experiments/phase4/baseline_manifest.json`

- `source_phase = "Phase 3A-R-Paid"`
- `default_parser_pipeline = "pymupdf_standard_adapter"`
- `comparison_parser_pipeline = "mineru_online_clean_adapter"`
- 包含：默认原因、固定模型、embedding、query_mode/top_k/chunk_top_k/rerank、黄金集 path+SHA256、P0/P1 正式指标、逐题结果路径+SHA256、prompt bundle SHA256、实验 commit、created_at。

**Phase 4 不再比较解析器**，唯一实验变量为检索策略（混合检索 / Parent 扩展 / Rerank）。允许进入 Phase 4，但本任务不实施。
