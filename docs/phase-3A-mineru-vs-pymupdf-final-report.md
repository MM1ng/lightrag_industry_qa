# Phase 3A 最终报告：MinerU Online 与 PyMuPDF 解析、切块与 RAG 检索对比

**报告日期**: 2026-08-01
**分支**: `codex/knowledge-qa-platform-design`
**Phase 3 commit**: `6eae939` … `ff6afec`（见 [phase-3-qdrant-final-report.md](phase-3-qdrant-final-report.md) §12）

---

## 1. 阶段结论

- **Phase 3A complete（2026-08-01 付费运行正式完成）；Phase 4 allowed（不自动实施）**
- 付费授权：用户已开启阿里云百炼按量付费并明确授权本次实验产生费用；显式门禁 `IRA_PHASE3A_PAID_RUN=1` 已设置并通过。
- 固定模型 `qwen-plus-2025-07-28`，fallback=false，thinking=false；P0/P1 全部 LLM 调用 requested==actual==该模型，0 重试、0 错误。
- P0（PyMuPDF 标准解析管线）与 P1（MinerU 标准清洗解析管线）**均完成完整索引 + 50 题**，全部完整性门禁通过，临时资源已精确清理。
- MinerU 真实调用 **全部成功**：batch submit → 签名上传 → poll done → **CDN ZIP 下载成功** → content_list 可读 → pages 提取成功（两份 PDF 均完成）。
- P1 使用冻结的 MinerU-clean（`mineru_online_clean_adapter`）构建索引；P1-raw 仅作消融，不参与正式对比。
- 正式结果摘要：P0 在 Recall@1/5、MRR、Gold Page Recall、Citation Accuracy 上领先；P1（MinerU-clean）在 Gold Evidence Recall 上略高（0.9375 vs 0.9167），LLM Token 消耗低约 **51%**（602k vs 1,229k）。
- 此前历史实验（非固定模型）还经历了 DashScope 免费额度逐模型耗尽（kimi → qwen3.6-plus → qwen3.6-flash → qwen-plus → qwen-turbo → qwen3.5-flash），已归档为 historical，不参与最终对比。
- **最终解析器决策：C（PyMuPDF 默认，MinerU 手动选择）**——数据不支持自动切换；默认解析器保持 PyMuPDF，不实施生产默认值修改。

---

## 2. Phase 3 commit

- 数据模型/migration：`6eae939`
- Qdrant 存储适配：`a10c94e`
- 运行时与生命周期：`51b4837`
- API：`e8f06ce`
- 测试：`bfd2f8b`
- 文档：`98dcb4d`，commit hash 回填：`ff6afec`

---

## 3. 实验环境和固定变量

### 环境

| 项 | 值 |
|---|---|
| Python | 3.11.15（`industrial-rag` conda env） |
| PyMuPDF | 1.28.0 |
| LightRAG | 1.5.4 |
| qdrant-client | 1.18.0 |
| Qdrant Server | v1.13.6（容器 `ira-phase3-qdrant-test`，127.0.0.1:16333） |
| 实验起始 | 2026-08-01 09:34（CST 时段） |
| Git commit | `ff6afec`（实验脚本/测试提交见文件变更章节） |

### PDF 事实（未修改）

| PDF | 大小 | SHA256 | 页数 | 加密 |
|---|---|---|---|---|
| 2196-ANSI-Manual-Chinese.pdf | 1,561,387 | `e0f80874dd923d03ea15584f4fe25046ba184675062d6d16e1decafa2a6c8700` | 55 | 否 |
| t1739cn.pdf | 4,532,306 | `77fd7ebf86ef6c574de11eac446dc321de04fd0773cd7d844287da7fa4d6c4ae` | 62 | 否 |

两页空白（2196 第 2、4 页；t1739cn 第 2 页）经 PyMuPDF 文本长度核验确认为**无文本页**，两组解析覆盖率一致（53/55、61/62）。

### 固定变量（P0/P1 相同）

