"""The languages the product speaks, and how a language code from outside becomes one of them.

The codes are BCP 47 tags and are what is stored (`app_user.lang`, `shop.lang`, `customer.lang`; the
database checks them, migration 0044) and what the front end uses (`frontend/src/i18n/types.ts`):

    uz       Uzbek, Latin script: the source every text is written in first
    uz-Cyrl  Uzbek, Cyrillic script: made from `uz` by `qarz.domain.uz_cyrillic`, never typed
    ru       Russian
    tg       Tajik
    kaa      Karakalpak, Latin script
    en       English

A text that a language does not have yet is read in Uzbek (`qarz.application.chat_texts.say`).
"""

DEFAULT = "uz"
UZ_CYRILLIC = "uz-Cyrl"
LANGUAGES = ("uz", UZ_CYRILLIC, "ru", "tg", "kaa", "en")

# Each SMS wording has to be registered with the provider before it may be sent (runbook 12), and only
# these two are. A customer whose language is another one gets the Uzbek SMS.
SMS_LANGUAGES = ("uz", "ru")

# What a validation refusal says about a language field.
ALLOWED = "must be one of: " + ", ".join(LANGUAGES)

# The primary subtag Telegram reports for its interface language, and the language a new user starts
# with. Kazakh starts in Karakalpak, its closest relative here; anything unknown starts in Uzbek. The
# person's own choice (/til, the picker) always replaces this.
_FROM_TELEGRAM = {"uz": "uz", "ru": "ru", "tg": "tg", "kaa": "kaa", "kk": "kaa", "en": "en"}


def is_language(code: object) -> bool:
    return isinstance(code, str) and code in LANGUAGES


def from_telegram(code: str | None) -> str:
    """The language for Telegram's `language_code` ("ru", "en-US", "uz_Cyrl"); Uzbek when it is unknown."""
    parts = (code or "").strip().lower().replace("_", "-").split("-")
    if parts[0] == "uz" and "cyrl" in parts[1:]:
        return UZ_CYRILLIC
    return _FROM_TELEGRAM.get(parts[0], DEFAULT)


def sms_language(code: str | None) -> str:
    """The language an SMS to this reader is written in."""
    return code if code in SMS_LANGUAGES else DEFAULT
