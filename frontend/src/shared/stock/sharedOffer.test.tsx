// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { fakeServer, itemBody, ok, refusal, type Sent, SHOP_BASE } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { Role } from "../navigation";
import { ItemFinder } from "./ItemFinder";
import type { StockItem } from "./stockApi";
import { EAN, ITEM_ID, STOCK, stockItemBody } from "./testing";

afterEach(cleanup);

const SHARED_LOOKUP = `${SHOP_BASE}/shared-catalog/lookup`;
const SHARED_ID = "66666666-6666-4666-8666-666666666661";
const LABEL = "Tovar: shtrix-kodni skanerlang yoki nom yozing";
const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");

const sharedBody = (overrides: Record<string, unknown> = {}) => ({
  id: SHARED_ID,
  name: "Ko'k choy Tog'",
  name_ru: null,
  name_uz: "Ko'k choy Tog'",
  amount: "100 g",
  category: "tea",
  subcategory: null,
  unit: "dona",
  price_hint: 12990,
  image: null,
  picked: null,
  ...overrides,
});

/**
 * A shop that does not have the scanned code until it picks the catalogue's item: from then on its own
 * lookup finds it, as the server makes it (the picked item carries the approved barcode).
 */
function backend(shared: "known" | "unknown" | "held") {
  let picked = false;
  return fakeServer((sent: Sent) => {
    if (sent.path === `${STOCK}/lookup`) {
      return picked ? ok(stockItemBody({ name: "Ko'k choy Tog' 100 g", barcodes: [EAN] })) : NOT_FOUND;
    }
    if (sent.path === SHARED_LOOKUP) {
      if (shared === "unknown") {
        return NOT_FOUND;
      }
      return ok(sharedBody(shared === "held" ? { picked: { id: ITEM_ID, name: "x", price: 1, status: "hidden" } } : {}));
    }
    if (sent.method === "POST" && sent.path === `${SHOP_BASE}/shared-catalog/${SHARED_ID}/pick`) {
      picked = true;
      return ok(itemBody({ name: "Ko'k choy Tog' 100 g", price: 15000 }));
    }
    return ok({ items: [], next_cursor: null });
  });
}

function open(server: ReturnType<typeof fakeServer>, options: { catalog?: boolean; role?: Role } = {}) {
  const pickedItems: StockItem[] = [];
  renderScreen(
    <ItemFinder id="finder" filter="all" host={{}} onPick={(item) => pickedItems.push(item)} missing={() => <p>Yangi tovar qo'shish</p>} />,
    { fetch: server.fetch, role: options.role ?? "manager", features: { stock: true, catalog: options.catalog ?? true } },
  );
  const input = screen.getByLabelText(LABEL);
  fireEvent.change(input, { target: { value: EAN } });
  fireEvent.submit(input.closest("form") as HTMLFormElement);
  return pickedItems;
}

const asked = (server: ReturnType<typeof fakeServer>, path: string) => server.sent.filter((sent) => sent.path === path);

describe("a scanned code no item of the shop has", () => {
  it("falls through to the shared catalogue, and picking the item there finds it under the same code", async () => {
    const server = backend("known");
    const picked = open(server);
    const form = await screen.findByRole("form", { name: "Mahsulotlarimga qo'shish" });
    expect(screen.getByText(`${EAN} shtrix-kodli tovar topilmadi.`)).toBeTruthy();
    expect(within(form).getByText("Bu shtrix-kod umumiy katalogda bor. Narxingizni yozib, mahsulotlaringizga qo'shing.")).toBeTruthy();
    expect(within(form).getByText("Ko'k choy Tog', 100 g")).toBeTruthy();
    expect(within(form).getByText("Toshkentda taxminan 12 990 so'm")).toBeTruthy();
    expect(asked(server, SHARED_LOOKUP)[0]?.query).toEqual({ code: EAN });
    // What was offered before is still offered: the catalogue adds a way, it takes none away.
    expect(screen.getByText("Yangi tovar qo'shish")).toBeTruthy();

    const price = within(form).getByLabelText<HTMLInputElement>("Sizning narxingiz, so'm");
    expect(price.value).toBe("");
    fireEvent.change(price, { target: { value: "15000" } });
    fireEvent.click(within(form).getByRole("button", { name: "Mahsulotlarimga qo'shish" }));
    await waitFor(() => expect(picked).toHaveLength(1));
    expect(picked[0]?.name).toBe("Ko'k choy Tog' 100 g");
    expect(asked(server, `${STOCK}/lookup`)).toHaveLength(2);
    expect(server.sent.find((sent) => sent.method === "POST")?.body).toEqual({ price: 15000 });
    expect(screen.queryByRole("form", { name: "Mahsulotlarimga qo'shish" })).toBeNull();
  });

  it("reads as plain not found when the catalogue does not know the code either", async () => {
    const server = backend("unknown");
    open(server);
    await waitFor(() => expect(asked(server, SHARED_LOOKUP)).toHaveLength(1));
    expect(screen.getByText(`${EAN} shtrix-kodli tovar topilmadi.`)).toBeTruthy();
    expect(screen.getByText("Yangi tovar qo'shish")).toBeTruthy();
    expect(screen.queryByRole("form", { name: "Mahsulotlarimga qo'shish" })).toBeNull();
  });

  it("does not offer again what the shop already holds", async () => {
    const server = backend("held");
    open(server);
    await waitFor(() => expect(asked(server, SHARED_LOOKUP)).toHaveLength(1));
    expect(screen.queryByRole("form", { name: "Mahsulotlarimga qo'shish" })).toBeNull();
  });

  it("asks the catalogue nothing while it is switched off", async () => {
    const server = backend("known");
    open(server, { catalog: false });
    await screen.findByText(`${EAN} shtrix-kodli tovar topilmadi.`);
    expect(screen.getByText("Yangi tovar qo'shish")).toBeTruthy();
    expect(asked(server, SHARED_LOOKUP)).toEqual([]);
    expect(screen.queryByRole("form", { name: "Mahsulotlarimga qo'shish" })).toBeNull();
  });

  it("asks the catalogue nothing for a member who may not add items", async () => {
    const server = backend("known");
    open(server, { role: "seller" });
    await screen.findByText(`${EAN} shtrix-kodli tovar topilmadi.`);
    expect(asked(server, SHARED_LOOKUP)).toEqual([]);
    expect(screen.queryByRole("form", { name: "Mahsulotlarimga qo'shish" })).toBeNull();
  });
});