- Chunker：`ChunkerConfig(strategy="pymupdf-v1")`（parent_target 1500 / child_target 450 / child_min 120 / child_max 700 / overlap 80 / merge_small_children=True）
- Embedding：`text-embedding-v4`，1024 维
- **历史非固定模型基线**（仅归档，不用于公平对比）：旧实验开始时锁定 qwen3.6-plus，随后因额度耗尽依次降级 qwen3.6-flash → qwen-turbo → qwen3.5-flash-2026-02-23；P1 索引在 2196 第 60/278 chunk 处被 403 中断。该结果是真实运行结果，但 LightRAG 图谱抽取与 mix 查询可能受 LLM 变化影响，因此**不得用于最终 P0/P1 公平对比**。
- LightRAG：mix / top_k=12 / chunk_top_k=20 / enable_rerank=False / evidence_limit=3 / chunk_token_size=2000（实验统一；生产默认仍 1600）
- Qdrant：COSINE、1024 维、随机测试前缀、P0/P1 不同 KB/collection
- 黄金集：`industrial_pump_golden_set_50.jsonl`（SHA256 `fc52600f…`，50 题，未修改）

---

## 3b. 正式固定模型实验配置

正式公平实验（Phase 3A-R）使用以下冻结配置（`fixed_model/config.json`），P0/P1 完全一致，唯一独立变量为 `parser_pipeline`：

| 项 | 值 |
|---|---|
| model / index_llm / query_llm | `qwen-plus-2025-07-28`（三者一致） |
| fallback | `false` |
| thinking | `false` |
| embedding | `text-embedding-v4` |
| dimension | 1024 |
| query_mode | `mix` |
| top_k | 12 |
| chunk_top_k | 20 |
| rerank | `false` |

```json
{
  "only_independent_variable": "parser_pipeline",
  "p0_parser_pipeline": "pymupdf_standard_adapter",
  "p1_parser_pipeline": "mineru_online_clean_adapter"
}
```

八项配置 hash（chunk / embedding / index_llm / query_llm / prompt_bundle / retrieval / qdrant_schema / golden_set）P0=P1；冻结产物 25 项 SHA256 已固化（见 [phase-3A-paid-run-readiness-report.md](phase-3A-paid-run-readiness-report.md)）。

---

## 3c. Phase 3A-R-Paid 正式公平实验结果

### 索引完整性与模型一致性（付费运行实测）

| 项 | P0 pymupdf_standard_adapter | P1 mineru_online_clean_adapter |
|---|---|---|
| KB / generation | `4e1b9915…` / `g746baf3…` | `b3885f95…` / `g9fff457…` |
| chunks points | 453 | 184 |
| entities points | 1,024 | 774 |
| relationships points | 1,082 | 661 |
| 文档状态 | 2/2 processed | 2/2 processed |
| 无 processing/failed/partial | ✅ | ✅ |
| LLM 调用数 | 594 | 296 |
| 全部 requested==actual | `qwen-plus-2025-07-28` ✅ | `qwen-plus-2025-07-28` ✅ |
| retry / error / mismatch | 0 / 0 / 0 | 0 / 0 / 0 |
| input / output / total tokens | 1,053,531 / 175,852 / **1,229,383** | 499,956 / 102,220 / **602,176** |
| cache hit / miss | 0 / 594（首次运行无缓存） | 0 / 296 |
| 索引耗时 | ≈18.4 min | ≈11 min |

### 检索指标（同 50 题黄金集）

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

### 引用与拒答

| 指标 | P0 | P1 |
|---|---|---|
| Citation Accuracy | **0.8958** | 0.7917 |
| Citation Precision | **0.3507** | 0.3090 |
| Citation Recall | **0.7785** | 0.6941 |
| Citation Traceability | 1.0000 | 1.0000 |
| Unsupported Citation Rate | 0 | 0 |
| Insufficient Evidence Rejection（N001/N002） | 1.0000 | 1.0000 |
| False Rejection Rate | 0.2917 | 0.3333 |
| Unsupported Answer Rate | 0 | 0 |
| 查询延迟 avg / P50 / P95 | 3.78s / 3.75s / 5.66s | 3.70s / 3.44s / 6.09s |

### 分类对比

| 分类 | P0 page/evidence | P1 page/evidence |
|---|---|---|
| 参数查询 (20) | **0.90 / 0.80** | 0.65 / 0.75 |
| 表格查询 (3) | 1.00 / 1.00 | 1.00 / 1.00 |
| 操作步骤 (9) | **0.667 / 0.667** | 0.556 / 0.667 |
| 故障诊断 (3) | **0.667 / 0.667** | 0.333 / 0.333 |
| 安全警告 (5) | **0.80 / 0.80** | 0.60 / 0.60 |
| 普通事实 (2) | 0.50 / 0.50 | **1.00 / 1.00** |
| 跨页问题 (6) | 0.667 / 0.667 | **0.833 / 0.833** |
| 证据不足 (2) | 拒答 ✅ | 拒答 ✅ |

