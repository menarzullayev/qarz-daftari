import { describe, expect, it } from "vitest";

import {
  creditSettingsBody,
  customerBody,
  fakeServer,
  ok,
  refusal,
  remindersBody,
  SHOP_BASE,
  SHOP_ID,
  subscriptionBody,
} from "../testing/fakeServer";
import { ApiError, BAD_RESPONSE, cardTag, createApi, groupedCard } from "./api";

const KEY = "0123456789abcdef";
const CUSTOMER_ID = "11111111-1111-4111-8111-111111111111";

const shopOf = (fetch: Parameters<typeof createApi>[0]["fetch"]) =>
  createApi({ fetch, auth: { kind: "bearer", token: "session-token" } }).shop(SHOP_ID);

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) {
      return error;
    }
    throw error;
  }
  throw new Error("the call did not fail");
}

describe("reminders", () => {
  it("reads the settings with the hours and the wordings as the server sent them", async () => {
    const server = fakeServer(() => ok(remindersBody({ on: true, hour: 9, template: 2, sms_on: true })));
    const settings = await shopOf(server.fetch).readReminders();
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/reminders` });
    expect(settings).toMatchObject({ on: true, hour: 9, template: 2, smsOn: true, hours: { first: 8, last: 20 } });
    expect(settings.templates.map((template) => template.id)).toEqual([1, 2, 3]);
    expect(settings.templates[0]?.dueToday).toEqual({
      uz: "1: «{shop}»: {name}, bugun {amount} to'lash kuni.",
      ru: "1: «{shop}»: {name}, сегодня срок оплаты {amount}.",
    });
    expect(Object.keys(settings.templates[2]?.overdue ?? {})).toEqual(["uz", "ru"]);
  });

  it.each([
    [{ hours: [20, 8] }],
    [{ hours: [8] }],
    [{ hours: [8, 20.5] }],
    [{ hour: "10" }],
    [{ on: 1 }],
    [{ templates: [{ id: 1, due_today: { uz: 5 }, overdue: {} }] }],
  ])("refuses settings that are not the contract: %j", async (broken) => {
    const server = fakeServer(() => ok(remindersBody(broken)));
    expect((await failure(shopOf(server.fetch).readReminders())).code).toBe(BAD_RESPONSE);
  });

  it("patches only what was given, with the key", async () => {
    const server = fakeServer(() => ok(remindersBody()));
    const shop = shopOf(server.fetch);
    await shop.updateReminders({ on: true }, KEY);
    await shop.updateReminders({ hour: 8, template: 3, smsOn: false }, KEY);
    expect(server.sent[0]).toMatchObject({ method: "PATCH", path: `${SHOP_BASE}/reminders` });
    expect(server.sent[0]?.body).toEqual({ on: true });
    expect(server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);
    expect(server.sent[1]?.body).toEqual({ hour: 8, template: 3, sms_on: false });
  });

  it("never sends an hour or a wording that is not a whole number", () => {
    const server = fakeServer(() => ok(remindersBody()));
    expect(() => shopOf(server.fetch).updateReminders({ hour: 9.5 }, KEY)).toThrow(RangeError);
    expect(() => shopOf(server.fetch).updateReminders({ template: Number.NaN }, KEY)).toThrow(RangeError);
    expect(server.sent).toHaveLength(0);
  });

  it("sends one reminder for one customer and reads how it went out", async () => {
    const server = fakeServer(() => ok({ sent: true, channel: "sms", amount: 45000 }, 201));
    const sent = await shopOf(server.fetch).sendReminder(CUSTOMER_ID, KEY);
    expect(server.sent[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/reminders/manual` });
    expect(server.sent[0]?.body).toEqual({ customer_id: CUSTOMER_ID });
    expect(server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);
    expect(sent).toEqual({ channel: "sms", amount: 45000 });
  });

  it("lists who cannot be reached, with or without a phone", async () => {
    const server = fakeServer(() =>
      ok({
        items: [
          { customer_id: CUSTOMER_ID, display_name: "Ali Valiyev", phone: "+998901234567", amount: 45000 },
          { customer_id: "x", display_name: "Vali", phone: null, amount: 100 },
        ],
      }),
    );
    const list = await shopOf(server.fetch).listUnreachable();
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/reminders/unreachable` });
    expect(list).toEqual([
      { customerId: CUSTOMER_ID, displayName: "Ali Valiyev", phone: "+998901234567", amount: 45000 },
      { customerId: "x", displayName: "Vali", phone: null, amount: 100 },
    ]);
  });

  it("refuses an unreachable amount that is a fraction", async () => {
    const server = fakeServer(() => ok({ items: [{ customer_id: "x", display_name: "Vali", phone: null, amount: 100.5 }] }));
    expect((await failure(shopOf(server.fetch).listUnreachable())).code).toBe(BAD_RESPONSE);
  });
});

describe("credit limits", () => {
  it("reads the shop's rules and the bounds of a limit", async () => {
    const server = fakeServer(() => ok(creditSettingsBody({ default_credit_limit: 500000, sellers_may_exceed: true })));
    expect(await shopOf(server.fetch).readCreditSettings()).toEqual({
      defaultLimit: 500000,
      sellersMayExceed: true,
      bounds: { min: 1000, max: 10_000_000_000 },
    });
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/credit-settings` });
  });

  it.each([[{ default_credit_limit: 500000.5 }], [{ sellers_may_exceed: "no" }], [{ limit_bounds: [5, 1] }], [{ limit_bounds: null }]])(
    "refuses rules that are not the contract: %j",
    async (broken) => {
      const server = fakeServer(() => ok(creditSettingsBody(broken)));
      expect((await failure(shopOf(server.fetch).readCreditSettings())).code).toBe(BAD_RESPONSE);
    },
  );

  it("sets a default with a number, removes it with null, and leaves it out when it is not given", async () => {
    const server = fakeServer(() => ok(creditSettingsBody()));
    const shop = shopOf(server.fetch);
    await shop.updateCreditSettings({ defaultLimit: 500000 }, KEY);
    await shop.updateCreditSettings({ defaultLimit: null }, KEY);
    await shop.updateCreditSettings({ sellersMayExceed: true }, KEY);
    expect(server.sent.map((sent) => sent.body)).toEqual([
      { default_credit_limit: 500000 },
      { default_credit_limit: null },
      { sellers_may_exceed: true },
    ]);
    expect(server.sent.every((sent) => sent.method === "PATCH" && sent.path === `${SHOP_BASE}/credit-settings`)).toBe(true);
    expect(server.sent.every((sent) => sent.headers["Idempotency-Key"] === KEY)).toBe(true);
  });

  it("sets a customer's own limit with a number, removes it with null, and otherwise leaves it out", async () => {
    const server = fakeServer(() => ok(customerBody({ credit_limit: 300000 })));
    const shop = shopOf(server.fetch);
    const updated = await shop.updateCustomer(CUSTOMER_ID, { creditLimit: 300000 }, KEY);
    await shop.updateCustomer(CUSTOMER_ID, { creditLimit: null }, KEY);
    await shop.updateCustomer(CUSTOMER_ID, { remindersOff: true }, KEY);
    expect(updated.creditLimit).toBe(300000);
    expect(server.sent.map((sent) => sent.body)).toEqual([{ credit_limit: 300000 }, { credit_limit: null }, { reminders_off: true }]);
  });

  it("never sends a limit that is not a whole number", () => {
    const server = fakeServer(() => ok({}));
    const shop = shopOf(server.fetch);
    expect(() => shop.updateCustomer(CUSTOMER_ID, { creditLimit: 300000.5 }, KEY)).toThrow(RangeError);
    expect(() => shop.updateCreditSettings({ defaultLimit: 0.1 }, KEY)).toThrow(RangeError);
    expect(server.sent).toHaveLength(0);
  });

  it("reads a customer from a server that does not send limits yet as a customer without one", async () => {
    const server = fakeServer(() => ok({ items: [{ ...customerBody(), credit_limit: undefined }], next_cursor: null }));
    const page = await shopOf(server.fetch).listCustomers({});
    expect(page.items[0]?.creditLimit).toBeNull();
  });

  const entryAnswer = (extra: Record<string, unknown>) =>
    ok({ entry: { id: "e", kind: "credit", amount: 50000, promised_date: null }, customer: customerBody({ balance: 170000 }), ...extra }, 201);

  it("reads the warning of a sale saved above the limit, and none when there is none", async () => {
    const warned = fakeServer(() => entryAnswer({ limit_warning: { limit: 150000, balance: 170000 } }));
    const plain = fakeServer(() => entryAnswer({}));
    const entry = { kind: "credit", amount: 50000, note: null, promisedDate: null } as const;
    expect((await shopOf(warned.fetch).recordEntry(CUSTOMER_ID, entry, KEY)).limitWarning).toEqual({ limit: 150000, balance: 170000 });
    expect((await shopOf(plain.fetch).recordEntry(CUSTOMER_ID, entry, KEY)).limitWarning).toBeNull();
  });

  it("refuses a warning whose figures are not whole so'm", async () => {
    const server = fakeServer(() => entryAnswer({ limit_warning: { limit: "150000", balance: 170000 } }));
    const entry = { kind: "credit", amount: 50000, note: null, promisedDate: null } as const;
    expect((await failure(shopOf(server.fetch).recordEntry(CUSTOMER_ID, entry, KEY))).code).toBe(BAD_RESPONSE);
  });

  it("carries the figures of a LIMIT_REACHED refusal as the server wrote them", async () => {
    const server = fakeServer(() => refusal(409, "LIMIT_REACHED", "Limitdan oshadi.", { limit: "150000", balance: "170000" }));
    const entry = { kind: "credit", amount: 50000, note: null, promisedDate: null } as const;
    const error = await failure(shopOf(server.fetch).recordEntry(CUSTOMER_ID, entry, KEY));
    expect(error).toMatchObject({ status: 409, code: "LIMIT_REACHED", serverMessage: "Limitdan oshadi." });
    expect(error.fields).toEqual({ limit: "150000", balance: "170000" });
  });
});

