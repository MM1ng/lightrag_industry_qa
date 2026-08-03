"""Validated single-variable retrieval ablation configurations."""

from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_ABLATION_MODES = ("mix", "naive", "hybrid", "local", "global")
BASELINE_TOP_K = 12
BASELINE_CHUNK_TOP_K = 20


@dataclass(frozen=True, slots=True)
class AblationConfig:
    experiment_id: str
    query_mode: str
    top_k: int
    chunk_top_k: int

    def __post_init__(self) -> None:
        if self.query_mode not in SUPPORTED_ABLATION_MODES:
            raise ValueError(f"query_mode must be one of {SUPPORTED_ABLATION_MODES}")
        if self.top_k <= 0 or self.chunk_top_k < self.top_k:
            raise ValueError("top_k must be positive and chunk_top_k must be >= top_k")
        changed = self.changed_variables
        if len(changed) > 1:
            raise ValueError("an ablation must change one variable")

    @property
    def changed_variables(self) -> tuple[str, ...]:
        changed: list[str] = []
        if self.query_mode != "mix":
            changed.append("query_mode")
        if self.top_k != BASELINE_TOP_K:
            changed.append("top_k")
        if self.chunk_top_k != BASELINE_CHUNK_TOP_K:
            changed.append("chunk_top_k")
        return tuple(changed)

    def to_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "query_mode": self.query_mode,
            "top_k": self.top_k,
            "chunk_top_k": self.chunk_top_k,
            "changed_variables": list(self.changed_variables),
        }
