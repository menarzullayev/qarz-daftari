import type { APIRequestContext } from "@playwright/test";

import { lit, sql, stack } from "./stack.ts";
import type { Person } from "./telegram.ts";

/**
 * The bot's chat, from Telegram's side. What a person types or presses reaches the application the way
 * Telegram delivers it: an update posted to `/tg/webhook` with the webhook secret. What the bot answers
 * never leaves this machine: it is read from the `outbox_message` table, where the worker would have
 * picked it up to call Telegram.
 */
export type BotMessage = {
  method: string;
  text: string;
  /** Inline buttons under the message: label and the data Telegram sends back when one is pressed. */
  buttons: { text: string; data: string }[];
};

let updateSequence = 0;

function nextUpdateId(): number {
  updateSequence += 1;
  return Math.floor(Date.now() / 1000) * 10_000 + (updateSequence % 10_000) + Math.floor(Math.random() * 1000) * 100_000_000_000;
}

function sender(person: Person) {
  return { id: person.id, is_bot: false, first_name: person.firstName, language_code: person.languageCode ?? "uz" };
}

function readMessage(payload: string): BotMessage {
  const raw = JSON.parse(payload) as {
    method?: string;
    text?: string;
    reply_markup?: { inline_keyboard?: { text: string; callback_data?: string }[][] };
  };
  return {
    method: raw.method ?? "sendMessage",
    text: raw.text ?? "",
    buttons: (raw.reply_markup?.inline_keyboard ?? []).flat().map((button) => ({ text: button.text, data: button.callback_data ?? "" })),
  };
}

/** Everything queued for a person so far, oldest first. */
export function outbox(person: Person): BotMessage[] {
  return sql(
    `SELECT payload::text FROM outbox_message WHERE channel = 'telegram' AND recipient = ${lit(person.id)} ORDER BY created_at, id`,
  ).map((row) => readMessage(row[0] ?? "{}"));
}

function outboxIds(person: Person): Set<string> {
  return new Set(sql(`SELECT id::text FROM outbox_message WHERE recipient = ${lit(person.id)}`).map((row) => row[0] ?? ""));
}

function newMessages(person: Person, before: Set<string>): BotMessage[] {
  return sql(
    `SELECT id::text, payload::text FROM outbox_message WHERE channel = 'telegram' AND recipient = ${lit(person.id)} ORDER BY created_at, id`,
  )
    .filter((row) => !before.has(row[0] ?? ""))
    .map((row) => readMessage(row[1] ?? "{}"));
}

export class Chat {
  private messageId = 1000;

  constructor(private readonly request: APIRequestContext) {}

  private async deliver(update: Record<string, unknown>): Promise<void> {
    const answer = await this.request.post(`${stack.baseURL}/tg/webhook`, {
      headers: { "X-Telegram-Bot-Api-Secret-Token": stack.webhookSecret },
      data: update,
    });
    if (answer.status() !== 200) {
      throw new Error(`the webhook answered ${answer.status()}`);
    }
  }

  /** The person writes to the bot. Answers what the bot queued in reply. */
  async say(person: Person, text: string): Promise<BotMessage[]> {
    const before = outboxIds(person);
    this.messageId += 1;
    await this.deliver({
      update_id: nextUpdateId(),
      message: {
        message_id: this.messageId,
        date: Math.floor(Date.now() / 1000),
        chat: { id: person.id, type: "private", first_name: person.firstName },
        from: sender(person),
        text,
      },
    });
    return newMessages(person, before);
  }

  /** The person presses an inline button of a message the bot sent them. */
  async press(person: Person, button: { text: string; data: string }): Promise<BotMessage[]> {
    const before = outboxIds(person);
    this.messageId += 1;
    await this.deliver({
      update_id: nextUpdateId(),
      callback_query: {
        id: String(nextUpdateId()),
        from: sender(person),
        chat_instance: "e2e",
        data: button.data,
        message: {
          message_id: this.messageId,
          date: Math.floor(Date.now() / 1000),
          chat: { id: person.id, type: "private", first_name: person.firstName },
        },
      },
    });
    return newMessages(person, before);
  }

  /**
   * One line in the chat is one entry ("Ali 45000", "Ali -15000"). For a name the shop has not seen
   * the bot asks first, and the person presses its "add and write" button.
   */
  async entry(person: Person, line: string): Promise<BotMessage[]> {
    const answers = await this.say(person, line);
    const add = answers.flatMap((message) => message.buttons).find((button) => button.data.includes(":nc:"));
    return add ? this.press(person, add) : answers;
  }

  /**
   * A new person opens a shop the way the product offers it: /start, the "open a shop" button, the name.
   * Answers the shop's identifier.
   */
  async openShop(owner: Person, name: string): Promise<string> {
    const welcome = await this.say(owner, "/start");
    const open = welcome.flatMap((message) => message.buttons).find((button) => button.data.endsWith(":newshop"));
    if (!open) {
      throw new Error("the bot did not offer to open a shop");
    }
    await this.press(owner, open);
    await this.say(owner, name);
    const rows = sql(
      `SELECT s.id::text FROM shop s JOIN membership m ON m.shop_id = s.id JOIN app_user u ON u.id = m.user_id
       WHERE u.tg_id = ${lit(owner.id)} AND s.name = ${lit(name)}`,
    );
    const shopId = rows[0]?.[0];
    if (!shopId) {
      throw new Error("the shop was not created");
    }
    return shopId;
  }
}
