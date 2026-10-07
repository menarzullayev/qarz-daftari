// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  creditSettingsBody,
  CUSTOMER_ID,
  customerBody,
  deferred,
  detailBody,
  fakeServer,
  linkBody,
  NO_OVERDUE,
  NOON,
  NOTICE_ID,
  noticeBody,
  ok,
  openNoticeBody,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
  SHOP_ID,
} from "../../testing/fakeServer";
import { exact, go, renderScreen } from "../../testing/renderScreen";
import type { ApiAuth } from "../api";
import type { Role } from "../navigation";
import { StaffRoot } from "../StaffRoot";
import { CustomerScreen } from "./CustomerScreen";
import PaymentNoticesScreen from "./PaymentNoticesScreen";

beforeEach(() => {
  window.location.hash = "";
});
afterEach(cleanup);

const PATH = `${SHOP_BASE}/payment-notices`;
const OTHER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab";
const OTHER = openNoticeBody({
  id: OTHER_ID,
  customer_id: "11111111-1111-4111-8111-111111111112",
  customer_name: "Vali Aliyev",
  amount: 30000,
  customer_balance: 30000,
  has_receipt: false,
});
const LINK = { url: "/files/abc.def", expires_at: "2026-10-06T07:05:00+00:00" };

function shop(items: unknown[] = [openNoticeBody(), OTHER], onWrite: (sent: Sent) => Reply = () => ok({})) {
  const held = { items, link: ok(LINK) as Reply };
  const server = fakeServer((sent) => {
    if (sent.method !== "GET") {
      return onWrite(sent);
    }
    return sent.path.endsWith("/receipt") ? held.link : ok({ items: held.items });
  });
  return { ...server, held };
}

const show = (server: ReturnType<typeof fakeServer>, role: Role = "seller") =>
  renderScreen(<PaymentNoticesScreen />, { fetch: server.fetch, role });
const rows = () => screen.getAllByRole("listitem");
const row = (name: string) => rows().find((item) => within(item).queryByText(name) !== null) as HTMLElement;
const ali = () => row("Ali Valiyev");
const click = (scope: HTMLElement, name: string) => fireEvent.click(within(scope).getByRole("button", { name }));

