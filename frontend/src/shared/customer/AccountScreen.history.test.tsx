// @vitest-environment jsdom
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { I18nProvider } from "../../i18n/I18nProvider";
import { catalogs } from "../../i18n/catalog";
import { accountBody, accountEntryBody, fakeServer, LINK_ID, ok } from "../../testing/fakeServer";
import { createApi } from "../api";
import { AccountScreen } from "./AccountScreen";

afterEach(cleanup);

/**
 * The customer sees their own payment history indicator on their own page (BR-9; the founder's decision
 * of 2026-10-08, DEC-066, which changes DEC-033). Until then this file proved the opposite: that the
 * indicator never reached the page. What stays the shop's own, and is still proved below, is the
 * seller's note on an entry and who wrote the entry (REQ-045).
 */
const HISTORY = { on_time_percent: 67, on_time_amount: 200000, due_amount: 300000, longest_delay_days: 9 };

// The two languages every text exists in; the others may trail behind and read Uzbek (i18n/check.ts).
const LANGUAGES = ["uz", "ru"] as const;
type Language = (typeof LANGUAGES)[number];

const TITLE: Record<Language, string> = { uz: "To'lov tarixingiz", ru: "Ваша история оплат" };

async function open(language: Language, body: Record<string, unknown>): Promise<HTMLElement> {
  const server = fakeServer(() => ok(accountBody(body)));
  const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "customer-session" } }).account(LINK_ID);
  render(
    <I18nProvider initialLanguage={language}>
      <AccountScreen api={api} back={<a href="#/my">back</a>} />
    </I18nProvider>,
  );
  await screen.findByRole("heading", { level: 2, name: "Baraka savdo" });
  const section = screen.getByRole("heading", { level: 2, name: TITLE[language] }).closest("section");
  expect(section).not.toBeNull();
  return section as HTMLElement;
}

/** The text of a message without its placeholders, so that a figure in the middle does not matter. */
function wording(language: Language, key: string): string[] {
  const message = (catalogs[language] as Record<string, unknown>)[key];
  const templates = typeof message === "string" ? [message] : Object.values(message as Record<string, string>);
  return templates.flatMap((template) => template.split(/\{[a-zA-Z]+\}/)).filter((part) => part.trim().length >= 6);
}

describe("the customer's own payment history on their page (BR-9, DEC-066)", () => {
  it("shows the share paid on time, the longest delay and the amounts, in Uzbek", async () => {
    const section = await open("uz", { payment_history: HISTORY });
    const text = section.textContent ?? "";
    expect(text).toContain("Muddati kelgan qarzlaringizning 67% qismini o'z vaqtida to'lagansiz.");
    expect(text).toContain("Eng uzoq kechikishingiz: 9 kun.");
    expect(text).toMatch(/Muddati kelgan 300\s000 so'm dan 200\s000 so'm va'da qilingan kungacha to'langan\./);
    expect(text).toContain("Faqat shu do'kondagi yozuvlaringiz bo'yicha hisoblangan.");
  });

  it("shows the same in Russian, with the plural of the days", async () => {
    const section = await open("ru", { payment_history: HISTORY });
    const text = section.textContent ?? "";
    expect(text).toContain("Вы оплатили вовремя 67% долгов с наступившим сроком.");
    expect(text).toContain("Ваша самая долгая просрочка: 9 дней.");
    expect(text).toMatch(/Из 300\s000 сум с наступившим сроком 200\s000 сум оплачено к обещанной дате\./);
    cleanup();
    const two = await open("ru", { payment_history: { ...HISTORY, longest_delay_days: 2 } });
    expect(two.textContent).toContain("Ваша самая долгая просрочка: 2 дня.");
  });

  it.each(LANGUAGES)("says there was no delay instead of naming zero days, in %s", async (language) => {
    const section = await open(language, {
      payment_history: { on_time_percent: 100, on_time_amount: 300000, due_amount: 300000, longest_delay_days: 0 },
    });
    const text = section.textContent ?? "";
    expect(text).toContain(catalogs[language]["my.history.noDelay"]);
    expect(text).toContain("100%");
    for (const words of wording(language, "my.history.longestDelay")) {
      expect(text, words).not.toContain(words);
    }
  });

  it.each(LANGUAGES)("says that nothing has fallen due yet when there is no indicator, in %s", async (language) => {
    const section = await open(language, { payment_history: null });
    // The title, that sentence and where the figures would come from: no figure and no other sentence.
    const messages = catalogs[language];
    expect(Array.from(section.children, (part) => part.textContent)).toEqual([
      messages["my.history.title"],
      messages["my.history.none"],
      messages["my.history.source"],
    ]);
    expect(section.textContent).not.toContain("%");
    // A server that sends no such field at all is read the same way.
    cleanup();
    const body: Record<string, unknown> = accountBody();
    delete body["payment_history"];
    const server = fakeServer(() => ok(body));
    const account = await createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "customer-session" } })
      .account(LINK_ID)
      .read();
    expect(account.paymentHistory).toBeNull();
  });

  it("addresses the customer about themselves in both languages, with the same keys in each", () => {
    const keys = (language: Language) =>
      Object.keys(catalogs[language])
        .filter((key) => key.startsWith("my.history."))
        .sort();
    expect(keys("uz")).toEqual([
      "my.history.amounts",
      "my.history.longestDelay",
      "my.history.noDelay",
      "my.history.none",
      "my.history.onTime",
      "my.history.source",
      "my.history.title",
    ]);
    expect(keys("ru")).toEqual(keys("uz"));
    // Not the staff's wording about somebody else ("the customer paid"), and no judgement of the person.
    for (const language of LANGUAGES) {
      const words = keys(language)
        .flatMap((key) => wording(language, key))
        .join(" ")
        .toLowerCase();
      expect(words).not.toMatch(/mijoz|клиент|ishonch|надёжн|надежн|yomon|плох|reyting|рейтинг/);
    }
  });
});

