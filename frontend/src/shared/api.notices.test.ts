import { describe, expect, it } from "vitest";

import { translate } from "../i18n/catalog";
import {
  accountBody,
  CUSTOMER_ID,
  detailBody,
  fakeServer,
  LINK_ID,
  ME_BASE,
  NOTICE_ID,
  noticeBody,
  ok,
  openNoticeBody,
  refusal,
  SHOP_BASE,
  SHOP_ID,
} from "../testing/fakeServer";
import { ApiError, createApi, RECEIPT_MAX_BYTES, RECEIPT_TYPES } from "./api";
import { errorText } from "./workspace/parts";

const KEY = "key-0000-0001";

function client(reply: Parameters<typeof fakeServer>[0]) {
  const server = fakeServer(reply);
  const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "session-token" } });
  return { server, shop: api.shop(SHOP_ID), account: api.account(LINK_ID) };
}

describe("sending a payment notice", () => {
  it("is JSON with the amount alone when there is no receipt, with the idempotency key", async () => {
    const { server, account } = client(() => ok(noticeBody(), 201));
    const sent = await account.sendPaymentNotice(50000, null, KEY);
    expect(sent).toMatchObject({ id: NOTICE_ID, status: "sent", amount: 50000, hasReceipt: false, recordedAmount: null });
    expect(server.sent[0]).toMatchObject({ method: "POST", path: `${ME_BASE}/payment-notices`, body: { amount: 50000 } });
    expect(server.sent[0]?.form).toBeUndefined();
    expect(server.sent[0]?.headers["Content-Type"]).toBe("application/json");
    expect(server.sent[0]?.headers["Idempotency-Key"]).toBe(KEY);
  });

  it("is a multipart form with the fields amount and receipt when there is one, and sets no content type itself", async () => {
    const { server, account } = client(() => ok(noticeBody({ has_receipt: true }), 201));
    const file = new File([new Uint8Array([137, 80, 78, 71])], "chek.png", { type: "image/png" });
    await account.sendPaymentNotice(50000, file, KEY);
    const sent = server.sent[0];
    expect(sent?.body).toBeUndefined();
    expect(Object.keys(sent?.form ?? {}).sort()).toEqual(["amount", "receipt"]);
    expect(sent?.form?.["amount"]).toBe("50000");
    expect((sent?.form?.["receipt"] as File).size).toBe(4);
    // The browser writes "multipart/form-data; boundary=…": a content type set here would have no boundary.
    expect(sent?.headers["Content-Type"]).toBeUndefined();
    expect(sent?.headers["Idempotency-Key"]).toBe(KEY);
  });

  it("refuses an amount that is not a whole number before anything is sent", () => {
    const { server, account } = client(() => ok(noticeBody(), 201));
    expect(() => account.sendPaymentNotice(50000.5, null, KEY)).toThrow(RangeError);
    expect(server.sent).toHaveLength(0);
  });

  it("knows what a receipt may be", () => {
    expect(RECEIPT_MAX_BYTES).toBe(5 * 1024 * 1024);
    expect(RECEIPT_TYPES).toEqual(["image/jpeg", "image/png", "image/webp", "application/pdf"]);
  });
});

