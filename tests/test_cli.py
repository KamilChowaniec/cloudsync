from __future__ import annotations

import pytest

from cloudsync.cli import main


def test_help_lists_high_level_actions(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for word in ("add", "remove", "rename", "list", "sync", "resync",
                 "doctor", "remote", "api", "units", "status", "setup"):
        assert word in out
    assert "samba" not in out


def test_sync_dispatches_single_account(env_paths):
    from unittest import mock
    with mock.patch("cloudsync.cli.SyncEngine") as engine_cls:
        engine_cls.return_value.sync_account.return_value = 0
        assert main(["sync", "personal", "--force"]) == 0
        engine_cls.return_value.sync_account.assert_called_once_with(
            "personal", skip_debounce=True
        )


def test_resync_dispatch(env_paths):
    from unittest import mock
    with mock.patch("cloudsync.cli.SyncEngine") as engine_cls:
        engine_cls.return_value.resync.return_value = 0
        assert main(["resync", "personal", "--mode", "newer"]) == 0
        engine_cls.return_value.resync.assert_called_once_with("personal", "newer")


def test_doctor_exit_code_propagates(env_paths):
    from unittest import mock
    with mock.patch("cloudsync.cli.doctor.print_report", return_value=1), \
         mock.patch("cloudsync.cli.doctor.run_checks", return_value=[]):
        assert main(["doctor"]) == 1


def test_remote_check_requires_argument(env_paths, capsys):
    assert main(["remote", "check"]) == 2
    assert "usage" in capsys.readouterr().err