describe("what stays the shop's own on the customer's page (REQ-045)", () => {
  /** An entry as a careless server might send it: with the seller's note and who wrote it. */
  const LEAKY = accountEntryBody({
    note: "ichki izoh: kechikib to'laydi",
    author_id: "99999999-9999-4999-8999-999999999999",
    author_name: "Sotuvchi Salim",
    author: { id: "99999999-9999-4999-8999-999999999999", name: "Sotuvchi Salim" },
  });

  it.each(LANGUAGES)("shows neither the note nor the author in %s, even when the server sends them", async (language) => {
    await open(language, { payment_history: HISTORY, entries: [LEAKY] });
    await waitFor(() => expect(within(document.body).getAllByRole("listitem").length).toBeGreaterThan(0));
    const page = document.body.textContent ?? "";
    expect(page).not.toContain("ichki izoh");
    expect(page).not.toContain("Salim");
    expect(page).not.toContain("999999");
  });

  it("does not even read them from the account the server answers with", async () => {
    const server = fakeServer(() => ok(accountBody({ payment_history: HISTORY, entries: [LEAKY] })));
    const account = await createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "customer-session" } })
      .account(LINK_ID)
      .read();
    const read = JSON.stringify(account);
    expect(read).not.toContain("ichki izoh");
    expect(read).not.toContain("Salim");
    expect(read).not.toContain("99999999");
    expect(account.entries.flatMap((entry) => Object.keys(entry)).some((key) => /note|author/i.test(key))).toBe(false);
    // The indicator, which is the customer's to see, is read.
    expect(account.paymentHistory).toEqual({ onTimePercent: 67, onTimeAmount: 200000, dueAmount: 300000, longestDelayDays: 9 });
  });

  it("has no code on the customer's side that mentions a note or an author", () => {
    const mentions = (source: string) => /\bnote\b|\.note\b|author/i.test(source.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, ""));
    const directory = import.meta.dirname;
    const sources = readdirSync(directory).filter((name) => /\.tsx?$/.test(name) && !name.includes(".test."));
    expect(sources).toContain("AccountScreen.tsx");
    expect(sources).toContain("CustomerArea.tsx");
    expect(sources).toContain("MyPaymentHistory.tsx");
    for (const name of sources) {
      expect(mentions(readFileSync(resolve(directory, name), "utf8")), name).toBe(false);
    }
    // The check itself: it does find the staff page's ways of showing them, and is not fooled by a comment.
    expect(mentions("<p>{entry.note}</p>")).toBe(true);
    expect(mentions("const by = entry.authorId;")).toBe(true);
    expect(mentions("// the seller's note and the author are not shown\nconst x = 1;")).toBe(false);
  });
});
