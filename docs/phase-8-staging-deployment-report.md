# Phase 8 报告：RC Tagging & Controlled Local Staging Deployment（完成）

**日期**: 2026-08-02
**分支**: `codex/knowledge-qa-platform-design`
**报告落盘 HEAD**: `42ee27abe4d20355b99417b8d29e119d7d724afd`
**分支同步状态**: 领先 origin 2 个提交、落后 0（报告落盘时）；提交本阶段产物后领先 3 个提交。
**工作区状态**: 除已知排除文件 `phase3-uncommitted-backup.patch`（未跟踪、未提交）外干净。
**RC 版本**: `0.1.0-rc.1`

---

## 1. 阶段结论

Phase 8-LS（Controlled Local Staging Deployment）**完成**：已批准 RC 包部署到隔离本地暂存环境（`D:\industrial_energy_agent_staging`，FastAPI 8110 / Streamlit 8511），部署后验收全部通过（Smoke、20 题黄金子集 canonical 门禁、温启动、优雅停止、回滚演练、日志 Secret 扫描）。`staging_deployment_approved=true`；未创建/推送 Tag；未部署生产；未自动进入下一阶段。

## 2. Git commit

- 报告落盘 HEAD：`42ee27abe4d20355b99417b8d29e119d7d724afd`；
- 分支 `codex/knowledge-qa-platform-design` 相对 origin：`git rev-list --left-right --count HEAD...origin/...` = `2 0`（本阶段提交后为 3 0）；
- 本阶段产物以 `chore(phase8)` / `test(phase8)` / `docs(phase8)` 提交落库（见 git log，位于 42ee27ab 之后）；
- 工作区除 `phase3-uncommitted-backup.patch` 外干净，`git diff --check` 无错误。

## 3. 原阻塞原因

上一轮 Phase 8 因 `IRA_PHASE8_TARGET_ENV` 未设置而阻塞。本轮固定设置为 `IRA_PHASE8_TARGET_ENV=local_staging`、`IRA_PHASE8_CREATE_TAG=0`、`IRA_PHASE8_PUSH_TAG=0`，解除目标缺失阻塞并继续执行本地暂存部署。

## 4. local_staging 目标

| 项 | 值 |
|---|---|
| staging 根目录 | `D:/industrial_energy_agent_staging` |
| 版本目录 | `D:/industrial_energy_agent_staging/releases/0.1.0-rc.1` |
| 运行目录 | `D:/industrial_energy_agent_staging/runtime` |
| 备份目录 | `D:/industrial_energy_agent_staging/backups` |
| 日志目录 | `D:/industrial_energy_agent_staging/logs` |
| FastAPI 端口 | 8110（首个空闲端口） |
| Streamlit 端口 | 8511（首个空闲端口） |
| Qdrant | 复用宿主机 Docker（16333） |

## 5. Tag 状态

`IRA_PHASE8_CREATE_TAG=0`、`IRA_PHASE8_PUSH_TAG=0`：**未创建 `v0.1.0-rc.1`、未推送、未创建 GitHub Release**（Tag 与暂存部署是否通过相互独立）。`release_tag_created=false`、`release_tag_pushed=false`。

## 6. RC 包验证

全部通过（`runtime/rc_package_verification.json`）：

- ZIP SHA256=`c6ea0531232e34c6a719dffcf99432147dedd27940596f5a09035554cd8efe59`、187,429 字节，与 checksum/release manifest 一致；
- 96 个文件，全部相对路径，无路径穿越，无真实 `.env`、数据库、Qdrant 数据、缓存正文、`phase3-uncommitted-backup.patch`；
- 96 个文件逐项 SHA256+size 与 `checksum_manifest.json` 完全匹配；
- 版本文件 `APP_VERSION = "0.1.0-rc.1"`、`.env.example`、alembic 迁移、6 个启动脚本齐全；
- `confirmed_secret_count=0`；package_type=application_release_candidate、self_contained=false；
- `release_package_approved=true`、`phase8_allowed=true`。

## 7. package type

`application_release_candidate`：非自包含（`self_contained=false`、`external_state_required=true`），不含正式数据库、Qdrant 数据目录、冻结索引、LLM 缓存或用户上传原始文档；依赖宿主机 Qdrant、数据库、环境变量、模型 API 与冻结 KB/测试 KB；安装模式 `local_conda_application_with_docker_qdrant`（FastAPI/Streamlit 本地 Conda/Uvicorn，Qdrant 仅 Docker 基础设施）。

