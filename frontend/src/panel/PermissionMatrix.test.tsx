// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../testing/fakeServer";
import { changesFor } from "./PermissionMatrix";
import { StaffScreen } from "./StaffScreen";
import { MANAGER_ID, memberBody, OWNER_ID, renderOffice, SELLER_ID, tabStops } from "./testing";

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
  window.sessionStorage.clear();
});
afterEach(cleanup);

const OWNER_HOLDS = ["staff.manage", "permissions.manage", "ledger.view"];
const STAFF = [memberBody(OWNER_ID, "owner"), memberBody(MANAGER_ID, "manager"), memberBody(SELLER_ID, "seller")];

const permission = (key: string, uz: string, ru: string, roles: string[], fixed = false) => ({
  key,
  label: { uz, ru },
  roles,
  fixed,
});
const ALL = ["seller", "manager", "owner"];
const MANAGERS = ["manager", "owner"];

/** A catalogue as the server gives it, cut down to two areas and one group that is the owner's alone. */
const CATALOGUE = {
  groups: [
    {
      key: "ledger",
      label: { uz: "Qarz va to'lovlar", ru: "Долги и оплаты" },
      permissions: [
        permission("credits.record", "Qarzga savdo yozish", "Записывать продажу в долг", ALL),
        permission("payments.record", "To'lov qabul qilish", "Принимать оплату", ALL),
        permission("entries.cancel", "Yozuvni bekor qilish", "Отменять запись", MANAGERS),
      ],
    },
    {
      key: "reports",
      label: { uz: "Hisobot va fayllar", ru: "Отчёты и файлы" },
      permissions: [permission("reports.view", "Hisobotlarni ko'rish", "Просматривать отчёты", MANAGERS)],
    },
    {
      key: "owner",
      label: { uz: "Faqat egasi", ru: "Только владелец" },
      permissions: [permission("shop.delete", "Do'konni o'chirish", "Удалять магазин", ["owner"], true)],
    },
  ],
};

const SELLER_DEFAULTS = ["credits.record", "payments.record"];

/** What the server answers for a seller whose changes are `granted` and `denied`. */
function sellerHolds(granted: string[] = [], denied: string[] = []) {
  const keys = CATALOGUE.groups.flatMap((group) => group.permissions);
  return {
    membership_id: SELLER_ID,
    role: "seller",
    status: "active",
    granted,
    denied,
    permissions: keys.map((item) => {
      const byRole = SELLER_DEFAULTS.includes(item.key);
      const allowed = !item.fixed && (granted.includes(item.key) || (byRole && !denied.includes(item.key)));
      const source = item.fixed ? "role" : granted.includes(item.key) ? "granted" : denied.includes(item.key) ? "denied" : "role";
      return { key: item.key, allowed, source, default: byRole, fixed: item.fixed };
    }),
  };
}

function office(extra: (sent: Sent) => Reply | null = () => null, start: { granted?: string[]; denied?: string[] } = {}) {
  const held = { granted: start.granted ?? [], denied: start.denied ?? [] };
  const server = fakeServer((sent) => {
    const special = extra(sent);
    if (special !== null) {
      return special;
    }
    const member = `${SHOP_BASE}/staff/${SELLER_ID}/permissions`;
    if (sent.method === "PUT" && sent.path === member) {
      const body = sent.body as { granted: string[]; denied: string[] };
      held.granted = body.granted;
      held.denied = body.denied;
      return ok(sellerHolds(held.granted, held.denied));
    }
    switch (sent.path) {
      case `${SHOP_BASE}/staff`:
        return ok({ items: STAFF });
      case `${SHOP_BASE}/staff/invitations`:
        return ok({ items: [] });
      case `${SHOP_BASE}/ownership-transfer`:
        return ok({ pending: null });
      case `${SHOP_BASE}/deletion`:
        return ok({ status: "active", deletion_due: null });
      case `${SHOP_BASE}/permissions`:
        return ok(CATALOGUE);
      case member:
        return ok(sellerHolds(held.granted, held.denied));
    }
    return refusal(404, "NOT_FOUND", "Topilmadi.");
  });
  return { ...server, held };
}

