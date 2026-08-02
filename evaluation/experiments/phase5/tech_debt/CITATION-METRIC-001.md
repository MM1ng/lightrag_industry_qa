# CITATION-METRIC-001: Citation 指标命名与定义审计

## 审计结论

Phase 4D-R2 阶段二中的 `Citation Accuracy`（0.8958）与 `Unsupported Citation Rate`（0.6691）不是互补关系：

- `Citation Accuracy` 是 per-question 指标（至少 1 条引用命中黄金页才算正确）。
- `Unsupported Citation Rate` 是 per-citation 指标（错误引用引用数 / 总引用数）。
- 两者的分母与计算单位不同，因此不互补；继续使用旧名称容易混淆。

## 重命名（保留历史值）

| historical_name | canonical_name | 单位 |
|---|---|---|
| Citation Accuracy | answer_citation_accuracy | per-question |
| Citation Precision | answer_citation_precision | per-question mean |
| Citation Recall | answer_citation_recall | per-question mean |
| Citation Traceability | citation_traceability | per-question |
| Unsupported Citation Rate | non_gold_citation_reference_rate（historical: unsupported_citation_reference_rate） | per-citation |
| （新增） | gold_citation_reference_rate | per-citation（与 non_gold 同分母互补） |
| Unsupported Answer Rate | negative_unsupported_answer_rate | per-question (N=2) |

历史 Phase 4 指标原值未修改；`metrics_definition.json` 同时保留 historical_name 与 canonical_name，并输出原始计数。

## 原始计数（Phase 4D-R2 CN0 基线答案文件）

- **answer_citation_accuracy**（historical: Citation Accuracy）: 43 / 48 = 0.8958
- **answer_citation_precision**（historical: Citation Precision）: 16.1667 / 48 = 0.3368
- **answer_citation_recall**（historical: Citation Recall）: 37.9167 / 48 = 0.7899
- **citation_traceability**（historical: Citation Traceability）: 48 / 48 = 1.0
- **citation_traceability_emitted**（historical: None）: None / None = None
- **unsupported_citation_reference_rate**（historical: Unsupported Citation Rate）: 93 / 139 = 0.6691
- **false_rejection_rate**（historical: False Rejection Rate）: 14 / 48 = 0.2917
- **insufficient_evidence_rejection_rate**（historical: Insufficient Evidence Rejection Rate）: 2 / 2 = 1.0
- **negative_unsupported_answer_rate**（historical: Unsupported Answer Rate）: 0 / 2 = 0.0
- **answered_without_evidence_rate**（historical: None）: 0 / 48 = 0.0
