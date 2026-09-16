from __future__ import annotations

from unittest import mock

from cloudsync import accounts, config, units
from cloudsync.config import Account, AccountStore


def _proc(returncode=0, stdout=""):
    proc = mock.Mock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = ""
    return proc


def test_units_render_contents():
    settings = units.load_settings()
    rendered = units.render_units(settings)
    interval = settings.get("sync", "interval_min", default=15)
    port = settings.get("api", "port", default=8788)
    base = config.cloud_base()
    assert f"OnCalendar=*:0/{interval}" in rendered["cloudsync-sync-all.timer"]
    assert f"PathModified={base}/%i" in rendered["cloudsync-watch@.path"]
    # path unit must target the sync service explicitly — without Unit=,
    # systemd looks for cloudsync-watch@%i.service (does not exist)
    assert "Unit=cloudsync-sync@%i.service" in rendered["cloudsync-watch@.path"]
    assert "sync %i" in rendered["cloudsync-sync@.service"]
    assert f"ListenStream=0.0.0.0:{port}" in rendered["cloudsync-api.socket"]
    assert "api serve" in rendered["cloudsync-api.service"]
    # rootless design: no User=/Group= directives anywhere
    for content in rendered.values():
        assert "User=" not in content
        assert "Group=" not in content


def test_units_wanted_by_matches_context():
    # multi-user.target when root, default.target for systemd --user units
    rendered = units.render_units(units.load_settings())
    wanted = "multi-user.target" if units._is_root() else "default.target"
    assert f"WantedBy={wanted}" in rendered["cloudsync-watch@.path"]


def test_units_install_writes_and_enables(env_paths):
    with mock.patch.object(units.util, "run", return_value=_proc()) as rm:
        units.install()
    names = {p.name for p in env_paths["units"].iterdir()}
    assert "cloudsync-sync@.service" in names
    assert "cloudsync-sync-all.timer" in names
    assert "cloudsync-watch@.path" in names
    called = [c[0][0] for c in rm.call_args_list if c[0]]
    assert any("daemon-reload" in c for c in called)
    assert any("enable" in c and "cloudsync-sync-all.timer" in c for c in called)


def _fake_rclone_basic(cmd, **kwargs):
    if cmd[:2] == ["rclone", "listremotes"]:
        return _proc(stdout="gpers:\n")
    if cmd[:3] == ["rclone", "config", "show"]:
        return _proc(stdout="[gpers]\ntype = drive\n")
    return _proc()


def test_add_account_wires_everything(env_paths):
    with mock.patch.object(accounts.util, "require_cmd"), \
         mock.patch.object(accounts.util, "run", side_effect=_fake_rclone_basic), \
         mock.patch("cloudsync.accounts.units.install") as ui:
        accounts.add("personal", ["gpers:"])

    assert (env_paths["base"] / "personal").is_dir()
    store = AccountStore()
    assert store.get("personal").remotes == ["gpers:"]
    assert store.get("personal").backends == ["drive"]
    ui.assert_called_once()


def test_add_rejects_unknown_remote(env_paths):
    with mock.patch.object(accounts.util, "require_cmd"), \
         mock.patch.object(accounts.util, "run",
                           return_value=_proc(stdout="other:\n")):
        try:
            accounts.add("personal", ["gpers:"])
            raise AssertionError("expected SystemExit")
        except SystemExit:
            pass
    assert AccountStore().get("personal") is None


def _fake_rclone_run(cmd, **kwargs):
    if cmd[:2] == ["rclone", "listremotes"]:
        return _proc(stdout="gpers:\ngs3:\n")
    if cmd[:3] == ["rclone", "config", "show"]:
        name = cmd[3]
        backend = "drive" if name == "gpers" else "s3"
        return _proc(stdout=f"[{name}]\ntype = {backend}\n")
    return _proc()


def test_add_stores_backend_types(env_paths):
    # backend knowledge is data (remotes.BUILT_IN_BACKEND_FLAGS / settings.toml);
    # add() stores the type, flags are resolved at sync time
    with mock.patch.object(accounts.util, "require_cmd"), \
         mock.patch.object(accounts.util, "run", side_effect=_fake_rclone_run), \
         mock.patch("cloudsync.accounts.units.install"):
        accounts.add("personal", ["gpers:"])
        accounts.add("archive", ["gs3:"])
    assert AccountStore().get("personal").backends == ["drive"]
    assert AccountStore().get("archive").backends == ["s3"]


def test_rename_moves_directory(env_paths):
    store = AccountStore()
    store.add(Account(slug="old", remotes=["r:"], backends=["s3"]))
    (env_paths["base"] / "old").mkdir()
    (env_paths["base"] / "old" / "f.txt").write_text("x")

    with mock.patch.object(accounts.units, "stop_account_units"), \
         mock.patch.object(accounts.units, "install"):
        accounts.rename("old", "new")

    assert (env_paths["base"] / "new" / "f.txt").exists()
    assert not (env_paths["base"] / "old").exists()
    assert AccountStore().get("new") is not None
    assert AccountStore().get("old") is None
