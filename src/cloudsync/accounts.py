"""Account lifecycle: add / remove / rename / list.

Pure data lives in config.AccountStore; this module adds the system-side
effects (directories, remote validation, regenerating systemd units). It is
strictly read-only with respect to rclone: remotes must already exist.
`cloudsync remote guide <backend>` prints setup help instead of ever
touching rclone.conf.
"""

from __future__ import annotations

from . import remotes, units, util
from .config import Account, AccountStore, cloud_base, load_settings, state_dir


def _resolve_backends(remote_list: list[str]) -> list[str]:
    """Backend type per remote; used to pick bisync flags (see remotes.py)."""
    backends: list[str] = []
    for remote in remote_list:
        backend = remotes.remote_type(remote)
        if backend is None:
            util.die(
                f"could not determine backend type of remote '{remote}' — "
                "check 'rclone config show'"
            )
        backends.append(backend)
    return backends


def add(slug: str, remote_list: list[str]) -> None:
    if not util.valid_slug(slug):
        util.die(f"invalid account name '{slug}' (lowercase a-z, 0-9, dashes)")
    if not remote_list:
        util.die("usage: cloudsync add <account> <remote[:path]> [remote2...]")
    util.require_cmd("rclone")

    known = set(remotes.list_remotes())
    for remote in remote_list:
        name = remotes.remote_name(remote)
        if name + ":" not in known:
            util.die(
                f"rclone remote '{name}:' not found — run 'rclone config' first "
                "(headless help: cloudsync remote guide <backend>)"
            )

    backends = _resolve_backends(remote_list)

    store = AccountStore()
    store.add(Account(slug=slug, remotes=remote_list, backends=backends))

    directory = cloud_base() / slug
    util.ensure_dir(directory)
    util.ensure_dir(state_dir() / "bisync" / slug)

    units.install()  # regenerate triggers for the (possibly new) account list

    flags = remotes.backend_flags(backends, load_settings())
    if flags:
        util.info(f"backend flags for {slug}: {' '.join(flags)}")
    util.info(
        f"account '{slug}' -> remotes: {' '.join(remote_list)} "
        f"(backends: {','.join(backends)})"
    )
    util.info(f"first-time setup: run 'cloudsync resync {slug}' once")


def remove(slug: str) -> None:
    store = AccountStore()
    if store.get(slug) is None:
        util.die(f"unknown account: {slug}")

    units.stop_account_units(slug)
    store.remove(slug)
    units.install()  # regenerate what remains

    util.warn(
        f"account '{slug}' removed. Local data kept at {cloud_base() / slug} "
        "(delete manually if unwanted)."
    )


def rename(old: str, new: str) -> None:
    store = AccountStore()
    if store.get(old) is None:
        util.die(f"unknown account: {old}")
    if not util.valid_slug(new):
        util.die(f"invalid new name '{new}'")

    units.stop_account_units(old)
    store.rename(old, new)

    old_dir = cloud_base() / old
    new_dir = cloud_base() / new
    if old_dir.exists():
        if new_dir.exists():
            util.die(f"target directory already exists: {new_dir}")
        old_dir.rename(new_dir)

    units.install()
    util.warn(
        f"renamed '{old}' -> '{new}'. Remote content unchanged; "
        f"run 'cloudsync resync {new}' to re-baseline bisync listings."
    )


def list_accounts() -> None:
    accounts = AccountStore().load()
    if not accounts:
        util.info("no accounts configured — use: cloudsync add <account> <remote:>")
        return
    print(f"{'ACCOUNT':<16} {'BACKENDS':<12} {'REMOTES':<40} LOCAL DIR")
    for account in accounts:
        print(
            f"{account.slug:<16} {','.join(account.backends) or '?':<12} "
            f"{' '.join(account.remotes):<40} {cloud_base() / account.slug}"
        )
