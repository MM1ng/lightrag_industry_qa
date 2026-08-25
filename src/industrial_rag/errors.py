"""Unified error model for Phase 2 API."""

from __future__ import annotations

from typing import Any


class AppErrorCode:
    unauthorized = "UNAUTHORIZED"
    admin_permission_required = "ADMIN_PERMISSION_REQUIRED"
    knowledge_base_not_found = "knowledge_base_not_found"
    document_not_found = "document_not_found"
    generation_not_found = "generation_not_found"
    update_job_not_found = "update_job_not_found"
    duplicate_document = "duplicate_document"
    no_change = "no_change"
    knowledge_base_busy = "knowledge_base_busy"
    task_already_running = "task_already_running"
    invalid_state_transition = "invalid_state_transition"
    generation_invalid_state = "generation_invalid_state"
    generation_validation_failed = "generation_validation_failed"
    generation_validation_required = "generation_validation_required"
    generation_validation_stale = "generation_validation_stale"
    concurrent_promote = "concurrent_promote"
    unsupported_file_type = "unsupported_file_type"
    file_too_large = "file_too_large"
    invalid_pdf = "invalid_pdf"
    storage_failure = "storage_failure"
    task_not_found = "task_not_found"
    knowledge_base_deleting = "knowledge_base_deleting"
    index_not_ready = "index_not_ready"
    kb_protected_from_delete = "kb_protected_from_delete"
    path_traversal_rejected = "path_traversal_rejected"
    empty_file = "empty_file"
    retrieval_trace_not_found = "RETRIEVAL_TRACE_NOT_FOUND"
    feedback_not_found = "FEEDBACK_NOT_FOUND"
    query_rewrite_ambiguous = "QUERY_REWRITE_AMBIGUOUS"
    query_rewrite_failed = "QUERY_REWRITE_FAILED"


HTTP_STATUS_MAP: dict[str, int] = {
    AppErrorCode.unauthorized: 401,
    AppErrorCode.admin_permission_required: 403,
    AppErrorCode.knowledge_base_not_found: 404,
    AppErrorCode.document_not_found: 404,
    AppErrorCode.generation_not_found: 404,
    AppErrorCode.update_job_not_found: 404,
    AppErrorCode.task_not_found: 404,
    AppErrorCode.duplicate_document: 409,
    AppErrorCode.no_change: 200,
    AppErrorCode.knowledge_base_busy: 409,
    AppErrorCode.task_already_running: 409,
    AppErrorCode.invalid_state_transition: 409,
    AppErrorCode.generation_invalid_state: 409,
    AppErrorCode.generation_validation_failed: 422,
    AppErrorCode.generation_validation_required: 409,
    AppErrorCode.generation_validation_stale: 409,
    AppErrorCode.concurrent_promote: 409,
    AppErrorCode.knowledge_base_deleting: 423,
    AppErrorCode.kb_protected_from_delete: 403,
    AppErrorCode.unsupported_file_type: 415,
    AppErrorCode.file_too_large: 413,
    AppErrorCode.empty_file: 422,
    AppErrorCode.invalid_pdf: 422,
    AppErrorCode.storage_failure: 500,
    AppErrorCode.index_not_ready: 503,
    AppErrorCode.path_traversal_rejected: 400,
    AppErrorCode.retrieval_trace_not_found: 404,
    AppErrorCode.query_rewrite_ambiguous: 422,
    AppErrorCode.query_rewrite_failed: 422,
}


class AppError(Exception):
    """Application-level error with code and HTTP status.

    Always caught by the FastAPI exception handler; never leaks to clients.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code or HTTP_STATUS_MAP.get(code, 500)
        self.details = details or {}
