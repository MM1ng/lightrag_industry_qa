# RAG 问答评测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为工业手册问答应用构建不依赖 Ragas 的可重复黄金集评测框架和显式真实运行 CLI。

**Architecture:** `industrial_rag.evaluation` 只依赖现有的 `QueryResult` 和 `Citation` 契约，加载 JSONL 黄金问题、调用注入的同步查询函数，并生成逐题和聚合 JSON 报告。真实 CLI 用现有 `LightRAGRuntime` 建立一次运行时后逐题查询；单元测试注入本地查询函数，不触发网络或模型调用。

**Tech Stack:** Python 3.11、标准库 `json`/`statistics`/`time`、pytest、现有 LightRAG runtime。

## Global Constraints

- 不添加 Ragas、Langfuse、LangGraph 或新的运行时依赖。
- 黄金引用的文档名、页码和块 ID 必须来自真实解析产物；示例数据仅用于说明格式，不能作为工业手册评测结论。
- 默认 pytest 不读取 API Key、不访问 LightRAG 或模型服务。
- 真实评测只能通过带 `--real` 的命令执行；报告输出路径由调用者显式指定。
- 评测不记录完整提示词、密钥、隐藏推理或原始文档正文。

---

## Planned File Structure

```text
src/industrial_rag/evaluation.py        # 黄金集契约、加载、指标计算和 JSON 报告
scripts/evaluate.py                     # 显式真实 LightRAG 评测命令
data/evaluation/golden_questions.example.jsonl  # 不可用于生产结论的格式样例
tests/test_evaluation.py                # 纯本地黄金集、指标和异常测试
docs/README or README.md                # 真实黄金集准备和评测命令
```

### Task 1: Define and load the golden-set contract

**Files:**
- Create: `src/industrial_rag/evaluation.py`
- Create: `tests/test_evaluation.py`

**Interfaces:**
- Produces `GoldenCase(case_id: str, question: str, expects_evidence: bool, expected_citations: tuple[Citation, ...])`.
- Produces `load_golden_cases(path: Path) -> tuple[GoldenCase, ...]`.
- Input JSONL line shape is `{"id":"startup","question":"启动前检查什么？","expects_evidence":true,"expected_citations":[{"source_file":"pump.pdf","page_number":7,"chunk_id":"pump-p7-c1"}]}`.

- [ ] **Step 1: Write the failing loader tests**

```python
from pathlib import Path

import pytest

from industrial_rag.evaluation import load_golden_cases


def test_load_golden_cases_preserves_expected_citations(tmp_path: Path) -> None:
    path = tmp_path / "golden.jsonl"
    path.write_text(
        '{"id":"startup","question":"启动前检查什么？","expects_evidence":true,'
        '"expected_citations":[{"source_file":"pump.pdf","page_number":7,'
        '"chunk_id":"pump-p7-c1"}]}\\n',
        encoding="utf-8",
    )

    cases = load_golden_cases(path)

    assert cases[0].case_id == "startup"
    assert cases[0].expected_citations[0].chunk_id == "pump-p7-c1"


def test_load_golden_cases_rejects_evidence_case_without_expected_citation(tmp_path: Path) -> None:
    path = tmp_path / "golden.jsonl"
    path.write_text(
        '{"id":"bad","question":"问题","expects_evidence":true,"expected_citations":[]}\\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected_citations"):
        load_golden_cases(path)
```

- [ ] **Step 2: Run the loader tests and verify they fail**

Run: `python -m pytest tests/test_evaluation.py -q`

Expected: FAIL because `industrial_rag.evaluation` does not exist.

- [ ] **Step 3: Implement the immutable contract and strict JSONL loader**

