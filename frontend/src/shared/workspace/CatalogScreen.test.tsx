// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { deferred, fakeServer, itemBody, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { CatalogItem } from "../api";
import type { Role } from "../navigation";
import { CATALOG_SEARCH_DELAY_MS, CatalogScreen, itemPatch } from "./CatalogScreen";

afterEach(cleanup);

const NON_ID = "44444444-4444-4444-8444-444444444441";
const SUT_ID = "44444444-4444-4444-8444-444444444442";
const LEARNED_ID = "55555555-5555-4555-8555-555555555551";
const CATALOG = `${SHOP_BASE}/catalog`;

const NON = itemBody({ id: NON_ID, name: "Non", unit: "dona", price: 4000 });
const SUT = itemBody({ id: SUT_ID, name: "Sut", unit: "l", price: 12000 });
const HIDDEN = itemBody({ id: "44444444-4444-4444-8444-444444444448", name: "Eski choy", unit: "quti", price: 9000, status: "hidden" });
const LEARNED = itemBody({ id: LEARNED_ID, name: "non buxanka", unit: "dona", price: 4500, learned: true });

const page = (items: unknown[], nextCursor: string | null = null) => ok({ items, next_cursor: nextCursor });

/** A catalog with two shown items, a hidden one and a learned one; `onWrite` answers every write. */
function shop(onWrite: (sent: Sent, attempt: number) => Reply = () => ok(NON)) {
  let attempt = 0;
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return onWrite(sent, attempt++);
    }
    const q = (sent.query["q"] ?? "").toLowerCase();
    const matching = (items: unknown[]) => page(items.filter((item) => (item as { name: string }).name.toLowerCase().includes(q)));
    if (sent.query["status"] === "hidden") {
      return matching([HIDDEN]);
    }
    if (sent.query["learned"] === "true") {
      return matching([LEARNED]);
    }
    if (sent.query["learned"] === "false") {
      return matching([NON, SUT]);
    }
    return matching([LEARNED, NON, SUT]);
  });
}

async function open(server: ReturnType<typeof fakeServer>, role: Role = "manager") {
  renderScreen(<CatalogScreen />, { fetch: server.fetch, role });
  await screen.findByText("Non");
}

const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });
const rows = () => Array.from(document.querySelectorAll<HTMLElement>("ul.rows > li"));
const rowOf = (name: string) => rows().find((row) => row.querySelector(".row__name")?.textContent === name) as HTMLElement;
const inRow = (name: string, button: string) => within(rowOf(name)).getByRole<HTMLButtonElement>("button", { name: button });
const view = (name: string) => within(screen.getByRole("group", { name: "Katalog ko'rinishi" })).getByRole("button", { name });
const search = () => screen.getByRole<HTMLInputElement>("searchbox", { name: "Mahsulot nomi bo'yicha qidirish" });
const reads = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.method === "GET");
const key = (sent: Sent | undefined) => sent?.headers["Idempotency-Key"];

