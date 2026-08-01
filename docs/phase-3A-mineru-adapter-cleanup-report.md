# Phase 3A-P 报告：MinerU 确定性适配器清洗（P1-raw → P1-clean）

**日期**: 2026-08-01
**分支**: `codex/knowledge-qa-platform-design`

---

## 1. 当前 Adapter 根因

原 `_extract_pages_from_mineru_zip` 把 content_list 中**所有带文本的项**直接拼进页面 Markdown，导致：

- `header`（52/121 项）、`footer`（53/72 项）、`page_number`（50/60 项）作为正文每页重复 → 页眉页脚样板进入 Embedding，产生重复 chunk（P1-raw 2196：46 个重复 chunk id / 58 次重复）；
- t1739cn 的 `page_footnote`（169 项，公司地址片段）重复混入正文；
- 表格 `table_body` 原始 HTML 直接进 Embedding（2196 约 25,095 个 HTML 标记 token）；
- `image` 项的 `image_caption` 未被使用（2196 的 39 张图仅 9 个有标题，其中多数为空）；
- 没有任何结构信息（text_level 等被丢弃），标题/警告/步骤只能靠文本启发式猜测。

## 2. content_list 实际 schema（本机两份真实文件）

字段：`type`、`text`、`html`（本批无）、`bbox`、`page_idx`、`text_level`（text 项）、`table_body`/`table_caption`/`table_footnote`（table 项）、`image_caption`/`image_footnote`/`img_path`（image 项）。

| type | 2196 | t1739cn | 说明 |
|---|---|---|---|
| text | 342 | 576 | 正文/标题/步骤/警告；`text_level` 1=标题、2=小标题/标签 |
| header | 52 | 121 | 页眉（重复） |
| footer | 53 | 72 | 页脚（重复） |
| page_number | 50 | 60 | 页码（2196 部分为空文本） |
| page_footnote | 0 | 169 | 版式脚注（公司地址片段，重复） |
| table | 39 | 24 | `table_body` 为 HTML 字符串；9 个 t1739cn 表格 `table_body=None`（图片式表格） |
| image | 39 | 86 | 仅 `img_path`/caption |

## 3. 过滤规则（MinerUBlockPolicy，全确定性）

- 丢弃：`header`、`footer`、`page_number`（reason=`page_layout_boilerplate`）
- 丢弃：空文本块（`empty_block`）、图片式表格（`table_without_text_body`）、无文本图片（`image_without_text`）
- `page_footnote`：归一化文本出现在 ≥3 个不同页 → 判定为重复版式脚注并丢弃（`repeated_page_footnote_layout`）；唯一脚注保留
- 保留：正文、标题（text_level 1；text_level 2 且短、非标签）、列表、步骤、警告、表格（raw_html + embedding_text）、公式、图片说明、故障内容
- 每个被过滤块写入 `cleanup_manifest.json`：document、page_number、block_type、block_hash、filter_reason
- 规范化仅限：NFC、换行统一、行内空格压缩、空行折叠；**无摘要/改写/OCR 修复/LLM**

## 4. 表格双表示

- `raw_html`：MinerU 原始 HTML 原样保存（`tables_clean.json`），用于引用/前端/人工检查；
- `embedding_text`：确定性 HTML→文本（stdlib `HTMLParser`），展开 rowspan/colspan 为可读行列，保留表格标题/表头/数值/单位/备注；OCR 原文（如 `ACO`、`$1 5 0 ^ { \circ }$`）**原样保留，不做自动纠正**。

示例（2196 p11 表1）：

```text
表格标题：1
列： | 泵型号
行1： | STO | MTO | LTO | XLO | XLO-17
行2：填料尺寸 | 5/16" | 3/8" | 3/8" | 7/16" | 7/16"
行3：填料环数量 | 5
```

## 5. P1-raw / P1-clean 指标

