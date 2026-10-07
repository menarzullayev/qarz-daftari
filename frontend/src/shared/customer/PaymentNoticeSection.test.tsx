// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { I18nProvider } from "../../i18n/I18nProvider";
import type { Language } from "../../i18n/types";
import {
  accountBody,
  accountEntryBody,
  deferred,
  fakeServer,
  LINK_ID,
  ME_BASE,
  NOON,
  noticeBody,
  ok,
  refusal,
  type Reply,
  type Sent,
} from "../../testing/fakeServer";
import { exact } from "../../testing/renderScreen";
import { createApi, RECEIPT_MAX_BYTES } from "../api";
import { AccountScreen } from "./AccountScreen";
import { MAX_OPEN_NOTICES, receiptProblem } from "./PaymentNoticeSection";

afterEach(cleanup);

const ASK = "To'ladim";
const AMOUNT = "To'langan summa, so'm";
const RECEIPT = "Chek (ixtiyoriy)";
const png = (size = 4) => new File([new Uint8Array(size)], "chek.png", { type: "image/png" });

/** The customer's account owing 120 000, with the given notices; `onWrite` answers a sent notice. */
function backend(notices: unknown[] = [], onWrite: (sent: Sent) => Reply = () => ok(noticeBody(), 201), balance = 120000) {
  const held = { notices };
  const server = fakeServer((sent) =>
    sent.method === "GET" ? ok(accountBody({ balance, entries: [accountEntryBody()], payment_notices: held.notices })) : onWrite(sent),
  );
  return { ...server, held };
}

async function open(server: ReturnType<typeof fakeServer>, language: Language = "uz") {
  const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "customer-session" } }).account(LINK_ID);
  const view = render(
    <I18nProvider initialLanguage={language}>
      <AccountScreen api={api} back={<a href="#/my">back</a>} now={() => NOON} />
    </I18nProvider>,
  );
  await screen.findByRole("heading", { level: 2, name: "Baraka savdo" });
  return view;
}

