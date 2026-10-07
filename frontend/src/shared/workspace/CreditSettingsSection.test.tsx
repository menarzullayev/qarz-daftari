// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { creditSettingsBody, deferred, fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { Role } from "../navigation";
import { CreditSettingsSection } from "./CreditSettingsSection";

afterEach(cleanup);

const RANGE = "Limit 1\u00a0000 so'mdan 10\u00a0000\u00a0000\u00a0000 so'mgacha bo'lishi kerak.";
const NOT_WHOLE = "Limit butun so'mda bo'lishi kerak, masalan 500000.";

/** A shop with the given credit rules; a write answers the rules after the change. */
function shop(settings: Record<string, unknown> = {}, onWrite?: (sent: Sent, attempt: number) => Reply) {
  let current: Record<string, unknown> = creditSettingsBody(settings);
  let attempt = 0;
  return fakeServer((sent) => {
    if (sent.method === "GET") {
      return ok(current);
    }
    if (onWrite) {
      return onWrite(sent, attempt++);
    }
    current = { ...current, ...(sent.body as Record<string, unknown>) };
    return ok(current);
  });
}

async function open(server: ReturnType<typeof fakeServer>, role: Role = "manager") {
  renderScreen(<CreditSettingsSection />, { fetch: server.fetch, role });
  await screen.findByText(role === "seller" ? "Umumiy limit" : "Do'konning umumiy limiti, so'm");
}

const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });
const limit = () => screen.getByLabelText<HTMLInputElement>("Do'konning umumiy limiti, so'm");
const sellers = () =>
  screen.getByLabelText<HTMLInputElement>("Sotuvchilar limitdan oshirib nasiya yoza oladi (ogohlantirish bilan)");
const save = () => screen.getByRole<HTMLButtonElement>("button", { name: "Nasiya sozlamalarini saqlash" });

