# 当前系统流程详细文档

**日期**: 2026-07-30
**基于**: 实际代码阅读和测试

---

## 1. 系统启动流程

```
1. 用户激活 Conda 环境: conda activate industrial-rag
2. 用户编辑 .env: 设置 DASHSCOPE_API_KEY
3. 解析 PDF: python scripts/parse_manuals.py
4. 导入 LightRAG: python scripts/ingest_documents.py
5. 启动 FastAPI: python -m uvicorn industrial_rag.api:app --host 127.0.0.1 --port 8000
6. 启动 Streamlit: python -m streamlit run app/streamlit_app.py
```

## 2. PDF 解析流程

```
入口: scripts/parse_manuals.py::main()
  → scan_pdf_files(data/manuals/)
  → 验证 PDF 数量 = 2
  → 记录原始哈希
  → parse_manuals(MANUAL_DIR, OUTPUT_PATH)
    → 对每个 PDF:
      → parse_pdf(path, ParserConfig(max_characters=1800, overlap=180))
        → PyMuPDF open(path)
        → 逐页:
          → get_text("text", sort=True)
          → _normalize_text()
          → _section_title()
          → _chunk_page()
          → 生成 chunk_id = {stem}-p{page}-c{ordinal}-{sha256[:10]}
      → 写入 documents.jsonl (原子替换: .tmp → rename)
  → 验证源 PDF 哈希未变
  → 打印统计
```

### 解析参数

| 参数 | 值 | 位置 |
|------|-----|------|
| max_characters | 1800 | `ParserConfig.__init__` |
| overlap_characters | 180 | `ParserConfig.__init__` |
| chunk 边界 | `\n\n`, `\n`, `。`, `！`, `？`, `.`, `!`, `?` | `_BOUNDARIES` |
| 文本规范化 | 替换连续空白为单空格 | `_normalize_text()` |
| 章节检测 | 第一行非空短文本 (≤120 字符) | `_section_title()` |

## 3. LightRAG 索引流程

```
入口: scripts/ingest_documents.py::main()
  → Settings.from_env()
  → load_documents(data/processed/documents.jsonl)
  → LightRAGService.initialize()
    → check_storage_compatibility()
    → build_official_backend()
      → LightRAG(
          working_dir=str(storage),
          llm_model_func=llm_model_func,
          llm_model_name=settings.llm_model,
          embedding_func=EmbeddingFunc(
            embedding_dim=1024,
            max_token_size=8192,
            func=openai_embed(模型=text-embedding-v4, 端点=北京),
            send_dimensions=True,
          ),
          chunk_token_size=1600,
          enable_content_headings=True,
          entity_extract_max_gleaning=0,
          entity_extract_max_records=12,
          entity_extract_max_entities=12,
          max_parallel_insert=1,
        )
      → LightRAG.initialize_storages()
  → LightRAGService.ingest(chunks)
    → 按 source_file 分组 chunks
    → 每个 chunk 注入:
      - [[INDUSTRIAL_RAG_SOURCE file=... page=... chunk=...]]
      - [来源：文件名，第X页，章节：XXX]
    → 用 <<<INDUSTRIAL_RAG_CHUNK_BOUNDARY>>> 连接
    → LightRAG.ainsert(
        input=[合并后的文本],
        ids=[manual-{sha256[:20]}],
        file_paths=[source_file],
        split_by_character=INDUSTRIAL_RAG_CHUNK_BOUNDARY,
        split_by_character_only=True,
      )
    → get_track_status(track_id) — 验证所有 status="processed"
```

### LightRAG 内部处理

```
LightRAG.ainsert()
  → 按 INDUSTRIAL_RAG_CHUNK_BOUNDARY 切分
  → 每个分段:
    → chunk_token_size=1600 二次切块（如果分段超过 1600 token）
    → Embedding: text-embedding-v4 → 1024 维向量 → NanoVectorDB
    → 实体抽取: LLM (百炼模型) → NetworkX 图
    → 关系抽取: LLM (百炼模型) → NetworkX 图
    → KV 存储: full_docs, text_chunks, full_entities, full_relations
    → 文档状态: JsonDocStatusStorage
```

## 4. 问答查询流程

