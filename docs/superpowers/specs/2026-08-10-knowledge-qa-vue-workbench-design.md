# 知识问答 Vue 工作台重设计规格

## 目标

将当前以 Streamlit 为主的工业离心泵知识问答界面，重构为一个统一的 Vue 3 单页前端，服务现场运维人员与管理员两类角色。普通用户以“提问—获得可执行步骤—核验证据”为主路径；管理员在同一前端内通过受保护路由完成知识库、文档、任务和 Generation 管理。

## 已确认的产品决策

- 采用方向 A“问答工作台”，吸收方向 C 的高频问题入口和步骤化回答。
- 用户端和管理端使用同一个 Vue 3 前端，通过路由和后端权限控制区分。
- Streamlit 保留为迁移期回退入口，不作为最终用户入口。
- 不增加多 Agent、传感器分析、工单、审批或其他未确认的运维业务。
- 保留 FastAPI 查询、引用、反馈、知识库和 Generation 语义；检索核心不重写。
- 图谱只读；前端所需的数据通过 FastAPI 的只读图谱投影获取。

## 用户与信息架构

### 普通用户

现场操作员、设备维护工程师和需要查询离心泵手册的技术人员。默认只能看到：

- `/chat` 智能问答
- `/graph` 知识图谱

### 管理员

在普通用户页面基础上看到：

- `/admin/knowledge-bases`
- `/admin/documents`
- `/admin/jobs`
- `/admin/generations`

管理员通过同一前端的“管理员入口”输入一次管理员 Bearer 凭据；凭据只保存在当前浏览器内存中，不写入构建产物、不写入 URL。前端只负责隐藏无权限入口；FastAPI 仍必须对每个管理员接口执行真实凭据校验。

## 视觉方向

产品视觉采用“泵体金属灰 + 安全钴蓝 + 检修琥珀”的工业工具感，不使用通用聊天产品的大面积渐变或装饰性卡片。

### Token

```text
--color-canvas: #F3F5F7
--color-surface: #FFFFFF
--color-ink: #17212B
--color-muted: #63707D
--color-line: #D9E0E6
--color-cobalt: #155EEF
--color-cobalt-soft: #EAF1FF
--color-amber: #C77800
--color-amber-soft: #FFF3D6
--color-success: #147D64
--color-danger: #B42318
--radius-panel: 14px
--radius-control: 10px
--shadow-panel: 0 8px 28px rgba(23, 33, 43, .07)
```

正文使用 `Microsoft YaHei` / `Segoe UI`，数字和技术元数据使用 `Bahnschrift` / `Cascadia Mono`。页面标题采用紧凑的半粗体，答案正文保持 15–16px 和较宽行距。

### 签名元素

每个证据引用显示为带左侧钴蓝色竖线的 `[1]` 标签；右侧证据抽屉沿页面边缘显示一条“手册页码带”，让用户一眼知道当前是在核验来源，而不是浏览普通聊天附件。

## 页面与组件

```text
frontend/
├── src/app/App.vue
├── src/app/router.ts
├── src/app/stores/session.ts
├── src/layouts/WorkspaceLayout.vue
├── src/views/ChatView.vue
├── src/views/GraphView.vue
├── src/views/admin/AdminGateView.vue
├── src/views/admin/KnowledgeBasesView.vue
├── src/views/admin/DocumentsView.vue
├── src/views/admin/JobsView.vue
├── src/views/admin/GenerationsView.vue
├── src/components/chat/HighFrequencyPrompts.vue
├── src/components/chat/ChatTimeline.vue
├── src/components/chat/AnswerMessage.vue
├── src/components/chat/EvidenceDrawer.vue
├── src/components/chat/FeedbackActions.vue
├── src/components/graph/GraphCanvas.vue
├── src/components/admin/...
├── src/api/client.ts
├── src/types/api.ts
└── src/styles/tokens.css
```

## 问答工作台

```text
┌──────────────┬──────────────────────────────┬──────────────────┐
│ 品牌与导航    │ 当前知识库 / 新建对话          │ 证据抽屉          │
│ 智能问答      ├──────────────────────────────┤ 默认隐藏          │
│ 知识图谱      │ 连续对话                      │ 点击 [1] 后打开   │
│ 管理员入口    │ 结论 / 步骤 / 注意事项         │ 文件 / 页码 / 原文 │
│              ├──────────────────────────────┤                  │
│              │ 问题输入 + 发送                │                  │
└──────────────┴──────────────────────────────┴──────────────────┘
```

首屏高频入口按三组展示：启动与停机、故障排查、维修安全。点击后直接发起查询；技术检索模式、Generation 和 Chunk 不出现在普通用户主路径。

答案组件支持 `success`、`partial_answer`、`insufficient_evidence`、`safety_blocked` 和 `error` 状态。已有 claims、citations、evidence 和 feedback 字段直接用于渲染，不强制修改答案协议。

## 交互状态

- 空状态：展示高频问题和一句产品说明。
- 加载中：消息时间线显示骨架，输入框保留且发送按钮锁定。
- 完整回答：显示结论、步骤、注意事项、引用和反馈。
- 部分回答：使用琥珀色提示，展示 `partial_reason` 和推荐追问。
- 证据不足：明确告诉用户无法可靠回答，保留改写问题和高频问题入口。
- 查询失败：保留原问题，提供重新查询和检查服务状态。
- 切换知识库：已有消息时用确认对话框，确认后清空会话。
- 清空会话：已有消息时确认，取消不改变时间线。
- 证据抽屉：点击引用切换证据，关闭后保持消息滚动位置。
- 反馈：有帮助直接提交；没帮助展开原因和可选说明。

## 后端边界

- 复用现有 `/v1/knowledge-bases/{kb_id}/query`、`/v1/feedback`、知识库管理和 Generation 接口。
- 新增只读图谱投影接口，例如 `GET /v1/graph/overview` 与 `GET /v1/graph/neighborhood`，从现有 GraphML 读取，不写回图谱、不调用模型。
- 不把 `generation_id`、`trace_id`、内部诊断和凭据投影到普通用户组件。
- 管理员 Bearer 凭据由用户在前端管理员门禁中输入，保存在内存状态；页面刷新后重新验证，不在前端环境变量、源码、Local Storage 或 URL 中保存。
- 生产构建后由 FastAPI 或独立静态服务器提供 `frontend/dist`；开发环境使用 Vite 代理到本地 API。

## 可访问性与响应式

- 主要按钮和输入控件最小高度 44px。
- 所有抽屉、弹窗和引用标签支持键盘焦点和 Escape 关闭。
- 使用 `prefers-reduced-motion` 禁止非必要过渡。
- 1440px 桌面显示三栏；1024px 以下证据抽屉改为覆盖式侧板；768px 以下改为单列并将导航折叠为顶部菜单。

## 验收标准

- 普通用户打开 `/chat` 可在 3 秒内理解如何开始提问。
- 高峰问题点击后只需一次操作即可进入加载状态。
- 答案中的每个可用引用可打开对应证据抽屉并展示文件名、页码和片段。
- 所有五类回答状态有明确且不泄露内部实现的界面。
- 未授权用户无法通过前端导航进入管理页，后端仍拒绝未授权管理请求。
- `/graph` 支持全局概览、实体搜索和邻居关系展示。
- 原 Streamlit 入口仍可启动，作为迁移期回退。
