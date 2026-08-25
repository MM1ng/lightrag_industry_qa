# Incident Triage Runbook

| 症状 | 可能原因 | 检查 | 处置 |
|---|---|---|---|
| 检索无结果 | Qdrant 不可用 / prefix 错误 | `/ready`、collections | 启动容器、核对 prefix |
| 页码错误 | 模型/证据选择错误 | shadow audit invalid_page | 记录审计；升级人工复核 |
| 模型超时 | 网络/限流 | 日志 error_code=QA_TIMEOUT | 有限重试；不切模型 |
| Qdrant 异常 | 服务/连接 | Qdrant 日志、/ready | 恢复容器后重试 |
| Embedding 异常 | 额度/网络 | 日志 EMBEDDING_FAILED | 重试；不降级模型 |
| 错误拒答 | Evidence Policy 过严 | refusal_reason | 人工复核；不按 question_id 特判 |
| 引用异常 | 引用不在上下文 | shadow audit invalid_chunk | 记录 warning；不阻塞主链路 |
| Prompt Injection | 注入攻击 | safety policy_id | 403 SAFETY_POLICY_BLOCKED；审计 |
| Secret 泄漏疑似事件 | 日志/响应含密钥 | 立即检查日志脱敏 | 轮换密钥；修复脱敏；报告 |

原则：系统只提供信息检索与分析；不执行设备动作、不生成联锁旁路方案、不输出系统提示词与凭证；高风险操作必须人工复核。
