from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from industrial_energy_agent.domain.enums import IngestJobStatus
from industrial_energy_agent.persistence.database import Database
from industrial_energy_agent.persistence.ingest_job_repository import IngestJobRepository
from industrial_energy_agent.rag.base import (
    IngestResult,
    RAGConflictError,
    RAGDocument,
    ReconciliationResult,
    TrackStatus,
)
from industrial_energy_agent.rag.ingest_worker import run_worker
from industrial_energy_agent.rag.ingestion import (
    DocumentRegistry,
    IngestionService,
    SimulatedWorkerCrash,
    UnregisteredDocumentError,
)


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 21, 10, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class RecordingRAGAdapter:
    def __init__(self) -> None:
        self.insert_calls = 0
        self.documents: list[RAGDocument] = []
        self.crash_after_insert = False
        self.conflict = False
        self.reconciliation: bool | None = None
        self.track_status_summary = {"PROCESSED": 1}
        self.track_status_calls = 0
        self.delay_seconds = 0.0
        self.reconciliation_markers: list[str] = []

    def ingest_documents(self, documents: list[RAGDocument]) -> IngestResult:
        self.insert_calls += 1
        self.documents.extend(documents)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.crash_after_insert:
            raise SimulatedWorkerCrash("simulated process death after remote dispatch")
        if self.conflict:
            raise RAGConflictError("conflict", retryable=False, status_code=409)
        return IngestResult(status="success", message="accepted", track_id="insert-test-001")

    def track_status(self, track_id: str) -> TrackStatus:
        self.track_status_calls += 1
        return TrackStatus(
            track_id=track_id,
            documents=(),
            total_count=1,
            status_summary=self.track_status_summary,
        )

    def reconcile_file_source(
        self,
        file_source: str,
        *,
        track_id: str,
        expected_marker: str,
    ) -> ReconciliationResult:
        self.reconciliation_markers.append(expected_marker)
        return ReconciliationResult(
            file_source=file_source,
            confirmed=self.reconciliation,
            probes=frozenset({"track_status", "documents_paginated", "query_references"}),
            track_match=self.reconciliation is True,
            paginated_match=self.reconciliation is True,
            reference_match=self.reconciliation is True,
            marker_match=self.reconciliation is True,
        )


class HeartbeatRecordingRepository(IngestJobRepository):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.heartbeat_calls = 0

    def heartbeat(self, *args, **kwargs):
        self.heartbeat_calls += 1
        return super().heartbeat(*args, **kwargs)


