"""Small shared helpers."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .config import SLUG_RE, cloud_base, etc_dir, log_dir, state_dir


def info(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"warn: {msg}", file=sys.stderr, flush=True)


def die(msg: str):
    print(f"error: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_dirs() -> list[Path]:
    """Create every configured cloudsync directory from resolved settings.

    Idempotent; safe to re-run after editing [paths] in settings.toml.
    Per-account subdirs (base/<slug>, state/bisync/<slug>) are made by
    `cloudsync add`; this covers the roots + lock dir. The OS naturally
    assigns ownership to whoever runs this — rootless by design, no chown.
    """
    made = [
        ensure_dir(etc_dir()),
        ensure_dir(state_dir()),
        ensure_dir(state_dir() / "locks"),
        ensure_dir(log_dir()),
        ensure_dir(cloud_base()),
    ]
    return made


def valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug or ""))


def require_cmd(binary: str) -> str:
    found = shutil.which(binary)
    if not found:
        die(f"required command not found: {binary}")
    return found


def which(binary: str) -> str | None:
    return shutil.which(binary)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command without raising; caller decides what rc means."""
    kwargs.setdefault("check", False)
    return subprocess.run(cmd, **kwargs)


def newest_mtime(root: Path) -> float | None:
    """Newest mtime in a tree (recursive), or None if the tree is empty."""
    newest: float | None = None
    for path in root.rglob("*"):
        try:
            mtime = path.lstat().st_mtime
        except OSError:
            continue  # raced deletion, unreadable symlink, ...
        if newest is None or mtime > newest:
            newest = mtime
    return newest
