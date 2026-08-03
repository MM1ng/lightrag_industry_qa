# Phase 10B-3C 设计：Clean Staging Generation & Provider Recovery

本阶段采用“旧 Active 只读保护 + 隔离 Candidate workspace + 确定性上下文注册表 + Provider preflight”的边界。Candidate 使用真实手册重新解析和切块，写入 `runtime/phase10b3c/kb_data/<kb>/<generation>/`，不依赖 evaluation 目录作为线上数据源，不写入旧 Generation，不移动 Active 指针，不创建新查询策略。任何表格元数据缺失时返回不支持，不生成猜测表头。

固定配置为 naive、TopK 12、chunk TopK 20、normalization/grounding 开启、cache 关闭、rerank 关闭。Provider 不可用则停止，不使用隐式 fallback。Candidate 的 smoke 必须在受控 Candidate 查询入口完成；当前入口仅支持 Active Generation，因此 Candidate 保持未激活。
