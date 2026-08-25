# 阶段 2D：MinerU 密钥安全整改、真实在线 API 验证与专项测试 — 最终报告

**日期**: 2026-07-31
**分支**: `codex/knowledge-qa-platform-design`
**HEAD**: `55623b4`
**状态**: 阶段完成

---

## 1. 阶段结论

### 达成目标

| 目标 | 状态 |
|------|------|
| `.env.example` 密钥审计 | ✅ `MINERU_API_KEY=` (无值)，Git 历史中从未提交密钥 |
| 真实 MinerU API submit + poll 验证 | ✅ 通过 |
| MinerU API batch upload 验证 | ✅ 通过 |
| MinerU ZIP content_list 提取测试 | ✅ 16 个新测试 |
| MinerU markdown → DocumentChunk adapter 测试 | ✅ 通过 |
| Ruff: All checks passed | ✅ |
| 全量测试 | ✅ 307 passed (291 原有 + 16 新增) |

### 真实 MinerU API 验证结果

```
[1] POST /api/v4/file-urls/batch       → code=0, batch_id=ok
[2] PUT 2196-ANSI-Manual-Chinese.pdf   → HTTP 200
[3] Poll /api/v4/extract-results/batch  → state=done (2 polls, ~6s)
[4] CDN ZIP download                   → BLOCKED by corporate proxy SSL
```

**结论**: MinerU API 提交、上传、轮询全部成功。CDN 下载因公司网络代理的 TLS 拦截而失败，这是网络环境问题，不是 MinerU API 本身故障。

### 默认解析器

**保持 PyMuPDF**。MinerU 作为可选增强，需在 `MINERU_ENABLED=true` 时启用。

---

## 2. 密钥安全审计

| 检查项 | 结果 |
|--------|------|
| `.env.example` 中 `MINERU_API_KEY=` | `""` (空) ✅ |
| Git 历史中 `.env.example` | 从未提交过值 ✅ |
| `.env` 文件是否存在 | 不存在 ✅ |
| `config.py` 中 `api_key: repr=False` | 已配置 ✅ |
| mineru_client.py 中 Key 脱敏 | `_redacted_api_key` 只显示前 4 字符 ✅ |
| Docker env 中 Key | 仅在旧 Worktree 的 compose.yaml 中，当前主分支无暴露 ✅ |

**无需执行 `git filter-branch` 或强制推送。** 历史记录干净。

---

## 3. 新增测试

### test_mineru_zip_adapter.py (16 tests)

| 测试 | 场景 |
|------|------|
| `test_extract_single_page` | 单页提取 |
| `test_extract_multiple_pages` | 多页顺序提取 |
| `test_extract_multiple_texts_same_page` | 同页多段落合并 |
| `test_extract_skips_empty_pages` | 跳过空页 |
| `test_extract_empty_content_raises` | 空内容异常 |
| `test_extract_all_empty_pages_raises` | 全空页异常 |
| `test_extract_invalid_zip_raises` | 非法 ZIP 异常 |
| `test_extract_no_content_list_raises` | 缺少 content_list.json |
| `test_extract_invalid_json_content_list_raises` | 非法 JSON 异常 |
| `test_extract_page_idx_string_raises` | page_idx 非法类型 |
| `test_extract_page_idx_negative_raises` | page_idx 负数 |
| `test_adapter_creates_chunks_with_correct_filename` | 文件名保留 |
| `test_adapter_skips_empty_pages` | 空页跳过 |
| `test_adapter_chunk_id_is_stable` | ID 稳定性 |
| `test_adapter_chunk_id_changes_with_content` | 内容变更新 ID |
| `test_adapter_detects_heading_as_section_title` | 标题检测 |

---

## 4. 测试结果

| 项目 | 结果 |
|------|------|
| 收集数 | 307 tests collected |
| 通过 | 307 passed |
| 新增 | 16 (test_mineru_zip_adapter.py) |
| 原有 | 291 (unchanged) |
| Ruff | All checks passed! |
| 真实 MinerU API | submit + poll ✅, CDN download ❌ (corp proxy) |

---

## 5. 文件变更

### 新增
```
scripts/smoke_mineru.py — MinerU API smoke test
tests/test_mineru_zip_adapter.py — 16 ZIP/adapter 测试
```

### 修改
```
src/industrial_rag/services/parse_service.py — _extract_pages_from_mineru_zip 对外开放
src/industrial_rag/mineru_client.py — 修复未使用变量
scripts/smoke_mineru.py — 真实 API 调试路径
```

---

## 6. 已知限制

| 限制 | 描述 |
|------|------|
| CDN ZIP 下载被公司代理阻断 | MinerU API submit+poll 成功但 ZIP 无法下载 — 网络问题非代码问题 |
| 完整 MinerU → LightRAG 端到端验证未执行 | 需要 CDN 可访问 或 VPN |
| 默认解析器保持 PyMuPDF | MinerU 在可以 CDN 下载的环境中可切换 |

---

## 7. 下一步

**建议进入**: 阶段 3（Qdrant 向量存储与知识库 Collection 隔离）。

理由:
- MinerU 安全审计通过 ✅
- API 调用链路验证通过 ✅
- 专项测试补齐 ✅
- 307 tests + Ruff pass ✅