const row = async (name: RegExp) => (await screen.findByRole("rowheader", { name })).closest("tr") as HTMLElement;
const sellerRow = () => row(/Sotuvchi · 3d4e5f/);

async function openSeller(server: ReturnType<typeof office>, language: "uz" | "ru" = "uz") {
  renderOffice(<StaffScreen />, { fetch: server.fetch, permissions: OWNER_HOLDS, language });
  const seller = await row(language === "uz" ? /Sotuvchi · 3d4e5f/ : /Продавец · 3d4e5f/);
  fireEvent.click(await within(seller).findByRole("button", { name: language === "uz" ? "Ruxsatlar" : "Разрешения" }));
  await screen.findByRole("group", { name: language === "uz" ? "Qarz va to'lovlar" : "Долги и оплаты" });
}

const box = (name: RegExp) => screen.getByRole("checkbox", { name }) as HTMLInputElement;

describe("when the server keeps to roles", () => {
  it("shows no permissions button and asks for no catalogue", async () => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch });
    const seller = await sellerRow();
    expect(within(seller).queryByRole("button", { name: "Ruxsatlar" })).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent.filter((sent) => sent.path.endsWith("/permissions"))).toHaveLength(0);
  });

  it("shows no permissions button when the catalogue route does not answer", async () => {
    const server = office((sent) => (sent.path === `${SHOP_BASE}/permissions` ? refusal(404, "NOT_FOUND", "Topilmadi.") : null));
    renderOffice(<StaffScreen />, { fetch: server.fetch, permissions: OWNER_HOLDS });
    const seller = await sellerRow();
    await waitFor(() => expect(server.sent.some((sent) => sent.path === `${SHOP_BASE}/permissions`)).toBe(true));
    expect(within(seller).queryByRole("button", { name: "Ruxsatlar" })).toBeNull();
  });
});

