"""Deleting a shop (REQ-048, domain rule BR-25): a waiting period, then erasure."""

from datetime import datetime, timedelta

WAITING_PERIOD = timedelta(days=30)


def erasure_due(requested_at: datetime) -> datetime:
    return requested_at + WAITING_PERIOD


def name_confirms(typed: str, shop_name: str) -> bool:
    """The owner types the shop's name to confirm. Case and spacing do not matter; nothing else is forgiven."""

    def tidy(text: str) -> str:
        return " ".join(text.split()).casefold()

    return bool(tidy(typed)) and tidy(typed) == tidy(shop_name)
