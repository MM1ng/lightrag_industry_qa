"""Phase 9 staging service starter (local_staging rehearsal).

Starts FastAPI + Streamlit from the current source workspace against the
isolated staging database/workspace.  PID files are written under the staging
runtime directory.  Never touches production resources.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(r"D:\industrial_energy_agent")
STAGING = Path(r"D:\industrial_energy_agent_staging")
RUNTIME = STAGING / "runtime"
LOGS = STAGING / "logs"
PY = r"C:\Users\12189\.conda\envs\industrial-rag\python.exe"
API_PORT = 8110
UI_PORT = 8511


def _load_env() -> dict[str, str]:
    env = dict(os.environ)
    env_file = RUNTIME / "staging.env"
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    env["PYTHONPATH"] = str(REPO / "src")
    env["KB_DATA_ROOT"] = str(STAGING / "kb_data")
    env["LIGHTRAG_WORKING_DIR"] = str(RUNTIME / "lightrag_storage")
    env["STREAMLIT_API_URL"] = f"http://127.0.0.1:{API_PORT}"
    return env


def _health_ok(timeout: float = 180.0) -> bool:
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{API_PORT}/health", timeout=3
            ) as resp:
                return resp.status == 200
        except Exception:
            time.sleep(2)
    return False


def _ui_ok(timeout: float = 60.0) -> bool:
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{UI_PORT}", timeout=3) as resp:
                return resp.status == 200
        except Exception:
            time.sleep(2)
    return False


def _is_running(pid_file: Path) -> bool:
    if not pid_file.is_file():
        return False
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def main() -> int:
    env = _load_env()
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    LOGS.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    api_pid_file = RUNTIME / "api.pid"
    ui_pid_file = RUNTIME / "ui.pid"

    if mode in ("all", "api") and not _is_running(api_pid_file):
        with (LOGS / "phase9_api.out.log").open("a", encoding="utf-8") as out, (
            LOGS / "phase9_api.err.log"
        ).open("a", encoding="utf-8") as err:
            proc = subprocess.Popen(
                [
                    PY,
                    "-m",
                    "uvicorn",
                    "industrial_rag.api:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(API_PORT),
                ],
                cwd=str(REPO),
                env=env,
                stdout=out,
                stderr=err,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        api_pid_file.write_text(str(proc.pid), encoding="utf-8")
        if not _health_ok():
            print("API_NOT_READY")
            return 1
        print(f"API_READY pid={proc.pid}")

    if mode in ("all", "ui"):
        if not _is_running(ui_pid_file):
            with (LOGS / "phase9_ui.out.log").open("a", encoding="utf-8") as out, (
                LOGS / "phase9_ui.err.log"
            ).open("a", encoding="utf-8") as err:
                proc = subprocess.Popen(
                    [
                        PY,
                        "-m",
                        "streamlit",
                        "run",
                        str(REPO / "app" / "streamlit_app.py"),
                        "--server.port",
                        str(UI_PORT),
                        "--server.headless",
                        "true",
                    ],
                    cwd=str(REPO),
                    env=env,
                    stdout=out,
                    stderr=err,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            ui_pid_file.write_text(str(proc.pid), encoding="utf-8")
        if not _ui_ok():
            print("UI_NOT_READY")
            return 1
        ui_pid = int(ui_pid_file.read_text(encoding="utf-8").strip())
        print(f"UI_READY pid={ui_pid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
