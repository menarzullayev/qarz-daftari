// @vitest-environment jsdom
import { cleanup, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  CUSTOMER_ID,
  customerBody,
  detailBody,
  entryBody,
  fakeServer,
  linkBody,
  noticeBody,
  ok,
  openDateRequestBody,
  openDisputeBody,
  openNoticeBody,
  remindersBody,
  SHOP_BASE,
} from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { Role } from "../navigation";
import { MIN_ROLE } from "../permissions";
import { mayAddGoods } from "./AddGoodsScreen";
import { CustomerScreen } from "./CustomerScreen";
import { CustomersScreen } from "./CustomersScreen";
import DateRequestsScreen from "./DateRequestsScreen";
import { DisputesScreen } from "./DisputesScreen";
import { EntryScreen } from "./EntryScreen";
import { LinkSection } from "./LinkSection";
import { NewCustomerScreen } from "./NewCustomerScreen";
import PaymentNoticesScreen from "./PaymentNoticesScreen";
import { RemindersScreen } from "./RemindersScreen";
import { WaitingScreen } from "./WaitingScreen";

/**
 * What each screen offers follows the permissions the server named for the member, not the role: a
 * seller the owner denied something is not offered it, a seller the owner granted something is, and
 * while the server keeps no permissions per member (`permissions` absent) the role decides as before.
 */

beforeEach(() => {
  window.location.hash = "";
});
afterEach(cleanup);

/** Everything a seller holds by role, as the server would name it. */
const SELLER_HOLDS = (Object.keys(MIN_ROLE) as (keyof typeof MIN_ROLE)[]).filter((key) => MIN_ROLE[key] === "seller");
const without = (...denied: string[]) => SELLER_HOLDS.filter((key) => !denied.includes(key));

const NOT_FOUND = "Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.";
const link = (name: string) => screen.queryByRole("link", { name });
const button = (name: string) => screen.queryByRole("button", { name });
const loaded = () => waitFor(() => expect(screen.queryByText("Yuklanmoqda…")).toBeNull());

const book = () => fakeServer(() => ok({ items: [customerBody()], next_cursor: null }));

describe("the customer book", () => {
  it("offers a seller everything of the book while the server keeps to roles", async () => {
    renderScreen(<CustomersScreen />, { fetch: book().fetch, role: "seller" });
    await screen.findByText("Ali Valiyev");
    expect(link("Yangi mijoz")?.getAttribute("href")).toBe("#/customers/new");
    expect(link("To'lov xabarlari")?.getAttribute("href")).toBe("#/payment-notices");
    expect(link("Ulanishni kutayotganlar")?.getAttribute("href")).toBe("#/customers/waiting");
    expect(link("Peshtaxta kodi")?.getAttribute("href")).toBe("#/customers/counter-code");
  });

  it("offers the same to a seller whose permissions are the role's, named by the server", async () => {
    renderScreen(<CustomersScreen />, { fetch: book().fetch, role: "seller", permissions: SELLER_HOLDS });
    await screen.findByText("Ali Valiyev");
    expect(link("Yangi mijoz")).not.toBeNull();
    expect(link("To'lov xabarlari")).not.toBeNull();
    expect(link("Ulanishni kutayotganlar")).not.toBeNull();
  });

  it("shows a seller denied `customers.create` no way to add a customer", async () => {
    renderScreen(<CustomersScreen />, { fetch: book().fetch, role: "seller", permissions: without("customers.create") });
    await screen.findByText("Ali Valiyev");
    expect(link("Yangi mijoz")).toBeNull();
    expect(link("To'lov xabarlari")).not.toBeNull();
  });

  it("shows a manager denied `customers.create` none either: the role does not bring it back", async () => {
    renderScreen(<CustomersScreen />, { fetch: book().fetch, role: "manager", permissions: without("customers.create") });
    await screen.findByText("Ali Valiyev");
    expect(link("Yangi mijoz")).toBeNull();
  });

  it("shows a seller denied `payment_notices.decide` no way to the payment notices", async () => {
    renderScreen(<CustomersScreen />, {
      fetch: book().fetch,
      role: "seller",
      permissions: without("payment_notices.decide"),
    });
    await screen.findByText("Ali Valiyev");
    expect(link("To'lov xabarlari")).toBeNull();
    expect(link("Yangi mijoz")).not.toBeNull();
    expect(link("Ulanishni kutayotganlar")).not.toBeNull();
  });
});

