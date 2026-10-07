"""PostgreSQL storage. Tenant isolation is enforced by the database (ADR-016).

Every tenant transaction sets `qd.shop_id` with transaction scope, so the setting cannot survive into the
next use of a pooled connection, and the row-level security policies hide every other shop's rows.
"""

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from qarz.application.errors import AlreadyMember, StorageTimeout
from qarz.application.ports import (
    ActivityRow,
    AdminAccount,
    AdminAuditRow,
    AdminReceiptRow,
    AdminShopRow,
    CatalogItemRecord,
    CreditSettings,
    CustomerAccount,
    CustomerRecord,
    DateRequestRecord,
    DayFigures,
    DebtFigures,
    DisputeRecord,
    EntryRow,
    GoodsLineRecord,
    LockedSubscription,
    MemberRecord,
    Membership,
    MyShop,
    OnlinePayment,
    OutboxMessage,
    PaymentNoticeRecord,
    PeriodTotals,
    PromiseRecord,
    ReminderCandidate,
    ReminderSettings,
    SessionInfo,
    ShopSettings,
    ShopToErase,
    ShopTotals,
    StaffFigures,
    StaffInvitation,
    StoredFileRecord,
    SubscriptionToReview,
    TransferRecord,
    UncoveredDebt,
    WaitingLink,
)
from qarz.domain.access import Role
from qarz.domain.ledger import Entry, EntryKind

# Measurement rows refer to a shop or an entry by a value derived from its identifier, never by the
# identifier itself (ADR-010).
_MEASURE_NAMESPACE = UUID("6f1d1c0e-8f0b-5d55-9d0a-51a7c0de0a10")

_CUSTOMER_COLUMNS = "c.id, c.display_name, c.phone, c.status, c.reminders_off, c.credit_limit"
_CUSTOMER_BY_ID = f"SELECT {_CUSTOMER_COLUMNS} FROM customer c WHERE c.id = :id"
_CUSTOMER_LOCKED = f"{_CUSTOMER_BY_ID} FOR UPDATE"

_CATALOG_COLUMNS = "i.id, i.name, i.name_norm, i.unit, i.price, i.learned, i.status, i.merged_into"
_CATALOG_BY_ID = f"SELECT {_CATALOG_COLUMNS} FROM catalog_item i WHERE i.id = :id"
_CATALOG_LOCKED = f"{_CATALOG_BY_ID} FOR UPDATE"

# The current promised date of an entry is its newest promise row.
_PROMISED = (
    "(SELECT p.promised_date FROM promise p WHERE p.entry_id = {entry}.id "
    "ORDER BY p.created_at DESC, p.id DESC LIMIT 1)"
)

# Entries that still count: not a reversal and not reversed (INV-2).
_LIVE = (
    "SELECT e.id, e.customer_id, e.seq, e.kind, e.amount FROM ledger_entry e "
    "WHERE e.kind <> 'reversal' AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id)"
)

_BALANCES = (
    "SELECT l.customer_id, "
    "sum(CASE WHEN l.kind IN ('credit', 'opening') THEN l.amount ELSE -l.amount END)::bigint AS balance "
    f"FROM ({_LIVE}) l GROUP BY l.customer_id"
)

# The balance of the customer row `c`, read through the (customer_id, seq) index. A page of customers
# then costs what the page holds, not what the shop holds: joining `_BALANCES` to a filtered customer list
# adds up every entry of the shop, and the planner may do that once per customer (S19.1 load test).
_BALANCE_OF_C = (
    "(SELECT coalesce(sum(CASE WHEN e.kind IN ('credit', 'opening') THEN e.amount ELSE -e.amount END), 0)::bigint "
    "FROM ledger_entry e WHERE e.customer_id = c.id AND e.kind <> 'reversal' "
    "AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id))"
)

# Oldest-first allocation (BR-3) in one pass: the uncovered part of a debt is what its running total
# exceeds the customer's total payments by, capped at its own amount. This must agree with
# `qarz.domain.ledger`; tests/api/test_customers_ledger.py compares the two on generated accounts.
# The promised date is looked up only for debts with something left uncovered: a covered debt adds
# nothing to any figure, and most debts of a long account are covered (S19.1 load test).
_OWED = (
    f"WITH live AS ({_LIVE}), "
    "paid AS (SELECT customer_id, sum(amount) AS paid FROM live WHERE kind = 'payment' GROUP BY customer_id), "
    "debt AS ("
    "  SELECT l.id, l.customer_id, l.amount, "
    "         sum(l.amount) OVER (PARTITION BY l.customer_id ORDER BY l.seq) AS running "
    "    FROM live l WHERE l.kind IN ('credit', 'opening')), "
    "uncovered AS ("
    "  SELECT d.id, d.customer_id, "
    "         least(d.amount, greatest(0, d.running - coalesce(p.paid, 0))) AS remaining "
    "    FROM debt d LEFT JOIN paid p ON p.customer_id = d.customer_id), "
    "owed AS ("
    "  SELECT u.customer_id, u.remaining, "
    f"         CASE WHEN u.remaining > 0 THEN {_PROMISED.format(entry='u')} END AS promised "
    "    FROM uncovered u)"
)

_FIGURES = (
    f"{_OWED}, "
    "figures AS ("
    "  SELECT customer_id, "
    "         sum(remaining)::bigint AS balance, "
    "         coalesce(sum(remaining) FILTER (WHERE promised < :today), 0)::bigint AS overdue, "
    "         min(promised) FILTER (WHERE promised < :today AND remaining > 0) AS since, "
    "         coalesce(sum(remaining) FILTER (WHERE promised = :today), 0)::bigint AS due_today "
    "    FROM owed GROUP BY customer_id) "
)


# --- reports (REQ-046) ---------------------------------------------------------------------------------
# Entries that still count, as `_LIVE`, with when and by whom they were recorded.
_LIVE_RECORDED = (
    "SELECT e.id, e.customer_id, e.seq, e.kind, e.amount, e.author_id, e.created_at FROM ledger_entry e "
    "WHERE e.kind <> 'reversal' AND NOT EXISTS (SELECT 1 FROM ledger_entry r WHERE r.reverses_id = e.id)"
)
_TASHKENT_DAY = "(l.created_at AT TIME ZONE 'Asia/Tashkent')::date"
_SIGNED = "CASE WHEN l.kind IN ('credit', 'opening') THEN l.amount ELSE -l.amount END"
# A period is the instants from :start up to but not including :end.
_RECORDED_IN = "{row}.created_at >= :start AND {row}.created_at < :end"

# One statement, so the balances and the movements between them come from one snapshot and reconcile:
# start + credit + opening - payments = end. `inside` separates the period from what came before it.
_PERIOD_TOTALS = (
    "SELECT t.*, r.reversal_count, r.reversal_amount, c.new_customers, d.disputes_opened FROM (SELECT "
    f"  coalesce(sum({_SIGNED}) FILTER (WHERE NOT l.inside), 0) AS outstanding_start, "
    f"  coalesce(sum({_SIGNED}), 0) AS outstanding_end, "
    "  coalesce(sum(l.amount) FILTER (WHERE l.inside AND l.kind = 'credit'), 0) AS credit_amount, "
    "  count(*) FILTER (WHERE l.inside AND l.kind = 'credit') AS credit_count, "
    "  count(DISTINCT l.customer_id) FILTER (WHERE l.inside AND l.kind = 'credit') AS credit_customers, "
    "  coalesce(sum(l.amount) FILTER (WHERE l.inside AND l.kind = 'payment'), 0) AS payment_amount, "
    "  count(*) FILTER (WHERE l.inside AND l.kind = 'payment') AS payment_count, "
    "  count(DISTINCT l.customer_id) FILTER (WHERE l.inside AND l.kind = 'payment') AS payment_customers, "
    "  coalesce(sum(l.amount) FILTER (WHERE l.inside AND l.kind = 'opening'), 0) AS opening_amount, "
    "  count(*) FILTER (WHERE l.inside AND l.kind = 'opening') AS opening_count "
    f"  FROM (SELECT e.*, e.created_at >= :start AS inside FROM ({_LIVE_RECORDED}) e WHERE e.created_at < :end) l) t, "
    "  (SELECT count(*) AS reversal_count, coalesce(sum(r.amount), 0) AS reversal_amount FROM ledger_entry r "
    f"    WHERE r.kind = 'reversal' AND {_RECORDED_IN.format(row='r')}) r, "
    f"  (SELECT count(*) AS new_customers FROM customer c WHERE {_RECORDED_IN.format(row='c')}) c, "
    f"  (SELECT count(*) AS disputes_opened FROM dispute d WHERE {_RECORDED_IN.format(row='d')}) d"
)

_PERIOD_DAYS = (
    f"SELECT {_TASHKENT_DAY} AS day, "
    "  coalesce(sum(l.amount) FILTER (WHERE l.kind = 'credit'), 0) AS credit, "
    "  coalesce(sum(l.amount) FILTER (WHERE l.kind = 'payment'), 0) AS payments "
    f"FROM ({_LIVE_RECORDED}) l WHERE {_RECORDED_IN.format(row='l')} GROUP BY day"
)

_PERIOD_STAFF = (
    "SELECT l.author_id, m.role, "
    "  coalesce(sum(l.amount) FILTER (WHERE l.kind = 'credit'), 0) AS credit_amount, "
    "  count(*) FILTER (WHERE l.kind = 'credit') AS credit_count, "
    "  coalesce(sum(l.amount) FILTER (WHERE l.kind = 'payment'), 0) AS payment_amount, "
    "  count(*) FILTER (WHERE l.kind = 'payment') AS payment_count "
    f"FROM ({_LIVE_RECORDED}) l JOIN membership m ON m.id = l.author_id "
    f"WHERE {_RECORDED_IN.format(row='l')} AND l.kind IN ('credit', 'payment') "
    "GROUP BY l.author_id, m.role "
    "ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'manager' THEN 1 ELSE 2 END, l.author_id"
)

_DEBTORS_AS_OF = (
    "SELECT c.id, c.display_name, b.balance "
    f"FROM (SELECT l.customer_id, sum({_SIGNED})::bigint AS balance FROM ({_LIVE_RECORDED}) l "
    "       WHERE l.created_at < :end GROUP BY l.customer_id) b "
    "JOIN customer c ON c.id = b.customer_id "
    "WHERE b.balance > 0 AND c.status <> 'anonymized' "
    "ORDER BY b.balance DESC, c.id DESC LIMIT :limit"
)

# BR-9 for the debt promised from :first up to but not including :before. Debts and payments are laid end
# to end in `seq` order, each taking the stretch that ends at its running total; the oldest-first
# allocation (BR-3) gives a payment to a debt exactly where their stretches overlap. This must agree with
# `qarz.domain.ledger.payment_history`; tests/api/test_reports.py compares the two on generated accounts.
_FELL_DUE = (
    f"WITH live AS ({_LIVE_RECORDED}), "
    "debt AS ("
    "  SELECT l.customer_id, l.amount, "
    "         sum(l.amount) OVER (PARTITION BY l.customer_id ORDER BY l.seq) AS upto, "
    f"         {_PROMISED.format(entry='l')} AS promised "
    "    FROM live l WHERE l.kind IN ('credit', 'opening')), "
    "paid AS ("
    "  SELECT l.customer_id, l.amount, "
    "         sum(l.amount) OVER (PARTITION BY l.customer_id ORDER BY l.seq) AS upto, "
    f"         {_TASHKENT_DAY} AS paid_on "
    "    FROM live l WHERE l.kind = 'payment'), "
    "due AS (SELECT * FROM debt WHERE promised >= :first AND promised < :before) "
    "SELECT (SELECT coalesce(sum(amount), 0) FROM due) AS due_amount, "
    "  (SELECT coalesce(sum(least(d.upto, p.upto) - greatest(d.upto - d.amount, p.upto - p.amount)), 0) "
    "     FROM due d JOIN paid p ON p.customer_id = d.customer_id AND p.paid_on <= d.promised "
    "      AND p.upto > d.upto - d.amount AND p.upto - p.amount < d.upto) AS on_time_amount"
)

# What the oldest-first allocation leaves uncovered, per customer and promised date. Whether that is
# overdue, and for how long, is decided by `qarz.domain.reports.age_band`.
_UNCOVERED = (
    f"{_OWED} "
    "SELECT customer_id, promised, sum(remaining)::bigint AS remaining FROM owed "
    "WHERE remaining > 0 AND promised IS NOT NULL GROUP BY customer_id, promised"
)


_DISPUTE_SELECT = (
    "SELECT d.id, d.entry_id, e.customer_id, e.amount, d.reason, d.status, d.decline_reason, d.created_at, "
    "c.display_name FROM dispute d JOIN ledger_entry e ON e.id = d.entry_id JOIN customer c ON c.id = e.customer_id "
)
_DISPUTE_BY_ID = f"{_DISPUTE_SELECT} WHERE d.id = :id"
_DISPUTE_BY_ENTRY = f"{_DISPUTE_SELECT} WHERE d.entry_id = :id"
_DISPUTES_OF_CUSTOMER = f"{_DISPUTE_SELECT} WHERE e.customer_id = :id"
_OPEN_DISPUTES = f"{_DISPUTE_SELECT} WHERE d.status = 'open' ORDER BY d.created_at, d.id"

_DATE_REQUEST_SELECT = (
    "SELECT r.id, r.entry_id, e.customer_id, e.amount, r.requested_date, r.reason, r.status, r.decline_reason, "
    f"r.created_at, r.closed_at, c.display_name, {_PROMISED.format(entry='e')} AS promised_date "
    "FROM date_change_request r JOIN ledger_entry e ON e.id = r.entry_id JOIN customer c ON c.id = e.customer_id "
)
_DATE_REQUEST_BY_ID = f"{_DATE_REQUEST_SELECT} WHERE r.id = :id"
_DATE_REQUESTS_OF_CUSTOMER = f"{_DATE_REQUEST_SELECT} WHERE e.customer_id = :id ORDER BY r.created_at, r.id"
_OPEN_DATE_REQUESTS = f"{_DATE_REQUEST_SELECT} WHERE r.status = 'open' ORDER BY r.created_at, r.id"


