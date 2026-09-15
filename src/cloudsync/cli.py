"""cloudsync — high-level CLI.

Everything is exposed here; no manual systemd editing needed. rclone remotes
are consumed read-only (validate / probe / guide) — never written.
"""

from __future__ import annotations

import argparse
import sys

from . import accounts, doctor, remotes, units, util
from .sync import SyncEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloudsync",
        description="rclone bisync orchestrator for multiple cloud accounts",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="register an account + wire everything up")
    p.add_argument("account")
    p.add_argument("remotes", nargs="+", help="rclone remote[:path] entries")

    p = sub.add_parser("remove", help="unregister an account (local data kept)")
    p.add_argument("account")

    p = sub.add_parser("rename", help="rename an account slug (local data moved)")
    p.add_argument("old")
    p.add_argument("new")

    sub.add_parser("list", help="list configured accounts")

    p = sub.add_parser("sync", help="sync all accounts (or one)")
    p.add_argument("account", nargs="?")
    p.add_argument("--force", action="store_true", help="skip the debounce check")

    p = sub.add_parser("resync", help="DESTRUCTIVE init/recovery bisync")
    p.add_argument("account")
    p.add_argument(
        "--mode", default="path1",
        help="resync-mode: path1|path2|newer|older|larger|smaller",
    )

    p = sub.add_parser("doctor", help="read-only health report")
    p.add_argument(
        "--offline", action="store_true",
        help="skip network probes (rclone lsd auth checks)",
    )

    p = sub.add_parser("remote", help="read-only rclone remote helpers")
    p.add_argument("action", choices=["list", "check", "guide"])
    p.add_argument("remote", nargs="?", help="remote name for check / backend type for guide")

    sub.add_parser("setup", help="create all configured directories (from settings.toml [paths])")

    p = sub.add_parser("api", help="trigger API control")
    p.add_argument("action", choices=["start", "stop", "status", "serve"],
                   help="serve: run the API server in the foreground (systemd unit)")

    p = sub.add_parser("units", help="(re)generate systemd units")
    p.add_argument("action", choices=["install", "uninstall"])

    sub.add_parser("status", help="overview of everything")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.cmd == "add":
        accounts.add(args.account, args.remotes)
    elif args.cmd == "remove":
        accounts.remove(args.account)
    elif args.cmd == "rename":
        accounts.rename(args.old, args.new)
    elif args.cmd == "list":
        accounts.list_accounts()
    elif args.cmd == "sync":
        engine = SyncEngine()
        if args.account:
            return engine.sync_account(args.account, skip_debounce=args.force)
        return engine.sync_all(skip_debounce=args.force)
    elif args.cmd == "resync":
        return SyncEngine().resync(args.account, args.mode)
    elif args.cmd == "doctor":
        return doctor.print_report(doctor.run_checks(offline=args.offline))
    elif args.cmd == "remote":
        return _remote_cmd(args)
    elif args.cmd == "api":
        if args.action == "serve":
            from .api import main as api_main
            api_main()
            return 0
        return units.api_control(args.action)
    elif args.cmd == "units":
        units.install() if args.action == "install" else units.uninstall()
    elif args.cmd == "setup":
        for made in util.ensure_dirs():
            print(f"dir ok: {made}")
    elif args.cmd == "status":
        accounts.list_accounts()
        print()
        units.api_control("status")
    return 0


def _remote_cmd(args: argparse.Namespace) -> int:
    if args.action == "list":
        found = remotes.list_remotes()
        if not found:
            print("no rclone remotes configured (rclone config)")
        for name in found:
            print(name, remotes.remote_type(name) or "?")
        return 0

    if args.action == "check":
        if not args.remote:
            print("usage: cloudsync remote check <remote[:path]>", file=sys.stderr)
            return 2
        if not remotes.remote_exists(args.remote):
            print(f"remote '{remotes.remote_name(args.remote)}' not found", file=sys.stderr)
            return 1
        ok, detail = remotes.probe(args.remote)
        print(f"{args.remote}: {detail}")
        return 0 if ok else 1

    # guide
    backend = args.remote or ""
    if not backend:
        print(
            "usage: cloudsync remote guide <backend> "
            f"(known: {', '.join(k for k in remotes.GUIDES if k != 'generic')})",
            file=sys.stderr,
        )
        return 2
    print(remotes.guide(backend))
    return 0


if __name__ == "__main__":
    sys.exit(main())
