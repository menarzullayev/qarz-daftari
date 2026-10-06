// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  creditSettingsBody,
  CUSTOMER_ID,
  customerBody,
  detailBody,
  fakeServer,
  FIVE_ITEMS,
  ok,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
} from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { EntryKind } from "../api";
import type { Role } from "../navigation";
import { EntryScreen } from "./EntryScreen";

afterEach(cleanup);

type Options = {
  /** The customer's own limit. */
  own?: number | null;
  credit?: () => Reply;
  onWrite?: (sent: Sent) => Reply;
};

const saved = (amount: number, extra: Record<string, unknown> = {}) =>
  ok(
    {
      entry: { id: "e-new", kind: "credit", amount, promised_date: "2026-11-05" },
      customer: customerBody({ balance: 120000 + amount }),
      ...extra,
    },
    201,
  );

/** One customer who owes 120 000, five goods, and the shop's credit rules. */
function shop(options: Options = {}) {
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return options.onWrite ? options.onWrite(sent) : saved(50000);
    }
    if (sent.path.endsWith("/credit-settings")) {
      return options.credit ? options.credit() : ok(creditSettingsBody());
    }
    if (sent.path === `${SHOP_BASE}/catalog`) {
      return ok({ items: FIVE_ITEMS, next_cursor: null });
    }
    return ok(detailBody({ credit_limit: options.own ?? null }));
  });
}

const defaultLimit = (limit: number | null, sellersMayExceed = false) => () =>
  ok(creditSettingsBody({ default_credit_limit: limit, sellers_may_exceed: sellersMayExceed }));

async function openForm(server: ReturnType<typeof fakeServer>, role: Role = "seller", kind: EntryKind = "credit") {
  renderScreen(<EntryScreen customerId={CUSTOMER_ID} kind={kind} />, { fetch: server.fetch, role });
  const amount = await screen.findByLabelText<HTMLInputElement>("Summa, so'm");
  if (kind === "credit") {
    await waitFor(() => expect(server.sent.some((sent) => sent.path.endsWith("/credit-settings"))).toBe(true));
    // Let the answer to the credit settings reach the form.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
    });
  }
  return amount;
}

const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });
const submit = () => screen.getByRole<HTMLButtonElement>("button", { name: "Nasiyani yozish" });
const warning = () => screen.queryByRole("note");
const entries = (server: ReturnType<typeof fakeServer>) => server.writes().filter((sent) => sent.path.endsWith("/entries"));

const OVER = (balance: string, limit: string) => `Bu savdo bilan qarz ${balance} so'm bo'ladi va ${limit} so'm limitdan oshadi.`;
const ALLOWED = "Yozish mumkin; limit oshgani sizga ko'rsatiladi.";
const STOPPED = "Limitdan oshadigan nasiyani faqat menejer yoki do'kon egasi yoza oladi.";