### Evidence Mapping（独立映射，黄金集未修改）

| | P0 | P1 |
|---|---|---|
| 黄金证据总数 | 70 | 70 |
| 精确映射 | 70 | 61 |
| 模糊映射（跨页文本覆盖） | 0 | 5 |
| unmapped | 0 | 4 |
| 人工确认 | 抽样 14 页 | 抽样 14 页 |

P1 的 4 条 unmapped 与 5 条模糊映射集中在 2196 p23/24/28/29 与 t1739cn p22：MinerU-clean 的 heading 分组产生跨页 parent（如 p22-24、p27-39），child 的 page_start 偏移，导致页级映射失败（内容未丢失，见 pages_clean 核验）。

### OCR 错误影响

- 2196 p14 表3：`150°F` → `$1 5 0 ^ { \circ }$`、`表2` 丢字；p15 表4：`AC0→ACO`、美孚行错位——这些 OCR 原始错误在 P1-clean 中保留未修复，参数查询页召回 0.65 vs P0 0.90。
- 故障诊断页 23 的表格虽保留结构，但因页属性偏移（chunk page_start=22）导致 P1 故障诊断 evidence recall 0.333 vs P0 0.667。
- MinerU-clean 在普通事实与跨页问题上优于 P0（结构保留的收益体现在部分类别）。

### 结论

- 两组均为真实、完整、同一固定模型下的公平结果；唯一变量 `parser_pipeline`。
- MinerU-clean 的核心收益是**成本**（Token −51%、调用数 −50%）与部分结构化内容；核心代价是**检索/引用指标整体略降**（Gold Page Recall −12.5pp、Citation Accuracy −10.4pp、故障诊断 −33pp），且引入 OCR 错误与页属性偏移。
- 表格双表示没有提升表格类查询（两组均 1.0），未构成切换到 MinerU 的依据。

---

## 4. 网络与 MinerU 真实调用

### 网络核验

- Shell 代理变量：`HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` 未设置（仅 `NO_PROXY`）。
- Windows 系统代理：`ProxyEnable=0`。
- 首查发现 Clash Verge 进程与 **TUN 网卡（Meta Tunnel）仍在运行**，mineru 域名被解析到 198.18.1.x fake-IP，`api.mineru.net` TLS 被中断。
- 用户退出 Clash 后复检：TUN 网卡消失、DNS 恢复正常（真实公网 IP）、`https://mineru.net` TLS 200。
- 全程未使用 `verify=False`，未关闭证书校验。

### Smoke Test（2196 全本）

成功链路：`POST /api/v4/file-urls/batch` → 签名 PUT（HTTP 200）→ 轮询 3 次 state=done → 下载 CDN ZIP 5,390,193 B → 解包 → content_list.json（575 项）→ pages.json（53 页）。

脱敏记录：

| 项 | 值 |
|---|---|
| task_id | `069bd31f-40a0-4768-9202-f53544deff44` |
| poll 次数 | 3（间隔 3s） |
| ZIP 字节 | 5,390,193 |
| ZIP SHA256 | `cc71e3b638072f80d943fbf64f5c76779c993de25a4826b9df48782e51a7b538` |
| content_list SHA256 | `93e7509fe1901a956dbffc2594fe6cdf021185bb65bbc1f867155a412cb82927` |
| 原始页面数 | 53（内容页） |
| 中文内容核验 | 正常（无乱码；早期控制台乱码为 GBK 显示问题，已修脚本） |

### P1 正式解析（严格模式，无 fallback）

| PDF | task_id | poll | ZIP 字节 | 页面 | content items | 总耗时 |
|---|---|---|---|---|---|---|
| 2196 | `3c8243c8-15ea-4c60-a2cb-c2347c83d769` | 2 | 5,390,193 | 53 | 575 | 5.5 s |
| t1739cn | `bd399f71-256c-49e5-9ffc-5b709689d03e` | 13 | 10,007,889 | 61 | 1478 | 39.9 s |

两份 P1 manifest 均满足：

