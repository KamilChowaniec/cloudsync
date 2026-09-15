from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from unittest import mock

from cloudsync.api import make_server
from cloudsync.config import Account, AccountStore


def test_api_endpoints(env_paths):
    (env_paths["etc"] / "settings.toml").write_text("[api]\nport = 0\n")
    AccountStore().add(Account(slug="personal", remotes=["gpers:"]))

    server = make_server()
    # server_address host may be 0.0.0.0 (not connectable on some platforms);
    # always talk to it via loopback.
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"{base}/health") as resp:
            assert json.loads(resp.read()) == {"status": "ok"}

        with urllib.request.urlopen(f"{base}/accounts") as resp:
            assert json.loads(resp.read()) == {"accounts": ["personal"]}

        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = "ok"
        fake.stderr = ""
        with mock.patch("cloudsync.api.subprocess.run", return_value=fake) as rm:
            req = urllib.request.Request(
                f"{base}/sync?account=personal", method="POST", data=b""
            )
            with urllib.request.urlopen(req) as resp:
                body = json.loads(resp.read())
        assert body["ok"] is True
        cmd = rm.call_args[0][0]
        assert cmd[-2:] == ["sync", "personal"]

        # unknown route -> 404
        try:
            urllib.request.urlopen(f"{base}/nope")
            raise AssertionError("expected HTTP 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()
