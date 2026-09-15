"""Systemd unit generation + control.

Context-aware: root installs use system units (/etc/systemd/system +
systemctl); normal users get systemd --user units (~/.config/systemd/user +
systemctl --user, with linger for boot persistence).

Generated units (templates live here, written to the unit dir):
  cloudsync-sync@.service    oneshot: one bisync pass for account %i
  cloudsync-sync-all.service oneshot: pass over all accounts
  cloudsync-sync-all.timer   every sync.interval_min minutes
  cloudsync-watch@.path      inotify trigger for the account dir
  cloudsync-api.socket       socket-activated HTTP trigger API
  cloudsync-api.service      the API server (stdlib http.server)
"""

from __future__ import annotations

import os

from . import util
from .config import (
    AccountStore,
    Settings,
    cloud_base,
    load_settings,
    log_dir,
    unit_dir,
)

BEGIN = "# >>> cloudsync managed >>>"
END = "# <<< cloudsync managed <<<"


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _sysctl(*args: str):
    """systemctl as root, `systemctl --user` otherwise."""
    prefix = ["systemctl"] if _is_root() else ["systemctl", "--user"]
    return util.run(prefix + list(args))


def _cli_path(settings: Settings) -> str:
    return str(settings.get("paths", "cli", default="/usr/local/bin/cloudsync"))


def render_units(settings: Settings) -> dict[str, str]:
    cli = _cli_path(settings)
    base = cloud_base()
    interval = int(settings.get("sync", "interval_min", default=15))
    port = int(settings.get("api", "port", default=8788))
    wanted_by = "multi-user.target" if _is_root() else "default.target"

    sync_service = f"""\
[Unit]
Description=cloudsync: bisync pass for account %i
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={cli} sync %i
TimeoutStartSec=2h
"""

    sync_all_service = f"""\
[Unit]
Description=cloudsync: bisync pass for all accounts
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={cli} sync
"""

    timer = f"""\
[Unit]
Description=cloudsync: periodic sync every {interval} min

[Timer]
OnCalendar=*:0/{interval}
Persistent=true
Unit=cloudsync-sync-all.service

[Install]
WantedBy=timers.target
"""

    watch_path = f"""\
[Unit]
Description=cloudsync: watch local changes for account %i

[Path]
PathModified={base}/%i
# inotify bursts are collapsed by the engine (debounce + per-account flock)
TriggerLimitIntervalSec=30s
TriggerLimitBurst=1

[Install]
WantedBy={wanted_by}
"""

    api_socket = f"""\
[Unit]
Description=cloudsync: trigger API socket

[Socket]
ListenStream=0.0.0.0:{port}
Accept=no

[Install]
WantedBy=sockets.target
"""

    api_service = f"""\
[Unit]
Description=cloudsync: trigger API
Requires=cloudsync-api.socket

[Service]
ExecStart={cli} api serve
Restart=on-failure
"""

    return {
        "cloudsync-sync@.service": sync_service,
        "cloudsync-sync-all.service": sync_all_service,
        "cloudsync-sync-all.timer": timer,
        "cloudsync-watch@.path": watch_path,
        "cloudsync-api.socket": api_socket,
        "cloudsync-api.service": api_service,
    }


def install() -> None:
    settings = load_settings()
    udir = unit_dir()
    util.ensure_dir(udir)
    for name, content in render_units(settings).items():
        target = udir / name
        if not target.exists() or target.read_text() != content:
            target.write_text(content)
            util.info(f"wrote {target}")

    _sysctl("daemon-reload")
    _sysctl("enable", "--now", "cloudsync-sync-all.timer")
    _sysctl("enable", "--now", "cloudsync-api.socket")
    for slug in AccountStore().slugs():
        _sysctl("enable", "--now", f"cloudsync-watch@{slug}.path")
    util.info("units installed and enabled")


def uninstall() -> None:
    udir = unit_dir()
    _sysctl("disable", "--now", "cloudsync-sync-all.timer")
    _sysctl("disable", "--now", "cloudsync-api.socket")
    _sysctl("stop", "cloudsync-api.service")
    for slug in AccountStore().slugs():
        stop_account_units(slug)
    for name in (
        "cloudsync-sync@.service",
        "cloudsync-sync-all.service",
        "cloudsync-sync-all.timer",
        "cloudsync-watch@.path",
        "cloudsync-api.socket",
        "cloudsync-api.service",
    ):
        (udir / name).unlink(missing_ok=True)
    _sysctl("daemon-reload")
    util.info("units uninstalled")


def stop_account_units(slug: str) -> None:
    """Disable this account's trigger units (before removing/renaming it)."""
    _sysctl("disable", "--now", f"cloudsync-watch@{slug}.path")
    _sysctl("stop", f"cloudsync-sync@{slug}.service")


def api_control(action: str) -> int:
    if action == "start":
        _sysctl("enable", "--now", "cloudsync-api.socket")
        _sysctl("start", "cloudsync-api.service")
    elif action == "stop":
        _sysctl("stop", "cloudsync-api.service")
        _sysctl("stop", "cloudsync-api.socket")
    # status prints regardless
    active = _sysctl(
        "is-active", "cloudsync-api.socket",
        capture_output=True, text=True,
    ).stdout.strip()
    if active == "active":
        settings = load_settings()
        port = int(settings.get("api", "port", default=8788))
        util.info(f"api: listening on 0.0.0.0:{port}  (POST /sync?account=<slug>)")
        return 0
    util.info("api: stopped")
    return 1


def log_hint(slug: str):
    return log_dir() / f"{slug}.log"