```json
{"parser_requested": "mineru_online", "parser_used": "mineru_online", "fallback_used": false, "fallback_reason": null}
```

原始 ZIP、content_list.json、pages.json 已保存（`evaluation/experiments/parser_backend/P1/<pdf>/mineru_raw/`，不入库）。

---

## 5. 两份 PDF 解析质量对比

### 页面/文本统计

| 指标 | P0 2196 | P1 2196 | P0 t1739cn | P1 t1739cn |
|---|---|---|---|---|
| 有效页 / 总页 | 53/55 | 53/55 | 61/62 | 61/62 |
| 缺页 | [2,4]（空白页） | [2,4] | [2]（空白页） | [2] |
| 总字符 | 29,064 | 68,146 | 38,459 | 46,989 |
| 中文字符 | 12,253 | 11,988 | 14,586 | 14,043 |
| 段落数 | 1,203 | 610 | 1,447 | 1,078 |
| 重复段落 | 53 | 58 | 18 | 0 |
| 乱码字符 | 0 | 0 | 0 | 0 |

说明：P1 字符更多主要来自 HTML 表格标记与页眉/页脚/页码样板（2196 的 P1 content_list 含 52 header + 53 footer + 50 page_number 项）。中文正文量两组接近；MinerU 在 2196 略少（11,988 vs 12,253），抽查未见正文整段丢失，属分段方式差异。

### 结构统计（启发式，行级检测；表格为硬证据）

| 指标 | P0 2196 | P1 2196 | P0 t1739cn | P1 t1739cn |
|---|---|---|---|---|
| 标题（启发式） | 23 | 19 | 3 | 3 |
| 步骤行 | 183 | 142 | 224 | 186 |
| 警告行 | 52 | 34 | 118 | 96 |
| 故障条目 | 5 | 1 | 44 | 34 |
| 表格 | **0** | **39（402 行/2033 格）** | **0** | **15（151 行/476 格）** |

**关键差异**：PyMuPDF 把表格压平成文本；MinerU 保留 HTML 表格结构（含 rowspan/colspan）。人工抽查确认 MinerU 表格结构真实可用，但存在少量 OCR 误差（见 §7）。

---

## 6. 代表性页面人工抽样

页面选择规则：每份 PDF 覆盖封面、目录、正文、参数表、故障表、操作步骤、安全警告、跨页内容；选择在解析前确定，未按 MinerU 优势挑选。

| PDF | 页 | 类型 | PyMuPDF | MinerU | 更优 | 原因 |
|---|---|---|---|---|---|---|
| 2196 | 1 | 封面 | 完整 | 完整 | 持平 | 内容一致 |
| 2196 | 5 | 目录 | **部分（约半页）** | 完整（到 p47） | **MinerU** | PyMuPDF 在双栏/长目录处截断，MinerU 捕获全部条目 |
| 2196 | 9 | 存储/警告 | 完整 | 完整+表格化警告 | 持平 | 警告框 MinerU 结构化 |
| 2196 | 11 | 对正步骤+表1 | 表格压平 | 表格 HTML 结构化 | **MinerU（结构）/P0（文本）** | MinerU 保留行列；两文数值一致 |
| 2196 | 14 | 轴承温度表2/表3 | 压平但文本准确 | 结构化；**OCR 误差** | **PyMuPDF（文本准确）** | MinerU `150°F` 变 `$1 5 0 ^ { \circ }$`、“表2”丢失“表”字 |
| 2196 | 15 | 润滑油表4 | 压平 | 结构化；**OCR 误差** | **PyMuPDF（文本准确）** | MinerU “AC0”→“ACO”、美孚行单元格错位 |
| 2196 | 17-18 | 启动/停机步骤 | 完整有序 | 完整有序 | 持平 | 步骤 1-8 顺序一致；警告框 MinerU 结构化 |
| 2196 | 23 | 故障诊断表 | 压平 | **结构化（rowspan 原因/措施）** | **MinerU** | 泵不泵送液体→5 条原因/处理，检索友好 |
| 2196 | 25 | 拆卸安全步骤 | 完整 | 完整+警告表格 | 持平 | 步骤与警告均保留 |
| 2196 | 27 | 装配/扭矩表 | 压平 | 结构化 | MinerU | 扭矩表行列保留 |
| t1739cn | 24 | 吸程公式 | 完整 | 完整 | 持平 | `H = Hb - NPSHr - Hf - Hv - Hs` 两方一致 |
| t1739cn | 26 | 对中步骤 | 完整 | 完整 | 持平 | 5 步顺序一致 |
| t1739cn | 31 | 启动前提 | 完整 | 完整 | 持平 | 列表顺序一致 |
| t1739cn | 32 | 启动/关闭步骤 | 完整 | 完整 | 持平 | 步骤与警告一致 |

