from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from industrial_energy_agent.domain.enums import IngestJobStatus
from industrial_energy_agent.domain.errors import DomainValidationError
from industrial_energy_agent.persistence.database import Database
from industrial_energy_agent.persistence.ingest_job_repository import IngestJobRepository


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 21, 10, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def _repository(tmp_path: Path) -> tuple[IngestJobRepository, MutableClock, Database]:
    database = Database(tmp_path / "energyops.sqlite")
    database.initialize()
    clock = MutableClock()
    return IngestJobRepository(database, clock=clock.now), clock, database


def test_expired_running_job_is_reclaimed_but_reconcile_job_is_not(
    tmp_path: Path,
) -> None:
    repository, clock, _ = _repository(tmp_path)
    expired = repository.create_pending("doc-a", "key-a")
    repository.claim(expired.job_id, owner="worker-a", lease_until=clock.now())

    reclaimed = repository.claim_next(owner="worker-b")

    assert reclaimed is not None
    assert reclaimed.job_id == expired.job_id
    assert reclaimed.lease_owner == "worker-b"
    assert reclaimed.attempt_count == 2
    reconcile = repository.create_pending("doc-b", "key-b")
    repository.mark_reconcile_required(reconcile.job_id, "remote outcome unknown")
    assert repository.get(reconcile.job_id).status is IngestJobStatus.RECONCILE_REQUIRED  # type: ignore[union-attr]
    assert repository.claim_next(owner="worker-c") is None


def test_create_pending_is_locally_idempotent_even_after_success(tmp_path: Path) -> None:
    repository, clock, _ = _repository(tmp_path)
    first = repository.create_pending("doc-a", "key-a")
    duplicate = repository.create_pending("doc-a", "key-a")
    running = repository.claim(
        first.job_id,
        owner="worker-a",
        lease_until=clock.now() + timedelta(seconds=60),
    )
    succeeded = repository.mark_succeeded(running.job_id, owner="worker-a")

    assert duplicate == first
    assert succeeded.status is IngestJobStatus.SUCCEEDED
    assert repository.create_pending("doc-a", "key-a") == succeeded
    with pytest.raises(DomainValidationError, match="idempotency"):
        repository.create_pending("different-doc", "key-a")


def test_heartbeat_and_finalization_require_the_current_lease_owner(tmp_path: Path) -> None:
    repository, clock, _ = _repository(tmp_path)
    pending = repository.create_pending("doc-a", "key-a")
    running = repository.claim(
        pending.job_id,
        owner="worker-a",
        lease_until=clock.now() + timedelta(seconds=60),
    )

    with pytest.raises(DomainValidationError, match="owner"):
        repository.heartbeat(
            running.job_id,
            owner="worker-b",
            lease_until=clock.now() + timedelta(seconds=120),
        )
    with pytest.raises(DomainValidationError, match="owner"):
        repository.mark_succeeded(running.job_id, owner="worker-b")

    repository.mark_remote_call_started(running.job_id, owner="worker-a")
    clock.advance(61)
    assert repository.claim_next(owner="worker-b") is None
    reconciled = repository.mark_reconcile_required(
        running.job_id,
        "remote outcome unknown",
        owner="worker-a",
    )
    assert reconciled.status is IngestJobStatus.RECONCILE_REQUIRED


def test_expired_lease_owner_cannot_finalize_before_reclaim(tmp_path: Path) -> None:
    repository, clock, _ = _repository(tmp_path)
    pending = repository.create_pending("doc-a", "key-a")
    running = repository.claim(
        pending.job_id,
        owner="worker-a",
        lease_until=clock.now() + timedelta(seconds=1),
    )
    clock.advance(2)

    with pytest.raises(DomainValidationError, match="expired"):
        repository.mark_succeeded(running.job_id, owner="worker-a")

    reclaimed = repository.claim_next(owner="worker-b")
    assert reclaimed is not None
    assert reclaimed.lease_owner == "worker-b"


def test_failed_job_requires_explicit_retry_and_honors_max_attempts(tmp_path: Path) -> None:
    repository, clock, _ = _repository(tmp_path)
    pending = repository.create_pending("doc-a", "key-a", max_attempts=2)
    first = repository.claim(
        pending.job_id,
        owner="worker-a",
        lease_until=clock.now() + timedelta(seconds=60),
    )
    failed = repository.mark_failed(
        first.job_id,
        owner="worker-a",
        error="DASHSCOPE_API_KEY=must-not-persist",
    )

    assert failed.status is IngestJobStatus.FAILED
    assert "must-not-persist" not in (failed.last_error or "")
    assert repository.claim_next(owner="worker-b") is None
    repository.retry_failed(failed.job_id)
    second = repository.claim(
        failed.job_id,
        owner="worker-b",
        lease_until=clock.now() + timedelta(seconds=60),
    )
    repository.mark_failed(second.job_id, owner="worker-b", error="retry exhausted")

    with pytest.raises(DomainValidationError, match="maximum attempts"):
        repository.retry_failed(failed.job_id)


def test_two_workers_atomically_compete_for_only_one_pending_job(tmp_path: Path) -> None:
    repository, clock, database = _repository(tmp_path)
    pending = repository.create_pending("doc-a", "key-a")
    barrier = Barrier(2)

    def claim(owner: str):
        worker_repository = IngestJobRepository(database, clock=clock.now)
        barrier.wait()
        return worker_repository.claim_next(owner=owner)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("worker-a", "worker-b")))

    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0].job_id == pending.job_id
    assert claimed[0].attempt_count == 1