```python
@dataclass(frozen=True, slots=True)
class GoldenCase:
    case_id: str
    question: str
    expects_evidence: bool
    expected_citations: tuple[Citation, ...]

    def __post_init__(self) -> None:
        if not self.case_id.strip() or not self.question.strip():
            raise ValueError("golden case id and question are required")
        if self.expects_evidence != bool(self.expected_citations):
            raise ValueError("expected_citations must match expects_evidence")


def load_golden_cases(path: Path) -> tuple[GoldenCase, ...]:
    if not path.is_file():
        raise FileNotFoundError(f"golden file does not exist: {path}")
    cases: list[GoldenCase] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"line {line_number}: invalid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError(f"line {line_number}: case must be an object")
        case_id = payload.get("id")
        question = payload.get("question")
        expects_evidence = payload.get("expects_evidence")
        raw_citations = payload.get("expected_citations")
        if not isinstance(case_id, str) or not isinstance(question, str):
            raise ValueError(f"line {line_number}: id and question must be strings")
        if not isinstance(expects_evidence, bool) or not isinstance(raw_citations, list):
            raise ValueError(f"line {line_number}: invalid evaluation fields")
        if case_id in seen_ids:
            raise ValueError(f"line {line_number}: duplicate id {case_id}")
        citations = tuple(
            Citation(item["source_file"], item["page_number"], item["chunk_id"])
            for item in raw_citations
            if isinstance(item, dict)
            and isinstance(item.get("source_file"), str)
            and isinstance(item.get("page_number"), int)
            and isinstance(item.get("chunk_id"), str)
        )
        if len(citations) != len(raw_citations):
            raise ValueError(f"line {line_number}: invalid expected_citations")
        cases.append(GoldenCase(case_id, question, expects_evidence, citations))
        seen_ids.add(case_id)
    if not cases:
        raise ValueError("golden file contains no cases")
    return tuple(cases)
```

Implement parsing with only `json.loads`, `Citation`, and built-in type checks. Include the line number in all file-format errors and reject an empty file.

- [ ] **Step 4: Run the loader tests and verify they pass**

Run: `python -m pytest tests/test_evaluation.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the contract**

```powershell
git add src/industrial_rag/evaluation.py tests/test_evaluation.py
git commit -m "feat: add RAG evaluation golden-set contract"
```

### Task 2: Calculate deterministic per-case and aggregate metrics

**Files:**
- Modify: `src/industrial_rag/evaluation.py`
- Modify: `tests/test_evaluation.py`

**Interfaces:**
- Consumes `GoldenCase` and `Callable[[str], tuple[QueryResult, float]]`.
- Produces `EvaluationReport` with `cases`, `retrieval_recall_at_1`, `retrieval_recall_at_3`, `retrieval_recall_at_5`, `mean_reciprocal_rank`, `citation_presence_rate`, `citation_traceability_rate`, `no_evidence_refusal_rate`, `success_rate`, `latency_p50_ms`, and `latency_p95_ms`.
- Produces `evaluate_cases(cases: tuple[GoldenCase, ...], query: QueryCallable) -> EvaluationReport` and `EvaluationReport.to_dict() -> dict[str, object]`.

- [ ] **Step 1: Write the failing metric tests**

```python
from industrial_rag.citation_formatter import Citation
from industrial_rag.lightrag_service import QueryResult
from industrial_rag.evaluation import GoldenCase, evaluate_cases


def test_evaluate_cases_reports_ranked_retrieval_and_safe_refusal() -> None:
    expected = Citation("pump.pdf", 7, "pump-p7-c1")
    cases = (
        GoldenCase("answer", "轴承温度高怎么办？", True, (expected,)),
        GoldenCase("refuse", "火星维护周期？", False, ()),
    )

    def query(question: str) -> tuple[QueryResult, float]:
        if question == "火星维护周期？":
            return QueryResult(INSUFFICIENT_EVIDENCE_MESSAGE, (), "mix"), 0.2
        return QueryResult("检查润滑。", (Citation("pump.pdf", 2, "p2"), expected), "mix"), 0.1

    report = evaluate_cases(cases, query)

    assert report.retrieval_recall_at_1 == 0.0
    assert report.retrieval_recall_at_3 == 1.0
    assert report.mean_reciprocal_rank == 0.5
    assert report.no_evidence_refusal_rate == 1.0
    assert report.latency_p50_ms == 150.0
