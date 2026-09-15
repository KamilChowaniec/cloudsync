from __future__ import annotations

from cloudsync.config import (
    Account,
    AccountStore,
    _deep_merge,
    _resolve_path,
    load_settings,
)


def test_deep_merge_overrides_nested():
    base = {"sync": {"interval_min": 15, "flags": ["a"]}, "api": {"port": 1}}
    override = {"sync": {"interval_min": 5}}
    merged = _deep_merge(base, override)
    assert merged["sync"]["interval_min"] == 5
    assert merged["sync"]["flags"] == ["a"]  # untouched key survives
    assert merged["api"]["port"] == 1
    assert base["sync"]["interval_min"] == 15  # original not mutated


def test_settings_toml_overrides_defaults(env_paths):
    (env_paths["etc"] / "settings.toml").write_text(
        '[sync]\ninterval_min = 30\n[api]\nport = 9999\n'
    )
    settings = load_settings()
    assert settings.get("sync", "interval_min") == 30
    assert settings.get("api", "port") == 9999
    assert settings.get("sync", "max_delete_pct") == 50  # default survives
    assert settings.get("nope", "nothing", default="x") == "x"


def test_paths_from_settings_toml(env_paths, monkeypatch):
    # no env var set -> [paths].base is the source of truth
    from cloudsync import config
    monkeypatch.delenv("CLOUDSYNC_BASE", raising=False)
    custom = env_paths["tmp"] / "custom" / "cloud"
    custom.mkdir(parents=True)
    (env_paths["etc"] / "settings.toml").write_text(
        f'[paths]\nbase = "{custom.as_posix()}"\n'
    )
    assert config.cloud_base() == custom


def test_env_var_beats_settings_toml(env_paths):
    # env vars win (emergency override + test isolation guarantee)
    from cloudsync import config
    custom = env_paths["tmp"] / "from" / "settings"
    (env_paths["etc"] / "settings.toml").write_text(
        f'[paths]\nbase = "{custom.as_posix()}"\n'
    )
    assert config.cloud_base() == env_paths["base"]


def test_paths_expand_tilde(env_paths, monkeypatch):
    # XDG-style defaults with ~ must expand to absolute paths
    monkeypatch.delenv("CLOUDSYNC_BASE", raising=False)
    (env_paths["etc"] / "settings.toml").write_text('[paths]\nbase = "~/cloud"\n')
    resolved = _resolve_path("base")
    assert resolved.is_absolute()
    assert "~" not in str(resolved)


def test_account_store_roundtrip(tmp_path):
    store = AccountStore(tmp_path / "accounts.conf")
    assert store.load() == []
    store.add(Account(slug="personal", remotes=["gpers:", "gwork:backup"],
                      extra=["--drive-skip-gdocs"]))
    store.add(Account(slug="work", remotes=["gwork:"]))
    loaded = {a.slug: a for a in store.load()}
    assert loaded["personal"].remotes == ["gpers:", "gwork:backup"]
    assert loaded["personal"].extra == ["--drive-skip-gdocs"]
    assert loaded["work"].remotes == ["gwork:"]

    store.rename("work", "job")
    assert [a.slug for a in store.load()] == ["personal", "job"]

    store.remove("personal")
    assert [a.slug for a in store.load()] == ["job"]


def test_account_store_add_replaces_same_slug(tmp_path):
    store = AccountStore(tmp_path / "accounts.conf")
    store.add(Account(slug="a", remotes=["one:"]))
    store.add(Account(slug="a", remotes=["two:"]))
    rows = store.load()
    assert len(rows) == 1
    assert rows[0].remotes == ["two:"]
