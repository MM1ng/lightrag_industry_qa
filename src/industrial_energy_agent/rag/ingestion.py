"""Protected manual registry and recoverable LightRAG ingestion service."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from industrial_energy_agent.domain.enums import IngestJobStatus
from industrial_energy_agent.domain.errors import DomainValidationError
from industrial_energy_agent.persistence.ingest_job_repository import (
    IngestJob,
    IngestJobRepository,
)
from industrial_energy_agent.rag.base import (
    CitationSource,
    IngestResult,
    RAGConflictError,
    RAGDocument,
    ReconciliationResult,
    TrackStatus,
)
from industrial_energy_agent.rag.document_parser import DocumentChunk

CHUNKING_VERSION = "physical-page-char-v1-1800-180"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_child(root: Path, relative: str, *, expected_suffix: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or candidate.suffix.casefold() != expected_suffix:
        raise DomainValidationError("registered path has an invalid type")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise DomainValidationError("registered path escapes its configured root")
    return resolved


class UnregisteredDocumentError(LookupError):
    """A caller attempted to ingest anything outside the internal registry."""


class SimulatedWorkerCrash(BaseException):
    """Fault-injection signal that deliberately bypasses normal exception handling."""


class RegisteredDocument(BaseModel):
    """Immutable registry snapshot used to create a deterministic remote marker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1, max_length=160)
    source_file: str = Field(min_length=1, max_length=255)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parser_name: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    chunking_version: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    embedding_dimension: int = Field(ge=1)
    namespace: str = Field(min_length=1, max_length=128)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=1)
    remote_file_source: str = Field(min_length=1, max_length=255)
    chunk_ids: tuple[str, ...]
    manifest_marker: str = Field(min_length=1, repr=False)
    rag_text: str = Field(min_length=1, repr=False)


class IngestionRAGAdapter(Protocol):
    """Narrow remote boundary needed by the ingestion worker."""

    def ingest_documents(self, documents: Sequence[RAGDocument]) -> IngestResult: ...

    def track_status(self, track_id: str) -> TrackStatus: ...

    def reconcile_file_source(
        self,
        file_source: str,
        *,
        track_id: str,
        expected_marker: str,
    ) -> ReconciliationResult: ...


class _LeaseHeartbeat:
    """Renew one owned lease while synchronous remote work is in flight."""

    def __init__(
        self,
        *,
        jobs: IngestJobRepository,
        job_id: str,
        owner: str,
        clock: Callable[[], datetime],
        lease_seconds: int,
        interval_seconds: float,
    ) -> None:
        self._jobs = jobs
        self._job_id = job_id
        self._owner = owner
        self._clock = clock
        self._lease_seconds = lease_seconds
        self._interval_seconds = interval_seconds
        self._stop = Event()
        self._thread = Thread(target=self._run, name=f"lease-heartbeat-{job_id}", daemon=True)
        self.failed = False

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._jobs.heartbeat(
                    self._job_id,
                    owner=self._owner,
                    lease_until=self._clock() + timedelta(seconds=self._lease_seconds),
                )
            except Exception:
                self.failed = True
                return

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()


