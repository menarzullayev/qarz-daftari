// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { I18nProvider } from "../../i18n/I18nProvider";
import type { Language } from "../../i18n/types";
import { accountBody, fakeServer, LINK_ID, NOON, ok } from "../../testing/fakeServer";
import { createApi } from "../api";
import { AccountScreen } from "./AccountScreen";
import CustomerArea from "./CustomerArea";

/**
 * The customer's own pages where a shop holds their advance: they are told "you are in credit by X" in
 * words, with the amount itself and no minus sign. A customer who owes reads what they always read.
 */

afterEach(cleanup);

const NBSP = "\u00a0";
const markup = (node: Element | null) => (node?.innerHTML ?? "").replace(/&nbsp;/g, NBSP);

async function account(body: Record<string, unknown>, language: Language = "uz") {
  const server = fakeServer(() => ok(accountBody(body)));
  const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "customer-session" } }).account(LINK_ID);
  render(
    <I18nProvider initialLanguage={language}>
      <AccountScreen api={api} back={<a href="#/my">back</a>} />
    </I18nProvider>,
  );
  await screen.findByRole("heading", { level: 2, name: "Baraka savdo" });
  return document.querySelector(".balance--large");
}

describe("the customer's own account", () => {
  it.each([
    ["uz", `Siz haqdorsiz: 15${NBSP}000 so'm`],
    ["ru", `У вас предоплата: 15${NBSP}000 сум`],
  ] as const)("says in %s that the shop holds their advance", async (language, text) => {
    const line = await account({ balance: -15000 }, language);
    expect(line?.textContent).toBe(text);
    expect(document.body.textContent).not.toMatch(/[-−]\s?15/);
    expect(document.body.textContent).not.toMatch(/Qarzingiz|Ваш долг/);
  });

  it("offers nothing to pay: a notice of payment is for a debt", async () => {
    await account({ balance: -15000 });
    expect(screen.queryByRole("button", { name: /To'lov qildim/ })).toBeNull();
  });

  it("says a so'm debt and a dollar advance as two parts, the debt first", async () => {
    const line = await account({ balance: 120000, usd: { balance: -500, overdue: { amount: 0, due_today: 0 } } });
    expect(line?.textContent).toBe(`Qarzingiz: 120${NBSP}000 so'm · Siz haqdorsiz: 5.00${NBSP}$`);
  });

  it("says a so'm advance and a dollar debt as two parts, the debt first", async () => {
    const line = await account({ balance: -15000, usd: { balance: 1250, overdue: { amount: 0, due_today: 0 } } });
    expect(line?.textContent).toBe(`Qarzingiz: 12.50${NBSP}$ · Siz haqdorsiz: 15${NBSP}000 so'm`);
    expect(line?.textContent).not.toMatch(/Qarzingiz: Siz|[-−]/);
  });

  it("shows a customer who owes exactly the line they always had", async () => {
    expect(markup(await account({}))).toBe(`<span>Qarzingiz:</span> <strong>120${NBSP}000 so'm</strong>`);
  });
});

describe("the customer's list of shops", () => {
  const OTHER = "99999999-9999-4999-8999-999999999999";
  const row = (linkId: string, shop: string, balance: number) => ({ link_id: linkId, shop_name: shop, display_name: "Ali Valiyev", balance });

  it("says which shop holds their advance and what they owe the other, each in its own words", async () => {
    const server = fakeServer(() => ok({ items: [row(LINK_ID, "Baraka savdo", -15000), row(OTHER, "Oltin don", 30000)] }));
    const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "customer-session" } });
    render(
      <I18nProvider initialLanguage="uz">
        <CustomerArea api={api} staffHome={false} now={() => NOON} />
      </I18nProvider>,
    );
    await screen.findByText("Oltin don");
    const links = within(screen.getByRole("main")).getAllByRole("link").map((link) => link.textContent);
    expect(links).toEqual([`Baraka savdoSiz haqdorsiz: 15${NBSP}000 so'm`, `Oltin don30${NBSP}000 so'm`]);
  });
});