describe("the warning before a credit sale is saved (REQ-044)", () => {
  it("is silent below the limit and exactly at it, and speaks one so'm above", async () => {
    const server = shop({ own: 150000 });
    const amount = await openForm(server);
    type(amount, "29 999");
    expect(warning()).toBeNull();
    type(amount, "30 000"); // 120 000 + 30 000 is the limit itself: within it
    expect(warning()).toBeNull();
    type(amount, "30 001");
    expect(warning()?.textContent).toContain(OVER("150\u00a0001", "150\u00a0000"));
    type(amount, "30 000");
    expect(warning()).toBeNull();
  });

  it("uses the shop's default when the customer has no limit of their own", async () => {
    const amount = await openForm(shop({ credit: defaultLimit(130000) }));
    type(amount, "10000");
    expect(warning()).toBeNull();
    type(amount, "10100");
    expect(warning()?.textContent).toContain(OVER("130\u00a0100", "130\u00a0000"));
  });

  it("uses the customer's own limit rather than the shop's default, in both directions", async () => {
    const higher = await openForm(shop({ own: 500000, credit: defaultLimit(130000) }));
    type(higher, "100000");
    expect(warning()).toBeNull();
    cleanup();

    const lower = await openForm(shop({ own: 125000, credit: defaultLimit(900000) }));
    type(lower, "6000");
    expect(warning()?.textContent).toContain(OVER("126\u00a0000", "125\u00a0000"));
  });

  it("says nothing when no limit applies, or while the amount cannot be read", async () => {
    const none = await openForm(shop());
    type(none, "100000000");
    expect(warning()).toBeNull();
    cleanup();

    const unread = await openForm(shop({ own: 150000 }));
    type(unread, "ko'p");
    expect(warning()).toBeNull();
    type(unread, "");
    expect(warning()).toBeNull();
  });

  it.each([
    ["seller", false, STOPPED],
    ["seller", true, ALLOWED],
    ["manager", false, ALLOWED],
    ["owner", false, ALLOWED],
  ] as const)("tells a %s (sellers may exceed: %s) what will happen", async (role, sellersMayExceed, text) => {
    const amount = await openForm(shop({ own: 150000, credit: defaultLimit(null, sellersMayExceed) }), role);
    type(amount, "50000");
    expect(Array.from(warning()?.querySelectorAll("p") ?? [], (node) => node.textContent)).toEqual([
      OVER("170\u00a0000", "150\u00a0000"),
      text,
    ]);
  });

  it("warns and does not block: the server decides, also for a seller the shop stops", async () => {
    const server = shop({
      own: 150000,
      onWrite: () => refusal(409, "LIMIT_REACHED", "Bu savdo mijozning nasiya limitidan oshadi.", { limit: "150000", balance: "170000" }),
    });
    const amount = await openForm(server, "seller");
    type(amount, "50000");
    expect(submit().disabled).toBe(false);
    fireEvent.click(submit());
    await screen.findByRole("alert");
    expect(entries(server)).toHaveLength(1);
    expect(entries(server)[0]?.body).toEqual({ kind: "credit", amount: 50000 });
  });

  it("sums the goods of an itemized sale: silent at the limit, speaking above it", async () => {
    // Guruch costs 25 000 and Non 4 000: 120 000 + 25 000 + 4 000 = 149 000, then 153 000.
    const server = shop({ own: 149000 });
    await openForm(server);
    fireEvent.click(screen.getByRole("button", { name: "Tovarlar bilan yozish" }));
    const picks = await screen.findByRole("list", { name: "Katalogdagi tovarlar" });
    const pick = (name: string) => fireEvent.click(within(picks).getByRole("button", { name: new RegExp(`^${name}`) }));
    pick("Guruch");
    pick("Non");
    expect(screen.queryByLabelText("Summa, so'm")).toBeNull();
    expect(warning()).toBeNull();
    pick("Non");
    expect(warning()?.textContent).toContain(OVER("153\u00a0000", "149\u00a0000"));
    fireEvent.change(screen.getByLabelText("Non: miqdor"), { target: { value: "1" } });
    expect(warning()).toBeNull();
  });

  it("asks for no credit rules on a payment and warns of nothing there", async () => {
    const server = shop({ own: 1000 });
    const amount = await openForm(server, "seller", "payment");
    type(amount, "50000");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(warning()).toBeNull();
    expect(server.sent.some((sent) => sent.path.endsWith("/credit-settings"))).toBe(false);
  });

  it("still warns from the customer's own limit when the shop's rules cannot be read", async () => {
    const seller = await openForm(shop({ own: 150000, credit: () => refusal(500, "ERROR", "Xatolik.") }), "seller");
    type(seller, "50000");
    // Whether a seller may proceed is not known, so nothing is claimed about it.
    expect(Array.from(warning()?.querySelectorAll("p") ?? [], (node) => node.textContent)).toEqual([
      OVER("170\u00a0000", "150\u00a0000"),
    ]);
    expect(screen.queryByRole("alert")).toBeNull();
    cleanup();

    const manager = await openForm(shop({ own: 150000, credit: () => "offline" }), "manager");
    type(manager, "50000");
    expect(warning()?.textContent).toContain(ALLOWED);
  });
});

