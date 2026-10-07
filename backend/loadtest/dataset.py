"""Deterministic generated shops for the load test (REQ-N13, NFR-005, NFR-006).

`generate` yields one `ShopData` per shop: every row of every table for that shop, as plain tuples in the
column order given by `COLUMNS`. Nothing here touches a database, so the invariants can be checked in
memory (`loadtest.invariants`) and the same rows are what `loadtest.load` copies in.

The result depends only on the profile, the seed and the anchor instant `now`: the same three give the
same rows, identifier for identifier. Dates are laid out backwards from `now`, so a set generated today
and one generated tomorrow differ.

Rules the rows follow, because the database or the domain layer would refuse anything else:

- `seq` runs 1, 2, 3, ... per customer and `created_at` rises with it;
- a payment never exceeds the balance, and a credit sale is reversed only while the balance covers it,
  so no running balance is ever negative (INV-3);
- a reversal names an earlier entry of the same customer that is not itself a reversal and has not been
  reversed, and carries its amount (INV-5, INV-6);
- every credit sale and opening balance has a promise, between the sale date and 365 days after it;
- goods lines exist only on credit sales, share one `batch_at`, are numbered from 1, and their totals,
  each rounded as `qarz.domain.rounding.line_total` rounds, sum to the entry amount;
- an archived customer owes nothing;
- names are normalized by `qarz.domain.names.normalize_name`, exactly as the application stores them.
"""

import hashlib
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from qarz.domain.names import normalize_name
from qarz.domain.promise import tashkent_date
from qarz.domain.rounding import line_total

Row = tuple[Any, ...]

# Column order of every generated table. `loadtest.load` copies in this order, which respects the
# foreign keys.
COLUMNS: dict[str, tuple[str, ...]] = {
    "app_user": ("id", "tg_id", "lang", "active_shop", "created_at"),
    "shop": ("id", "name", "lang", "default_promise_days", "created_at"),
    "subscription": ("shop_id", "state", "trial_ends", "paid_through"),
    "membership": ("id", "shop_id", "user_id", "role", "status", "created_at"),
    "user_session": ("id", "token_hash", "user_id", "kind", "created_at", "expires_at"),
    "catalog_item": ("id", "shop_id", "name", "name_norm", "unit", "price", "learned"),
    "customer": (
        "id",
        "shop_id",
        "display_name",
        "name_norm",
        "phone",
        "lang",
        "status",
        "credit_limit",
        "created_at",
    ),
    "customer_link": (
        "id",
        "shop_id",
        "customer_id",
        "user_id",
        "status",
        "consent_text_v",
        "consent_at",
        "created_at",
    ),
    "ledger_entry": (
        "id",
        "shop_id",
        "customer_id",
        "seq",
        "kind",
        "amount",
        "note",
        "reverses_id",
        "author_id",
        "created_at",
    ),
    "promise": ("id", "shop_id", "entry_id", "promised_date", "reason", "actor", "created_at"),
    "goods_line": (
        "id",
        "shop_id",
        "entry_id",
        "line_no",
        "catalog_item_id",
        "name",
        "qty",
        "unit",
        "unit_price",
        "line_total",
        "batch_at",
    ),
    "dispute": ("id", "shop_id", "entry_id", "reason", "status", "created_at"),
    "activity": ("id", "shop_id", "at", "actor_kind", "actor_id", "action", "subject_type", "subject_id"),
}
TABLES = tuple(COLUMNS)


def _words(text: str) -> tuple[str, ...]:
    return tuple(text.split())