describe("picking a customer for a new entry", () => {
  const pick = (role: Role, permissions?: readonly string[]) => {
    renderScreen(<CustomersScreen pick />, { fetch: book().fetch, role, ...(permissions ? { permissions } : {}) });
    return screen.findByText("Ali Valiyev");
  };

  it("offers a seller the sale and the payment while the server keeps to roles", async () => {
    await pick("seller");
    expect(link("Nasiya")?.getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}/credit`);
    expect(link("To'lov")?.getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}/payment`);
  });

  it("offers a seller denied `payments.record` the sale alone", async () => {
    await pick("seller", without("payments.record"));
    expect(link("Nasiya")).not.toBeNull();
    expect(link("To'lov")).toBeNull();
  });

  it("offers a seller denied `credits.record` the payment alone", async () => {
    await pick("seller", without("credits.record"));
    expect(link("Nasiya")).toBeNull();
    expect(link("To'lov")).not.toBeNull();
  });
});

describe("adding a customer", () => {
  it("is a form for a seller by role", () => {
    const server = fakeServer(() => ok(customerBody()));
    renderScreen(<NewCustomerScreen />, { fetch: server.fetch, role: "seller" });
    expect(button("Yangi mijoz")).not.toBeNull();
  });

  it("is no form, and nothing is sent, for a seller denied `customers.create`", () => {
    const server = fakeServer(() => ok(customerBody()));
    renderScreen(<NewCustomerScreen />, { fetch: server.fetch, role: "seller", permissions: without("customers.create") });
    expect(screen.getByText(NOT_FOUND)).toBeTruthy();
    expect(button("Yangi mijoz")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(server.sent).toHaveLength(0);
  });
});

describe("the payment notices", () => {
  const notices = () => fakeServer(() => ok({ items: [openNoticeBody({ has_receipt: false })] }));

  it("are a seller's to decide by role", async () => {
    renderScreen(<PaymentNoticesScreen />, { fetch: notices().fetch, role: "seller" });
    await screen.findByText("Ali Valiyev");
    expect(button("Qabul qilish")).not.toBeNull();
  });

  it("are not shown to, and not asked for, a manager denied `payment_notices.decide`", async () => {
    const server = notices();
    renderScreen(<PaymentNoticesScreen />, {
      fetch: server.fetch,
      role: "manager",
      permissions: without("payment_notices.decide"),
    });
    expect(screen.getByText(NOT_FOUND)).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(button("Qabul qilish")).toBeNull();
    expect(server.sent).toHaveLength(0);
  });

  it("are named on the customer's page without a way in for a member who may not decide them", async () => {
    const server = fakeServer((sent) =>
      sent.path.endsWith("/link") ? ok(linkBody()) : ok(detailBody({ payment_notices: [noticeBody()] })),
    );
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, {
      fetch: server.fetch,
      role: "seller",
      permissions: without("payment_notices.decide"),
    });
    expect(await screen.findByText("Mijozning 1 ta to'lov xabari javob kutmoqda.")).toBeTruthy();
    expect(link("To'lov xabarlari")).toBeNull();
  });

  it("have a way in from the customer's page for a member who may decide them", async () => {
    const server = fakeServer((sent) =>
      sent.path.endsWith("/link") ? ok(linkBody()) : ok(detailBody({ payment_notices: [noticeBody()] })),
    );
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: server.fetch, role: "seller" });
    await screen.findByText("Mijozning 1 ta to'lov xabari javob kutmoqda.");
    expect(link("To'lov xabarlari")?.getAttribute("href")).toBe("#/payment-notices");
  });
});

