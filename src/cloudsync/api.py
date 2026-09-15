"""HTTP trigger API — stdlib only, systemd socket-activated.

Endpoints:
  POST /sync                 -> sync all accounts
  POST /sync?account=<slug>  -> sync one account (respects lock; queued? no:
                                a second concurrent trigger exits immediately)
  GET  /accounts             -> list account slugs
  GET  /health               -> liveness

Manual trigger:
  curl -XPOST 'http://<lxc>:8788/sync?account=personal'
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SYNCHRONOUS_TIMEOUT = 7200  # seconds; a long bisync finishes within the request


def _cli() -> list[str]:
    """Invoke the CLI as a subprocess so each trigger is isolated."""
    return [sys.executable, "-m", "cloudsync.cli"]


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        path = urlparse(self.path).path
        if path == "/health":
            self._json(200, {"status": "ok"})
        elif path == "/accounts":
            from .config import AccountStore
            self._json(200, {"accounts": AccountStore().slugs()})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/sync":
            self._json(404, {"error": "not found"})
            return
        account = (parse_qs(parsed.query).get("account") or [None])[0]
        cmd = [sys.executable, "-m", "cloudsync.cli", "sync"]
        if account:
            cmd.append(account)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=SYNCHRONOUS_TIMEOUT)
            ok = proc.returncode == 0
            self._json(200 if ok else 500, {
                "account": account or "*",
                "ok": ok,
                "output": (proc.stdout + proc.stderr)[-4000:],
            })
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": str(exc)})

    def log_message(self, fmt: str, *args) -> None:  # quiet access log
        sys.stderr.write("[api] " + (fmt % args) + "\n")


def make_server() -> ThreadingHTTPServer:
    if os.environ.get("LISTEN_FDS"):  # systemd socket activation
        server = ThreadingHTTPServer.__new__(ThreadingHTTPServer)
        server.daemon_threads = True
        server.allow_reuse_address = True
        server.socket = socket.socket(fileno=3)
        server.server_address = server.socket.getsockname()
        return server
    from .config import load_settings
    port = int(load_settings().get("api", "port", default=8788))
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def main() -> None:
    server = make_server()
    sys.stderr.write(f"[api] serving on {server.server_address}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