class DocumentRegistry:
    """Allowlist built only from protected manuals and trusted processed outputs."""

    def __init__(self, documents: Sequence[RegisteredDocument]) -> None:
        indexed = {document.document_id: document for document in documents}
        if not indexed or len(indexed) != len(documents):
            raise DomainValidationError("document registry must be non-empty and unique")
        self._documents = indexed

    @classmethod
    def from_processed_manuals(
        cls,
        *,
        manual_dir: Path,
        processed_dir: Path,
        embedding_model: str,
        embedding_dimension: int,
        namespace: str,
    ) -> DocumentRegistry:
        manual_root = manual_dir.resolve()
        processed_root = processed_dir.resolve()
        if not manual_root.is_dir() or not processed_root.is_dir():
            raise DomainValidationError("manual and processed registry directories must exist")
        manifest_path = processed_root / "manuals_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise DomainValidationError("manual registry manifest is unreadable") from error
        entries = manifest.get("documents") if isinstance(manifest, dict) else None
        if not isinstance(entries, list) or manifest.get("document_count") != len(entries):
            raise DomainValidationError("manual registry manifest has an invalid document count")

        documents: list[RegisteredDocument] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise DomainValidationError("manual registry entry must be an object")
            documents.append(
                cls._register_entry(
                    entry=entry,
                    manual_root=manual_root,
                    processed_root=processed_root,
                    embedding_model=embedding_model,
                    embedding_dimension=embedding_dimension,
                    namespace=namespace,
                )
            )
        return cls(documents)

    @staticmethod
    def _register_entry(
        *,
        entry: dict[str, object],
        manual_root: Path,
        processed_root: Path,
        embedding_model: str,
        embedding_dimension: int,
        namespace: str,
    ) -> RegisteredDocument:
        required_strings = (
            "document_id",
            "source_file",
            "source_sha256",
            "parser_name",
            "parser_version",
            "chunks_file",
        )
        values: dict[str, str] = {}
        for field_name in required_strings:
            value = entry.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(f"manual registry field {field_name} is required")
            values[field_name] = value

        source_file = values["source_file"]
        if Path(source_file).name != source_file:
            raise DomainValidationError("manual source must be a registered basename")
        source_path = _safe_child(manual_root, source_file, expected_suffix=".pdf")
        chunks_path = _safe_child(
            processed_root,
            values["chunks_file"],
            expected_suffix=".jsonl",
        )
        if not source_path.is_file() or not chunks_path.is_file():
            raise DomainValidationError("registered manual source or chunks are missing")
        if _sha256_file(source_path) != values["source_sha256"]:
            raise DomainValidationError("registered manual source hash changed")

        chunks: list[DocumentChunk] = []
        try:
            for line in chunks_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    chunks.append(DocumentChunk.model_validate_json(line))
        except (OSError, ValueError) as error:
            raise DomainValidationError("registered manual chunks are invalid") from error
        if not chunks or entry.get("chunk_count") != len(chunks):
            raise DomainValidationError("registered manual chunk count does not match")
        if any(
            chunk.source_file != source_file
            or chunk.source_sha256 != values["source_sha256"]
            or chunk.parser_name != values["parser_name"]
            or chunk.parser_version != values["parser_version"]
            for chunk in chunks
        ):
            raise DomainValidationError("registered chunk provenance does not match its manifest")

        body = "\n\n".join(
            f"[chunk_id={chunk.chunk_id};page={chunk.page_number}]\n{chunk.text}"
            for chunk in chunks
        )
        content_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
        fingerprint_fields = {
            "document_id": values["document_id"],
            "source_sha256": values["source_sha256"],
            "parser_name": values["parser_name"],
            "parser_version": values["parser_version"],
            "chunking_version": CHUNKING_VERSION,
            "embedding_model": embedding_model,
            "embedding_dimension": embedding_dimension,
            "namespace": namespace,
            "content_sha256": content_sha256,
        }
        fingerprint = hashlib.sha256(
            _canonical_json(fingerprint_fields).encode("utf-8")
        ).hexdigest()
        document_id = values["document_id"]
        remote_file_source = f"energyops-{document_id[:120]}-{fingerprint[:16]}.txt"
        marker = {**fingerprint_fields, "fingerprint": f"sha256:{fingerprint}"}
        manifest_marker = f"ENERGYOPS_INGEST_MANIFEST {_canonical_json(marker)}"
        rag_text = f"{manifest_marker}\n\n{body}"
        return RegisteredDocument(
            document_id=document_id,
            source_file=source_file,
            source_sha256=values["source_sha256"],
            parser_name=values["parser_name"],
            parser_version=values["parser_version"],
            chunking_version=CHUNKING_VERSION,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
            namespace=namespace,
            content_sha256=content_sha256,
            idempotency_key=f"ingest:sha256:{fingerprint}",
            remote_file_source=remote_file_source,
            chunk_ids=tuple(chunk.chunk_id for chunk in chunks),
            manifest_marker=manifest_marker,
            rag_text=rag_text,
        )

    def get(self, document_id: str) -> RegisteredDocument:
        try:
            return self._documents[document_id]
        except KeyError:
            raise UnregisteredDocumentError("document is not registered for ingestion") from None

    def resolve_sources(self, source_ids: tuple[str, ...]) -> list[CitationSource]:
        by_remote_source = {
            document.remote_file_source: document for document in self._documents.values()
        }
        resolved: list[CitationSource] = []
        for source_id in source_ids:
            document = by_remote_source.get(source_id) or self._documents.get(source_id)
            if document is not None:
                resolved.append(
                    CitationSource(
                        reference_id=source_id,
                        file_source=document.remote_file_source,
                        document_id=document.document_id,
                        chunk_ids=document.chunk_ids,
                    )
                )
        return resolved