describe("the waiting list", () => {
  const waiting = () =>
    fakeServer((sent) =>
      sent.path === `${SHOP_BASE}/waiting`
        ? ok({ items: [{ id: "99999999-9999-4999-8999-999999999991", name: "Vali T.", since: "2026-10-06T05:10:00+00:00" }] })
        : ok({ items: [customerBody()], next_cursor: null }),
    );

  it("lets a seller attach or dismiss by role", async () => {
    renderScreen(<WaitingScreen />, { fetch: waiting().fetch, role: "seller" });
    await screen.findByText("Vali T.");
    expect(button("Mijozga biriktirish")).not.toBeNull();
    expect(button("So'rovni rad etish")).not.toBeNull();
  });

  it("shows a seller denied `customers.create` who waits, with nothing to do about it", async () => {
    const server = waiting();
    renderScreen(<WaitingScreen />, { fetch: server.fetch, role: "seller", permissions: without("customers.create") });
    await screen.findByText("Vali T.");
    expect(button("Mijozga biriktirish")).toBeNull();
    expect(button("So'rovni rad etish")).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });
});

describe("a customer's personal link", () => {
  const notLinked = () => fakeServer(() => ok(linkBody()));

  it("is a seller's to create by role", async () => {
    renderScreen(<LinkSection customerId={CUSTOMER_ID} archived={false} />, { fetch: notLinked().fetch, role: "seller" });
    await loaded();
    expect(button("Shaxsiy havola yaratish")).not.toBeNull();
  });

  it("is not offered to a seller denied `customers.create`, who still reads whether the customer is connected", async () => {
    renderScreen(<LinkSection customerId={CUSTOMER_ID} archived={false} />, {
      fetch: notLinked().fetch,
      role: "seller",
      permissions: without("customers.create"),
    });
    await loaded();
    expect(screen.getByRole("heading", { level: 2 })).toBeTruthy();
    expect(button("Shaxsiy havola yaratish")).toBeNull();
  });
});

describe("recording an entry", () => {
  const customer = () => fakeServer(() => ok(detailBody()));

  it("is a form for a seller by role, for a sale and for a payment", async () => {
    renderScreen(<EntryScreen customerId={CUSTOMER_ID} kind="payment" />, { fetch: customer().fetch, role: "seller" });
    expect(await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" })).toBeTruthy();
  });

  it("is no form, and nothing is asked, for a seller denied `payments.record` who opens a payment", async () => {
    const server = customer();
    renderScreen(<EntryScreen customerId={CUSTOMER_ID} kind="payment" />, {
      fetch: server.fetch,
      role: "seller",
      permissions: without("payments.record"),
    });
    expect(screen.getByText(NOT_FOUND)).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent).toHaveLength(0);
  });

  it("is still a form for the same seller's sale: each kind has its own permission", async () => {
    renderScreen(<EntryScreen customerId={CUSTOMER_ID} kind="credit" />, {
      fetch: customer().fetch,
      role: "seller",
      permissions: without("payments.record"),
    });
    expect(await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" })).toBeTruthy();
  });

  it("is no form for a manager denied `credits.record` who opens a sale", () => {
    renderScreen(<EntryScreen customerId={CUSTOMER_ID} kind="credit" />, {
      fetch: customer().fetch,
      role: "manager",
      permissions: without("credits.record"),
    });
    expect(screen.getByText(NOT_FOUND)).toBeTruthy();
  });
});

describe("adding goods to a recorded sale", () => {
  const entry = { authorId: "m-1" } as Parameters<typeof mayAddGoods>[0];

  it("is the author's by role, and a manager's for another member's sale", () => {
    expect(mayAddGoods(entry, { role: "seller", membershipId: "m-1" })).toBe(true);
    expect(mayAddGoods(entry, { role: "seller", membershipId: "m-2" })).toBe(false);
    expect(mayAddGoods(entry, { role: "manager", membershipId: "m-2" })).toBe(true);
  });

  it("is nobody's who may not record a credit sale, the author and `entries.others` included", () => {
    const denied = new Set(without("credits.record"));
    expect(mayAddGoods(entry, { role: "seller", membershipId: "m-1", permissions: denied })).toBe(false);
    const others = new Set([...without("credits.record"), "entries.others"]);
    expect(mayAddGoods(entry, { role: "manager", membershipId: "m-2", permissions: others })).toBe(false);
  });

  it("is a seller's for another member's sale once the owner granted `entries.others`", () => {
    const granted = new Set([...SELLER_HOLDS, "entries.others"]);
    expect(mayAddGoods(entry, { role: "seller", membershipId: "m-2", permissions: granted })).toBe(true);
  });
});

describe("a seller granted what a manager holds by role", () => {
  it("reverses an entry on the customer's page once granted `entries.cancel`, and not before", async () => {
    const server = () => fakeServer((sent) => (sent.path.endsWith("/link") ? ok(linkBody()) : ok(detailBody({ entries: [entryBody()] }))));
    const first = renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: server().fetch, role: "seller" });
    await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
    expect(button("Yozuvni bekor qilish")).toBeNull();
    first.unmount();

    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, {
      fetch: server().fetch,
      role: "seller",
      permissions: [...SELLER_HOLDS, "entries.cancel"],
    });
    await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
    expect(button("Yozuvni bekor qilish")).not.toBeNull();
  });
});

