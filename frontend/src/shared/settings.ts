/**
 * Build-time settings of the client. Vite replaces `import.meta.env.VITE_*` when it builds, so a value
 * here is public: it is part of the shipped script. Nothing secret belongs in one.
 */

// Telegram: 5 to 32 characters, Latin letters, digits and underscores, starting with a letter.
const BOT_USERNAME_SHAPE = /^[A-Za-z][A-Za-z0-9_]{4,31}$/;

/** The bot's username without "@", or null when the setting is absent or is not a username. */
export function readBotUsername(raw: unknown): string | null {
  if (typeof raw !== "string") {
    return null;
  }
  const name = raw.trim().replace(/^@/, "");
  return BOT_USERNAME_SHAPE.test(name) ? name : null;
}

/** `VITE_BOT_USERNAME`: the bot whose deep links connect customers. Without it the start code is shown. */
export const BOT_USERNAME = readBotUsername(import.meta.env["VITE_BOT_USERNAME"]);

/** The bot's deep link for a start code: opening it sends "/start <code>" to the bot. */
export function deepLink(botUsername: string, start: string): string {
  return `https://t.me/${botUsername}?start=${encodeURIComponent(start)}`;
}