describe("what the server says about the limit after saving", () => {
  it("shows the warning of a sale saved above the limit, with both figures", async () => {
    const server = shop({ own: 150000, onWrite: () => saved(50000, { limit_warning: { limit: 150000, balance: 170000 } }) });
    type(await openForm(server, "manager"), "50000");
    fireEvent.click(submit());
    const done = await screen.findByRole("status");
    expect(done.textContent).toContain("Ali Valiyev: 50\u00a0000 so'm nasiya yozildi.");
    expect(done.textContent).toContain("Diqqat: qarz 170\u00a0000 so'm bo'ldi, bu 150\u00a0000 so'm limitdan ko'p.");
  });

  it("shows the server's figures, not the form's own guess", async () => {
    // The form knew no limit; the server, which is the authority, met one.
    const server = shop({ onWrite: () => saved(50000, { limit_warning: { limit: 160000, balance: 171000 } }) });
    type(await openForm(server, "owner"), "50000");
    expect(warning()).toBeNull();
    fireEvent.click(submit());
    expect((await screen.findByRole("status")).textContent).toContain(
      "Diqqat: qarz 171\u00a0000 so'm bo'ldi, bu 160\u00a0000 so'm limitdan ko'p.",
    );
  });

  it("says nothing of a limit after a sale the server saved without a warning", async () => {
    const server = shop({ own: 500000 });
    type(await openForm(server), "50000");
    fireEvent.click(submit());
    expect((await screen.findByRole("status")).textContent).not.toContain("limit");
  });

  it("shows a LIMIT_REACHED refusal with the server's message and its two figures, and keeps the form", async () => {
    const server = shop({
      own: 150000,
      onWrite: () =>
        refusal(409, "LIMIT_REACHED", "Bu savdo mijozning nasiya limitidan oshadi. Menejer yoki do'kon egasi yoza oladi.", {
          limit: "150000",
          balance: "170000",
        }),
    });
    const amount = await openForm(server, "seller");
    type(amount, "50000");
    fireEvent.click(submit());
    const alert = await screen.findByRole("alert");
    expect(Array.from(alert.querySelectorAll("p"), (node) => node.textContent)).toEqual([
      "Bu savdo mijozning nasiya limitidan oshadi. Menejer yoki do'kon egasi yoza oladi.",
      "Limit: 150\u00a0000 so'm. Bu savdo bilan qarz: 170\u00a0000 so'm.",
    ]);
    expect(screen.queryByRole("status")).toBeNull();
    expect(amount.value).toBe("50000");
  });

  it("shows only the message when the refusal carries no usable figures", async () => {
    const server = shop({ onWrite: () => refusal(409, "LIMIT_REACHED", "Limitdan oshadi.", { limit: "ko'p" }) });
    type(await openForm(server), "50000");
    fireEvent.click(submit());
    const alert = await screen.findByRole("alert");
    expect(Array.from(alert.querySelectorAll("p"), (node) => node.textContent)).toEqual(["Limitdan oshadi."]);
  });

  it("shows the figures for that refusal only", async () => {
    const server = shop({ onWrite: () => refusal(409, "CUSTOMER_ARCHIVED", "Mijoz arxivda.", { limit: "150000", balance: "170000" }) });
    type(await openForm(server), "50000");
    fireEvent.click(submit());
    expect((await screen.findByRole("alert")).textContent).toBe("Mijoz arxivda.");
  });
});

describe("a shop that is limited or suspended, on the entry form (REQ-057)", () => {
  it.each([
    [402, "SUBSCRIPTION_LIMITED", "Obuna tugagan: yangi nasiya yozilmaydi. To'lov qabul qilish va ko'rish ishlayveradi."],
    [403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan. Faqat do'kon egasi ma'lumotlarni ko'ra oladi va eksport qila oladi."],
  ])("shows the server's refusal %i %s where it came back", async (status, code, message) => {
    const server = shop({ onWrite: () => refusal(status, code, message) });
    type(await openForm(server), "50000");
    fireEvent.click(submit());
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    expect(screen.queryByRole("status")).toBeNull();
    expect(entries(server)).toHaveLength(1);
  });
});