describe("subscription", () => {
  it("reads the state, the end of the period, the price and the card", async () => {
    const server = fakeServer(() => ok(subscriptionBody()));
    expect(await shopOf(server.fetch).readSubscription()).toEqual({
      state: "trial",
      endsOn: "2026-10-26",
      daysLeft: 20,
      priceUzs: 100000,
      cardNumber: "8600123456789012",
      cards: [{ number: "8600123456789012", label: "Humo · Anorbank" }],
    });
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/subscription` });
  });

  it("reads a limited shop: no period, no days, and no card yet", async () => {
    const server = fakeServer(() => ok(subscriptionBody({ state: "limited", ends_on: null, days_left: null, card_number: null, cards: [] })));
    expect(await shopOf(server.fetch).readSubscription()).toMatchObject({
      state: "limited",
      endsOn: null,
      daysLeft: null,
      cardNumber: null,
      cards: [],
    });
  });

  it("reads the cards in the server's order: the first is the primary", async () => {
    const cards = [
      { number: "5614681234567890", label: "Uzcard · Kapitalbank" },
      { number: "8600123456789012", label: "Humo · Anorbank" },
    ];
    const server = fakeServer(() => ok(subscriptionBody({ card_number: cards[0]?.number, cards })));
    expect((await shopOf(server.fetch).readSubscription()).cards).toEqual(cards);
  });

  it("reads no cards from a server that names none, and refuses a card that is not a number and a label", async () => {
    const old = fakeServer(() => ok({ ...subscriptionBody(), cards: undefined }));
    expect((await shopOf(old.fetch).readSubscription()).cards).toEqual([]);
    for (const cards of [[{ number: "8600123456789012" }], [{ number: 8600123456789012, label: "Humo" }], ["8600123456789012"], "8600123456789012"]) {
      const server = fakeServer(() => ok(subscriptionBody({ cards })));
      expect((await failure(shopOf(server.fetch).readSubscription())).code).toBe(BAD_RESPONSE);
    }
  });

  it("writes a card number in groups of four and names a card by its label and last four digits", () => {
    expect(groupedCard("8600123456789012")).toBe("8600 1234 5678 9012");
    expect(groupedCard("")).toBe("");
    expect(cardTag({ number: "8600123456789012", label: "Humo · Anorbank" })).toBe("Humo · Anorbank ··9012");
    expect(cardTag({ number: "8600123456789012", label: "Humo" })).not.toContain("8600");
  });

  it("refuses a price that is not whole so'm", async () => {
    const server = fakeServer(() => ok(subscriptionBody({ price_uzs: 99999.5 })));
    expect((await failure(shopOf(server.fetch).readSubscription())).code).toBe(BAD_RESPONSE);
  });
});

describe("refusals are reported to the application", () => {
  it("tells of every refusal, with its code, and of nothing that succeeded or never arrived", async () => {
    const seen: string[] = [];
    let turn = 0;
    const server = fakeServer(() => {
      turn += 1;
      if (turn === 1) {
        return refusal(402, "SUBSCRIPTION_LIMITED", "Obuna tugagan.");
      }
      if (turn === 2) {
        return refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.");
      }
      return turn === 3 ? "offline" : ok(subscriptionBody());
    });
    const shop = createApi({
      fetch: server.fetch,
      auth: { kind: "bearer", token: "session-token" },
      onRefusal: (error) => seen.push(`${error.status} ${error.code}`),
    }).shop(SHOP_ID);
    await failure(shop.readSubscription());
    await failure(shop.readSubscription());
    await failure(shop.readSubscription());
    await shop.readSubscription();
    expect(seen).toEqual(["402 SUBSCRIPTION_LIMITED", "403 SHOP_SUSPENDED"]);
  });
});
