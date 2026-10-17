"""Single-port unified server: FastAPI API + Streamlit dashboard behind one uvicorn process.

/dashboard and everything under it is HTTP/websocket-proxied to a managed Streamlit
child (baseUrlPath=/dashboard on an internal localhost port). Every other path,
including the Streamlit health probe only reached through /dashboard, is dispatched
in-process to the zentinel live API app with its real lifespan.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import uvicorn
from websockets.asyncio.client import connect as ws_connect

from zentinel.api.live_api import app as api_app
from zentinel.config import ROOT_DIR

DASHBOARD_PATH = ROOT_DIR / "dashboard" / "app.py"
HOP_HEADERS = {
    "host",
    "content-length",
    "connection",
    "transfer-encoding",
    "keep-alive",
    "upgrade",
    "proxy-authorization",
    "proxy-authenticate",
    "te",
    "trailer",
}


def find_free_port(start: int, tries: int = 40) -> int:
    for candidate in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test_socket:
            try:
                test_socket.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    raise RuntimeError(f"No free port in range {start}-{start + tries - 1}")


class ApiLifespanProxy:
    """Runs the nested API app's lifespan across the unified lifespan cycle."""

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._messages: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._queue.put_nowait({"type": "lifespan.startup"})
        self._task = asyncio.ensure_future(
            api_app({"type": "lifespan"}, self._queue.get, self._put_message)
        )

        while True:
            message = await self._messages.get()
            if message["type"] == "lifespan.startup.complete":
                return
            if message["type"] == "lifespan.startup.failed":
                raise RuntimeError(f"API lifespan startup failed: {message.get('message')}")

    def _put_message(self, message: dict[str, Any]) -> Any:
        self._messages.put_nowait(message)

    async def shutdown(self) -> None:
        if self._task is None:
            return
        self._queue.put_nowait({"type": "lifespan.shutdown"})
        while True:
            message = await self._messages.get()
            if message["type"] == "lifespan.shutdown.complete":
                break
            if message["type"] == "lifespan.shutdown.failed":
                break
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None


