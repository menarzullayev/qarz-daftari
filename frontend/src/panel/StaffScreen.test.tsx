// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { deferred, fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../testing/fakeServer";
import { BOT } from "../testing/renderScreen";
import { StaffScreen } from "./StaffScreen";
import {
  CSRF,
  INVITATION_ID,
  invitationBody,
  MANAGER_ID,
  memberBody,
  OWNER_ID,
  renderOffice,
  SELLER_ID,
  STAFF,
  TOKEN,
  transferBody,
} from "./testing";

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
  window.sessionStorage.clear();
});
afterEach(cleanup);

type State = { staff: unknown[]; invitations: unknown[]; transfer: unknown };

/** A shop's back office: its staff, invitations and ownership offer; `extra` answers first. */
function office(state: Partial<State> = {}, extra: (sent: Sent, index: number) => Reply | null = () => null) {
  const held: State = { staff: STAFF, invitations: [], transfer: null, ...state };
  const server = fakeServer((sent, index) => {
    const special = extra(sent, index);
    if (special !== null) {
      return special;
    }
    if (sent.method === "GET") {
      switch (sent.path) {
        case `${SHOP_BASE}/staff`:
          return ok({ items: held.staff });
        case `${SHOP_BASE}/staff/invitations`:
          return ok({ items: held.invitations });
        case `${SHOP_BASE}/ownership-transfer`:
          return ok({ pending: held.transfer });
        case `${SHOP_BASE}/deletion`:
          return ok({ status: "active", deletion_due: null });
      }
      return refusal(404, "NOT_FOUND", "Topilmadi.");
    }
    if (sent.path === `${SHOP_BASE}/staff/invitations`) {
      const role = (sent.body as { role: string }).role;
      held.invitations = [...held.invitations, invitationBody({ role })];
      return ok({ ...invitationBody({ role }), token: TOKEN }, 201);
    }
    if (sent.path === `${SHOP_BASE}/ownership-transfer`) {
      return ok(transferBody(), sent.method === "POST" ? 201 : 200);
    }
    if (sent.path.startsWith(`${SHOP_BASE}/staff/invitations/`)) {
      return ok({ id: INVITATION_ID, status: "cancelled" });
    }
    return ok(memberBody(MANAGER_ID, "manager"));
  });
  return { ...server, held };
}

const row = async (name: RegExp) => (await screen.findByRole("rowheader", { name })).closest("tr") as HTMLElement;
const managerRow = () => row(/Menejer · 3a1b2c/);
const sellerRow = () => row(/Sotuvchi · 3d4e5f/);
const click = (scope: HTMLElement, name: string) => fireEvent.click(within(scope).getByRole("button", { name }));

describe("who may open the staff screen", () => {
  it.each(["manager", "seller"] as const)("shows a %s nothing and asks the server nothing", async (role) => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch, role });
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent.filter((sent) => sent.path.includes("/staff"))).toHaveLength(0);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("button", { name: "Taklif havolasini yaratish" })).toBeNull();
  });
});

describe("the list of staff", () => {
  it("is a real table: a caption, column headers, and a header cell naming each member with role and status", async () => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    const table = await screen.findByRole("table", { name: "Xodimlar ro'yxati" });
    expect(table.tagName).toBe("TABLE");
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Xodim", "Holat", "Amallar"]);
    expect(within(table).getAllByRole("columnheader").every((cell) => cell.getAttribute("scope") === "col")).toBe(true);
    const names = within(table).getAllByRole("rowheader");
    expect(names.map((cell) => cell.textContent)).toEqual([
      "Do'kon egasi · 333333 (siz)",
      "Menejer · 3a1b2c",
      "Sotuvchi · 3d4e5f",
    ]);
    expect(names.every((cell) => cell.tagName === "TH" && cell.getAttribute("scope") === "row")).toBe(true);
    expect(within(await managerRow()).getByText("Faol")).toBeTruthy();
    expect(within(await sellerRow()).getByText("To'xtatilgan")).toBeTruthy();
  });

  it("offers nothing on the owner's own membership", async () => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    const owner = await row(/Do'kon egasi/);
    expect(within(owner).queryAllByRole("button")).toHaveLength(0);
    expect(within(owner).getByText("Egalik faqat do'konni o'tkazish orqali o'zgaradi.")).toBeTruthy();
  });

  it("offers a member the other role, and suspend or restore by their status", async () => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    const names = (scope: HTMLElement) => within(scope).getAllByRole("button").map((button) => button.textContent);
    expect(names(await managerRow())).toEqual(["Sotuvchi qilish", "To'xtatib qo'yish", "Olib tashlash", "Do'konni taklif qilish"]);
    expect(names(await sellerRow())).toEqual(["Menejer qilish", "Tiklash", "Olib tashlash"]);
  });

  it("shows the failure with a retry when the list cannot be read", async () => {
    let fail = true;
    const server = office({}, (sent) =>
      fail && sent.path === `${SHOP_BASE}/staff` ? refusal(403, "FORBIDDEN_ROLE", "Bu amal uchun sizning rolingiz yetarli emas.") : null,
    );
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toContain("rolingiz yetarli emas");
    fail = false;
    fireEvent.click(screen.getAllByRole("button", { name: "Qayta urinish" })[0] as HTMLElement);
    expect(await screen.findByRole("table", { name: "Xodimlar ro'yxati" })).toBeTruthy();
  });
});

