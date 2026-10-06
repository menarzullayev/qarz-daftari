// @vitest-environment jsdom
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { I18nProvider } from "../../i18n/I18nProvider";
import { catalogs } from "../../i18n/catalog";
import { LANGUAGES, type Language } from "../../i18n/types";
import { accountBody, fakeServer, LINK_ID, ok } from "../../testing/fakeServer";
import { createApi } from "../api";
import { AccountScreen } from "./AccountScreen";

afterEach(cleanup);

/**
 * The payment history indicator is the shop's own view of a customer (REQ-045). It must never reach the
 * customer's own page, not even if a server were to send it there by mistake.
 */
const HISTORY = { on_time_percent: 67, on_time_amount: 200000, due_amount: 300000, longest_delay_days: 9 };

/** The words every history message starts with, taken from the catalogs so a new wording is covered. */
function historyWords(language: Language): string[] {
  return Object.entries(catalogs[language])
    .filter(([key]) => key.startsWith("customer.history."))
    .flatMap(([, message]) => (typeof message === "string" ? [message] : Object.values(message)))
    .map((template) => (template.split("{")[0] ?? "").trim())
    .filter((start) => start.length >= 8);
}

describe("the customer's own page and the payment history (REQ-045)", () => {
  it("knows the wordings it guards against", () => {
    expect(historyWords("uz")).toContain("O'z vaqtida to'langan:");
    expect(historyWords("uz")).toContain("To'lov tarixi");
    expect(historyWords("uz").length).toBeGreaterThanOrEqual(5);
    expect(historyWords("ru")).toContain("Оплачено вовремя:");
    expect(historyWords("ru").length).toBeGreaterThanOrEqual(5);
  });

  it.each(LANGUAGES)("shows none of it in %s, even when the server sends it", async (language) => {
    const server = fakeServer(() => ok(accountBody({ payment_history: HISTORY })));
    const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "customer-session" } }).account(LINK_ID);
    render(
      <I18nProvider initialLanguage={language}>
        <AccountScreen api={api} back={<a href="#/my">back</a>} />
      </I18nProvider>,
    );
    await screen.findByRole("heading", { level: 2, name: "Baraka savdo" });
    const page = document.body.textContent ?? "";
    for (const words of historyWords(language)) {
      expect(page, words).not.toContain(words);
    }
    expect(page).not.toContain("67");
    expect(page).not.toMatch(/200\s000|300\s000/);
  });

  it("does not even read it from the account the server answers with", async () => {
    const server = fakeServer(() => ok(accountBody({ payment_history: HISTORY })));
    const account = await createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "customer-session" } })
      .account(LINK_ID)
      .read();
    expect(JSON.stringify(account)).not.toContain("67");
    expect(Object.keys(account).some((key) => /history/i.test(key))).toBe(false);
  });

  it("has no code on the customer's side that mentions it", () => {
    const mentions = (source: string) => /PaymentHistory|paymentHistory|payment_history|customer\.history\./.test(source);
    const directory = import.meta.dirname;
    const sources = readdirSync(directory).filter((name) => /\.tsx?$/.test(name) && !name.includes(".test."));
    expect(sources).toContain("AccountScreen.tsx");
    expect(sources).toContain("CustomerArea.tsx");
    for (const name of sources) {
      expect(mentions(readFileSync(resolve(directory, name), "utf8")), name).toBe(false);
    }
    // The check itself: it does find the staff page's component and a catalog key.
    expect(mentions('import { PaymentHistoryNote } from "../workspace/PaymentHistoryNote";')).toBe(true);
    expect(mentions('t("customer.history.onTime", { percent })')).toBe(true);
  });
});
