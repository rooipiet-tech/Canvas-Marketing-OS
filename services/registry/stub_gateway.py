#!/usr/bin/env python3
"""A local stub implementing the model-gateway contract, for the --live path.

There is no reachable model gateway in this scope (C1/C8: Key Vault public
network access is Disabled and there is no in-VNet runner), so the only way
to show that `eval_harness.py --live` takes a materially different code path
from the skip path is to point it at a local stub and count what arrives.

Run standalone:

    python services/registry/stub_gateway.py --port 8931

It serves `POST /v1/completions` and `GET /v1/health` per
contracts/model-gateway/openapi.yaml, and records every request it receives
so a test can assert the exact number of intercepted calls.
"""

from __future__ import annotations

import argparse
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterator

from gateway_client import build_completion_response, validate_completion_request

STUB_MODEL_REPLY = json.dumps({"stub": True, "note": "local stub gateway reply"})


class RequestLog:
    """Thread-safe record of what the stub actually received."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests: list[dict] = []

    def record(self, method: str, path: str, payload: dict | None) -> None:
        with self._lock:
            self.requests.append({"method": method, "path": path, "payload": payload})

    def count(self, method: str, path: str) -> int:
        with self._lock:
            return sum(
                1
                for entry in self.requests
                if entry["method"] == method and entry["path"] == path
            )


def _make_handler(log: RequestLog) -> type[BaseHTTPRequestHandler]:
    class StubHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: object) -> None:  # silence stderr access log
            return

        def _send_json(self, status: int, body: dict) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path == "/v1/health":
                log.record("GET", self.path, None)
                self._send_json(200, {"status": "ok"})
                return
            log.record("GET", self.path, None)
            self._send_json(404, {"error": {"message": "not found", "code": "not_found"}})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            length = int(self.headers.get("content-length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                log.record("POST", self.path, None)
                self._send_json(
                    400, {"error": {"message": "body is not JSON", "code": "bad_request"}}
                )
                return

            log.record("POST", self.path, payload)

            if self.path != "/v1/completions":
                self._send_json(404, {"error": {"message": "not found", "code": "not_found"}})
                return
            try:
                validate_completion_request(payload)
            except ValueError as exc:
                self._send_json(400, {"error": {"message": str(exc), "code": "bad_request"}})
                return
            self._send_json(200, build_completion_response(payload, STUB_MODEL_REPLY))

    return StubHandler


@contextmanager
def run_stub_gateway(port: int = 0) -> Iterator[tuple[str, RequestLog]]:
    """Serve the stub on localhost for the duration of the context."""
    log = RequestLog()
    server = HTTPServer(("127.0.0.1", port), _make_handler(log))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", log
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local stub model gateway.")
    parser.add_argument("--port", type=int, default=8931)
    args = parser.parse_args()
    with run_stub_gateway(args.port) as (base_url, _log):
        print(f"stub gateway listening on {base_url}")
        print("PASS")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