| 指标 | 2196 raw | 2196 clean | t1739cn raw | t1739cn clean |
|---|---|---|---|---|
| 字符数 | 68,146 | 30,149 | 46,989 | 22,180 |
| Token 数 | 36,992 | 22,748 | 26,346 | 16,722 |
| ParsedBlock | 530 | 385 | 1,009 | 600 |
| Parent | 234 | 59 | 136 | 90 |
| Child | 278 | 89 | 159 | 95 |
| 重复 chunk id | 46 | 0 | 0 | 0 |
| 重复出现次数 | 58 | 0 | 0 | 0 |
| Child token mean/P50/P95/max | 137/31/494/1565 | 265/356/513/680 | 170/55/467/1494 | 178/114/471/573 |
| Child <120 token | 210 | 33 | 98 | 48 |
| Child =1 token | 11 | 3 | 1 | 0 |
| Child >700 token | 8 | 0 | 3 | 0 |
| HTML 标记 token | 25,095 | 0（raw_html 移出 Embedding） | 10,045 | 0 |
| 有效页 | 53 | 51（2 空白页仅含样板） | 61 | 55（6 页仅含样板/页脚） |
| 表格保留 | 39 | 39 | 24（含 9 个图片式） | 15 文本表格 + 9 个图片式明确记录 |
| 操作步骤行 | 142 | 142 | 186 | 186 |
| 安全警告 | 34 | 47 | 96 | 106 |

## 6. Token 减少比例

```text
token_reduction (2196)   = (36992 - 22748) / 36992 = 38.5%
token_reduction (t1739cn)= (26346 - 16722) / 26346 = 36.5%
```

## 7. Chunk 减少比例

```text
chunk_reduction (2196)   = (278 - 89) / 278 = 68.0%
chunk_reduction (t1739cn)= (159 - 95) / 159 = 40.2%
```

## 8. 人工页面复核

复核页面：2196 p5、p9、p11、p14、p15、p17、p18、p23、p25、p27；t1739cn p24、p26、p31、p32。

结论：**通过**。

- 表格仍存在（39+15），表头/行列关系保留（含 rowspan/colspan 展开）；raw_html 可读取；
- 步骤顺序未改变（启动 1-8、拆卸 1-9/16-18、装配 1-4 等）；
- 警告正文未丢失；目录（p5）完整未被误删；正文（含 t1739cn p24 公式）未丢失；
- 页码未错位；OCR 原始错误可追溯且未修复（`ACO`、`$1 5 0 ^ { \circ }$`、`请参阅3`、`表 2`→`2 所示信息` 均原样保留）。

## 9. 预计模型调用和 Token 节省

基于固定模型预检实测（每 chunk 约 2,676–2,805 token 总消耗）：

- P1-raw 全量 437 child 预计索引 Token ≈ 437 × 2,805 ≈ **122.6 万**
- P1-clean 全量 184 child 预计索引 Token ≈ 184 × 2,805 ≈ **51.6 万**
- 预计节省 ≈ **71 万 token（约 58%）**，同时 Child 数减少 68%/40%，Embedding 调用与 Qdrant point 数同步下降。

## 10. 是否使用 P1-clean

**是。** P1-clean 通过人工质量门禁与无 LLM 指标检查，作为正式 P1 组（`mineru_online_clean`）。P1-raw 保留为 Adapter 消融组，不作为正式对比。

## 11. 测试结果

新增 `tests/test_mineru_block_policy.py`（16 项）：normalize、表格双表示、OCR 不自动纠正、header/footer/page_number 过滤、正文/警告/唯一脚注保留、重复脚注过滤、filter audit trail、raw_html 保留、table_without_text_body 记录、固定模型配置与 hash 门禁、预检模型一致性、P1-clean 无孤儿/无超限、raw 产物 hash 未变、historical 标记。

```text
pytest tests/test_mineru_block_policy.py  -> 16 passed
```

## 12. 文件变更

- 新增：`evaluation/experiments/parser_backend/mineru_adapter.py`、`fixed_model/config.json`、`fixed_model_gate.py`、`fixed_model_llm.py`、`fixed_model_run.py`、`tests/test_mineru_block_policy.py`
- 新增产物：`fixed_model/P1_mineru/<pdf>/*`（P1-clean）、`mineru_adapter/comparison/*`、`manifests/artifact_sha256_manifest.json`
- 修改：`config.py`（`model_fallback_enabled`）、`lightrag_service.py`（可注入 LLM 函数）、`quality.py`（block-type 表格统计）、`index_retrieve.py` 相关清理
- P1-raw / MinerU 原始 ZIP / content_list / pages 未改动（hash 校验通过）

## 13. 已知限制

- 结构统计（步骤/警告）为行级启发式；表格为硬证据。
- 9 个图片式表格无文字内容，仅记录，不生成 Embedding 文本。
- P1-clean 页数少于 raw（纯样板页剔除）；PDF 空白页（2196 p2/p4、t1739cn p2）两组一致无正文。
- 固定模型全量实验被配额预检阻塞（见 Phase 3A 最终报告），P1-clean 的 RAG 指标尚未产生。