describe("the two lists a manager answers", () => {
  it("lead to each other by role", async () => {
    renderScreen(<DisputesScreen />, { fetch: fakeServer(() => ok({ items: [openDisputeBody()] })).fetch, role: "manager" });
    await loaded();
    expect(link("Muddat so'rovlari")?.getAttribute("href")).toBe("#/date-requests");
  });

  it("do not lead from the disputes to date requests the member may not answer", async () => {
    renderScreen(<DisputesScreen />, {
      fetch: fakeServer(() => ok({ items: [openDisputeBody()] })).fetch,
      role: "seller",
      permissions: [...SELLER_HOLDS, "disputes.decide"],
    });
    await loaded();
    expect(link("Muddat so'rovlari")).toBeNull();
  });

  it("do not lead from the date requests to disputes the member may not decide", async () => {
    renderScreen(<DateRequestsScreen />, {
      fetch: fakeServer(() => ok({ items: [openDateRequestBody()] })).fetch,
      role: "seller",
      permissions: [...SELLER_HOLDS, "promises.change"],
    });
    await loaded();
    expect(link("E'tirozlar")).toBeNull();
  });
});

describe("the reminders", () => {
  const reminders = () =>
    fakeServer((sent) => (sent.path.endsWith("/unreachable") ? ok({ items: [] }) : ok(remindersBody())));

  it("are a manager's to read, change and follow up by role", async () => {
    const server = reminders();
    renderScreen(<RemindersScreen />, { fetch: server.fetch, role: "manager" });
    await loaded();
    expect(button("Saqlash")).not.toBeNull();
    expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "Avtomatik eslatmalar yoqilgan" }).disabled).toBe(false);
    expect(server.sent.some((sent) => sent.path.endsWith("/unreachable"))).toBe(true);
  });

  it("are read without a way to change them by a member who holds `settings.view` alone", async () => {
    const server = reminders();
    renderScreen(<RemindersScreen />, { fetch: server.fetch, role: "seller", permissions: [...SELLER_HOLDS, "settings.view"] });
    await loaded();
    expect(screen.getByRole("heading", { level: 2, name: "Eslatma sozlamalari" })).toBeTruthy();
    expect(button("Saqlash")).toBeNull();
    for (const control of [...screen.getAllByRole<HTMLInputElement>("checkbox"), ...screen.getAllByRole<HTMLInputElement>("radio")]) {
      expect(control.disabled).toBe(true);
    }
    expect(screen.getByRole<HTMLSelectElement>("combobox").disabled).toBe(true);
    // Who cannot be reached goes with sending reminders, which this member may not do.
    expect(server.sent.some((sent) => sent.path.endsWith("/unreachable"))).toBe(false);
  });

  it("show a member who holds `reminders.send` alone who cannot be reached, and ask for no settings", async () => {
    const server = reminders();
    renderScreen(<RemindersScreen />, { fetch: server.fetch, role: "seller", permissions: [...SELLER_HOLDS, "reminders.send"] });
    await loaded();
    expect(screen.queryByRole("heading", { level: 2, name: "Eslatma sozlamalari" })).toBeNull();
    expect(button("Saqlash")).toBeNull();
    expect(server.sent.map((sent) => sent.path)).toEqual([`${SHOP_BASE}/reminders/unreachable`]);
  });
});
