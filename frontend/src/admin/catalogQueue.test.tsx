// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Reply, Sent } from "../testing/fakeServer";
import { AdminRoutes } from "./AdminApp";
import { CatalogQueueScreen } from "./CatalogQueueScreen";
import { SETTING_RULES, SUGGESTION_STATUSES } from "./rules";
import { SettingsScreen } from "./SettingsScreen";
import { ADMIN, adminApi, ok, platformBody, refusal, renderAdmin } from "./testing";

beforeEach(() => {
  window.location.hash = "";
});
afterEach(cleanup);

const QUEUE = `${ADMIN}/catalog/suggestions`;
const ITEM_ID = "99999999-9999-4999-8999-999999999901";
const CODE_ID = "99999999-9999-4999-8999-999999999902";
const SHARED_ID = "66666666-6666-4666-8666-666666666661";
const KEY = /^[A-Za-z0-9_-]{8,128}$/;

/** A suggestion as the server sends it: a name, a unit, a barcode, and nothing that names a shop. */
const suggestion = (overrides: Record<string, unknown> = {}) => ({
  id: ITEM_ID,
  kind: "item",
  name: "Uy qatig'i",
  unit: "l",
  barcode: null,
  shared_item: null,
  status: "pending",
  created_at: "2026-10-06T06:00:00+00:00",
  decided_at: null,
  same: 0,
  ...overrides,
});
const ITEM = suggestion({ same: 2 });
const CODE = suggestion({
  id: CODE_ID,
  kind: "barcode",
  name: null,
  unit: null,
  barcode: "4780000000014",
  shared_item: { id: SHARED_ID, name_ru: "Чай зелёный Тоғ", name_uz: "Ko'k choy Tog'", amount: "100 g" },
});

function open(onWrite: (sent: Sent) => Reply = (sent) => ok({ id: sent.path.split("/").at(-2), kind: "item", status: sent.path.endsWith("approve") ? "approved" : "rejected", shared_item_id: null })) {
  const made = adminApi((sent) => (sent.method === "GET" ? ok({ items: [ITEM, CODE], next_cursor: null, categories: [] }) : onWrite(sent)));
  renderAdmin(<CatalogQueueScreen api={made.api} />);
  return made.server;
}

const rows = () => within(screen.getByRole("list", { name: "Takliflar" })).getAllByRole("listitem");
const writes = (server: ReturnType<typeof open>) => server.sent.filter((sent) => sent.method !== "GET");