_REMINDER_SETTINGS = (
    "SELECT name, lang, reminders_on, reminder_hour, reminder_tpl, sms_on FROM shop WHERE status <> 'erased'"
)


_DELETION_STATE = "SELECT status, deletion_due FROM shop WHERE status <> 'erased'"
_DELETION_LOCKED = f"{_DELETION_STATE} FOR UPDATE"


# One week of measurement events, without any identity (ADR-010). A share is NULL when nothing fell under it.
_WEEK_FIGURES = (
    "SELECT count(DISTINCT shop_ref) FILTER (WHERE kind = 'credit') AS active_shops, "
    "count(*) FILTER (WHERE kind = 'credit') AS credit_count, "
    "coalesce(sum(amount) FILTER (WHERE kind = 'credit'), 0) AS credit_sum, "
    "count(*) FILTER (WHERE kind = 'payment') AS payment_count, "
    "coalesce(sum(amount) FILTER (WHERE kind = 'payment'), 0) AS payment_sum, "
    "count(*) FILTER (WHERE kind = 'reversal') AS reversal_count, "
    "count(*) FILTER (WHERE kind = 'dispute_opened') AS disputes_opened, "
    "count(*) FILTER (WHERE kind = 'dispute_opened')::numeric "
    "  / nullif(count(*) FILTER (WHERE kind = 'credit'), 0) AS dispute_rate, "
    "coalesce(sum(amount) FILTER (WHERE kind = 'repaid_in_time'), 0) AS repaid_in_time_sum, "
    "coalesce(sum(amount) FILTER (WHERE kind = 'repaid_late'), 0) AS repaid_late_sum, "
    "sum(amount) FILTER (WHERE kind = 'repaid_in_time')::numeric "
    "  / nullif(sum(amount) FILTER (WHERE kind IN ('repaid_in_time', 'repaid_late')), 0) AS in_time_share, "
    "percentile_cont(0.5) WITHIN GROUP (ORDER BY handle_ms) "
    "  FILTER (WHERE kind = 'credit' AND handle_ms IS NOT NULL) AS median_credit_handle_ms "
    "FROM measure.event WHERE at >= :start AND at < :end"
)
_ADMIN_ACCOUNT = (
    "SELECT status, totp_secret, confirmed_at, failed_codes, locked_until, last_step "
    "FROM admin_account WHERE user_id = :id"
)
_ADMIN_ACCOUNT_LOCKED = f"{_ADMIN_ACCOUNT} FOR UPDATE"

_FILE_COLUMNS = "f.id, f.purpose, f.object_key, f.sha256, f.size_bytes, f.mime, f.delete_after"
_FILE_BY_ID = f"SELECT {_FILE_COLUMNS} FROM stored_file f WHERE f.id = :id"
_DUE_RECEIPT_FILES = (
    f"SELECT {_FILE_COLUMNS} FROM stored_file f "
    "WHERE f.purpose = 'payment_notice' AND f.delete_after <= :now ORDER BY f.delete_after, f.id LIMIT :limit"
)
_NOTICE_SELECT = (
    "SELECT n.id, n.customer_id, n.amount, n.file_id, n.status, n.payment_entry, e.amount AS recorded_amount, "
    "n.decline_reason, n.created_at, n.closed_at, c.display_name, "
    # Row-level security keeps this inside the shop: another shop's file with the same content is not seen.
    "EXISTS (SELECT 1 FROM stored_file f JOIN stored_file g ON g.sha256 = f.sha256 "
    "AND (g.created_at, g.id) < (f.created_at, f.id) WHERE f.id = n.file_id) AS receipt_seen_before "
    "FROM payment_notice n JOIN customer c ON c.id = n.customer_id LEFT JOIN ledger_entry e ON e.id = n.payment_entry "
)
_NOTICE_BY_ID = f"{_NOTICE_SELECT} WHERE n.id = :id"
_NOTICES_OF_CUSTOMER = f"{_NOTICE_SELECT} WHERE n.customer_id = :id ORDER BY n.created_at DESC, n.id LIMIT :limit"
# A customer whose data was removed is nobody a payment can be recorded for; their notices only expire.
_OPEN_NOTICES = (
    f"{_NOTICE_SELECT} WHERE n.status = 'sent' AND n.created_at >= :since AND c.status <> 'anonymized' "
    "ORDER BY n.created_at, n.id"
)


def _like_pattern(part: str) -> str:
    escaped = part.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_QUERY_CANCELED = "57014"  # SQLSTATE query_canceled: what a statement over `statement_timeout` ends with


@contextmanager
def _timeouts() -> Iterator[None]:
    """Turn a statement the database cancelled for running too long into the application's own error.

    The transaction it ran in is rolled back by the block inside; the connection goes back to the pool
    and serves the next request.
    """
    try:
        yield
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) == _QUERY_CANCELED:
            raise StorageTimeout() from error
        raise


def _async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


_SUBSCRIPTION_LOCKED = "SELECT state, trial_ends, paid_through FROM subscription FOR UPDATE"
_ONLINE_PAYMENT_COLUMNS = (
    "id, prepare_id, months, amount, state, provider, provider_txn, provider_time, cancel_reason, "
    "started_at, paid_at, cancelled_at"
)
_ONLINE_PAYMENT_INSERT = (
    "INSERT INTO online_payment (id, shop_id, months, amount) VALUES (:id, :shop_id, :months, :amount) "
    f"RETURNING {_ONLINE_PAYMENT_COLUMNS}"
)
_ONLINE_PAYMENT_BY_ID = f"SELECT {_ONLINE_PAYMENT_COLUMNS} FROM online_payment WHERE id = :id"
_ONLINE_PAYMENT_LOCKED = f"{_ONLINE_PAYMENT_BY_ID} FOR UPDATE"
_ONLINE_PAYMENT_BY_TXN = (
    f"SELECT {_ONLINE_PAYMENT_COLUMNS} FROM online_payment "
    "WHERE provider = :provider AND provider_txn = :txn FOR UPDATE"
)


def _online_payment(row: Any) -> OnlinePayment:
    return OnlinePayment(
        id=UUID(str(row.id)),
        prepare_id=int(row.prepare_id),
        months=int(row.months),
        amount=int(row.amount),
        state=str(row.state),
        provider=row.provider,
        provider_txn=row.provider_txn,
        provider_time=None if row.provider_time is None else int(row.provider_time),
        cancel_reason=None if row.cancel_reason is None else int(row.cancel_reason),
        started_at=row.started_at,
        paid_at=row.paid_at,
        cancelled_at=row.cancelled_at,
    )