describe("notices in what is read", () => {
  it("come with the customer's own account, newest first, with their outcome", async () => {
    const { account } = client(() =>
      ok(accountBody({ payment_notices: [noticeBody({ status: "accepted", recorded_amount: 45000, closed_at: "2026-10-06T06:00:00+00:00" })] })),
    );
    expect((await account.read()).paymentNotices).toEqual([
      {
        id: NOTICE_ID,
        status: "accepted",
        amount: 50000,
        recordedAmount: 45000,
        hasReceipt: false,
        declineReason: null,
        createdAt: "2026-10-06T05:10:00+00:00",
        closedAt: "2026-10-06T06:00:00+00:00",
        expiresAt: "2026-10-20T05:10:00+00:00",
        receiptSeenBefore: false,
      },
    ]);
  });

  it("come with the staff customer card, and are none for a server that does not send them", async () => {
    const { shop } = client(() => ok(detailBody({ payment_notices: [{ ...noticeBody(), receipt_seen_before: true }] })));
    expect((await shop.readCustomer(CUSTOMER_ID)).paymentNotices[0]).toMatchObject({ receiptSeenBefore: true });
    const old = client(() => ok(detailBody()));
    expect((await old.shop.readCustomer(CUSTOMER_ID)).paymentNotices).toEqual([]);
  });

  it("are refused when an amount is not a whole number", async () => {
    const { account } = client(() => ok(accountBody({ payment_notices: [noticeBody({ amount: 50000.5 })] })));
    await expect(account.read()).rejects.toMatchObject({ code: "BAD_RESPONSE" });
  });
});

describe("the shop's side of payment notices", () => {
  it("lists open notices with the customer, their balance and whether the receipt was seen before", async () => {
    const { server, shop } = client(() => ok({ items: [openNoticeBody({ receipt_seen_before: true })] }));
    const [notice] = await shop.listPaymentNotices();
    expect(notice).toMatchObject({
      id: NOTICE_ID,
      amount: 50000,
      hasReceipt: true,
      receiptSeenBefore: true,
      customerId: CUSTOMER_ID,
      customerName: "Ali Valiyev",
      customerBalance: 120000,
    });
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/payment-notices` });
  });

  it("accepts with no amount for the stated one and with an amount for a correction", async () => {
    const { server, shop } = client(() => ok({}));
    await shop.acceptPaymentNotice(NOTICE_ID, null, KEY);
    await shop.acceptPaymentNotice(NOTICE_ID, 45000, KEY);
    expect(server.sent.map((sent) => [sent.method, sent.path, sent.body, sent.headers["Idempotency-Key"]])).toEqual([
      ["POST", `${SHOP_BASE}/payment-notices/${NOTICE_ID}/accept`, {}, KEY],
      ["POST", `${SHOP_BASE}/payment-notices/${NOTICE_ID}/accept`, { amount: 45000 }, KEY],
    ]);
    expect(() => shop.acceptPaymentNotice(NOTICE_ID, 45000.5, KEY)).toThrow(RangeError);
  });

  it("names how the money came only when it is told to: otherwise the body is what it was", async () => {
    const { server, shop } = client(() => ok({}));
    await shop.acceptPaymentNotice(NOTICE_ID, null, KEY, "card");
    await shop.acceptPaymentNotice(NOTICE_ID, 45000, KEY, "transfer");
    await shop.acceptPaymentNotice(NOTICE_ID, null, KEY, undefined);
    expect(server.sent.map((sent) => sent.body)).toEqual([{ method: "card" }, { amount: 45000, method: "transfer" }, {}]);
  });

  it("declines with a tidied reason of 3 to 300 characters, and refuses another before sending", async () => {
    const { server, shop } = client(() => ok({}));
    await shop.declinePaymentNotice(NOTICE_ID, "  Pul   kelmadi ", KEY);
    expect(server.sent[0]).toMatchObject({
      method: "POST",
      path: `${SHOP_BASE}/payment-notices/${NOTICE_ID}/decline`,
      body: { reason: "Pul kelmadi" },
    });
    expect(() => shop.declinePaymentNotice(NOTICE_ID, "yo", KEY)).toThrow(RangeError);
    expect(server.sent).toHaveLength(1);
  });

  it("asks for a receipt's link with the session and reads where it leads and until when", async () => {
    const { server, shop } = client(() => ok({ url: "/files/abc.def", expires_at: "2026-10-06T07:05:00+00:00" }));
    expect(await shop.receiptLink(NOTICE_ID)).toEqual({ url: "/files/abc.def", expiresAt: "2026-10-06T07:05:00+00:00" });
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/payment-notices/${NOTICE_ID}/receipt` });
    expect(server.sent[0]?.headers["Authorization"]).toBe("Bearer session-token");
  });
});

