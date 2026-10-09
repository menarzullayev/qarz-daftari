"""Send one clearly marked TEST alert to the operators' chats and say whether Telegram took it.

Run with:  python -m qarz.interface.alert_test

This is how launch criterion 9 ("alerts triggered and received") is closed after a deployment: the
message goes the way a real alert goes (the bot, straight to QD_ALERT_CHAT_IDS, not through the outbox),
and the person who runs the command then looks for it in the chat. It needs QD_BOT_TOKEN and
QD_ALERT_CHAT_IDS and nothing else: no database.

Prints one line a chat, naming it by its place in the list and its last four digits, and exits with
0 when every chat took the message, 1 when one did not, 2 when nothing is configured.
"""

import asyncio
import sys
from datetime import UTC, datetime

from aiogram import Bot

from qarz.application.ops_watch import SENT, AlertChannel, send_test_alert
from qarz.infrastructure.settings import Settings
from qarz.infrastructure.telegram_alerts import TelegramAlerts

WHY = {
    "unreachable": "Telegram did not answer, or asked to wait: try again in a minute",
    "refused": "Telegram refused the bot's token (QD_BOT_TOKEN)",
    "rejected": "Telegram refused this chat: the identifier is wrong, the bot is not a member of the group, "
    "or the person has never started the bot",
}


def masked(chat: int) -> str:
    """A chat as the output names it: enough to tell two apart, not the identifier itself."""
    return f"...{str(abs(chat))[-4:]}"


def report(outcomes: dict[int, str]) -> tuple[list[str], int]:
    """The lines to print and the exit code."""
    if not outcomes:
        return ["NOT CONFIGURED: QD_ALERT_CHAT_IDS names no chat. Nothing was sent."], 2
    lines = []
    for place, (chat, outcome) in enumerate(outcomes.items(), start=1):
        if outcome == SENT:
            lines.append(f"ACCEPTED: chat {place} ({masked(chat)}): Telegram took the test alert. Look for it there.")
        else:
            lines.append(f"NOT DELIVERED: chat {place} ({masked(chat)}): {WHY.get(outcome, outcome)}.")
    return lines, 0 if all(outcome == SENT for outcome in outcomes.values()) else 1


async def run(settings: Settings, channel: AlertChannel | None = None) -> int:
    chats = settings.alert_chats()
    bot: Bot | None = None
    if channel is None:
        if not settings.bot_token:
            print("NOT CONFIGURED: QD_BOT_TOKEN is not set. Nothing was sent.")
            return 2
        bot = Bot(settings.bot_token)
        channel = TelegramAlerts(bot)
    try:
        lines, code = report(await send_test_alert(channel, chats, datetime.now(UTC)))
    finally:
        if bot is not None:
            await bot.session.close()
    for line in lines:
        print(line)
    return code


def main() -> None:
    sys.exit(asyncio.run(run(Settings())))


if __name__ == "__main__":
    main()