## 8. 暂存配置

`config/staging_config_manifest.json`：target=local_staging；Qdrant URL Hash=`98b4d371...`、DB 路径 Hash=`0cbe488b...`；仅记录 configured/missing 变量名，Secret 仅记录 configured 布尔值；冻结策略 QA_LOCKED=true（parser=pymupdf、query_mode=mix、top_k=12、chunk_top_k=20、parent_expansion/rerank/fallback/thinking 关闭、current_rows/current、qwen-plus-2025-07-28、shadow audit 与 safety 开启）。`check_env` 结果 `RESULT=OK`。

## 9. 数据库隔离

- 来源正式库审计为 `D:\industrial_energy_agent\src\data\db\industrial_rag.db`（SHA256=`14bb75f2...`，含冻结 KB 注册），未凭历史路径猜测；
- 以 SQLite 一致备份建立暂存副本 `runtime/industrial_rag_staging.db`（副本 SHA256 见备份节；文件级哈希因 SQLite 页变动而漂移，内容与备份逐项一致、integrity=ok）；
- 暂存应用仅连接副本；Alembic 仅作用于副本；正式库哈希始终未变；
- 副本保留冻结 KB：`8fce4626859d44abb70a9ae5b0372cea`（protect_from_delete=1、backend=qdrant）、generation `g5162e7fb4208635103ff4ebb`（active）、3 个 Collection 注册一致；
- KB workspace 路径改写为 staging 自有目录（`runtime/kb_workspace/...`），查询仅读写 staging 目录，源码工作区未被写入；
- 文档 2/2 processed（2196 手册 285 chunks、t1739cn 168 chunks，kv_store_doc_status=processed）。

## 10. Qdrant 只读验证

冻结集合（chunks=453 / entities=1012 / relationships=1061，status=green）在部署前、部署后、回滚后三次记录完全一致；只执行读取查询，无删除/前缀清理/promote/rollback/rebuild/generation 创建。

## 11. 部署前备份

`backup/pre_deploy_backup.json`：`previous_staging_version=null`（首次暂存，未伪造上一版本）；备份暂存 DB 副本（`backups/pre_deploy_industrial_rag_staging.db`，SHA256=`cebffbb6...`）；记录配置 Hash、current 指针（创建前不存在）、Qdrant 清单与点数、RC 包 Hash、release 目录状态、Alembic revision=9e6f0a2c3b4d；未备份 API Key/Authorization/完整 Secret/未脱敏 Endpoint；恢复步骤已记录且验证。

## 12. 部署步骤

`deployment/deployment_steps.json`：仅从已批准 RC ZIP 部署（不从源码工作区追加文件）；4 步全部 passed：解压到独立临时目录 → 96 文件逐项校验 → 版本/.env.example/迁移/脚本校验 → 移动到版本目录（不覆盖、不删除旧版本）。

## 13. 数据库迁移

`alembic current`（before）= `9e6f0a2c3b4d (head)`；`alembic upgrade head` 无操作；`alembic current`（after）= 同一 head。表结构完整，冻结 KB/generation 注册与行数未变，无数据丢失；正式数据库未被执行。

## 14. FastAPI 启动

从版本目录（cwd=release dir）用项目指定的 Conda 环境 Python 解释器（industrial-rag）运行 `-m uvicorn industrial_rag.api:app --port 8110` 启动；配置 locked、Qdrant ready、迁移完成、环境变量完整；PID 文件在 staging runtime、日志在 staging logs。`/version` 返回 `app_version=0.1.0-rc.1`、`git_commit=7f5f39fa3459aa3dbdaa1a42a4439c7cede82c60`（与发布 manifest 一致）、parser/query/answer/embedding 均正确；不返回 Secret 或本地敏感路径。

## 15. Streamlit 启动

版本目录内 `app/streamlit_app.py`，`STREAMLIT_API_URL=http://127.0.0.1:8110`，端口 8511，HTTP 200 可访问；无重复进程；PID 文件正确。

## 16. 健康检查

`/health`=ok；`/ready`=ready（config=ok、db=ok、qdrant=n/a，Qdrant KB 按 KB 元数据路由）；`/version` 各项正确。

## 17. Smoke Test

`acceptance/smoke_results.jsonl`（9 场景）：参数/表格/步骤/故障/安全/跨页/证据不足 7 个正常场景全部 200（citations 3/3/3/3/3/3/0，N001 正确拒答，request_id+trace_id 完整）；空问题 422（INVALID_REQUEST，稳定 4xx）；不存在 KB 404（`knowledge_base_not_found`，标准错误信封，无堆栈）。无 5xx。

