// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  CUSTOMER_ID,
  customerBody,
  deferred,
  DISPUTE_ID,
  fakeServer,
  ok,
  openDisputeBody,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
} from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { Role } from "../navigation";
import { DisputesScreen } from "./DisputesScreen";

afterEach(cleanup);

const ENTRY_ID = "22222222-2222-4222-8222-222222222222";
const SECOND = openDisputeBody({
  id: "88888888-8888-4888-8888-888888888889",
  entry_id: "22222222-2222-4222-8222-222222222223",
  customer_id: "11111111-1111-4111-8111-111111111112",
  customer_name: "Vali Toshev",
  amount: 12000,
  reason: "Summa noto'g'ri",
});

function shop(disputes: () => unknown[], onWrite: (sent: Sent, attempt: number) => Reply = () => ok({})) {
  let attempt = 0;
  return fakeServer((sent) => (sent.method === "GET" ? ok({ items: disputes() }) : onWrite(sent, attempt++)));
}

async function open(server: ReturnType<typeof fakeServer>, role: Role = "manager") {
  renderScreen(<DisputesScreen />, { fetch: server.fetch, role });
  await waitFor(() => expect(screen.queryByText("Yuklanmoqda…")).toBeNull());
}

const rows = () => screen.getAllByRole("listitem");
const row = (name: string) => rows().find((item) => item.textContent?.includes(name)) as HTMLElement;
const reasonField = () => screen.getByRole("textbox", { name: "Rad etish sababi" });

