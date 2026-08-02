"""Official API entry for Phase 6 load tests.

Same FastAPI application as production (``industrial_rag.api:app``) with the
fixed qwen-plus-2025-07-28 recorder injected into the service layer (no
fallback, no thinking, shared disk cache). This keeps the load test at the
official HTTP boundary while making tokens/model observable.
"""

from __future__ import annotations

import functools
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from evaluation.experiments.parser_backend.fixed_model_llm import FixedModelLLM  # noqa: E402
import industrial_rag.lightrag_service as service_module  # noqa: E402

FIXED_MODEL = "qwen-plus-2025-07-28"
_llm = FixedModelLLM(
    model=FIXED_MODEL,
    api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    enable_thinking=False,
    cache_path=Path(__file__).resolve().parent / "cache" / "phase6_answers.jsonl",
    config_hash="phase6_e2e_v1",
)
service_module.build_official_backend = functools.partial(
    service_module.build_official_backend, llm_model_func=_llm
)

from industrial_rag.api import app  # noqa: E402
