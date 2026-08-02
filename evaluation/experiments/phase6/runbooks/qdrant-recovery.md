# Qdrant Recovery Runbook

## 1. 不可用诊断

```powershell
docker ps --filter name=ira-phase3-qdrant-test
Invoke-RestMethod http://127.0.0.1:16333/collections
```

异常：容器退出 → `docker start ira-phase3-qdrant-test`；端口不通 → 检查 Docker Desktop 引擎。

## 2. Collection 存在性检查

冻结前缀 `ira_p3ar_4ac7a596`，3 个集合（chunks/entities/relationships）。缺少任一集合即视为冻结索引不完整，**禁止自动重建**。

## 3. generation 校验

对照 `evaluation/experiments/phase4/parent_expansion/manifests/index_manifest.json`：

- kb_id=`8fce4626859d44abb70a9ae5b0372cea`；
- generation=`g5162e7fb4208635103ff4ebb`；
- points=chunks 453 / entities 1012 / relationships 1061；
- 应用 DB 中 KnowledgeBase+active VectorIndexGeneration 必须存在（`phase6/register_frozen_kb.py` 可幂等注册）。

## 4. 恢复步骤

1. 确认数据卷未被删除（容器名 `ira-phase3-qdrant-test`，46 小时前创建、持久化）。
2. `docker start` → 验证 3 个集合与 point 数。
3. `/ready` 应显示 qdrant=ok。
4. 用一条黄金集问题做冒烟查询，核对 citations 与 shadow audit。

## 5. 禁止

- 禁止模糊 prefix 删除（如按 `ira_` 前缀批量删）；
- 禁止删除冻结集合；
- 禁止在未核对 point 数前重建索引。
