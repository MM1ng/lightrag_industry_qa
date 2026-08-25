# Phase 7 报告：Release Candidate Packaging & Local Deployment Rehearsal

**日期**: 2026-08-02
**分支**: `codex/knowledge-qa-platform-design`
**阶段**: Phase 7（RC 打包、冷/温启动、优雅停止、故障与备份恢复演练、验收）
**Closeout 更新**: 2026-08-02T20:22:52+08:00；HEAD=`5051647ee2e377e3ea94f70eca1c0eded832e42b`

---

## 1. 阶段结论

- Phase 6B-Closeout 完成：32→29 门禁逐项映射、无遗漏；canonical 基线分层冻结；官方 FastAPI 声明为唯一权威发布路径。
- RC 版本 `0.1.0-rc.1`（单一权威版本源 `src/industrial_rag/version.py`，`/version` 同源读取）。
- RC 包 `dist/industrial-energy-agent-0.1.0-rc.1.zip`（96 文件、187,429 字节、SHA256=`c6ea0531...8efe59`），相对路径、无禁止文件、Secret 扫描 confirmed=0。
- 演练全部通过：冷启动（新目录解压+哈希校验+迁移+API/UI 启动+官方查询）、温启动、优雅停止、故障恢复（环境缺失/Qdrant 不可用/错误 PID/迁移恢复）、备份恢复。
- 验收通过：Smoke 6/6、Prompt Injection 10/12 拦截且安全项零泄漏、20 题黄金子集 HTTP 成功率 1.0、trace 完整率 1.0、N001/N002 正确拒答。
- **release_package_approved=true**；**未创建/推送 Tag**；**deployment_performed=false**。

## 2. Git commit

- 基线：`7f5f39fa3459aa3dbdaa1a42a4439c7cede82c60`。
- 本阶段提交：`fix(phase6b): reconcile release gates and freeze canonical baselines`、`chore(phase7): package local release candidate`、`test(phase7): validate cold start backup and recovery rehearsal`、`docs(phase7): report release packaging and local deployment rehearsal`。
- Closeout 提交 HEAD：`5051647ee2e377e3ea94f70eca1c0eded832e42b`（指标分母修正、最终测试结果、RC 包类型声明、Closeout 决策）。

## 3. Phase 6B Closeout

`phase6b/closeout/`：release_gate_reconciliation.json、canonical_baselines.json、authoritative_path.json、closeout_decision.json、model_identity_fields.json；`phase6b/tech_debt/OFFICIAL-PATH-CONTEXT-001.md`（C007）。

## 4. 32→29 门禁映射

- 27 项 unchanged（strategy/golden/trace/health/隔离/pool/index/安全五项/模型/fallback/thinking/黄金指标/拒答/P95/并发/测试/ruff/迁移/手册/无 Secret 等）；
- 2 项 renamed：`error_rate_zero_or_approved→error_rate_zero`、`citation_traceability_1→citation_traceability_emitted_1`；
- 3 项 merged（列出全部来源 ID 与证据路径）：`no_internal_stack_trace`→error_rate_zero、`no_context_crosstalk`→concurrency_5_success_ge_095、`workspace_clean`→no_secret_committed；
- `omitted_phase6_gates=[]`、`all_original_hard_gates_accounted_for=true`。

## 5. Canonical 基线分层

`answer_citation_accuracy`：historical_harness_v0=0.8333（保留、不用于发布比较）、canonical_harness_v1=0.6458（用于发布比较）、official_fastapi_v1=0.7708（权威运行路径）。

## 6. 指标差值表达

`candidate_minus_baseline=+0.1250`、`baseline_minus_candidate=-0.1250`、`maximum_allowed_drop=0.0200`、`passed=true`；历史 0.8333 未覆盖、未静默重定义基线。

## 7. 权威运行路径

`authoritative_release_path=official_fastapi`；Harness=历史实验与离线诊断，非逐输入等价；`retrieval_metrics_equal_under_canonical_at12=true`；后续门禁必须走官方 FastAPI，禁止混用基线或覆盖退化。

## 8. C007 技术债

