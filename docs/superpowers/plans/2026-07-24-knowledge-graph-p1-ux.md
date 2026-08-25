# Knowledge Graph P1 UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve knowledge-graph page UX without touching runtime/ingest.

**Architecture:** Continue PyVis (scheme A). Display-layer helpers + light vis-network JS. Streamlit toggle for labels. Graph cache stays separate from LightRAGRuntime.

**Tech Stack:** Python 3.11, NetworkX, PyVis, Streamlit, pytest

## Decision
Scheme A continue PyVis (recommended). Scheme B replace component rejected.

## Files
- Create: src/industrial_rag/graph_display_mapping.py
- Modify: src/industrial_rag/graph_visualizer.py
- Modify: app/streamlit_app.py
- Modify: tests/test_graph_visualizer.py
- Modify: README.md (brief)

## Constraints
No runtime/service/ingest/GraphML/.env changes. No st.cache_resource.clear(). No commit until user confirms.
