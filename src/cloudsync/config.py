"""Paths, settings loading, and the account store.

Every filesystem location is configurable, resolved in this order:

    1. environment variable   CLOUDSYNC_ETC / CLOUDSYNC_STATE / CLOUDSYNC_LOG
                              CLOUDSYNC_BASE / CLOUDSYNC_UNIT_DIR
    2. settings.toml [paths]  (state / log / base / unit_dir)
    3. built-in default       /var/lib/cloudsync, /var/log/cloudsync,
                              /mnt/cloud, /etc/systemd/system

Exception: the config directory itself (etc) cannot come from the config
file that lives inside it — bootstrap circularity — so it is env var or
default only. Env vars win over settings.toml (emergency overrides), which
is also what makes the test suite's isolation work.
"""

from __future__ import annotations

import copy
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# Built-in path defaults; settings.toml [paths] uses the same keys.
# 'etc' is intentionally absent: it cannot be set from its own config file.
# Context-aware: system-wide locations when running as root, XDG Base
# Directories when running as a normal user (rootless/systemd-user mode).
_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

_BUILTIN_PATHS: dict[str, str] = (
    {
        "etc": "/etc/cloudsync",
        "state": "/var/lib/cloudsync",
        "log": "/var/log/cloudsync",
        "base": "/mnt/cloud",
        "unit_dir": "/etc/systemd/system",
    }
    if _IS_ROOT
    else {
        "etc": "~/.config/cloudsync",
        "state": "~/.local/state/cloudsync",
        "log": "~/.local/state/cloudsync/log",
        "base": "~/cloud",
        "unit_dir": "~/.config/systemd/user",
    }
)

_ENV_VARS: dict[str, str] = {
    "etc": "CLOUDSYNC_ETC",
    "state": "CLOUDSYNC_STATE",
    "log": "CLOUDSYNC_LOG",
    "base": "CLOUDSYNC_BASE",
    "unit_dir": "CLOUDSYNC_UNIT_DIR",
}


def _resolve_path(key: str) -> Path:
    """env var -> settings.toml [paths] -> built-in default ('etc': no step 2)."""
    env = os.environ.get(_ENV_VARS[key])
    if env:
        return Path(env).expanduser()
    if key == "etc":
        return Path(_BUILTIN_PATHS["etc"]).expanduser()
    settings = load_settings()
    return Path(
        settings.get("paths", key, default=_BUILTIN_PATHS[key])
    ).expanduser()


def etc_dir() -> Path:
    return _resolve_path("etc")


def state_dir() -> Path:
    return _resolve_path("state")


def log_dir() -> Path:
    return _resolve_path("log")


def cloud_base() -> Path:
    return _resolve_path("base")


def unit_dir() -> Path:
    return _resolve_path("unit_dir")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS: dict = {
    "sync": {
        # systemd timer: run a pass every N minutes
        "interval_min": 15,
        # bisync --max-delete safety net (percent of files allowed to vanish)
        "max_delete_pct": 50,
        # rclone log verbosity: NOTICE | INFO | DEBUG
        "log_level": "INFO",
        # base flags for every bisync call (recover/resilient = self-healing)
        # NOTE: backend-specific flags (e.g. --drive-skip-gdocs) are NOT here —
        # they are auto-added per account at `add` time based on remote type.
        "bisync_flags": [
            "--resilient",
            "--recover",
            "--max-lock", "2m",
            "--conflict-resolve", "newer",
            "--create-empty-src-dirs",
            "--compare", "size,modtime,checksum",
            "--slow-hash-sync-only",
            "--fix-case",
        ],
    },
    "api": {
        "port": 8788,
    },
    # Filesystem locations (context-aware: system paths as root, XDG paths
    # as a normal user — see _BUILTIN_PATHS). NOTE: systemd units bake the
    # base path in at render time (PathModified=...), so after changing
    # [paths].base run 'cloudsync units install' (doctor flags the drift).
    # 'etc' is not here by design — see module docstring.
    "paths": {
        "base": "/mnt/cloud" if _IS_ROOT else "~/cloud",
        "state": "/var/lib/cloudsync" if _IS_ROOT else "~/.local/state/cloudsync",
        "log": "/var/log/cloudsync" if _IS_ROOT else "~/.local/state/cloudsync/log",
        "unit_dir": "/etc/systemd/system" if _IS_ROOT else "~/.config/systemd/user",
    },
    # Backend-specific bisync flags keyed by rclone backend type. Built-ins
    # live in remotes.py; an entry here REPLACES that backend's list, so new
    # backends (onedrive, s3, ...) are a settings.toml edit, not code.
    "backends": {
        "drive": {"extra_flags": ["--drive-skip-gdocs"]},
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


@dataclass
class Settings:
    data: dict

    def get(self, *path: str, default=None):
        node: object = self.data
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def load_settings() -> Settings:
    """Load <etc>/settings.toml and deep-merge it over DEFAULT_SETTINGS."""
    path = etc_dir() / "settings.toml"
    user: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            user = tomllib.load(fh)
    return Settings(_deep_merge(DEFAULT_SETTINGS, user))


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

@dataclass
class Account:
    slug: str
    remotes: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)  # per-account bisync args
    backends: list[str] = field(default_factory=list)  # rclone backend types, per remote


def accounts_path() -> Path:
    return etc_dir() / "accounts.conf"


class AccountStore:
    """TSV-backed account registry:  slug<TAB>remotes<TAB>extra-args<TAB>backends."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else accounts_path()

    def load(self) -> list[Account]:
        accounts: list[Account] = []
        if not self.path.exists():
            return accounts
        for line in self.path.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            remotes = parts[1].split() if len(parts) > 1 and parts[1] else []
            extra = parts[2].split() if len(parts) > 2 and parts[2] else []
            backends = parts[3].split(",") if len(parts) > 3 and parts[3] else []
            accounts.append(
                Account(slug=parts[0], remotes=remotes, extra=extra, backends=backends)
            )
        return accounts

    def save(self, accounts: list[Account]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "\t".join((a.slug, " ".join(a.remotes), " ".join(a.extra), ",".join(a.backends)))
            for a in accounts
        ]
        self.path.write_text("\n".join(lines) + ("\n" if lines else ""))

    def get(self, slug: str) -> Account | None:
        for account in self.load():
            if account.slug == slug:
                return account
        return None

    def add(self, account: Account) -> None:
        accounts = [a for a in self.load() if a.slug != account.slug]
        accounts.append(account)
        self.save(accounts)

    def remove(self, slug: str) -> None:
        self.save([a for a in self.load() if a.slug != slug])

    def rename(self, old: str, new: str) -> None:
        accounts = self.load()
        for account in accounts:
            if account.slug == old:
                account.slug = new
        self.save(accounts)

    def slugs(self) -> list[str]:
        return [a.slug for a in self.load()]