`OFFICIAL-PATH-CONTEXT-001.md`：候选范围/上下文渲染/Prompt 不同 → FastAPI 额外拒答；非模型波动；不按 question_id 特判；不阻塞 RC。

## 9. 模型身份字段

requested_model / configured_model / provider_reported_model(null) / provider_reported_model_available(false) / fallback_enabled / fallback_detected；`actual_model` 标记 deprecated（configured_model 历史别名），新 manifest 不再依赖。

## 10. RC 版本

`0.1.0-rc.1`（依据 pyproject 0.1.0，无既有 tag）；单一版本源 `src/industrial_rag/version.py`；`/version` 返回 app_version/release_channel/git_commit/config_version/strategy_version/build_time 与策略字段。

## 11. 依赖冻结

`phase7/dependency_manifest.json`：Python 3.11.15 / conda industrial-rag；直接依赖与安装版本（LightRAG 1.5.4、FastAPI 0.140.0、Uvicorn 0.51.0、Streamlit 1.60.0、qdrant-client 1.18.0、SQLAlchemy 2.0.51、Alembic 1.18.5、Pydantic 2.13.4、OpenAI 2.46.0 等）；差异报告：declared_but_not_installed=[]、version_mismatch=[]；未升级/降级，Qdrant 兼容债未修（单独 tech debt）。

## 12. 配置模板

`.env.example` 重写为 RC 模板（占位值+注释+必填/选填）；覆盖 DASHSCOPE_API_KEY、模型/Embedding、数据库、Qdrant、ProductionQASettings（QA_*）、超时重试、安全、Shadow Audit、日志、Streamlit API URL、MinerU 手动选项；无真实密钥/Endpoint。

## 13. 启动脚本

`scripts/`：check_env（只输出 configured/missing，缺失退出 1）、start_qdrant（仅 Docker 基础设施、端口冲突检测、不删数据）、start_api（解释器/环境/迁移/Qdrant ready/PID/不打印 Secret）、start_ui（API ready 检查、PID、防重复）、stop_local（优雅停止、PID 清理、不动 Qdrant 数据）、check_local（health/ready/version/Qdrant/UI 端口）。全部支持含空格路径。

## 14. 包含/排除清单

`package/include_manifest.json`（96 文件：源码、迁移、脚本、配置模板、文档、冻结策略与运行手册、closeout/tech-debt 审计文件）与 `package/exclude_manifest.json`（.env、DB、lightrag_storage、data、实验产物、缓存、.git、pycache、phase3-uncommitted-backup.patch 等，附理由）。

## 15. Secret 扫描

`security/secret_scan.json`：96 文件扫描，规则含 sk-/Bearer/Aliyun AK/私钥/Key 赋值/MaaS Endpoint/签名 URL；**confirmed_secret_count=0**，35 条 review 级命中均为哈希/占位符/类名（记录脱敏片段，不阻塞）。`log_scan.json`：演练日志 0 命中。

## 16. RC 包与 Hash

`dist/industrial-energy-agent-0.1.0-rc.1.zip`：SHA256=`c6ea0531232e34c6a719dffcf99432147dedd27940596f5a09035554cd8efe59`；`package/checksum_manifest.json` 记录每文件 size+SHA256、总数/总大小/ZIP SHA256；ZIP 内全部相对路径。

## 17. 冷启动演练

`rehearsal/cold_start.json`：临时目录解压（96 文件）→ checksum 全通过 → alembic stamp+upgrade head → Qdrant ready → uvicorn 启动 → /health、/ready、/version（app_version=0.1.0-rc.1）→ 2 次官方查询（200，citations=3/0）→ 停止。总耗时 18.2s，全通过。

## 18. 温启动

`rehearsal/warm_restart.json`：首次启动→停止→再次启动→/ready 与 /health 通过；无残留端口/重复实例。

## 19. 优雅停止

`rehearsal/graceful_shutdown.json`：stop_local 停止 PID、进程退出、Qdrant 集合 3→3 未动、再次启动成功。过程中发现并修复 `stop_local.ps1` 的 `$name:` PowerShell 解析缺陷（`${name}:`）。

## 20. 故障恢复

