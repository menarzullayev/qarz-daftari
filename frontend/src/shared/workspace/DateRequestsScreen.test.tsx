// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  CUSTOMER_ID,
  DATE_REQUEST_ID,
  deferred,
  fakeServer,
  ok,
  openDateRequestBody,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
} from "../../testing/fakeServer";
import { exact, renderScreen } from "../../testing/renderScreen";
import type { Role } from "../navigation";
import DateRequestsScreen from "./DateRequestsScreen";

afterEach(cleanup);

const OTHER_ID = "99999999-9999-4999-8999-99999999999a";
const OTHER = openDateRequestBody({
  id: OTHER_ID,
  customer_id: "11111111-1111-4111-8111-111111111112",
  customer_name: "Vali Aliyev",
  amount: 80000,
  promised_date: "2026-10-10",
  requested_date: "2026-10-25",
  reason: null,
});
const PATH = `${SHOP_BASE}/date-requests`;

function shop(items: unknown[] = [openDateRequestBody(), OTHER], onWrite: (sent: Sent) => Reply = () => ok(openDateRequestBody({ status: "accepted" }))) {
  const held = { items };
  const server = fakeServer((sent) => (sent.method === "GET" ? ok({ items: held.items }) : onWrite(sent)));
  return { ...server, held };
}

const show = (server: ReturnType<typeof fakeServer>, role: Role = "manager") =>
  renderScreen(<DateRequestsScreen />, { fetch: server.fetch, role });
const rows = () => screen.getAllByRole("listitem");
const aliRow = () => rows().find((row) => within(row).queryByText("Ali Valiyev") !== null) as HTMLElement;
const click = (scope: HTMLElement, name: string) => fireEvent.click(within(scope).getByRole("button", { name }));