describe("the list of open notices", () => {
  it.each(["seller", "manager", "owner"] as const)("is every member of staff's: a %s reads and decides", async (role) => {
    const server = shop();
    show(server, role);
    await screen.findByText("Ali Valiyev");
    expect(server.sent[0]).toMatchObject({ method: "GET", path: PATH });
    expect(within(ali()).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Chek havolasini olish",
      "Qabul qilish",
      "Rad etish",
    ]);
  });

  it("shows whose notice it is, the stated amount, what they owe, when it was sent and until when it waits", async () => {
    show(shop());
    const item = await waitFor(ali);
    expect(within(item).getByRole("link", { name: /Ali Valiyev/ }).getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}`);
    expect(within(item).getByText(exact("Xabar qilingan summa: 50 000 so'm. Mijozning qarzi: 120 000 so'm."))).toBeTruthy();
    expect(within(item).getByText("Yuborilgan: 2026-yil 6-oktabr, 10:10. Javob 2026-yil 20-oktabr, 10:10 gacha kutiladi.")).toBeTruthy();
    expect(within(row("Vali Aliyev")).getByText("Chek biriktirilmagan.")).toBeTruthy();
    expect(within(row("Vali Aliyev")).queryByRole("button", { name: "Chek havolasini olish" })).toBeNull();
  });

  it("warns when the same receipt was sent to the shop before, and only then", async () => {
    show(shop([openNoticeBody({ receipt_seen_before: true }), OTHER]));
    const warning = "Diqqat: aynan shu chek fayli bu do'konga avval ham yuborilgan.";
    expect(within(await waitFor(ali)).getByText(warning)).toBeTruthy();
    expect(screen.getAllByText(warning)).toHaveLength(1);
  });

  it("says so when there is none, and offers a retry when the list cannot be read", async () => {
    show(shop([]));
    expect(await screen.findByText("Ko'rib chiqiladigan xabar yo'q.")).toBeTruthy();
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

describe("opening a receipt, in two steps", () => {
  it("asks for nothing until the button is pressed, then shows a link that opens in a new tab", async () => {
    const server = shop();
    show(server);
    await screen.findByText("Ali Valiyev");
    expect(server.sent.filter((sent) => sent.path.endsWith("/receipt"))).toHaveLength(0);
    expect(screen.queryByRole("link", { name: "Chekni ochish" })).toBeNull();

    click(ali(), "Chek havolasini olish");
    const link = await screen.findByRole("link", { name: "Chekni ochish" });
    expect(server.sent.at(-1)).toMatchObject({ method: "GET", path: `${PATH}/${NOTICE_ID}/receipt` });
    // The session proves who asks for the link; the link itself is opened by the browser, without it.
    expect(server.sent.at(-1)?.headers["Authorization"]).toBe("Bearer test-session");
    expect([link.getAttribute("href"), link.getAttribute("target"), link.getAttribute("rel")]).toEqual([
      "/files/abc.def",
      "_blank",
      "noopener noreferrer",
    ]);
    expect(within(ali()).getByText("Havola 2026-yil 6-oktabr, 12:05 gacha amal qiladi. Ochilmasa, yangisini oling.")).toBeTruthy();
  });

  it("asks again for a new link when the first has run out", async () => {
    const server = shop();
    show(server);
    click(await waitFor(ali), "Chek havolasini olish");
    await screen.findByRole("link", { name: "Chekni ochish" });
    server.held.link = ok({ url: "/files/new.link", expires_at: "2026-10-06T07:15:00+00:00" });
    click(ali(), "Yangi havola olish");
    await waitFor(() => expect(screen.getByRole("link", { name: "Chekni ochish" }).getAttribute("href")).toBe("/files/new.link"));
    expect(server.sent.filter((sent) => sent.path.endsWith("/receipt"))).toHaveLength(2);
  });

  it("keeps the link out of the address and out of storage", async () => {
    const server = shop();
    show(server);
    click(await waitFor(ali), "Chek havolasini olish");
    await screen.findByRole("link", { name: "Chekni ochish" });
    expect(window.location.href).not.toContain("abc.def");
    expect(JSON.stringify({ ...window.localStorage, ...window.sessionStorage })).not.toContain("abc.def");
  });

  it("shows the refusal when the receipt is gone, and the button stays to ask again", async () => {
    const server = shop();
    server.held.link = refusal(404, "NOT_FOUND", "Topilmadi.");
    show(server);
    click(await waitFor(ali), "Chek havolasini olish");
    expect((await within(ali()).findByRole("alert")).textContent).toContain("Topilmadi.");
    expect(screen.queryByRole("link", { name: "Chekni ochish" })).toBeNull();
    expect(within(ali()).getByRole("button", { name: "Chek havolasini olish" })).toBeTruthy();
  });
});

describe("accepting", () => {
  const AMOUNT = "Yoziladigan to'lov summasi, so'm";

  it("asks first with the stated amount filled in, and sends nothing until the answer", async () => {
    const server = shop();
    show(server);
    click(await waitFor(ali), "Qabul qilish");
    expect(screen.getByText("Ali Valiyev uchun to'lov yozilsinmi? Mijozning qarzi shu summaga kamayadi.")).toBeTruthy();
    expect((screen.getByLabelText(AMOUNT) as HTMLInputElement).value).toBe("50000");
    expect(screen.getByText(exact("Mijoz 50 000 so'm deb yozgan. Boshqa summa olgan bo'lsangiz, to'g'rilang."))).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    click(ali(), "Bekor qilish");
    expect(screen.queryByLabelText(AMOUNT)).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it("records the stated amount with an empty body and a key, then says so and reads the list again", async () => {
    const server = shop();
    show(server);
    click(await waitFor(ali), "Qabul qilish");
    server.held.items = [OTHER];
    click(ali(), "Ha, to'lov yozilsin");
    expect((await screen.findByRole("status")).textContent).toBe("To'lov yozildi: 50 000 so'm.");
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${PATH}/${NOTICE_ID}/accept` });
    // No amount: the stated one is the server's to record. An amount here would be a correction.
    expect(server.writes()[0]?.body).toEqual({});
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    await waitFor(() => expect(screen.queryByText("Ali Valiyev")).toBeNull());
  });

  it("sends a corrected amount as the amount", async () => {
    const server = shop();
    show(server);
    click(await waitFor(ali), "Qabul qilish");
    fireEvent.change(screen.getByLabelText(AMOUNT), { target: { value: "45 000" } });
    click(ali(), "Ha, to'lov yozilsin");
    expect((await screen.findByRole("status")).textContent).toBe("To'lov yozildi: 45 000 so'm.");
    expect(server.writes()[0]?.body).toEqual({ amount: 45000 });
  });

  it.each([
    ["", "Summani kiriting."],
    ["45,5", "Summa butun so'mda bo'lishi kerak, tiyinsiz."],
    ["50", "Summa kamida 100 so'm bo'lishi kerak."],
  ])("sends nothing for the amount %j", async (amount, message) => {
    const server = shop();
    show(server);
    click(await waitFor(ali), "Qabul qilish");
    fireEvent.change(screen.getByLabelText(AMOUNT), { target: { value: amount } });
    click(ali(), "Ha, to'lov yozilsin");
    expect(screen.getByText(message)).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
  });

  it("sends nothing for more than the customer owes", async () => {
    const server = shop();
    show(server);
    click(await waitFor(ali), "Qabul qilish");
    fireEvent.change(screen.getByLabelText(AMOUNT), { target: { value: "120001" } });
    click(ali(), "Ha, to'lov yozilsin");
    expect(screen.getByText(exact("Summa mijozning qarzidan (120 000 so'm) katta bo'lishi mumkin emas."))).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
  });

  it("puts the server's refusal of a payment above the debt next to the amount, and resends the same key", async () => {
    const message = "To'lov mijozning qarzidan katta bo'lishi mumkin emas.";
    const server = shop(undefined, () => refusal(409, "EXCEEDS_BALANCE", message));
    show(server);
    click(await waitFor(ali), "Qabul qilish");
    click(ali(), "Ha, to'lov yozilsin");
    expect((await screen.findByText(message)).id).toBe(`accept-${NOTICE_ID}-error`);
    click(ali(), "Ha, to'lov yozilsin");
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]?.headers["Idempotency-Key"]).toBe(server.writes()[0]?.headers["Idempotency-Key"]);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("disables every answer while one is in flight, so a double tap sends one", async () => {
    const gate = deferred<ReturnType<typeof ok>>();
    const server = shop(undefined, () => gate.promise);
    show(server);
    click(await waitFor(ali), "Qabul qilish");
    const yes = within(ali()).getByRole("button", { name: "Ha, to'lov yozilsin" });
    fireEvent.click(yes);
    fireEvent.click(yes);
    expect(((await screen.findByRole("button", { name: "Saqlanmoqda…" })) as HTMLButtonElement).disabled).toBe(true);
    const others = within(row("Vali Aliyev")).getAllByRole("button");
    expect(others.every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
    gate.resolve(ok({}));
    await screen.findByRole("status");
    expect(server.writes()).toHaveLength(1);
  });

  it("says a notice someone else decided, or that expired, is closed, and reads the list again", async () => {
    const general = "Bu to'lov xabari allaqachon ko'rib chiqilgan yoki muddati o'tgan.";
    const server = shop(undefined, () => refusal(409, "PAYMENT_NOTICE_NOT_OPEN", general, { reason: "accepted" }));
    show(server);
    click(await waitFor(ali), "Qabul qilish");
    server.held.items = [OTHER];
    click(ali(), "Ha, to'lov yozilsin");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(general);
    expect(alert.textContent).toContain("ro'yxat yangilandi");
    await waitFor(() => expect(screen.queryByText("Ali Valiyev")).toBeNull());
  });

  it("shows any other refusal with the server's message", async () => {
    const server = shop(undefined, () => refusal(409, "CUSTOMER_ARCHIVED", "Bu mijoz arxivda. Avval arxivdan chiqaring."));
    show(server);
    click(await waitFor(ali), "Qabul qilish");
    click(ali(), "Ha, to'lov yozilsin");
    expect((await screen.findByRole("alert")).textContent).toContain("Bu mijoz arxivda.");
  });
});

