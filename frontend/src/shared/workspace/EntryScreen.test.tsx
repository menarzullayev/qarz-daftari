// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  CUSTOMER_ID,
  customerBody,
  deferred,
  detailBody,
  fakeServer,
  ok,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
} from "../../testing/fakeServer";
import { exact, renderScreen } from "../../testing/renderScreen";
import type { EntryKind } from "../api";
import { EntryScreen, promisedDateFor } from "./EntryScreen";

afterEach(cleanup);

const recorded = (kind: string, amount: number, balance: number, promisedDate: string | null = null) =>
  ok(
    {
      entry: { id: "e-new", seq: 2, kind, amount, note: null, created_at: "2026-10-06T07:00:00+00:00", promised_date: promisedDate },
      customer: customerBody({ balance }),
    },
    201,
  );

/** A shop with one customer who owes 120 000; `onWrite` answers the POST of an entry. */
function shop(onWrite: (sent: Sent, attempt: number) => Reply, detail: Reply = ok(detailBody())) {
  let attempt = 0;
  return fakeServer((sent) => (sent.method === "GET" ? detail : onWrite(sent, attempt++)));
}

async function openForm(server: ReturnType<typeof fakeServer>, kind: EntryKind = "credit", now?: Date) {
  renderScreen(<EntryScreen customerId={CUSTOMER_ID} kind={kind} />, now ? { fetch: server.fetch, now } : { fetch: server.fetch });
  return screen.findByLabelText<HTMLInputElement>("Summa, so'm");
}

const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });
const submitButton = (kind: EntryKind = "credit") =>
  screen.getByRole<HTMLButtonElement>("button", { name: kind === "credit" ? "Nasiyani yozish" : "To'lovni yozish" });