describe("the queue of what shops propose for the shared catalogue", () => {
  it("shows a name, a unit and a barcode, and says that no shop is named", async () => {
    const server = open();
    await waitFor(() => expect(rows()).toHaveLength(2));
    const [item, code] = rows();
    expect(item?.textContent).toContain("Yangi mahsulot: Uy qatig'i, l");
    expect(item?.textContent).toContain("Shu taklif boshqa do'konlardan ham kelgan: 2");
    expect(code?.textContent).toContain("Shtrix-kod 4780000000014: Ko'k choy Tog', 100 g");
    expect(screen.getByText("Taklifda faqat mahsulot nomi, o'lchov birligi va shtrix-kodi bor. Qaysi do'kondan kelgani ko'rsatilmaydi.")).toBeTruthy();
    expect(server.sent[0]?.query).toEqual({ status: "pending" });
    const status = screen.getByLabelText<HTMLSelectElement>("Holati");
    expect(Array.from(status.options).map((option) => [option.value, option.text])).toEqual([
      ["pending", "Kutmoqda"],
      ["approved", "Tasdiqlangan"],
      ["rejected", "Rad etilgan"],
    ]);
    expect([...SUGGESTION_STATUSES]).toEqual(["pending", "approved", "rejected"]);
  });

  it("approves an item under the names and the category the administrator gives", async () => {
    const server = open();
    await waitFor(() => expect(rows()).toHaveLength(2));
    const item = within(rows()[0] as HTMLElement);
    // The shop's own spelling is offered as the Uzbek name; the Russian one starts empty.
    expect(item.getByLabelText<HTMLInputElement>("O'zbekcha nomi (lotin)").value).toBe("Uy qatig'i");
    expect(item.getByLabelText<HTMLInputElement>("Ruscha nomi").value).toBe("");
    fireEvent.change(item.getByLabelText("Ruscha nomi"), { target: { value: "  Катык   домашний " } });
    fireEvent.change(item.getByLabelText("Bo'lim"), { target: { value: "dairy" } });
    fireEvent.click(item.getByRole("button", { name: "Tasdiqlash" }));
    await waitFor(() => expect(writes(server)).toHaveLength(1));
    const sent = writes(server)[0];
    expect(sent?.path).toBe(`${QUEUE}/${ITEM_ID}/approve`);
    expect(sent?.body).toEqual({ name_ru: "Катык домашний", name_uz: "Uy qatig'i", category: "dairy" });
    expect(sent?.headers["Idempotency-Key"]).toMatch(KEY);
    expect((await screen.findByRole("status")).textContent).toBe("Taklif tasdiqlandi.");
    // The queue is read again after a decision.
    await waitFor(() => expect(server.sent.filter((asked) => asked.method === "GET")).toHaveLength(2));
  });

  it("does not send an item without a name", async () => {
    const server = open();
    await waitFor(() => expect(rows()).toHaveLength(2));
    const item = within(rows()[0] as HTMLElement);
    fireEvent.change(item.getByLabelText("O'zbekcha nomi (lotin)"), { target: { value: "   " } });
    fireEvent.click(item.getByRole("button", { name: "Tasdiqlash" }));
    expect(item.getByRole("alert").textContent).toBe("Ruscha yoki o'zbekcha nomini kiriting.");
    expect(writes(server)).toEqual([]);
  });

  it("approves a barcode as it is, with no body, and rejects with none either", async () => {
    const server = open();
    await waitFor(() => expect(rows()).toHaveLength(2));
    const code = within(rows()[1] as HTMLElement);
    expect(code.queryByLabelText("Ruscha nomi")).toBeNull();
    fireEvent.click(code.getByRole("button", { name: "Tasdiqlash" }));
    await waitFor(() => expect(writes(server)).toHaveLength(1));
    expect(writes(server)[0]?.path).toBe(`${QUEUE}/${CODE_ID}/approve`);
    expect(writes(server)[0]?.body).toBeUndefined();
    await waitFor(() => expect(rows()).toHaveLength(2));
    fireEvent.click(within(rows()[0] as HTMLElement).getByRole("button", { name: "Rad etish" }));
    await waitFor(() => expect(writes(server)).toHaveLength(2));
    expect(writes(server)[1]?.path).toBe(`${QUEUE}/${ITEM_ID}/reject`);
    expect(writes(server)[1]?.body).toBeUndefined();
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Taklif rad etildi."));
  });

  it("says why the server refused a decision, on the suggestion it was about", async () => {
    open(() => refusal(409, "SHARED_BARCODE_TAKEN", "Bu shtrix-kod umumiy katalogdagi boshqa tovarga biriktirilgan."));
    await waitFor(() => expect(rows()).toHaveLength(2));
    fireEvent.click(within(rows()[1] as HTMLElement).getByRole("button", { name: "Tasdiqlash" }));
    const alert = await within(rows()[1] as HTMLElement).findByRole("alert");
    expect(alert.textContent).toBe("Bu shtrix-kod umumiy katalogdagi boshqa tovarga biriktirilgan.");
    expect(within(rows()[0] as HTMLElement).queryByRole("alert")).toBeNull();
  });

  it("offers no decision on what was decided", async () => {
    const made = adminApi(() => ok({ items: [suggestion({ status: "approved", decided_at: "2026-10-06T07:00:00+00:00" })], next_cursor: null }));
    renderAdmin(<CatalogQueueScreen api={made.api} />);
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.queryByRole("button", { name: "Tasdiqlash" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Rad etish" })).toBeNull();
  });
});

describe("the switch of the shared catalogue", () => {
  it("is a setting like the others, and the queue is linked only while it is on", async () => {
    expect(SETTING_RULES["catalog_on"]).toEqual({ kind: "switch" });
    const off = adminApi(() => ok(platformBody()));
    renderAdmin(<SettingsScreen api={off.api} />);
    await screen.findByLabelText("Umumiy mahsulotlar katalogi yoqilgan");
    expect(screen.queryByRole("link", { name: "Do'konlarning katalog takliflarini ko'rish" })).toBeNull();
    cleanup();

    const on = adminApi(() => ok(platformBody({}, { catalog_on: true })));
    renderAdmin(<SettingsScreen api={on.api} />);
    const link = await screen.findByRole("link", { name: "Do'konlarning katalog takliflarini ko'rish" });
    expect(link.getAttribute("href")).toBe("#/settings/catalog");
  });

  it("opens the queue under the settings, and shows the server's refusal while the switch is off", async () => {
    window.location.hash = "#/settings/catalog";
    const made = adminApi(() => refusal(404, "NOT_FOUND", "Topilmadi."));
    renderAdmin(<AdminRoutes api={made.api} />);
    expect(await screen.findByRole("heading", { name: "Katalog takliflari" })).toBeTruthy();
    expect((await screen.findByRole("alert")).textContent).toContain("Topilmadi.");
    expect(made.server.sent[0]?.path).toBe(QUEUE);
  });
});
