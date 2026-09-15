# cloudsync

rclone bisync orchestrator for multiple cloud accounts on a Proxmox LXC.
Works with **any rclone backend** — Google Drive, OneDrive, Dropbox, S3/B2,
SFTP, ... One CLI, zero runtime dependencies (Python stdlib only), no
hand-edited systemd files.

**Scope is deliberately narrow:** cloudsync keeps `<base>/<account>` in bisync
with its remotes. It never touches `rclone.conf` (read-only introspection
only) and never touches your LAN exposure — Samba/NFS/SMB-from-another-box is
your infrastructure, and can even live on a different LXC via a bind mount.

```
LXC (Debian, unprivileged)
├── /mnt/cloud/<account>/        real local dirs (one per account)
├── cloudsync bisync engine      debounced + per-account flock
│     triggers: 15-min timer · inotify (.path units) · HTTP POST /sync
└── your LAN exposure            samba / NFS / ... — your call, not ours
```

## Why bisync (not rclone mount)

- true local files: fast, offline-tolerant, SMB-friendly (mount+FUSE+SMB breaks)
- sync only touches deltas; drive letters/VFS caches are absent
- webhooks (Drive changes API) need a public HTTPS endpoint on a verified
  domain and expire weekly — polling every 15 min + manual trigger covers it

## Install

cloudsync ships as a **single-file executable** (`cloudsync.pyz` zipapp —
stdlib-only Python, built automatically by GitHub Actions on each `v*` tag).
One command installs it:

```bash
curl -sSL https://raw.githubusercontent.com/KamilChowaniec/cloudsync/main/install.sh | bash
```

The installer is **context-aware**: as root it lands in `/usr/local/bin` with
system-wide paths; as a normal user it installs to `~/.local/bin` with XDG
paths (`~/.config/cloudsync`, `~/.local/state/cloudsync`, synced tree at
`~/cloud`) and systemd **user** units — no sudo needed. It enables linger so
background sync survives logout. The only prerequisites are `python3` and
`rclone` (the installer checks; install them with your distro's package
manager if missing).

Upgrade = re-run the installer; uninstall =
`cloudsync units uninstall && rm ~/.local/bin/cloudsync` (or the
`/usr/local/bin` path as root).

## Usage

```bash
rclone config                          # once: add remotes (headless help below)
cloudsync remote guide drive           # prints the exact headless OAuth steps
cloudsync add personal gpersonal:      # register account → units regenerate
cloudsync add work gwork:backup        # one dir can mirror multiple remotes
cloudsync resync personal              # ONCE per account (destructive init)
cloudsync list
cloudsync sync                         # manual all-accounts pass
cloudsync sync personal --force        # skip debounce
cloudsync rename old new
cloudsync remove old                   # local data kept
cloudsync status
cloudsync doctor                       # read-only health report (--offline to skip auth probes)
cloudsync remote check gpersonal:      # probe one remote's auth/connectivity
```

Manual trigger endpoint (socket-activated, always listening):

```bash
curl -XPOST 'http://<lxc-ip>:8788/sync?account=personal'
curl -XPOST 'http://<lxc-ip>:8788/sync'          # all accounts
curl 'http://<lxc-ip>:8788/accounts'
```

Windows example (any LAN exposure works; this one is a junction, no drive letter):

```bat
mklink /J %USERPROFILE%\storage\cloud \\<file-server-ip>\cloud
```

## rclone boundary (read-only by design)

cloudsync *orchestrates* rclone but never *configures* it:

| Layer | Who owns it |
|---|---|
| remotes, OAuth tokens, backend options | `rclone config` (yours; portable across machines) |
| remote validation, backend type, auth probes | cloudsync — read-only (`listremotes`, `config show`, `lsd`) |
| bisync invocation + flags | cloudsync |

Backend-specific bisync flags are **data, not code**: built-ins live in
`src/cloudsync/remotes.py` (`drive → --drive-skip-gdocs`) and any backend can
be overridden/replaced in `/etc/cloudsync/settings.toml`:

```toml
[backends.onedrive]
extra_flags = ["--onedrive-no-accept-new-owners"]   # example
```

Per-account hand-tuning goes in the `extra` column of
`/etc/cloudsync/accounts.conf`; `cloudsync add` stores the detected backend
type per remote.

## How it works

| Concern        | Mechanism |
|----------------|-----------|
| 15-min timer   | `cloudsync-sync-all.timer` (`OnCalendar=*:0/15`, `Persistent=true`) |
| Local changes  | `cloudsync-watch@<acct>.path` (inotify) per account |
| Debounce       | mtime watermark: skip if nothing newer than last *finished* sync |
| Concurrency    | `flock(LOCK_EX|LOCK_NB)` per account — 2nd trigger exits immediately; bisync's own lockfile as backup |
| Manual trigger | systemd socket `:8788` → stdlib `http.server` |
| Boot           | everything `systemctl enable --now`, no login needed |
| Self-healing   | bisync `--resilient --recover --conflict-resolve newer` |

All tunables live in `/etc/cloudsync/settings.toml` (deep-merged over
defaults in `src/cloudsync/config.py`): interval, port, bisync flags,
backend flag table, log level — **and every filesystem location**. Nothing
is hardcoded; the built-in defaults are context-aware (system paths as root,
XDG paths as a normal user):

```toml
# as root                              # as a normal user (rootless)
[paths]
base     = "/mnt/cloud"                # ~/cloud
state    = "/var/lib/cloudsync"        # ~/.local/state/cloudsync
log      = "/var/log/cloudsync"        # ~/.local/state/cloudsync/log
unit_dir = "/etc/systemd/system"       # ~/.config/systemd/user
```

Resolution order: `CLOUDSYNC_*` env var → `[paths]` above → built-in default.
(The config dir itself — `/etc/cloudsync` or `~/.config/cloudsync` — is
env-var-or-default only; it can't be set from the file that lives inside it.)
After changing `base`, run `cloudsync units install` — systemd units bake the
path in at render time, and `cloudsync doctor` flags the drift if you forget.

### Rootless vs system install

Everything runs as whoever installed it — no chown, no `sudo -u`, no
privilege juggling. As root you get system units (`systemctl`); as a normal
user you get systemd user units (`systemctl --user`) with linger enabled for
boot persistence. rclone config simply lives in the installing user's
`~/.config/rclone/rclone.conf` — configure remotes as that same user.

## Safety notes

- `--max-delete 50` aborts a run if >50 % of files vanished (ransomware/OOPS guard).
- `resync` is the only destructive command; everything else refuses to run
  when bisync is in a bad state — check the per-account log under the
  configured `log` dir.
- Deleting files on Drive deletes them locally on the next pass (it *is* a
  sync). Use Drive's trash; it's your undo button.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .[dev]      # dev-only: pytest; runtime stays stdlib-only
pytest
# or run straight from the checkout:
PYTHONPATH=src python3 -m cloudsync doctor
# build the release artifact locally:
python -m zipapp src -m "cloudsync.cli:main" -p "/usr/bin/env python3" -o cloudsync.pyz
```

## Roadmap

- Google Drive webhook watcher (needs public HTTPS + verified domain;
  would slot in as another trigger calling the same engine)
- Prometheus exporter for sync health
- per-account Samba share generator as an *optional, separable* plugin

## License

MIT