退化案例：MinerU 2196 p14/p15 表格 OCR 误差（数学式混入、字符误读）；MinerU 页头/页脚/页码作为独立块混入正文（每页重复），是 P1 重复 chunk 的主要来源。

---

## 7. 表格、步骤、安全警告专项

### 表格

- PyMuPDF：全部压平为文本流；行列关系靠文字顺序，表头/合并单元不可恢复；RAG 检索时整表作为文本块，参数值可检索但“行列对应”依赖模型推断。
- MinerU：39（2196）+15（t1739cn）个表格保留 HTML 结构（表头、行、colspan/rowspan）；**对 RAG 检索更友好**，但存在 OCR 误差（`ACO`/`AC0`、`150°F` 数学式化、个别单元格错位）。
- 表格嵌入文本可读性：PyMuPDF 的文本干净；MinerU 的 HTML 标记在 embedding 中为噪声（增加 token，可能稀释语义），需要清洗才能最优。

### 步骤

- 两组步骤编号与顺序一致（2196 启动 8 步、停机 2 步；t1739cn 启动 4 步、关闭 2 步；拆卸步骤 1-18 顺序一致）。
- 未发现双栏重排或步骤丢失；MinerU 将步骤中的“小心/警告”块保留在原位置。

### 安全警告

- 两组均保留“警告！/小心！”正文；MinerU 以表格形式结构化（更易识别）。
- 未发现警告被页眉页脚过滤。

---

## 8. Parent/Child 统计

| 指标 | P0 2196 | P1 2196 | P0 t1739cn | P1 t1739cn |
|---|---|---|---|---|
| Parent 数 | 282 | 234 | 165 | 136 |
| Child 数 | 285 | 278 | 168 | 159 |
| 孤儿 Child | 0 | 0 | 0 | 0 |
| 重复 chunk（不同 id） | 5 | 46 | 5 | 0 |
| 重复出现次数 | 53 | 58 | 18 | 0 |
| Child <120 token | 243（85%） | 210（76%） | 117（70%） | 98（62%） |
| Child =1 token | 2 | 11 | 15 | 1 |
| Child >700 token | 0 | 8 | 0 | 3 |
| Child token mean / P50 / P95 / max | 78/22/442/670 | 137/31/494/1565 | 123/28/444/627 | 170/55/467/1494 |
| 表格 Parent 拆散 | 2/9 | 6/8 | 0/12 | 2/9 |
| 警告 Parent 拆散 | 0/17 | 1/17 | 1/24 | 5/20 |

要点：P1 的重复 chunk 主要来自 MinerU 每页页眉/页脚/页码样板（58 次重复，46 个不同 id）；P0 的重复来自页面标题样板（53 次，5 个 id）。MinerU 长页产生少量 >700 token 子块（上限 700 的合并策略导致），已通过实验统一 `chunk_token_size=2000` 兼容。

---

## 9. 黄金集检索结果

### 证据映射

- 原黄金集未修改（SHA256 校验通过）。
- Parser-specific 映射：70/70 条黄金证据（2196 p1-29、t1739cn p5-33 等）映射到 P0 child（映射率 100%）；P1 映射在 P1 检索完成后计算（映射逻辑与 P0 相同，独立文件）。

### P0（PyMuPDF → Qdrant）50 题真实结果

| 指标 | 值 |
|---|---|
| Recall@1 / @3 / @5 | 0.5625 / 0.6875 / 0.7917 |
| MRR | 0.6306 |
| Gold Document Recall | 1.0000 |
| Gold Page Recall | 0.8542 |
| Gold Evidence Recall | 0.8958 |
| Evidence Precision@5 | 0.2083 |
| 无结果率 | 0 |
| 错误文档召回率 | 0 |
| Top-1 文档正确率 | 1.0000 |
| Top-5 页面覆盖率 | 0.8542 |
| Citation Accuracy | 0.9375 |
| Citation Precision / Recall | 0.3854 / 0.8201 |
| Citation Traceability | 1.0000 |
| 证据不足拒绝率（N001/N002） | 0.5000（2 题中拒绝 1 题） |

