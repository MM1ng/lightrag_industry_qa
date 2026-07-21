from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

import pytest

from industrial_energy_agent import __version__
from industrial_energy_agent.cli import IngestionRuntime, main


class StubAdapter:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class StubService:
    def __init__(self) -> None:
        self.run_calls = 0
        self.reconciled_job_id: str | None = None
        self.recover_next_calls = 0

    def run_once(self):
        self.run_calls += 1
        return None

    def recover_expired(self, job_id: str):
        self.reconciled_job_id = job_id
        return StubJob(job_id=job_id, status="RECONCILE_REQUIRED")

    def recover_next_expired(self):
        self.recover_next_calls += 1
        return None


@dataclass(frozen=True)
class StubJob:
    job_id: str
    status: str


def test_cli_help_is_available(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    assert "EnergyOps Copilot" in capsys.readouterr().out


def test_cli_version_uses_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"energyops {__version__}"


def test_package_contains_pep561_marker() -> None:
    marker = resources.files("industrial_energy_agent").joinpath("py.typed")

    assert marker.is_file()


def test_ingest_worker_once_uses_shared_service_and_closes_adapter(capsys) -> None:
    service = StubService()
    adapter = StubAdapter()

    result = main(
        ["ingest-worker", "--once"],
        runtime_factory=lambda: IngestionRuntime(service=service, adapter=adapter),  # type: ignore[arg-type]
    )

    assert result == 0
    assert service.run_calls == 1
    assert service.recover_next_calls == 1
    assert adapter.closed is True
    assert capsys.readouterr().out.strip() == "NO_PENDING_JOB"


def test_reconcile_command_uses_shared_service(capsys) -> None:
    service = StubService()
    adapter = StubAdapter()

    result = main(
        ["reconcile-ingest", "ingest-test"],
        runtime_factory=lambda: IngestionRuntime(service=service, adapter=adapter),  # type: ignore[arg-type]
    )

    assert result == 0
    assert service.reconciled_job_id == "ingest-test"
    assert adapter.closed is True
    assert "job_id=ingest-test status=RECONCILE_REQUIRED" in capsys.readouterr().out