```

Also add tests that a raised query exception becomes a failed case, an evidence case without citations lowers citation presence/traceability, and an answer to a no-evidence case with citations fails the refusal metric.

- [ ] **Step 2: Run the metric tests and verify they fail**

Run: `python -m pytest tests/test_evaluation.py -q`

Expected: FAIL because `evaluate_cases` and report types do not exist.

- [ ] **Step 3: Implement the evaluator without model judging**

```python
QueryCallable: TypeAlias = Callable[[str], tuple[QueryResult, float]]


def evaluate_cases(cases: tuple[GoldenCase, ...], query: QueryCallable) -> EvaluationReport:
    results: list[CaseResult] = []
    completed_latencies_ms: list[float] = []
    for case in cases:
        try:
            result, seconds = query(case.question)
        except Exception as error:
            results.append(CaseResult(case.case_id, False, (), None, type(error).__name__))
            continue
        latency_ms = round(seconds * 1000, 3)
        completed_latencies_ms.append(latency_ms)
        expected = {(item.source_file, item.page_number, item.chunk_id) for item in case.expected_citations}
        returned = tuple((item.source_file, item.page_number, item.chunk_id) for item in result.citations)
        first_match = next((index for index, item in enumerate(returned, 1) if item in expected), None)
        refusal_passed = (
            not case.expects_evidence
            and result.answer == INSUFFICIENT_EVIDENCE_MESSAGE
            and not result.citations
        )
        results.append(CaseResult(case.case_id, True, result.citations, latency_ms, None, first_match, refusal_passed))
    return _build_report(cases, tuple(results), completed_latencies_ms)
```

`_build_report` uses evidence cases as the denominator for retrieval/citation metrics, no-evidence cases for refusal rate, and completed cases for success rate. It counts a citation as traceable only when its complete `(source_file, page_number, chunk_id)` identity equals a gold identity. `_nearest_rank(values, percentile)` returns `values[ceil(percentile * len(values)) - 1]` after sorting, or `None` when values is empty. Serialize only case ID, outcome booleans, returned citation displays, latency and safe exception class name.

- [ ] **Step 4: Run the metric tests and verify they pass**

Run: `python -m pytest tests/test_evaluation.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the evaluator**

```powershell
git add src/industrial_rag/evaluation.py tests/test_evaluation.py
git commit -m "feat: add deterministic RAG evaluation metrics"
```

### Task 3: Add the explicit real evaluation command and a format example

**Files:**
- Create: `scripts/evaluate.py`
- Create: `data/evaluation/golden_questions.example.jsonl`
- Modify: `tests/test_evaluation.py`

**Interfaces:**
- `python scripts/evaluate.py --real --golden <path> --output <path> [--mode mix]` creates a JSON report and returns nonzero when a query fails.
- The CLI constructs one `LightRAGRuntime(Settings.from_env())`, passes `runtime.query(question, mode=...)` to `evaluate_cases`, writes `report.to_dict()` with UTF-8 indentation, then closes the runtime in `finally`.

- [ ] **Step 1: Write the failing CLI parsing and report-writing tests**

```python
from scripts import evaluate


def test_evaluate_main_requires_explicit_real_flag() -> None:
    with pytest.raises(SystemExit) as error:
        evaluate.main(["--golden", "golden.jsonl", "--output", "report.json"])

    assert error.value.code == 2


def test_evaluate_main_writes_report_with_injected_runner(tmp_path: Path) -> None:
    golden = tmp_path / "golden.jsonl"
    output = tmp_path / "report.json"
    golden.write_text(
        '{"id":"answer","question":"启动前检查什么？","expects_evidence":true,'
        '"expected_citations":[{"source_file":"pump.pdf","page_number":7,'
        '"chunk_id":"pump-p7-c1"}]}\\n',
        encoding="utf-8",
    )

    exit_code = evaluate.main(
        ["--real", "--golden", str(golden), "--output", str(output)],
        runtime_factory=lambda: FakeRuntime(),
    )

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["success_rate"] == 1.0
```

