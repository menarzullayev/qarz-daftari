// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { translate } from "../../i18n/catalog";
import { fakeServer, itemBody, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { CATALOG_HEADER, createApi } from "../api";
import { CatalogScreen } from "./CatalogScreen";
import { categoryKey, SHARED_CATEGORIES } from "../sharedCatalog";
import { SHARED_SEARCH_DELAY_MS } from "./SharedPicker";

afterEach(cleanup);

const SHARED = `${SHOP_BASE}/shared-catalog`;
const TEA_ID = "66666666-6666-4666-8666-666666666661";
const JAM_ID = "66666666-6666-4666-8666-666666666662";
const HELD_ID = "66666666-6666-4666-8666-666666666663";
const PHOTO = `/files/catalog/${"ab".repeat(32)}`;

/** An item of the shared catalogue as the server sends it. The products are invented. */
const sharedBody = (overrides: Record<string, unknown> = {}) => ({
  id: TEA_ID,
  name: "Ko'k choy Tog'",
  name_ru: "Чай зелёный Тоғ",
  name_uz: "Ko'k choy Tog'",
  amount: "100 g",
  category: "tea",
  subcategory: null,
  unit: "dona",
  price_hint: 12990,
  image: PHOTO,
  picked: null,
  ...overrides,
});
const TEA = sharedBody();
const JAM = sharedBody({ id: JAM_ID, name: "Olma murabbosi", amount: null, category: "sweets", price_hint: null, image: null });
const HELD = sharedBody({ id: HELD_ID, name: "Shakar", amount: "1 kg", category: "grocery", picked: { id: "x", name: "Shakar 1 kg", price: 14000, status: "active" } });
const sharedPage = (items: unknown[], nextCursor: string | null = null) =>
  ok({ items, next_cursor: nextCursor, categories: [...SHARED_CATEGORIES] });

/** A shop with one item of its own, and the shared catalogue with three; `onWrite` answers every write. */
function shop(onWrite: (sent: Sent) => Reply = () => ok(itemBody({ name: "Ko'k choy Tog' 100 g", price: 15000 }))) {
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return onWrite(sent);
    }
    if (sent.path === SHARED) {
      const q = (sent.query["q"] ?? "").toLowerCase();
      const category = sent.query["category"];
      return sharedPage(
        [TEA, JAM, HELD].filter((item) => item.name.toLowerCase().includes(q) && (category === undefined || item.category === category)),
      );
    }
    return ok({ items: [itemBody()], next_cursor: null });
  });
}

async function open(server: ReturnType<typeof fakeServer>, catalog = true) {
  renderScreen(<CatalogScreen />, { fetch: server.fetch, role: "manager", features: { catalog } });
  await screen.findByText("Non");
  fireEvent.click(screen.getByRole("button", { name: "Yangi mahsulot" }));
}

const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });
const offered = () => within(screen.getByRole("list", { name: "Katalogdagi mahsulotlar" })).getAllByRole<HTMLButtonElement>("button");
const searches = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.path === SHARED);
const writes = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.method !== "GET");

describe("adding an item while the shared catalogue is off", () => {
  it("opens the form for typing it, as before, and asks the catalogue nothing", async () => {
    const server = shop();
    await open(server, false);
    expect(screen.getByRole("form", { name: "Yangi mahsulot" })).toBeTruthy();
    expect(screen.getByLabelText("Nomi")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Umumiy katalogdan tanlash" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Topilmadi: qo'lda qo'shish" })).toBeNull();
    expect(searches(server)).toEqual([]);
  });

  it("reads the switch from the header of the person's shops, and only the value `on`", async () => {
    const answer = (headers: Record<string, string>) =>
      createApi({ fetch: fakeServer(() => ({ status: 200, body: { items: [], active_shop: null }, headers })).fetch, auth: { kind: "bearer", token: "t" } }).myShops();
    expect((await answer({})).catalogOn).toBe(false);
    expect((await answer({ [CATALOG_HEADER]: "off" })).catalogOn).toBe(false);
    expect((await answer({ [CATALOG_HEADER]: "on" })).catalogOn).toBe(true);
  });
});

