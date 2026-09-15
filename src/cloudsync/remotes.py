"""rclone remote introspection — strictly read-only.

cloudsync orchestrates rclone but never writes rclone.conf: remotes are
opaque handles. Everything here either *reads* rclone's output (listremotes,
config show, version) or runs cheap connectivity probes. Managing remotes
themselves (auth, tokens, backend options) stays with plain `rclone config`;
`guide()` prints the recommended headless setup steps instead of automating
an OAuth flow that needs a browser anyway.
"""

from __future__ import annotations

from . import util
from .config import Settings

# Built-in knowledge: bisync flags that only make sense for one backend type.
# Overridable per backend via settings.toml (an entry REPLACES the built-in):
#   [backends.drive]
#   extra_flags = []
BUILT_IN_BACKEND_FLAGS: dict[str, list[str]] = {
    "drive": ["--drive-skip-gdocs"],  # Google Docs are not normal files
}


def remote_name(remote: str) -> str:
    """'gwork:backup/sub' -> 'gwork'."""
    return remote.split(":", 1)[0]


def list_remotes() -> list[str]:
    out = util.run(["rclone", "listremotes"], capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def remote_exists(remote: str) -> bool:
    return remote_name(remote) + ":" in set(list_remotes())


def remote_type(remote: str) -> str | None:
    """Backend type of a configured remote: 'drive', 's3', 'onedrive', ..."""
    proc = util.run(
        ["rclone", "config", "show", remote_name(remote)],
        capture_output=True, text=True,
    )
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("type"):
            _, _, value = stripped.partition("=")
            return value.strip() or None
    return None


def backend_flags(backends: list[str], settings: Settings) -> list[str]:
    """Union of bisync flags for an account's backend types.

    Per-backend lists come from settings (falling back to built-ins), so new
    backends are a settings.toml edit, not a code change.
    """
    flags: list[str] = []
    for backend in backends:
        configured = settings.get("backends", backend, "extra_flags")
        if configured is not None:
            flags.extend(str(flag) for flag in configured)
        else:
            flags.extend(BUILT_IN_BACKEND_FLAGS.get(backend, []))
    return flags


def probe(remote: str) -> tuple[bool, str]:
    """Cheap authenticated connectivity check. Returns (ok, last-error-line)."""
    if ":" not in remote:
        remote += ":"
    proc = util.run(
        ["rclone", "lsd", remote, "--max-depth", "1", "--contimeout", "20s"],
        capture_output=True, text=True,
    )
    if proc.returncode == 0:
        return True, "auth + connectivity OK"
    lines = (proc.stderr or proc.stdout or "").strip().splitlines()
    return False, (lines[-1] if lines else f"exit code {proc.returncode}")


def version() -> str:
    proc = util.run(["rclone", "version"], capture_output=True, text=True)
    first = proc.stdout.splitlines()[0] if proc.stdout else ""
    return first.replace("rclone ", "").strip() or "unknown"


GUIDES: dict[str, str] = {
    "drive": """\
Headless setup — Google Drive (backend 'drive')
-----------------------------------------------
1. On any machine with a browser (your Windows PC is fine), run:  rclone config
2. n) new remote -> name it after the account (e.g. gpersonal)
3. Storage type: drive
4. client_id / client_secret: use your OWN Google Cloud OAuth client if you
   can — rclone's shared one is rate-limited hard for bisync workloads.
   (https://rclone.org/drive/#making-your-own-client-id)
5. scope: 1 (full access);  root_folder_id / service_account: leave blank
6. "Use web browser to automatically authenticate?" -> NO (the LXC is headless)
7. rclone then prints a command like:
       rclone authorize "drive"
   Run THAT on the browser machine, complete the Google sign-in, and paste
   the resulting token/config back into the rclone session on the LXC.
8. Repeat per Google account — one rclone remote each.
9. Verify on the LXC:  rclone lsd gpersonal:
Note: only an OAuth token is stored (rclone.conf); no files are copied.
""",
    "onedrive": """\
Headless setup — Microsoft OneDrive (backend 'onedrive')
--------------------------------------------------------
1. Run `rclone config` on a machine with a browser; n) new remote, type onedrive.
2. Keep defaults for region/client id; at "Use web browser to automatically
   authenticate?" answer NO, then run the printed
   `rclone authorize "onedrive"` on the browser machine and paste the token back.
3. Copy the resulting remote block (~/.config/rclone/rclone.conf) to the LXC,
   then verify:  rclone lsd onedrive:
""",
    "s3": """\
Headless setup — S3-compatible (backend 's3': AWS, B2, MinIO, ...)
------------------------------------------------------------------
No browser/OAuth needed — everything is keys:
1. On the LXC:  rclone config  ->  n) new remote, type s3
2. Provide access_key_id / secret_access_key (a restricted IAM key is fine),
   region, and endpoint (set endpoint explicitly for MinIO/B2-compatible).
3. Verify:  rclone lsd mys3:
""",
    "sftp": """\
Headless setup — SFTP (backend 'sftp')
--------------------------------------
No browser/OAuth needed:
1. On the LXC:  rclone config  ->  n) new remote, type sftp, host/user/port.
2. Auth: ssh-agent or a key file (recommended) — rclone asks for the key path.
3. Verify:  rclone lsd myserver:
""",
    "dropbox": """\
Headless setup — Dropbox (backend 'dropbox')
--------------------------------------------
Same browser dance as drive: run `rclone config` (on the LXC, answering NO to
auto-config), then run the printed `rclone authorize "dropbox"` on whichever
machine has a browser, and paste the token back. Verify: `rclone lsd dbx:`.
""",
    "generic": """\
Generic headless setup
----------------------
OAuth-style backends (drive/onedrive/dropbox/...): run `rclone config` where a
browser is available (or answer NO to auto-config on the LXC and run the
printed `rclone authorize "<type>"` on a browser machine, then paste the token
back), then copy the remote block into the LXC's ~/.config/rclone/rclone.conf.
Key-based backends (s3/sftp/webdav/...): configure directly on the LXC.
Docs: https://rclone.org/docs/#configure-rclone-on-a-headless-machine
""",
}


def guide(backend: str) -> str:
    text = GUIDES.get(backend.strip().lower())
    if text is None:
        known = ", ".join(k for k in GUIDES if k != "generic")
        text = (
            f"No specific guide for '{backend}' (known: {known}).\n" + GUIDES["generic"]
        )
    return text