describe("recording a credit sale", () => {
  it("shows whose debt it is and what they owe now", async () => {
    const server = shop(() => recorded("credit", 45000, 165000));
    expect(screen.queryByText("Yuklanmoqda…")).toBeNull();
    await openForm(server);
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Ali Valiyev");
    expect(screen.getByText(exact("120\u00a0000 so'm"))).toBeTruthy();
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}` });
  });

  it("sends a whole amount and nothing else by default, then shows the balance the server answered", async () => {
    const server = shop(() => recorded("credit", 45000, 165000, "2026-11-05"));
    type(await openForm(server), "45 000");
    expect(screen.getByText(exact("45\u00a0000 so'm"))).toBeTruthy(); // what the field was read as, before sending
    fireEvent.click(submitButton());

    const done = await screen.findByRole("status");
    const write = server.writes()[0];
    expect(write).toMatchObject({ method: "POST", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}/entries` });
    // No promised date: the server applies the shop's usual term. No note: none was typed.
    expect(write?.body).toEqual({ kind: "credit", amount: 45000 });
    expect(write?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(done.textContent).toContain("Ali Valiyev: 45\u00a0000 so'm nasiya yozildi.");
    expect(done.textContent).toContain("Yangi qarz: 165\u00a0000 so'm");
    expect(done.textContent).toContain("To'lash va'dasi: 2026-yil 5-noyabr");
    expect(screen.queryByLabelText("Summa, so'm")).toBeNull();
    expect(screen.getByRole("link", { name: "Mijoz sahifasi" }).getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}`);
  });

  it.each([
    ["45000", 45000],
    ["45k", 45000],
    ["45 ming", 45000],
    ["1.250.000", 1250000],
    ["100", 100],
    ["100000000", 100000000],
  ])("reads %s as the integer %i", async (typed, amount) => {
    const server = shop(() => recorded("credit", amount, 120000 + amount));
    type(await openForm(server), typed);
    fireEvent.click(submitButton());
    await screen.findByRole("status");
    const body = server.writes()[0]?.body as { amount: unknown };
    expect(body.amount).toBe(amount);
    expect(Number.isSafeInteger(body.amount)).toBe(true);
  });

  it.each([
    ["", "Summani kiriting."],
    ["99", "Summa kamida 100 so'm bo'lishi kerak."],
    ["0", "Summa kamida 100 so'm bo'lishi kerak."],
    ["100000001", "Summa ko'pi bilan 100\u00a0000\u00a0000 so'm bo'lishi mumkin."],
    ["45.5", "Summa butun so'mda bo'lishi kerak, tiyinsiz."],
    ["0.500", "Summa butun so'mda bo'lishi kerak, tiyinsiz."],
    ["45,50", "Summa butun so'mda bo'lishi kerak, tiyinsiz."],
    ["-45000", "Summani tushunib bo'lmadi. Faqat raqam yozing, masalan 45000."],
    ["qirq besh", "Summani tushunib bo'lmadi. Faqat raqam yozing, masalan 45000."],
  ])("refuses the amount %j without calling the server", async (typed, message) => {
    const server = shop(() => recorded("credit", 45000, 165000));
    const amount = await openForm(server);
    type(amount, typed);
    fireEvent.click(submitButton());
    expect(screen.getByRole("alert").textContent).toBe(message);
    expect(amount.getAttribute("aria-invalid")).toBe("true");
    expect(server.writes()).toHaveLength(0);
  });

  it("sends the note with its white space tidied, and refuses one that is too long", async () => {
    const server = shop(() => recorded("credit", 45000, 165000));
    type(await openForm(server), "45000");
    const note = screen.getByLabelText("Izoh (ixtiyoriy)");
    type(note, "n".repeat(201));
    fireEvent.click(submitButton());
    expect(screen.getByRole("alert").textContent).toBe("Izoh 200 belgidan oshmasligi kerak.");
    expect(server.writes()).toHaveLength(0);

    type(note, "  non   va  sut ");
    fireEvent.click(submitButton());
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ kind: "credit", amount: 45000, note: "non va sut" });
  });
});

describe("promised date", () => {
  // The clock of these tests is Tuesday 6 October 2026 in Tashkent.
  it.each([
    [/^Ertaga/, "2026-10-07"],
    [/^Hafta oxirigacha/, "2026-10-11"],
    [/^Ikki haftada/, "2026-10-20"],
    [/^Bir oyda/, "2026-11-06"],
  ])("the choice %s sends %s", async (label, expected) => {
    const server = shop(() => recorded("credit", 45000, 165000, expected));
    type(await openForm(server), "45000");
    fireEvent.click(screen.getByRole("radio", { name: label }));
    fireEvent.click(submitButton());
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ kind: "credit", amount: 45000, promised_date: expected });
  });

  it("shows each choice's date and starts on the usual term", async () => {
    const server = shop(() => recorded("credit", 45000, 165000));
    await openForm(server);
    const names = screen.getAllByRole("radio").map((radio) => radio.closest("label")?.textContent);
    expect(names).toEqual([
      "Odatdagi muddat",
      "Ertaga2026-yil 7-oktabr",
      "Hafta oxirigacha2026-yil 11-oktabr",
      "Ikki haftada2026-yil 20-oktabr",
      "Bir oyda2026-yil 6-noyabr",
      "Sanani tanlash",
    ]);
    expect(screen.getByRole<HTMLInputElement>("radio", { name: "Odatdagi muddat" }).checked).toBe(true);
  });

  it("counts from the Tashkent date of the sale, not the UTC one", async () => {
    // 19:30 UTC on 6 October is already 00:30 on 7 October in Tashkent.
    const server = shop(() => recorded("credit", 45000, 165000, "2026-10-08"));
    type(await openForm(server, "credit", new Date("2026-10-06T19:30:00Z")), "45000");
    fireEvent.click(screen.getByRole("radio", { name: /^Ertaga/ }));
    fireEvent.click(submitButton());
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toMatchObject({ promised_date: "2026-10-08" });
  });

  it("sends a picked date, limited to today through 365 days from today", async () => {
    const server = shop(() => recorded("credit", 45000, 165000, "2026-12-01"));
    type(await openForm(server), "45000");
    fireEvent.click(screen.getByRole("radio", { name: "Sanani tanlash" }));
    const picker = screen.getByLabelText<HTMLInputElement>("Sanani tanlash", { selector: "input[type=date]" });
    expect(picker.min).toBe("2026-10-06");
    expect(picker.max).toBe("2027-10-06");

    fireEvent.click(submitButton());
    expect(screen.getByRole("alert").textContent).toBe("Sanani tanlang.");

    for (const outside of ["2026-10-05", "2027-10-07"]) {
      type(picker, outside);
      fireEvent.click(submitButton());
      expect(screen.getByRole("alert").textContent).toBe("Sana bugundan boshlab 365 kun ichida bo'lishi kerak.");
    }
    expect(server.writes()).toHaveLength(0);

    type(picker, "2026-12-01");
    fireEvent.click(submitButton());
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ kind: "credit", amount: 45000, promised_date: "2026-12-01" });
  });

  it("accepts the first and the last allowed day and nothing beyond them", () => {
    const today = { year: 2026, month: 10, day: 6 };
    expect(promisedDateFor("picked", "2026-10-06", today)).toEqual({ ok: true, date: "2026-10-06" });
    expect(promisedDateFor("picked", "2027-10-06", today)).toEqual({ ok: true, date: "2027-10-06" });
    expect(promisedDateFor("picked", "2026-10-05", today)).toEqual({ ok: false, problem: "range" });
    expect(promisedDateFor("picked", "2027-10-07", today)).toEqual({ ok: false, problem: "range" });
    expect(promisedDateFor("picked", "", today)).toEqual({ ok: false, problem: "required" });
    expect(promisedDateFor("picked", "2026-02-30", today)).toEqual({ ok: false, problem: "required" });
    expect(promisedDateFor("default", "2026-12-01", today)).toEqual({ ok: true, date: null });
  });
});

describe("recording a payment", () => {
  it("has no promised date and sends none", async () => {
    const server = shop(() => recorded("payment", 20000, 100000));
    type(await openForm(server, "payment"), "20000");
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.queryByText("Qachon to'laydi?")).toBeNull();
    fireEvent.click(submitButton("payment"));
    const done = await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ kind: "payment", amount: 20000 });
    expect(done.textContent).toContain("Ali Valiyev: 20\u00a0000 so'm to'lov qabul qilindi.");
    expect(done.textContent).toContain("Yangi qarz: 100\u00a0000 so'm");
  });

  it("offers to pay the whole debt in one tap", async () => {
    const server = shop(() => recorded("payment", 120000, 0));
    const amount = await openForm(server, "payment");
    fireEvent.click(screen.getByRole("button", { name: "Butun qarz: 120\u00a0000 so'm" }));
    expect(amount.value).toBe("120\u00a0000");
    fireEvent.click(submitButton("payment"));
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ kind: "payment", amount: 120000 });
  });

  it("shows the server's refusal of a payment larger than the debt and keeps the form", async () => {
    const server = shop(() => refusal(409, "EXCEEDS_BALANCE", "To'lov mijozning qarzidan katta bo'lishi mumkin emas."));
    const amount = await openForm(server, "payment");
    type(amount, "500000");
    fireEvent.click(submitButton("payment"));
    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThan(0));
    expect(screen.getAllByRole("alert")[0]?.textContent).toBe("To'lov mijozning qarzidan katta bo'lishi mumkin emas.");
    expect(amount.getAttribute("aria-invalid")).toBe("true");
    expect(screen.queryByRole("status")).toBeNull();
    expect(submitButton("payment").disabled).toBe(false);
  });
});

describe("exactly one entry per action", () => {
  it("sends one request for a double tap and locks the button while it is pending", async () => {
    const answer = deferred<{ status: number; body: unknown }>();
    const server = shop(() => answer.promise);
    type(await openForm(server), "45000");
    const button = submitButton();
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.submit(button.closest("form") as HTMLFormElement);

    const pending = screen.getByRole<HTMLButtonElement>("button", { name: "Saqlanmoqda…" });
    expect(pending.disabled).toBe(true);
    expect(server.writes()).toHaveLength(1);

    answer.resolve(recorded("credit", 45000, 165000));
    await screen.findByRole("status");
    expect(server.writes()).toHaveLength(1);
  });

  it("resends the same key when a failed request is retried, so the server cannot apply it twice", async () => {
    // The first two attempts never get an answer: the entry may or may not have been recorded.
    const server = shop((_sent, attempt) => (attempt < 2 ? "offline" : recorded("credit", 45000, 165000)));
    type(await openForm(server), "45000");

    fireEvent.click(submitButton());
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Serverga ulanib bo'lmadi.");
    expect(alert.textContent).toContain("Qayta urinsangiz, yozuv ikki marta yozilmaydi.");

    fireEvent.click(submitButton());
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    await waitFor(() => expect(submitButton().disabled).toBe(false));
    fireEvent.click(submitButton());
    await screen.findByRole("status");

    const keys = server.writes().map((write) => write.headers["Idempotency-Key"]);
    expect(keys).toHaveLength(3);
    expect(keys[0]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).toBe(keys[0]);
    expect(server.writes().map((write) => write.body)).toEqual(Array(3).fill({ kind: "credit", amount: 45000 }));
  });

  it("uses a new key once the amount was corrected, because that is another entry", async () => {
    const server = shop((_sent, attempt) => (attempt === 0 ? "offline" : recorded("credit", 54000, 174000)));
    const amount = await openForm(server);
    type(amount, "45000");
    fireEvent.click(submitButton());
    await screen.findByRole("alert");
    type(amount, "54000");
    fireEvent.click(submitButton());
    await screen.findByRole("status");
    const keys = server.writes().map((write) => write.headers["Idempotency-Key"]);
    expect(keys[1]).not.toBe(keys[0]);
  });

  it("starts a fresh form with the new balance for another entry", async () => {
    let balance = 120000;
    const server = fakeServer((sent) => {
      if (sent.method === "GET") {
        return ok(detailBody({ balance }));
      }
      balance += 45000;
      return recorded("credit", 45000, balance);
    });
    type(await openForm(server), "45000");
    fireEvent.click(submitButton());
    await screen.findByRole("status");
    fireEvent.click(screen.getByRole("button", { name: "Yana yozuv" }));

    const amount = await screen.findByLabelText<HTMLInputElement>("Summa, so'm");
    expect(amount.value).toBe("");
    expect(screen.getByText(exact("165\u00a0000 so'm"))).toBeTruthy();
    type(amount, "45000");
    fireEvent.click(submitButton());
    await screen.findByRole("status");
    const keys = server.writes().map((write) => write.headers["Idempotency-Key"]);
    expect(keys).toHaveLength(2);
    expect(keys[1]).not.toBe(keys[0]);
  });
});

describe("server refusals", () => {
  it.each([
    [402, "SUBSCRIPTION_LIMITED", "Obuna tugagan: yangi nasiya yozilmaydi. To'lov qabul qilish va ko'rish ishlayveradi."],
    [403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan. Faqat do'kon egasi ma'lumotlarni ko'ra oladi va eksport qila oladi."],
    [409, "CUSTOMER_ARCHIVED", "Bu mijoz arxivda. Avval arxivdan chiqaring."],
    [409, "IDEMPOTENCY_KEY_REUSED", "Bu so'rov kaliti boshqa amal uchun ishlatilgan."],
  ])("shows the server's message for %i %s and records nothing", async (status, code, message) => {
    const server = shop(() => refusal(status, code, message));
    type(await openForm(server), "45000");
    fireEvent.click(submitButton());
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(message);
    expect(screen.queryByRole("status")).toBeNull();
    // The server answered, so there is no "it is safe to retry" hint next to its message.
    expect(screen.queryByText("Qayta urinsangiz, yozuv ikki marta yozilmaydi.")).toBeNull();
  });

  it("puts a validation refusal next to the field it is about", async () => {
    const server = shop(() =>
      refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { promised_date: "PROMISE_TOO_FAR", amount: "x" }),
    );
    type(await openForm(server), "45000");
    fireEvent.click(submitButton());
    await screen.findAllByRole("alert");
    const texts = screen.getAllByRole("alert").map((alert) => alert.textContent);
    expect(texts).toEqual([
      "Ma'lumotlar noto'g'ri kiritilgan.",
      "Summani tushunib bo'lmadi. Faqat raqam yozing, masalan 45000.",
      "Sana bugundan boshlab 365 kun ichida bo'lishi kerak.",
    ]);
  });

  it("says so in Russian when the server cannot be reached", async () => {
    const server = shop(() => "offline");
    renderScreen(<EntryScreen customerId={CUSTOMER_ID} kind="credit" />, { fetch: server.fetch, language: "ru" });
    type(await screen.findByLabelText("Сумма, сум"), "45000");
    fireEvent.click(screen.getByRole("button", { name: "Записать продажу в долг" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Не удалось связаться с сервером.");
  });
});

describe("opening the form", () => {
  it("shows loading first, and the not-found text for a customer that does not exist", async () => {
    const server = fakeServer(() => refusal(404, "NOT_FOUND", "Topilmadi."));
    renderScreen(<EntryScreen customerId={CUSTOMER_ID} kind="credit" />, { fetch: server.fetch });
    expect(screen.getByRole("status").textContent).toBe("Yuklanmoqda…");
    expect(await screen.findByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    expect(screen.queryByLabelText("Summa, so'm")).toBeNull();
  });

  it("offers a retry when the customer cannot be loaded", async () => {
    let attempt = 0;
    const server = fakeServer(() => (attempt++ === 0 ? "offline" : ok(detailBody())));
    renderScreen(<EntryScreen customerId={CUSTOMER_ID} kind="credit" />, { fetch: server.fetch });
    fireEvent.click(await screen.findByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByLabelText("Summa, so'm")).toBeTruthy();
  });
});
