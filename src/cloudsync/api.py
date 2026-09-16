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


def _child_env() -> dict[str, str]:
    """Environment for the sync subprocess, carrying this process's sys.path.

    The API server runs in whatever context the CLI was launched from — a
    zipapp, a checkout, a pip install. A plain `python -m cloudsync` child
    has no way to know that; but copying sys.path into PYTHONPATH makes the
    child resolve `cloudsync.*` identically to the parent in every case
    (zipimport handles the archive path; a checkout contributes its src/).
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    return env


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
                                  timeout=SYNCHRONOUS_TIMEOUT,
                                  env=_child_env())
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
        # bind_and_activate=False still runs BaseServer.__init__ (which sets
        # up __is_shut_down etc.); the raw __new__ trick crashes serve_forever
        # on Python 3.13 with 'no attribute _BaseServer__is_shut_down'
        server = ThreadingHTTPServer(
            ("0.0.0.0", 0), Handler, bind_and_activate=False,
        )
        server.daemon_threads = True
        server.socket.close()  # drop the dummy socket; adopt fd 3
        server.socket = socket.socket(fileno=3)
        server.server_address = server.socket.getsockname()
        server.server_port = server.server_address[1]
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
