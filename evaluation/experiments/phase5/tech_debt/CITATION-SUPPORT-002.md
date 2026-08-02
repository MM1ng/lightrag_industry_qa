# CITATION-SUPPORT-002: unsupported_citation_reference_rate 命名修正

## 问题

`unsupported_citation_reference_rate`（Phase 5 曾用名）实际只衡量：**发出的引用中，未命中人工黄金证据（document, page）标注的引用比例**。

该指标没有确定性证明"引用文本无法支持回答中的 Claim"。没有 Claim Support Judge 时，不能从"未命中黄金标注"推出"引用不支持答案"。

## 修正

canonical 名称改为：

```text
non_gold_citation_reference_rate
```

保留：

```text
historical_name = unsupported_citation_reference_rate
```

## 明确声明

- non-gold 不等于 unsupported；
- 黄金证据可能不是穷尽标注；
- 没有 Claim Support Judge 时，不得声称引用一定不支持答案；
- 该指标只用于衡量引用与黄金标注的一致性。

## 互补指标

```text
gold_citation_reference_rate =
    gold_matching_citation_count / emitted_citation_count

gold_citation_reference_rate + non_gold_citation_reference_rate = 1.0
```

互补恒等式仅在**相同分母（emitted_citation_count）**下成立。

## 历史值

Phase 4D-R2（R1 臂）：non_gold=93/139=0.6691；gold=46/139=0.3309。历史原始值未修改。