describe("the matrix of one member", () => {
  it("offers it for a seller and a manager, never for the owner", async () => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch, permissions: OWNER_HOLDS });
    expect(await within(await sellerRow()).findByRole("button", { name: "Ruxsatlar" })).toBeTruthy();
    expect(within(await row(/Menejer · 3a1b2c/)).getByRole("button", { name: "Ruxsatlar" })).toBeTruthy();
    expect(within(await row(/Do'kon egasi/)).queryByRole("button", { name: "Ruxsatlar" })).toBeNull();
  });

  it("is a group of real, labelled checkboxes per area, ticked as the member holds them", async () => {
    await openSeller(office());
    expect(screen.getByRole("heading", { name: "Sotuvchi · 3d4e5f: ruxsatlar" })).toBeTruthy();
    const ledger = screen.getByRole("group", { name: "Qarz va to'lovlar" });
    expect(ledger.tagName).toBe("FIELDSET");
    const boxes = within(ledger).getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.map((input) => [input.closest("label")?.textContent, input.checked])).toEqual([
      ["Qarzga savdo yozishrol bo'yicha", true],
      ["To'lov qabul qilishrol bo'yicha", true],
      ["Yozuvni bekor qilishrol bo'yicha", false],
    ]);
    expect(boxes.every((input) => input.tagName === "INPUT" && input.type === "checkbox")).toBe(true);
    expect(within(screen.getByRole("group", { name: "Hisobot va fayllar" })).getAllByRole("checkbox")).toHaveLength(1);
  });

  it("leaves out what is the owner's alone, and says why", async () => {
    await openSeller(office());
    expect(screen.queryByRole("group", { name: "Faqat egasi" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: /Do'konni o'chirish/ })).toBeNull();
    expect(screen.getByText(/faqat do'kon egasida qoladi/)).toBeTruthy();
  });

  it("says in words, beside each box, what was granted and what was taken away", async () => {
    await openSeller(office(() => null, { granted: ["reports.view"], denied: ["payments.record"] }));
    expect(box(/Hisobotlarni ko'rish/).checked).toBe(true);
    expect(box(/Hisobotlarni ko'rish/).closest("label")?.textContent).toContain("qo'shimcha berilgan");
    expect(box(/To'lov qabul qilish/).checked).toBe(false);
    expect(box(/To'lov qabul qilish/).closest("label")?.textContent).toContain("olib qo'yilgan");
    expect(box(/Qarzga savdo yozish/).closest("label")?.textContent).toContain("rol bo'yicha");
  });

  it("can be worked with the keyboard: each box and each button is a tab stop", async () => {
    await openSeller(office());
    const form = screen.getByRole("group", { name: "Qarz va to'lovlar" }).closest("form") as HTMLElement;
    const stops = tabStops(form);
    expect(stops.filter((element) => element.tagName === "INPUT")).toHaveLength(4);
    expect(stops.at(-1)?.textContent).toBe("Yopish");
  });

  it("sends only what differs from the role, once, and shows what the server stored", async () => {
    const server = office();
    await openSeller(server);
    const save = screen.getByRole("button", { name: "Ruxsatlarni saqlash" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    fireEvent.click(box(/Hisobotlarni ko'rish/));
    fireEvent.click(box(/To'lov qabul qilish/));
    expect(box(/Hisobotlarni ko'rish/).closest("label")?.textContent).toContain("qo'shimcha berilgan");
    expect(save.disabled).toBe(false);
    fireEvent.click(save);

    expect((await screen.findByRole("status")).textContent).toContain("Saqlandi");
    const writes = server.writes();
    expect(writes).toHaveLength(1);
    expect(writes[0]?.method).toBe("PUT");
    expect(writes[0]?.path).toBe(`${SHOP_BASE}/staff/${SELLER_ID}/permissions`);
    expect(writes[0]?.body).toEqual({ granted: ["reports.view"], denied: ["payments.record"] });
    expect(writes[0]?.headers["Idempotency-Key"]).toBeTruthy();
    expect(box(/Hisobotlarni ko'rish/).checked).toBe(true);
    expect((screen.getByRole("button", { name: "Ruxsatlarni saqlash" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("ticking a box back to the role's default leaves nothing to save", async () => {
    await openSeller(office());
    fireEvent.click(box(/Yozuvni bekor qilish/));
    fireEvent.click(box(/Yozuvni bekor qilish/));
    expect((screen.getByRole("button", { name: "Ruxsatlarni saqlash" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("resets to the role's defaults only after a yes, by sending two empty lists", async () => {
    const server = office(() => null, { granted: ["reports.view"], denied: ["payments.record"] });
    await openSeller(server);
    fireEvent.click(screen.getByRole("button", { name: "Rolning odatiy ruxsatlariga qaytarish" }));
    expect(
      screen.getByText("Sotuvchi · 3d4e5f uchun barcha o'zgartirishlar bekor qilinib, «Sotuvchi» rolining odatiy ruxsatlari qaytarilsinmi?"),
    ).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Ha" }));
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ granted: [], denied: [] });
    expect(box(/Hisobotlarni ko'rish/).checked).toBe(false);
    expect(box(/To'lov qabul qilish/).checked).toBe(true);
    expect(box(/To'lov qabul qilish/).closest("label")?.textContent).toContain("rol bo'yicha");
  });

  it("has nothing to reset for a member who is exactly their role, and a no changes nothing", async () => {
    const server = office(() => null, { granted: ["reports.view"] });
    await openSeller(server);
    fireEvent.click(screen.getByRole("button", { name: "Rolning odatiy ruxsatlariga qaytarish" }));
    fireEvent.click(screen.getByRole("button", { name: "Yo'q" }));
    expect(server.writes()).toHaveLength(0);
    expect(box(/Hisobotlarni ko'rish/).checked).toBe(true);
    cleanup();
    await openSeller(office());
    expect((screen.getByRole("button", { name: "Rolning odatiy ruxsatlariga qaytarish" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the server's refusal and keeps what was ticked", async () => {
    const server = office((sent) =>
      sent.method === "PUT" ? refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { granted: "unknown permission" }) : null,
    );
    await openSeller(server);
    fireEvent.click(box(/Hisobotlarni ko'rish/));
    fireEvent.click(screen.getByRole("button", { name: "Ruxsatlarni saqlash" }));
    expect((await screen.findByRole("alert")).textContent).toContain("noto'g'ri kiritilgan");
    expect(box(/Hisobotlarni ko'rish/).checked).toBe(true);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("closes without sending anything", async () => {
    const server = office();
    await openSeller(server);
    fireEvent.click(box(/Hisobotlarni ko'rish/));
    fireEvent.click(screen.getByRole("button", { name: "Yopish" }));
    expect(screen.queryByRole("group", { name: "Qarz va to'lovlar" })).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it("is in Russian for a Russian reader, labels included", async () => {
    await openSeller(office(() => null, { granted: ["reports.view"] }), "ru");
    expect(box(/Просматривать отчёты/).closest("label")?.textContent).toContain("выдано дополнительно");
    expect(screen.getByRole("button", { name: "Вернуть разрешения роли по умолчанию" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Сохранить разрешения" })).toBeTruthy();
  });
});

describe("what is sent for a set of ticks", () => {
  const held = {
    membershipId: SELLER_ID,
    role: "seller",
    permissions: [
      { key: "payments.record", allowed: true, source: "role", byRole: true, fixed: false },
      { key: "reports.view", allowed: false, source: "role", byRole: false, fixed: false },
      { key: "shop.delete", allowed: false, source: "role", byRole: false, fixed: true },
      { key: "membership.own", allowed: true, source: "role", byRole: true, fixed: true },
    ],
  };

  it("is nothing for the role's own set", () => {
    expect(changesFor(held, new Set(["payments.record", "membership.own"]))).toEqual({ granted: [], denied: [] });
  });

  it("is a grant for a tick the role does not give and a denial for a missing one it does", () => {
    expect(changesFor(held, new Set(["reports.view"]))).toEqual({ granted: ["reports.view"], denied: ["payments.record"] });
  });

  it("never names a permission that cannot be changed, ticked or not", () => {
    expect(changesFor(held, new Set(["shop.delete"]))).toEqual({ granted: [], denied: ["payments.record"] });
    expect(changesFor(held, new Set(["payments.record"]))).toEqual({ granted: [], denied: [] });
  });
});

describe("someone the owner let manage staff", () => {
  const HOLDS = ["staff.manage", "ledger.view"];

  it("opens the staff screen as a manager, without the matrix, the roles or the ownership", async () => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch, role: "manager", membershipId: MANAGER_ID, permissions: HOLDS });
    const names = (scope: HTMLElement) => within(scope).queryAllByRole("button").map((button) => button.textContent);
    expect(names(await sellerRow())).toEqual(["To'xtatib qo'yish", "Olib tashlash"]);
    expect(names(await row(/Menejer · 3a1b2c/))).toEqual([]);
    expect(within(await row(/Menejer · 3a1b2c/)).getByText("Buni faqat do'kon egasi o'zgartira oladi.")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Egalikni o'tkazish" })).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent.filter((sent) => sent.path.endsWith("/permissions"))).toHaveLength(0);
  });

  it("may invite a seller only", async () => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch, role: "manager", membershipId: MANAGER_ID, permissions: HOLDS });
    const select = (await screen.findByLabelText("Yangi xodim roli")) as HTMLSelectElement;
    expect([...select.options].map((option) => option.value)).toEqual(["seller"]);
  });

  it("is shown nothing without the permission, whatever the role", async () => {
    const server = office();
    renderOffice(<StaffScreen />, { fetch: server.fetch, role: "owner", permissions: ["ledger.view"] });
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent.filter((sent) => sent.path.includes("/staff"))).toHaveLength(0);
  });
});