describe("declining", () => {
  const REASON = "Rad etish sababi";

  it("needs a reason of 3 to 300 characters before anything is sent", async () => {
    const server = shop();
    show(server);
    click(await waitFor(ali), "Rad etish");
    expect(screen.getByText("Bu sabab mijozga yuboriladi. 3 dan 300 belgigacha.")).toBeTruthy();
    click(ali(), "Rad etib, mijozga yuborish");
    expect(screen.getByText("Sabab 3 dan 300 belgigacha bo'lishi kerak.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(REASON), { target: { value: "yo" } });
    click(ali(), "Rad etib, mijozga yuborish");
    expect(server.writes()).toHaveLength(0);
    click(ali(), "Bekor qilish");
    expect(server.writes()).toHaveLength(0);
  });

  it("sends the tidied reason with a key, then says the customer is told", async () => {
    const server = shop();
    show(server);
    click(await waitFor(ali), "Rad etish");
    fireEvent.change(screen.getByLabelText(REASON), { target: { value: "  Pul   kelmadi " } });
    click(ali(), "Rad etib, mijozga yuborish");
    expect((await screen.findByRole("status")).textContent).toBe("Xabar rad etildi. Sabab mijozga yuborildi.");
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${PATH}/${NOTICE_ID}/decline`, body: { reason: "Pul kelmadi" } });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toBeTruthy();
  });

  it("opens one question at a time", async () => {
    show(shop());
    click(await waitFor(ali), "Rad etish");
    click(row("Vali Aliyev"), "Qabul qilish");
    expect(screen.queryByLabelText(REASON)).toBeNull();
    expect(screen.getAllByRole("button", { name: "Ha, to'lov yozilsin" })).toHaveLength(1);
  });
});

describe("reaching the notices", () => {
  const bearer = async (): Promise<ApiAuth> => ({ kind: "bearer", token: "session-token" });

  it("opens for a seller from the customer book, inside the customers section", async () => {
    const server = fakeServer((sent) => {
      if (sent.path === "/api/v1/me/shops") {
        return ok({ items: [{ shop_id: SHOP_ID, name: "Baraka savdo", role: "seller", membership_id: null }], active_shop: SHOP_ID });
      }
      if (sent.path === PATH) {
        return ok({ items: [openNoticeBody()] });
      }
      if (sent.path.endsWith("/overview")) {
        return ok({ outstanding: 0, debtors: 0, overdue: { amount: 0, customers: 0 }, due_today: 0 });
      }
      return ok({ items: [customerBody()], next_cursor: null });
    });
    window.location.hash = "#/customers";
    render(<StaffRoot entryKey="entry.app" initialLanguage="uz" connect={bearer} fetch={server.fetch} now={() => NOON} />);
    const link = await screen.findByRole("link", { name: "To'lov xabarlari" });
    expect(link.getAttribute("href")).toBe("#/payment-notices");
    go("#/payment-notices");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("To'lov xabarlari");
    expect(await screen.findByText("Ali Valiyev")).toBeTruthy();
    const current = within(screen.getAllByRole("navigation")[0] as HTMLElement)
      .getAllByRole("link")
      .filter((candidate) => candidate.getAttribute("aria-current") === "page");
    expect(current.map((candidate) => candidate.textContent)).toEqual(["Mijozlar"]);
  });

  it("is pointed to from the customer card when that customer's notices wait, and not otherwise", async () => {
    const card = (notices: unknown[]) =>
      fakeServer((sent) => {
        if (sent.path.endsWith("/credit-settings")) {
          return ok(creditSettingsBody());
        }
        return sent.path.endsWith("/link") ? ok(linkBody()) : ok(detailBody({ overdue: NO_OVERDUE, payment_notices: notices }));
      });
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: card([noticeBody(), noticeBody({ id: "n2" })]).fetch, role: "seller" });
    await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
    const note = screen.getByText("Mijozning 2 ta to'lov xabari javob kutmoqda.").parentElement as HTMLElement;
    expect(within(note).getByRole("link", { name: "To'lov xabarlari" }).getAttribute("href")).toBe("#/payment-notices");
    cleanup();
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: card([]).fetch, role: "seller" });
    await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
    expect(screen.queryByText(/to'lov xabari javob kutmoqda/)).toBeNull();
  });
});