def _write_registry_fixture(tmp_path: Path) -> tuple[Path, Path]:
    manual_dir = tmp_path / "manuals"
    processed_dir = tmp_path / "processed" / "manuals"
    document_dir = processed_dir / "manual-2196"
    manual_dir.mkdir(parents=True)
    document_dir.mkdir(parents=True)
    source = manual_dir / "manual-2196.pdf"
    source.write_bytes(b"test-only-pdf-content")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    chunk = {
        "text": "泵启动前应检查联轴器防护罩。",
        "source_file": source.name,
        "document_title": "2196 Pump Manual",
        "page_number": 3,
        "page_start": 3,
        "page_end": 3,
        "section_title": "启动前检查",
        "chunk_id": "manual-2196:p3:c1:12345678",
        "document_type": "operation_maintenance_manual",
        "equipment_type": "centrifugal_pump",
        "parser_name": "pymupdf",
        "parser_version": "1.28.0",
        "source_sha256": source_sha256,
        "limitations": [],
        "extraction_warnings": [],
    }
    (document_dir / "chunks.jsonl").write_text(
        json.dumps(chunk, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "requested_parser": "auto",
        "document_count": 1,
        "documents": [
            {
                "source_file": source.name,
                "document_id": "manual-2196",
                "document_title": "2196 Pump Manual",
                "source_sha256": source_sha256,
                "parser_name": "pymupdf",
                "parser_version": "1.28.0",
                "page_count": 1,
                "chunk_count": 1,
                "chunks_file": "manual-2196/chunks.jsonl",
                "report_file": "manual-2196/parse_report.json",
            }
        ],
    }
    (processed_dir / "manuals_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    return manual_dir, processed_dir


def _service(
    tmp_path: Path,
) -> tuple[IngestionService, RecordingRAGAdapter, IngestJobRepository, MutableClock]:
    manual_dir, processed_dir = _write_registry_fixture(tmp_path)
    clock = MutableClock()
    database = Database(tmp_path / "energyops.sqlite")
    database.initialize()
    repository = HeartbeatRecordingRepository(
        database,
        clock=clock.now,
        default_lease_seconds=30,
    )
    registry = DocumentRegistry.from_processed_manuals(
        manual_dir=manual_dir,
        processed_dir=processed_dir,
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
        namespace="energyops-manuals-test",
    )
    rag = RecordingRAGAdapter()
    service = IngestionService(
        registry=registry,
        jobs=repository,
        rag=rag,
        worker_id="worker-test",
        clock=clock.now,
        lease_seconds=30,
        heartbeat_interval_seconds=0.01,
        track_poll_attempts=1,
        track_poll_interval_seconds=0,
    )
    return service, rag, repository, clock


def test_ingest_rejects_unregistered_path_and_document(tmp_path: Path) -> None:
    service, _, _, _ = _service(tmp_path)

    with pytest.raises(UnregisteredDocumentError):
        service.submit_path("https://example.invalid/manual.pdf")
    with pytest.raises(UnregisteredDocumentError):
        service.submit_document("missing-manual")


def test_registration_and_submission_are_deterministic_and_idempotent(tmp_path: Path) -> None:
    service, _, _, _ = _service(tmp_path)

    first = service.submit_document("manual-2196")
    second = service.submit_document("manual-2196")

    assert first == second
    assert first.idempotency_key.startswith("ingest:sha256:")
    assert first.remote_file_source.startswith("energyops-manual-2196-")
    assert first.remote_file_source.endswith(".txt")
    assert "/" not in first.remote_file_source
    assert "\\" not in first.remote_file_source


def test_registry_resolves_remote_file_source_to_local_chunk_provenance(tmp_path: Path) -> None:
    manual_dir, processed_dir = _write_registry_fixture(tmp_path)
    registry = DocumentRegistry.from_processed_manuals(
        manual_dir=manual_dir,
        processed_dir=processed_dir,
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
        namespace="energyops-manuals-test",
    )
    registered = registry.get("manual-2196")

    sources = registry.resolve_sources((registered.remote_file_source, "missing-reference"))

    assert len(sources) == 1
    assert sources[0].reference_id == registered.remote_file_source
    assert sources[0].document_id == "manual-2196"
    assert sources[0].chunk_ids == ("manual-2196:p3:c1:12345678",)


def test_worker_persists_track_id_and_never_sends_unmarked_text(tmp_path: Path) -> None:
    service, rag, repository, _ = _service(tmp_path)
    pending = service.submit_document("manual-2196")

    completed = service.run_once()

    assert completed is not None
    assert completed.status is IngestJobStatus.SUCCEEDED
    assert completed.track_id == "insert-test-001"
    assert repository.get(pending.job_id) == completed
    assert rag.insert_calls == 1
    assert rag.track_status_calls == 1
    assert rag.documents[0].file_source == pending.remote_file_source
    assert rag.documents[0].text.startswith("ENERGYOPS_INGEST_MANIFEST ")
    assert "manual-2196:p3:c1:12345678" in rag.documents[0].text


def test_remote_success_before_local_commit_requires_reconciliation(tmp_path: Path) -> None:
    service, rag, repository, clock = _service(tmp_path)
    job = service.submit_document("manual-2196")
    rag.crash_after_insert = True

    with pytest.raises(SimulatedWorkerCrash):
        service.run_once()

    ambiguous = repository.get(job.job_id)
    assert ambiguous is not None
    assert ambiguous.status is IngestJobStatus.RUNNING
    assert ambiguous.remote_call_started is True
    assert ambiguous.track_id is None
    clock.advance(31)

    recovered = service.recover_expired(job.job_id)

    assert recovered.status is IngestJobStatus.RECONCILE_REQUIRED
    assert rag.insert_calls == 1


def test_saved_track_is_reconciled_without_replaying_insert(tmp_path: Path) -> None:
    service, rag, repository, clock = _service(tmp_path)
    pending = service.submit_document("manual-2196")
    running = repository.claim(
        pending.job_id,
        owner="worker-test",
        lease_until=clock.now() + timedelta(seconds=30),
    )
    repository.mark_remote_call_started(running.job_id, owner="worker-test")
    repository.mark_remote_accepted(
        running.job_id,
        owner="worker-test",
        track_id="insert-known",
    )
    rag.reconciliation = True
    clock.advance(31)

    recovered = service.recover_expired(pending.job_id)

    assert recovered.status is IngestJobStatus.SUCCEEDED
    assert recovered.track_id == "insert-known"
    assert rag.insert_calls == 0
    assert rag.reconciliation_markers[0].startswith("ENERGYOPS_INGEST_MANIFEST ")


def test_reconcile_required_job_can_be_retried_by_reconciliation_command(tmp_path: Path) -> None:
    service, rag, repository, clock = _service(tmp_path)
    pending = service.submit_document("manual-2196")
    running = repository.claim(
        pending.job_id,
        owner="worker-test",
        lease_until=clock.now() + timedelta(seconds=30),
    )
    repository.mark_remote_call_started(running.job_id, owner="worker-test")
    repository.mark_remote_accepted(
        running.job_id,
        owner="worker-test",
        track_id="insert-known",
    )
    repository.mark_reconcile_required(
        running.job_id,
        "operator reconciliation required",
        owner="worker-test",
    )
    rag.reconciliation = True

    recovered = service.recover_expired(pending.job_id)

    assert recovered.status is IngestJobStatus.SUCCEEDED


def test_async_track_is_not_succeeded_until_processing_completes(tmp_path: Path) -> None:
    service, rag, repository, _ = _service(tmp_path)
    pending = service.submit_document("manual-2196")
    rag.track_status_summary = {"PROCESSING": 1}

    result = service.run_once()

    assert result is not None
    assert result.status is IngestJobStatus.RUNNING
    assert result.track_id == "insert-test-001"
    assert repository.get(pending.job_id).status is IngestJobStatus.RUNNING  # type: ignore[union-attr]


def test_worker_recovers_expired_tracked_job_without_manual_command(tmp_path: Path) -> None:
    service, rag, _, clock = _service(tmp_path)
    service.submit_document("manual-2196")
    rag.track_status_summary = {"PROCESSING": 1}
    running = service.run_once()
    assert running is not None
    assert running.status is IngestJobStatus.RUNNING
    clock.advance(31)
    rag.reconciliation = True

    recovered = run_worker(service, once=True, idle_seconds=0)

    assert recovered is not None
    assert recovered.status is IngestJobStatus.SUCCEEDED
    assert rag.insert_calls == 1


def test_worker_heartbeats_while_remote_call_is_running(tmp_path: Path) -> None:
    service, rag, repository, _ = _service(tmp_path)
    service.submit_document("manual-2196")
    rag.delay_seconds = 0.06

    result = service.run_once()

    assert result is not None
    assert result.status is IngestJobStatus.SUCCEEDED
    assert isinstance(repository, HeartbeatRecordingRepository)
    assert repository.heartbeat_calls >= 1


def test_duplicate_without_a_saved_track_is_not_treated_as_success(tmp_path: Path) -> None:
    service, rag, _, _ = _service(tmp_path)
    service.submit_document("manual-2196")
    rag.conflict = True

    result = service.run_once()

    assert result is not None
    assert result.status is IngestJobStatus.RECONCILE_REQUIRED
    assert rag.insert_calls == 1
    assert service.recover_expired(result.job_id).status is IngestJobStatus.RECONCILE_REQUIRED


def test_once_worker_processes_at_most_one_registered_job(tmp_path: Path) -> None:
    service, rag, _, _ = _service(tmp_path)
    service.submit_document("manual-2196")

    result = run_worker(service, once=True, idle_seconds=0)

    assert result is not None
    assert result.status is IngestJobStatus.SUCCEEDED
    assert rag.insert_calls == 1


def test_public_ingest_script_uses_business_api_not_raw_lightrag() -> None:
    project_root = Path(__file__).resolve().parents[3]
    sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in (
            project_root / "scripts" / "ingest_lightrag.py",
            project_root / "scripts" / "reconcile_ingest.py",
        )
    }
    source = sources["ingest_lightrag.py"]

    assert "/api/v1/ingest" in source
    assert '"document_ids"' in source
    assert all("/documents/text" not in value for value in sources.values())
    assert all("LIGHTRAG_API_KEY" not in value for value in sources.values())