逐题结果已保存：`evaluation/experiments/parser_backend/retrieval/pymupdf_qdrant/results.jsonl`（question_id、question、retrieved 文档/页/chunk/score/rank、citations、answer、latency）。

### P1（MinerU → Qdrant）

**未完成**：P1 索引需约 437 次 LLM 抽取调用；实际完成 60/278（2196）后，qwen3.5-flash 也返回 403 免费额度耗尽，全部 6 个可用模型均不可用。任务规则要求真实完成才能计算指标，故 P1 检索指标缺省，不编造。

---

## 10. 分类结果（P0）

| 分类 | 题数 | Gold Page Recall | Gold Evidence Recall | 说明 |
|---|---|---|---|---|
| 参数查询 | 20 | 0.90 | 0.90 | 数值类问题表现最好 |
| 表格查询 | 3 | 0.67 | 0.67 | 表格压平后仍可检索到页 |
| 操作步骤 | 9 | 0.89 | 1.00 | 步骤页命中率高 |
| 安全警告 | 4 | 1.00 | 1.00 | 全部命中 |
| 故障诊断 | 3 | 0.67 | 0.67 | 2196 故障页 23 命中；部分跨页漏检 |
| 普通事实 | 2 | 0.50 | 0.50 | D001 命中、D002 未命中 |
| 跨页问题 | 7 | 0.86 | 0.86 | 多页问题基本命中 |
| 证据不足 | 2 | — | — | 拒绝率 0.50 |

（分类明细见 `comparison/` 与 metrics.json。）

---

## 11. 回答和引用结果

- 引用类指标为确定性计算（见 §9），两组可在 P1 完成后直接对比。
- **Answer Correctness / Faithfulness：N/A**。实验期间模型额度依次耗尽、P0 查询中途发生模型降级，无法保证两组合法且同模型的 LLM Judge；按任务规则标记 N/A，不编造回答指标。

---

## 12. 性能和成本

### PyMuPDF

| 指标 | 2196 | t1739cn |
|---|---|---|
| 解析时间 | 0.66 s | 0.34 s |
| 输出 | 584 blocks / 282 parents / 285 children | 937 / 165 / 168 |

### MinerU（真实）

| 指标 | 2196 | t1739cn |
|---|---|---|
| submit→upload→poll→下载 | 5.5 s 总耗时，poll=2 | 39.9 s 总耗时，poll=13 |
| ZIP 大小 | 5.39 MB | 10.01 MB |
| 费用 | N/A（免费额度） | N/A |

### RAG（P0 实测；P1 部分）

- 索引 LLM 调用：约 453 次抽取 + 合并阶段少量调用（Qdrant points=453）
- 50 题查询：100 次 LLM 调用（每题 keyword+answer），LLM 调用计数由 openai 包装器实测
- 平均查询延迟：约 55–70 s/题（受降级模型拖累；正常模型约 5–10 s）
- Qdrant：chunks 453 points；P0/P1 使用不同 KB/collection，跨 KB 隔离由 Phase 3 集成测试验证
- P1：索引到 60/278 后被 403 中断（已清理该次精确 collection）

**成本结论**：MinerU 解析本身便宜（两本手册 <1 分钟 API 时间），但真实 LLM 索引成本与模型配额是当前实验的主要瓶颈；免费额度无法支撑两组全量（约 900 次抽取 + 200 次查询调用）。

---

## 13. 提升与退化案例

### MinerU 提升（≥5）

1. 2196 p5 目录：PyMuPDF 截断（约半页），MinerU 完整（到 p47）。
2. 2196 p11 表1：表格结构保留（型号×填料尺寸/环数）。
3. 2196 p14/p15 表2/表4：轴承温度、润滑油品牌表结构化。
4. 2196 p23 故障诊断表：rowspan 保留“现象→原因→措施”对应。
5. 2196 p27 螺栓扭矩表：行/列结构完整。
6. t1739cn p24 公式：`H = Hb - NPSHr - Hf - Hv - Hs` 两组一致。

### MinerU 退化（≥5，实际 6）

