"""Move every stored second-factor secret from the previous server secret to the current one
(operations runbook 4).

One transaction: either every secret that can be moved is moved, or nothing changed. It can be run any
number of times; a second run finds everything current. A secret that neither key reads is left as it is
and counted: that administrator has to be enrolled again (runbook 7). No secret, key or code is returned,
logged or printed, only counts and the identifiers of the accounts that could not be read.
"""

from dataclasses import dataclass
from uuid import UUID

from qarz.application.ports import RotatingCipher, SecretUnreadable, Storage


@dataclass(frozen=True)
class RotationResult:
    reencrypted: int
    already_current: int
    unreadable: tuple[UUID, ...]


async def rotate_admin_secrets(storage: Storage, cipher: RotatingCipher) -> RotationResult:
    moved = current = 0
    unreadable: list[UUID] = []
    async with storage.platform() as session:
        # Every row is locked first: an administrator who signs in or enrols meanwhile waits and then
        # sees the new bytes.
        for user_id, stored in await session.admin_secrets():
            try:
                sealed = cipher.reseal(stored, user_id.bytes)
            except SecretUnreadable:
                unreadable.append(user_id)
                continue
            if sealed is None:
                current += 1
            elif await session.replace_admin_secret(user_id, old=stored, new=sealed):
                moved += 1
            else:  # pragma: no cover - the row is locked, so nobody could have changed it
                raise RuntimeError("a locked administrator account changed during the rotation")
    return RotationResult(moved, current, tuple(unreadable))