describe("changing a member", () => {
  it.each([
    ["Sotuvchi qilish", "Menejer · 3a1b2c roli «Sotuvchi» ga o'zgartirilsinmi?", "PATCH", { role: "seller" }],
    ["To'xtatib qo'yish", "Menejer · 3a1b2c to'xtatib qo'yilsinmi? U do'konga kira olmay qoladi; yozuvlari saqlanadi.", "PATCH", { status: "suspended" }],
    [
      "Olib tashlash",
      "Menejer · 3a1b2c do'kondan olib tashlansinmi? Yozuvlari uning nomida qoladi. Qaytarish uchun yangi taklif kerak bo'ladi.",
      "DELETE",
      undefined,
    ],
  ] as const)("%s asks first, sends nothing on no, and one request on yes", async (label, question, method, body) => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    click(await managerRow(), label);
    expect(within(await managerRow()).getByText(question)).toBeTruthy();
    expect(server.writes()).toHaveLength(0);

    click(await managerRow(), "Yo'q");
    expect(screen.queryByText(question)).toBeNull();
    expect(server.writes()).toHaveLength(0);

    click(await managerRow(), label);
    const reads = server.sent.filter((sent) => sent.path === `${SHOP_BASE}/staff`).length;
    click(await managerRow(), "Ha");
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    const sent = server.writes()[0];
    expect(sent).toMatchObject({ method, path: `${SHOP_BASE}/staff/${MANAGER_ID}` });
    expect(sent?.body).toEqual(body);
    expect(sent?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(sent?.headers["X-CSRF-Token"]).toBe(CSRF);
    // The list is read again, so the screen shows what the server now holds.
    await waitFor(() => expect(server.sent.filter((s) => s.path === `${SHOP_BASE}/staff`).length).toBe(reads + 1));
  });

  it("restores a suspended member and can make a seller a manager", async () => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    click(await sellerRow(), "Tiklash");
    click(await sellerRow(), "Ha");
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "PATCH", path: `${SHOP_BASE}/staff/${SELLER_ID}`, body: { status: "active" } });

    click(await sellerRow(), "Menejer qilish");
    click(await sellerRow(), "Ha");
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]?.body).toEqual({ role: "manager" });
    expect(server.writes()[1]?.headers["Idempotency-Key"]).not.toBe(server.writes()[0]?.headers["Idempotency-Key"]);
  });

  it("shows the server's refusal for the owner's membership and resends the same key on a retry", async () => {
    const server = office({}, (sent) =>
      sent.method === "PATCH"
        ? refusal(409, "OWNER_MEMBERSHIP_FIXED", "Do'kon egasining a'zoligi faqat egalikni o'tkazish orqali o'zgaradi.")
        : null,
    );
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    click(await managerRow(), "To'xtatib qo'yish");
    click(await managerRow(), "Ha");
    expect((await screen.findByRole("alert")).textContent).toContain("faqat egalikni o'tkazish orqali o'zgaradi");
    // The question stays open, so the same action can be sent again.
    click(await managerRow(), "Ha");
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]?.headers["Idempotency-Key"]).toBe(server.writes()[0]?.headers["Idempotency-Key"]);
  });

  it("disables the answer while the request is in flight, so a double tap sends one", async () => {
    const gate = deferred<ReturnType<typeof ok>>();
    const server = office({}, (sent) => (sent.method === "DELETE" ? gate.promise : null));
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    click(await managerRow(), "Olib tashlash");
    const yes = within(await managerRow()).getByRole("button", { name: "Ha" });
    fireEvent.click(yes);
    fireEvent.click(yes);
    await waitFor(() => expect((within(document.body).getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement).disabled).toBe(true));
    // Every other row's buttons wait too.
    expect(within(await sellerRow()).getAllByRole("button").every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
    gate.resolve(ok(memberBody(MANAGER_ID, "manager", "removed")));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Saqlanmoqda…" })).toBeNull());
    expect(server.writes()).toHaveLength(1);
  });
});

