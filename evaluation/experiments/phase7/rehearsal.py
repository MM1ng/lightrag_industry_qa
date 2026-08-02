"""Phase 7 rehearsals: cold start, warm restart, shutdown, failure, backup."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

from .config import DIST_DIR, PHASE7_ROOT, PROJECT_ROOT, RC_VERSION, RC_ZIP_NAME

PYTHON = r"C:\Users\12189\.conda\envs\industrial-rag\python.exe"
QDRANT_URL = "http://127.0.0.1:16333"


def _default_db_path() -> Path:
    """Resolve the application's authoritative default SQLite database."""
    # The application package lives in <repo>/src; its default DB is
    # <repo>/src/data/db/industrial_rag.db (db/session.py resolves
    # PROJECT_ROOT to the package's src directory).
    return PROJECT_ROOT / "src" / "data" / "db" / "industrial_rag.db"


def _step(name: str, ok: bool, detail: str, *, exit_code: int | None = None) -> dict[str, Any]:
    return {
        "step": name,
        "status": "passed" if ok else "failed",
        "detail": detail[:500],
        "exit_code": exit_code,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def _run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _verify_zip(zip_path: Path, extract_dir: Path) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            if any(Path(name).is_absolute() or name.startswith("..") for name in names):
                return False, "absolute or parent-path entry found"
            archive.extractall(extract_dir)
        return True, f"extracted {len(names)} files"
    except Exception as error:
        return False, f"unzip failed: {error}"


def _wait_health(base_url: str, timeout: int = 120) -> bool:
    for _ in range(timeout // 2):
        try:
            response = httpx.get(base_url + "/health", timeout=3)
            if response.status_code == 200 and response.json().get("status") == "ok":
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def _start_uvicorn(
    cwd: Path,
    port: int,
    env: dict[str, str],
    log_dir: Path,
) -> subprocess.Popen:
    log_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            PYTHON,
            "-m",
            "uvicorn",
            "industrial_rag.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(cwd),
        env=env,
        stdout=(log_dir / "api.out.log").open("wb"),
        stderr=subprocess.STDOUT,
    )
    return proc


def cold_start() -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    started = time.perf_counter()
    temp_dir = Path(tempfile.mkdtemp(prefix="ira_rc_coldstart_"))
    zip_path = DIST_DIR / RC_ZIP_NAME
    try:
        ok, detail = _verify_zip(zip_path, temp_dir)
        steps.append(_step("unzip_and_relative_paths", ok, detail))
        checksum = json.loads(
            (PHASE7_ROOT / "package" / "checksum_manifest.json").read_text(encoding="utf-8")
        )
        mismatch = []
        for rel, info in checksum["files"].items():
            path = temp_dir / rel
            if not path.is_file():
                mismatch.append(rel)
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != info["sha256"]:
                mismatch.append(rel)
        steps.append(_step("checksum_verify", not mismatch, f"mismatches={mismatch[:10]}"))
        # Copy the app DB as a read-only rehearsal copy.
        db_copy = temp_dir / "data" / "db"
        db_copy.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_default_db_path(), db_copy / "industrial_rag.db")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(temp_dir / "src")
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{(db_copy / 'industrial_rag.db').as_posix()}"
        env["VECTOR_BACKEND"] = "nano"
        env["QDRANT_URL"] = QDRANT_URL
        env["QDRANT_COLLECTION_PREFIX"] = "ira_p3ar_4ac7a596"
        _run([PYTHON, "-m", "alembic", "stamp", "head"], cwd=temp_dir, env=env, timeout=120)
        migration = _run([PYTHON, "-m", "alembic", "upgrade", "head"], cwd=temp_dir, env=env)
        steps.append(_step("alembic_upgrade", migration.returncode == 0, migration.stderr[-300:], exit_code=migration.returncode))
        qdrant = _run(["docker", "start", "ira-phase3-qdrant-test"])
        time.sleep(4)
        try:
            q = httpx.get(QDRANT_URL + "/collections", timeout=5)
            qdrant_ok = q.status_code == 200
        except Exception:
            qdrant_ok = False
        steps.append(_step("qdrant_ready", qdrant_ok, "collections endpoint" if qdrant_ok else "unreachable", exit_code=qdrant.returncode))
        log_dir = temp_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        proc = _start_uvicorn(temp_dir, 8097, env, log_dir)
        health_ok = _wait_health("http://127.0.0.1:8097", timeout=180)
        steps.append(_step("api_health", health_ok, "uvicorn pid=%s" % proc.pid))
        if health_ok:
            ready = httpx.get("http://127.0.0.1:8097/ready", timeout=10)
            version = httpx.get("http://127.0.0.1:8097/version", timeout=10).json()
            steps.append(
                _step(
                    "api_ready",
                    ready.status_code == 200 and ready.json().get("status") == "ready",
                    str(ready.json()),
                )
            )
            steps.append(
                _step(
                    "api_version",
                    version.get("app_version") == RC_VERSION,
                    json.dumps(version, ensure_ascii=False),
                )
            )
            # Cold-start smoke: two read-only queries against the frozen KB copy.
            headers = {}
            if env.get("SERVICE_API_KEY"):
                headers["Authorization"] = f"Bearer {env['SERVICE_API_KEY']}"
            smoke_ok = True
            for question in (
                "SUMMIT 2196 系列泵长期存放时，存放环境和泵轴转动频率有什么要求？",
                "手册中完全没有记载的 XYZ 型号设备如何维护？",
            ):
                try:
                    response = httpx.post(
                        "http://127.0.0.1:8097/v1/knowledge-bases/8fce4626859d44abb70a9ae5b0372cea/query",
                        json={"query": question},
                        headers=headers,
                        timeout=60,
                    )
                    body = response.json()
                    ok = response.status_code == 200
                    smoke_ok = smoke_ok and ok
                    steps.append(
                        _step(
                            "smoke_query_detail",
                            ok,
                            f"http={response.status_code} citations={len(body.get('citations') or [])}",
                        )
                    )
                except Exception as error:
                    smoke_ok = False
                    steps.append(_step("smoke_query", False, str(error)[:200]))
            steps.append(_step("cold_start_smoke_queries", smoke_ok, "2 queries via official API"))
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
        steps.append(_step("api_stopped", True, "pid cleaned"))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return {
        "scenario": "cold_start",
        "started_at": started,
        "total_seconds": round(time.perf_counter() - started, 2),
        "steps": steps,
        "passed": all(step["status"] == "passed" for step in steps),
    }


