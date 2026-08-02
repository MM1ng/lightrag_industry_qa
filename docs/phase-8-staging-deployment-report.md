# Phase 8 报告：RC Tagging & Controlled Staging Deployment（阻塞）

**日期**: 2026-08-02
**分支**: `codex/knowledge-qa-platform-design`
**HEAD**: `5051647ee2e377e3ea94f70eca1c0eded832e42b`
**RC 版本**: `0.1.0-rc.1`
**阶段结论**: **Phase 8 被阻塞**——`IRA_PHASE8_TARGET_ENV` 未配置，无部署目标；未创建/推送 Tag；未执行任何暂存部署。

---

## 1. 阶段结论

Phase 8（RC Tagging & Controlled Staging Deployment）在 Phase 7 Closeout 完成后启动，但因缺失部署目标环境变量而**立即阻塞**：`IRA_PHASE8_TARGET_ENV` 未设置。允许值为 `local_staging` / `remote_staging`；禁止默认选择 remote_staging，禁止从 Git 配置、SSH 历史或本机文件猜测服务器。未执行 Tag 创建、暂存部署、部署后验收、回滚演练；`staging_deployment_approved=false`，`production_deployment_performed=false`。

## 2. Git commit

当前 HEAD：`5051647ee2e377e3ea94f70eca1c0eded832e42b`（Phase 7 Closeout + 本报告）。

## 3. Phase 7 指标分母修正

已完成（见 `docs/phase-7-release-packaging-report.md` 第 31 节与 `evaluation/experiments/phase7/closeout/acceptance_metric_correction.json`）：可回答题指标分母从 20 修正为 18，负样本指标分母为 2。

## 4. 历史与 canonical 指标

| 指标 | 历史（superseded） | Canonical |
|---|---|---|
| answer_citation_accuracy | 14/20=0.70 | 14/18=0.7778 |
| false_rejection_rate | 6/20=0.30 | 4/18=0.2222 |
| insufficient_evidence_rejection_rate | 2/2=1.0 | 2/2=1.0 |
| negative_unsupported_answer_rate | 0/2=0 | 0/2=0 |
| citation_traceability_emitted | 1.0（14/14） | 1.0（14/14） |

## 5. Phase 7 Release Gate 复评

全部通过：Phase 6B Closeout 通过、32→29 门禁映射完整、RC 包 SHA256 有效、Secret confirmed=0、冷/温启动/优雅停止/故障恢复/备份恢复演练通过、Smoke 6/6、Prompt Injection 安全项=0、20 题完整、answerable 分母=18、N001/N002 正确拒答、citation_traceability_emitted=1.0、request_id/trace_id 完整率=1.0、fallback=0、error rate=0。`release_package_approved=true`。

## 6. 最终测试结果

`546 collected / 534 passed / 12 skipped / 0 failed`（20.61s），skip 全部为外部 opt-in（MinerU 1、Qdrant E2E 2、Qdrant integration 9）；`ruff check .` 全部通过。

## 7. RC 包类型

`application_release_candidate`，非自包含：不含数据库/Qdrant 索引/冻结索引/LLM 缓存/用户文档；依赖宿主机 Qdrant、数据库、环境变量、模型 API 与冻结 KB 或测试 KB；安装模式 `local_conda_application_with_docker_qdrant`。

## 8. Phase 7 Closeout 决策

`phase8_allowed=true`（canonical 分母修正、原始结果未覆盖、门禁复评通过、pytest 结果明确、Ruff 通过、package_type 已声明、Secret=0、release_package_approved=true、deployment_performed=false）。

## 9. Phase 8 目标环境

**阻塞**：`IRA_PHASE8_TARGET_ENV` 未配置。未选择任何目标；不猜测 remote_staging 主机。

## 10. Tag 状态

未创建、未推送。`v0.1.0-rc.1` 不存在于仓库；`IRA_PHASE8_CREATE_TAG` / `IRA_PHASE8_PUSH_TAG` 均未设置，无显式授权。

## 11. RC 包验证

ZIP `dist/industrial-energy-agent-0.1.0-rc.1.zip`：SHA256=`c6ea0531...8efe59`、187,429 字节，与 checksum/release manifest 一致；冻结策略 SHA256=`f468b7af...`、candidate pool SHA256=`fc731efc...` 一致；confirmed_secret_count=0；package_type 已声明。

## 12. 暂存配置

**未生成**（`staging_config_manifest.json` 未创建）：无部署目标，无法声明端口/Endpoint/数据库。

## 13. 部署前备份

**未执行**：无暂存环境可备份。

## 14. 部署步骤

**未执行**：未上传/解压 RC 包、未迁移、未启动服务。

## 15. 健康检查

**未执行**（/health、/ready、/version 未在暂存环境验证）。

## 16. Smoke Test

**未执行**；Phase 7 既有 smoke 证据（6/6 HTTP 200）保留。

## 17. Prompt Injection

**未执行**；Phase 7 既有 robustness 证据（12 条，安全项 0）保留。

## 18. 黄金子集 canonical 指标

Phase 7 修正后 canonical 结果：answer_citation_accuracy=14/18=0.7778、false_rejection_rate=4/18=0.2222、insufficient_evidence_rejection_rate=2/2=1.0、negative_unsupported_answer_rate=0/2=0、citation_traceability_emitted=1.0、HTTP 成功率=1.0、request_id/trace_id 完整率=1.0、P95=2.72s、error rate=0、fallback=0。暂存部署后需按同一 20 题子集重跑并对比（本次未执行）。

## 19. 可观测性

**未执行**（无部署日志可审计）。

## 20. 日志 Secret 扫描

**未执行**（无部署日志）。

## 21. 回滚演练

**未执行**。

## 22. Phase 8 门禁

**未评估**：部署目标缺失，门禁在授权目标环境配置后才能执行；不为进入部署而放宽任何门禁。

## 23. staging_deployment_approved

**false**。

## 24. production_deployment_performed

**false**；未自动部署生产环境。

## 25. Tag 是否创建

**false**。

## 26. Tag 是否推送

**false**。

## 27. 已知限制

- `IRA_PHASE8_TARGET_ENV` 未配置 → Phase 8 阻塞；所需部署/验收/回滚产物未生成（未编造）；
- 暂存部署需独立目录、独立端口、独立测试数据库；Qdrant 仅可复用宿主机只读冻结 KB 或独立测试 KB，不得修改正式集合；
- Tag 创建需 `IRA_PHASE8_CREATE_TAG=1`，推送需 `IRA_PHASE8_PUSH_TAG=1`。

## 28. 下一阶段是否允许

Phase 8 尚未完成；配置 `IRA_PHASE8_TARGET_ENV`（local_staging，或 remote_staging 且补齐 `IRA_PHASE8_STAGING_HOST/USER/PATH` 与 `IRA_PHASE8_DEPLOY_STAGING=1`）后重跑。未获得显式授权前不得创建/推送 Tag，不得部署 production，不得自动进入下一阶段。

---

## 最终决策

```json
{
  "phase7_closeout_completed": true,
  "phase8_status": "blocked",
  "staging_deployment_approved": false,
  "production_deployment_performed": false,
  "release_tag_created": false,
  "release_tag_pushed": false,
  "blocked_reason": "IRA_PHASE8_TARGET_ENV is not configured"
}
```