describe("picking an item from the shared catalogue", () => {
  it("starts by searching the catalogue, and shows each item with its photo, package and category", async () => {
    const server = shop();
    await open(server);
    const region = await screen.findByRole("region", { name: "Umumiy katalogdan tanlash" });
    await waitFor(() => expect(offered()).toHaveLength(3));
    expect(screen.queryByLabelText("Nomi")).toBeNull();
    const [tea, jam, held] = offered();
    expect(tea?.textContent).toContain("Ko'k choy Tog', 100 g");
    expect(tea?.textContent).toContain("Kofe va choy");
    expect(jam?.textContent).toContain("Olma murabbosi");
    expect(jam?.textContent).toContain("Shirinliklar");
    // The photo is ours, decorative, and lazily loaded; an item without one gets a plain square.
    const photo = tea?.querySelector("img");
    expect(photo?.getAttribute("src")).toBe(PHOTO);
    expect(photo?.getAttribute("alt")).toBe("");
    expect(photo?.getAttribute("loading")).toBe("lazy");
    expect(jam?.querySelector("img")).toBeNull();
    expect(jam?.querySelector(".thumb--none")?.getAttribute("aria-hidden")).toBe("true");
    // What the shop already holds cannot be added a second time.
    expect(held?.disabled).toBe(true);
    expect(held?.textContent).toContain("Sizda bor");
    expect(within(region).getByRole("searchbox", { name: "Katalogdan qidirish: nomi yoki hajmi" })).toBeTruthy();
  });

  it("searches as the person types and filters by category", async () => {
    const server = shop();
    await open(server);
    await waitFor(() => expect(offered()).toHaveLength(3));
    type(screen.getByRole("searchbox", { name: "Katalogdan qidirish: nomi yoki hajmi" }), "choy");
    await act(() => new Promise((resolve) => setTimeout(resolve, SHARED_SEARCH_DELAY_MS + 50)));
    await waitFor(() => expect(offered()).toHaveLength(1));
    expect(searches(server).at(-1)?.query).toMatchObject({ q: "choy", limit: "20" });
    expect(searches(server).at(-1)?.query["category"]).toBeUndefined();

    const category = screen.getByLabelText<HTMLSelectElement>("Bo'lim");
    expect(Array.from(category.options).map((option) => option.value)).toEqual(["", ...SHARED_CATEGORIES]);
    type(category, "sweets");
    await waitFor(() => expect(searches(server).at(-1)?.query["category"]).toBe("sweets"));
    await screen.findByText("Katalogda topilmadi. Pastda qo'lda qo'shishingiz mumkin.");
  });

  it("asks only for the shop's own price, shows the approximate one as advice and never fills it in", async () => {
    const server = shop();
    await open(server);
    await waitFor(() => expect(offered()).toHaveLength(3));
    fireEvent.click(offered()[0] as HTMLElement);
    const form = screen.getByRole("form", { name: "Mahsulotlarimga qo'shish" });
    const price = within(form).getByLabelText<HTMLInputElement>("Sizning narxingiz, so'm");
    expect(price.value).toBe("");
    expect(price.placeholder).toBe("");
    const hint = within(form).getByText("Toshkentda taxminan 12 990 so'm");
    expect(price.getAttribute("aria-describedby")?.split(" ")).toContain(hint.id);
    expect(within(form).queryByLabelText("Nomi")).toBeNull();

    // Saving without typing a price sends nothing: the advice is not a price.
    fireEvent.click(within(form).getByRole("button", { name: "Mahsulotlarimga qo'shish" }));
    expect(within(form).getByRole("alert").textContent).toBe("Narxni kiriting.");
    expect(writes(server)).toEqual([]);

    type(price, "15000");
    fireEvent.click(within(form).getByRole("button", { name: "Mahsulotlarimga qo'shish" }));
    await waitFor(() => expect(writes(server)).toHaveLength(1));
    const sent = writes(server)[0];
    expect(sent?.path).toBe(`${SHARED}/${TEA_ID}/pick`);
    expect(sent?.body).toEqual({ price: 15000 });
    expect(sent?.headers["Idempotency-Key"]).toBeTruthy();
    // Done: the panel closes and the shop's list is read again.
    await waitFor(() => expect(screen.queryByRole("form", { name: "Mahsulotlarimga qo'shish" })).toBeNull());
  });

  it("shows no advice for an item the catalogue has no price for", async () => {
    const server = shop();
    await open(server);
    await waitFor(() => expect(offered()).toHaveLength(3));
    fireEvent.click(offered()[1] as HTMLElement);
    expect(screen.queryByText(/Toshkentda taxminan/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Orqaga" }));
    await waitFor(() => expect(offered()).toHaveLength(3));
  });

  it("says why the server refused, next to the price", async () => {
    const server = shop(() => refusal(409, "CATALOG_NAME_TAKEN", "Bu nomli mahsulot allaqachon bor."));
    await open(server);
    await waitFor(() => expect(offered()).toHaveLength(3));
    fireEvent.click(offered()[0] as HTMLElement);
    type(screen.getByLabelText("Sizning narxingiz, so'm"), "15000");
    fireEvent.click(screen.getByRole("button", { name: "Mahsulotlarimga qo'shish" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Bu nomli mahsulot allaqachon bor.");
  });

  it("leaves adding by hand one button away, and that form is the one there always was", async () => {
    const server = shop(() => ok(itemBody({ name: "Uy qatig'i", price: 9000 }), 201));
    await open(server);
    await waitFor(() => expect(offered()).toHaveLength(3));
    fireEvent.click(screen.getByRole("button", { name: "Topilmadi: qo'lda qo'shish" }));
    const form = screen.getByRole("form", { name: "Yangi mahsulot" });
    expect(screen.queryByRole("region", { name: "Umumiy katalogdan tanlash" })).toBeNull();
    type(within(form).getByLabelText("Nomi"), "Uy qatig'i");
    type(within(form).getByLabelText("Narxi, so'm"), "9000");
    fireEvent.click(within(form).getByRole("button", { name: "Saqlash" }));
    await waitFor(() => expect(writes(server)).toHaveLength(1));
    expect(writes(server)[0]?.path).toBe(`${SHOP_BASE}/catalog`);
    expect(writes(server)[0]?.body).toEqual({ name: "Uy qatig'i", price: 9000 });
  });
});

describe("what the client refuses to show", () => {
  it("drops a photo address that is not a catalogue photo of this host", async () => {
    const api = (image: unknown) =>
      createApi({ fetch: fakeServer(() => sharedPage([sharedBody({ image })])).fetch, auth: { kind: "bearer", token: "t" } })
        .shop("11111111-1111-4111-8111-111111111111")
        .searchSharedCatalog({});
    expect((await api(PHOTO)).items[0]?.image).toBe(PHOTO);
    for (const elsewhere of ["https://example.invalid/a.png", "//example.invalid/a.png", `${PHOTO}/../x`, "/files/abc", `http://x${PHOTO}`]) {
      expect((await api(elsewhere)).items[0]?.image).toBeNull();
    }
  });

  it("names every category, each differently, and an unknown one as other", () => {
    // Every language has every key or the build fails (i18n/types.ts); here, that none is left unnamed.
    const names = SHARED_CATEGORIES.map((key) => translate("uz", categoryKey(key)));
    expect(names.every((name) => name.trim() !== "" && !name.startsWith("catalog."))).toBe(true);
    expect(new Set(names).size).toBe(SHARED_CATEGORIES.length);
    expect(SHARED_CATEGORIES).toHaveLength(21);
    expect(categoryKey("weapons")).toBe("catalog.category.other");
  });
});
