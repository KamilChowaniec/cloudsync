"""cloudsync doctor — read-only health report.

Never mutates anything: checks binaries, settings, per-account state
(directories, remotes, auth, resolved flags, watermark, locks), systemd unit
drift, and trigger liveness. Exit 0 = healthy (warnings allowed), 1 = at
least one failure.

Run `cloudsync doctor` after install, and again whenever a sync misbehaves.
`--offline` skips the network probes (rclone lsd) for airgapped debugging —
everything else is local file reads and `systemctl is-active` queries.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import remotes, units, util
from .config import (
    Account,
    AccountStore,
    Settings,
    cloud_base,
    etc_dir,
    load_settings,
    log_dir,
    state_dir,
    unit_dir,
)
from .sync import SyncEngine

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"

_ICONS = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]", SKIP: "[skip]"}


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


def run_checks(*, offline: bool = False, settings: Settings | None = None) -> list[Check]:
    settings = settings or load_settings()
    store = AccountStore()
    checks: list[Check] = []

    if util.which("rclone") is None:
        checks.append(Check("rclone", FAIL, "binary not found in PATH"))
        return checks  # every other check depends on it
    checks.append(Check("rclone", OK, f"version {remotes.version()}"))

    accounts = store.load()
    checks.append(Check(
        "accounts",
        OK if accounts else WARN,
        f"{len(accounts)} configured" if accounts
        else "none configured yet — cloudsync add <slug> <remote:>",
    ))

    expected = [
        etc_dir(), state_dir(), state_dir() / "locks", log_dir(), cloud_base(),
    ]
    missing = [str(p) for p in expected if not p.is_dir()]
    checks.append(Check(
        "directories",
        OK if not missing else FAIL,
        "all present" if not missing
        else f"missing: {', '.join(missing)} — fix with 'cloudsync setup'",
    ))

    engine = SyncEngine(settings=settings, store=store)
    for account in accounts:
        checks.extend(_account_checks(account, engine, settings, offline))

    checks.extend(_units_checks(settings, store))
    return checks


def _account_checks(
    account: Account, engine: SyncEngine, settings: Settings, offline: bool
) -> list[Check]:
    checks: list[Check] = []
    slug = account.slug
    directory = engine.account_dir(slug)

    if directory.is_dir():
        checks.append(Check(f"account {slug}: local dir", OK, str(directory)))
    else:
        checks.append(Check(f"account {slug}: local dir", FAIL, f"missing: {directory}"))

    for remote in account.remotes:
        name = remotes.remote_name(remote)
        if remotes.remote_exists(remote):
            checks.append(Check(f"account {slug}: remote {name}", OK, remote))
        else:
            checks.append(Check(
                f"account {slug}: remote {name}", FAIL,
                "not in rclone config — run 'rclone config' "
                "(headless help: cloudsync remote guide <backend>)",
            ))

    for remote in account.remotes:
        live = remotes.remote_type(remote)
        if live is not None and live not in account.backends:
            checks.append(Check(
                f"account {slug}: backend {remotes.remote_name(remote)}", WARN,
                f"rclone now reports '{live}' but the account was added with "
                f"'{','.join(account.backends) or '?'}' — re-run "
                f"'cloudsync add {slug} {remote}' to refresh",
            ))
        else:
            checks.append(Check(
                f"account {slug}: backend {remotes.remote_name(remote)}",
                OK, live or "unknown",
            ))

    flags = remotes.backend_flags(account.backends, settings) + account.extra
    checks.append(Check(
        f"account {slug}: bisync flags", OK,
        " ".join(flags) or "(none beyond bisync defaults)",
    ))

    if not offline:
        for remote in account.remotes:
            ok, detail = remotes.probe(remote)
            checks.append(Check(
                f"account {slug}: auth {remotes.remote_name(remote)}",
                OK if ok else FAIL,
                detail if ok else detail + " — re-auth with rclone (token expired?)",
            ))

    watermark = engine.watermark(slug)
    if watermark.exists():
        age_min = int((time.time() - watermark.stat().st_mtime) / 60)
        checks.append(Check(
            f"account {slug}: last finished sync", OK, f"{age_min} min ago",
        ))
    elif directory.is_dir() and any(directory.iterdir()):
        checks.append(Check(
            f"account {slug}: last finished sync", WARN,
            f"never finished — run 'cloudsync resync {slug}' once",
        ))
    else:
        checks.append(Check(
            f"account {slug}: last finished sync", SKIP, "empty dir, nothing synced yet",
        ))

    state = engine.probe_lock(slug)
    checks.append(Check(
        f"account {slug}: lock", OK,
        "sync in progress" if state == "running" else "idle",
    ))

    return checks


def _units_checks(settings: Settings, store: AccountStore) -> list[Check]:
    checks: list[Check] = []
    rendered = units.render_units(settings)
    udir = unit_dir()

    problems: list[str] = []
    for name, content in rendered.items():
        target = udir / name
        if not target.exists():
            problems.append(f"{name} missing")
        elif target.read_text() != content:
            problems.append(f"{name} drifted")
    if problems:
        checks.append(Check(
            "systemd units", WARN,
            "; ".join(problems) + " — fix with 'cloudsync units install'",
        ))
    else:
        checks.append(Check("systemd units", OK, f"{len(rendered)} unit files match"))

    if util.which("systemctl") is None:
        checks.append(Check("systemctl", SKIP, "not available (dev machine?)"))
        return checks

    def is_active(unit: str) -> bool:
        return util.run(
            ["systemctl", "is-active", unit], capture_output=True, text=True,
        ).stdout.strip() == "active"

    if is_active("cloudsync-sync-all.timer"):
        checks.append(Check("timer", OK, "cloudsync-sync-all.timer active"))
    else:
        checks.append(Check(
            "timer", WARN, "cloudsync-sync-all.timer not active — 'cloudsync units install'",
        ))

    for slug in store.slugs():
        if is_active(f"cloudsync-watch@{slug}.path"):
            checks.append(Check(f"watch {slug}", OK, "path unit active"))
        else:
            checks.append(Check(
                f"watch {slug}", WARN,
                "path unit not active — 'cloudsync units install'",
            ))

    if is_active("cloudsync-api.socket"):
        checks.append(Check("api socket", OK, "listening (socket-activated)"))
    else:
        checks.append(Check("api socket", WARN, "not active — 'cloudsync api start'"))

    return checks


def print_report(checks: list[Check]) -> int:
    failed = False
    for check in checks:
        line = f"{_ICONS.get(check.status, '[ ?? ]')} {check.name}"
        if check.detail:
            line += f" — {check.detail.replace(chr(10), ' | ')}"
        print(line, flush=True)
        if check.status == FAIL:
            failed = True
    return 1 if failed else 0
