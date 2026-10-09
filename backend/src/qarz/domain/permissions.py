"""The permission catalogue: what a member of a shop may do, one permission at a time.

A role (`qarz.domain.access.Role`) is a preset: it gives a member a default set of permissions. The owner
of a shop may then change that set for one member, permission by permission: grant what the role does not
give, or deny what it does. This module is the single definition of the permissions, of the defaults of
each role, and of the one function that says what a member may do (`effective`). Nothing else decides.

The rules of `effective`:

- the owner holds every permission, always: nothing stored can reduce the owner's rights;
- anyone else holds the defaults of their role, plus what was granted, minus what was denied;
- a *fixed* permission never moves: it follows the role alone, so it can be neither granted nor denied.
  Those are the things that stay the owner's (the permissions themselves, the ownership, the subscription,
  deleting the shop, support access), receiving an ownership transfer, which is addressed to a manager,
  and reading one's own permissions, which every member can;
- a stored key that is not in the catalogue is ignored.

The per-member changes apply only while the platform switch `permissions_on` is on. With it off the
callers pass no overrides and `effective` returns the role's defaults, which the tests hold equal to the
role table of `qarz.domain.access` for every operation: the service behaves as it did before this module.

How a later module adds its permissions (cash book, stock, suppliers)
----------------------------------------------------------------------
1. Add a `Group` to `GROUPS` if the module is a new area of the staff screen.
2. Add one `Permission` per real distinction to `CATALOGUE`: a stable key `area.verb`, the group, the
   Uzbek and Russian labels, the roles that hold it by default, and the names of the operations it opens.
   Prefer few permissions that are fully enforced over many that are not.
3. Register the operations as usual (`operation(name, capability)` in the application layer). The
   capability must give the same roles as the permission's defaults; tests/test_permissions.py fails when
   an operation has no permission, when a permission names an operation that does not exist, and when the
   two disagree. `require_member` then enforces the permission with no further code.
4. An operation listed under two permissions is opened by either; the service must then ask for the one
   the request needs with `require_permission` (see `credits.record` and `payments.record`). A permission
   with no operation of its own is asked for the same way inside a service (`entries.others`).
5. Describe each new operation in tests/api/test_authorization_suite.py (`CALLS`, `ALLOWED_ROLES`); the
   permission matrix in tests/api/test_permissions.py then covers it for every role and override state.
6. Nothing to do in the database, the API or the panel: overrides are stored by key, and the staff screen
   draws the matrix from the catalogue the API returns.

A key is never renamed or reused: stored overrides refer to it.
"""

from dataclasses import dataclass
from typing import Literal

from qarz.domain.access import Role

Source = Literal["role", "granted", "denied"]

_ALL = frozenset({Role.SELLER, Role.MANAGER, Role.OWNER})
_MANAGERS = frozenset({Role.MANAGER, Role.OWNER})
_OWNER = frozenset({Role.OWNER})

# Lowest first: used to name the role a refused caller would need.
_RANK = (Role.SELLER, Role.MANAGER, Role.OWNER)

MAX_OVERRIDES = 64


@dataclass(frozen=True)
class Group:
    key: str
    uz: str
    ru: str


@dataclass(frozen=True)
class Permission:
    key: str
    group: str
    uz: str
    ru: str
    # The roles that hold it unless the owner decides otherwise for one member.
    roles: frozenset[Role]
    # The operations it opens (names from `qarz.application.operations`).
    operations: tuple[str, ...] = ()
    # Follows the role alone: it can be neither granted nor denied.
    fixed: bool = False


GROUPS: tuple[Group, ...] = (
    Group("customers", "Mijozlar", "Клиенты"),
    Group("ledger", "Qarz va to'lovlar", "Долги и оплаты"),
    Group("goods", "Tovarlar", "Товары"),
    Group("reminders", "Eslatmalar", "Напоминания"),
    Group("reports", "Hisobot va fayllar", "Отчёты и файлы"),
    Group("shop", "Do'kon va xodimlar", "Магазин и сотрудники"),
    Group("owner", "Faqat egasi", "Только владелец"),
)