```
用户输入 → Streamlit chat_input
  → _submit_question(prompt)
    → KnowledgeApiClient.query(question, history)
      → POST http://127.0.0.1:8000/v1/query
      → Body: {"query": question, "history": [...]}
      → Headers: Authorization: Bearer <key> (可选)

FastAPI 处理:
  → authenticate_query_request 中间件 (验证 SERVICE_API_KEY)
  → query(payload, request)
    → runtime.query(payload.query, mode="mix", timeout=180.0)

LightRAGRuntime 同步桥:
  → asyncio.run_coroutine_threadsafe(_query_async, bg_loop)
  → 获取 asyncio.Lock (保证串行)
  → LightRAGService.query(question, mode="mix")

LightRAGService.query():
  → backend.aquery_data(question, QueryParam(mode="mix"))
    → LightRAG mix 检索 (local + global + naive 融合)
    → 返回 payload:
      {
        "data": {
          "entities": [...],
          "relationships": [...],
          "chunks": [
            {"content": "...", "file_path": "rag-source::..."},
            ...
          ],
          "references": [...]
        }
      }

  → evidence_policy.select_evidence(question, payload)
    1. 从 chunks 和 references 提取候选
    2. 解码 file_path 中的 provenance 信息
    3. 文档别名匹配 (SUMMIT → 2196, DESMI → t1739)
    4. 按 source_file 路由过滤
    5. Token 重叠打分 (去停用词 + CJK n-gram)
    6. Top-3 (需要 ≥2 个共享 token)

  → 如果 evidence.allowed:
    → 构造 context (注入 chunk header)
    → backend.generate(question, context, system_prompt)
      → llm_model_func(prompt, system_prompt)
        → openai_complete_if_cache(模型链)
        → 故障切换: 额度耗尽/限流 → 下一个模型
    → 返回 QueryResult(answer, citations, mode)

  → 如果 !evidence.allowed:
    → 返回 INSUFFICIENT_EVIDENCE_MESSAGE

API 响应格式:
  {
    "request_id": "uuid",
    "status": "success" | "insufficient_evidence",
    "answer": "...",
    "citations": [
      {"citation_id": "cite_1", "document_name": "...", "page": N, "chunk_id": "..."}
    ],
    "claims": [{"claim_id": "claim_1", "text": "...", "citation_ids": ["cite_1"]}],
    "latency_ms": N
  }
```

## 5. 知识图谱可视化流程

```
Streamlit → 知识图谱 Tab
  → gv.locate_graph_file(working_dir)
    → 查找 lightrag_storage/graph_chunk_entity_relation.graphml
  → _load_graph_cached(path, mtime, size)
    → nx.read_graphml(path)
  → 用户选择模式:
    - 全局概览: 按 degree 取 top-N 节点
    - 实体搜索: 匹配实体名 → BFS 取 N 跳邻居
  → gv.render_pyvis_html(subgraph)
    → pyvis Network
    → 注入 CSS + JS (tooltip, 点击高亮)
    → components.html(html)
```

## 6. 黄金集评估流程

```
入口: python scripts/evaluate.py --real --golden <file> --output <file>
  → load_golden_cases(path) — 加载 JSONL
  → LightRAGRuntime(Settings.from_env())
  → evaluate_cases(cases, query_lambda)
    → 每个 case:
      → runtime.query(question, mode="mix")
      → 计算 Recall@K, MRR, citation_presence_rate
      → 检查 refusal_passed (无证据问题是否拒答)
      → 检查 routed_document (文档路由准确率)
  → 写入 JSON 报告
```

---

## 关键文件路径索引

| 职责 | 文件路径 |
|------|---------|
| FastAPI 入口 + 路由 | [api.py](../src/industrial_rag/api.py) |
| LightRAG 服务封装 | [lightrag_service.py](../src/industrial_rag/lightrag_service.py) |
| 同步桥 | [runtime.py](../src/industrial_rag/runtime.py) |
| 配置 | [config.py](../src/industrial_rag/config.py) |
| PDF 解析 | [document_parser.py](../src/industrial_rag/document_parser.py) |
| 引用格式化 | [citation_formatter.py](../src/industrial_rag/citation_formatter.py) |
| 证据策略 | [evidence_policy.py](../src/industrial_rag/evidence_policy.py) |
| 评估引擎 | [evaluation.py](../src/industrial_rag/evaluation.py) |
| 图谱可视化 | [graph_visualizer.py](../src/industrial_rag/graph_visualizer.py) |
| Streamlit 前端 | [streamlit_app.py](../app/streamlit_app.py) |
| API 客户端 | [api_client.py](../app/api_client.py) |
| 聊天状态 | [chat_state.py](../app/chat_state.py) |
| 解析脚本 | [parse_manuals.py](../scripts/parse_manuals.py) |
| 索引脚本 | [ingest_documents.py](../scripts/ingest_documents.py) |
| 评估脚本 | [evaluate.py](../scripts/evaluate.py) |
| .env 模板 | [.env.example](../.env.example) |
| 依赖声明 | [pyproject.toml](../pyproject.toml) |