class PgTenantSession:
    def __init__(self, conn: AsyncConnection, shop_id: UUID) -> None:
        self._conn = conn
        self._shop_id = shop_id

    async def active_membership(self, user_id: UUID) -> Membership | None:
        row = (
            await self._conn.execute(
                # The shop is named here as well as by row-level security, so that a connection made
                # with a role that bypasses it still finds nobody a member of a shop they are not in.
                text(
                    "SELECT id, role FROM membership "
                    "WHERE user_id = :user_id AND shop_id = :shop_id AND status = 'active'"
                ),
                {"user_id": user_id, "shop_id": self._shop_id},
            )
        ).first()
        return None if row is None else Membership(row.id, Role(row.role))

    async def shop_settings(self) -> ShopSettings | None:
        row = (
            await self._conn.execute(
                text("SELECT id, name, lang, default_promise_days FROM shop WHERE status <> 'erased'")
            )
        ).first()
        return None if row is None else ShopSettings(row.id, row.name, row.lang, row.default_promise_days)

    async def update_shop_settings(
        self, *, name: str | None, lang: str | None, default_promise_days: int | None
    ) -> ShopSettings:
        row = (
            await self._conn.execute(
                text(
                    "UPDATE shop SET name = coalesce(:name, name), lang = coalesce(:lang, lang), "
                    "default_promise_days = coalesce(:days, default_promise_days) "
                    "WHERE id = :shop_id RETURNING id, name, lang, default_promise_days"
                ),
                {"name": name, "lang": lang, "days": default_promise_days, "shop_id": self._shop_id},
            )
        ).one()
        return ShopSettings(row.id, row.name, row.lang, row.default_promise_days)

    async def record_activity(self, *, membership_id: UUID, action: str, subject_type: str, subject_id: UUID) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO activity (id, shop_id, actor_kind, actor_id, action, subject_type, subject_id) "
                "VALUES (:id, :shop_id, 'staff', :actor_id, :action, :subject_type, :subject_id)"
            ),
            {
                "id": uuid4(),
                "shop_id": self._shop_id,
                "actor_id": membership_id,
                "action": action,
                "subject_type": subject_type,
                "subject_id": subject_id,
            },
        )

    async def create_shop(self, *, name: str, lang: str) -> ShopSettings:
        row = (
            await self._conn.execute(
                text(
                    "INSERT INTO shop (id, name, lang) VALUES (:id, :name, :lang) "
                    "RETURNING id, name, lang, default_promise_days"
                ),
                {"id": self._shop_id, "name": name, "lang": lang},
            )
        ).one()
        return ShopSettings(row.id, row.name, row.lang, row.default_promise_days)

    async def add_member(self, *, user_id: UUID, role: Role) -> UUID:
        membership_id = uuid4()
        await self._conn.execute(
            text(
                "INSERT INTO membership (id, shop_id, user_id, role, status) "
                "VALUES (:id, :shop_id, :user_id, :role, 'active')"
            ),
            {"id": membership_id, "shop_id": self._shop_id, "user_id": user_id, "role": role.value},
        )
        return membership_id

    async def create_subscription(self, *, state: str, trial_ends: date | None) -> None:
        await self._conn.execute(
            text("INSERT INTO subscription (shop_id, state, trial_ends) VALUES (:shop_id, :state, :trial_ends)"),
            {"shop_id": self._shop_id, "state": state, "trial_ends": trial_ends},
        )

    @staticmethod
    def _member(row: Any) -> MemberRecord:
        return MemberRecord(row.id, row.user_id, Role(row.role), row.status)

    async def list_members(self) -> list[MemberRecord]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT id, user_id, role, status FROM membership WHERE status <> 'removed' "
                    "ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'manager' THEN 1 ELSE 2 END, created_at, id"
                )
            )
        ).all()
        return [self._member(row) for row in rows]

    async def get_member(self, membership_id: UUID) -> MemberRecord | None:
        row = (
            await self._conn.execute(
                text("SELECT id, user_id, role, status FROM membership WHERE id = :id FOR UPDATE"),
                {"id": membership_id},
            )
        ).first()
        return None if row is None else self._member(row)

    async def update_member(self, membership_id: UUID, *, role: Role | None, status: str | None) -> MemberRecord:
        row = (
            await self._conn.execute(
                text(
                    "UPDATE membership SET role = coalesce(:role, role), status = coalesce(:status, status) "
                    "WHERE id = :id RETURNING id, user_id, role, status"
                ),
                {"id": membership_id, "role": role.value if role else None, "status": status},
            )
        ).one()
        return self._member(row)

    async def create_staff_invitation(self, token_hash: bytes, role: Role, expires_at: datetime) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO invitation (token_hash, shop_id, kind, role, expires_at) "
                "VALUES (:token_hash, :shop_id, 'staff', :role, :expires_at)"
            ),
            {"token_hash": token_hash, "shop_id": self._shop_id, "role": role.value, "expires_at": expires_at},
        )

    async def list_staff_invitations(self, now: datetime) -> list[StaffInvitation]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT token_hash, role, expires_at FROM invitation "
                    "WHERE kind = 'staff' AND status = 'issued' AND expires_at > :now ORDER BY created_at"
                ),
                {"now": now},
            )
        ).all()
        return [StaffInvitation(bytes(row.token_hash), Role(row.role), row.expires_at) for row in rows]

    async def cancel_staff_invitation(self, token_hash: bytes) -> bool:
        result = await self._conn.execute(
            text(
                "UPDATE invitation SET status = 'cancelled' "
                "WHERE token_hash = :token_hash AND kind = 'staff' AND status = 'issued'"
            ),
            {"token_hash": token_hash},
        )
        return int(result.rowcount) == 1

    @staticmethod
    def _transfer(row: Any) -> TransferRecord:
        return TransferRecord(row.id, row.from_membership, row.to_membership, row.status, row.expires_at)

    async def expire_transfers(self, now: datetime) -> None:
        await self._conn.execute(
            text(
                "UPDATE ownership_transfer SET status = 'expired', decided_at = :now "
                "WHERE status = 'pending' AND expires_at <= :now"
            ),
            {"now": now},
        )

    async def pending_transfer(self) -> TransferRecord | None:
        row = (
            await self._conn.execute(
                text(
                    "SELECT id, from_membership, to_membership, status, expires_at FROM ownership_transfer "
                    "WHERE status = 'pending' FOR UPDATE"
                )
            )
        ).first()
        return None if row is None else self._transfer(row)

    async def create_transfer(
        self, *, transfer_id: UUID, from_membership: UUID, to_membership: UUID, now: datetime, expires_at: datetime
    ) -> TransferRecord:
        row = (
            await self._conn.execute(
                text(
                    "INSERT INTO ownership_transfer "
                    "(id, shop_id, from_membership, to_membership, created_at, expires_at) "
                    "VALUES (:id, :shop_id, :from_m, :to_m, :now, :expires_at) "
                    "RETURNING id, from_membership, to_membership, status, expires_at"
                ),
                {
                    "id": transfer_id,
                    "shop_id": self._shop_id,
                    "from_m": from_membership,
                    "to_m": to_membership,
                    "now": now,
                    "expires_at": expires_at,
                },
            )
        ).one()
        return self._transfer(row)

    async def close_transfer(self, transfer_id: UUID, *, status: str, now: datetime) -> TransferRecord:
        row = (
            await self._conn.execute(
                text(
                    "UPDATE ownership_transfer SET status = :status, decided_at = :now "
                    "WHERE id = :id AND status = 'pending' "
                    "RETURNING id, from_membership, to_membership, status, expires_at"
                ),
                {"id": transfer_id, "status": status, "now": now},
            )
        ).one()
        return self._transfer(row)

    async def list_activity(
        self,
        *,
        actor: UUID | None,
        action_prefix: str | None,
        subject: UUID | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[ActivityRow]:
        # Optional filters are written so that each parameter has one type, whatever combination is used.
        rows = (
            await self._conn.execute(
                text(
                    "SELECT id, at, actor_kind, actor_id, action, subject_type, subject_id FROM activity "
                    "WHERE (CAST(:actor AS uuid) IS NULL OR actor_id = CAST(:actor AS uuid)) "
                    "  AND (CAST(:subject AS uuid) IS NULL OR subject_id = CAST(:subject AS uuid)) "
                    "  AND (CAST(:prefix AS text) IS NULL OR action LIKE CAST(:prefix AS text) || '%') "
                    "  AND (CAST(:before_at AS timestamptz) IS NULL "
                    "       OR (at, id) < (CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
                    "ORDER BY at DESC, id DESC LIMIT :limit"
                ),
                {
                    "actor": actor,
                    "subject": subject,
                    "prefix": action_prefix,
                    "before_at": before[0] if before else None,
                    "before_id": before[1] if before else None,
                    "limit": limit,
                },
            )
        ).all()
        return [
            ActivityRow(row.id, row.at, row.actor_kind, row.actor_id, row.action, row.subject_type, row.subject_id)
            for row in rows
        ]

    async def subscription(self) -> tuple[str, date | None, date | None] | None:
        row = (await self._conn.execute(text("SELECT state, trial_ends, paid_through FROM subscription"))).first()
        return None if row is None else (str(row.state), row.trial_ends, row.paid_through)

    @staticmethod
    def _customer(row: Any) -> CustomerRecord:
        return CustomerRecord(
            row.id,
            row.display_name,
            row.phone,
            row.status,
            row.reminders_off,
            None if row.credit_limit is None else int(row.credit_limit),
        )

    async def create_customer(
        self, *, customer_id: UUID, display_name: str, name_norm: str, phone: str | None
    ) -> CustomerRecord:
        row = (
            await self._conn.execute(
                text(
                    "INSERT INTO customer AS c (id, shop_id, display_name, name_norm, phone) "
                    f"VALUES (:id, :shop_id, :name, :norm, :phone) RETURNING {_CUSTOMER_COLUMNS}"
                ),
                {"id": customer_id, "shop_id": self._shop_id, "name": display_name, "norm": name_norm, "phone": phone},
            )
        ).one()
        return self._customer(row)

    async def get_customer(self, customer_id: UUID, *, for_update: bool) -> CustomerRecord | None:
        row = (
            await self._conn.execute(text(_CUSTOMER_LOCKED if for_update else _CUSTOMER_BY_ID), {"id": customer_id})
        ).first()
        return None if row is None else self._customer(row)

    async def update_customer(
        self,
        customer_id: UUID,
        *,
        display_name: str | None,
        name_norm: str | None,
        set_phone: bool,
        phone: str | None,
        reminders_off: bool | None,
        set_limit: bool = False,
        credit_limit: int | None = None,
    ) -> CustomerRecord:
        row = (
            await self._conn.execute(
                text(
                    "UPDATE customer AS c SET display_name = coalesce(:name, c.display_name), "
                    "name_norm = coalesce(:norm, c.name_norm), "
                    "phone = CASE WHEN :set_phone THEN CAST(:phone AS text) ELSE c.phone END, "
                    "reminders_off = coalesce(:reminders_off, c.reminders_off), "
                    "credit_limit = CASE WHEN :set_limit THEN CAST(:credit_limit AS bigint) ELSE c.credit_limit END "
                    f"WHERE c.id = :id RETURNING {_CUSTOMER_COLUMNS}"
                ),
                {
                    "id": customer_id,
                    "name": display_name,
                    "norm": name_norm,
                    "set_phone": set_phone,
                    "phone": phone,
                    "reminders_off": reminders_off,
                    "set_limit": set_limit,
                    "credit_limit": credit_limit,
                },
            )
        ).one()
        return self._customer(row)

    async def set_customer_status(self, customer_id: UUID, status: str) -> CustomerRecord:
        row = (
            await self._conn.execute(
                text(f"UPDATE customer AS c SET status = :status WHERE c.id = :id RETURNING {_CUSTOMER_COLUMNS}"),
                {"id": customer_id, "status": status},
            )
        ).one()
        return self._customer(row)

    async def search_customers(
        self,
        *,
        name_part: str | None,
        phone_digits: str | None,
        status: str,
        after: tuple[str, UUID] | None,
        limit: int,
    ) -> list[tuple[CustomerRecord, int, str]]:
        rows = (
            await self._conn.execute(
                text(
                    # The page is chosen first; balances are then read for its customers only.
                    f"SELECT {_CUSTOMER_COLUMNS}, c.name_norm, {_BALANCE_OF_C} AS balance FROM ("
                    f"SELECT {_CUSTOMER_COLUMNS}, c.name_norm FROM customer c "
                    "WHERE c.status = :status "
                    "  AND ((CAST(:name AS text) IS NULL AND CAST(:digits AS text) IS NULL) "
                    "       OR c.name_norm LIKE CAST(:name AS text) "
                    "       OR regexp_replace(coalesce(c.phone, ''), '[^0-9]', '', 'g') LIKE CAST(:digits AS text)) "
                    "  AND (CAST(:after_name AS text) IS NULL "
                    "       OR (c.name_norm, c.id) > (CAST(:after_name AS text), CAST(:after_id AS uuid))) "
                    "ORDER BY c.name_norm, c.id LIMIT :limit) c ORDER BY c.name_norm, c.id"
                ),
                {
                    "status": status,
                    "name": _like_pattern(name_part) if name_part else None,
                    "digits": f"%{phone_digits}%" if phone_digits else None,
                    "after_name": after[0] if after else None,
                    "after_id": after[1] if after else None,
                    "limit": limit,
                },
            )
        ).all()
        return [(self._customer(row), int(row.balance), str(row.name_norm)) for row in rows]

    async def customers_named(self, name_norm: str) -> list[tuple[CustomerRecord, int]]:
        rows = (
            await self._conn.execute(
                text(
                    f"SELECT {_CUSTOMER_COLUMNS}, {_BALANCE_OF_C} AS balance FROM customer c "
                    "WHERE c.status = 'active' AND c.name_norm = :name ORDER BY c.created_at, c.id"
                ),
                {"name": name_norm},
            )
        ).all()
        return [(self._customer(row), int(row.balance)) for row in rows]

    async def balances(self, customer_ids: list[UUID]) -> dict[UUID, int]:
        rows = (
            await self._conn.execute(
                text(
                    f"SELECT b.customer_id, b.balance FROM ({_BALANCES}) b "
                    "WHERE b.customer_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": customer_ids},
            )
        ).all()
        return {row.customer_id: int(row.balance) for row in rows}

    async def entries_of(self, customer_id: UUID) -> list[EntryRow]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT e.id, e.seq, e.kind, e.amount, e.note, e.reverses_id, e.author_id, e.created_at, "
                    f"       {_PROMISED.format(entry='e')} AS promised_date, "
                    "       (e.kind IN ('credit', 'opening') AND EXISTS ("
                    "          SELECT 1 FROM dispute d WHERE d.entry_id = e.id AND d.status = 'open')) AS disputed "
                    "FROM ledger_entry e WHERE e.customer_id = :customer_id ORDER BY e.seq"
                ),
                {"customer_id": customer_id},
            )
        ).all()
        return [
            EntryRow(
                Entry(
                    id=row.id,
                    seq=row.seq,
                    kind=EntryKind(row.kind),
                    amount=int(row.amount),
                    created_at=row.created_at,
                    reverses_id=row.reverses_id,
                    promised_date=row.promised_date,
                    disputed=bool(row.disputed),
                ),
                row.note,
                row.author_id,
            )
            for row in rows
        ]

    async def customer_of_entry(self, entry_id: UUID) -> UUID | None:
        row = (
            await self._conn.execute(text("SELECT customer_id FROM ledger_entry WHERE id = :id"), {"id": entry_id})
        ).first()
        return None if row is None else row.customer_id

    async def entry_created_at(self, entry_id: UUID) -> datetime | None:
        row = (
            await self._conn.execute(text("SELECT created_at FROM ledger_entry WHERE id = :id"), {"id": entry_id})
        ).first()
        return None if row is None else row.created_at

    async def promise_actors(self, entry_id: UUID) -> list[str]:
        rows = (
            await self._conn.execute(
                text("SELECT actor FROM promise WHERE entry_id = :id ORDER BY created_at, id"), {"id": entry_id}
            )
        ).all()
        return [str(row.actor) for row in rows]

    async def append_entry(
        self,
        *,
        entry_id: UUID,
        customer_id: UUID,
        seq: int,
        kind: str,
        amount: int,
        note: str | None,
        reverses_id: UUID | None,
        author_id: UUID,
        created_at: datetime,
    ) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO ledger_entry "
                "(id, shop_id, customer_id, seq, kind, amount, note, reverses_id, author_id, created_at) "
                "VALUES (:id, :shop_id, :customer_id, :seq, :kind, :amount, :note, :reverses_id, :author_id, :at)"
            ),
            {
                "id": entry_id,
                "shop_id": self._shop_id,
                "customer_id": customer_id,
                "seq": seq,
                "kind": kind,
                "amount": amount,
                "note": note,
                "reverses_id": reverses_id,
                "author_id": author_id,
                "at": created_at,
            },
        )

    async def add_promise(
        self, *, entry_id: UUID, promised_date: date, actor: str, created_at: datetime, reason: str | None = None
    ) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO promise (id, shop_id, entry_id, promised_date, reason, actor, created_at) "
                "VALUES (:id, :shop_id, :entry_id, :promised_date, :reason, :actor, :at)"
            ),
            {
                "id": uuid4(),
                "shop_id": self._shop_id,
                "entry_id": entry_id,
                "promised_date": promised_date,
                "reason": reason,
                "actor": actor,
                "at": created_at,
            },
        )

    async def promises_of(self, entry_ids: list[UUID]) -> dict[UUID, list[PromiseRecord]]:
        if not entry_ids:
            return {}
        rows = (
            await self._conn.execute(
                text(
                    "SELECT entry_id, promised_date, actor, reason, created_at FROM promise "
                    "WHERE entry_id = ANY(CAST(:ids AS uuid[])) ORDER BY created_at, id"
                ),
                {"ids": entry_ids},
            )
        ).all()
        history: dict[UUID, list[PromiseRecord]] = {}
        for row in rows:
            history.setdefault(row.entry_id, []).append(
                PromiseRecord(row.promised_date, str(row.actor), row.reason, row.created_at)
            )
        return history

    async def record_measure(
        self, *, kind: str, entry_ref: UUID, amount: int, promised: date | None, handle_ms: int | None = None
    ) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO measure.event (id, shop_ref, entry_ref, kind, amount, promised, handle_ms) "
                "VALUES (:id, :shop_ref, :entry_ref, :kind, :amount, :promised, :handle_ms)"
            ),
            {
                "id": uuid4(),
                "shop_ref": uuid5(_MEASURE_NAMESPACE, str(self._shop_id)),
                "entry_ref": uuid5(_MEASURE_NAMESPACE, str(entry_ref)),
                "kind": kind,
                "amount": amount,
                "promised": promised,
                "handle_ms": handle_ms,
            },
        )

    async def add_goods_lines(self, entry_id: UUID, lines: list[GoodsLineRecord]) -> None:
        # One transaction, so every row takes the same `batch_at` (its default is the transaction start)
        # and the trigger sees one batch.
        await self._conn.execute(
            text(
                "INSERT INTO goods_line "
                "(id, shop_id, entry_id, line_no, catalog_item_id, name, qty, unit, unit_price, line_total) "
                "VALUES (:id, :shop_id, :entry_id, :line_no, :item, :name, :qty, :unit, :unit_price, :line_total)"
            ),
            [
                {
                    "id": uuid4(),
                    "shop_id": self._shop_id,
                    "entry_id": entry_id,
                    "line_no": line.line_no,
                    "item": line.catalog_item_id,
                    "name": line.name,
                    "qty": line.qty,
                    "unit": line.unit,
                    "unit_price": line.unit_price,
                    "line_total": line.line_total,
                }
                for line in lines
            ],
        )

    async def goods_lines_of(self, entry_ids: list[UUID]) -> dict[UUID, list[GoodsLineRecord]]:
        if not entry_ids:
            return {}
        rows = (
            await self._conn.execute(
                text(
                    "SELECT g.entry_id, g.line_no, g.catalog_item_id, g.name, g.qty, g.unit, g.unit_price, "
                    "       g.line_total "
                    "FROM goods_line g WHERE g.entry_id = ANY(CAST(:ids AS uuid[])) ORDER BY g.entry_id, g.line_no"
                ),
                {"ids": entry_ids},
            )
        ).all()
        found: dict[UUID, list[GoodsLineRecord]] = {}
        for row in rows:
            found.setdefault(row.entry_id, []).append(
                GoodsLineRecord(
                    line_no=int(row.line_no),
                    catalog_item_id=row.catalog_item_id,
                    name=row.name,
                    qty=row.qty,
                    unit=row.unit,
                    unit_price=int(row.unit_price),
                    line_total=int(row.line_total),
                )
            )
        return found

    async def shop_totals(self, today: date) -> ShopTotals:
        row = (
            await self._conn.execute(
                text(
                    _FIGURES + "SELECT coalesce(sum(balance), 0) AS outstanding, "
                    "count(*) FILTER (WHERE balance > 0) AS debtors, "
                    "coalesce(sum(overdue), 0) AS overdue, "
                    "count(*) FILTER (WHERE overdue > 0) AS overdue_customers, "
                    "coalesce(sum(due_today), 0) AS due_today FROM figures"
                ),
                {"today": today},
            )
        ).one()
        return ShopTotals(
            int(row.outstanding), int(row.debtors), int(row.overdue), int(row.overdue_customers), int(row.due_today)
        )

    async def debtors_page(
        self, *, today: date, only_overdue: bool, before: tuple[int, UUID] | None, limit: int
    ) -> list[tuple[CustomerRecord, DebtFigures]]:
        rows = (
            await self._conn.execute(
                text(
                    _FIGURES + f"SELECT {_CUSTOMER_COLUMNS}, f.balance, f.overdue, f.since, f.due_today "
                    "FROM figures f JOIN customer c ON c.id = f.customer_id "
                    "WHERE f.balance > 0 AND c.status <> 'anonymized' "
                    "  AND (NOT :only_overdue OR f.overdue > 0) "
                    "  AND (CAST(:before_balance AS bigint) IS NULL "
                    "       OR (f.balance, c.id) < (CAST(:before_balance AS bigint), CAST(:before_id AS uuid))) "
                    "ORDER BY f.balance DESC, c.id DESC LIMIT :limit"
                ),
                {
                    "today": today,
                    "only_overdue": only_overdue,
                    "before_balance": before[0] if before else None,
                    "before_id": before[1] if before else None,
                    "limit": limit,
                },
            )
        ).all()
        return [
            (self._customer(row), DebtFigures(int(row.balance), int(row.overdue), row.since, int(row.due_today)))
            for row in rows
        ]

    async def period_totals(self, start: datetime, end: datetime) -> PeriodTotals:
        row = (await self._conn.execute(text(_PERIOD_TOTALS), {"start": start, "end": end})).one()
        return PeriodTotals(
            outstanding_start=int(row.outstanding_start),
            outstanding_end=int(row.outstanding_end),
            credit_amount=int(row.credit_amount),
            credit_count=int(row.credit_count),
            credit_customers=int(row.credit_customers),
            payment_amount=int(row.payment_amount),
            payment_count=int(row.payment_count),
            payment_customers=int(row.payment_customers),
            opening_amount=int(row.opening_amount),
            opening_count=int(row.opening_count),
            reversal_amount=int(row.reversal_amount),
            reversal_count=int(row.reversal_count),
            new_customers=int(row.new_customers),
            disputes_opened=int(row.disputes_opened),
        )

    async def period_days(self, start: datetime, end: datetime) -> list[DayFigures]:
        rows = (await self._conn.execute(text(_PERIOD_DAYS), {"start": start, "end": end})).all()
        return [DayFigures(row.day, int(row.credit), int(row.payments)) for row in rows]

    async def period_staff(self, start: datetime, end: datetime) -> list[StaffFigures]:
        rows = (await self._conn.execute(text(_PERIOD_STAFF), {"start": start, "end": end})).all()
        return [
            StaffFigures(
                row.author_id,
                Role(row.role),
                int(row.credit_amount),
                int(row.credit_count),
                int(row.payment_amount),
                int(row.payment_count),
            )
            for row in rows
        ]

    async def debtors_as_of(self, end: datetime, limit: int) -> list[tuple[UUID, str, int]]:
        rows = (await self._conn.execute(text(_DEBTORS_AS_OF), {"end": end, "limit": limit})).all()
        return [(row.id, str(row.display_name), int(row.balance)) for row in rows]

    async def fell_due(self, first: date, before: date) -> tuple[int, int]:
        row = (await self._conn.execute(text(_FELL_DUE), {"first": first, "before": before})).one()
        return int(row.on_time_amount), int(row.due_amount)

    async def uncovered_debts(self) -> list[UncoveredDebt]:
        rows = (await self._conn.execute(text(_UNCOVERED))).all()
        return [UncoveredDebt(row.customer_id, row.promised, int(row.remaining)) for row in rows]

    @staticmethod
    def _catalog_item(row: Any) -> CatalogItemRecord:
        return CatalogItemRecord(
            row.id, row.name, row.name_norm, row.unit, int(row.price), row.learned, row.status, row.merged_into
        )

    async def insert_catalog_item(
        self, *, item_id: UUID, name: str, name_norm: str, unit: str, price: int, learned: bool
    ) -> CatalogItemRecord | None:
        row = (
            await self._conn.execute(
                text(
                    "INSERT INTO catalog_item AS i (id, shop_id, name, name_norm, unit, price, learned) "
                    "VALUES (:id, :shop_id, :name, :norm, :unit, :price, :learned) "
                    f"ON CONFLICT (shop_id, name_norm) DO NOTHING RETURNING {_CATALOG_COLUMNS}"
                ),
                {
                    "id": item_id,
                    "shop_id": self._shop_id,
                    "name": name,
                    "norm": name_norm,
                    "unit": unit,
                    "price": price,
                    "learned": learned,
                },
            )
        ).first()
        return None if row is None else self._catalog_item(row)

    async def get_catalog_item(self, item_id: UUID, *, for_update: bool) -> CatalogItemRecord | None:
        row = (
            await self._conn.execute(text(_CATALOG_LOCKED if for_update else _CATALOG_BY_ID), {"id": item_id})
        ).first()
        return None if row is None else self._catalog_item(row)

    async def catalog_item_by_norm(self, name_norm: str) -> CatalogItemRecord | None:
        row = (
            await self._conn.execute(
                text(f"SELECT {_CATALOG_COLUMNS} FROM catalog_item i WHERE i.name_norm = :norm"), {"norm": name_norm}
            )
        ).first()
        return None if row is None else self._catalog_item(row)

    async def update_catalog_item(
        self, item_id: UUID, *, name: str | None, name_norm: str | None, unit: str | None, price: int | None
    ) -> CatalogItemRecord | None:
        try:
            # A savepoint, so that a name already taken leaves the transaction usable.
            async with self._conn.begin_nested():
                row = (
                    await self._conn.execute(
                        text(
                            "UPDATE catalog_item AS i SET name = coalesce(:name, i.name), "
                            "name_norm = coalesce(:norm, i.name_norm), unit = coalesce(:unit, i.unit), "
                            "price = coalesce(:price, i.price) "
                            f"WHERE i.id = :id RETURNING {_CATALOG_COLUMNS}"
                        ),
                        {"id": item_id, "name": name, "norm": name_norm, "unit": unit, "price": price},
                    )
                ).one()
        except IntegrityError as error:
            if "catalog_item_shop_id_name_norm_key" in str(error.orig):
                return None
            raise
        return self._catalog_item(row)

    async def set_catalog_item_state(
        self, item_id: UUID, *, status: str, learned: bool, merged_into: UUID | None
    ) -> CatalogItemRecord:
        row = (
            await self._conn.execute(
                text(
                    "UPDATE catalog_item AS i SET status = :status, learned = :learned, "
                    "merged_into = CAST(:merged_into AS uuid) "
                    f"WHERE i.id = :id RETURNING {_CATALOG_COLUMNS}"
                ),
                {"id": item_id, "status": status, "learned": learned, "merged_into": merged_into},
            )
        ).one()
        return self._catalog_item(row)

    async def search_catalog(
        self,
        *,
        name_part: str | None,
        status: str,
        learned: bool | None,
        after: tuple[str, UUID] | None,
        limit: int,
    ) -> list[CatalogItemRecord]:
        rows = (
            await self._conn.execute(
                text(
                    f"SELECT {_CATALOG_COLUMNS} FROM catalog_item i "
                    "WHERE i.status = :status "
                    "  AND (CAST(:learned AS boolean) IS NULL OR i.learned = CAST(:learned AS boolean)) "
                    "  AND (CAST(:name AS text) IS NULL OR i.name_norm LIKE CAST(:name AS text)) "
                    "  AND (CAST(:after_name AS text) IS NULL "
                    "       OR (i.name_norm, i.id) > (CAST(:after_name AS text), CAST(:after_id AS uuid))) "
                    "ORDER BY i.name_norm, i.id LIMIT :limit"
                ),
                {
                    "status": status,
                    "learned": learned,
                    "name": _like_pattern(name_part) if name_part else None,
                    "after_name": after[0] if after else None,
                    "after_id": after[1] if after else None,
                    "limit": limit,
                },
            )
        ).all()
        return [self._catalog_item(row) for row in rows]

    async def issue_customer_link(self, token_hash: bytes, customer_id: UUID, expires_at: datetime) -> None:
        # A new personal link replaces the one issued before for the same customer.
        await self._conn.execute(
            text(
                "UPDATE invitation SET status = 'cancelled' "
                "WHERE kind = 'customer' AND customer_id = :customer_id AND status = 'issued'"
            ),
            {"customer_id": customer_id},
        )
        await self._conn.execute(
            text(
                "INSERT INTO invitation (token_hash, shop_id, kind, customer_id, expires_at) "
                "VALUES (:token_hash, :shop_id, 'customer', :customer_id, :expires_at)"
            ),
            {"token_hash": token_hash, "shop_id": self._shop_id, "customer_id": customer_id, "expires_at": expires_at},
        )

    async def rotate_counter_code(self, token_hash: bytes) -> None:
        await self._conn.execute(
            text("UPDATE invitation SET status = 'cancelled' WHERE kind = 'counter' AND status = 'issued'")
        )
        await self._conn.execute(
            text("INSERT INTO invitation (token_hash, shop_id, kind) VALUES (:token_hash, :shop_id, 'counter')"),
            {"token_hash": token_hash, "shop_id": self._shop_id},
        )

    async def counter_code_since(self) -> datetime | None:
        row = (
            await self._conn.execute(
                text("SELECT created_at FROM invitation WHERE kind = 'counter' AND status = 'issued'")
            )
        ).first()
        return None if row is None else row.created_at

    async def link_state(self, customer_id: UUID) -> tuple[str, datetime] | None:
        row = (
            await self._conn.execute(
                text(
                    "SELECT status, created_at FROM customer_link "
                    "WHERE customer_id = :customer_id AND status IN ('active', 'unreachable')"
                ),
                {"customer_id": customer_id},
            )
        ).first()
        return None if row is None else (str(row.status), row.created_at)

    async def waiting_links(self, since: datetime) -> list[WaitingLink]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT id, waiting_name, created_at FROM customer_link "
                    "WHERE status = 'waiting' AND created_at > :since ORDER BY created_at, id"
                ),
                {"since": since},
            )
        ).all()
        return [WaitingLink(row.id, row.waiting_name, row.created_at) for row in rows]

    async def attach_waiting(self, link_id: UUID, customer_id: UUID, since: datetime) -> bool:
        result = await self._conn.execute(
            text(
                "UPDATE customer_link SET customer_id = :customer_id, status = 'active', waiting_name = NULL "
                "WHERE id = :id AND status = 'waiting' AND created_at > :since"
            ),
            {"id": link_id, "customer_id": customer_id, "since": since},
        )
        return int(result.rowcount) == 1

    async def dismiss_waiting(self, link_id: UUID, now: datetime) -> bool:
        result = await self._conn.execute(
            text(
                "UPDATE customer_link SET status = 'ended', ended_at = :now, waiting_name = NULL "
                "WHERE id = :id AND status = 'waiting'"
            ),
            {"id": link_id, "now": now},
        )
        return int(result.rowcount) == 1

    async def waiting_recipient(self, link_id: UUID) -> tuple[int, str] | None:
        row = (
            await self._conn.execute(
                text(
                    "SELECT u.tg_id, u.lang FROM customer_link l JOIN app_user u ON u.id = l.user_id "
                    "WHERE l.id = :id AND u.tg_id IS NOT NULL"
                ),
                {"id": link_id},
            )
        ).first()
        return None if row is None else (int(row.tg_id), str(row.lang))

    async def customer_recipient(self, customer_id: UUID) -> tuple[int, str] | None:
        # Only an active link is notified: an ended one has no right to the data, an unreachable one
        # has blocked the bot.
        row = (
            await self._conn.execute(
                text(
                    "SELECT u.tg_id, u.lang FROM customer_link l JOIN app_user u ON u.id = l.user_id "
                    "WHERE l.customer_id = :customer_id AND l.status = 'active' AND u.tg_id IS NOT NULL"
                ),
                {"customer_id": customer_id},
            )
        ).first()
        return None if row is None else (int(row.tg_id), str(row.lang))

    async def enqueue(
        self, *, recipient: str, payload: dict[str, Any], dedupe_key: str, channel: str = "telegram"
    ) -> bool:
        row = (
            await self._conn.execute(
                text(
                    "INSERT INTO outbox_message (id, channel, recipient, shop_id, payload, dedupe_key) "
                    "VALUES (:id, :channel, :recipient, :shop_id, CAST(:payload AS jsonb), :dedupe_key) "
                    "ON CONFLICT (dedupe_key) DO NOTHING RETURNING id"
                ),
                {
                    "id": uuid4(),
                    "channel": channel,
                    "recipient": recipient,
                    "shop_id": self._shop_id,
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "dedupe_key": dedupe_key,
                },
            )
        ).first()
        return row is not None

    async def record_customer_activity(self, *, action: str, subject_id: UUID) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO activity (id, shop_id, actor_kind, actor_id, action, subject_type, subject_id) "
                "VALUES (:id, :shop_id, 'customer', NULL, :action, 'customer', :subject_id)"
            ),
            {"id": uuid4(), "shop_id": self._shop_id, "action": action, "subject_id": subject_id},
        )

    async def removal_waiting(self, customer_id: UUID) -> bool:
        row = (
            await self._conn.execute(
                text("SELECT 1 FROM removal_request WHERE customer_id = :customer_id AND status = 'waiting'"),
                {"customer_id": customer_id},
            )
        ).first()
        return row is not None

    async def open_removal_request(self, customer_id: UUID, now: datetime) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO removal_request (id, shop_id, customer_id, status, created_at) "
                "VALUES (:id, :shop_id, :customer_id, 'waiting', :now) "
                "ON CONFLICT (customer_id) WHERE status = 'waiting' DO NOTHING"
            ),
            {"id": uuid4(), "shop_id": self._shop_id, "customer_id": customer_id, "now": now},
        )

    async def close_removal_request(self, customer_id: UUID, now: datetime) -> None:
        result = await self._conn.execute(
            text(
                "UPDATE removal_request SET status = 'completed', completed_at = :now "
                "WHERE customer_id = :customer_id AND status = 'waiting'"
            ),
            {"customer_id": customer_id, "now": now},
        )
        if int(result.rowcount) == 0:
            await self._conn.execute(
                text(
                    "INSERT INTO removal_request (id, shop_id, customer_id, status, created_at, completed_at) "
                    "VALUES (:id, :shop_id, :customer_id, 'completed', :now, :now)"
                ),
                {"id": uuid4(), "shop_id": self._shop_id, "customer_id": customer_id, "now": now},
            )

    async def anonymize_customer(self, customer_id: UUID, *, label: str, name_norm: str, now: datetime) -> None:
        await self._conn.execute(
            text(
                "UPDATE customer SET display_name = :label, name_norm = :norm, phone = NULL, lang = NULL, "
                "status = 'anonymized', reminders_off = true WHERE id = :id"
            ),
            {"id": customer_id, "label": label, "norm": name_norm},
        )
        users = (
            await self._conn.execute(
                text(
                    "UPDATE customer_link l SET user_id = NULL, waiting_name = NULL, "
                    "status = 'ended', ended_at = coalesce(l.ended_at, :now) "
                    "FROM customer_link before WHERE before.id = l.id AND l.customer_id = :id "
                    "RETURNING before.user_id"
                ),
                {"id": customer_id, "now": now},
            )
        ).all()
        await self._conn.execute(
            text("UPDATE invitation SET status = 'cancelled' WHERE customer_id = :id AND status = 'issued'"),
            {"id": customer_id},
        )
        # Receipts the customer sent (BR-32): unreachable from now on, and due for deletion at once. The
        # objects themselves are deleted by the retention cleanup, which cannot run inside this transaction.
        await self._conn.execute(
            text(
                "UPDATE stored_file SET delete_after = :now WHERE id IN "
                "(SELECT file_id FROM payment_notice WHERE customer_id = :id AND file_id IS NOT NULL)"
            ),
            {"id": customer_id, "now": now},
        )
        await self._conn.execute(
            text("UPDATE payment_notice SET file_id = NULL WHERE customer_id = :id AND file_id IS NOT NULL"),
            {"id": customer_id},
        )
        # The person's Telegram identity is forgotten too when nothing else refers to them.
        for user_id in {row.user_id for row in users if row.user_id is not None}:
            await self._conn.execute(text("SELECT forget_user_if_unused(:user_id)"), {"user_id": user_id})

    @staticmethod
    def _dispute(row: Any) -> DisputeRecord:
        return DisputeRecord(
            row.id,
            row.entry_id,
            row.customer_id,
            int(row.amount),
            str(row.reason),
            str(row.status),
            row.decline_reason,
            row.created_at,
        )

    async def dispute_of_entry(self, entry_id: UUID) -> DisputeRecord | None:
        row = (await self._conn.execute(text(_DISPUTE_BY_ENTRY), {"id": entry_id})).first()
        return None if row is None else self._dispute(row)

    async def get_dispute(self, dispute_id: UUID) -> DisputeRecord | None:
        row = (await self._conn.execute(text(_DISPUTE_BY_ID), {"id": dispute_id})).first()
        return None if row is None else self._dispute(row)

    async def disputes_of_customer(self, customer_id: UUID) -> dict[UUID, DisputeRecord]:
        rows = (await self._conn.execute(text(_DISPUTES_OF_CUSTOMER), {"id": customer_id})).all()
        return {row.entry_id: self._dispute(row) for row in rows}

    async def open_dispute(self, *, dispute_id: UUID, entry_id: UUID, reason: str, now: datetime) -> DisputeRecord:
        await self._conn.execute(
            text(
                "INSERT INTO dispute (id, shop_id, entry_id, reason, status, created_at) "
                "VALUES (:id, :shop_id, :entry_id, :reason, 'open', :now)"
            ),
            {"id": dispute_id, "shop_id": self._shop_id, "entry_id": entry_id, "reason": reason, "now": now},
        )
        row = (await self._conn.execute(text(_DISPUTE_BY_ID), {"id": dispute_id})).one()
        return self._dispute(row)

    async def close_dispute(
        self, dispute_id: UUID, *, status: str, decline_reason: str | None, decided_by: UUID | None, now: datetime
    ) -> DisputeRecord:
        await self._conn.execute(
            text(
                "UPDATE dispute SET status = :status, decline_reason = :decline_reason, decided_by = :decided_by, "
                "closed_at = :now WHERE id = :id AND status = 'open'"
            ),
            {
                "id": dispute_id,
                "status": status,
                "decline_reason": decline_reason,
                "decided_by": decided_by,
                "now": now,
            },
        )
        row = (await self._conn.execute(text(_DISPUTE_BY_ID), {"id": dispute_id})).one()
        return self._dispute(row)

    async def open_disputes(self) -> list[tuple[DisputeRecord, str]]:
        rows = (await self._conn.execute(text(_OPEN_DISPUTES))).all()
        return [(self._dispute(row), str(row.display_name)) for row in rows]

    @staticmethod
    def _date_request(row: Any) -> DateRequestRecord:
        return DateRequestRecord(
            row.id,
            row.entry_id,
            row.customer_id,
            str(row.display_name),
            int(row.amount),
            row.promised_date,
            row.requested_date,
            row.reason,
            str(row.status),
            row.decline_reason,
            row.created_at,
            row.closed_at,
        )

    async def get_date_request(self, request_id: UUID) -> DateRequestRecord | None:
        row = (await self._conn.execute(text(_DATE_REQUEST_BY_ID), {"id": request_id})).first()
        return None if row is None else self._date_request(row)

    async def date_requests_of_customer(self, customer_id: UUID) -> list[DateRequestRecord]:
        rows = (await self._conn.execute(text(_DATE_REQUESTS_OF_CUSTOMER), {"id": customer_id})).all()
        return [self._date_request(row) for row in rows]

    async def open_date_request(
        self, *, request_id: UUID, entry_id: UUID, requested_date: date, reason: str | None, now: datetime
    ) -> DateRequestRecord:
        await self._conn.execute(
            text(
                "INSERT INTO date_change_request (id, shop_id, entry_id, requested_date, reason, status, created_at) "
                "VALUES (:id, :shop_id, :entry_id, :requested_date, :reason, 'open', :now)"
            ),
            {
                "id": request_id,
                "shop_id": self._shop_id,
                "entry_id": entry_id,
                "requested_date": requested_date,
                "reason": reason,
                "now": now,
            },
        )
        row = (await self._conn.execute(text(_DATE_REQUEST_BY_ID), {"id": request_id})).one()
        return self._date_request(row)

    async def close_date_request(
        self, request_id: UUID, *, status: str, decline_reason: str | None, decided_by: UUID | None, now: datetime
    ) -> DateRequestRecord:
        await self._conn.execute(
            text(
                "UPDATE date_change_request SET status = :status, decline_reason = :decline_reason, "
                "decided_by = :decided_by, closed_at = :now WHERE id = :id AND status = 'open'"
            ),
            {
                "id": request_id,
                "status": status,
                "decline_reason": decline_reason,
                "decided_by": decided_by,
                "now": now,
            },
        )
        row = (await self._conn.execute(text(_DATE_REQUEST_BY_ID), {"id": request_id})).one()
        return self._date_request(row)

    async def open_date_requests(self) -> list[DateRequestRecord]:
        rows = (await self._conn.execute(text(_OPEN_DATE_REQUESTS))).all()
        return [self._date_request(row) for row in rows]

    # --- stored files and payment notices -------------------------------------------------------------

    @staticmethod
    def _file(row: Any) -> StoredFileRecord:
        return StoredFileRecord(
            row.id,
            str(row.purpose),
            str(row.object_key),
            bytes(row.sha256),
            int(row.size_bytes),
            str(row.mime),
            row.delete_after,
        )

    async def add_stored_file(
        self,
        *,
        file_id: UUID,
        purpose: str,
        object_key: str,
        sha256: bytes,
        size_bytes: int,
        mime: str,
        now: datetime,
        delete_after: datetime | None,
    ) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime, created_at, "
                "delete_after) VALUES (:id, :shop_id, :purpose, :object_key, :sha256, :size_bytes, :mime, :now, "
                ":delete_after)"
            ),
            {
                "id": file_id,
                "shop_id": self._shop_id,
                "purpose": purpose,
                "object_key": object_key,
                "sha256": sha256,
                "size_bytes": size_bytes,
                "mime": mime,
                "now": now,
                "delete_after": delete_after,
            },
        )

    async def get_stored_file(self, file_id: UUID) -> StoredFileRecord | None:
        row = (await self._conn.execute(text(_FILE_BY_ID), {"id": file_id})).first()
        return None if row is None else self._file(row)

    async def shorten_file_retention(self, file_id: UUID, delete_after: datetime) -> None:
        await self._conn.execute(
            text(
                "UPDATE stored_file SET delete_after = least(coalesce(delete_after, :delete_after), :delete_after) "
                "WHERE id = :id"
            ),
            {"id": file_id, "delete_after": delete_after},
        )

    async def due_receipt_files(self, now: datetime, limit: int) -> list[StoredFileRecord]:
        rows = (await self._conn.execute(text(_DUE_RECEIPT_FILES), {"now": now, "limit": limit})).all()
        return [self._file(row) for row in rows]

    async def remove_stored_file(self, file_id: UUID) -> None:
        await self._conn.execute(text("UPDATE payment_notice SET file_id = NULL WHERE file_id = :id"), {"id": file_id})
        await self._conn.execute(
            text("DELETE FROM stored_file WHERE id = :id AND purpose = 'payment_notice'"), {"id": file_id}
        )

    async def stored_object_keys(self) -> list[str]:
        rows = (await self._conn.execute(text("SELECT object_key FROM stored_file ORDER BY created_at, id"))).all()
        return [str(row.object_key) for row in rows]

    @staticmethod
    def _notice(row: Any) -> PaymentNoticeRecord:
        return PaymentNoticeRecord(
            row.id,
            row.customer_id,
            int(row.amount),
            row.file_id,
            str(row.status),
            row.payment_entry,
            None if row.recorded_amount is None else int(row.recorded_amount),
            row.decline_reason,
            row.created_at,
            row.closed_at,
            bool(row.receipt_seen_before),
        )

    async def add_payment_notice(
        self, *, notice_id: UUID, customer_id: UUID, amount: int, file_id: UUID | None, now: datetime
    ) -> PaymentNoticeRecord:
        await self._conn.execute(
            text(
                "INSERT INTO payment_notice (id, shop_id, customer_id, amount, file_id, status, created_at) "
                "VALUES (:id, :shop_id, :customer_id, :amount, :file_id, 'sent', :now)"
            ),
            {
                "id": notice_id,
                "shop_id": self._shop_id,
                "customer_id": customer_id,
                "amount": amount,
                "file_id": file_id,
                "now": now,
            },
        )
        row = (await self._conn.execute(text(_NOTICE_BY_ID), {"id": notice_id})).one()
        return self._notice(row)

    async def get_payment_notice(self, notice_id: UUID) -> PaymentNoticeRecord | None:
        row = (await self._conn.execute(text(_NOTICE_BY_ID), {"id": notice_id})).first()
        return None if row is None else self._notice(row)

    async def notices_of_customer(self, customer_id: UUID, limit: int) -> list[PaymentNoticeRecord]:
        rows = (await self._conn.execute(text(_NOTICES_OF_CUSTOMER), {"id": customer_id, "limit": limit})).all()
        return [self._notice(row) for row in rows]

    async def count_open_notices(self, customer_id: UUID) -> int:
        row = (
            await self._conn.execute(
                text("SELECT count(*) AS waiting FROM payment_notice WHERE customer_id = :id AND status = 'sent'"),
                {"id": customer_id},
            )
        ).one()
        return int(row.waiting)

    async def open_payment_notices(self, since: datetime) -> list[tuple[PaymentNoticeRecord, str]]:
        rows = (await self._conn.execute(text(_OPEN_NOTICES), {"since": since})).all()
        return [(self._notice(row), str(row.display_name)) for row in rows]

    async def close_payment_notice(
        self,
        notice_id: UUID,
        *,
        status: str,
        payment_entry: UUID | None,
        decline_reason: str | None,
        decided_by: UUID | None,
        now: datetime,
    ) -> PaymentNoticeRecord:
        await self._conn.execute(
            text(
                "UPDATE payment_notice SET status = :status, payment_entry = :payment_entry, "
                "decline_reason = :decline_reason, decided_by = :decided_by, closed_at = :now "
                "WHERE id = :id AND status = 'sent'"
            ),
            {
                "id": notice_id,
                "status": status,
                "payment_entry": payment_entry,
                "decline_reason": decline_reason,
                "decided_by": decided_by,
                "now": now,
            },
        )
        row = (await self._conn.execute(text(_NOTICE_BY_ID), {"id": notice_id})).one()
        return self._notice(row)

    async def expire_payment_notices(
        self, *, before: datetime, now: datetime, customer_id: UUID | None, files_delete_after: datetime
    ) -> int:
        rows = (
            await self._conn.execute(
                text(
                    "UPDATE payment_notice SET status = 'expired', closed_at = :now "
                    "WHERE status = 'sent' AND created_at < :before "
                    "AND (CAST(:customer_id AS uuid) IS NULL OR customer_id = CAST(:customer_id AS uuid)) "
                    "RETURNING file_id"
                ),
                {"before": before, "now": now, "customer_id": customer_id},
            )
        ).all()
        for file_id in [row.file_id for row in rows if row.file_id is not None]:
            await self.shorten_file_retention(file_id, files_delete_after)
        return len(rows)

    async def staff_recipients(self, roles: list[str]) -> list[tuple[int, str]]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT u.tg_id, u.lang FROM membership m JOIN app_user u ON u.id = m.user_id "
                    "WHERE m.status = 'active' AND m.role = ANY(CAST(:roles AS text[])) AND u.tg_id IS NOT NULL "
                    "ORDER BY m.created_at, m.id"
                ),
                {"roles": roles},
            )
        ).all()
        return [(int(row.tg_id), str(row.lang)) for row in rows]

    async def reminder_settings(self) -> ReminderSettings | None:
        row = (await self._conn.execute(text(_REMINDER_SETTINGS))).first()
        return (
            None
            if row is None
            else ReminderSettings(
                str(row.name),
                str(row.lang),
                bool(row.reminders_on),
                int(row.reminder_hour),
                int(row.reminder_tpl),
                bool(row.sms_on),
            )
        )

    async def update_reminder_settings(
        self, *, on: bool | None, hour: int | None, template: int | None, sms_on: bool | None
    ) -> None:
        await self._conn.execute(
            text(
                "UPDATE shop SET reminders_on = coalesce(:on, reminders_on), "
                "reminder_hour = coalesce(:hour, reminder_hour), reminder_tpl = coalesce(:template, reminder_tpl), "
                "sms_on = coalesce(:sms_on, sms_on) WHERE id = :shop_id"
            ),
            {"on": on, "hour": hour, "template": template, "sms_on": sms_on, "shop_id": self._shop_id},
        )

    async def reminder_candidates(self, *, after: UUID | None, limit: int) -> list[ReminderCandidate]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT c.id, c.display_name, c.phone, c.lang, c.reminders_off, u.tg_id, u.lang AS user_lang "
                    f"FROM customer c JOIN ({_BALANCES}) b ON b.customer_id = c.id AND b.balance > 0 "
                    "LEFT JOIN customer_link l ON l.customer_id = c.id AND l.status = 'active' "
                    "LEFT JOIN app_user u ON u.id = l.user_id AND u.tg_id IS NOT NULL "
                    "WHERE c.status = 'active' AND (CAST(:after AS uuid) IS NULL OR c.id > CAST(:after AS uuid)) "
                    "ORDER BY c.id LIMIT :limit"
                ),
                {"after": after, "limit": limit},
            )
        ).all()
        return [
            ReminderCandidate(
                row.id,
                str(row.display_name),
                row.phone,
                row.user_lang or row.lang,
                bool(row.reminders_off),
                None if row.tg_id is None else int(row.tg_id),
            )
            for row in rows
        ]

    async def reminder_candidate(self, customer_id: UUID) -> ReminderCandidate | None:
        row = (
            await self._conn.execute(
                text(
                    "SELECT c.id, c.display_name, c.phone, c.lang, c.reminders_off, u.tg_id, u.lang AS user_lang "
                    "FROM customer c "
                    "LEFT JOIN customer_link l ON l.customer_id = c.id AND l.status = 'active' "
                    "LEFT JOIN app_user u ON u.id = l.user_id AND u.tg_id IS NOT NULL "
                    "WHERE c.id = :id AND c.status = 'active'"
                ),
                {"id": customer_id},
            )
        ).first()
        if row is None:
            return None
        return ReminderCandidate(
            row.id,
            str(row.display_name),
            row.phone,
            row.user_lang or row.lang,
            bool(row.reminders_off),
            None if row.tg_id is None else int(row.tg_id),
        )

    async def entries_of_many(self, customer_ids: list[UUID]) -> dict[UUID, list[Entry]]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT e.customer_id, e.id, e.seq, e.kind, e.amount, e.reverses_id, e.created_at, "
                    f"       {_PROMISED.format(entry='e')} AS promised_date, "
                    "       (e.kind IN ('credit', 'opening') AND EXISTS ("
                    "          SELECT 1 FROM dispute d WHERE d.entry_id = e.id AND d.status = 'open')) AS disputed "
                    "FROM ledger_entry e WHERE e.customer_id = ANY(CAST(:ids AS uuid[])) ORDER BY e.customer_id, e.seq"
                ),
                {"ids": customer_ids},
            )
        ).all()
        accounts: dict[UUID, list[Entry]] = {customer_id: [] for customer_id in customer_ids}
        for row in rows:
            accounts[row.customer_id].append(
                Entry(
                    id=row.id,
                    seq=row.seq,
                    kind=EntryKind(row.kind),
                    amount=int(row.amount),
                    created_at=row.created_at,
                    reverses_id=row.reverses_id,
                    promised_date=row.promised_date,
                    disputed=bool(row.disputed),
                )
            )
        return accounts

    async def last_automatic_reminders(self, customer_ids: list[UUID]) -> dict[UUID, date]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT customer_id, max(sent_on) AS last FROM reminder "
                    "WHERE kind = 'auto' AND customer_id = ANY(CAST(:ids AS uuid[])) GROUP BY customer_id"
                ),
                {"ids": customer_ids},
            )
        ).all()
        return {row.customer_id: row.last for row in rows}

    async def add_reminder(self, *, customer_id: UUID, kind: str, channel: str, amount: int, sent_on: date) -> bool:
        row = (
            await self._conn.execute(
                text(
                    "INSERT INTO reminder (id, shop_id, customer_id, kind, channel, amount, sent_on) "
                    "VALUES (:id, :shop_id, :customer_id, :kind, :channel, :amount, :sent_on) "
                    "ON CONFLICT (customer_id, kind, sent_on) DO NOTHING RETURNING id"
                ),
                {
                    "id": uuid4(),
                    "shop_id": self._shop_id,
                    "customer_id": customer_id,
                    "kind": kind,
                    "channel": channel,
                    "amount": amount,
                    "sent_on": sent_on,
                },
            )
        ).first()
        return row is not None

    async def sms_reminders_since(self, first_day: date) -> int:
        row = (
            await self._conn.execute(
                text("SELECT count(*) AS n FROM reminder WHERE channel = 'sms' AND sent_on >= :first_day"),
                {"first_day": first_day},
            )
        ).one()
        return int(row.n)

    async def platform_setting(self, key: str) -> Any | None:
        row = (
            await self._conn.execute(text("SELECT value FROM platform_setting WHERE key = :key"), {"key": key})
        ).first()
        return None if row is None else row.value

    async def credit_settings(self) -> CreditSettings:
        row = (
            await self._conn.execute(
                text("SELECT default_credit_limit, sellers_may_exceed FROM shop WHERE status <> 'erased'")
            )
        ).one()
        return CreditSettings(
            None if row.default_credit_limit is None else int(row.default_credit_limit), bool(row.sellers_may_exceed)
        )

    async def update_credit_settings(
        self, *, set_default: bool, default_limit: int | None, sellers_may_exceed: bool | None
    ) -> None:
        await self._conn.execute(
            text(
                "UPDATE shop SET default_credit_limit = CASE WHEN :set_default THEN CAST(:default_limit AS bigint) "
                "ELSE default_credit_limit END, sellers_may_exceed = coalesce(:may_exceed, sellers_may_exceed) "
                "WHERE id = :shop_id"
            ),
            {
                "set_default": set_default,
                "default_limit": default_limit,
                "may_exceed": sellers_may_exceed,
                "shop_id": self._shop_id,
            },
        )

    async def limit_subscription(self, now: datetime) -> None:
        await self._conn.execute(
            text(
                "UPDATE subscription SET prior_state = state, state = 'limited', updated_at = :now "
                "WHERE state IN ('trial', 'active')"
            ),
            {"now": now},
        )

    async def record_system_activity(self, *, action: str, subject_id: UUID) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO activity (id, shop_id, actor_kind, actor_id, action, subject_type, subject_id) "
                "VALUES (:id, :shop_id, 'system', NULL, :action, 'shop', :subject_id)"
            ),
            {"id": uuid4(), "shop_id": self._shop_id, "action": action, "subject_id": subject_id},
        )

    async def deletion_state(self, *, for_update: bool = False) -> tuple[str, datetime | None]:
        row = (await self._conn.execute(text(_DELETION_LOCKED if for_update else _DELETION_STATE))).one()
        return str(row.status), row.deletion_due

    async def subscription_locked(self) -> tuple[str, date | None, date | None] | None:
        row = (await self._conn.execute(text(_SUBSCRIPTION_LOCKED))).first()
        return None if row is None else (str(row.state), row.trial_ends, row.paid_through)

    async def pay_subscription(self, paid_through: date, now: datetime) -> None:
        await self._conn.execute(
            text(
                "UPDATE subscription SET paid_through = :paid_through, updated_at = :now, "
                "state = CASE WHEN state = 'suspended' THEN state ELSE 'active' END, "
                "prior_state = CASE WHEN state = 'suspended' THEN 'active' ELSE NULL END"
            ),
            {"paid_through": paid_through, "now": now},
        )

    async def create_online_payment(self, *, order_id: UUID, months: int, amount: int) -> OnlinePayment:
        row = (
            await self._conn.execute(
                text(_ONLINE_PAYMENT_INSERT),
                {"id": order_id, "shop_id": self._shop_id, "months": months, "amount": amount},
            )
        ).one()
        return _online_payment(row)

    async def online_payment(self, order_id: UUID, *, for_update: bool = False) -> OnlinePayment | None:
        row = (
            await self._conn.execute(
                text(_ONLINE_PAYMENT_LOCKED if for_update else _ONLINE_PAYMENT_BY_ID), {"id": order_id}
            )
        ).first()
        return None if row is None else _online_payment(row)

    async def online_payment_by_txn(self, provider: str, txn: str) -> OnlinePayment | None:
        row = (await self._conn.execute(text(_ONLINE_PAYMENT_BY_TXN), {"provider": provider, "txn": txn})).first()
        return None if row is None else _online_payment(row)

    async def start_online_payment(
        self, order_id: UUID, *, provider: str, txn: str, provider_time: int | None, now: datetime
    ) -> None:
        await self._conn.execute(
            text(
                "UPDATE online_payment SET state = 'pending', provider = :provider, provider_txn = :txn, "
                "provider_time = :provider_time, started_at = :now WHERE id = :id AND state = 'created'"
            ),
            {"id": order_id, "provider": provider, "txn": txn, "provider_time": provider_time, "now": now},
        )

    async def finish_online_payment(self, order_id: UUID, now: datetime) -> None:
        await self._conn.execute(
            text("UPDATE online_payment SET state = 'paid', paid_at = :now WHERE id = :id AND state = 'pending'"),
            {"id": order_id, "now": now},
        )

    async def cancel_online_payment(self, order_id: UUID, *, reason: int | None, now: datetime) -> None:
        await self._conn.execute(
            text(
                "UPDATE online_payment SET state = 'cancelled', cancel_reason = :reason, cancelled_at = :now "
                "WHERE id = :id AND state = 'pending'"
            ),
            {"id": order_id, "reason": reason, "now": now},
        )

    async def set_deletion(self, *, status: str, due: datetime | None) -> None:
        await self._conn.execute(
            text("UPDATE shop SET status = :status, deletion_due = :due WHERE id = :shop_id"),
            {"status": status, "due": due, "shop_id": self._shop_id},
        )

    async def lock_request_key(self, key: str) -> None:
        # Transaction-scoped advisory lock: a second request with the same key in the same shop waits here
        # until the first commits, then finds the stored response.
        await self._conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"request_key:{self._shop_id}:{key}"},
        )

    async def stored_response(self, key: str) -> dict[str, Any] | None:
        row = (
            await self._conn.execute(text("SELECT response FROM request_key WHERE key = :key"), {"key": key})
        ).first()
        if row is None:
            return None
        response = row.response
        return json.loads(response) if isinstance(response, str) else dict(response)

    async def store_response(self, key: str, response: dict[str, Any]) -> None:
        await self._conn.execute(
            text("INSERT INTO request_key (shop_id, key, response) VALUES (:shop_id, :key, CAST(:response AS jsonb))"),
            {"shop_id": self._shop_id, "key": key, "response": json.dumps(response, ensure_ascii=False)},
        )


