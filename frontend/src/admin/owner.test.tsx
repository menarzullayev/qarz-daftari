// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { deferred, type Reply, type Sent } from "../testing/fakeServer";
import { isOwnerRefusal, telegramId } from "./rules";
import { ShopScreen } from "./ShopScreen";
import { ADMIN, adminApi, CSRF, NOBODY, NOW, ok, refusal, renderAdmin, SHOP_ID, shopBody, shopDetailBody } from "./testing";

beforeEach(() => {
  window.location.hash = "";
});
afterEach(cleanup);

const NEW_OWNER = 987654321;
const REASON = "Egasi hisobini yo'qotdi, pasport bilan tasdiqlandi";

/** The shop page over a fake server: reads answer with `detail`, writes with `write`. */
function open(detail: Record<string, unknown> = shopDetailBody(), write: (sent: Sent) => Reply = () => ok(shopBody({ owner_tg_id: NEW_OWNER }))) {
  const held = { detail };
  const made = adminApi((sent) => (sent.method === "GET" ? ok(sent.path.endsWith("/support-access") ? { items: [], next_cursor: null } : held.detail) : write(sent)));
  renderAdmin(<ShopScreen api={made.api} shopId={SHOP_ID} now={() => NOW} who={NOBODY} />);
  return { server: made.server, held };
}

const section = () => screen.getByRole("region", { name: "Do'kon egasi" });

async function fill(owner = String(NEW_OWNER), reason = REASON, code = "123456") {
  fireEvent.click(await screen.findByRole("button", { name: "Egani almashtirish" }));
  const form = within(section());
  fireEvent.change(form.getByLabelText("Yangi eganing Telegram ID raqami"), { target: { value: owner } });
  fireEvent.change(form.getByLabelText("Sabab"), { target: { value: reason } });
  fireEvent.change(form.getByLabelText("Autentifikator kodi"), { target: { value: code } });
  fireEvent.click(form.getByRole("button", { name: "Davom etish" }));
}

describe("a Telegram identifier as typed", () => {
  it.each([
    ["987654321", 987654321],
    [" 42 ", 42],
    ["9007199254740991", 9007199254740991],
  ])("%s is one", (typed, id) => {
    expect(telegramId(typed)).toBe(id);
  });

  it.each(["", "0", "012", "-5", "1.5", "12a", "1e5", "9007199254740993", "12345678901234567"])("%s is not", (typed) => {
    expect(telegramId(typed)).toBeNull();
  });

  it("knows the server's refusals and nothing else", () => {
    expect(["unknown_user", "already_owner"].every(isOwnerRefusal)).toBe(true);
    // "shop_limit" was one until the limit of five shops was removed (DEC-065).
    expect([undefined, "", "paid", 5, "shop_limit"].some(isOwnerRefusal)).toBe(false);
  });
});

