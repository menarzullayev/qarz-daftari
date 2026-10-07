"""Re-encrypt the administrators' stored second-factor secrets under the current server secret
(operations runbook 4).

Run with:  python -m qarz.interface.rotate_secrets

Reads `QD_SECRETS_KEY` (the new secret) and `QD_SECRETS_KEY_PREVIOUS` (the one it replaces) from the
environment, like the application. Everything happens in one transaction and the command can be run
again safely. It prints three counts and, for secrets neither key could read, the user identifiers of
those administrator accounts: they must be enrolled again (runbook 7). It never prints a secret or a key.
The exit status is 1 when some secret could not be read, 2 when the configuration is unusable.
"""

import argparse
import asyncio
import sys

from qarz.application.secret_rotation import RotationResult, rotate_admin_secrets
from qarz.infrastructure.db import Database
from qarz.infrastructure.secret_box import SecretBox
from qarz.infrastructure.settings import Settings


async def run(settings: Settings | None = None) -> RotationResult:
    settings = settings or Settings()
    # Built before the database is touched: a missing or too short secret stops here.
    cipher = SecretBox(settings.secrets_key, settings.secrets_key_previous)
    database = Database(settings.database_url)
    try:
        return await rotate_admin_secrets(database, cipher)
    finally:
        await database.dispose()


def report(result: RotationResult) -> str:
    lines = [
        f"re-encrypted under the current key: {result.reencrypted}",
        f"already under the current key: {result.already_current}",
        f"readable under neither key: {len(result.unreadable)}",
    ]
    lines += [f"  must enrol again: administrator account {user_id}" for user_id in result.unreadable]
    return "\n".join(lines) + "\n"


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        result = asyncio.run(run())
    except ValueError as error:
        # The message names the setting's problem ("too short"), never its value.
        sys.stderr.write(f"cannot rotate: {error}\n")
        raise SystemExit(2) from None
    sys.stdout.write(report(result))
    if result.unreadable:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
