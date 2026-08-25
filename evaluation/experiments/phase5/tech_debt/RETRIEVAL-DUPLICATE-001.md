# RETRIEVAL-DUPLICATE-001: frozen candidate pool duplicate chunk rows

## 概述

- 范围：全部 50 题冻结候选（998 行）。
- 重复行总数：4。
- 受影响问题：4 题（['C003', 'C004', 'C007', 'C008']）。

## 重复来源

mix query merges multiple recall channels (local/global/naive) into chunk_top_k=20; the same chunk_id can be recalled by several channels at different ranks. The Phase 4 freeze step did not deduplicate by chunk_id, so the frozen pool keeps duplicate rows with identical text_hash/document/page.

RRF/mix 合并去重：Observed rows share chunk_id/text_hash/page but differ in rank; consistent with an RRF/mix merge that lacked a final chunk_id deduplication pass.

## 对各项结果的影响

- 是否影响 Citation Precision：同一 chunk 被重复召回不会直接改变引用计数，但会占用上下文名额，可能挤掉其他候选，间接影响证据选择。
- 是否影响上下文权重：重复文本在 context 中会重复出现，放大该 chunk 的 token 权重。
- 是否影响安全问题：可能使安全相关 chunk 的重复行掩盖其他安全证据。
- 是否影响 Rerank：qwen3-rerank 按输入行逐一返回，重复行被完整保留（Phase 4D-R2 按多重集合判定完整性）。

## 处理决定

- 本阶段仅在答案上下文组装层稳定去重（`stable_unique_fill`）。
- 不修改 frozen candidate pool。
- 不重写 Phase 3A/4 历史结果。

## 逐题明细

### C003（20 行 / 19 唯一）
- `cchunk-pymupdf-v1-护手册-e05e769c5e5d-000-e05e769c5e5d`：首次 rank 3，重复 rank 5；page 24；text_hash 一致=True

### C004（20 行 / 19 唯一）
- `cchunk-pymupdf-v1-护手册-e05e769c5e5d-000-e05e769c5e5d`：首次 rank 9，重复 rank 11；page 24；text_hash 一致=True

### C007（19 行 / 18 唯一）
- `cchunk-pymupdf-v1-护手册-e05e769c5e5d-000-e05e769c5e5d`：首次 rank 2，重复 rank 5；page 24；text_hash 一致=True

### C008（20 行 / 19 唯一）
- `cchunk-pymupdf-v1-护手册-e05e769c5e5d-000-e05e769c5e5d`：首次 rank 14，重复 rank 16；page 24；text_hash 一致=True