describe("changing a shop's owner", () => {
  it("is offered on the shop's page and sends nothing until asked", async () => {
    const { server } = open();
    await screen.findByRole("button", { name: "Egani almashtirish" });
    expect(within(section()).queryByRole("form")).toBeNull();
    expect(server.writes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Egani almashtirish" }));
    const form = within(section());
    expect(form.getByRole("form", { name: "Egani almashtirish" })).toBeTruthy();
    expect(form.getByText("Faqat ega Telegram hisobini yo'qotganda va asoschining yozma qarori bo'lganda.")).toBeTruthy();
    expect((form.getByLabelText("Autentifikator kodi") as HTMLInputElement).autocomplete).toBe("one-time-code");
    fireEvent.click(form.getByRole("button", { name: "Bekor qilish" }));
    expect(within(section()).queryByRole("form")).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it.each([
    ["", REASON, "123456", "owner-new-error", "Telegram ID faqat raqamlardan iborat."],
    ["12a", REASON, "123456", "owner-new-error", "Telegram ID faqat raqamlardan iborat."],
    ["123456789", REASON, "123456", "owner-new-error", "Bu odam allaqachon shu do'kon egasi."],
    [String(NEW_OWNER), "ab", "123456", "owner-reason-error", "Sabab 3 dan 500 belgigacha bo'lishi kerak."],
    [String(NEW_OWNER), REASON, "", "owner-code-error", "Kod aynan 6 ta raqamdan iborat."],
    [String(NEW_OWNER), REASON, "12345", "owner-code-error", "Kod aynan 6 ta raqamdan iborat."],
  ])("needs a new owner, a reason and a code before the question is asked (%s, %s, %s)", async (owner, reason, code, where, message) => {
    const { server } = open();
    await fill(owner, reason, code);
    expect(document.getElementById(where)?.textContent).toBe(message);
    expect(screen.queryByRole("button", { name: "Ha, ega almashtirilsin" })).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it("states what will happen, sends nothing on going back, and one request with a key on yes", async () => {
    const { server, held } = open();
    await fill(` ${NEW_OWNER} `, `  ${REASON}  `);
    const question = within(section());
    expect(question.getByText(`Baraka savdo: egalik Telegram ID ${NEW_OWNER} hisobiga o'tkazilsinmi?`)).toBeTruthy();
    expect(question.getByText(`Sabab: ${REASON}`)).toBeTruthy();
    expect(question.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Hozirgi ega do'konda to'xtatilgan menejer bo'lib qoladi va hech narsa qila olmaydi; yangi ega uni tiklashi yoki chiqarishi mumkin.",
      "Kutilayotgan egalikni o'tkazish taklifi bekor qilinadi.",
      "Yangi egaga xabar yuboriladi. Eski hisobga faqat egalik xizmat ma'muriyati tomonidan o'zgartirilgani aytiladi.",
    ]);
    expect(server.writes()).toHaveLength(0);

    // Going back keeps what was typed, except the code: it is asked for again.
    fireEvent.click(question.getByRole("button", { name: "Orqaga" }));
    expect(server.writes()).toHaveLength(0);
    const form = within(section());
    expect((form.getByLabelText("Sabab") as HTMLTextAreaElement).value).toBe(`  ${REASON}  `);
    expect((form.getByLabelText("Autentifikator kodi") as HTMLInputElement).value).toBe("");
    fireEvent.change(form.getByLabelText("Autentifikator kodi"), { target: { value: "654321" } });
    fireEvent.click(form.getByRole("button", { name: "Davom etish" }));

    held.detail = shopDetailBody({ owner_tg_id: NEW_OWNER });
    fireEvent.click(screen.getByRole("button", { name: "Ha, ega almashtirilsin" }));
    await waitFor(() => expect(within(section()).getByRole("status").textContent).toBe("Do'kon egasi almashtirildi."));
    expect(server.writes()).toHaveLength(1);
    const sent = server.writes()[0];
    expect(sent).toMatchObject({ method: "POST", path: `${ADMIN}/shops/${SHOP_ID}/owner` });
    expect(sent?.body).toEqual({ new_owner_tg_id: NEW_OWNER, reason: REASON, code: "654321" });
    expect(sent?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(sent?.headers["X-CSRF-Token"]).toBe(CSRF);
    // The page shows the new owner, and was read again once.
    await waitFor(() => expect(screen.getByText(String(NEW_OWNER))).toBeTruthy());
    expect(server.sent.filter((s) => s.method === "GET" && s.path === `${ADMIN}/shops/${SHOP_ID}`)).toHaveLength(2);
    // The form is closed and empty: nothing of the code is kept.
    expect(within(section()).queryByRole("form")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Egani almashtirish" }));
    expect((within(section()).getByLabelText("Autentifikator kodi") as HTMLInputElement).value).toBe("");
    expect(within(section()).queryByRole("status")).toBeNull();
  });

  it("says that a shop waiting for deletion keeps waiting", async () => {
    open(shopDetailBody({ status: "deletion_pending", deletion_due: "2026-11-05T07:00:00+00:00" }));
    await fill();
    expect(within(section()).getAllByRole("listitem").map((item) => item.textContent)).toContain(
      "Do'kon o'chirishni kutmoqda: muddat o'zgarmaydi, uni yangi ega bekor qilishi mumkin.",
    );
  });

  it("disables the answer while the change is in flight, so a double tap sends one", async () => {
    const gate = deferred<ReturnType<typeof ok>>();
    const { server } = open(undefined, () => gate.promise);
    await fill();
    const yes = screen.getByRole("button", { name: "Ha, ega almashtirilsin" });
    fireEvent.click(yes);
    fireEvent.click(yes);
    expect(((await screen.findByRole("button", { name: "Saqlanmoqda…" })) as HTMLButtonElement).disabled).toBe(true);
    gate.resolve(ok(shopBody({ owner_tg_id: NEW_OWNER })));
    await within(section()).findByRole("status");
    expect(server.writes()).toHaveLength(1);
  });

  it.each([
    ["unknown_user", "Bunday Telegram ID bilan botni ishga tushirgan odam yo'q."],
    ["already_owner", "Bu odam allaqachon shu do'kon egasi."],
  ])("explains the server's refusal %s", async (reason, detail) => {
    const general = "Do'konni bu odamga berib bo'lmaydi.";
    const { server } = open(undefined, () => refusal(409, "OWNER_REASSIGNMENT_REFUSED", general, { reason }));
    await fill();
    fireEvent.click(screen.getByRole("button", { name: "Ha, ega almashtirilsin" }));
    expect((await screen.findByRole("alert")).textContent).toContain(general);
    expect(within(section()).getByText(detail)).toBeTruthy();
    expect(within(section()).queryByRole("status")).toBeNull();
    expect(server.writes()).toHaveLength(1);
  });

  it("after a refused code asks for a new one and resends the same request under the same key", async () => {
    let calls = 0;
    const { server } = open(undefined, () => {
      calls += 1;
      return calls === 1 ? refusal(403, "SECOND_FACTOR_INVALID", "Kod noto'g'ri yoki eskirgan.") : ok(shopBody({ owner_tg_id: NEW_OWNER }));
    });
    await fill();
    fireEvent.click(screen.getByRole("button", { name: "Ha, ega almashtirilsin" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Kod noto'g'ri yoki eskirgan.");
    expect(within(section()).queryByRole("status")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Orqaga" }));
    const form = within(section());
    expect((form.getByLabelText("Autentifikator kodi") as HTMLInputElement).value).toBe("");
    fireEvent.change(form.getByLabelText("Autentifikator kodi"), { target: { value: "222333" } });
    fireEvent.click(form.getByRole("button", { name: "Davom etish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, ega almashtirilsin" }));
    await within(section()).findByRole("status");
    const [first, second] = server.writes();
    expect([first?.body, second?.body]).toMatchObject([{ code: "123456" }, { code: "222333" }]);
    expect(second?.headers["Idempotency-Key"]).toBe(first?.headers["Idempotency-Key"]);
  });

  it("is in Russian for an administrator who reads Russian", async () => {
    const made = adminApi((sent) => ok(sent.path.endsWith("/support-access") ? { items: [], next_cursor: null } : shopDetailBody()));
    renderAdmin(<ShopScreen api={made.api} shopId={SHOP_ID} now={() => NOW} who={NOBODY} />, "ru");
    fireEvent.click(await screen.findByRole("button", { name: "Сменить владельца" }));
    const form = within(screen.getByRole("region", { name: "Владелец магазина" }));
    fireEvent.change(form.getByLabelText("Telegram ID нового владельца"), { target: { value: String(NEW_OWNER) } });
    fireEvent.change(form.getByLabelText("Причина"), { target: { value: REASON } });
    fireEvent.change(form.getByLabelText("Код аутентификатора"), { target: { value: "123456" } });
    fireEvent.click(form.getByRole("button", { name: "Продолжить" }));
    expect(screen.getByText(`Baraka savdo: передать магазин аккаунту с Telegram ID ${NEW_OWNER}?`)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Да, сменить владельца" })).toBeTruthy();
  });
});
