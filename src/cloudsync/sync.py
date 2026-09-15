"""Bisync engine.

Each run is:
  - debounced: a "watermark" file records when a sync last *finished*; if no
    local mtime is newer, the run is skipped (keeps the 15-min timer cheap
    and collapses inotify trigger bursts)
  - locked: fcntl.flock(LOCK_EX|LOCK_NB) per account — a second trigger for
    the same account exits immediately instead of stacking rclone processes.
    (bisync additionally keeps its own per-pair lock file, so this is
    belt-and-suspenders.)

One local directory can be synced to several remotes; each pair runs its own
sequential bisync pass, so bisync listings never collide.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

try:
    import fcntl  # POSIX
except ImportError:  # pragma: no cover — non-POSIX dev machines
    fcntl = None  # locking degrades to no-op there; the LXC target is Linux

from . import config, remotes, util
from .config import Account, AccountStore, Settings


class SyncEngine:
    def __init__(
        self,
        settings: Settings | None = None,
        store: AccountStore | None = None,
    ) -> None:
        self.settings = settings or config.load_settings()
        self.store = store or AccountStore()
        self.base = config.cloud_base()
        self.state = config.state_dir()
        self.logs = config.log_dir()

    # -- paths ---------------------------------------------------------------

    def account_dir(self, slug: str) -> Path:
        return self.base / slug

    def watermark(self, slug: str) -> Path:
        return self.state / f"last-sync-{slug}"

    def lockfile(self, slug: str) -> Path:
        return config.state_dir() / "locks" / f"bisync-{slug}.lock"

    def logfile(self, slug: str) -> Path:
        return config.log_dir() / f"{slug}.log"

    # -- debounce ------------------------------------------------------------

    def local_changed_since_last_sync(self, slug: str) -> bool:
        watermark = self.watermark(slug)
        if not watermark.exists():
            return True  # never finished a sync -> always run
        newest = util.newest_mtime(self.account_dir(slug))
        # empty tree + existing watermark = nothing local changed
        return newest is not None and newest > watermark.stat().st_mtime

    # -- locking ---------------------------------------------------------------

    def try_lock(self, slug: str) -> int | None:
        """Acquire the per-account lock; None if somebody else holds it."""
        lock = self.lockfile(slug)
        util.ensure_dir(lock.parent)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
        if fcntl is None:  # pragma: no cover
            return fd
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        return fd

    def probe_lock(self, slug: str) -> str:
        """Non-destructive lock check: 'running' if another process holds it."""
        lock = self.lockfile(slug)
        if not lock.exists():
            return "idle"
        fd = os.open(lock, os.O_RDWR)
        try:
            if fcntl is None:  # pragma: no cover — non-POSIX dev machines
                return "idle"
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                return "idle"
            except OSError:
                return "running"
        finally:
            os.close(fd)

    # -- main entry points -----------------------------------------------------

    def sync_account(
        self,
        slug: str,
        *,
        skip_debounce: bool = False,
        extra: list[str] | None = None,
    ) -> int:
        account = self.store.get(slug)
        if account is None:
            util.die(f"unknown account: {slug}")
        extra = list(extra or [])

        directory = self.account_dir(slug)
        if not directory.is_dir():
            util.die(f"missing directory: {directory}")

        if not skip_debounce and not self.local_changed_since_last_sync(slug):
            util.info(f"[{slug}] no local changes since last sync — skipping")
            return 0

        fd = self.try_lock(slug)
        if fd is None:
            util.info(f"[{slug}] sync already running — skipping")
            return 0
        try:
            return self._run_bisync(account, directory, extra)
        finally:
            if fcntl is not None:  # pragma: no branch
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def sync_all(self, *, skip_debounce: bool = False) -> int:
        rc = 0
        accounts = self.store.load()
        if not accounts:
            util.info("no accounts configured")
            return 0
        for account in accounts:
            if self.sync_account(account.slug, skip_debounce=skip_debounce) != 0:
                rc = 1
        return rc

    def resync(self, slug: str, mode: str = "path1") -> int:
        """Destructive init/recovery pass. See bisync docs for --resync-mode."""
        return self.sync_account(
            slug, skip_debounce=True, extra=["--resync", "--resync-mode", mode]
        )

    # -- internals ---------------------------------------------------------------

    def _run_bisync(self, account: Account, directory: Path, extra: list[str]) -> int:
        util.ensure_dir(self.logs)
        base_flags = list(self.settings.get("sync", "bisync_flags", default=[]))
        base_flags += [
            "--max-delete",
            str(self.settings.get("sync", "max_delete_pct", default=50)),
            "--log-level",
            str(self.settings.get("sync", "log_level", default="INFO")),
        ]
        # backend-specific flags (e.g. --drive-skip-gdocs) are data-driven;
        # account.extra and call-time extra take precedence, in that order
        base_flags += remotes.backend_flags(account.backends, self.settings)

        with self.logfile(account.slug).open("ab") as logf:
            for remote in account.remotes:
                cmd = [
                    "rclone", "bisync", str(directory), remote,
                    *base_flags, *account.extra, *extra,
                ]
                util.info(f"[{account.slug}] bisync start: {remote}")
                proc = subprocess.run(cmd, stdout=logf, stderr=logf, check=False)
                if proc.returncode != 0:
                    util.warn(
                        f"[{account.slug}] bisync FAILED rc={proc.returncode} "
                        f"— see {self.logfile(account.slug)}"
                    )
                    return proc.returncode

        self.watermark(account.slug).touch()
        util.info(f"[{account.slug}] bisync OK")
        return 0