FIRST_NAMES = _words(
    "Alisher Aziz Akmal Anvar Bahodir Baxtiyor Bekzod Botir Davron Dilshod Doniyor Eldor Elyor Farrux Farhod "
    "G'ayrat G'ulom Husan Hasan Ibrohim Ilhom Islom Jahongir Jamshid Jasur Javlon Kamol Komil Lochin Mansur "
    "Mirzo Murod Muzaffar Nodir Odil Olim Otabek Oybek Ravshan Rustam Sanjar Sardor Sherzod Shuhrat Sobir "
    "Sunnat Temur Tohir Ulug'bek Umid Valijon Xurshid Yusuf Zafar Zokir Aziza Barno Dilfuza Dilnoza Feruza "
    "Gulnora Gulchehra Hilola Iroda Kamola Lola Madina Malika Mavluda Muhabbat Munira Nargiza Nigora Nilufar "
    "Nodira Oydin Ra'no Saida Sevara Shahlo Shoira Umida Xadicha Yulduz Zarina Zilola Zuhra Мадина Рустам "
    "Алишер Наргиза Фарход Дилноза Сардор Гулнора"
)
SURNAMES = _words(
    "Abdullayev Ahmedov Aliyev Azimov Boboyev Davlatov Ergashev Fayzullayev G'afurov Hakimov Hamidov Ibragimov "
    "Ismoilov Jo'rayev Karimov Komilov Latipov Mahmudov Mirzayev Musayev Nazarov Normatov Olimov Ortiqov "
    "Po'latov Qodirov Qosimov Rahimov Rasulov Ro'ziyev Sadikov Saidov Salimov Sharipov Sobirov Sultonov "
    "Tojiboyev Toshmatov Tursunov Umarov Usmonov Valiyev Xolmatov Xudoyberdiyev Yo'ldoshev Yusupov Zokirov "
    "Каримов Рахимов Юсупов Назаров Умаров Алиев"
)
# How a shopkeeper tells two people of one name apart: a trade, a street, a relation.
_TAGS = _words(
    "usta qo'shni tog'a amaki hoji domla shofyor tikuvchi novvoy qassob bozor mahalla katta kichik opa aka "
    "bobo xola taksi quruvchi"
)
GOODS = (
    ("Non", "dona", 4000),
    ("Un", "kg", 7500),
    ("Shakar", "kg", 13000),
    ("Guruch", "kg", 22000),
    ("Yog'", "litr", 21000),
    ("Tuxum", "dona", 1600),
    ("Sut", "litr", 12000),
    ("Qatiq", "dona", 9000),
    ("Choy", "dona", 15000),
    ("Tuz", "dona", 3000),
    ("Makaron", "dona", 8000),
    ("Kartoshka", "kg", 6000),
    ("Piyoz", "kg", 4500),
    ("Sabzi", "kg", 5000),
    ("Go'sht", "kg", 95000),
    ("Tovuq", "kg", 38000),
    ("Kolbasa", "dona", 42000),
    ("Pishloq", "kg", 78000),
    ("Sariyog'", "dona", 26000),
    ("Suv", "dona", 3500),
    ("Limonad", "dona", 11000),
    ("Sovun", "dona", 6500),
    ("Kir kukuni", "dona", 34000),
    ("Shampun", "dona", 28000),
    ("Gugurt", "dona", 500),
    ("Сахар", "kg", 13000),
    ("Мука", "kg", 7500),
    ("Масло", "litr", 21000),
    ("Чай", "dona", 15000),
    ("Хлеб", "dona", 4000),
)
_VARIANTS = (
    "",
    "katta",
    "kichik",
    "1-nav",
    "2-nav",
    "mahalliy",
    "import",
    "arzon",
    "qimmat",
    "yangi",
    "0.5",
    "1",
    "2",
    "5",
)
_NOTES = ("oylikdan beradi", "to'yga", "eri biladi", "telefon qilib aytdi", "yarmini berdi", "до зарплаты")
_DISPUTE_REASONS = ("Men bunday xarid qilmaganman", "Summa noto'g'ri yozilgan", "Я это уже оплатил")
_PROMISE_DAYS = (7, 14, 30, 30, 30, 45)


@dataclass(frozen=True)
class Profile:
    """How much to generate. `large_*` describe the shops of the NFR-005 size."""

    shops: int
    customers: int  # in all shops together
    large_shops: int
    large_customers: int  # per large shop
    large_entries: int  # per large shop
    entries_per_customer: float  # mean, in the other shops
    history_days: int = 400  # entries are spread over this many days before `now`
    catalog_large: int = 300
    catalog_small: int = 25

    def __post_init__(self) -> None:
        small_shops = self.shops - self.large_shops
        small_customers = self.customers - self.large_shops * self.large_customers
        if self.large_shops < 0 or small_shops < 0 or self.shops < 1:
            raise ValueError("there must be at least one shop, and not more large shops than shops")
        if small_customers < small_shops * 2:
            raise ValueError("every shop needs at least two customers")
        if small_shops == 0 and small_customers != 0:
            raise ValueError("customers are left over with no shop to put them in")
        if self.large_shops and self.large_entries < self.large_customers * 2:
            raise ValueError("a large shop needs at least two entries per customer")
        if self.large_customers > len(FIRST_NAMES) * len(SURNAMES) * len(_TAGS):
            raise ValueError("more customers in one shop than there are distinct names")


