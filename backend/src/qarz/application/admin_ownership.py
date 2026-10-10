"""An administrator gives a shop to another person (operations runbook 7; REQ-031, REQ-058, INV-11).

Telegram is the only way to sign in (ADR-017): an owner who loses the account loses the shop, and the
ordinary transfer (REQ-036) cannot help, because the old owner starts it. This is the audited way out. It
always needs a reason and a fresh second-factor code, whatever the administrator's session.

The former owner becomes a manager, as after a transfer (BR-22), but a suspended one: the lost account
may be in someone else's hands, and it must not keep reading and writing the shop's ledger. The new owner
reinstates or removes that membership like any other. A transfer still waiting is cancelled. A shop
waiting for deletion keeps waiting, with the same due time: the new owner is told and can cancel it. A
suspended shop stays suspended. Nothing of the shop's customers or entries is read (REQ-059).

Every method here assumes the caller already passed `AdminAccess.require_admin`.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from qarz.application import idempotency
from qarz.application.admin import clean_reason, shop_bodies
from qarz.application.admin_access import AdminAccess, AdminRequestKeys
from qarz.application.chat_texts import day, say
from qarz.application.errors import AppError, NotFound, Unauthenticated
from qarz.application.operations import admin_operation
from qarz.application.ports import Storage
from qarz.domain.promise import tashkent_date

REASSIGN_OWNER = admin_operation("admin.shops.owner.reassign")

log = logging.getLogger("qarz.admin")

# The database function's refusals that the administrator can do something about.
_REFUSALS = {"no_user": "unknown_user", "same_owner": "already_owner"}


class OwnerReassignmentRefused(AppError):
    """The shop cannot be given to that person; `fields.reason` says why."""

    code = "OWNER_REASSIGNMENT_REFUSED"


class _CodeRefused(Exception):
    """Carries a refused second-factor code out of the idempotent action without rolling back."""

    def __init__(self, refusal: AppError) -> None:
        super().__init__(refusal.code)
        self.refusal = refusal


class AdminOwnershipService:
    def __init__(self, storage: Storage, access: AdminAccess, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._access = access
        self._now = now or (lambda: datetime.now(UTC))

    async def reassign(
        self,
        admin_id: UUID,
        shop_id: UUID,
        *,
        new_owner_tg_id: int,
        code: str | None,
        reason: str | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        key = idempotency.validate_key(request_key)
        why = clean_reason(reason, required=True)
        assert why is not None
        now = self._now()
        refusal: AppError | None = None
        body: dict[str, Any] = {}
        async with self._storage.platform() as session:

            async def apply() -> dict[str, Any]:
                # The code first, and always: a wrong one is counted, and nothing about the shop or the
                # other person is told to someone who cannot give a right one.
                refused = await self._access.check_code(session, admin_id, code)
                if refused is not None:
                    raise _CodeRefused(refused)
                done = await session.admin_reassign_owner(
                    admin_id, shop_id, new_owner_tg=new_owner_tg_id, reason=why, now=now
                )
                if done.outcome == "refused":
                    raise Unauthenticated()
                if done.outcome == "no_shop":
                    raise NotFound()
                if done.outcome != "reassigned":
                    raise OwnerReassignmentRefused({"reason": _REFUSALS[done.outcome]})
                assert done.audit_id is not None and done.shop_name is not None
                if not self._access.second_factor_required:
                    # The database writes the row of the change itself and knows nothing of codes. The
                    # audit is insert-only, so that this change was made without one is a row beside it.
                    await session.add_admin_audit(
                        admin_id=admin_id,
                        action="shop.owner_reassigned_without_code",
                        target_type="shop",
                        target_id=str(shop_id),
                        shop_id=shop_id,
                        reason=None,
                        detail={"change": str(done.audit_id)} | self._access.unverified(),
                        now=now,
                    )
                if done.deletion_due is None:
                    text = say(done.new_owner_lang or "uz", "owner_reassigned_new", shop=done.shop_name)
                else:
                    text = say(
                        done.new_owner_lang or "uz",
                        "owner_reassigned_new_deletion",
                        shop=done.shop_name,
                        due=day(tashkent_date(done.deletion_due)),
                    )
                await session.enqueue(
                    channel="telegram",
                    recipient=str(new_owner_tg_id),
                    payload={"text": text},
                    dedupe_key=f"admin:{done.audit_id}:new",
                    shop_id=shop_id,
                )
                if done.previous_owner_tg is not None:
                    # That account may be in someone else's hands: the shop's name and the bare fact,
                    # and nothing else. Not the reason, not who has the shop now.
                    await session.enqueue(
                        channel="telegram",
                        recipient=str(done.previous_owner_tg),
                        payload={
                            "text": say(done.previous_owner_lang or "uz", "owner_reassigned_old", shop=done.shop_name)
                        },
                        dedupe_key=f"admin:{done.audit_id}:old",
                        shop_id=shop_id,
                    )
                log.warning(
                    "admin_owner_reassigned admin=%s shop=%s previous=%s new=%s",
                    admin_id,
                    shop_id,
                    done.previous_owner,
                    done.new_owner,
                )
                rows = await session.admin_shop_search(
                    admin_id, today=tashkent_date(now), query=None, state=None, shop_id=shop_id, after=None, limit=1
                )
                if not rows:
                    raise NotFound()
                (shop,) = await shop_bodies(session, rows)
                return {
                    **shop,
                    "deletion_due": None if done.deletion_due is None else done.deletion_due.isoformat(),
                    "previous_owner_tg_id": done.previous_owner_tg,
                    "previous_owner_membership": None if done.previous_owner is None else "suspended",
                    "transfer_cancelled": done.transfer_cancelled,
                }

            try:
                body = await idempotency.run_once(
                    # The stored answer shows the shop; it is kept under the shop and erased with it.
                    AdminRequestKeys(session, admin_id, about_shop=shop_id),
                    key=key,
                    operation=REASSIGN_OWNER.name,
                    user_id=admin_id,
                    # The code is not part of what is asked for: a retry with a newer code is the same request.
                    request={"shop": str(shop_id), "new_owner_tg_id": new_owner_tg_id, "reason": why},
                    action=apply,
                )
            except _CodeRefused as stop:
                # Caught inside the transaction so that it commits: the wrong code has been counted, and
                # nothing else was written.
                refusal = stop.refusal
        if refusal is not None:
            raise refusal
        return body