describe("the shop's credit settings for a manager and an owner (REQ-044)", () => {
  it.each(["manager", "owner"] as const)("loads them into a form for a %s", async (role) => {
    const server = shop({ default_credit_limit: 500000, sellers_may_exceed: true });
    renderScreen(<CreditSettingsSection />, { fetch: server.fetch, role });
    expect(screen.getByRole("status").textContent).toBe("Yuklanmoqda…");
    await screen.findByLabelText("Do'konning umumiy limiti, so'm");
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/credit-settings` });
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Nasiya sozlamalari");
    expect(limit().value).toBe("500\u00a0000");
    expect(sellers().checked).toBe(true);
  });

  it("shows an empty field when there is no default limit", async () => {
    await open(shop());
    expect(limit().value).toBe("");
    expect(sellers().checked).toBe(false);
  });

  it.each([
    ["sets a default", {}, () => type(limit(), "500 000"), { default_credit_limit: 500000 }],
    ["changes the default", { default_credit_limit: 500000 }, () => type(limit(), "750000"), { default_credit_limit: 750000 }],
    ["removes the default with null", { default_credit_limit: 500000 }, () => type(limit(), "  "), { default_credit_limit: null }],
    ["lets sellers proceed", {}, () => fireEvent.click(sellers()), { sellers_may_exceed: true }],
    ["stops sellers", { sellers_may_exceed: true }, () => fireEvent.click(sellers()), { sellers_may_exceed: false }],
    ["takes the smallest limit", {}, () => type(limit(), "1000"), { default_credit_limit: 1000 }],
    ["takes the largest limit", {}, () => type(limit(), "10000000000"), { default_credit_limit: 10000000000 }],
  ])("%s and sends only that", async (_what, settings, change, body) => {
    const server = shop(settings);
    await open(server);
    change();
    fireEvent.click(save());
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Nasiya sozlamalari saqlandi."));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "PATCH", path: `${SHOP_BASE}/credit-settings` });
    expect(server.writes()[0]?.body).toEqual(body);
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
  });

  it("sends both changes in one request, then has nothing more to send", async () => {
    const server = shop();
    await open(server);
    type(limit(), "300000");
    fireEvent.click(sellers());
    fireEvent.click(save());
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ default_credit_limit: 300000, sellers_may_exceed: true });
    expect(limit().value).toBe("300\u00a0000");
    fireEvent.click(save());
    expect(server.writes()).toHaveLength(1);
  });

  it("sends nothing when nothing was changed", async () => {
    const server = shop({ default_credit_limit: 500000 });
    await open(server);
    fireEvent.click(save());
    type(limit(), "500000");
    fireEvent.click(save());
    expect(server.writes()).toHaveLength(0);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it.each([
    ["999", RANGE],
    ["0", RANGE],
    ["10000000001", RANGE],
    ["500.5", NOT_WHOLE],
    ["500,50", NOT_WHOLE],
    ["-500000", NOT_WHOLE],
    ["besh yuz ming", NOT_WHOLE],
  ])("refuses the limit %j without calling the server", async (typed, message) => {
    const server = shop();
    await open(server);
    type(limit(), typed);
    fireEvent.click(save());
    expect(screen.getByRole("alert").textContent).toBe(message);
    expect(limit().getAttribute("aria-invalid")).toBe("true");
    expect(server.writes()).toHaveLength(0);
  });

  it("shows what the typed limit was read as before it is saved", async () => {
    await open(shop());
    type(limit(), "500 ming");
    expect(screen.getByText("500\u00a0000 so'm", { normalizer: (text) => text })).toBeTruthy();
  });

  it("shows the server's refusal and puts a validation refusal next to the field", async () => {
    const server = shop({}, (_sent, attempt) =>
      attempt === 0
        ? refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.")
        : refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { default_credit_limit: "x" }),
    );
    await open(server);
    type(limit(), "500000");
    fireEvent.click(save());
    expect((await screen.findByRole("alert")).textContent).toBe("Do'kon to'xtatilgan.");
    expect(screen.queryByRole("status")).toBeNull();

    type(limit(), "600000");
    fireEvent.click(save());
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
    expect(screen.getAllByRole("alert").map((alert) => alert.textContent)).toEqual(["Ma'lumotlar noto'g'ri kiritilgan.", RANGE]);
  });

  it("sends one request for a double tap and the same key on a retry", async () => {
    const answer = deferred<{ status: number; body: unknown }>();
    const server = shop({}, (_sent, attempt) => (attempt === 0 ? "offline" : answer.promise));
    await open(server);
    type(limit(), "500000");
    fireEvent.click(save());
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi.");

    const button = save();
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.submit(button.closest("form") as HTMLFormElement);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Saqlanmoqda…" }).disabled).toBe(true);
    expect(server.writes()).toHaveLength(2);
    answer.resolve(ok(creditSettingsBody({ default_credit_limit: 500000 })));
    await screen.findByRole("status");
    expect(server.writes()).toHaveLength(2);
    expect(server.writes()[1]?.headers["Idempotency-Key"]).toBe(server.writes()[0]?.headers["Idempotency-Key"]);
    expect(server.writes()[1]?.body).toEqual({ default_credit_limit: 500000 });
  });

  it("offers a retry when the settings cannot be loaded", async () => {
    let attempt = 0;
    const server = fakeServer(() => (attempt++ === 0 ? "offline" : ok(creditSettingsBody())));
    renderScreen(<CreditSettingsSection />, { fetch: server.fetch, role: "owner" });
    fireEvent.click(await screen.findByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByLabelText("Do'konning umumiy limiti, so'm")).toBeTruthy();
  });
});

describe("the shop's credit settings for a seller", () => {
  it.each([
    [{ default_credit_limit: 500000, sellers_may_exceed: false }, ["Umumiy limit", "500\u00a0000 so'm", "Sotuvchi limitdan oshira oladimi", "Yo'q"]],
    [{ sellers_may_exceed: true }, ["Umumiy limit", "Belgilanmagan", "Sotuvchi limitdan oshira oladimi", "Ha, ogohlantirish bilan"]],
  ])("shows them to read and nothing to change them with: %j", async (settings, facts) => {
    const server = shop(settings);
    await open(server, "seller");
    expect(screen.getByText("Nasiya sozlamalarini menejer yoki do'kon egasi o'zgartiradi.")).toBeTruthy();
    expect(Array.from(document.querySelectorAll("dl.facts > *"), (node) => node.textContent)).toEqual(facts);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.querySelector("form")).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });
});