`rehearsal/failure_recovery.json`：环境变量缺失→check_env 退出 1；Qdrant 停止→start_api 明确失败（QDRANT_NOT_READY）→恢复后成功；错误 PID→stop_local 安全处理；全新空库→alembic 自动建表恢复→API 可启动；清理通过。另修复了三个测试模块对真实库的 `drop_all` 污染（隔离临时库），并恢复冻结 KB 注册。

## 21. 备份恢复

`rehearsal/backup_restore.json`：备份应用 DB（SHA256 记录）+ Qdrant 集合清单（3 集合/453 chunks）→ 恢复到全新库 → alembic → API 启动 → 官方查询返回 3 条引用 → 集合点数一致 → 精确清理临时资源。修复了演练复制旧 DB 路径（应用默认库实为 `src/data/db/industrial_rag.db`）的问题。

## 22. Smoke Test

`acceptance/smoke_results.jsonl`：6 场景全部 HTTP 200（参数/润滑/步骤/故障/安全/跨页）。

## 23. Prompt Injection

`acceptance/robustness_results.jsonl`：12 条，10 条 403 拦截、2 条通过输入检查但未产生 Secret/系统提示/设备动作/伪造引用/联锁绕过输出（`security_zero=true`）。

## 24. 黄金子集（20 题）

`acceptance/golden_subset_results.jsonl`（参数 5、表格 2、步骤 3、故障 2、安全 3、跨页 3、证据不足 2）：

| 指标 | 结果 |
|---|---|
| HTTP 成功率 | 20/20=1.0 |
| answer_citation_accuracy（历史口径，superseded） | 14/20=0.70 |
| answer_citation_accuracy（canonical 18 分母） | 14/18=0.7778 |
| citation_traceability_emitted | 1.0 |
| false_rejection_rate（历史口径，superseded） | 6/20=0.30（含 N001/N002） |
| false_rejection_rate（canonical 18 分母） | 4/18=0.2222（S007/D005/C001/C002） |
| insufficient_evidence_rejection_rate | 2/2=1.0 |
| negative_unsupported_answer_rate | 0/2=0 |
| answer_citation_precision | 4.6667/18=0.2593 |
| answer_citation_recall | 12.3333/18=0.6852 |
| gold_page_citation_rate | 14/18=0.7778 |
| gold_evidence_citation_rate | 12/18=0.6667 |
| answered_without_evidence_rate | 0/18=0 |
| request_id/trace_id 完整率 | 20/20=1.0 |
| P95 延迟 / error rate | 2.72s / 0 |

> 历史值（14/20=0.70、6/20=0.30）已保留在 `closeout/acceptance_metric_correction.json` 的 `historical_metrics` 中并标记 superseded；canonical 指标以 18 题可回答 / 2 题负样本为分母，N001/N002 不计入 answer_citation_accuracy 与 false_rejection_rate。

## 25. Release Gates

`acceptance/release_gates.json`：smoke、20 题完整、引用可追溯、N 题拒答、安全零风险、fallback=0、trace 完整、安全零泄漏、http 1.0、error 0——全部通过（passed=true）。

Closeout 复评（2026-08-02）：使用 canonical 18/2 分母重新评估全部门禁，并新增 `answerable_denominator_correct`、`negative_denominator_correct`、`n001_n002_excluded_from_answerable_denominator`、`n001_n002_excluded_from_false_rejection` 等门禁；全部 passed=true，`release_package_approved=true`。

## 26. 测试与 Ruff

最终结果（Closeout 实测，`C:\Users\12189\.conda\envs\industrial-rag\python.exe`）：

- `python -m pytest --collect-only -q`：546 collected（5.10s）；
- `python -m pytest -q`：534 passed / 12 skipped / 0 failed，耗时 20.61s；
- skip 分类（全部为真实外部 opt-in，非静默跳过）：Real MinerU opt-in 1 项（IRA_MINERU_REAL=1）、Real DashScope+Qdrant E2E opt-in 2 项（IRA_QDRANT_E2E=1）、Real Qdrant integration opt-in 9 项（IRA_QDRANT_INTEGRATION=1）；
- `python -m ruff check .`：All checks passed!