class UnifiedServer:
    def __init__(self, api_port: int | None = None, dashboard_port: int | None = None):
        self.api_port = api_port or 8000
        self.child: subprocess.Popen | None = None
        self.client: httpx.AsyncClient | None = None
        self.lifespan_proxy = ApiLifespanProxy()
        requested = dashboard_port or int(os.getenv("ZENTINEL_STREAMLIT_PORT", "8501"))
        self.dashboard_port = find_free_port(requested)

    async def _child_healthy(self) -> bool:
        try:
            response = await self.client.get(
                f"http://127.0.0.1:{self.dashboard_port}/dashboard/_stcore/health", timeout=2.0
            )
            return response.status_code == 200 and "ok" in response.text
        except Exception:
            return False

    async def startup(self) -> None:
        await self.lifespan_proxy.start()
        self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{self.dashboard_port}", timeout=30.0)
        self.child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(DASHBOARD_PATH),
                "--server.baseUrlPath",
                "/dashboard",
                "--server.address",
                "127.0.0.1",
                "--server.port",
                str(self.dashboard_port),
                "--server.headless",
                "true",
                "--browser.gatherUsageStats",
                "false",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        for _ in range(120):
            if self.child.poll() is not None:
                self._raise_child_failure()
            if await self._child_healthy():
                return
            await asyncio.sleep(1.0)
        self._raise_child_failure()

    def _raise_child_failure(self) -> None:
        raise RuntimeError(
            f"Streamlit child failed to start (exit code {self.child.returncode}); check that dashboard/app.py imports cleanly."
        )

    async def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            await self.lifespan_proxy.shutdown()
        if self.child is not None:
            self.child.terminate()
            await asyncio.to_thread(self.child.wait, None, 8)
            if self.child.poll() is None:
                self.child.kill()
            self.child = None
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def _proxy_http(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        assert self.client is not None
        query_string = scope.get("query_string", b"")
        target = scope["path"] + (f"?{query_string.decode('latin-1')}" if query_string else "")
        headers = [
            (key.decode("latin-1"), value.decode("latin-1"))
            for key, value in scope.get("headers", [])
            if key.decode("latin-1").lower() not in HOP_HEADERS
        ]
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break

        try:
            async with self.client.stream(
                scope["method"], target, headers=headers, content=bytes(body)
            ) as response:
                response_headers = [
                    (name.encode("latin-1"), value.encode("latin-1"))
                    for name, value in response.headers.multi_items()
                    if name.lower() not in {"connection", "transfer-encoding"}
                ]
                await send(
                    {
                        "type": "http.response.start",
                        "status": response.status_code,
                        "headers": response_headers,
                    }
                )

                async def stream_body() -> AsyncIterator[bytes]:
                    with contextlib.suppress(Exception):
                        async for chunk in response.aiter_bytes():
                            if chunk:
                                yield chunk
                    yield b""

                async for chunk in stream_body():
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                await send({"type": "http.response.body", "body": b"", "more_body": False})
        except Exception as exc:
            payload = f"dashboard upstream error: {type(exc).__name__}".encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 502,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": payload})

    async def _proxy_websocket(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        query_string = scope.get("query_string", b"")
        target = scope["path"] + (f"?{query_string.decode('latin-1')}" if query_string else "")
        inner = None
        upstream_ready = False
        try:
            await send({"type": "websocket.accept"})
            inner = await ws_connect(
                f"ws://127.0.0.1:{self.dashboard_port}{target}", max_size=2**25, open_timeout=15
            )
            upstream_ready = True

            async def outer_to_inner() -> None:
                while True:
                    message = await receive()
                    if message["type"] == "websocket.disconnect":
                        with contextlib.suppress(Exception):
                            await inner.close()
                        return
                    if "text" in message:
                        await inner.send(message["text"])
                    elif "bytes" in message:
                        await inner.send(message["bytes"])

            async def inner_to_outer() -> None:
                async for message in inner:
                    if isinstance(message, bytes):
                        await send({"type": "websocket.send", "bytes": message})
                    else:
                        await send({"type": "websocket.send", "text": message})

            await asyncio.gather(outer_to_inner(), inner_to_outer())
        except Exception:
            if not upstream_ready:
                with contextlib.suppress(Exception):
                    await send({"type": "websocket.close", "code": 1011, "reason": "dashboard upstream unavailable"})
            else:
                with contextlib.suppress(Exception):
                    await send({"type": "websocket.close", "code": 1001})
            with contextlib.suppress(Exception):
                if inner is not None:
                    await inner.close()

    async def _reject_websocket(self, send: Any) -> None:
        with contextlib.suppress(Exception):
            await send(
                {
                    "type": "websocket.close",
                    "code": 4404,
                    "reason": "No websocket endpoint at this path",
                }
            )

    async def _handle_lifespan(self, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await self.startup()
                except Exception as exc:
                    await self.shutdown()
                    await send({"type": "lifespan.startup.failed", "message": str(exc)})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await self.shutdown()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope["type"]
        if scope_type == "lifespan":
            await self._handle_lifespan(receive, send)
            return
        path = scope.get("path", "")
        if path == "/dashboard" or path.startswith("/dashboard/"):
            if scope_type == "websocket":
                await self._proxy_websocket(scope, receive, send)
            elif scope_type == "http":
                await self._proxy_http(scope, receive, send)
            return
        if scope_type == "websocket":
            await self._reject_websocket(send)
            return
        await api_app(scope, receive, send)


def build_unified_app() -> UnifiedServer:
    return UnifiedServer()


def _assert_port_free(host: str, port: int) -> None:
    """Fail fast with a readable message when the requested port is already in use.

    Without this, uvicorn retries the bind forever and leaves a zombie parent
    plus a running Streamlit child while every client probes the *other*
    server that owns the port.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test_socket:
        try:
            test_socket.bind((host, port))
        except OSError as exc:
            print(f"ERROR: port {host}:{port} is already in use ({exc}).")
            print("Stop the other server first, or run Zentinel on a free port:")
            print(f"  python -m zentinel.api.unified --port {find_free_port(port + 1)}")
            raise SystemExit(1) from exc


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Zentinel unified single-port server (API + dashboard).")
    parser.add_argument("--host", default=os.getenv("ZENTINEL_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ZENTINEL_PORT", "8000")))
    parser.add_argument("--port-file", default=None)
    args = parser.parse_args()
    os.environ["ZENTINEL_HOST"] = args.host
    os.environ["ZENTINEL_PORT"] = str(args.port)
    _assert_port_free(args.host, args.port)
    server = build_unified_app()
    if args.port_file:
        Path(args.port_file).write_text(
            json.dumps({"dashboard_internal_port": server.dashboard_port}), encoding="utf-8"
        )
    print(f"Zentinel unified single-port mode: one process on http://{args.host}:{args.port}")
    print(f"  API:        http://{args.host}:{args.port}/  (docs at /docs)")
    print(f"  Dashboard:  http://{args.host}:{args.port}/dashboard")
    print(f"  internal streamlit child on 127.0.0.1:{server.dashboard_port} (localhost only)")
    uvicorn.run(server, host=args.host, port=args.port, lifespan="on", log_level="info")


if __name__ == "__main__":
    main()