CATALOGUE: tuple[Permission, ...] = (
    # --- customers ------------------------------------------------------------------------------------
    Permission(
        "ledger.view",
        "customers",
        "Daftarni ko'rish: mijozlar, qarzlar, tovarlar ro'yxati",
        "Просмотр книги: клиенты, долги, список товаров",
        _ALL,
        (
            "customers.list",
            "customers.read",
            "overview.read",
            "overview.debtors",
            "customers.link.read",
            "counter_code.read",
            "waiting.list",
            "shop.credit.read",
            "catalog.list",
        ),
    ),
    Permission(
        "customers.create",
        "customers",
        "Mijoz qo'shish va uni Telegramga ulash",
        "Добавлять клиентов и подключать их к Telegram",
        _ALL,
        ("customers.create", "customers.link.create", "waiting.attach", "waiting.dismiss"),
    ),
    Permission(
        "customers.edit",
        "customers",
        "Mijozni tahrirlash, limit qo'yish, arxivlash",
        "Изменять клиента, задавать лимит, архивировать",
        _MANAGERS,
        ("customers.update", "customers.archive", "customers.unarchive"),
    ),
    # --- ledger ---------------------------------------------------------------------------------------
    Permission(
        "credits.record",
        "ledger",
        "Qarzga savdo yozish",
        "Записывать продажу в долг",
        _ALL,
        ("ledger.entry.create", "ledger.entry.promise.choose", "ledger.entry.lines.add"),
    ),
    Permission(
        "payments.record",
        "ledger",
        "To'lov qabul qilish",
        "Принимать оплату",
        _ALL,
        ("ledger.entry.create",),
    ),
    Permission(
        "payment_notices.decide",
        "ledger",
        "Mijoz yuborgan to'lov xabarini tasdiqlash yoki rad etish",
        "Подтверждать или отклонять сообщение клиента об оплате",
        _ALL,
        ("payment_notices.list", "payment_notices.accept", "payment_notices.decline", "payment_notices.receipt"),
    ),
    Permission(
        "entries.others",
        "ledger",
        "Boshqa xodim yozgan savdoga muddat va tovar qo'shish",
        "Добавлять срок и товары к продаже другого сотрудника",
        _MANAGERS,
    ),
    Permission(
        "entries.over_limit",
        "ledger",
        "Limitdan oshirib qarz yozish",
        "Записывать долг сверх лимита",
        _MANAGERS,
    ),
    Permission(
        "entries.cancel",
        "ledger",
        "Yozuvni bekor qilish",
        "Отменять запись",
        _MANAGERS,
        ("ledger.entry.reverse",),
    ),
    Permission(
        "promises.change",
        "ledger",
        "To'lov muddatini o'zgartirish va muddat so'rovlarini hal qilish",
        "Менять срок оплаты и решать запросы о сроке",
        _MANAGERS,
        ("ledger.entry.promise.change", "date_requests.list", "date_requests.accept", "date_requests.decline"),
    ),
    Permission(
        "disputes.decide",
        "ledger",
        "E'tirozlarni ko'rish va hal qilish",
        "Просматривать и решать возражения",
        _MANAGERS,
        ("disputes.list", "disputes.decline"),
    ),
    # --- goods ----------------------------------------------------------------------------------------
    Permission(
        "goods.edit",
        "goods",
        "Tovarlar ro'yxatini tahrirlash",
        "Изменять список товаров",
        _MANAGERS,
        (
            "catalog.create",
            "catalog.update",
            "catalog.hide",
            "catalog.unhide",
            "catalog.learned.accept",
            "catalog.learned.dismiss",
            "catalog.learned.merge",
        ),
    ),
    # --- reminders ------------------------------------------------------------------------------------
    Permission(
        "reminders.send",
        "reminders",
        "Mijozga eslatma yuborish",
        "Отправлять клиенту напоминание",
        _MANAGERS,
        ("reminders.send", "reminders.unreachable"),
    ),
    # --- reports and files ----------------------------------------------------------------------------
    Permission(
        "reports.view",
        "reports",
        "Hisobotlarni ko'rish",
        "Просматривать отчёты",
        _MANAGERS,
        ("reports.period", "reports.overdue"),
    ),
    Permission(
        "reports.export",
        "reports",
        "Ma'lumotni faylga yuklab olish",
        "Выгружать данные в файл",
        _MANAGERS,
        ("exports.request", "exports.list", "exports.download"),
    ),
    Permission(
        "imports.run",
        "reports",
        "Fayldan ma'lumot yuklash",
        "Загружать данные из файла",
        _MANAGERS,
        (
            "imports.template",
            "imports.upload",
            "imports.list",
            "imports.read",
            "imports.apply",
            "imports.undo",
            "imports.discard",
        ),
    ),
    # --- the shop and its staff -----------------------------------------------------------------------
    Permission(
        "settings.view",
        "shop",
        "Do'kon sozlamalarini ko'rish",
        "Просматривать настройки магазина",
        _MANAGERS,
        ("shop.read", "reminders.settings.read"),
    ),
    Permission(
        "settings.edit",
        "shop",
        "Qarz limiti, eslatmalar va kassa kodi sozlamalarini o'zgartirish",
        "Менять настройки лимита, напоминаний и кода кассы",
        _MANAGERS,
        ("shop.credit.update", "reminders.settings.update", "counter_code.rotate"),
    ),
    Permission(
        "shop.edit",
        "shop",
        "Do'kon nomi, tili va odatiy muddatini o'zgartirish",
        "Менять название, язык и обычный срок магазина",
        _OWNER,
        ("shop.update",),
    ),
    Permission(
        "staff.manage",
        "shop",
        "Sotuvchilarni taklif qilish, to'xtatib turish va chiqarish",
        "Приглашать, приостанавливать и удалять продавцов",
        _OWNER,
        (
            "staff.list",
            "staff.invite",
            "staff.invitations.list",
            "staff.invitations.cancel",
            "staff.update",
            "staff.remove",
        ),
    ),
    Permission(
        "activity.view",
        "shop",
        "Amallar jurnalini ko'rish",
        "Просматривать журнал действий",
        _OWNER,
        ("activity.list",),
    ),
    # --- never moved ----------------------------------------------------------------------------------
    Permission(
        "membership.own",
        "owner",
        "O'z ruxsatlarini ko'rish (har bir xodimda bor)",
        "Видеть свои разрешения (есть у каждого сотрудника)",
        _ALL,
        ("permissions.mine",),
        fixed=True,
    ),
    Permission(
        "permissions.manage",
        "owner",
        "Xodimlarning ruxsatlarini boshqarish",
        "Управлять разрешениями сотрудников",
        _OWNER,
        ("permissions.catalogue", "permissions.member.read", "permissions.member.set"),
        fixed=True,
    ),
    Permission(
        "ownership.transfer",
        "owner",
        "Do'konni boshqa egaga o'tkazish",
        "Передавать магазин другому владельцу",
        _OWNER,
        ("ownership.transfer.start", "ownership.transfer.cancel"),
        fixed=True,
    ),
    Permission(
        "ownership.receive",
        "owner",
        "Egalikni qabul qilish (menejerga taklif qilinganda)",
        "Принимать владение (когда его предлагают менеджеру)",
        _MANAGERS,
        ("ownership.transfer.read", "ownership.transfer.accept", "ownership.transfer.decline"),
        fixed=True,
    ),
    Permission(
        "subscription.manage",
        "owner",
        "Obuna va to'lovlar",
        "Подписка и её оплата",
        _OWNER,
        (
            "shop.subscription.read",
            "shop.subscription.receipts.submit",
            "shop.subscription.receipts.list",
            "shop.subscription.online_order.create",
        ),
        fixed=True,
    ),
    Permission(
        "support.manage",
        "owner",
        "Qo'llab-quvvatlash xizmatining do'konga kirishini ko'rish va yopish",
        "Видеть и закрывать доступ поддержки к магазину",
        _OWNER,
        ("shop.support_access.list", "shop.support_access.end"),
        fixed=True,
    ),
    Permission(
        "shop.delete",
        "owner",
        "Do'konni o'chirish",
        "Удалять магазин",
        _OWNER,
        ("shop.deletion.read", "shop.deletion.request", "shop.deletion.cancel"),
        fixed=True,
    ),
)