describe("inviting", () => {
  const invite = async (role: "manager" | "seller" = "seller") => {
    const select = await screen.findByLabelText("Yangi xodim roli");
    fireEvent.change(select, { target: { value: role } });
    fireEvent.click(screen.getByRole("button", { name: "Taklif havolasini yaratish" }));
  };
  const link = `https://t.me/${BOT}?start=s_${TOKEN}`;

  it("creates the invitation for the chosen role and shows its link once, with a copy button and a QR code", async () => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    await invite("manager");
    expect(await screen.findByText(link)).toBeTruthy();
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/staff/invitations`, body: { role: "manager" } });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(server.writes()[0]?.headers["X-CSRF-Token"]).toBe(CSRF);
    expect(screen.getByText(/Bu faqat hozir, bir marta ko'rsatiladi/)).toBeTruthy();
    expect(screen.getByText("Menejer uchun taklif havolasi")).toBeTruthy();
    expect(screen.getByText("Havola 2026-yil 13-oktabr, 12:00 gacha amal qiladi va bir marta ishlatiladi.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Nusxalash" })).toBeTruthy();
    expect((await screen.findByRole("img", { name: "Havolaning QR kodi" })).tagName).toBe("svg");
    // The new invitation appears in the list of open ones.
    expect(await screen.findByRole("table", { name: "Ochiq takliflar" })).toBeTruthy();
  });

  it("copies the link to the clipboard when asked", async () => {
    const copied: string[] = [];
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async (text: string) => void copied.push(text) },
    });
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    await invite();
    fireEvent.click(await screen.findByRole("button", { name: "Nusxalash" }));
    expect(await screen.findByRole("button", { name: "Nusxalandi" })).toBeTruthy();
    expect(copied).toEqual([link]);
  });

  it("keeps the token out of the address and out of storage, and forgets it when the screen is left", async () => {
    const server = office();
    const view = renderOffice(<StaffScreen />, { fetch: server.fetch });
    await invite();
    await screen.findByText(link);
    expect(window.location.href).not.toContain(TOKEN);
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(TOKEN);
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(TOKEN);

    view.unmount();
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    await screen.findByRole("table", { name: "Xodimlar ro'yxati" });
    expect(screen.queryByText(link)).toBeNull();
    expect(document.body.innerHTML).not.toContain(TOKEN);
    expect(screen.getByRole("button", { name: "Taklif havolasini yaratish" })).toBeTruthy();
  });

  it("takes the link off the screen on request", async () => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    await invite();
    await screen.findByText(link);
    fireEvent.click(screen.getByRole("button", { name: "Havolani yashirish" }));
    expect(screen.queryByText(link)).toBeNull();
    expect(document.body.innerHTML).not.toContain(TOKEN);
  });

  it("says so when the server's answer to a repeated request no longer carries the token", async () => {
    const server = office({}, (sent) => (sent.method === "POST" ? ok({ ...invitationBody(), token: null }, 201) : null));
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    await invite();
    expect((await screen.findByRole("alert")).textContent).toContain("havola matni bu ekranga yetib kelmadi");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("shows the server's refusal and keeps the form", async () => {
    const server = office({}, (sent) =>
      sent.method === "POST" ? refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { role: "must be manager or seller" }) : null,
    );
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    await invite();
    expect((await screen.findByRole("alert")).textContent).toBe("Ma'lumotlar noto'g'ri kiritilgan.");
    expect(screen.getByRole("button", { name: "Taklif havolasini yaratish" })).toBeTruthy();
  });

  it("disables the button while the invitation is being created", async () => {
    const gate = deferred<ReturnType<typeof ok>>();
    const server = office({}, (sent) => (sent.method === "POST" ? gate.promise : null));
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    await invite();
    const button = screen.getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    gate.resolve(ok({ ...invitationBody(), token: TOKEN }, 201));
    await screen.findByText(link);
    expect(server.writes()).toHaveLength(1);
  });

  it("gives the command to send when the build names no bot, worded for the invited person", async () => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch, botUsername: null });
    await invite();
    expect(await screen.findByText(`/start s_${TOKEN}`)).toBeTruthy();
    expect(screen.getByText("Taklif qilingan odam Telegramda do'kon botiga shu buyruqni yuborsin:")).toBeTruthy();
    expect(screen.queryByText(/Mijoz Telegramda/)).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  });
});

describe("open invitations", () => {
  it("says so when there are none", async () => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    expect(await screen.findByText("Ochiq taklif yo'q.")).toBeTruthy();
  });

  it("lists them with role and expiry, and cancels one only after a yes", async () => {
    const server = office({ invitations: [invitationBody()] });
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    const table = await screen.findByRole("table", { name: "Ochiq takliflar" });
    expect(within(table).getByRole("rowheader", { name: "Sotuvchi" })).toBeTruthy();
    expect(within(table).getByText("2026-yil 13-oktabr, 12:00")).toBeTruthy();

    fireEvent.click(within(table).getByRole("button", { name: "Taklifni bekor qilish" }));
    expect(screen.getByText("Sotuvchi uchun taklif bekor qilinsinmi? Havola ishlamay qoladi.")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    fireEvent.click(within(table).getByRole("button", { name: "Yo'q" }));
    expect(server.writes()).toHaveLength(0);

    fireEvent.click(within(table).getByRole("button", { name: "Taklifni bekor qilish" }));
    fireEvent.click(within(table).getByRole("button", { name: "Ha" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "DELETE", path: `${SHOP_BASE}/staff/invitations/${INVITATION_ID}` });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toBeTruthy();
  });

  it("shows the refusal when the invitation is already gone", async () => {
    const server = office({ invitations: [invitationBody()] }, (sent) =>
      sent.method === "DELETE" ? refusal(404, "NOT_FOUND", "Topilmadi.") : null,
    );
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    const table = await screen.findByRole("table", { name: "Ochiq takliflar" });
    fireEvent.click(within(table).getByRole("button", { name: "Taklifni bekor qilish" }));
    fireEvent.click(within(table).getByRole("button", { name: "Ha" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Topilmadi.");
  });
});

describe("handing the shop over", () => {
  it("offers the shop to an active manager only, after a yes", async () => {
    const server = office({ staff: [...STAFF, memberBody("33333333-3333-4333-8333-333333777777", "manager", "suspended")] });
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    expect(await screen.findByText("Hozir javob kutayotgan taklif yo'q.")).toBeTruthy();
    expect(within(await sellerRow()).queryByRole("button", { name: "Do'konni taklif qilish" })).toBeNull();
    expect(within(await row(/Menejer · 777777/)).queryByRole("button", { name: "Do'konni taklif qilish" })).toBeNull();

    click(await managerRow(), "Do'konni taklif qilish");
    expect(within(await managerRow()).getByText(/Do'kon egaligi Menejer · 3a1b2c ga taklif qilinsinmi\?/)).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    server.held.transfer = transferBody();
    click(await managerRow(), "Ha");
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({
      method: "POST",
      path: `${SHOP_BASE}/ownership-transfer`,
      body: { membership_id: MANAGER_ID },
    });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toBeTruthy();
    // The offer is read again and shown as waiting, with its expiry.
    expect(
      await screen.findByText("Do'kon egaligi Menejer · 3a1b2c ga taklif qilingan. Taklif 2026-yil 8-oktabr, 12:00 gacha amal qiladi."),
    ).toBeTruthy();
  });

  it("offers the shop to no one else while an offer waits, and takes the offer back after a yes", async () => {
    const server = office({ transfer: transferBody() });
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    await screen.findByText(/taklif qilingan\. Taklif/);
    expect(within(await managerRow()).queryByRole("button", { name: "Do'konni taklif qilish" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Taklifni qaytarib olish" }));
    expect(screen.getByText("Egalikni o'tkazish taklifi qaytarib olinsinmi?")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    server.held.transfer = null;
    fireEvent.click(screen.getByRole("button", { name: "Ha" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "DELETE", path: `${SHOP_BASE}/ownership-transfer` });
    expect(await screen.findByText("Hozir javob kutayotgan taklif yo'q.")).toBeTruthy();
  });

  it.each([
    ["TRANSFER_PENDING", "Egalikni o'tkazish taklifi allaqachon javob kutmoqda."],
    ["TRANSFER_TARGET_INVALID", "Egalikni faqat shu do'konning faol menejeriga o'tkazish mumkin."],
  ])("shows the server's %s refusal", async (code, message) => {
    const server = office({}, (sent) => (sent.method === "POST" ? refusal(409, code, message) : null));
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    click(await managerRow(), "Do'konni taklif qilish");
    click(await managerRow(), "Ha");
    expect((await screen.findByRole("alert")).textContent).toContain(message);
  });

  it("shows the refusal when the offer cannot be taken back", async () => {
    const server = office({ transfer: transferBody() }, (sent) =>
      sent.method === "DELETE" ? refusal(404, "NOT_FOUND", "Topilmadi.") : null,
    );
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    fireEvent.click(await screen.findByRole("button", { name: "Taklifni qaytarib olish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Topilmadi.");
  });

  it("names the owner's membership in the test data as the signed-in person", () => {
    expect(STAFF[0]?.id).toBe(OWNER_ID);
  });
});