describe("open disputes", () => {
  it.each(["manager", "owner"] as Role[])("shows a %s the customer, the amount, the reason and since when", async (role) => {
    const server = shop(() => [openDisputeBody(), SECOND]);
    await open(server, role);
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/disputes` });
    expect(rows()).toHaveLength(2);
    const first = row("Ali Valiyev");
    expect(within(first).getByRole("link", { name: /Ali Valiyev/ }).getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}`);
    expect(first.textContent).toContain("45 000 so'm");
    expect(first.textContent).toContain("Mijoz sababi: Men bu tovarni olmaganman");
    expect(first.textContent).toContain("E'tiroz vaqti: 2026-yil 6-oktabr, 10:10");
    expect(within(first).getByRole("button", { name: "Yozuvni bekor qilish" })).toBeTruthy();
    expect(within(first).getByRole("button", { name: "E'tirozni rad etish" })).toBeTruthy();
  });

  it("shows a seller no list and asks the server for nothing", async () => {
    const server = shop(() => [openDisputeBody()]);
    renderScreen(<DisputesScreen />, { fetch: server.fetch, role: "seller" });
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    await Promise.resolve();
    expect(server.sent).toHaveLength(0);
    expect(screen.queryByText("Ali Valiyev")).toBeNull();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("says when there is no open dispute", async () => {
    await open(shop(() => []));
    expect(screen.getByText("Ochiq e'tiroz yo'q.")).toBeTruthy();
  });

  it("shows the server's message when the list cannot be read, and retries", async () => {
    let fail = true;
    const server = fakeServer(() =>
      fail ? refusal(403, "FORBIDDEN_ROLE", "Bu amal uchun sizning rolingiz yetarli emas.") : ok({ items: [openDisputeBody()] }),
    );
    await open(server);
    expect(screen.getByRole("alert").textContent).toContain("Bu amal uchun sizning rolingiz yetarli emas.");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Ali Valiyev")).toBeTruthy();
  });
});

describe("reversing a disputed entry", () => {
  it("asks first, then posts one keyed reversal of that entry and reads the list again", async () => {
    let reversed = false;
    const server = shop(
      () => (reversed ? [SECOND] : [openDisputeBody(), SECOND]),
      () => {
        reversed = true;
        return ok({ entry: { id: "r1", kind: "reversal", amount: 45000 }, customer: customerBody({ balance: 75000 }) }, 201);
      },
    );
    await open(server);
    fireEvent.click(within(row("Ali Valiyev")).getByRole("button", { name: "Yozuvni bekor qilish" }));
    expect(server.writes()).toHaveLength(0);
    expect(row("Ali Valiyev").textContent).toContain("Bu yozuv (45 000 so'm) bekor qilinsinmi?");

    fireEvent.click(screen.getByRole("button", { name: "Ha, bekor qilinsin" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Yozuv bekor qilindi, e'tiroz yopildi."));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/entries/${ENTRY_ID}/reversal` });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.queryByText("Ali Valiyev")).toBeNull();
  });

  it("sends nothing when the answer is no", async () => {
    const server = shop(() => [openDisputeBody()]);
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Yozuvni bekor qilish" }));
    fireEvent.click(screen.getByRole("button", { name: "Yo'q, qolsin" }));
    expect(screen.getByRole("button", { name: "Yozuvni bekor qilish" })).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
  });

  it.each([
    [409, "WOULD_GO_NEGATIVE", "Bekor qilinsa qarz manfiy bo'lib qoladi. Avval keyingi to'lovni bekor qiling."],
    [409, "ALREADY_REVERSED", "Bu yozuv allaqachon bekor qilingan."],
    [403, "FORBIDDEN_ROLE", "Bu amal uchun sizning rolingiz yetarli emas."],
  ])("shows the server's refusal %i %s and keeps the dispute", async (status, code, message) => {
    const server = shop(() => [openDisputeBody()], () => refusal(status, code, message));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Yozuvni bekor qilish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, bekor qilinsin" }));
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByText("Ali Valiyev")).toBeTruthy();
  });
});

describe("declining a dispute", () => {
  async function declineWith(server: ReturnType<typeof fakeServer>, reason: string) {
    await open(server);
    fireEvent.click(within(row("Ali Valiyev")).getByRole("button", { name: "E'tirozni rad etish" }));
    fireEvent.change(reasonField(), { target: { value: reason } });
    fireEvent.click(screen.getByRole("button", { name: "Rad etib, mijozga yuborish" }));
  }

  it("says the reason goes to the customer, then posts one keyed decline with the tidied reason", async () => {
    let declined = false;
    const server = shop(
      () => (declined ? [] : [openDisputeBody()]),
      () => {
        declined = true;
        return ok(openDisputeBody({ status: "declined", decline_reason: "Tovar berilgan, imzo bor" }));
      },
    );
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "E'tirozni rad etish" }));
    expect(screen.getByText("Bu sabab mijozga yuboriladi. 3 dan 300 belgigacha.")).toBeTruthy();
    fireEvent.change(reasonField(), { target: { value: "  Tovar berilgan,\n imzo   bor " } });
    fireEvent.click(screen.getByRole("button", { name: "Rad etib, mijozga yuborish" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("E'tiroz rad etildi. Sabab mijozga yuborildi."));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({
      method: "POST",
      path: `${SHOP_BASE}/disputes/${DISPUTE_ID}/decline`,
      body: { reason: "Tovar berilgan, imzo bor" },
    });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(await screen.findByText("Ochiq e'tiroz yo'q.")).toBeTruthy();
  });

  it.each([
    ["no reason", ""],
    ["two characters", "yo"],
    ["two characters padded with spaces", "   yo 	   "],
    ["301 characters", "x".repeat(301)],
  ])("refuses %s before anything is sent", async (_what, reason) => {
    const server = shop(() => [openDisputeBody()]);
    await declineWith(server, reason);
    expect(screen.getByRole("alert").textContent).toBe("Sabab 3 dan 300 belgigacha bo'lishi kerak.");
    expect(reasonField().getAttribute("aria-invalid")).toBe("true");
    expect(server.writes()).toHaveLength(0);
  });

  it.each([
    ["three characters", "yo'"],
    ["300 characters", "x".repeat(300)],
  ])("accepts %s", async (_what, reason) => {
    const server = shop(() => [openDisputeBody()]);
    await declineWith(server, reason);
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ reason });
  });

  it("shows the server's refusal and keeps the text; a retry of the same text reuses the key", async () => {
    const server = shop(
      () => [openDisputeBody()],
      (_sent, attempt) =>
        attempt === 0
          ? refusal(409, "DISPUTE_NOT_ALLOWED", "Bu yozuv bo'yicha e'tiroz bildirib bo'lmaydi yoki u allaqachon ko'rib chiqilgan.", {
              reason: "not_open",
            })
          : attempt === 1
            ? "offline"
            : ok({}),
    );
    await declineWith(server, "Imzo bor");
    expect((await screen.findByRole("alert")).textContent).toContain("allaqachon ko'rib chiqilgan.");
    expect((reasonField() as HTMLTextAreaElement).value).toBe("Imzo bor");
    expect(screen.queryByRole("status")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Rad etib, mijozga yuborish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    await screen.findByText("Serverga ulanib bo'lmadi. Internetni tekshirib, qayta urinib ko'ring.");
    fireEvent.click(screen.getByRole("button", { name: "Rad etib, mijozga yuborish" }));
    await screen.findByRole("status");
    const keys = server.writes().map((sent) => sent.headers["Idempotency-Key"]);
    expect(new Set(keys).size).toBe(1);
  });

  it("sends one request for a double tap and disables the form while it is pending", async () => {
    const held = deferred<Reply>();
    const server = shop(() => [openDisputeBody()], () => held.promise as Reply);
    await declineWith(server, "Imzo bor");
    const pending = screen.getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement;
    expect(pending.disabled).toBe(true);
    fireEvent.click(pending);
    fireEvent.submit(pending.closest("form") as HTMLFormElement);
    held.resolve(ok({}));
    await screen.findByRole("status");
    expect(server.writes()).toHaveLength(1);
  });

  it("closes the form on cancel and sends nothing", async () => {
    const server = shop(() => [openDisputeBody()]);
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "E'tirozni rad etish" }));
    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });
});