class PgPlatformSession:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def claim_update(self, update_id: int) -> bool:
        row = (
            await self._conn.execute(
                text(
                    "INSERT INTO processed_update (update_id) VALUES (:id) "
                    "ON CONFLICT (update_id) DO NOTHING RETURNING update_id"
                ),
                {"id": update_id},
            )
        ).first()
        return row is not None

    async def health_figures(self) -> dict[str, dict[str, float]]:
        waiting = (
            await self._conn.execute(
                text(
                    "SELECT channel, extract(epoch FROM now() - min(next_try_at)) AS seconds "
                    "FROM outbox_message WHERE status = 'pending' AND next_try_at <= now() GROUP BY channel"
                )
            )
        ).all()
        jobs = (
            await self._conn.execute(
                text("SELECT job, extract(epoch FROM now() - max(finished_at)) AS seconds FROM job_run GROUP BY job")
            )
        ).all()
        return {
            "qd_outbox_oldest_due_seconds": {str(row.channel): float(row.seconds) for row in waiting},
            "qd_job_last_finished_seconds": {str(row.job): float(row.seconds) for row in jobs},
        }

    async def subscriptions_to_review(self, today: date) -> list[SubscriptionToReview]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT shop_id, shop_name, state, ends_on, owner_tg, owner_lang "
                    "FROM subscriptions_to_review(:today)"
                ),
                {"today": today},
            )
        ).all()
        return [
            SubscriptionToReview(
                row.shop_id,
                str(row.shop_name),
                str(row.state),
                row.ends_on,
                None if row.owner_tg is None else int(row.owner_tg),
                row.owner_lang,
            )
            for row in rows
        ]

    async def shops_to_erase(self) -> list[ShopToErase]:
        rows = (
            await self._conn.execute(text("SELECT shop_id, shop_name, owner_tg, owner_lang FROM shops_to_erase()"))
        ).all()
        return [
            ShopToErase(
                row.shop_id, str(row.shop_name), None if row.owner_tg is None else int(row.owner_tg), row.owner_lang
            )
            for row in rows
        ]

    async def online_payment_shop(self, order_id: UUID) -> UUID | None:
        row = (await self._conn.execute(text("SELECT online_payment_shop(:id) AS shop_id"), {"id": order_id})).one()
        return None if row.shop_id is None else UUID(str(row.shop_id))

    async def online_payment_shop_by_txn(self, provider: str, txn: str) -> UUID | None:
        row = (
            await self._conn.execute(
                text("SELECT online_payment_shop_by_txn(:provider, :txn) AS shop_id"),
                {"provider": provider, "txn": txn},
            )
        ).one()
        return None if row.shop_id is None else UUID(str(row.shop_id))

    async def payme_statement(self, from_ms: int, to_ms: int) -> list[OnlinePayment]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT id, 0 AS prepare_id, 0 AS months, amount, state, 'payme' AS provider, provider_txn, "
                    "provider_time, cancel_reason, started_at, paid_at, cancelled_at "
                    "FROM payme_statement(:from_ms, :to_ms)"
                ),
                {"from_ms": from_ms, "to_ms": to_ms},
            )
        ).all()
        return [_online_payment(row) for row in rows]

    async def erase_shop(self, shop_id: UUID) -> bool:
        row = (await self._conn.execute(text("SELECT erase_shop(:shop_id) AS erased"), {"shop_id": shop_id})).one()
        return bool(row.erased)

    async def measure_between(self, start: datetime, end: datetime) -> dict[str, float | None]:
        row = (await self._conn.execute(text(_WEEK_FIGURES), {"start": start, "end": end})).one()
        return {name: None if value is None else float(value) for name, value in row._mapping.items()}

    async def store_week(self, week_start: date, metrics: dict[str, float | None]) -> None:
        for metric, value in metrics.items():
            await self._conn.execute(
                text(
                    "INSERT INTO measure.weekly (week_start, metric, value) VALUES (:week, :metric, :value) "
                    "ON CONFLICT (week_start, metric) DO UPDATE SET value = EXCLUDED.value, computed_at = now()"
                ),
                {"week": week_start, "metric": metric, "value": value},
            )

    async def stored_weeks(self, limit: int) -> list[tuple[date, str, float | None]]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT week_start, metric, value FROM measure.weekly WHERE week_start IN ("
                    "  SELECT DISTINCT week_start FROM measure.weekly ORDER BY week_start DESC LIMIT :limit) "
                    "ORDER BY week_start, metric"
                ),
                {"limit": limit},
            )
        ).all()
        return [(row.week_start, str(row.metric), None if row.value is None else float(row.value)) for row in rows]

    async def shops_due_for_reminders(self, hour: int) -> list[UUID]:
        rows = (
            await self._conn.execute(
                text("SELECT shop_id FROM shops_due_for_reminders(CAST(:hour AS smallint))"), {"hour": hour}
            )
        ).all()
        return [row.shop_id for row in rows]

    async def shops_with_receipt_work(self, stale_before: datetime, now: datetime) -> list[UUID]:
        rows = (
            await self._conn.execute(
                text("SELECT shop_id FROM shops_with_receipt_work(:stale_before, :now)"),
                {"stale_before": stale_before, "now": now},
            )
        ).all()
        return [row.shop_id for row in rows]

    async def job_done(self, job: str, period: str) -> bool:
        row = (
            await self._conn.execute(
                text("SELECT 1 FROM job_run WHERE job = :job AND period = :period"), {"job": job, "period": period}
            )
        ).first()
        return row is not None

    async def finish_job(self, job: str, period: str) -> None:
        await self._conn.execute(
            text("INSERT INTO job_run (job, period) VALUES (:job, :period) ON CONFLICT (job, period) DO NOTHING"),
            {"job": job, "period": period},
        )

    async def update_seen(self, update_id: int) -> bool:
        row = (
            await self._conn.execute(text("SELECT 1 FROM processed_update WHERE update_id = :id"), {"id": update_id})
        ).first()
        return row is not None

    async def language_of_telegram_user(self, tg_id: int) -> str | None:
        row = (
            await self._conn.execute(text("SELECT lang FROM app_user WHERE tg_id = :tg_id"), {"tg_id": tg_id})
        ).first()
        return None if row is None else str(row.lang)

    async def ensure_user(self, tg_id: int, lang: str) -> UUID:
        # The no-op update makes RETURNING yield the existing row without changing its language.
        row = (
            await self._conn.execute(
                text(
                    "INSERT INTO app_user (id, tg_id, lang) VALUES (:id, :tg_id, :lang) "
                    "ON CONFLICT (tg_id) DO UPDATE SET tg_id = EXCLUDED.tg_id RETURNING id"
                ),
                {"id": uuid4(), "tg_id": tg_id, "lang": lang},
            )
        ).one()
        return UUID(str(row.id))

    async def user_language(self, user_id: UUID) -> str | None:
        row = (await self._conn.execute(text("SELECT lang FROM app_user WHERE id = :id"), {"id": user_id})).first()
        return None if row is None else str(row.lang)

    async def set_user_language(self, user_id: UUID, lang: str) -> None:
        await self._conn.execute(text("UPDATE app_user SET lang = :lang WHERE id = :id"), {"id": user_id, "lang": lang})

    async def create_session(
        self,
        *,
        token_hash: bytes,
        user_id: UUID,
        kind: str,
        csrf_hash: bytes | None,
        now: datetime,
        expires_at: datetime,
    ) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO user_session (id, token_hash, user_id, kind, csrf_hash, created_at, expires_at) "
                "VALUES (:id, :token_hash, :user_id, :kind, :csrf_hash, :now, :expires_at)"
            ),
            {
                "id": uuid4(),
                "token_hash": token_hash,
                "user_id": user_id,
                "kind": kind,
                "csrf_hash": csrf_hash,
                "now": now,
                "expires_at": expires_at,
            },
        )

    async def find_session(self, token_hash: bytes, now: datetime) -> SessionInfo | None:
        row = (
            await self._conn.execute(
                text(
                    "SELECT user_id, kind, csrf_hash FROM user_session "
                    "WHERE token_hash = :token_hash AND revoked_at IS NULL AND expires_at > :now"
                ),
                {"token_hash": token_hash, "now": now},
            )
        ).first()
        if row is None:
            return None
        return SessionInfo(row.user_id, row.kind, None if row.csrf_hash is None else bytes(row.csrf_hash))

    async def revoke_session(self, token_hash: bytes, now: datetime) -> None:
        await self._conn.execute(
            text("UPDATE user_session SET revoked_at = :now WHERE token_hash = :token_hash AND revoked_at IS NULL"),
            {"token_hash": token_hash, "now": now},
        )

    async def platform_setting(self, key: str) -> Any | None:
        row = (
            await self._conn.execute(text("SELECT value FROM platform_setting WHERE key = :key"), {"key": key})
        ).first()
        return None if row is None else row.value

    # --- the administrator's side (ADR-017, ADR-018) ---------------------------------------------------

    async def telegram_id(self, user_id: UUID) -> int | None:
        row = (await self._conn.execute(text("SELECT tg_id FROM app_user WHERE id = :id"), {"id": user_id})).first()
        return None if row is None or row.tg_id is None else int(row.tg_id)

    async def admin_account(self, user_id: UUID, *, for_update: bool) -> AdminAccount | None:
        row = (
            await self._conn.execute(text(_ADMIN_ACCOUNT_LOCKED if for_update else _ADMIN_ACCOUNT), {"id": user_id})
        ).first()
        if row is None:
            return None
        return AdminAccount(
            status=row.status,
            secret=bytes(row.totp_secret),
            confirmed=row.confirmed_at is not None,
            failures=int(row.failed_codes),
            locked_until=row.locked_until,
            last_step=None if row.last_step is None else int(row.last_step),
        )

    async def enrol_admin(self, user_id: UUID, secret: bytes) -> bool:
        # A confirmed or disabled account is left alone: only someone who never proved they hold the
        # secret may be given a new one.
        row = (
            await self._conn.execute(
                text(
                    "INSERT INTO admin_account (user_id, totp_secret) VALUES (:id, :secret) "
                    "ON CONFLICT (user_id) DO UPDATE SET totp_secret = EXCLUDED.totp_secret, failed_codes = 0, "
                    "  locked_until = NULL, last_step = NULL "
                    "WHERE admin_account.confirmed_at IS NULL AND admin_account.status = 'active' "
                    "RETURNING user_id"
                ),
                {"id": user_id, "secret": secret},
            )
        ).first()
        return row is not None

    async def save_factor_state(
        self,
        user_id: UUID,
        *,
        failures: int,
        locked_until: datetime | None,
        last_step: int | None,
        confirmed_at: datetime | None,
    ) -> None:
        await self._conn.execute(
            text(
                "UPDATE admin_account SET failed_codes = :failures, locked_until = :locked_until, "
                "  last_step = :last_step, confirmed_at = coalesce(confirmed_at, :confirmed_at) "
                "WHERE user_id = :id"
            ),
            {
                "id": user_id,
                "failures": failures,
                "locked_until": locked_until,
                "last_step": last_step,
                "confirmed_at": confirmed_at,
            },
        )

    async def open_admin_session(
        self, *, token_hash: bytes, user_id: UUID, now: datetime, expires_at: datetime
    ) -> None:
        await self.revoke_admin_sessions(user_id, now)
        await self._conn.execute(
            text(
                "INSERT INTO admin_session (id, token_hash, user_id, created_at, expires_at) "
                "VALUES (:id, :token_hash, :user_id, :now, :expires_at)"
            ),
            {"id": uuid4(), "token_hash": token_hash, "user_id": user_id, "now": now, "expires_at": expires_at},
        )

    async def admin_session_expiry(self, token_hash: bytes, user_id: UUID, now: datetime) -> datetime | None:
        row = (
            await self._conn.execute(
                text(
                    "SELECT expires_at FROM admin_session WHERE token_hash = :token_hash AND user_id = :user_id "
                    "AND revoked_at IS NULL AND expires_at > :now"
                ),
                {"token_hash": token_hash, "user_id": user_id, "now": now},
            )
        ).first()
        return None if row is None else row.expires_at

    async def revoke_admin_sessions(self, user_id: UUID, now: datetime) -> None:
        await self._conn.execute(
            text("UPDATE admin_session SET revoked_at = :now WHERE user_id = :user_id AND revoked_at IS NULL"),
            {"user_id": user_id, "now": now},
        )

    async def add_admin_audit(
        self,
        *,
        admin_id: UUID,
        action: str,
        target_type: str,
        target_id: str | None,
        shop_id: UUID | None,
        reason: str | None,
        detail: dict[str, Any],
        now: datetime,
    ) -> UUID:
        audit_id = uuid4()
        await self._conn.execute(
            text(
                "INSERT INTO admin_audit "
                "  (id, at, admin_id, action, target_type, target_id, target_shop, reason, detail) "
                "VALUES (:id, :at, :admin_id, :action, :target_type, :target_id, :shop_id, :reason, "
                "        CAST(:detail AS jsonb))"
            ),
            {
                "id": audit_id,
                "at": now,
                "admin_id": admin_id,
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "shop_id": shop_id,
                "reason": reason,
                "detail": json.dumps(detail, ensure_ascii=False),
            },
        )
        return audit_id

    async def list_admin_audit(
        self, *, shop_id: UUID | None, action_prefix: str | None, before: tuple[datetime, UUID] | None, limit: int
    ) -> list[AdminAuditRow]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT id, at, admin_id, action, target_type, target_id, target_shop, reason, detail "
                    "FROM admin_audit "
                    "WHERE (CAST(:shop_id AS uuid) IS NULL OR target_shop = CAST(:shop_id AS uuid)) "
                    "  AND (CAST(:prefix AS text) IS NULL OR starts_with(action, CAST(:prefix AS text))) "
                    "  AND (CAST(:before_at AS timestamptz) IS NULL "
                    "       OR (at, id) < (CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
                    "ORDER BY at DESC, id DESC LIMIT :limit"
                ),
                {
                    "shop_id": shop_id,
                    "prefix": action_prefix,
                    "before_at": before[0] if before else None,
                    "before_id": before[1] if before else None,
                    "limit": limit,
                },
            )
        ).all()
        return [
            AdminAuditRow(
                audit_id=row.id,
                at=row.at,
                admin_id=row.admin_id,
                action=row.action,
                target_type=row.target_type,
                target_id=row.target_id,
                shop_id=row.target_shop,
                reason=row.reason,
                detail=json.loads(row.detail) if isinstance(row.detail, str) else dict(row.detail),
            )
            for row in rows
        ]

    async def lock_admin_request_key(self, admin_id: UUID, key: str) -> None:
        # The lock's name travels as a bound parameter; see lock_request_key of the tenant session.
        await self._conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('admin_request_key:' || :admin || ':' || :key, 0))"),
            {"admin": str(admin_id), "key": key},
        )

    async def admin_stored_response(self, admin_id: UUID, key: str) -> dict[str, Any] | None:
        row = (
            await self._conn.execute(
                text("SELECT response FROM admin_request_key WHERE admin_id = :admin_id AND key = :key"),
                {"admin_id": admin_id, "key": key},
            )
        ).first()
        if row is None:
            return None
        response = row.response
        return json.loads(response) if isinstance(response, str) else dict(response)

    async def store_admin_response(
        self, admin_id: UUID, key: str, response: dict[str, Any], about_shop: UUID | None
    ) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO admin_request_key (admin_id, key, response, about_shop) "
                "VALUES (:admin_id, :key, CAST(:response AS jsonb), CAST(:about_shop AS uuid))"
            ),
            {
                "admin_id": admin_id,
                "key": key,
                "response": json.dumps(response, ensure_ascii=False),
                "about_shop": about_shop,
            },
        )

    async def admin_shop_search(
        self,
        admin_id: UUID,
        *,
        today: date,
        query: str | None,
        state: str | None,
        shop_id: UUID | None,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[AdminShopRow]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT shop_id, name, lang, status, created_at, deletion_due, state, effective_state, "
                    "       trial_ends, paid_through, prior_state, owner_tg, staff_count, customer_count "
                    "FROM admin_shop_search(:admin, :today, CAST(:query AS text), CAST(:state AS text), "
                    "  CAST(:shop AS uuid), CAST(:after_created AS timestamptz), CAST(:after_id AS uuid), :limit)"
                ),
                {
                    "admin": admin_id,
                    "today": today,
                    "query": query,
                    "state": state,
                    "shop": shop_id,
                    "after_created": after[0] if after else None,
                    "after_id": after[1] if after else None,
                    "limit": limit,
                },
            )
        ).all()
        return [
            AdminShopRow(
                shop_id=row.shop_id,
                name=row.name,
                lang=row.lang,
                status=row.status,
                created_at=row.created_at,
                deletion_due=row.deletion_due,
                state=row.state,
                effective_state=row.effective_state,
                trial_ends=row.trial_ends,
                paid_through=row.paid_through,
                prior_state=row.prior_state,
                owner_tg=None if row.owner_tg is None else int(row.owner_tg),
                staff_count=int(row.staff_count),
                customer_count=int(row.customer_count),
            )
            for row in rows
        ]

    async def admin_shop_receipts(self, admin_id: UUID, shop_id: UUID) -> list[AdminReceiptRow]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT receipt_id, stated_amount, status, months, reject_reason, created_at, decided_at "
                    "FROM admin_shop_receipts(:admin, :shop)"
                ),
                {"admin": admin_id, "shop": shop_id},
            )
        ).all()
        return [
            AdminReceiptRow(
                receipt_id=row.receipt_id,
                stated_amount=int(row.stated_amount),
                status=row.status,
                months=None if row.months is None else int(row.months),
                reject_reason=row.reject_reason,
                created_at=row.created_at,
                decided_at=row.decided_at,
            )
            for row in rows
        ]

    async def admin_lock_subscription(self, admin_id: UUID, shop_id: UUID) -> LockedSubscription | None:
        row = (
            await self._conn.execute(
                text(
                    "SELECT state, trial_ends, paid_through, prior_state, shop_name, owner_tg, owner_lang "
                    "FROM admin_lock_subscription(:admin, :shop)"
                ),
                {"admin": admin_id, "shop": shop_id},
            )
        ).first()
        if row is None:
            return None
        return LockedSubscription(
            state=row.state,
            trial_ends=row.trial_ends,
            paid_through=row.paid_through,
            prior_state=row.prior_state,
            shop_name=row.shop_name,
            owner_tg=None if row.owner_tg is None else int(row.owner_tg),
            owner_lang=row.owner_lang,
        )

    async def admin_store_subscription(
        self,
        admin_id: UUID,
        shop_id: UUID,
        *,
        state: str,
        trial_ends: date | None,
        paid_through: date | None,
        prior_state: str | None,
        now: datetime,
    ) -> bool:
        row = (
            await self._conn.execute(
                text(
                    "SELECT admin_store_subscription(:admin, :shop, :state, CAST(:trial_ends AS date), "
                    "  CAST(:paid_through AS date), CAST(:prior_state AS text), :now) AS changed"
                ),
                {
                    "admin": admin_id,
                    "shop": shop_id,
                    "state": state,
                    "trial_ends": trial_ends,
                    "paid_through": paid_through,
                    "prior_state": prior_state,
                    "now": now,
                },
            )
        ).one()
        return bool(row.changed)

    async def platform_settings(self) -> dict[str, tuple[Any, str, datetime]]:
        rows = (await self._conn.execute(text("SELECT key, value, updated_by, updated_at FROM platform_setting"))).all()
        # The driver hands a jsonb value over already decoded; a stored string must not be decoded again.
        return {row.key: (row.value, row.updated_by, row.updated_at) for row in rows}

    async def set_platform_setting(self, key: str, value: Any, *, updated_by: str, now: datetime) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO platform_setting (key, value, updated_by, updated_at) "
                "VALUES (:key, CAST(:value AS jsonb), :updated_by, :now) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, "
                "  updated_at = EXCLUDED.updated_at"
            ),
            {"key": key, "value": json.dumps(value), "updated_by": updated_by, "now": now},
        )

    async def set_active_shop(self, user_id: UUID, shop_id: UUID) -> None:
        await self._conn.execute(
            text("UPDATE app_user SET active_shop = :shop_id WHERE id = :id"), {"id": user_id, "shop_id": shop_id}
        )

    async def active_shop(self, user_id: UUID) -> UUID | None:
        row = (
            await self._conn.execute(text("SELECT active_shop FROM app_user WHERE id = :id"), {"id": user_id})
        ).first()
        return None if row is None or row.active_shop is None else UUID(str(row.active_shop))

    async def my_memberships(self, user_id: UUID) -> list[MyShop]:
        rows = (
            await self._conn.execute(
                text("SELECT shop_id, shop_name, role, membership_id FROM my_memberships(:user_id)"),
                {"user_id": user_id},
            )
        ).all()
        return [MyShop(row.shop_id, row.shop_name, Role(row.role), row.membership_id) for row in rows]

    async def put_pending(
        self,
        *,
        pending_id: UUID,
        user_id: UUID,
        kind: str,
        payload: dict[str, Any],
        now: datetime,
        expires_at: datetime,
    ) -> None:
        await self._conn.execute(
            text("DELETE FROM chat_pending WHERE user_id = :user_id AND expires_at <= :now"),
            {"user_id": user_id, "now": now},
        )
        await self._conn.execute(
            text(
                "INSERT INTO chat_pending (id, user_id, kind, payload, created_at, expires_at) "
                "VALUES (:id, :user_id, :kind, CAST(:payload AS jsonb), :now, :expires_at) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": pending_id,
                "user_id": user_id,
                "kind": kind,
                "payload": json.dumps(payload, ensure_ascii=False),
                "now": now,
                "expires_at": expires_at,
            },
        )

    @staticmethod
    def _payload(value: Any) -> dict[str, Any]:
        return json.loads(value) if isinstance(value, str) else dict(value)

    async def take_pending(self, pending_id: UUID, user_id: UUID, kind: str, now: datetime) -> dict[str, Any] | None:
        row = (
            await self._conn.execute(
                text(
                    "DELETE FROM chat_pending WHERE id = :id AND user_id = :user_id AND kind = :kind "
                    "AND expires_at > :now RETURNING payload"
                ),
                {"id": pending_id, "user_id": user_id, "kind": kind, "now": now},
            )
        ).first()
        return None if row is None else self._payload(row.payload)

    async def current_pending(self, user_id: UUID, kind: str, now: datetime) -> tuple[UUID, dict[str, Any]] | None:
        row = (
            await self._conn.execute(
                text(
                    "SELECT id, payload FROM chat_pending WHERE user_id = :user_id AND kind = :kind "
                    "AND expires_at > :now ORDER BY created_at DESC, id LIMIT 1"
                ),
                {"user_id": user_id, "kind": kind, "now": now},
            )
        ).first()
        return None if row is None else (row.id, self._payload(row.payload))

    async def drop_pending(self, user_id: UUID, kind: str) -> None:
        await self._conn.execute(
            text("DELETE FROM chat_pending WHERE user_id = :user_id AND kind = :kind"),
            {"user_id": user_id, "kind": kind},
        )

    async def customer_token_info(self, token_hash: bytes) -> tuple[str, UUID, str] | None:
        row = (
            await self._conn.execute(
                text("SELECT kind, shop_id, shop_name FROM customer_token_info(:token_hash)"),
                {"token_hash": token_hash},
            )
        ).first()
        return None if row is None else (str(row.kind), row.shop_id, str(row.shop_name))

    async def link_customer(
        self, token_hash: bytes, user_id: UUID, consent_version: int, name: str | None
    ) -> tuple[str, UUID | None, UUID | None]:
        row = (
            await self._conn.execute(
                text(
                    "SELECT outcome, shop_id, customer_id "
                    "FROM link_customer(:token_hash, :user_id, CAST(:version AS smallint), :name)"
                ),
                {"token_hash": token_hash, "user_id": user_id, "version": consent_version, "name": name},
            )
        ).one()
        return str(row.outcome), row.shop_id, row.customer_id

    async def my_accounts(self, user_id: UUID) -> list[CustomerAccount]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT link_id, shop_id, shop_name, customer_id, display_name, balance FROM my_accounts(:user_id)"
                ),
                {"user_id": user_id},
            )
        ).all()
        return [
            CustomerAccount(
                row.link_id, row.shop_id, str(row.shop_name), row.customer_id, str(row.display_name), int(row.balance)
            )
            for row in rows
        ]

    async def my_link(self, user_id: UUID, link_id: UUID) -> tuple[UUID, UUID] | None:
        row = (
            await self._conn.execute(
                text("SELECT shop_id, customer_id FROM my_link(:user_id, :link_id)"),
                {"user_id": user_id, "link_id": link_id},
            )
        ).first()
        return None if row is None else (row.shop_id, row.customer_id)

    async def end_my_link(self, user_id: UUID, shop_id: UUID) -> bool:
        row = (
            await self._conn.execute(
                text("SELECT end_my_link(:user_id, :shop_id) AS ended"), {"user_id": user_id, "shop_id": shop_id}
            )
        ).one()
        return int(row.ended) == 1

    async def mark_recipient_reachable(self, user_id: UUID) -> int:
        row = (
            await self._conn.execute(text("SELECT mark_recipient_reachable(:user_id) AS n"), {"user_id": user_id})
        ).one()
        return int(row.n)

    async def accept_staff_invitation(self, token_hash: bytes, user_id: UUID) -> UUID | None:
        try:
            row = (
                await self._conn.execute(
                    text("SELECT accept_staff_invitation(:token_hash, :user_id) AS shop_id"),
                    {"token_hash": token_hash, "user_id": user_id},
                )
            ).one()
        except IntegrityError as error:
            if "already_member" in str(error.orig):
                raise AlreadyMember() from error
            raise
        return None if row.shop_id is None else UUID(str(row.shop_id))

    async def enqueue(
        self, *, channel: str, recipient: str, payload: dict[str, Any], dedupe_key: str, shop_id: UUID | None = None
    ) -> bool:
        row = (
            await self._conn.execute(
                text(
                    "INSERT INTO outbox_message (id, channel, recipient, shop_id, payload, dedupe_key) "
                    "VALUES (:id, :channel, :recipient, :shop_id, CAST(:payload AS jsonb), :dedupe_key) "
                    "ON CONFLICT (dedupe_key) DO NOTHING RETURNING id"
                ),
                {
                    "id": uuid4(),
                    "channel": channel,
                    "recipient": recipient,
                    "shop_id": shop_id,
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "dedupe_key": dedupe_key,
                },
            )
        ).first()
        return row is not None

    async def claim_due_messages(self, *, now: datetime, lease_seconds: int, limit: int) -> list[OutboxMessage]:
        rows = (
            await self._conn.execute(
                text(
                    "UPDATE outbox_message SET next_try_at = :lease_until "
                    "WHERE id IN (SELECT id FROM outbox_message WHERE status = 'pending' AND next_try_at <= :now "
                    "             ORDER BY created_at, id LIMIT :limit FOR UPDATE SKIP LOCKED) "
                    "RETURNING id, channel, recipient, payload, attempts, created_at"
                ),
                {"now": now, "lease_until": now + timedelta(seconds=lease_seconds), "limit": limit},
            )
        ).all()
        messages = [
            OutboxMessage(
                message_id=row.id,
                channel=row.channel,
                recipient=row.recipient,
                payload=json.loads(row.payload) if isinstance(row.payload, str) else dict(row.payload),
                attempts=row.attempts,
                created_at=row.created_at,
            )
            for row in rows
        ]
        # RETURNING does not preserve the subquery's order.
        return sorted(messages, key=lambda m: (m.created_at, str(m.message_id)))

    async def mark_sent(self, message_id: UUID, *, now: datetime) -> None:
        await self._conn.execute(
            text("UPDATE outbox_message SET status = 'sent', sent_at = :now WHERE id = :id AND status = 'pending'"),
            {"id": message_id, "now": now},
        )

    async def reschedule(self, message_id: UUID, *, next_try_at: datetime, count_attempt: bool) -> None:
        await self._conn.execute(
            text(
                "UPDATE outbox_message SET next_try_at = :next_try_at, attempts = attempts + :inc "
                "WHERE id = :id AND status = 'pending'"
            ),
            {"id": message_id, "next_try_at": next_try_at, "inc": 1 if count_attempt else 0},
        )

    async def mark_failed(self, message_id: UUID) -> None:
        await self._conn.execute(
            text("UPDATE outbox_message SET status = 'failed' WHERE id = :id AND status = 'pending'"),
            {"id": message_id},
        )

    async def fail_pending_for(self, *, channel: str, recipient: str) -> int:
        result = await self._conn.execute(
            text(
                "UPDATE outbox_message SET status = 'failed' "
                "WHERE status = 'pending' AND channel = :channel AND recipient = :recipient"
            ),
            {"channel": channel, "recipient": recipient},
        )
        return int(result.rowcount)

    async def mark_recipient_unreachable(self, tg_id: int) -> int:
        row = (await self._conn.execute(text("SELECT mark_recipient_unreachable(:tg_id) AS n"), {"tg_id": tg_id})).one()
        return int(row.n)


