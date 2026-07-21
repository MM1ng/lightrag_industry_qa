from __future__ import annotations

from importlib import resources

import pytest

from industrial_energy_agent import __version__
from industrial_energy_agent.cli import main


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
