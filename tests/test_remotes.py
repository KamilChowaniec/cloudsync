from __future__ import annotations

from cloudsync import remotes
from cloudsync.config import load_settings


def test_backend_flags_defaults():
    settings = load_settings()  # defaults: drive -> --drive-skip-gdocs
    assert remotes.backend_flags(["drive"], settings) == ["--drive-skip-gdocs"]
    assert remotes.backend_flags(["s3"], settings) == []
    assert remotes.backend_flags(["drive", "s3"], settings) == ["--drive-skip-gdocs"]


def test_backend_flags_settings_override(env_paths):
    # settings.toml entry REPLACES the built-in list for that backend
    (env_paths["etc"] / "settings.toml").write_text(
        '[backends.drive]\nextra_flags = []\n[backends.s3]\nextra_flags = ["--s3-a", "--s3-b"]\n'
    )
    settings = load_settings()
    assert remotes.backend_flags(["drive"], settings) == []
    assert remotes.backend_flags(["s3"], settings) == ["--s3-a", "--s3-b"]


def test_remote_name():
    assert remotes.remote_name("gwork:backup/sub") == "gwork"
    assert remotes.remote_name("gwork:") == "gwork"


def test_guide_known_and_unknown():
    assert "rclone authorize" in remotes.guide("drive")
    out = remotes.guide("webdav")  # not in table -> generic fallback
    assert "headless" in out.lower()
