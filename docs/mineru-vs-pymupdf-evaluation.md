# MinerU API vs PyMuPDF — 解析效果评估

**日期**: 2026-07-30
**状态**: MinerU API key 未配置，真实调用未执行

---

## 1. 评估方法

计划使用两份真实 PDF 的代表性页面进行对比：

- `2196-ANSI-Manual-Chinese.pdf` (60 页, 1.5MB)
- `t1739cn.pdf` (64 页, 4.3MB)

### 代表性页面选取

| 页面 | 来源 | 特征 | 选取原因 |
|------|------|------|---------|
| 1 | 2196 | 封面 + 标题 | 测试标题识别和版面信息 |
| 5 | 2196 | 目录（多级标题 + 页码） | 测试表格/列表结构 |
| 7 | 2196 | 安全警告（警告！/小心！） | 测试警告块识别 |
| 注：完整页面列表待 MinerU API key 可用后确定 | | | |

### 未执行真实 API 调用的原因

- `.env` 中未配置 `MINERU_API_KEY`
- MinerU v4 精准提取 API 需要注册获取 Token
- 未在 `https://mineru.net` 注册账号

---

## 2. MinerU API Client 实现

已实现完整、可测试的 async HTTP Client：

**文件**: [mineru_client.py](../src/industrial_rag/mineru_client.py)

### 功能覆盖

| 功能 | 状态 |
|------|------|
| v4 Precision API 提交 (URL-based) | ✅ |
| v1 Agent API 提交 (URL-based, 无需认证) | ✅ |
| 任务状态轮询 | ✅ |
| 等待完成 (指数退避 + 超时) | ✅ |
| 结果下载 | ✅ |
| API Key 脱敏 | ✅ |
| 网络错误重试 | ✅ |
| 限流处理 | ✅ |
| 认证错误处理 | ✅ |
| 任务失败处理 | ✅ |
| 超时处理 | ✅ |
| 非法响应处理 | ✅ |
| 配置化 Settings 集成 | ✅ |

### 配置项

```env
MINERU_ENABLED=false
MINERU_API_BASE_URL=https://mineru.net
MINERU_API_KEY=
MINERU_API_VERSION=v4
MINERU_REQUEST_TIMEOUT_SECONDS=60
MINERU_TASK_TIMEOUT_SECONDS=600
MINERU_POLL_INTERVAL_SECONDS=3
MINERU_MAX_RETRIES=3
MINERU_FALLBACK_TO_PYMUPDF=true
MINERU_SAVE_RAW_RESPONSE=true
```

### 测试覆盖 (29 tests)

| 场景 | 测试数 |
|------|--------|
| 提交成功 / 缺 task_id / 认证失败 / 限流 / 服务端错误 / 非法 JSON | 6 |
| 状态查询 (pending / done / failed / unknown) | 4 |
| 等待完成 (成功 / 失败 / 超时) | 3 |
| 下载结果 | 1 |
| 完整流程 (失败任务) | 1 |
| 配置安全 (key 脱敏 / 空 key / 认证模式 / 默认值) | 5 |
| Config 测试 (MinerU 字段存在性) | 集成在 config 测试中 |

---

## 3. PyMuPDF 基线

当前 PyMuPDF 解析器对两份 PDF 的已知表现：

| 指标 | 2196-ANSI | t1739cn |
|------|-----------|---------|
| 解析成功 | ✅ 全部 60 页 | ✅ 全部 64 页 |
| 产生 chunk 数 | 53 | 65 |
| 章节标题检测 | ✅ 封面/正文 | ✅ 封面/正文 |
| 页码 | ✅ 自动递增 | ✅ 自动递增 |
| 表格处理 | ⚠️ 纯文本提取，无结构 | ⚠️ 纯文本提取，无结构 |
| 图片 | ❌ 丢弃 | ❌ 丢弃 |
| 安全警告 | ✅ 保留文本 | ✅ 保留文本 |
| 页眉页脚 | ⚠️ 部分侵入正文 | ⚠️ 部分侵入正文 |

---

## 4. MinerU 预期收益（未验证）

根据 MinerU 文档：

| 能力 | MinerU | PyMuPDF当前 |
|------|--------|------------|
| 版式保留 | ✅ 阅读顺序保证 | ⚠️ sort=True 物理顺序 |
| 表格提取 | ✅ 结构化 JSON | ❌ 纯文本 |
| 公式 | ✅ LaTeX | ⚠️ 纯文本 |
| 图片提取 | ✅ 独立文件 | ❌ |
| 页眉页脚去除 | ✅ | ❌ |
| Markdown 输出 | ✅ | ❌ |
| Page-by-page 输出 | ✅ | ✅ |

---

## 5. 决策：MinerU API 的角色

**当前建议**：PyMuPDF 作为默认解析器，MinerU API 作为可选增强。

理由：

1. **PyMuPDF 已足够** — 当前两份 PDF 的版式简单，PyMuPDF 文本提取质量良好
2. **MinerU 有成本** — API 调用有每日配额（2000 页/天），需要外网访问
3. **优先级评估** — 表格/公式/图片提取在当前问答场景中收益有限
4. **工程成熟度** — MinerU Client 已实现并可测试，但未经过真实调用验证
5. **Linux 依赖** — MinerU 本地部署需要 Linux，Windows 上只能用 API

**迁移路径**：

1. 获取 MinerU API Key
2. 对代表性页面执行真实对比
3. 根据对比结果决定：
   - 方案 A: PyMuPDF 为主，MinerU 处理复杂页
   - 方案 B: MinerU 为主，PyMuPDF fallback
   - 方案 C: 暂不接入 MinerU

---

## 6. 已知限制

- MinerU API 真实调用未执行
- 未获得 MinerU 输出样本文件
- 表格/图片提取质量未评估
- API 延迟和稳定性未测量
- 大文件（>200MB）场景未测试
