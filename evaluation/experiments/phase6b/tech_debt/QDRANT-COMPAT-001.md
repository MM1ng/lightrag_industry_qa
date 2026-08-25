# QDRANT-COMPAT-001: qdrant-client / server version mismatch

## 现状

- qdrant-client = 1.18.0
- Qdrant Server = 1.13.6（容器 `ira-phase3-qdrant-test`）
- 运行时出现兼容性警告：`Major versions should match and minor version difference must not exceed 1`。

## 影响

- 功能测试（Phase 4 冻结索引查询、Phase 6 E2E、生命周期回归）全部通过；
- 本阶段审计确认：该版本差异与 Citation Accuracy 0.8333→0.7708 差距无因果关系；
- 本阶段不升级 Qdrant。

## 后续

升级需独立执行：备份数据卷 → 升级镜像 → 兼容性回归（检索/E2E/生命周期）→ 回滚预案 → 独立报告。不得与 Phase 6B 修复混在一起。