# The design capacity of REQ-N13 with one shop of the NFR-005 size (2,000 customers, 200,000 entries).
FULL = Profile(
    shops=5000, customers=500_000, large_shops=1, large_customers=2000, large_entries=200_000, entries_per_customer=6.0
)
# A tenth of it, for a machine that cannot hold the full set; the large shop keeps its NFR-005 size.
TENTH = Profile(
    shops=500, customers=50_000, large_shops=1, large_customers=2000, large_entries=200_000, entries_per_customer=6.0
)
# Seconds to generate and check; used by the test suite.
TINY = Profile(
    shops=4,
    customers=70,
    large_shops=1,
    large_customers=25,
    large_entries=400,
    entries_per_customer=5.0,
    catalog_large=30,
    catalog_small=8,
)
PROFILES = {"full": FULL, "tenth": TENTH, "tiny": TINY}


@dataclass
class ShopData:
    index: int
    shop_id: UUID
    large: bool
    rows: dict[str, list[Row]] = field(default_factory=lambda: {table: [] for table in TABLES})


def session_token(seed: int, user_id: UUID) -> str:
    """The bearer token of a generated staff session. The driver derives it the same way.

    Predictable on purpose, which is why the loader refuses any database that is not a throwaway one.
    """
    return "lt" + hashlib.sha256(f"qd-loadtest:{seed}:{user_id}".encode()).hexdigest()[:46]


def _uuid(rng: random.Random) -> UUID:
    return UUID(int=rng.getrandbits(128), version=4)


def _split(total: int, parts: int, rng: random.Random, *, sigma: float, least: int) -> list[int]:
    """`parts` whole numbers of at least `least` that sum to `total`, with a long tail."""
    if parts == 0:
        return []
    spare = total - parts * least
    if spare < 0:
        raise ValueError(f"cannot give {parts} parts at least {least} each out of {total}")
    weights = [rng.lognormvariate(0.0, sigma) for _ in range(parts)]
    scale = spare / sum(weights)
    sizes = [least + int(weight * scale) for weight in weights]
    # Hand the rounding remainder out one by one, so the sum is exact.
    for position in rng.sample(range(parts), k=min(parts, total - sum(sizes))):
        sizes[position] += 1
    sizes[0] += total - sum(sizes)
    return sizes


def _people_names(count: int, rng: random.Random) -> list[str]:
    """Display names for one shop. About one in fifty repeats another customer's name, as in a real book."""
    plain = len(FIRST_NAMES) * len(SURNAMES)
    names: list[str] = []
    seen: set[str] = set()
    while len(names) < count:
        first, surname = rng.choice(FIRST_NAMES), rng.choice(SURNAMES)
        # Small shops mostly know people by one name; a tag is needed once plain pairs run short.
        style = rng.random()
        if count > plain // 4 or style < 0.25:
            name = f"{first} {surname} {rng.choice(_TAGS)}" if style < 0.5 else f"{first} {surname}"
        elif style < 0.6:
            name = f"{first} {rng.choice(_TAGS)}"
        else:
            name = f"{first} {surname}"
        if names and rng.random() < 0.02:
            name = rng.choice(names)
        elif name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _catalog(count: int, rng: random.Random) -> list[tuple[str, str, int]]:
    combos = [(good, variant) for good in GOODS for variant in _VARIANTS]
    items = []
    for (name, unit, price), variant in rng.sample(combos, k=min(count, len(combos))):
        shown = f"{name} {variant}".strip()
        items.append((shown, unit, max(500, round(price * rng.uniform(0.8, 1.3) / 100) * 100)))
    return items


def _lines(
    rng: random.Random, catalog: list[tuple[UUID, str, str, int]]
) -> list[tuple[UUID | None, str, Decimal, str, int, int]]:
    """One to ten goods of one sale: (catalog item, name, quantity, unit, unit price, line total)."""
    count = min(10, 1 + int(rng.expovariate(1 / 2.5)))
    lines = []
    for _ in range(count):
        item_id: UUID | None
        if catalog and rng.random() < 0.7:
            item_id, name, unit, price = rng.choice(catalog)
        else:
            base, unit, base_price = rng.choice(GOODS)
            item_id, name, price = (
                None,
                f"{base} {rng.choice(_TAGS)}",
                max(500, round(base_price * rng.uniform(0.7, 1.4) / 100) * 100),
            )
        qty = Decimal(rng.randint(100, 5000)) / 1000 if unit in ("kg", "litr") else Decimal(rng.randint(1, 6))
        lines.append((item_id, name, qty, unit, price, line_total(qty, price)))
    return lines


