# MINERU-PROVENANCE-001：MinerU-clean 页级 provenance 元数据归属问题

**状态**: 登记（open）
**来源**: Phase 3A-R-Paid 正式公平实验（2026-08-01）

## 问题描述

当前 MinerU-clean 解析管线的页级 provenance 元数据归属存在偏移：

- MinerU-clean 的 heading 分类（基于 `text_level` 的确定性规则）产生的父分组跨越多个 PDF 页（例如 2196 的 p22-24、p27-39 组）；
- StructuredChunker 的 `ParentChunk.page_start` 取分组内第一个 block 的页码，`ChildChunk.page_start` 继承 parent 的起始页；
- 因此部分实际位于 p23/24/28/29（2196）与 p22（t1739cn）的内容，其 child 的 page_start 被记为其 parent 起始页（p22、p27 等）。

**这不是内容丢失**：`pages_clean.json` 与人工页面复核确认正文、表格、警告均在；受影响的是**页码归属元数据**。

## 受影响页面

- 2196-ANSI-Manual-Chinese.pdf：p23、p24、p28、p29（内容落入 p22-24 / p27-39 跨页 parent）
- t1739cn.pdf：p22（内容落入跨页 parent）

## 触发条件

1. MinerU-clean Adapter 将 `text_level==2` 的短行分类为 heading；
2. 跨页内容前存在 heading 且后续多页无新的 heading 边界；
3. StructuredChunker 以 heading 分组并让 child 继承 parent 起始页。

## 当前影响（正式固定模型实测）

| 指标 | P0 (PyMuPDF) | P1 (MinerU-clean) | 差异 |
|---|---|---|---|
| Gold Page Recall | 0.7917 | 0.6667 | −12.5pp |
| Citation Accuracy | 0.8958 | 0.7917 | −10.4pp |
| Evidence Mapping（70 条证据） | 70 exact / 0 unmapped | 61 exact + 5 fuzzy / 4 unmapped | — |
| 故障诊断类 evidence recall | 0.6667 | 0.3333 | −33pp |

具体表现：4 条黄金证据因页级过滤无法映射（只能模糊映射或 unmapped）；引用中的页码与黄金页码不一致，拉低 Citation Accuracy。

## 可选修复方向

1. **块级页码传播**：在 Adapter 阶段把每个 ParsedBlock 的 `source_page` 作为子块 provenance 保存，child 生成时按 block 页范围（而非 parent 起始页）写入 `page_start/page_end`；
2. **分组内页码细分**：允许 parent 记录 `page_min/page_max`，child 继承实际 block 页；
3. **映射层改进**：Evidence Mapping 使用内容覆盖 + block 页联合判定（已部分实现 fuzzy 兜底），并人工确认。

## 是否阻塞 Phase 4

**不阻塞。** Phase 4 默认解析器为 PyMuPDF（`pymupdf_standard_adapter`），不存在该问题；在重新评估 MinerU 作为默认解析器之前应修复本项。
