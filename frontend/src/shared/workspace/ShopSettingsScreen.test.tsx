// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { deferred, fakeServer, ok, refusal, type Reply, type Sent, settingsBody, SHOP_BASE } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { Role } from "../navigation";
import { parsePromiseDays, settingsAccess, ShopSettingsScreen } from "./ShopSettingsScreen";

afterEach(cleanup);

/** A shop named "Baraka savdo", Uzbek, 30 days; a write answers with the settings after the change. */
function shop(onWrite?: (sent: Sent, attempt: number) => Reply) {
  let current: Record<string, unknown> = settingsBody();
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

async function open(server: ReturnType<typeof fakeServer>, role: Role = "owner") {
  renderScreen(<ShopSettingsScreen />, { fetch: server.fetch, role });
  await screen.findByText("Do'kon nomi");
}

const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });
const name = () => screen.getByLabelText<HTMLInputElement>("Do'kon nomi");
const lang = () => screen.getByLabelText<HTMLSelectElement>("Do'kon tili");
const days = () => screen.getByLabelText<HTMLInputElement>("Odatdagi to'lash muddati, kun");
const save = () => screen.getByRole<HTMLButtonElement>("button", { name: "Saqlash" });

describe("shop settings for the owner (REQ-049)", () => {
  it("loads the settings into a form", async () => {
    const server = shop();
    renderScreen(<ShopSettingsScreen />, { fetch: server.fetch, role: "owner" });
    expect(screen.getByRole("status").textContent).toBe("Yuklanmoqda…");
    await screen.findByText("Do'kon nomi");
    expect(server.sent[0]).toMatchObject({ method: "GET", path: SHOP_BASE });
    expect(name().value).toBe("Baraka savdo");
    expect(lang().value).toBe("uz");
    expect(Array.from(lang().options).map((option) => [option.value, option.textContent])).toEqual([
      ["uz", "O'zbekcha"],
      ["ru", "Русский"],
    ]);
    expect(days().value).toBe("30");
  });

  it.each([
    ["the name", () => type(name(), "  Baraka market "), { name: "Baraka market" }],
    ["the language", () => type(lang(), "ru"), { lang: "ru" }],
    ["the days", () => type(days(), "14"), { default_promise_days: 14 }],
    ["the first allowed day count", () => type(days(), "1"), { default_promise_days: 1 }],
    ["the last allowed day count", () => type(days(), "365"), { default_promise_days: 365 }],
  ])("sends only %s when only that changed", async (_what, change, body) => {
    const server = shop();
    await open(server);
    change();
    fireEvent.click(save());
    expect((await screen.findByRole("status")).textContent).toBe("Sozlamalar saqlandi.");
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "PATCH", path: SHOP_BASE });
    expect(server.writes()[0]?.body).toEqual(body);
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
  });

  it("sends everything that changed in one request, the days as a whole number", async () => {
    const server = shop();
    await open(server);
    type(name(), "Ziyo market");
    type(lang(), "ru");
    type(days(), " 45 ");
    fireEvent.click(save());
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ name: "Ziyo market", lang: "ru", default_promise_days: 45 });
    // The form now holds what the server answered, so saving again has nothing to send.
    expect(name().value).toBe("Ziyo market");
    expect(days().value).toBe("45");
    fireEvent.click(save());
    expect(server.writes()).toHaveLength(1);
  });

  it("sends nothing when nothing was changed", async () => {
    const server = shop();
    await open(server);
    fireEvent.click(save());
    type(name(), " Baraka savdo ");
    fireEvent.click(save());
    expect(server.writes()).toHaveLength(0);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it.each([
    ["", "30", "Do'kon nomi 1 dan 80 belgigacha bo'lishi kerak."],
    ["   ", "30", "Do'kon nomi 1 dan 80 belgigacha bo'lishi kerak."],
    ["n".repeat(81), "30", "Do'kon nomi 1 dan 80 belgigacha bo'lishi kerak."],
    ["Baraka", "", "Muddat 1 kundan 365 kungacha butun son bo'lishi kerak."],
    ["Baraka", "0", "Muddat 1 kundan 365 kungacha butun son bo'lishi kerak."],
    ["Baraka", "366", "Muddat 1 kundan 365 kungacha butun son bo'lishi kerak."],
    ["Baraka", "30.5", "Muddat 1 kundan 365 kungacha butun son bo'lishi kerak."],
    ["Baraka", "-5", "Muddat 1 kundan 365 kungacha butun son bo'lishi kerak."],
    ["Baraka", "bir oy", "Muddat 1 kundan 365 kungacha butun son bo'lishi kerak."],
  ])("refuses the name %j with %j days without calling the server", async (shopName, dayCount, message) => {
    const server = shop();
    await open(server);
    type(name(), shopName);
    type(days(), dayCount);
    fireEvent.click(save());
    expect(screen.getByRole("alert").textContent).toBe(message);
    expect(server.writes()).toHaveLength(0);
  });

  it("accepts a name of exactly 80 characters", async () => {
    const server = shop();
    await open(server);
    type(name(), "n".repeat(80));
    fireEvent.click(save());
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ name: "n".repeat(80) });
  });

  it("shows the server's refusal and puts a validation refusal next to its field", async () => {
    const server = shop((_sent, attempt) =>
      attempt === 0
        ? refusal(403, "FORBIDDEN_ROLE", "Bu amal uchun sizning rolingiz yetarli emas.")
        : refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { default_promise_days: "x", name: "y" }),
    );
    await open(server);
    type(days(), "14");
    fireEvent.click(save());
    expect((await screen.findByRole("alert")).textContent).toBe("Bu amal uchun sizning rolingiz yetarli emas.");
    expect(screen.queryByRole("status")).toBeNull();

    type(days(), "15");
    fireEvent.click(save());
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(3));
    expect(screen.getAllByRole("alert").map((alert) => alert.textContent)).toEqual([
      "Ma'lumotlar noto'g'ri kiritilgan.",
      "Do'kon nomi 1 dan 80 belgigacha bo'lishi kerak.",
      "Muddat 1 kundan 365 kungacha butun son bo'lishi kerak.",
    ]);
  });

  it("sends one request for a double tap and the same key on a retry", async () => {
    const answer = deferred<{ status: number; body: unknown }>();
    const server = shop((_sent, attempt) => (attempt === 0 ? "offline" : answer.promise));
    await open(server);
    type(days(), "14");
    fireEvent.click(save());
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi.");

    const button = save();
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.submit(button.closest("form") as HTMLFormElement);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Saqlanmoqda…" }).disabled).toBe(true);
    expect(server.writes()).toHaveLength(2);
    answer.resolve(ok(settingsBody({ default_promise_days: 14 })));
    await screen.findByRole("status");
    expect(server.writes()).toHaveLength(2);
    expect(server.writes()[1]?.headers["Idempotency-Key"]).toBe(server.writes()[0]?.headers["Idempotency-Key"]);
    expect(server.writes()[1]?.body).toEqual({ default_promise_days: 14 });
  });

  it("offers a retry when the settings cannot be loaded", async () => {
    let attempt = 0;
    const server = fakeServer(() => (attempt++ === 0 ? "offline" : ok(settingsBody())));
    renderScreen(<ShopSettingsScreen />, { fetch: server.fetch, role: "owner" });
    fireEvent.click(await screen.findByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByLabelText("Do'kon nomi")).toBeTruthy();
  });
});