Give `FakeRuntime` a real `query(question, *, mode, timeout)` method returning a `QueryResult` and seconds plus a `close()` method. The test must never import or create a real LightRAG backend.

- [ ] **Step 2: Run the CLI tests and verify they fail**

Run: `python -m pytest tests/test_evaluation.py -q`

Expected: FAIL because `scripts.evaluate` does not exist.

- [ ] **Step 3: Implement the guarded CLI and example file**

```python
def main(argv: Sequence[str] | None = None, *, runtime_factory: Callable[[], Runtime] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="允许调用已有 LightRAG 索引和模型")
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=SUPPORTED_QUERY_MODES, default="mix")
    args = parser.parse_args(argv)
    if not args.real:
        parser.error("评测调用真实服务，必须显式传入 --real")
    cases = load_golden_cases(args.golden)
    runtime = (runtime_factory or _build_runtime)()
    try:
        def query(question: str) -> tuple[QueryResult, float]:
            return runtime.query(question, mode=args.mode)

        report = evaluate_cases(cases, query)
    finally:
        runtime.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\\n",
        encoding="utf-8",
    )
    return 0 if report.success_rate == 1.0 else 1
```

Create an example JSONL with exactly one evidence case using `example-manual.pdf`, page 1 and `example-p1-c1`, plus one no-evidence case. Its top comment is not permitted because JSONL must be parseable; describe that it is illustrative in README instead.

- [ ] **Step 4: Run the CLI and evaluator tests and verify they pass**

Run: `python -m pytest tests/test_evaluation.py -q`

Expected: PASS without network or API keys.

- [ ] **Step 5: Commit the command**

```powershell
git add scripts/evaluate.py data/evaluation/golden_questions.example.jsonl tests/test_evaluation.py
git commit -m "feat: add explicit real RAG evaluation command"
```

### Task 4: Document the evaluation workflow

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-29-rag-qa-simplification-design.md`
- Test: `tests/test_evaluation.py`

**Interfaces:**
- README tells operators how to copy the example to a local golden JSONL, replace every example citation with verified parser/ingestion metadata, run the explicit real command, and read the generated JSON report.

- [ ] **Step 1: Write the failing documentation-contract test**

```python
def test_readme_documents_explicit_real_evaluation_command() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "scripts\\evaluate.py --real" in readme
    assert "golden_questions.example.jsonl" in readme
    assert "Recall@5" in readme
    assert "Ragas" in readme
```

- [ ] **Step 2: Run the documentation test and verify it fails**

Run: `python -m pytest tests/test_evaluation.py::test_readme_documents_explicit_real_evaluation_command -q`

Expected: FAIL because the README has no evaluation section.

- [ ] **Step 3: Add concise operator documentation**

Add a “质量评测” section to README that contains this exact PowerShell command, with user-selected real paths:

```powershell
python scripts\evaluate.py --real --golden data\evaluation\golden_questions.jsonl --output dist\evaluation-report.json
```

Define each report metric, state that actual labels must be verified against parsed chunk metadata, state that Ragas is not installed in the first version, and state that the command may call the external model and requires a ready index plus local API key.

- [ ] **Step 4: Run documentation and full default test suite**

Run: `python -m pytest -q`

Expected: PASS without network or API keys.

Run: `ruff check .`

Expected: PASS.

- [ ] **Step 5: Commit the documentation**

```powershell
git add README.md docs/superpowers/specs/2026-07-29-rag-qa-simplification-design.md tests/test_evaluation.py
git commit -m "docs: describe RAG evaluation workflow"
```

## Plan Self-Review

- Spec coverage: Tasks 1–2 implement all deterministic golden-set metrics; Task 3 provides explicit real execution and JSON reporting; Task 4 documents verified labels, external-call safety and the decision not to use Ragas.
- Placeholder scan: every task names files, interfaces, test commands and expected results. The code excerpts give concrete parsing, evaluation and CLI control flow.
- Type consistency: the loader produces `GoldenCase`; the evaluator consumes `GoldenCase` and `QueryResult`; the CLI adapts the existing `LightRAGRuntime.query` return value to the evaluator callable.
