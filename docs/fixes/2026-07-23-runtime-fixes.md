# 2026-07-23 — LightRAG 导入/查询运行时修复

## 背景

首次运行 `ingest_documents.py` 导入两份离心泵手册后，再次执行导入和 Streamlit 查询时分别暴露了两个 bug。

---

## 修复 1：重复文档导入不再崩溃

### 症状

```text
RuntimeError: 手册 2196-ANSI-Manual-Chinese.pdf 导入失败，LightRAG 状态: {'dup-ddb0c23cafe2dd4264d930bbd32a9754': 'failed'}
```

### 根因

LightRAG 对已存在的文档返回 `dup-<hash>` 前缀的 track ID，其状态可能是 `processed`。旧代码用 `set(statuses.values()) != {"processed"}` 检查，只要返回值不是正好 `{"processed"}` 就抛异常，不识别 `dup-*` 前缀。

### 改动

`src/industrial_rag/lightrag_service.py:216-222` — 状态检查改为逐条判断，允许 `dup-` 开头的文档 ID：

```python
if not statuses or not all(
    s == "processed" or doc_id.startswith("dup-")
    for doc_id, s in statuses.items()
):
    raise RuntimeError(...)
```

### 测试

`tests/test_lightrag_service.py::test_ingest_accepts_dup_status_from_lightrag` — 模拟 backend 返回 `{"dup-ddoc123": "processed", "track-test-1": "processed"}`，验证不再抛异常。

---

## 修复 2：Streamlit 查询事件循环锁冲突

### 症状

```text
<asyncio.locks.Lock object at 0x...> is bound to a different event loop
```

### 根因

- `LightRAG.__init__`（lightrag-hku 内部）会创建 `asyncio.Lock` 对象，锁创建时绑定到当前运行的事件循环。
- Streamlit app 在按钮回调中用 `asyncio.run(_ask(...))` 执行查询，每次 `asyncio.run()` 创建一个*新*事件循环。
- `LightRAGService.__init__` 在 Streamlit 模块级别（即 `asyncio.run()` 之外）调用 `build_official_backend()`，导致锁绑定到主线程的默认事件循环（或无循环）。
- LightRAG 1.5.4 内部有模块级缓存（`@lru_cache` 的 LLM/Embedding 函数），其内部锁在首个事件循环中创建，后续请求使用新循环时锁绑定冲突。

### 改动

**`src/industrial_rag/lightrag_service.py`** — 将后端构建从 `__init__` 推迟到 `initialize()`：

```python
# 之前：在 __init__ 中立即构建（可能无事件循环）
self._backend = backend or build_official_backend(settings)

# 之后：在 initialize() 中按需构建（确保在 async 上下文中）
self._backend: LightRAGBackend | None = backend
# ...
async def initialize(self) -> None:
    if self._backend is None:
        self._backend = build_official_backend(self.settings)
```

**`app/streamlit_app.py`** — 全局复用同一个事件循环和 service 实例：

- 用模块级变量 `_loop` 和 `_service` 持有持久的事件循环和 service 实例。
- `_get_or_create_loop()` 保证整个进程生命周期只创建一个事件循环。
- `_ensure_service()` 在首次调用时初始化 service，后续复用。
- 用 `loop.run_until_complete()` 替代 `asyncio.run()`。

### 注意事项

- LightRAG 1.5.4 的模块级缓存锁问题在 future 版本可能修复，届时可考虑恢复按请求创建 service 的模式。
- 当前方案下 service 实例在 Streamlit 热重载后不会自动重建（`_service` 模块变量保持旧实例），如果 settings 发生变更需要手动重启进程。

---

## 受影响文件

| 文件 | 改动类型 |
|---|---|
| `src/industrial_rag/lightrag_service.py` | 逻辑修改 |
| `app/streamlit_app.py` | 逻辑修改 |
| `tests/test_lightrag_service.py` | 新增测试用例 |