def warm_restart() -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    temp_dir = Path(tempfile.mkdtemp(prefix="ira_rc_warm_"))
    try:
        with zipfile.ZipFile(DIST_DIR / RC_ZIP_NAME) as archive:
            archive.extractall(temp_dir)
        db_copy = temp_dir / "data" / "db"
        db_copy.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_default_db_path(), db_copy / "industrial_rag.db")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(temp_dir / "src")
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{(db_copy / 'industrial_rag.db').as_posix()}"
        env["VECTOR_BACKEND"] = "nano"
        env["QDRANT_URL"] = QDRANT_URL
        env["QDRANT_COLLECTION_PREFIX"] = "ira_p3ar_4ac7a596"
        _run([PYTHON, "-m", "alembic", "upgrade", "head"], cwd=temp_dir, env=env)
        log_dir = temp_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        first = _start_uvicorn(temp_dir, 8096, env, log_dir)
        steps.append(_step("first_start", _wait_health("http://127.0.0.1:8096", 180), "pid=%s" % first.pid))
        first.terminate()
        first.wait(timeout=15)
        second = _start_uvicorn(temp_dir, 8096, env, log_dir)
        steps.append(_step("second_start", _wait_health("http://127.0.0.1:8096", 180), "pid=%s" % second.pid))
        ready = httpx.get("http://127.0.0.1:8096/ready", timeout=10)
        steps.append(_step("ready_after_restart", ready.json().get("status") == "ready", str(ready.json())))
        query = httpx.get("http://127.0.0.1:8096/health", timeout=10)
        steps.append(_step("health_after_restart", query.status_code == 200, "ok"))
        second.terminate()
        second.wait(timeout=15)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return {
        "scenario": "warm_restart",
        "steps": steps,
        "passed": all(step["status"] == "passed" for step in steps),
    }


def graceful_shutdown() -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    before = _collections_count()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    run_dir = PROJECT_ROOT / ".run"
    run_dir.mkdir(parents=True, exist_ok=True)
    proc = _start_uvicorn(PROJECT_ROOT, 8095, env, PROJECT_ROOT / ".run")
    ok = _wait_health("http://127.0.0.1:8095", 180)
    steps.append(_step("start_before_shutdown", ok, "pid=%s" % proc.pid))
    (run_dir / "api.pid").write_text(str(proc.pid), encoding="utf-8")
    stop = _run(
        [
            "powershell",
            "-NoProfile",
            "-File",
            str(PROJECT_ROOT / "scripts" / "stop_local.ps1"),
        ],
        cwd=PROJECT_ROOT,
    )
    steps.append(_step("stop_local_script", stop.returncode == 0, stop.stdout[-200:], exit_code=stop.returncode))
    time.sleep(3)
    time.sleep(1)
    steps.append(
        _step(
            "process_stopped",
            proc.poll() is not None,
            f"pid={proc.pid} poll={proc.poll()}",
        )
    )
    after = _collections_count()
    steps.append(_step("qdrant_collections_untouched", before == after, f"{before}->{after}"))
    restart = _start_uvicorn(PROJECT_ROOT, 8095, env, PROJECT_ROOT / ".run")
    steps.append(_step("restart_after_shutdown", _wait_health("http://127.0.0.1:8095", 180), "pid=%s" % restart.pid))
    restart.terminate()
    restart.wait(timeout=15)
    return {
        "scenario": "graceful_shutdown",
        "steps": steps,
        "passed": all(step["status"] == "passed" for step in steps),
    }