describe("shop settings by role", () => {
  it("shows a manager the settings and nothing to change them with", async () => {
    const server = shop();
    await open(server, "manager");
    expect(screen.getByText("Sozlamalarni faqat do'kon egasi o'zgartira oladi.")).toBeTruthy();
    const facts = Array.from(document.querySelectorAll("dl.facts > *")).map((node) => node.textContent);
    expect(facts).toEqual(["Do'kon nomi", "Baraka savdo", "Do'kon tili", "O'zbekcha", "Odatdagi to'lash muddati, kun", "30 kun"]);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Saqlash" })).toBeNull();
    expect(document.querySelector("form")).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it("shows a seller nothing and asks the server nothing", async () => {
    const server = shop();
    renderScreen(<ShopSettingsScreen />, { fetch: server.fetch, role: "seller" });
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent).toHaveLength(0);
    expect(screen.queryByText("Baraka savdo")).toBeNull();
  });

  it("decides by role: the owner edits, a manager reads, a seller gets nothing", () => {
    expect(settingsAccess("owner")).toBe("edit");
    expect(settingsAccess("manager")).toBe("read");
    expect(settingsAccess("seller")).toBe("none");
  });

  it("reads days only as a whole number from 1 to 365", () => {
    expect(parsePromiseDays("1")).toBe(1);
    expect(parsePromiseDays(" 30 ")).toBe(30);
    expect(parsePromiseDays("365")).toBe(365);
    for (const text of ["", "0", "366", "1000", "30.0", "30,5", "1e2", "-1", "+5", "30 kun"]) {
      expect(parsePromiseDays(text), text).toBeNull();
    }
  });
});