describe("catalog list (REQ-039)", () => {
  it("lists the shown goods with their price and unit", async () => {
    const server = shop();
    renderScreen(<CatalogScreen />, { fetch: server.fetch, role: "seller" });
    expect(screen.getByRole("status").textContent).toBe("Yuklanmoqda…");
    await screen.findByText("Non");
    expect(server.sent[0]).toMatchObject({ method: "GET", path: CATALOG, query: { status: "active" } });
    expect(server.sent[0]?.query).not.toHaveProperty("learned");
    expect(rowOf("Non").textContent).toBe("Non4 000 so'm / dona");
    expect(rowOf("Sut").textContent).toBe("Sut12 000 so'm / l");
    expect(rowOf("non buxanka").textContent).toContain("Yangi: hali ko'rib chiqilmagan");
  });

  it("searches by what was typed once typing pauses, and at once when the form is submitted", async () => {
    const server = shop();
    await open(server);
    for (const typed of ["s", "su", " sut "]) {
      type(search(), typed);
      await act(() => new Promise((resolve) => setTimeout(resolve, CATALOG_SEARCH_DELAY_MS / 6)));
    }
    await waitFor(() => expect(screen.queryByText("Non")).toBeNull());
    expect(rows()).toHaveLength(1);
    expect(reads(server).map((sent) => sent.query["q"])).toEqual([undefined, "sut"]);

    type(search(), "non");
    fireEvent.submit(screen.getByRole("search"));
    await screen.findByText("Non");
    expect(reads(server).at(-1)?.query).toEqual({ q: "non", status: "active" });
  });

  it("reads the next page with the server's cursor", async () => {
    const server = fakeServer((sent) => (sent.query["cursor"] === "c1" ? page([SUT]) : page([NON], "c1")));
    renderScreen(<CatalogScreen />, { fetch: server.fetch });
    await screen.findByText("Non");
    fireEvent.click(screen.getByRole("button", { name: "Yana ko'rsatish" }));
    await screen.findByText("Sut");
    expect(rows()).toHaveLength(2);
    expect(server.sent[1]?.query).toEqual({ status: "active", cursor: "c1" });
    expect(screen.queryByRole("button", { name: "Yana ko'rsatish" })).toBeNull();
  });

  it("shows the hidden goods when asked", async () => {
    const server = shop();
    await open(server, "seller");
    fireEvent.click(view("Yashirilgan"));
    await screen.findByText("Eski choy");
    expect(reads(server).at(-1)?.query).toEqual({ status: "hidden" });
    expect(screen.queryByText("Non")).toBeNull();
    expect(view("Yashirilgan").getAttribute("aria-pressed")).toBe("true");
  });

  it.each([
    ["active", null, "Katalogda hali mahsulot yo'q."],
    ["hidden", "Yashirilgan", "Yashirilgan mahsulot yo'q."],
    ["review", "Ko'rib chiqish", "Ko'rib chiqiladigan yangi mahsulot yo'q."],
  ])("says so when the %s list is empty", async (_view, button, message) => {
    const server = fakeServer(() => page([]));
    renderScreen(<CatalogScreen />, { fetch: server.fetch, role: "manager" });
    await screen.findByText("Katalogda hali mahsulot yo'q.");
    if (button !== null) {
      fireEvent.click(view(button));
    }
    expect(await screen.findByText(message)).toBeTruthy();
  });

  it("says nothing matched for a search without results", async () => {
    const server = shop();
    await open(server);
    type(search(), "zzz");
    fireEvent.submit(screen.getByRole("search"));
    expect(await screen.findByText("Hech narsa topilmadi.")).toBeTruthy();
  });

  it("shows the server's message and a retry when the list cannot be read", async () => {
    let attempt = 0;
    const server = fakeServer(() => (attempt++ === 0 ? refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.") : page([NON])));
    renderScreen(<CatalogScreen />, { fetch: server.fetch });
    expect((await screen.findByRole("alert")).textContent).toContain("Do'kon to'xtatilgan.");
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Non")).toBeTruthy();
  });
});

describe("catalog by role", () => {
  it("gives a seller the list and nothing that changes it", async () => {
    const server = shop();
    await open(server, "seller");
    expect(rows()).toHaveLength(3);
    for (const name of ["Yangi mahsulot", "Tahrirlash", "Yashirish", "Ko'rsatish", "Qabul qilish", "Rad etish", "Birlashtirish"]) {
      expect(screen.queryByRole("button", { name }), name).toBeNull();
    }
    // No review queue either: only the two lists a seller reads.
    expect(within(screen.getByRole("group", { name: "Katalog ko'rinishi" })).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Ko'rinadigan",
      "Yashirilgan",
    ]);
    fireEvent.click(view("Yashirilgan"));
    await screen.findByText("Eski choy");
    expect(screen.queryByRole("button", { name: "Ko'rsatish" })).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it.each(["manager", "owner"] as const)("gives a %s every control", async (role) => {
    await open(shop(), role);
    expect(screen.getByRole("button", { name: "Yangi mahsulot" })).toBeTruthy();
    expect(inRow("Non", "Tahrirlash")).toBeTruthy();
    expect(inRow("Non", "Yashirish")).toBeTruthy();
    expect(view("Ko'rib chiqish")).toBeTruthy();
    // Review actions only on an item that is still learned.
    expect(within(rowOf("Non")).queryByRole("button", { name: "Qabul qilish" })).toBeNull();
    expect(inRow("non buxanka", "Qabul qilish")).toBeTruthy();
    expect(inRow("non buxanka", "Rad etish")).toBeTruthy();
    expect(inRow("non buxanka", "Birlashtirish")).toBeTruthy();
  });
});

describe("adding and changing an item", () => {
  const newForm = () => screen.getByRole("form", { name: "Yangi mahsulot" });
  const field = (form: HTMLElement, label: string) => within(form).getByLabelText<HTMLInputElement>(label);
  const save = (form: HTMLElement) => within(form).getByRole<HTMLButtonElement>("button", { name: "Saqlash" });

  it("creates an item with a name, a unit and a whole price, then reads the list again", async () => {
    const server = shop(() => ok(itemBody({ name: "Qora choy", unit: "quti", price: 18000 }), 201));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Yangi mahsulot" }));
    const form = newForm();
    type(field(form, "Nomi"), "  Qora   choy ");
    type(field(form, "O'lchov birligi (ixtiyoriy)"), " quti ");
    type(field(form, "Narxi, so'm"), "18 000");
    const before = reads(server).length;
    fireEvent.click(save(form));

    await waitFor(() => expect(screen.queryByRole("form", { name: "Yangi mahsulot" })).toBeNull());
    const write = server.writes()[0];
    expect(write).toMatchObject({ method: "POST", path: CATALOG });
    expect(write?.body).toEqual({ name: "Qora choy", unit: "quti", price: 18000 });
    expect(key(write)).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    await waitFor(() => expect(reads(server).length).toBe(before + 1));
  });

  it("leaves the unit out when none was typed, so the server counts in pieces", async () => {
    const server = shop(() => ok(itemBody({ name: "Gugurt", price: 500 }), 201));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Yangi mahsulot" }));
    type(field(newForm(), "Nomi"), "Gugurt");
    type(field(newForm(), "Narxi, so'm"), "500");
    fireEvent.click(save(newForm()));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ name: "Gugurt", price: 500 });
  });

  it.each([
    ["", "4000", "Mahsulot nomini kiriting."],
    ["   ", "4000", "Mahsulot nomini kiriting."],
    ["n".repeat(81), "4000", "Nom 80 belgidan oshmasligi kerak."],
    ["Non", "", "Narxni kiriting."],
    ["Non", "0", "Narx 1 so'mdan 100 000 000 so'mgacha bo'lishi kerak."],
    ["Non", "100000001", "Narx 1 so'mdan 100 000 000 so'mgacha bo'lishi kerak."],
    ["Non", "4000,50", "Narx butun so'mda bo'lishi kerak, masalan 4000."],
    ["Non", "arzon", "Narx butun so'mda bo'lishi kerak, masalan 4000."],
  ])("refuses the name %j with the price %j without calling the server", async (name, price, message) => {
    const server = shop();
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Yangi mahsulot" }));
    type(field(newForm(), "Nomi"), name);
    type(field(newForm(), "Narxi, so'm"), price);
    fireEvent.click(save(newForm()));
    expect(within(newForm()).getByRole("alert").textContent).toBe(message);
    expect(server.writes()).toHaveLength(0);
  });

  it("shows a taken name next to the name field and keeps the form", async () => {
    const message = "Katalogda shu nomli mahsulot bor (yashirilgan bo'lishi ham mumkin).";
    const server = shop(() => refusal(409, "CATALOG_NAME_TAKEN", message, { existing_id: NON_ID, existing_status: "hidden" }));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Yangi mahsulot" }));
    type(field(newForm(), "Nomi"), "Non");
    type(field(newForm(), "Narxi, so'm"), "4000");
    fireEvent.click(save(newForm()));
    const alert = await within(newForm()).findByRole("alert");
    expect(alert.textContent).toBe(message);
    expect(field(newForm(), "Nomi").getAttribute("aria-invalid")).toBe("true");
    expect(field(newForm(), "Nomi").value).toBe("Non");
  });

  it("puts a validation refusal next to the fields it is about", async () => {
    const server = shop(() => refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { unit: "x", name: "y", price: "z" }));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Yangi mahsulot" }));
    type(field(newForm(), "Nomi"), "'");
    type(field(newForm(), "O'lchov birligi (ixtiyoriy)"), "123");
    type(field(newForm(), "Narxi, so'm"), "4000");
    fireEvent.click(save(newForm()));
    await within(newForm()).findAllByRole("alert");
    expect(within(newForm()).getAllByRole("alert").map((alert) => alert.textContent)).toEqual([
      "Ma'lumotlar noto'g'ri kiritilgan.",
      "Nomda harf yoki raqam bo'lishi kerak, uzunligi 80 belgigacha.",
      "Birlik qisqa bo'lsin va harf bilan yozilsin, masalan kg yoki dona.",
      "Narx butun so'mda bo'lishi kerak, masalan 4000.",
    ]);
  });

  it("sends one request for a double tap and the same key on a retry", async () => {
    const answer = deferred<{ status: number; body: unknown }>();
    const server = shop((_sent, attempt) => (attempt === 0 ? "offline" : answer.promise));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Yangi mahsulot" }));
    type(field(newForm(), "Nomi"), "Gugurt");
    type(field(newForm(), "Narxi, so'm"), "500");
    fireEvent.click(save(newForm()));
    expect((await within(newForm()).findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi.");

    const button = save(newForm());
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.submit(newForm());
    expect(within(newForm()).getByRole<HTMLButtonElement>("button", { name: "Saqlanmoqda…" }).disabled).toBe(true);
    expect(server.writes()).toHaveLength(2);
    answer.resolve(ok(itemBody({ name: "Gugurt", price: 500 }), 201));
    await waitFor(() => expect(screen.queryByRole("form", { name: "Yangi mahsulot" })).toBeNull());
    expect(server.writes()).toHaveLength(2);
    expect(key(server.writes()[1])).toBe(key(server.writes()[0]));
  });

  it("changes only what was edited", async () => {
    const server = shop(() => ok(itemBody({ price: 4500 })));
    await open(server);
    fireEvent.click(inRow("Non", "Tahrirlash"));
    const form = within(rowOf("Non")).getByRole("form", { name: "Tahrirlash" });
    expect(field(form, "Nomi").value).toBe("Non");
    expect(field(form, "O'lchov birligi (ixtiyoriy)").value).toBe("dona");
    expect(field(form, "Narxi, so'm").value).toBe("4000");
    type(field(form, "Narxi, so'm"), "4500");
    fireEvent.click(save(form));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "PATCH", path: `${CATALOG}/${NON_ID}` });
    expect(server.writes()[0]?.body).toEqual({ price: 4500 });
    expect(key(server.writes()[0])).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
  });

  it("sends nothing when nothing was changed, and closes the form", async () => {
    const server = shop();
    await open(server);
    fireEvent.click(inRow("Non", "Tahrirlash"));
    fireEvent.click(save(within(rowOf("Non")).getByRole("form", { name: "Tahrirlash" })));
    await waitFor(() => expect(within(rowOf("Non")).queryByRole("form")).toBeNull());
    expect(server.writes()).toHaveLength(0);
  });

  it("works out the change field by field", () => {
    const item: CatalogItem = { id: NON_ID, name: "Non", unit: "dona", price: 4000, learned: false, status: "active", mergedInto: null };
    expect(itemPatch(item, { name: "Non", unit: "dona", price: 4000 })).toBeNull();
    expect(itemPatch(item, { name: "Buxanka non", unit: "dona", price: 4000 })).toEqual({ name: "Buxanka non" });
    expect(itemPatch(item, { name: "Non", unit: "", price: 4000 })).toEqual({ unit: "" });
    expect(itemPatch(item, { name: "Yopgan non", unit: "ta", price: 5000 })).toEqual({ name: "Yopgan non", unit: "ta", price: 5000 });
  });
});