def _account(
    rng: random.Random,
    shop: ShopData,
    customer_id: UUID,
    count: int,
    *,
    opened: datetime,
    now: datetime,
    staff: list[UUID],
    catalog: list[tuple[UUID, str, str, int]],
    settle: bool,
) -> tuple[int, UUID | None]:
    """Append `count` ledger entries of one customer. Returns the final balance and the last live credit sale."""
    span = (now - opened).total_seconds() - 120
    moments = sorted(rng.uniform(60, span) for _ in range(count))
    balance = 0
    last: tuple[UUID, str, int] | None = None  # the newest entry, while it may still be reversed
    last_credit: UUID | None = None
    rows = shop.rows
    for seq, offset in enumerate(moments, start=1):
        at = opened + timedelta(seconds=round(offset))
        entry_id = _uuid(rng)
        author = rng.choice(staff)
        reverses: UUID | None = None
        draw = rng.random()
        final = settle and seq == count
        can_reverse = last is not None and (last[1] == "payment" or balance - last[2] >= 0)
        if final:
            # The entry before this one was a credit sale (below), so there is something to pay off.
            kind, amount = "payment", balance
        elif settle and seq == count - 1:
            kind, amount = "credit", rng.randint(5, 300) * 1000
        elif balance == 0 and seq == 1 and draw < 0.05:
            kind, amount = "opening", rng.randint(20, 900) * 1000
        elif last is not None and can_reverse and draw < 0.02:
            kind, amount, reverses = "reversal", last[2], last[0]
        elif balance > 0 and draw < 0.42:
            kind = "payment"
            part = rng.randint(1, max(1, balance // 1000)) * 1000
            amount = balance if rng.random() < 0.45 or part > balance else part
        else:
            kind, amount = "credit", rng.randint(5, 300) * 1000

        goods: list[tuple[UUID | None, str, Decimal, str, int, int]] = []
        if kind == "credit" and rng.random() < 0.15:
            goods = _lines(rng, catalog)
            amount = sum(line[5] for line in goods)

        if kind == "reversal":
            assert last is not None
            balance += last[2] if last[1] == "payment" else -last[2]
            if last[0] == last_credit:
                last_credit = None
            last = None
        else:
            balance += -amount if kind == "payment" else amount
            last = (entry_id, kind, amount)
            if kind == "credit":
                last_credit = entry_id

        note = rng.choice(_NOTES) if kind != "reversal" and rng.random() < 0.1 else None
        rows["ledger_entry"].append(
            (entry_id, shop.shop_id, customer_id, seq, kind, amount, note, reverses, author, at)
        )
        action = "ledger.entry_reversed" if kind == "reversal" else f"ledger.{kind}_recorded"
        rows["activity"].append((_uuid(rng), shop.shop_id, at, "staff", author, action, "customer", customer_id))
        if kind in ("credit", "opening"):
            sold_on = tashkent_date(at)
            promised = sold_on + timedelta(days=rng.choice(_PROMISE_DAYS))
            actor = "default" if rng.random() < 0.6 else "staff"
            rows["promise"].append((_uuid(rng), shop.shop_id, entry_id, promised, None, actor, at))
            if rng.random() < 0.05:
                # The date was moved later; the newest row is the current promise.
                moved_at = min(at + timedelta(days=rng.randint(1, 20)), now - timedelta(seconds=30))
                if moved_at > at:
                    rows["promise"].append(
                        (
                            _uuid(rng),
                            shop.shop_id,
                            entry_id,
                            promised + timedelta(days=14),
                            "mijoz so'radi",
                            "staff",
                            moved_at,
                        )
                    )
        for line_no, (item_id, name, qty, unit, price, total) in enumerate(goods, start=1):
            rows["goods_line"].append(
                (_uuid(rng), shop.shop_id, entry_id, line_no, item_id, name, qty, unit, price, total, at)
            )
    return balance, last_credit


def _shop(profile: Profile, seed: int, now: datetime, index: int, customers: int, entries: int | None) -> ShopData:
    rng = random.Random(f"qd-loadtest:{seed}:{index}")  # noqa: S311  (test data, not a secret)
    large = entries is not None
    shop = ShopData(index, _uuid(rng), large)
    rows = shop.rows
    today = tashkent_date(now)
    history = timedelta(days=profile.history_days)
    opened = now - history - timedelta(days=rng.randint(1, 60))
    lang = "ru" if rng.random() < 0.2 else "uz"
    name = f"{rng.choice(SURNAMES)} savdo {index + 1}"
    rows["shop"].append((shop.shop_id, name, lang, rng.choice((14, 30, 30, 30)), opened))
    if rng.random() < 0.3:
        rows["subscription"].append((shop.shop_id, "trial", today + timedelta(days=rng.randint(2, 30)), None))
    else:
        rows["subscription"].append((shop.shop_id, "active", None, today + timedelta(days=rng.randint(5, 300))))

    roles = ["owner", "manager", "manager", "seller", "seller", "seller", "seller", "seller"] if large else ["owner"]
    if not large:
        roles += ["seller"] * rng.choice((0, 0, 1, 1, 2))
    staff: list[UUID] = []
    for position, role in enumerate(roles):
        user_id, membership_id = _uuid(rng), _uuid(rng)
        # Telegram identifiers are unique across the whole set: shop index and position give one each.
        rows["app_user"].append((user_id, 1_000_000_000 + index * 100 + position, lang, shop.shop_id, opened))
        rows["membership"].append((membership_id, shop.shop_id, user_id, role, "active", opened))
        token_hash = hashlib.sha256(session_token(seed, user_id).encode("ascii")).digest()
        rows["user_session"].append(
            (_uuid(rng), token_hash, user_id, "webapp", now - timedelta(hours=1), now + timedelta(days=30))
        )
        staff.append(membership_id)

    catalog: list[tuple[UUID, str, str, int]] = []
    for item_name, unit, price in _catalog(profile.catalog_large if large else profile.catalog_small, rng):
        item_id = _uuid(rng)
        catalog.append((item_id, item_name, unit, price))
        rows["catalog_item"].append(
            (item_id, shop.shop_id, item_name, normalize_name(item_name), unit, price, rng.random() < 0.2)
        )

    if entries is None:
        # A long tail: most customers have a handful of entries, a few have dozens.
        sizes = [
            max(1, min(200, round(rng.expovariate(1 / profile.entries_per_customer)) + 1)) for _ in range(customers)
        ]
    else:
        sizes = _split(entries, customers, rng, sigma=0.8, least=2)

    for position, (display_name, count) in enumerate(zip(_people_names(customers, rng), sizes, strict=True)):
        customer_id = _uuid(rng)
        created = opened + timedelta(seconds=rng.randint(0, 40 * 86400))
        archived = count >= 2 and rng.random() < 0.03
        settle = archived or (count >= 2 and rng.random() < 0.25)
        phone = f"+9989{rng.randint(0, 9)}{rng.randint(0, 9_999_999):07d}" if rng.random() < 0.6 else None
        rows["customer"].append(
            (
                customer_id,
                shop.shop_id,
                display_name,
                normalize_name(display_name),
                phone,
                None,
                "archived" if archived else "active",
                5_000_000 if rng.random() < 0.05 else None,
                created,
            )
        )
        balance, last_credit = _account(
            rng, shop, customer_id, count, opened=created, now=now, staff=staff, catalog=catalog, settle=settle
        )
        assert not (archived and balance), "an archived customer must owe nothing"
        if not archived and rng.random() < 0.1:
            user_id = _uuid(rng)
            linked = created + timedelta(seconds=30)
            rows["app_user"].append((user_id, 5_000_000_000 + index * 100_000 + position, lang, None, linked))
            rows["customer_link"].append((_uuid(rng), shop.shop_id, customer_id, user_id, "active", 2, linked, linked))
            if last_credit is not None and rng.random() < 0.03:
                reason = rng.choice(_DISPUTE_REASONS)
                rows["dispute"].append(
                    (_uuid(rng), shop.shop_id, last_credit, reason, "open", now - timedelta(hours=2))
                )
    return shop


def generate(profile: Profile, seed: int, now: datetime) -> Iterator[ShopData]:
    """Yield every shop of the profile. Large shops come first, at indexes 0 to `large_shops - 1`."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    now = now.astimezone(UTC).replace(microsecond=0)
    small_shops = profile.shops - profile.large_shops
    small_customers = profile.customers - profile.large_shops * profile.large_customers
    sizing = random.Random(f"qd-loadtest:{seed}:sizes")  # noqa: S311
    small_sizes = _split(small_customers, small_shops, sizing, sigma=0.6, least=2)
    for index in range(profile.shops):
        if index < profile.large_shops:
            yield _shop(profile, seed, now, index, profile.large_customers, profile.large_entries)
        else:
            yield _shop(profile, seed, now, index, small_sizes[index - profile.large_shops], None)


def digest(shops: Iterator[ShopData] | list[ShopData]) -> str:
    """A fingerprint of every generated row, for checking that two runs produced the same data."""
    sha = hashlib.sha256()
    for shop in shops:
        for table in TABLES:
            for row in shop.rows[table]:
                sha.update(repr((table, row)).encode())
    return sha.hexdigest()


def anchor(day: date) -> datetime:
    """Noon UTC of a day: a convenient fixed `now` for repeatable runs."""
    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