def _index() -> dict[str, Permission]:
    by_key: dict[str, Permission] = {}
    groups = {group.key for group in GROUPS}
    for permission in CATALOGUE:
        if permission.key in by_key:
            raise ValueError(f"permission {permission.key!r} is in the catalogue twice")
        if permission.group not in groups:
            raise ValueError(f"permission {permission.key!r} names an unknown group")
        if Role.OWNER not in permission.roles:
            raise ValueError(f"permission {permission.key!r} must be held by the owner")
        by_key[permission.key] = permission
    return by_key


_BY_KEY = _index()

# The permissions a service asks for by name (`qarz.application.authorization.require_permission`).
CREDITS_RECORD = "credits.record"
PAYMENTS_RECORD = "payments.record"
PAYMENT_NOTICES_DECIDE = "payment_notices.decide"
ENTRIES_OTHERS = "entries.others"
ENTRIES_OVER_LIMIT = "entries.over_limit"
ENTRIES_CANCEL = "entries.cancel"
PROMISES_CHANGE = "promises.change"
DISPUTES_DECIDE = "disputes.decide"
STAFF_MANAGE = "staff.manage"
PERMISSIONS_MANAGE = "permissions.manage"
ALL_KEYS: frozenset[str] = frozenset(_BY_KEY)
FIXED_KEYS: frozenset[str] = frozenset(key for key, permission in _BY_KEY.items() if permission.fixed)
_NO_OVERRIDES: frozenset[str] = frozenset()