1. 2196 p14 表3：`150°F` 被 OCR 为 `$1 5 0 ^ { \circ }$`。
2. 2196 p14 正文：“表2 所示信息”丢失“表”字（“2 所示信息”）。
3. 2196 p15 表4：威氏润滑脂 `AC0` 误读为 `ACO`。
4. 2196 p15 表4：美孚 DTE 行单元格错位（“轻级 1 中级 重级”）。
5. 2196 p15 表4：飞利浦两行 rowspan 合并错误。
6. 每页页眉/页脚/页码作为独立块重复（P1 重复 chunk 46 个 id vs P0 5 个）。

---

## 14. 默认解析器决策

未完成 P1 检索对比前，按任务规则只能选择 **C 或 D**。综合现有证据：

- MinerU 结构（表格/目录/警告框）确实更好，但存在真实 OCR 误差，且引入页眉/页脚噪声与网络依赖；
- PyMuPDF 文本准确、零成本、离线，表格虽压平但对当前检索仍可用（Gold Page Recall 0.85）；
- 网络检查显示 Clash/TUN 会破坏 CDN TLS 下载（已复现并解决一次），网络稳定性风险真实存在。

**推荐：C. PyMuPDF 默认，MinerU 由用户手动选择**（当前不修改生产默认值；待 P1 检索完成后再做最终 A/B/C/D 决策）。

---

## 14b. Phase 3A-P/R：确定性适配器清洗 + 固定模型预检

### MinerU 确定性适配器（P1-clean）

- 根因：原 Adapter 把 header/footer/page_number/page_footnote 与表格 raw HTML 全部并入正文。
- 实现 `MinerUBlockPolicy`：确定性过滤 + filter audit trail + 表格双表示（raw_html 原样保存、embedding_text 由 HTML 确定性转换，OCR 错误不修复）。
- P1-clean 人工质量门禁：14 个代表性页面全部通过（表格/表头/行列/步骤/警告/目录/正文/页码/OCR 可追溯）。
- 无 LLM 指标（详见 [phase-3A-mineru-adapter-cleanup-report.md](phase-3A-mineru-adapter-cleanup-report.md)）：
  - token_reduction：2196 **38.5%**、t1739cn **36.5%**
  - chunk_reduction：2196 **68.0%**、t1739cn **40.2%**
  - P1-clean 重复 chunk 0、>700 token 0；表格 39+15 全部保留。

### 固定模型 Token 预检（真实 LightRAG 流程，qwen-plus-2025-07-28）

| 组 | 样本 | 索引 Token（20 chunk） | 每 chunk Token | 4 题查询 Token |
|---|---|---|---|---|
| P0（PyMuPDF） | 20 | 53,514 | 2,676 | 6,618 |
| P1（MinerU-clean） | 20 | 56,097 | 2,805 | 4,643 |

- 53 次真实 LLM 调用，0 重试、0 模型不匹配、0 错误；requested_model == actual_model == `qwen-plus-2025-07-28`，fallback 关闭。
- 样本说明：P0/P1 各 20 个 ChildChunk、各 4 个黄金问题（S001/S007/S011/S015），使用真实完整 LightRAG Prompt；53 次真实调用，0 模型不匹配；估算包含 8% merge 与 20% 安全余量。
- 估算（含 merge 8% + 20% 安全余量）：
  - P0 全量索引 ≈ 1,212,092
  - P1-clean 全量索引 ≈ 516,092
  - 查询 ≈ 140,763
  - **合计 ≈ 2,408,642 token > 1,000,000 → blocked_insufficient_quota**
- 预检阶段因此未启动全量索引（免费额度不足）；**付费授权后已正式完成全量运行**（见 §3c），实际消耗 P0 1,229,383 + P1 602,176 token。
- **2,408,642 是估算值，不是最终实际计费值**；正式运行实际合计约 183 万 token（费用 SDK 未提供，N/A）。

### 配置一致性门禁

`fixed_model/config.json` 冻结，8 项 hash（chunk/embedding/index_llm/query_llm/prompt/retrieval/qdrant/golden）P0=P1 全部一致，唯一变量 `parser_pipeline`；golden set SHA256 与冻结值一致。

---

## 15. 测试与 Ruff

```text
python -m pytest --collect-only -q   -> 388 collected（Phase 3 369 + Phase 3A 新增 19）
python -m pytest -q                  -> 373 passed, 15 skipped, 0 failed
python -m ruff check .               -> All checks passed
```

