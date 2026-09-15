from __future__ import annotations

from unittest import mock

from cloudsync import doctor, units
from cloudsync.config import Account, AccountStore, load_settings


def _proc(returncode=0, stdout="", stderr=""):
    proc = mock.Mock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _fake_rclone_run(cmd, **kwargs):
    if cmd[:2] == ["rclone", "listremotes"]:
        return _proc(stdout="gpers:\n")
    if cmd[:3] == ["rclone", "config", "show"]:
        return _proc(stdout="[gpers]\ntype = drive\n")
    if cmd[0] == "rclone" and cmd[1] == "lsd":
        return _proc(returncode=1, stderr="directory not found\n")
    return _proc()


def _seed_account(env_paths):
    store = AccountStore()
    store.add(Account(slug="personal", remotes=["gpers:"], backends=["drive"]))
    base = env_paths["base"] / "personal"
    base.mkdir()
    (base / "a.txt").write_text("x")
    return store


def _matching_units():
    settings = load_settings()
    for name, content in units.render_units(settings).items():
        (units.unit_dir() / name).write_text(content)


def test_doctor_fails_when_rclone_missing(env_paths, capsys):
    with mock.patch.object(doctor.util, "which", return_value=None):
        rc = doctor.print_report(doctor.run_checks(offline=True))
    assert rc == 1
    assert "not found" in capsys.readouterr().out


def test_doctor_healthy_offline(env_paths, capsys):
    _seed_account(env_paths)
    _matching_units()
    with mock.patch.object(doctor.util, "which",
                           side_effect=lambda b: "/usr/bin/" + b if b == "rclone" else None), \
         mock.patch.object(doctor.util, "run", side_effect=_fake_rclone_run):
        rc = doctor.print_report(doctor.run_checks(offline=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "[ ok ] rclone" in out
    assert "[ ok ] account personal: local dir" in out
    assert "[ ok ] systemd units" in out


def test_doctor_reports_auth_failure(env_paths, capsys):
    _seed_account(env_paths)
    with mock.patch.object(doctor.util, "which",
                           side_effect=lambda b: "/usr/bin/" + b if b == "rclone" else None), \
         mock.patch.object(doctor.util, "run", side_effect=_fake_rclone_run):
        rc = doctor.print_report(doctor.run_checks(offline=False))
    out = capsys.readouterr().out
    assert rc == 1
    assert "[FAIL] account personal: auth gpers" in out
    assert "re-auth" in out


def test_doctor_warns_on_unit_drift(env_paths, capsys):
    _seed_account(env_paths)
    # units dir left empty -> everything counts as missing/drifted
    with mock.patch.object(doctor.util, "which",
                           side_effect=lambda b: "/usr/bin/" + b if b == "rclone" else None), \
         mock.patch.object(doctor.util, "run", side_effect=_fake_rclone_run):
        rc = doctor.print_report(doctor.run_checks(offline=True))
    out = capsys.readouterr().out
    assert rc == 0  # warnings don't fail the doctor
    assert "missing" in out and "cloudsync units install" in out