describe("hiding, showing and reviewing", () => {
  it.each([
    ["Non", "Yashirish", NON_ID, "hide", null],
    ["Eski choy", "Ko'rsatish", "44444444-4444-4444-8444-444444444448", "unhide", "Yashirilgan"],
    ["non buxanka", "Qabul qilish", LEARNED_ID, "accept", "Ko'rib chiqish"],
    ["non buxanka", "Rad etish", LEARNED_ID, "dismiss", "Ko'rib chiqish"],
  ])("%s: %s posts to the item's %s and reads the list again", async (name, button, id, action, tab) => {
    const server = shop(() => ok(NON));
    await open(server);
    if (tab !== null) {
      fireEvent.click(view(tab));
      await screen.findByText(name);
    }
    const before = reads(server).length;
    fireEvent.click(inRow(name, button));
    await waitFor(() => expect(reads(server).length).toBe(before + 1));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${CATALOG}/${id}/${action}` });
    expect(server.writes()[0]?.body).toBeUndefined();
    expect(key(server.writes()[0])).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
  });

  it("asks for the review queue: shown items that are still learned", async () => {
    const server = shop();
    await open(server);
    fireEvent.click(view("Ko'rib chiqish"));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(reads(server).at(-1)?.query).toEqual({ status: "active", learned: "true" });
    expect(screen.getByText(/^Bu mahsulotlarni sotuvchilar/)).toBeTruthy();
  });

  it("sends one request for a double tap and locks the other rows meanwhile", async () => {
    const answer = deferred<{ status: number; body: unknown }>();
    const server = shop(() => answer.promise);
    await open(server);
    const hide = inRow("Non", "Yashirish");
    fireEvent.click(hide);
    fireEvent.click(hide);
    fireEvent.click(inRow("Sut", "Yashirish"));
    expect(inRow("Sut", "Yashirish").disabled).toBe(true);
    expect(server.writes()).toHaveLength(1);
    answer.resolve(ok(NON));
    await waitFor(() => expect(inRow("Sut", "Yashirish").disabled).toBe(false));
    expect(server.writes()).toHaveLength(1);
  });

  it("resends the same key when the same action is retried after a lost connection", async () => {
    const server = shop((_sent, attempt) => (attempt === 0 ? "offline" : ok(NON)));
    await open(server);
    fireEvent.click(inRow("Non", "Yashirish"));
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi.");
    fireEvent.click(inRow("Non", "Yashirish"));
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(key(server.writes()[1])).toBe(key(server.writes()[0]));
  });

  it("shows the server's refusal of a review that someone else already made", async () => {
    const server = shop(() => refusal(409, "CATALOG_ITEM_NOT_LEARNED", "Bu mahsulot allaqachon ko'rib chiqilgan."));
    await open(server);
    fireEvent.click(inRow("non buxanka", "Qabul qilish"));
    expect((await screen.findByRole("alert")).textContent).toBe("Bu mahsulot allaqachon ko'rib chiqilgan.");
  });

  describe("merging a learned item", () => {
    const candidates = () => screen.getByRole("list", { name: "Birlashtirish mumkin bo'lgan mahsulotlar" });
    async function chooseTarget(server: ReturnType<typeof fakeServer>, target = "Non") {
      await open(server);
      fireEvent.click(inRow("non buxanka", "Birlashtirish"));
      fireEvent.click(await within(rowOf("non buxanka")).findByRole("button", { name: new RegExp(`^${target}`) }));
    }

    it("offers only shown, reviewed items, found by search", async () => {
      const server = shop();
      await open(server);
      fireEvent.click(inRow("non buxanka", "Birlashtirish"));
      await waitFor(() => expect(within(candidates()).getAllByRole("listitem")).toHaveLength(2));
      expect(reads(server).at(-1)?.query).toEqual({ status: "active", learned: "false", limit: "10" });

      type(screen.getByLabelText("Qaysi mahsulotga birlashtiriladi? Nomini yozing"), "sut");
      await waitFor(() => expect(within(candidates()).getAllByRole("listitem")).toHaveLength(1));
      expect(reads(server).at(-1)?.query).toEqual({ q: "sut", status: "active", learned: "false", limit: "10" });
      expect(server.writes()).toHaveLength(0);
    });

    it("merges into the chosen item only after a yes", async () => {
      const server = shop(() => ok(itemBody({ id: LEARNED_ID, status: "hidden", merged_into: NON_ID })));
      await chooseTarget(server);
      expect(screen.getByText("«non buxanka» «Non» ga birlashtirilsinmi? «non buxanka» katalogdan yashiriladi.")).toBeTruthy();
      expect(server.writes()).toHaveLength(0);
      const before = reads(server).length;
      fireEvent.click(screen.getByRole("button", { name: "Ha, birlashtirilsin" }));
      await waitFor(() => expect(reads(server).length).toBe(before + 1));
      expect(server.writes()).toHaveLength(1);
      expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${CATALOG}/${LEARNED_ID}/merge` });
      expect(server.writes()[0]?.body).toEqual({ into: NON_ID });
      expect(key(server.writes()[0])).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    });

    it("lets the choice be taken back", async () => {
      const server = shop();
      await chooseTarget(server, "Sut");
      fireEvent.click(screen.getByRole("button", { name: "Orqaga" }));
      expect(await screen.findByRole("list", { name: "Birlashtirish mumkin bo'lgan mahsulotlar" })).toBeTruthy();
      fireEvent.click(within(rowOf("non buxanka")).getByRole("button", { name: "Bekor qilish" }));
      expect(inRow("non buxanka", "Birlashtirish")).toBeTruthy();
      expect(server.writes()).toHaveLength(0);
    });

    it("shows the server's refusal of the target, and sends one request for a double tap", async () => {
      const answer = deferred<{ status: number; body: unknown }>();
      const message = "Faqat katalogda ko'rinadigan, ko'rib chiqilgan mahsulotga birlashtiriladi.";
      const server = shop(() => answer.promise);
      await chooseTarget(server);
      const yes = screen.getByRole("button", { name: "Ha, birlashtirilsin" });
      fireEvent.click(yes);
      fireEvent.click(yes);
      expect(server.writes()).toHaveLength(1);
      answer.resolve(refusal(409, "CATALOG_MERGE_TARGET_INVALID", message));
      expect((await screen.findByRole("alert")).textContent).toBe(message);
      expect(server.writes()).toHaveLength(1);
    });
  });
});