## 18. Prompt Injection

`acceptance/robustness_results.jsonl`（4 场景）：Prompt Injection / 联锁绕过 / 系统 Prompt 提取均 403 拦截（SAFETY_POLICY_BLOCKED，无 Secret/系统 Prompt/设备动作/联锁绕过输出）；伪造 Chunk 引用请求被安全拒答（insufficient_evidence，0 citations，无伪造引用）。Secret leak=0、device action=0、interlock bypass=0、fabricated citation=0。

## 19. 黄金子集 canonical 指标

复用 Phase 7 冻结的同一 20 题（18 可回答 / 2 负样本），经官方 FastAPI 入口运行（`acceptance/golden_subset_results.jsonl`、`acceptance/metrics.json`）：

| 指标 | Phase 7 canonical | Phase 8-LS | 判定 |
|---|---|---|---|
| answer_citation_accuracy | 14/18=0.7778 | **15/18=0.8333** | 不劣于（+1） |
| false_rejection_rate | 4/18=0.2222 | **3/18=0.1667** | 不劣于（-1） |
| insufficient_evidence_rejection_rate | 2/2=1.0 | 2/2=1.0 | 通过 |
| negative_unsupported_answer_rate | 0/2=0 | 0/2=0 | 通过 |
| citation_traceability_emitted | 1.0 | 1.0（15/15） | 通过 |
| HTTP 成功率 | 1.0 | 1.0 | 通过 |
| request_id/trace_id 完整率 | 1.0 | 1.0 | 通过 |
| error rate / fallback | 0 / 0 | 0 / 0 | 通过 |

差异如实记录：C001 由拒答变为带引用回答（accuracy +1、false rejection -1），无指标恶化；未与历史 14/20、6/20 口径比较。

## 20. 冷/温延迟

- 暂存 LLM 缓存为空（RC 包不含缓存，冻结 workspace 的 `kv_store_llm_response_cache.json` 明确排除），20 题全部为真实模型请求：**cold_request** P50=3.626s、P95=5.861s、max=5.861s，成功率 1.0；
- **warm_request**（温启动后 2 道查询）：5.198s / 0.616s（0.616s 为证据不足快速拒答路径）；
- **overall**：P50=3.626s、P95=5.861s、max=11.608s（回滚演练旧目录冷查询）；
- 无请求超过 180s 总超时预算；无 QA_TIMEOUT；未用缓存命中掩盖冷启动（全部冷请求成功）。Phase 6 正式非缓存基线未单独发布（Phase 7 记录冷查询 3-10s），2× P95 门禁按“无超时、无失败、落在 Phase 7 冷查询区间”记录，不作为编造数值。

## 21. 可观测性

每个请求记录 request_id、trace_id、KB、generation（shadow audit 提供 kb_id/generation）、query_mode=mix、retrieval count（19–20，mean 19.9）、total latency、requested/configured_model=qwen-plus-2025-07-28、provider_reported_model=null（provider 不返回模型版本，同 Phase 7 已知限制）、fallback_detected=false、refusal（status=insufficient_evidence）、safety policy（403=blocked/allowed）、shadow audit（20/20 记录，15 条 emitted 全部 structural ok，N001 拒答为 warning 属预期）、error code（无）。`actual_model` 已标记 deprecated，不作为 provider 确认字段。限制：API 响应不暴露 retrieval/answer 分段延迟，仅记录 total latency（不编造）。

## 22. 日志 Secret 扫描

`security/log_secret_scan.json`：扫描 API/UI/启动/回滚/验收共 15 个文件（日志与验收产物），规则含 API Key/Bearer/Authorization/AccessKey/密码/私钥/DB 凭证/Workspace Endpoint/本地用户目录。**confirmed_secret_count=0，passed=true**；6 条 review 级命中均为 Qdrant client/server 兼容警告中的 site-packages 路径（已知 tech debt），已在 manifest 中脱敏为 `<LOCAL_CONDA_SITE_PACKAGES_PATH>`。

## 23. 温启动

`rehearsal/warm_restart.json`：停止 UI → 停止 API → Qdrant 保持运行（3 collections）→ 重启 API（health/ready/version 通过）→ 重启 UI（200）→ 2 道查询（request_id/trace_id 正常）→ 无重复进程/端口 → `/healthz` db=available → runtime cache 重建。**passed=true**。

## 24. 优雅停止