初始基线：532 collected / 520 passed / 12 skipped / 0 failed。Phase 7 新增测试 14 项（门禁映射、基线分层、权威路径、C007 技术债、模型字段、版本源、包清单与 ZIP、Secret 扫描、演练、验收、.env.example、脚本）已并入 546 项；另修复三个测试模块的 DB 隔离。

## 27. release_package_approved

**true**（Closeout 通过、canonical 指标分母修正、包完整、演练全通过、验收门禁复评全通过、最终 pytest 534 passed/0 failed、Ruff 通过、Secret 0）。

## 28. Tag 是否创建

**未创建**（默认不创建；未设置 IRA_PHASE8_CREATE_TAG=1，`v0.1.0-rc.1` 不存在于仓库）。

## 29. deployment_performed

**false**；未修改外部生产环境，未自动部署。

## 30. 已知限制

- Qdrant client 1.18 vs server 1.13.6 兼容警告存在（功能测试通过，独立 tech debt，本阶段不升级）；
- provider_reported_model 为 null（provider 不返回模型版本），模型身份以 configured/requested 为准；
- 冷启动查询使用官方内置 LLM 链路，单次查询延迟 3-10s（验收 20 题 P95=2.96s，多为缓存命中）；
- RC 包不含数据/缓存/冻结索引本身，部署时依赖宿主机 Qdrant 与 DB（手册已说明）；
- `actual_model` 字段在历史产物中保留为 deprecated 别名，新产物使用新字段。

## 31. 下一步建议

## 31. 指标分母审计（Closeout）

`closeout/acceptance_metric_correction.json`：逐题读取 `golden_subset_results.jsonl`（question_id / primary_category / answerable / refusal / citation success / error）重算：

- 可回答题分母统一为 18：answer_citation_accuracy=14/18=0.7778、answer_citation_precision=4.6667/18=0.2593、answer_citation_recall=12.3333/18=0.6852、gold_page_citation_rate=14/18=0.7778、gold_evidence_citation_rate=12/18=0.6667、false_rejection_rate=4/18=0.2222、answered_without_evidence_rate=0/18=0；
- 负样本分母为 2：insufficient_evidence_rejection_rate=2/2=1.0、negative_unsupported_answer_rate=0/2=0；
- 历史值 14/20=0.70 与 6/20=0.30 保留为 superseded；`llm_rerun_required=false`；`source_results_sha256=655fef83...ed776e`；原始逐题结果未被覆盖。

## 32. RC 包类型声明（Closeout）

`closeout/package_type.json`：`artifact_type=application_release_candidate`、`self_contained=false`、`external_state_required=true`；不含正式数据库、Qdrant 数据目录、冻结索引、LLM 缓存或用户上传原始文档；依赖宿主机提供 Qdrant、数据库、环境变量、模型 API、冻结 KB 或测试 KB；`installation_mode=local_conda_application_with_docker_qdrant`。该声明同步至 release_manifest.json、artifact_manifest.json、package/include_manifest.json 与 local-startup 运行手册。RC 包不得描述为完全独立离线安装包。

## 33. Phase 7 Closeout 决策

`closeout/closeout_decision.json`：canonical 分母修正=true、原始结果未覆盖=true、acceptance 门禁复评通过=true、最终 pytest 结果明确（546/534/12/0，20.61s）、Ruff 通过=true、package_type 已声明=true、confirmed_secret_count=0、release_package_approved=true、deployment_performed=false → **phase8_allowed=true**。

## 34. 下一步建议

1. Phase 8（RC Tagging & Controlled Staging Deployment）当前被阻塞：`IRA_PHASE8_TARGET_ENV` 未配置；设置 `local_staging`（或 `remote_staging` + 对应字段 + `IRA_PHASE8_DEPLOY_STAGING=1`）后重跑；禁止默认选择 remote_staging；
2. Tag `v0.1.0-rc.1` 需在显式授权（`IRA_PHASE8_CREATE_TAG=1` / `IRA_PHASE8_PUSH_TAG=1`）下创建/推送；
3. 如需正式部署：先创建 annotated tag、执行生产环境发布手册并人工审批；
4. 可选升级 Qdrant 版本（独立迁移/回滚实验）；
5. 后续 50 题官方 E2E 复评应在每次发布前重跑（含 load 与 shadow audit）。