const section = () => screen.getByRole("region", { name: /To'lov xabarlarim|Мои сообщения об оплате/ });
const notices = (server: ReturnType<typeof fakeServer>) => server.writes().filter((sent) => sent.path === `${ME_BASE}/payment-notices`);

function fill(amount: string, file: File | null = null) {
  fireEvent.click(screen.getByRole("button", { name: ASK }));
  fireEvent.change(screen.getByLabelText(AMOUNT), { target: { value: amount } });
  if (file) {
    fireEvent.change(screen.getByLabelText(RECEIPT), { target: { files: [file] } });
  }
  fireEvent.click(screen.getByRole("button", { name: "Xabarni yuborish" }));
}

describe("when \"I have paid\" is offered", () => {
  it("is offered while something is owed", async () => {
    await open(backend());
    expect(within(section()).getByRole("button", { name: ASK })).toBeTruthy();
  });

  it("is not offered, and the section is not drawn, when nothing is owed and nothing was ever sent", async () => {
    await open(backend([], undefined, 0));
    expect(screen.queryByRole("button", { name: ASK })).toBeNull();
    expect(screen.queryByText("To'lov xabarlarim")).toBeNull();
  });

  it("is not offered while three notices wait, and says why", async () => {
    expect(MAX_OPEN_NOTICES).toBe(3);
    const waiting = [1, 2, 3].map((n) => noticeBody({ id: `aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa${n}` }));
    await open(backend(waiting));
    expect(screen.queryByRole("button", { name: ASK })).toBeNull();
    expect(within(section()).getByText(/Javob kutayotgan xabarlaringiz 3 ta/)).toBeTruthy();
  });

  it("is offered again when one of the three is no longer waiting", async () => {
    const mixed = [noticeBody({ id: "a1" }), noticeBody({ id: "a2" }), noticeBody({ id: "a3", status: "declined", decline_reason: "Pul kelmadi" })];
    await open(backend(mixed));
    expect(screen.getByRole("button", { name: ASK })).toBeTruthy();
  });
});

describe("the form", () => {
  it("has a labelled amount with what is owed as its limit, an optional receipt of the accepted kinds, and sends nothing yet", async () => {
    const server = backend();
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: ASK }));
    expect(screen.getByLabelText(AMOUNT).getAttribute("aria-describedby")).toContain("notice-amount-hint");
    expect(screen.getByText(exact("Ko'pi bilan qarzingiz: 120 000 so'm."))).toBeTruthy();
    const receipt = screen.getByLabelText(RECEIPT) as HTMLInputElement;
    expect([receipt.type, receipt.accept, receipt.required]).toEqual(["file", "image/jpeg,image/png,image/webp,application/pdf", false]);
    expect(screen.getByText("JPEG, PNG yoki WebP rasm, yoki PDF; 5 MB gacha.")).toBeTruthy();
    expect(screen.getByText(/Qarzingiz do'kon xodimi xabarni ko'rib chiqqanidan keyingina kamayadi/)).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    expect(screen.queryByLabelText(AMOUNT)).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it.each([
    ["", "Summani kiriting."],
    ["45,5", "Summa butun so'mda bo'lishi kerak, tiyinsiz."],
    ["50", "Summa kamida 100 so'm bo'lishi kerak."],
    ["abc", "Summani tushunib bo'lmadi. Faqat raqam yozing, masalan 45000."],
    ["120001", "To'lov mijozning qarzidan katta bo'lishi mumkin emas."],
  ])("sends nothing for the amount %j and says why next to the field", async (amount, message) => {
    const server = backend();
    await open(server);
    fill(amount);
    expect(screen.getByText(message).id).toBe("notice-amount-error");
    expect(screen.getByLabelText(AMOUNT).getAttribute("aria-invalid")).toBe("true");
    expect(server.writes()).toHaveLength(0);
  });

  it("accepts exactly what is owed", async () => {
    const server = backend();
    await open(server);
    fill("120 000");
    await waitFor(() => expect(notices(server)).toHaveLength(1));
    expect(notices(server)[0]?.body).toEqual({ amount: 120000 });
  });

  it("checks a receipt before sending: empty, larger than 5 MB, or not an image or a PDF", () => {
    expect(receiptProblem({ size: 0, type: "image/png" })).toBe("empty");
    expect(receiptProblem({ size: RECEIPT_MAX_BYTES + 1, type: "image/png" })).toBe("too_large");
    expect(receiptProblem({ size: RECEIPT_MAX_BYTES, type: "application/pdf" })).toBeNull();
    expect(receiptProblem({ size: 10, type: "image/gif" })).toBe("type");
    expect(receiptProblem({ size: 10, type: "" })).toBe("type");
    expect(receiptProblem({ size: 10, type: "image/webp" })).toBeNull();
  });

  it.each([
    [new File([], "bosh.png", { type: "image/png" }), "Fayl bo'sh."],
    [png(RECEIPT_MAX_BYTES + 1), "Fayl 5 MB dan katta."],
    [new File([new Uint8Array(8)], "chek.gif", { type: "image/gif" }), "Faqat JPEG, PNG, WebP rasm yoki PDF yuboriladi."],
  ])("sends nothing for such a file and says why next to the field", async (file, message) => {
    const server = backend();
    await open(server);
    fill("50000", file);
    expect(screen.getByText(message).id).toBe("notice-receipt-error");
    expect(server.writes()).toHaveLength(0);
  });
});

describe("sending", () => {
  it("sends the amount as JSON when there is no receipt, then says it was sent and shows it as waiting", async () => {
    const server = backend();
    await open(server);
    server.held.notices = [noticeBody()];
    fill("50000");
    expect((await screen.findByRole("status")).textContent).toBe("Xabar do'konga yuborildi: 50 000 so'm.");
    expect(notices(server)).toHaveLength(1);
    expect(notices(server)[0]).toMatchObject({ method: "POST", body: { amount: 50000 } });
    expect(notices(server)[0]?.form).toBeUndefined();
    expect(notices(server)[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(await within(section()).findByText("Yuborilgan: do'kon javobi 2026-yil 20-oktabr, 10:10 gacha kutiladi.")).toBeTruthy();
    expect(screen.queryByLabelText(AMOUNT)).toBeNull();
    // The balance is what it was: a notice changes nothing by itself.
    expect(screen.getByText(exact("120 000 so'm"))).toBeTruthy();
  });

  it("sends a multipart form with the amount and the file when there is a receipt", async () => {
    const server = backend();
    await open(server);
    fill("50 000", png());
    await waitFor(() => expect(notices(server)).toHaveLength(1));
    const sent = notices(server)[0];
    expect(sent?.body).toBeUndefined();
    expect(sent?.form?.["amount"]).toBe("50000");
    expect((sent?.form?.["receipt"] as File).name).toBe("chek.png");
    expect(sent?.headers["Content-Type"]).toBeUndefined();
  });

  it("disables the form while the notice is in flight, so a double tap sends one", async () => {
    const gate = deferred<ReturnType<typeof ok>>();
    const server = backend([], () => gate.promise);
    await open(server);
    fill("50000");
    const button = screen.getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    fireEvent.submit(button.closest("form") as HTMLFormElement);
    gate.resolve(ok(noticeBody(), 201));
    await screen.findByRole("status");
    expect(notices(server)).toHaveLength(1);
  });

  it("resends the same key after a failure, and a new one for another amount", async () => {
    let fail = true;
    const server = backend([], () => (fail ? "offline" : ok(noticeBody(), 201)));
    await open(server);
    fill("50000");
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Xabarni yuborish" }));
    await waitFor(() => expect(notices(server)).toHaveLength(2));
    fireEvent.change(screen.getByLabelText(AMOUNT), { target: { value: "60000" } });
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Xabarni yuborish" }));
    await waitFor(() => expect(notices(server)).toHaveLength(3));
    const keys = notices(server).map((sent) => sent.headers["Idempotency-Key"]);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });
});

describe("the server's refusals, each where it belongs", () => {
  const VALIDATION = "Ma'lumotlar noto'g'ri kiritilgan.";

  it("puts a payment above the debt next to the amount", async () => {
    const server = backend([], () => refusal(409, "EXCEEDS_BALANCE", "To'lov mijozning qarzidan katta bo'lishi mumkin emas."));
    await open(server);
    fill("50000");
    expect((await screen.findByText("To'lov mijozning qarzidan katta bo'lishi mumkin emas.")).id).toBe("notice-amount-error");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("explains too many open notices", async () => {
    const general = "Ko'rib chiqilmagan to'lov xabarlaringiz juda ko'p. Do'kon javobini kuting.";
    const server = backend([], () => refusal(409, "PAYMENT_NOTICE_NOT_ALLOWED", general, { reason: "too_many_open" }));
    await open(server);
    fill("50000");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(general);
    expect(alert.textContent).toContain("Javob kutayotgan xabarlaringiz 3 ta.");
  });

  it.each([
    ["empty", "Fayl bo'sh."],
    ["too_large", "Fayl 5 MB dan katta."],
    ["type", "Faqat JPEG, PNG, WebP rasm yoki PDF yuboriladi."],
    ["malformed", "Fayl buzilgan yoki to'liq emas. Boshqa nusxasini yuboring."],
  ])("puts the server's word about the receipt, %s, next to the receipt", async (word, message) => {
    const server = backend([], () => refusal(422, "VALIDATION", VALIDATION, { receipt: word }));
    await open(server);
    fill("50000", png());
    expect((await screen.findByText(message)).id).toBe("notice-receipt-error");
    expect(screen.getByLabelText(RECEIPT).getAttribute("aria-invalid")).toBe("true");
  });

  it("keeps a word about the receipt it does not know next to the receipt, as the server said it", async () => {
    const server = backend([], () => refusal(422, "VALIDATION", VALIDATION, { receipt: "scanned" }));
    await open(server);
    fill("50000", png());
    expect((await screen.findByText(VALIDATION)).id).toBe("notice-receipt-error");
  });

  it("puts the server's refusal of the amount next to the amount", async () => {
    const server = backend([], () => refusal(422, "VALIDATION", VALIDATION, { amount: "a whole amount between 100 and 100000000 UZS" }));
    await open(server);
    fill("50000");
    expect((await screen.findByText(/Summani tushunib bo'lmadi/)).id).toBe("notice-amount-error");
  });

  it("says the receipt is too large when the server would not read a body that big", async () => {
    const server = backend([], () => refusal(413, "BODY_TOO_LARGE", "So'rov juda katta."));
    await open(server);
    fill("50000", png());
    expect((await screen.findByText("Fayl 5 MB dan katta.")).id).toBe("notice-receipt-error");
  });

  it("says what to do when the file store is down", async () => {
    const general = "Fayllarni saqlash hozir ishlamayapti. Birozdan keyin qayta urinib ko'ring.";
    const server = backend([], () => refusal(503, "FILE_STORE_UNAVAILABLE", general));
    await open(server);
    fill("50000", png());
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(general);
    expect(alert.textContent).toContain("xabarni cheksiz yuboring");
  });

  it("says how long to wait when there are too many requests", async () => {
    const server = backend([], () => ({ ...refusal(429, "RATE_LIMITED", "So'rovlar juda ko'p."), headers: { "Retry-After": "30" } }));
    await open(server);
    fill("50000");
    expect((await screen.findByRole("alert")).textContent).toContain("So'rovlar juda ko'p. 30 soniyadan keyin urinib ko'ring.");
  });
});

describe("the state of recent notices", () => {
  const rows = () => within(section()).getAllByRole("listitem").map((item) => [...item.children].map((part) => part.textContent?.replace(/\u00a0/g, " ")));

  it("shows each notice with its amount, when it was sent, and what became of it", async () => {
    await open(
      backend([
        noticeBody({ id: "n1", has_receipt: true }),
        noticeBody({ id: "n2", status: "accepted", recorded_amount: 50000, closed_at: "2026-10-06T06:00:00+00:00" }),
        noticeBody({ id: "n3", status: "accepted", recorded_amount: 45000, closed_at: "2026-10-06T06:00:00+00:00" }),
        noticeBody({ id: "n4", status: "declined", decline_reason: "Pul kelmadi", closed_at: "2026-10-06T06:00:00+00:00" }),
        noticeBody({ id: "n5", status: "expired" }),
        noticeBody({ id: "n6", status: "archived" }),
      ]),
    );
    const when = "2026-yil 6-oktabr, 10:1050 000 so'm";
    expect(rows()).toEqual([
      [when, "Yuborilgan: do'kon javobi 2026-yil 20-oktabr, 10:10 gacha kutiladi.", "chek bilan"],
      [when, "Do'kon to'lovni yozdi."],
      [when, "Do'kon to'lovni boshqa summada yozdi: 45 000 so'm."],
      [when, "Do'kon rad etdi.", "Do'kon sababi: Pul kelmadi"],
      [when, "Muddati o'tdi: do'kon o'z vaqtida javob bermadi."],
      [when, "Yopilgan."],
    ]);
  });

  it("shows past notices even when nothing is owed any more", async () => {
    await open(backend([noticeBody({ status: "accepted", recorded_amount: 50000 })], undefined, 0));
    expect(within(section()).getByText("Do'kon to'lovni yozdi.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: ASK })).toBeNull();
  });

  it("reads the same in Russian", async () => {
    await open(backend([noticeBody({ status: "declined", decline_reason: "Деньги не пришли" })]), "ru");
    expect(within(section()).getByText("Магазин отклонил.")).toBeTruthy();
    expect(within(section()).getByText("Причина магазина: Деньги не пришли")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Я оплатил" })).toBeTruthy();
  });
});