class IngestionService:
    """Coordinate local leases and non-idempotent remote insert side effects."""

    def __init__(
        self,
        *,
        registry: DocumentRegistry,
        jobs: IngestJobRepository,
        rag: IngestionRAGAdapter,
        worker_id: str,
        clock: Callable[[], datetime] = _utc_now,
        lease_seconds: int = 60,
        heartbeat_interval_seconds: float | None = None,
        track_poll_attempts: int = 60,
        track_poll_interval_seconds: float = 2.0,
    ) -> None:
        heartbeat_interval = heartbeat_interval_seconds or min(10.0, lease_seconds / 3)
        if (
            not worker_id.strip()
            or lease_seconds <= 0
            or heartbeat_interval <= 0
            or track_poll_attempts <= 0
            or track_poll_interval_seconds < 0
        ):
            raise ValueError("worker_id and a positive lease are required")
        self._registry = registry
        self._jobs = jobs
        self._rag = rag
        self._worker_id = worker_id
        self._clock = clock
        self._lease_seconds = lease_seconds
        self._heartbeat_interval_seconds = heartbeat_interval
        self._track_poll_attempts = track_poll_attempts
        self._track_poll_interval_seconds = track_poll_interval_seconds

    @staticmethod
    def _track_outcome(status: TrackStatus) -> str:
        counts = {key.upper(): value for key, value in status.status_summary.items()}
        if any(counts.get(key, 0) > 0 for key in ("FAILED", "ERROR", "CANCELLED")):
            return "failed"
        if status.total_count > 0 and counts.get("PROCESSED", 0) >= status.total_count:
            return "succeeded"
        return "pending"

    def _poll_track(self, track_id: str) -> str:
        for attempt in range(self._track_poll_attempts):
            outcome = self._track_outcome(self._rag.track_status(track_id))
            if outcome != "pending":
                return outcome
            if attempt + 1 < self._track_poll_attempts:
                time.sleep(self._track_poll_interval_seconds)
        return "pending"

    def submit_document(self, document_id: str) -> IngestJob:
        document = self._registry.get(document_id)
        return self._jobs.create_pending(
            document.document_id,
            document.idempotency_key,
            remote_file_source=document.remote_file_source,
        )

    def submit_path(self, path: str | Path) -> IngestJob:
        del path
        raise UnregisteredDocumentError("caller-provided paths and URLs are not accepted")

    def run_once(self) -> IngestJob | None:
        now = self._clock()
        job = self._jobs.claim_next(
            owner=self._worker_id,
            lease_until=now + timedelta(seconds=self._lease_seconds),
        )
        if job is None:
            return None
        try:
            document = self._registry.get(job.document_id)
        except UnregisteredDocumentError:
            return self._jobs.mark_failed(
                job.job_id,
                owner=self._worker_id,
                error="registered ingestion document is unavailable",
            )
        if document.remote_file_source != job.remote_file_source:
            return self._jobs.mark_failed(
                job.job_id,
                owner=self._worker_id,
                error="registered ingestion fingerprint changed",
            )

        self._jobs.mark_remote_call_started(job.job_id, owner=self._worker_id)
        heartbeat = _LeaseHeartbeat(
            jobs=self._jobs,
            job_id=job.job_id,
            owner=self._worker_id,
            clock=self._clock,
            lease_seconds=self._lease_seconds,
            interval_seconds=self._heartbeat_interval_seconds,
        )
        remote_error: str | None = None
        outcome = "unknown"
        heartbeat.start()
        try:
            result = self._rag.ingest_documents(
                [RAGDocument(text=document.rag_text, file_source=document.remote_file_source)]
            )
            self._jobs.mark_remote_accepted(
                job.job_id,
                owner=self._worker_id,
                track_id=result.track_id,
            )
            outcome = self._poll_track(result.track_id)
        except RAGConflictError:
            remote_error = "duplicate remote file source requires reconciliation"
        except Exception:
            remote_error = "remote insert or track outcome is unknown"
        finally:
            heartbeat.stop()
        if remote_error is not None or heartbeat.failed:
            return self._jobs.mark_reconcile_required(
                job.job_id,
                remote_error or "ingestion lease heartbeat failed",
                owner=self._worker_id,
            )
        if outcome == "succeeded":
            return self._jobs.mark_succeeded(job.job_id, owner=self._worker_id)
        if outcome == "failed":
            return self._jobs.mark_failed(
                job.job_id,
                owner=self._worker_id,
                error="remote ingestion track failed",
            )
        current = self._jobs.get(job.job_id)
        if current is None:
            raise RuntimeError("ingest job disappeared while track remained pending")
        return current

    def recover_expired(self, job_id: str) -> IngestJob:
        job = self._jobs.get(job_id)
        now = self._clock()
        if job is None or not job.remote_call_started:
            raise DomainValidationError("job is not an ambiguous remote call")
        if job.status is IngestJobStatus.RUNNING:
            if job.lease_expires_at is None or job.lease_expires_at > now:
                raise DomainValidationError("running ingest job lease has not expired")
        elif job.status is not IngestJobStatus.RECONCILE_REQUIRED:
            raise DomainValidationError("job does not require remote reconciliation")
        document = self._registry.get(job.document_id)
        if document.remote_file_source != job.remote_file_source:
            raise DomainValidationError("registered ingestion fingerprint changed")
        if job.track_id is None:
            return self._jobs.mark_reconcile_required(
                job.job_id,
                "remote insert may have succeeded before track persistence",
                owner=job.lease_owner,
            )
        try:
            result = self._rag.reconcile_file_source(
                job.remote_file_source,
                track_id=job.track_id,
                expected_marker=document.manifest_marker,
            )
        except Exception:
            return self._jobs.mark_reconcile_required(
                job.job_id,
                "remote reconciliation probes failed",
                owner=job.lease_owner,
            )
        if result.confirmed is True:
            return self._jobs.mark_reconciled_succeeded(job.job_id)
        if result.confirmed is False and job.attempt_count < job.max_attempts:
            return self._jobs.requeue_after_confirmed_absent(job.job_id)
        return self._jobs.mark_reconcile_required(
            job.job_id,
            "remote reconciliation was inconclusive",
            owner=job.lease_owner,
        )

    def recover_next_expired(self) -> IngestJob | None:
        job = self._jobs.get_next_expired_remote_call()
        return None if job is None else self.recover_expired(job.job_id)
