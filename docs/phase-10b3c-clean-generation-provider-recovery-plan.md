# Phase 10B-3C 实施计划

1. 冻结并读取旧 Active Generation 元数据，禁止写入旧 workspace、Collection 和 Active 指针。
2. 审计全部 legacy child JSONL 的重复 ID，记录分组、内容和位置根因。
3. 修复 Parent/Child ID，使文档、页/顺序、分组和规范化内容进入确定性身份摘要。
4. 从真实 PDF 构建隔离 Candidate，并生成 parsed、parents、chunks、relationships、tables 和 manifest。
5. 执行上下文注册表完整性门禁及 Golden evidence 只读 sidecar 映射。
6. 执行 Provider preflight，固定可用模型和 Embedding，禁止隐式 fallback。
7. 使用现有 admin-only 显式 Generation 查询接口执行 Candidate smoke 和 52 题 development/validation 验收，确认响应、Trace、Citation、Evidence 的数据库 Generation ID 一致。
8. 运行 pytest、Ruff、secret scan，生成阶段报告；不执行 Holdout、Tag、RC 或生产部署。