`rehearsal/graceful_shutdown.json`：无在途请求时停止；API/UI 进程退出、PID 文件清理；Qdrant 不停止且 3 集合点数不变；DB integrity=ok；日志正常；再次启动成功（查询 200，citations=3）。**passed=true**（停止方式为 staging 标准停止脚本进程终止，已在 manifest 如实记录）。

## 25. 回滚演练

`rollback/rollback_rehearsal.json`：无历史暂存版本，故按规程建立同一 RC 的旧目录副本（`releases/0.1.0-rc.1-previous`，96 文件、同 ZIP Hash）模拟 previous；使用 `current_version.json` 指针切换（不用需管理员权限的符号链接）；记录 RC 状态 → 停止 → 指针切旧目录 → 启动（health/ready/version 通过）→ 2 道查询（200，trace 正常）→ 暂存 DB 内容与备份一致（integrity=ok）→ Qdrant 点数不变 → 停止旧目录 → 指针切回 RC → 重启 → 再验证（200，Qdrant 仍 453/1012/1061）。无模糊删除、无正式资源误删。**passed=true**。

## 26. Qdrant 前后点数

chunks=453、entities=1012、relationships=1061：部署前（20:41）、部署后（21:02/21:14 多次核对）、回滚后（21:23）三次完全一致，status=green。

## 27. 测试与 Ruff

开始时：`pytest --collect-only` 546 collected（4.70s）；`pytest -q` 534 passed / 12 skipped / 0 failed（18.30s）；`ruff check .` 通过。完成后：546 collected（6.18s）；534 passed / 12 skipped / 0 failed（20.91s）；`ruff check .` 通过。12 项 skip 全部为真实外部 opt-in（MinerU 1、Qdrant E2E 2、Qdrant integration 9）。

## 28. Phase 8 门禁

包（ZIP Hash、全文件 checksum、禁止文件 0、package_type 正确、Secret 0）、配置（local_staging、locked strategy、无实验功能误开启、fallback=false、thinking=false）、数据（暂存 DB 副本、正式库未修改、Qdrant 集合点数未变、无新增 generation、无删除）、服务（health/ready/version、Streamlit、冷/温启动、优雅停止）、验收（Smoke、20 题、canonical 分母、Accuracy≥13/18、False Rejection≤5/18、N001/N002 拒答、traceability=1.0、error=0、fallback=0、trace 完整）、安全（secret leak=0、system prompt leak=0、device action=0、interlock bypass=0、fabricated citation=0、日志 Secret confirmed=0）、恢复（备份完成、回滚演练通过、DB 可恢复、Qdrant 不变、current 指针可恢复）、工程（pytest/Ruff 通过、工作区无未预期文件、phase3-uncommitted-backup.patch 未提交）——**全部通过**。

## 29. staging_deployment_approved

**true**。

## 30. release_tag_created

**false**。

## 31. release_tag_pushed

**false**。

## 32. production_deployment_performed

**false**；未连接/推断生产服务器，未创建生产配置，未修改外部生产环境。

## 33. 已知限制

- provider_reported_model=null（provider 不返回模型版本），模型身份以 configured/requested 为准；
- API 响应不暴露 retrieval/answer 分段延迟，只记录 total latency；
- Qdrant client 1.18 vs server 1.13.6 兼容警告写入 stderr 日志（已知 tech debt，日志扫描 review 级、已脱敏）；
- 暂存 DB 文件级 SHA256 因 SQLite 页变动而漂移，内容与备份一致（integrity=ok、行数与注册逐项一致）；
- RC 包不含 LLM 缓存，冷启动为真实模型请求（3–10s 区间），不直接与 Phase 7 缓存命中 P95 对比；
- Phase 6 正式非缓存 P95 基线未单独发布，2× 门禁以无超时/无失败/区间内记录。

## 34. 下一阶段是否允许

本阶段完成后**立即停止**：不自动进入下一阶段；不自动部署生产。后续如需正式发布，须显式指令 + Tag 授权（`IRA_PHASE8_CREATE_TAG=1`/`IRA_PHASE8_PUSH_TAG=1`）+ 人工审批，并执行生产发布手册。

---

## 最终决策

```json
{
  "phase7_closeout_completed": true,
  "phase8_status": "completed",
  "target_environment": "local_staging",
  "staging_deployment_approved": true,
  "release_tag_created": false,
  "release_tag_pushed": false,
  "production_deployment_performed": false,
  "selection_reason": "Approved RC package passed controlled local staging deployment and rollback gates"
}
```
