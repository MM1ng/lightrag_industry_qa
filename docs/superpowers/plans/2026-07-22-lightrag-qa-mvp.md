# Industrial Centrifugal Pump LightRAG QA MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-page LightRAG knowledge-base QA application over the two centrifugal-pump PDFs in `data/manuals`, with trustworthy filename/page citations.

**Architecture:** PyMuPDF produces deterministic within-page chunks in one JSONL file. A small service configures the locally installed official LightRAG 1.5.4 API for BaiLian, inserts chunks with encoded page provenance in `file_paths`, retrieves structured evidence with `aquery_data`, and renders only metadata-derived citations. Streamlit calls that service directly; no Agent, LangGraph, REST service, database, or workflow layer is involved.

**Tech Stack:** Python 3.11, PyMuPDF, LightRAG 1.5.4, BaiLian OpenAI-compatible API, Streamlit, pytest, Ruff

---

### Task 1: Lock the minimal project contract

**Files:**
- Modify: `pyproject.toml`
- Create: `requirements.txt`
- Modify: `.env.example`

- [ ] Write `tests/test_lightrag_service.py` assertions for the four public modes, model names, Beijing base URL, and embedding dimension 1024.
- [ ] Run `python -m pytest tests/test_lightrag_service.py -q` and confirm import/config failures.
- [ ] Implement the matching settings in `src/industrial_rag/config.py` and declare only the MVP runtime/test dependencies.
- [ ] Re-run the focused test and confirm it passes.

### Task 2: Parse both manuals without changing them

**Files:**
- Create: `src/industrial_rag/document_parser.py`
- Replace: `scripts/parse_manuals.py`
- Create: `tests/test_document_parser.py`

- [ ] Write tests that create a small PDF, scan PDF suffixes case-insensitively, extract one-based pages, preserve section/source metadata, and split long pages with overlap.
- [ ] Run `python -m pytest tests/test_document_parser.py -q` and confirm the missing implementation fails.
- [ ] Implement `scan_pdf_files`, page-local chunking, PyMuPDF extraction, stable chunk IDs, and atomic `data/processed/documents.jsonl` output.
- [ ] Re-run the focused parser test and confirm it passes.

### Task 3: Produce citations only from stored provenance

**Files:**
- Create: `src/industrial_rag/citation_formatter.py`
- Create: `tests/test_citation_formatter.py`

- [ ] Write tests for source-reference round trips, `[文档名称，第X页]` formatting, deduplication, and rejection of malformed/non-positive pages.
- [ ] Run `python -m pytest tests/test_citation_formatter.py -q` and confirm the missing implementation fails.
- [ ] Implement encoded LightRAG `file_path` provenance and deterministic citation extraction from structured retrieval data.
- [ ] Re-run the focused citation test and confirm it passes.

### Task 4: Wrap the verified LightRAG API

**Files:**
- Create: `src/industrial_rag/lightrag_service.py`
- Create: `src/industrial_rag/__init__.py`
- Extend: `tests/test_lightrag_service.py`

- [ ] Add fake-backend tests for initialization, deterministic insert IDs/file paths, four query modes, citation return, and the fixed insufficient-evidence response.
- [ ] Run the focused service test and confirm these behaviors fail before implementation.
- [ ] Implement the official 1.5.4 async API calls, BaiLian LLM/embedding functions, structured evidence retrieval, source citations, and storage-dimension preflight metadata.
- [ ] Re-run all three MVP test files and confirm they pass offline without network calls.

### Task 5: Add only the required operating surfaces

**Files:**
- Create: `scripts/inspect_environment.py`
- Create: `scripts/ingest_documents.py`
- Create: `scripts/smoke_test.py`
- Create: `app/streamlit_app.py`
- Replace: `README.md`

- [ ] Implement environment inspection without printing secrets, JSONL ingestion, offline/real smoke modes, and one Streamlit page with model/status/mode/question/answer/source/error areas and six example questions.
- [ ] Document tested Windows Conda setup, parse, ingest, query, Streamlit, test, and non-destructive index rebuild procedures.
- [ ] Keep existing out-of-scope source files present but ensure the new package, scripts, UI, and tests import none of them.

### Task 6: Verify the scoped MVP

**Files:**
- Generate: `data/processed/documents.jsonl`
- Generate on real ingestion: `lightrag_storage/*`

- [ ] Run `python -m pytest -q` and require zero failures.
- [ ] Run `ruff check .` and require zero findings.
- [ ] Run `python scripts/parse_manuals.py` and verify exactly two source PDFs produce non-empty page-aware records while source hashes remain unchanged.
- [ ] Run `python scripts/smoke_test.py` and verify fake initialization, insertion, answered query, and no-evidence query.
- [ ] With the existing process secret, run `python scripts/ingest_documents.py` followed by `python scripts/smoke_test.py --real`, requiring initialization, successful insertion, at least three real queries, citations, and an explicit no-evidence behavior check.
- [ ] Start `streamlit run app/streamlit_app.py` on a temporary local port, fetch its health endpoint, then stop it.
- [ ] Inspect Git changes and tracked files for secret material before committing the feature branch.
