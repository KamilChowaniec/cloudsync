"""Shared pytest fixtures: isolated env paths per test."""

from __future__ import annotations

import pytest


@pytest.fixture()
def env_paths(tmp_path, monkeypatch):
    """Point every cloudsync location at a temp dir."""
    etc = tmp_path / "etc"
    state = tmp_path / "state"
    log = tmp_path / "log"
    base = tmp_path / "mnt" / "cloud"
    units_dir = tmp_path / "systemd"
    for d in (etc, state, log, base, units_dir, state / "locks"):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLOUDSYNC_ETC", str(etc))
    monkeypatch.setenv("CLOUDSYNC_STATE", str(state))
    monkeypatch.setenv("CLOUDSYNC_LOG", str(log))
    monkeypatch.setenv("CLOUDSYNC_BASE", str(base))
    monkeypatch.setenv("CLOUDSYNC_UNIT_DIR", str(units_dir))
    return {
        "tmp": tmp_path,
        "etc": etc,
        "state": state,
        "log": log,
        "base": base,
        "units": units_dir,
    }