class Database:
    """Connection pool for the application role, which cannot bypass row-level security."""

    def __init__(self, url: str, *, pool_size: int = 5, max_overflow: int = 5, statement_timeout_ms: int = 0) -> None:
        """`statement_timeout_ms` is the longest one statement may run on these connections; 0 is no limit.

        It is a setting of each connection this pool opens, not of the role: the migration owner and
        anything else that connects as `qd_app` by other means is unaffected.
        """
        if statement_timeout_ms < 0:
            raise ValueError("the statement timeout cannot be negative")
        connect_args: dict[str, Any] = {}
        if statement_timeout_ms and _async_url(url).startswith("postgresql+asyncpg://"):
            connect_args["server_settings"] = {"statement_timeout": str(statement_timeout_ms)}
        self._engine: AsyncEngine = create_async_engine(
            _async_url(url),
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            # Errors are logged with their text; without this it would hold names and phone numbers.
            hide_parameters=True,
            connect_args=connect_args,
        )

    @asynccontextmanager
    async def tenant(self, shop_id: UUID) -> AsyncIterator[PgTenantSession]:
        with _timeouts():
            async with self._engine.begin() as conn:
                # is_local = true: the setting ends with this transaction.
                await conn.execute(text("SELECT set_config('qd.shop_id', :shop_id, true)"), {"shop_id": str(shop_id)})
                yield PgTenantSession(conn, shop_id)

    @asynccontextmanager
    async def platform(self) -> AsyncIterator[PgPlatformSession]:
        with _timeouts():
            async with self._engine.begin() as conn:
                yield PgPlatformSession(conn)

    async def user_language(self, user_id: UUID) -> str | None:
        with _timeouts():
            async with self._engine.connect() as conn:
                row = (await conn.execute(text("SELECT lang FROM app_user WHERE id = :id"), {"id": user_id})).first()
        return None if row is None else str(row.lang)

    async def reachable(self) -> bool:
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def dispose(self) -> None:
        await self._engine.dispose()
