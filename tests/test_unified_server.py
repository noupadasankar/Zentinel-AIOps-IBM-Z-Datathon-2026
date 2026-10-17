from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import psutil
import pytest
import websockets
from websockets.exceptions import InvalidStatus

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "raw" / "sample_replay.log"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _kill_tree(process: subprocess.Popen) -> None:
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in children:
            child.terminate()
        _, alive = psutil.wait_procs(children, timeout=10)
        for child in alive:
            child.kill()
        parent.terminate()
        parent.wait(timeout=15)
    except psutil.NoSuchProcess:
        return


@pytest.fixture()
def unified_server(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    port = _free_port()
    workdir = tmp_path_factory.mktemp("unified")
    port_file = workdir / "ports.json"
    env = dict(os.environ)
    env["ZENTINEL_DB_PATH"] = str(workdir / "unified_test.sqlite3")
    env["PYTHONIOENCODING"] = "utf-8"
    process: subprocess.Popen | None = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "zentinel.api.unified", "--port", str(port), "--port-file", str(port_file)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=ROOT,
        )
        deadline = time.time() + 150
        last_note = ""
        ready = False
        while time.time() < deadline:
            if process.poll() is not None:
                output = process.stdout.read().decode("utf-8", errors="replace") if process.stdout else ""
                raise RuntimeError(f"unified server exited early: {output[-1500:]}")
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
                if response.status_code == 200:
                    ready = True
                    break
                last_note = response.text[:200]
            except Exception as exc:
                last_note = f"{type(exc).__name__}: {exc}"
            time.sleep(2.0)
        if not ready:
            raise RuntimeError(f"unified server never became ready; last={last_note}")
        dashboard_port = json.loads(Path(port_file).read_text(encoding="utf-8"))["dashboard_internal_port"]
        yield {"port": port, "dashboard_port": dashboard_port}
    finally:
        if process is not None:
            _kill_tree(process)


def test_unified_single_port_end_to_end(unified_server: dict[str, Any]) -> None:
    assert unified_server["dashboard_port"] >= 1024
    base = f"http://127.0.0.1:{unified_server['port']}"

    with httpx.Client(base_url=base, timeout=30.0) as client:
        health = client.get("/health")
        assert health.status_code == 200
        body = health.json()
        assert body["mode"] == "live_ingestion"
        assert body["frozen_drain_state_loaded"] is True
        assert body["lstm_loaded"] is True

        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json() == {"ready": True, "engine": True, "persistence": True}

        status = client.get("/api/v1/live/status")
        assert status.status_code == 200

        document = client.get("/docs")
        assert document.status_code == 200
        assert "text/html" in document.headers.get("content-type", "")

        stream_health = client.get("/dashboard/_stcore/health")
        assert stream_health.status_code == 200
        assert "ok" in stream_health.text

        page = client.get("/dashboard/")
        assert page.status_code == 200
        assert "<html" in page.text.lower()
        assert "./static/" in page.text or "static/" in page.text

        replay = json.dumps(
            {"lines": SAMPLE.read_text(encoding="utf-8", errors="replace").splitlines()[:120], "source_id": "unified-test"}
        ).encode()
        ingested = client.post(
            "/api/v1/live/ingest/batch", content=replay, headers={"Content-Type": "application/json"}
        )
        assert ingested.status_code == 200

    async def ws_checks() -> None:
        async with websockets.connect(f"ws://127.0.0.1:{base.split(':')[-1]}/dashboard/_stcore/stream", open_timeout=15) as ws:
            try:
                await asyncio.wait_for(ws.send("ping"), timeout=10)
            except Exception:
                pass
        with pytest.raises(InvalidStatus):
            async with websockets.connect(f"ws://127.0.0.1:{base.split(':')[-1]}/api/v1/live/windows", open_timeout=10):
                pass

    asyncio.run(ws_checks())