describe("who has the list of date requests", () => {
  it("shows a seller the not-found screen and asks the server nothing", async () => {
    const server = shop();
    show(server, "seller");
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent).toHaveLength(0);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it.each(["manager", "owner"] as const)("shows a %s the open requests", async (role) => {
    const server = shop();
    show(server, role);
    await screen.findByText("Ali Valiyev");
    expect(server.sent[0]).toMatchObject({ method: "GET", path: PATH });
    expect(rows()).toHaveLength(2);
  });
});

describe("the list", () => {
  it("shows whose request it is, the amount, both dates, the reason and when it was made", async () => {
    show(shop());
    const row = await waitFor(aliRow);
    expect(within(row).getByRole("link", { name: /Ali Valiyev/ }).getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}`);
    expect(within(row).getByText(exact("45 000 so'm"))).toBeTruthy();
    expect(within(row).getByText("Hozirgi muddat: 2026-yil 5-noyabr. So'ralgan muddat: 2026-yil 20-noyabr.")).toBeTruthy();
    expect(within(row).getByText("Mijoz sababi: Oylik kechikdi")).toBeTruthy();
    expect(within(row).getByText("So'rov vaqti: 2026-yil 6-oktabr, 10:10")).toBeTruthy();
    expect(within(row).getAllByRole("button").map((button) => button.textContent)).toEqual(["Qabul qilish", "Rad etish"]);
    // A request with no reason shows none.
    expect(screen.getAllByText(/Mijoz sababi/)).toHaveLength(1);
  });

  it("leads back to the disputes, which share the section", async () => {
    show(shop());
    await screen.findByText("Ali Valiyev");
    expect(screen.getByRole("link", { name: "E'tirozlar" }).getAttribute("href")).toBe("#/disputes");
  });

  it("says so when there is none, and offers a retry when the list cannot be read", async () => {
    show(shop([]));
    expect(await screen.findByText("Ochiq so'rov yo'q.")).toBeTruthy();
    cleanup();

    let fail = true;
    const server = fakeServer(() => (fail ? "offline" : ok({ items: [OTHER] })));
    show(server);
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Vali Aliyev")).toBeTruthy();
  });
});

describe("accepting", () => {
  it("asks first, sends nothing on no, and one request with a key on yes", async () => {
    const server = shop();
    show(server);
    click(await waitFor(aliRow), "Qabul qilish");
    expect(screen.getByText(exact("Ali Valiyev: 45 000 so'm yozuvining muddati 2026-yil 20-noyabr ga ko'chirilsinmi?"))).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    click(aliRow(), "Bekor qilish");
    expect(server.writes()).toHaveLength(0);
    expect(within(aliRow()).getByRole("button", { name: "Qabul qilish" })).toBeTruthy();

    click(aliRow(), "Qabul qilish");
    server.held.items = [OTHER];
    click(aliRow(), "Ha, ko'chirilsin");
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("So'rov qabul qilindi: muddat ko'chirildi."));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${PATH}/${DATE_REQUEST_ID}/accept` });
    expect(server.writes()[0]?.body).toBeUndefined();
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    await waitFor(() => expect(screen.queryByText("Ali Valiyev")).toBeNull());
    expect(screen.getByText("Vali Aliyev")).toBeTruthy();
  });

  it("disables every answer while one is in flight, so a double tap sends one", async () => {
    const gate = deferred<ReturnType<typeof ok>>();
    const server = shop(undefined, () => gate.promise);
    show(server);
    click(await waitFor(aliRow), "Qabul qilish");
    const yes = within(aliRow()).getByRole("button", { name: "Ha, ko'chirilsin" });
    fireEvent.click(yes);
    fireEvent.click(yes);
    expect((await screen.findByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement).disabled).toBe(true);
    const other = rows().find((row) => within(row).queryByText("Vali Aliyev") !== null) as HTMLElement;
    expect(within(other).getAllByRole("button").every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
    gate.resolve(ok({}));
    await screen.findByRole("status");
    expect(server.writes()).toHaveLength(1);
  });

  it("shows the server's refusal, keeps the question open, and resends the same key", async () => {
    const server = shop(undefined, () => refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan."));
    show(server);
    click(await waitFor(aliRow), "Qabul qilish");
    click(aliRow(), "Ha, ko'chirilsin");
    expect((await screen.findByRole("alert")).textContent).toContain("Do'kon to'xtatilgan.");
    click(aliRow(), "Ha, ko'chirilsin");
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]?.headers["Idempotency-Key"]).toBe(server.writes()[0]?.headers["Idempotency-Key"]);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("says a request someone else answered first is closed, and reads the list again", async () => {
    const general = "Bu yozuv muddatini ko'chirish so'rovini hozir qabul qilib bo'lmaydi.";
    const server = shop(undefined, () => refusal(409, "DATE_REQUEST_NOT_ALLOWED", general, { reason: "not_open" }));
    show(server);
    click(await waitFor(aliRow), "Qabul qilish");
    server.held.items = [OTHER];
    click(aliRow(), "Ha, ko'chirilsin");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(general);
    expect(alert.textContent).toContain("Bu so'rov allaqachon yopilgan: ro'yxat yangilandi.");
    await waitFor(() => expect(screen.queryByText("Ali Valiyev")).toBeNull());
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("declining", () => {
  const REASON = "Rad etish sababi (ixtiyoriy)";

  it("asks first with an optional reason, and sends nothing until the answer", async () => {
    const server = shop();
    show(server);
    click(await waitFor(aliRow), "Rad etish");
    expect(screen.getByText("Ali Valiyev: muddatni 2026-yil 20-noyabr ga ko'chirish so'rovi rad etilsinmi?")).toBeTruthy();
    expect(screen.getByText("Yozilsa, mijozga yuboriladi. 300 belgigacha.")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    click(aliRow(), "Bekor qilish");
    expect(screen.queryByLabelText(REASON)).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it("declines without a reason: an empty body, one request, a key", async () => {
    const server = shop();
    show(server);
    click(await waitFor(aliRow), "Rad etish");
    fireEvent.change(screen.getByLabelText(REASON), { target: { value: "   " } });
    click(aliRow(), "Ha, rad etilsin");
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("So'rov rad etildi."));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${PATH}/${DATE_REQUEST_ID}/decline`, body: {} });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toBeTruthy();
  });

  it("sends the tidied reason when one is written", async () => {
    const server = shop();
    show(server);
    click(await waitFor(aliRow), "Rad etish");
    fireEvent.change(screen.getByLabelText(REASON), { target: { value: " Muddat  juda uzoq " } });
    click(aliRow(), "Ha, rad etilsin");
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ reason: "Muddat juda uzoq" });
  });

  it("sends nothing while the reason is longer than 300 characters", async () => {
    const server = shop();
    show(server);
    click(await waitFor(aliRow), "Rad etish");
    fireEvent.change(screen.getByLabelText(REASON), { target: { value: "a".repeat(301) } });
    click(aliRow(), "Ha, rad etilsin");
    expect(screen.getByText("Sabab 300 belgidan oshmasligi kerak.")).toBeTruthy();
    expect(screen.getByLabelText(REASON).getAttribute("aria-invalid")).toBe("true");
    expect(server.writes()).toHaveLength(0);
  });

  it("shows the server's refusal of the reason next to it, and any other refusal above the form", async () => {
    let fields: Record<string, string> = { reason: "at most 300 characters" };
    const server = shop(undefined, () =>
      "reason" in fields
        ? refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", fields)
        : refusal(404, "NOT_FOUND", "Topilmadi."),
    );
    show(server);
    click(await waitFor(aliRow), "Rad etish");
    click(aliRow(), "Ha, rad etilsin");
    expect((await screen.findByText("Sabab 300 belgidan oshmasligi kerak.")).id).toBe(`decline-date-${DATE_REQUEST_ID}-error`);
    fields = {};
    fireEvent.change(screen.getByLabelText(REASON), { target: { value: "boshqa" } });
    click(aliRow(), "Ha, rad etilsin");
    expect((await screen.findByRole("alert")).textContent).toContain("Topilmadi.");
  });

  it("answers one request at a time: opening another question closes the first", async () => {
    show(shop());
    click(await waitFor(aliRow), "Rad etish");
    const other = rows().find((row) => within(row).queryByText("Vali Aliyev") !== null) as HTMLElement;
    click(other, "Qabul qilish");
    expect(screen.queryByLabelText(REASON)).toBeNull();
    expect(screen.getAllByRole("button", { name: "Ha, ko'chirilsin" })).toHaveLength(1);
  });
});