describe("what a refusal carries", () => {
  it("has the wait from Retry-After, in whole seconds", async () => {
    const { shop } = client(() => ({ ...refusal(429, "RATE_LIMITED", "So'rovlar juda ko'p."), headers: { "Retry-After": "17" } }));
    await expect(shop.listPaymentNotices()).rejects.toMatchObject({ status: 429, code: "RATE_LIMITED", retryAfter: 17 });
  });

  it.each(["", "soon", "-5", "1.5", "Wed, 21 Oct 2026 07:28:00 GMT"])("has no wait for Retry-After %j", async (value) => {
    const { shop } = client(() => ({ ...refusal(429, "RATE_LIMITED", "So'rovlar juda ko'p."), headers: { "Retry-After": value } }));
    await expect(shop.listPaymentNotices()).rejects.toMatchObject({ retryAfter: null });
  });

  it("names a body that was too large and too many requests even when a proxy answers without a code", async () => {
    const proxy = (status: number) => client(() => ({ status, body: "<html>nginx</html>" })).shop.listPaymentNotices();
    await expect(proxy(413)).rejects.toMatchObject({ code: "BODY_TOO_LARGE", serverMessage: null });
    await expect(proxy(429)).rejects.toMatchObject({ code: "RATE_LIMITED" });
    await expect(proxy(502)).rejects.toMatchObject({ code: "ERROR" });
  });
});

describe("the shared wording of errors", () => {
  const t = (language: "uz" | "ru") => (key: Parameters<typeof translate>[1], params?: Parameters<typeof translate>[2]) =>
    translate(language, key, params);

  it("words a body that is too large from the catalog: the server says it in Uzbek only", () => {
    const error = new ApiError(413, "BODY_TOO_LARGE", "So'rov juda katta.");
    expect(errorText(error, t("ru"))).toBe("Отправленные данные слишком велики. Попробуйте с файлом поменьше.");
    expect(errorText(error, t("uz"))).toBe("Yuborilgan ma'lumot juda katta. Kichikroq fayl bilan urinib ko'ring.");
  });

  it("adds how long to wait to too many requests, when the server said", () => {
    const said = "So'rovlar juda ko'p. Biroz kutib, qayta urinib ko'ring.";
    expect(errorText(new ApiError(429, "RATE_LIMITED", said, {}, 17), t("uz"))).toBe(`${said} 17 soniyadan keyin urinib ko'ring.`);
    expect(errorText(new ApiError(429, "RATE_LIMITED", said), t("uz"))).toBe(said);
    expect(errorText(new ApiError(429, "RATE_LIMITED", null, {}, 1), t("ru"))).toBe(
      "Слишком много запросов. Подождите немного и повторите. Повторите через 1 секунду.",
    );
    expect(errorText(new ApiError(429, "RATE_LIMITED", null, {}, 22), t("ru"))).toContain("через 22 секунды");
  });

  it("words a timeout, with the server's message when there is one", () => {
    expect(errorText(new ApiError(503, "TIMEOUT", null), t("uz"))).toBe(
      "So'rov juda uzoq davom etdi va to'xtatildi. Hech narsa saqlanmadi. Qayta urinib ko'ring.",
    );
    expect(errorText(new ApiError(503, "TIMEOUT", "Server aytdi."), t("uz"))).toBe("Server aytdi.");
  });

  it("leaves every other refusal as the server worded it", () => {
    expect(errorText(new ApiError(409, "EXCEEDS_BALANCE", "To'lov qarzdan katta."), t("uz"))).toBe("To'lov qarzdan katta.");
    expect(errorText(new ApiError(500, "ERROR", null), t("uz"))).toBe("Xatolik yuz berdi");
  });
});
