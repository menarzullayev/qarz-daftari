"""Make and end an administrator's service key, and set an administrator's password.

Run with:

    python -m qarz.interface.admin_sign_in key-create TG_ID LABEL [--out FILE]
    python -m qarz.interface.admin_sign_in key-list
    python -m qarz.interface.admin_sign_in key-revoke LABEL
    python -m qarz.interface.admin_sign_in password-set TG_ID LOGIN

Connects with `QD_ADMIN_DATABASE_URL`, the administrators' role, and reads `QD_ADMIN_TG_IDS`: a key or a
password is made only for a person on the allow-list who has an active administrator's account.

A key is printed once and kept nowhere; with `--out` it is written to that file (readable by its owner
only) and not printed. The password is asked for on the terminal, twice, and is never an argument, so it
reaches neither the shell's history nor a process list.
"""

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Callable
from pathlib import Path

from qarz.application.admin_sign_in import AdminSignIn
from qarz.domain import admin_password as rules
from qarz.infrastructure.db import Database
from qarz.infrastructure.settings import Settings

LABEL = "a label is 1 to 40 of a-z, 0-9 and dash, and starts with a letter or digit"


def _service(settings: Settings) -> AdminSignIn:
    if not settings.admin_database_url:
        raise ValueError("QD_ADMIN_DATABASE_URL is not set")
    allowed = settings.admin_allow_list()
    if not allowed:
        raise ValueError("QD_ADMIN_TG_IDS names nobody")
    return AdminSignIn(Database(settings.admin_database_url), allowed_tg_ids=allowed)


def _write_secret(path: Path, secret: str) -> None:
    # Created for its owner alone, and never over a file that exists: an old key is not lost by a typo.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as file:
        file.write(secret + "\n")


def read_password(ask: Callable[[str], str] = getpass.getpass) -> str:
    first = ask("New password: ")
    if first != ask("Again: "):
        raise ValueError("the two did not match; nothing was changed")
    return rules.check_password(first)


async def run(arguments: argparse.Namespace, settings: Settings | None = None) -> list[str]:
    service = _service(settings or Settings())
    if arguments.command == "key-create":
        key = await service.create_key(arguments.tg_id, arguments.label)
        if arguments.out is None:
            return [key, "This is the only time the key is shown. It is sent as:  Authorization: Bearer <key>"]
        _write_secret(arguments.out, key)
        return [f"key {arguments.label!r} written to {arguments.out}; it is kept nowhere else"]
    if arguments.command == "key-list":
        keys = await service.keys()
        return [f"{key.label}  made {key.created_at:%Y-%m-%d %H:%M} UTC" for key in keys] or ["no keys"]
    if arguments.command == "key-revoke":
        ended = await service.revoke_key(arguments.label)
        return [f"key {arguments.label!r} revoked" if ended else f"no live key is labelled {arguments.label!r}"]
    rules.check_login(arguments.login)
    await service.set_password(arguments.tg_id, arguments.login, read_password())
    return [f"password set for login {arguments.login!r}; any earlier one no longer works"]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = root.add_subparsers(dest="command", required=True)
    create = commands.add_parser("key-create")
    create.add_argument("tg_id", type=int)
    create.add_argument("label", help=LABEL)
    create.add_argument("--out", type=Path, default=None)
    commands.add_parser("key-list")
    revoke = commands.add_parser("key-revoke")
    revoke.add_argument("label")
    password = commands.add_parser("password-set")
    password.add_argument("tg_id", type=int)
    password.add_argument("login")
    return root


def main() -> None:
    arguments = parser().parse_args()
    try:
        lines = asyncio.run(run(arguments))
    except (ValueError, OSError) as error:
        sys.stderr.write(f"refused: {error}\n")
        raise SystemExit(1) from error
    print("\n".join(lines))


if __name__ == "__main__":
    main()