class InvalidOverrides(ValueError):
    """The overrides asked for cannot be stored. `fields` says why, per list."""

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("invalid permission overrides")
        self.fields = fields


def get(key: str) -> Permission:
    return _BY_KEY[key]


def role_defaults(role: Role) -> frozenset[str]:
    """What the role holds when nothing was changed for the member."""
    return frozenset(key for key, permission in _BY_KEY.items() if role in permission.roles)


def effective(
    role: Role, granted: frozenset[str] = _NO_OVERRIDES, denied: frozenset[str] = _NO_OVERRIDES
) -> frozenset[str]:
    """Every permission the member holds. The one place that decides (see the module's rules)."""
    if role is Role.OWNER:
        return ALL_KEYS
    movable = ALL_KEYS - FIXED_KEYS
    return (role_defaults(role) | (granted & movable)) - (denied & movable)


def holds(role: Role, granted: frozenset[str], denied: frozenset[str], key: str) -> bool:
    if key not in _BY_KEY:
        raise LookupError(f"{key!r} is not a permission of the catalogue")
    return key in effective(role, granted, denied)


def source(role: Role, granted: frozenset[str], denied: frozenset[str], key: str) -> Source:
    """Where the member's answer for one permission comes from: the role, a grant or a denial."""
    permission = _BY_KEY[key]
    if role is Role.OWNER or permission.fixed:
        return "role"
    if key in denied:
        return "denied"
    if key in granted:
        return "granted"
    return "role"


def lowest_role_holding(key: str) -> Role:
    """The lowest role that holds the permission by default: named to a caller refused for lacking it."""
    roles = _BY_KEY[key].roles
    return next(role for role in _RANK if role in roles)


def clean_overrides(role: Role, granted: list[str], denied: list[str]) -> tuple[frozenset[str], frozenset[str]]:
    """The overrides as they are stored for a member of this role, or InvalidOverrides.

    Only real changes are kept: granting what the role already gives, or denying what it does not, is
    dropped, so "no overrides" always means "exactly the role's defaults".
    """
    fields: dict[str, str] = {}
    if role is Role.OWNER:
        raise InvalidOverrides({"_": "the owner's permissions cannot be changed"})
    for name, keys in (("granted", granted), ("denied", denied)):
        if len(keys) > MAX_OVERRIDES:
            fields[name] = f"at most {MAX_OVERRIDES} permissions"
        elif len(set(keys)) != len(keys):
            fields[name] = "the same permission is listed twice"
        elif unknown := sorted(set(keys) - ALL_KEYS):
            fields[name] = f"unknown permission: {unknown[0]}"
        elif fixed := sorted(set(keys) & FIXED_KEYS):
            fields[name] = f"this permission cannot be changed: {fixed[0]}"
    if not fields and (both := sorted(set(granted) & set(denied))):
        fields["denied"] = f"granted and denied at once: {both[0]}"
    if fields:
        raise InvalidOverrides(fields)
    defaults = role_defaults(role)
    return frozenset(granted) - defaults, frozenset(denied) & defaults


def permissions_of_operation(name: str) -> tuple[Permission, ...]:
    """The permissions that open an operation: holding any one of them does."""
    return _BY_OPERATION.get(name, ())


def _by_operation() -> dict[str, tuple[Permission, ...]]:
    found: dict[str, list[Permission]] = {}
    for permission in CATALOGUE:
        for name in permission.operations:
            found.setdefault(name, []).append(permission)
    return {name: tuple(permissions) for name, permissions in found.items()}


_BY_OPERATION = _by_operation()