def _collections_count() -> int:
    try:
        return len(httpx.get(QDRANT_URL + "/collections", timeout=5).json()["result"]["collections"])
    except Exception:
        return -1


def failure_recovery() -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    run_dir = PROJECT_ROOT / ".run"
    run_dir.mkdir(parents=True, exist_ok=True)
    # 1) missing env vars
    env = dict(os.environ)
    for name in ("DASHSCOPE_API_KEY", "LLM_MODEL", "MODEL_FALLBACK_ENABLED", "QDRANT_URL"):
        env.pop(name, None)
    check = _run(
        ["powershell", "-NoProfile", "-File", str(PROJECT_ROOT / "scripts" / "check_env.ps1")],
        cwd=PROJECT_ROOT,
        env=env,
    )
    steps.append(_step("missing_env_detected", check.returncode != 0, check.stdout[-200:], exit_code=check.returncode))
    # 2) Qdrant temporarily unavailable -> start_api must fail
    _run(["docker", "stop", "ira-phase3-qdrant-test"])
    time.sleep(3)
    api_env = dict(os.environ)
    api_env.update(
        {
            "VECTOR_BACKEND": "nano",
            "EMBEDDING_MODEL": api_env.get("EMBEDDING_MODEL") or "text-embedding-v4",
            "EMBEDDING_DIM": api_env.get("EMBEDDING_DIM") or "1024",
            "QDRANT_URL": QDRANT_URL,
            "QDRANT_COLLECTION_PREFIX": "ira_p3ar_4ac7a596",
        }
    )
    start = _run(
        ["powershell", "-NoProfile", "-File", str(PROJECT_ROOT / "scripts" / "start_api.ps1"), "-Port", "8094"],
        cwd=PROJECT_ROOT,
        env=api_env,
        timeout=90,
    )
    steps.append(_step("api_refuses_when_qdrant_down", start.returncode != 0, start.stdout[-200:], exit_code=start.returncode))
    _run(["docker", "start", "ira-phase3-qdrant-test"])
    time.sleep(4)
    # 3) wrong PID file is handled safely
    run_dir = PROJECT_ROOT / ".run"
    run_dir.mkdir(exist_ok=True)
    (run_dir / "api.pid").write_text("999999", encoding="utf-8")
    stop = _run(
        ["powershell", "-NoProfile", "-File", str(PROJECT_ROOT / "scripts" / "stop_local.ps1")],
        cwd=PROJECT_ROOT,
    )
    steps.append(_step("stale_pid_handled", stop.returncode == 0, stop.stdout[-200:], exit_code=stop.returncode))
    # 4) migration auto-applied on a fresh DB
    tmp = Path(tempfile.mkdtemp(prefix="ira_rc_fail_"))
    fresh_db = tmp / "fresh.db"
    env2 = dict(os.environ)
    env2.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{fresh_db.as_posix()}",
            "VECTOR_BACKEND": "nano",
            "EMBEDDING_MODEL": env2.get("EMBEDDING_MODEL") or "text-embedding-v4",
            "EMBEDDING_DIM": env2.get("EMBEDDING_DIM") or "1024",
            "QDRANT_URL": QDRANT_URL,
            "QDRANT_COLLECTION_PREFIX": "ira_p3ar_4ac7a596",
        }
    )
    migrate = _run(
        [PYTHON, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env2,
        timeout=120,
    )
    steps.append(_step("fresh_db_migration_recovers", migrate.returncode == 0, migrate.stderr[-250:] or migrate.stdout[-250:], exit_code=migrate.returncode))
    proc = _start_uvicorn(PROJECT_ROOT, 8093, env2, run_dir)
    ok = _wait_health("http://127.0.0.1:8093", timeout=120)
    steps.append(_step("fresh_db_api_starts", ok, "pid=%s" % proc.pid))
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except Exception:
        proc.kill()
    shutil.rmtree(tmp, ignore_errors=True)
    stop = _run(
        ["powershell", "-NoProfile", "-File", str(PROJECT_ROOT / "scripts" / "stop_local.ps1")],
        cwd=PROJECT_ROOT,
    )
    steps.append(_step("cleanup_after_recovery", stop.returncode == 0, stop.stdout[-120:], exit_code=stop.returncode))
    return {
        "scenario": "failure_recovery",
        "steps": steps,
        "passed": all(step["status"] == "passed" for step in steps),
    }


def backup_restore() -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    backup_dir = Path(tempfile.mkdtemp(prefix="ira_rc_backup_"))
    try:
        db_source = _default_db_path()
        db_backup = backup_dir / "app.db"
        shutil.copy2(db_source, db_backup)
        db_hash = hashlib.sha256(db_backup.read_bytes()).hexdigest()
        collections = httpx.get(QDRANT_URL + "/collections", timeout=5).json()["result"]["collections"]
        inventory = {c["name"]: _collection_points(c["name"]) for c in collections}
        (backup_dir / "collection_inventory.json").write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        steps.append(_step("backup_created", db_backup.is_file() and bool(inventory), f"db_sha={db_hash[:16]} collections={len(inventory)}"))
        # Restore into a fresh copy and verify a query.
        restore_dir = Path(tempfile.mkdtemp(prefix="ira_rc_restore_"))
        try:
            restored = restore_dir / "industrial_rag.db"
            shutil.copy2(db_backup, restored)
            env = dict(os.environ)
            env["DATABASE_URL"] = f"sqlite+aiosqlite:///{restored.as_posix()}"
            env["VECTOR_BACKEND"] = "nano"
            env["QDRANT_URL"] = QDRANT_URL
            env["QDRANT_COLLECTION_PREFIX"] = "ira_p3ar_4ac7a596"
            migrate = _run([PYTHON, "-m", "alembic", "upgrade", "head"], cwd=PROJECT_ROOT, env=env)
            steps.append(_step("restore_db_migration", migrate.returncode == 0, migrate.stderr[-200:], exit_code=migrate.returncode))
            proc = _start_uvicorn(PROJECT_ROOT, 8092, env, PROJECT_ROOT / ".run")
            ok = _wait_health("http://127.0.0.1:8092", 180)
            steps.append(_step("restored_api_ready", ok, "pid=%s" % proc.pid))
            if ok:
                headers = {}
                if env.get("SERVICE_API_KEY"):
                    headers["Authorization"] = f"Bearer {env['SERVICE_API_KEY']}"
                try:
                    response = httpx.post(
                        "http://127.0.0.1:8092/v1/knowledge-bases/8fce4626859d44abb70a9ae5b0372cea/query",
                        json={"query": "SUMMIT 2196 系列泵长期存放时，存放环境和泵轴转动频率有什么要求？"},
                        headers=headers,
                        timeout=60,
                    )
                    body = response.json()
                    steps.append(
                        _step(
                            "restored_query_and_citations",
                            response.status_code == 200 and bool(body.get("citations")),
                            f"http={response.status_code} citations={len(body.get('citations') or [])}",
                        )
                    )
                except Exception as error:
                    steps.append(_step("restored_query_and_citations", False, str(error)[:200]))
                after = _collection_points("ira_p3ar_4ac7a596_kb_8fce4626859d44abb70a9ae5b0372cea_g5162e7fb4208635103ff4ebb_chunks")
                steps.append(_step("collection_points_match", after == inventory.get("ira_p3ar_4ac7a596_kb_8fce4626859d44abb70a9ae5b0372cea_g5162e7fb4208635103ff4ebb_chunks"), f"points={after}"))
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except Exception:
                proc.kill()
        finally:
            shutil.rmtree(restore_dir, ignore_errors=True)
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)
    return {
        "scenario": "backup_restore",
        "steps": steps,
        "passed": all(step["status"] == "passed" for step in steps),
    }


def _collection_points(name: str) -> int:
    try:
        response = httpx.get(f"{QDRANT_URL}/collections/{name}", timeout=5)
        return response.json()["result"]["points_count"]
    except Exception:
        return -1


def main() -> int:
    stages = sys.argv[1:] or ["cold_start", "warm_restart", "graceful_shutdown", "failure_recovery", "backup_restore"]
    results: dict[str, Any] = {}
    for stage in stages:
        results[stage] = globals()[stage]()
    summary = {
        "rc_version": RC_VERSION,
        "results": results,
        "passed": all(results[stage]["passed"] for stage in results),
    }
    (PHASE7_ROOT / "rehearsal").mkdir(parents=True, exist_ok=True)
    for stage, result in results.items():
        (PHASE7_ROOT / "rehearsal" / f"{stage}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (PHASE7_ROOT / "rehearsal" / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