**Phase 3A-P/R 更新后**：`python -m pytest --collect-only -q` → **404 collected**；`python -m pytest -q` → **389 passed, 15 skipped, 0 failed**；`ruff check .` → All checks passed。

**Phase 3A-R-Paid 运行后**：`python -m pytest --collect-only -q` → **417 collected**；`python -m pytest -q` → **402 passed, 15 skipped, 0 failed**；`ruff check .` → All checks passed（原测试无退化）。

- Phase 3A 新增测试：19（parser comparison）+ 16（MinerU Block Policy / 固定模型门禁），见 `tests/test_parser_backend_comparison.py`、`tests/test_mineru_block_policy.py`。
- 真实 MinerU 测试：1（`IRA_MINERU_REAL=1` opt-in；本阶段已用 smoke + P1 真实调用完成验证，测试本身未重复消耗额度）。
- parser comparison 测试：18 项（目录隔离、PDF hash、chunk 参数一致、P1 strict manifest、ZIP hash、页面覆盖率、token 分布、Recall@K、MRR、Gold Page Recall、黄金集未修改、mapping 独立等）。
- skipped 15 = 11（Qdrant opt-in）+ 1（真实 MinerU opt-in）+ 3（P1 检索结果待生成）。

---

## 16. 文件变更

### 新增实验资产（不入库二进制）

- `evaluation/experiments/parser_backend/`：config、common、quality、metrics、parse_p0、parse_p1、index_retrieve
- 实验产物：`P0_*/`、`P1_*/`（含 mineru_raw ZIP/pages，磁盘保存）、`retrieval/`、`comparison/`
- `scripts/smoke_mineru.py`（编码修复）
- `tests/test_parser_backend_comparison.py`

### 生产修复（Phase 3 遗留真实缺陷）

1. `index_service.py`：多文档 KB 的 ainsert input/ids/file_paths 按文档对齐（原实现仅单文档可用）。
2. `physical_qdrant_storage.py`：upsert 按 `embedding_batch_num` 分批 embedding（DashScope 批量上限 20）。
3. `config.py` / `lightrag_service.py`：`chunk_token_size` 可配置（默认 1600 不变），避免 MinerU 大块触发硬编码上限。
4. `smoke_mineru.py`：stdout UTF-8，修复 GBK 控制台崩溃。

上述修复已用 Phase 3 E2E 回归验证（2 passed）。

---

## 17. 已知限制

- 正式 P0/P1 检索指标已产生（付费运行），但 **Answer Correctness / Faithfulness 标记 N/A**：为避免额外 Judge 费用，未运行 LLM Judge；检索与确定性引用指标完整。
- 旧 P0（历史非固定模型基线）查询中途发生模型降级，回答类指标 N/A；该基线不用于最终公平对比（LightRAG 图谱抽取与 mix 查询可能受 LLM 变化影响）。
- 标题/步骤/警告统计为行级启发式，人工抽查覆盖 14 页代表性页面。
- 页眉、页脚、页码与重复 page_footnote 已完成确定性清洗（P1-clean）；HTML 表格已实现 raw_html 与 embedding_text 双表示；OCR 原始字符错误未自动修复；P1-clean 已通过人工质量门禁并有完整 RAG 指标。
- MinerU-clean 的 heading 分组导致跨页 parent（p22-24、p27-39），child page_start 偏移，是 P1 Gold Page Recall 偏低与 4 条 evidence unmapped 的主因（内容未丢失）。
- 未修改生产默认解析器；未重新解析正式 KB。
- 付费运行实际消耗：P0 1,229,383 token + P1 602,176 token（合计约 183 万 token；人民币费用 SDK 未提供，标记 N/A，不编造金额）。
- 实验临时 Qdrant Collection 与临时 workspace 已按登记名称精确清理（Qdrant 现存 0 collection）；缓存、checkpoint、逐题结果与指标均保留。

---

## 18. 是否允许进入 Phase 4

**是（Phase 3A complete）。** P0/P1 完整索引、同一固定模型、无 fallback、两组各 50 题、黄金集未修改、独立 Evidence Mapping、完整公平指标、测试与 Ruff 通过、临时资源精确清理——全部满足。允许进入 **Phase 4：混合检索、Parent 扩展与 Rerank 对比**；**不自动实施**，需用户另行发起。
