"""Pydantic schemas for KB API request / response."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

KBName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class KnowledgeBaseCreate(BaseModel):
    name: KBName
    description: str | None = Field(default=None, max_length=2000)


class KnowledgeBaseUpdate(BaseModel):
    name: KBName | None = None
    description: str | None = Field(default=None, max_length=2000)


class KnowledgeBaseSummary(BaseModel):
    id: str
    name: str
    description: str | None = None
    status: str
    document_count: int
    active_document_count: int = 0
    chunk_count: int = 0
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None


class KnowledgeBaseDetail(BaseModel):
    id: str
    name: str
    description: str | None = None
    status: str
    parser_name: str
    parser_version: str | None = None
    chunking_strategy: str
    chunking_version: str
    chunking_config: dict[str, Any] | None = None
    embedding_model: str
    embedding_dimension: int
    vector_backend: str = "nano"
    active_vector_generation: str | None = None
    document_count: int
    active_document_count: int
    chunk_count: int
    entity_count: int | None = None
    relation_count: int | None = None
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    deleted_at: datetime | str | None = None
    last_error: str | None = None


class VectorBackendUpdateRequest(BaseModel):
    target_backend: str


class VectorBackendTaskResponse(BaseModel):
    task_id: str
    knowledge_base_id: str
    status: str
    target_backend: str
    idempotent: bool = False


class DeleteTaskResponse(BaseModel):
    task_id: str
    knowledge_base_id: str
    status: str


class DocumentSummary(BaseModel):
    id: str
    knowledge_base_id: str
    original_file_name: str
    file_hash: str
    file_size: int
    version: int
    status: str
    parse_status: str
    index_status: str
    page_count: int | None = None
    parent_chunk_count: int = 0
    child_chunk_count: int = 0
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    last_error: str | None = None


class DocumentTaskResponse(BaseModel):
    task_id: str
    document_id: str
    status: str


class TaskSummary(BaseModel):
    id: str
    knowledge_base_id: str
    document_id: str | None = None
    task_type: str
    status: str
    progress: float = 0.0
    current_stage: str | None = None
    attempt: int = 0
    created_at: datetime | str | None = None
    started_at: datetime | str | None = None
    finished_at: datetime | str | None = None
    error_code: str | None = None
    error_message: str | None = None


class TaskDetail(TaskSummary):
    payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    cleanup_steps: list[dict[str, Any]] | None = None


class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    offset: int
    limit: int


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] | None = None


class DocumentUpdateResponse(BaseModel):
    status: str
    knowledge_base_id: str
    document_id: str
    operation: str = "add"
    job_id: str | None = None
    candidate_generation_id: str | None = None
    candidate_generation: str | None = None
    metrics: dict[str, Any] | None = None
    reason: str | None = None


class GenerationSummary(BaseModel):
    id: str
    knowledge_base_id: str
    generation: str
    status: str
    backend: str
    collections: dict[str, str] | None = None
    created_at: datetime | str | None = None
    activated_at: datetime | str | None = None
    last_error: str | None = None


class GenerationActionResponse(BaseModel):
    status: str
    knowledge_base_id: str
    generation_id: str
    idempotent: bool = False
    active_generation_id: str | None = None
    previous_generation_id: str | None = None


class GenerationValidateResponse(BaseModel):
    generation_id: str
    knowledge_base_id: str
    passed: bool
    gates: dict[str, Any]
    report: dict[str, Any] | None = None


class UpdateJobSummary(BaseModel):
    job_id: str
    knowledge_base_id: str
    base_generation_id: str | None = None
    candidate_generation_id: str | None = None
    operation: str
    document_id: str | None = None
    old_content_sha256: str | None = None
    new_content_sha256: str | None = None
    status: str
    current_stage: str | None = None
    retry_count: int = 0
    error_code: str | None = None
    sanitized_error_message: str | None = None
    started_at: datetime | str | None = None
    finished_at: datetime | str | None = None
    created_by: str = "api"
    approved_by: str | None = None
    metrics: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
