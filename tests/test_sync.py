from __future__ import annotations

import time
from unittest import mock

import pytest

from cloudsync.config import Account, AccountStore
from cloudsync.sync import SyncEngine


@pytest.fixture()
def engine(env_paths):
    store = AccountStore()
    store.add(Account(slug="personal", remotes=["gpers:"]))
    (env_paths["base"] / "personal").mkdir(exist_ok=True)
    return SyncEngine(store=store)


def _ok_proc(*args, **kwargs):
    proc = mock.Mock()
    proc.returncode = 0
    return proc


def _fail_proc(*args, **kwargs):
    proc = mock.Mock()
    proc.returncode = 1
    return proc


def test_debounce_skips_when_nothing_changed(engine, env_paths):
    # establish baseline: a successful sync stamps the watermark
    with mock.patch("cloudsync.sync.subprocess.run", side_effect=_ok_proc):
        assert engine.sync_account("personal") == 0
    assert engine.watermark("personal").exists()

    # nothing changed -> skipped, rclone not called
    with mock.patch("cloudsync.sync.subprocess.run", side_effect=_ok_proc) as run_mock:
        assert engine.sync_account("personal") == 0
        assert run_mock.call_count == 0

    # touching a file changes mtimes -> runs again
    time.sleep(0.01)
    (env_paths["base"] / "personal" / "new.txt").write_text("hi")
    with mock.patch("cloudsync.sync.subprocess.run", side_effect=_ok_proc) as run_mock:
        assert engine.sync_account("personal") == 0
        assert run_mock.call_count == 1


def test_skip_debounce_flag_forces_run(engine):
    with mock.patch("cloudsync.sync.subprocess.run", side_effect=_ok_proc):
        assert engine.sync_account("personal") == 0  # stamps watermark
    with mock.patch("cloudsync.sync.subprocess.run", side_effect=_ok_proc) as run_mock:
        assert engine.sync_account("personal", skip_debounce=True) == 0
        assert run_mock.call_count == 1


def test_failed_sync_does_not_stamp_watermark(engine):
    with mock.patch("cloudsync.sync.subprocess.run", side_effect=_fail_proc):
        assert engine.sync_account("personal") == 1
    assert not engine.watermark("personal").exists()


def test_lock_blocks_concurrent_same_account(engine):
    pytest.importorskip("fcntl")  # real locking is POSIX-only
    import fcntl

    fd = engine.try_lock("personal")
    assert fd is not None
    try:
        assert engine.try_lock("personal") is None  # second holder blocked
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        import os

        os.close(fd)
    assert engine.try_lock("personal") is not None  # releasable again


def test_resync_passes_resync_flags(engine):
    with mock.patch("cloudsync.sync.subprocess.run", side_effect=_ok_proc) as run_mock:
        assert engine.resync("personal", mode="newer") == 0
        cmd = run_mock.call_args[0][0]
        assert "--resync" in cmd and "--resync-mode" in cmd
        assert cmd[cmd.index("--resync-mode") + 1] == "newer"


def test_sync_all_runs_each_account(engine, env_paths):
    store = AccountStore()
    store.add(Account(slug="work", remotes=["gwork:"]))
    (env_paths["base"] / "work").mkdir(exist_ok=True)
    with mock.patch("cloudsync.sync.subprocess.run", side_effect=_ok_proc) as run_mock:
        assert engine.sync_all() == 0
        assert run_mock.call_count == 2  # one per account
