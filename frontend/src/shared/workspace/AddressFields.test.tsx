// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { customerBody, CUSTOMER_ID, detailBody, fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { ADDRESS_HEADER, createApi, type CustomerAddress } from "../api";
import { addressInput, addressLine, addressProblem, draftOf, NO_ADDRESS, sameAddress } from "./AddressFields";
import { CustomerScreen } from "./CustomerScreen";
import { NewCustomerScreen } from "./NewCustomerScreen";

beforeEach(() => {
  window.location.hash = "#/customers/new";
});
afterEach(cleanup);

const GEO = `${SHOP_BASE}/territories`;
// The places are invented; a mahalla has an Uzbek name only.
const REGION = { id: "a0000000-0000-4000-8000-000000000001", name: "Sinov viloyati" };
const OTHER_REGION = { id: "a0000000-0000-4000-8000-000000000002", name: "Boshqa viloyat" };
const DISTRICT = { id: "b0000000-0000-4000-8000-000000000001", name: "Qo'rg'on tumani" };
const SECOND_DISTRICT = { id: "b0000000-0000-4000-8000-000000000002", name: "Ikkinchi tuman" };
const MAHALLA = { id: "c0000000-0000-4000-8000-000000000001", name: "Oqchobsoy", district_id: DISTRICT.id, group: "1404" };
// Two of one name whose district the reference does not know: told apart by the group alone.
const LOOSE = { id: "c0000000-0000-4000-8000-000000000002", name: "Bo'ston", district_id: null, group: "101-" };
const LOOSE_TWIN = { id: "c0000000-0000-4000-8000-000000000003", name: "Bo'ston", district_id: null, group: "102-" };
const STREET = { id: "d0000000-0000-4000-8000-000000000001", name: "Beklarsoy", kind: "street", road_type: "straight" };

/** The territory reference as the server answers it, and `onWrite` for everything that is not a read of it. */
function server(options: { last?: unknown; onOther?: (sent: Sent) => Reply } = {}) {
  return fakeServer((sent) => {
    if (sent.path === `${GEO}/regions`) {
      return ok({ items: [OTHER_REGION, REGION] });
    }
    if (sent.path === `${GEO}/districts`) {
      return ok({ items: sent.query["region"] === REGION.id ? [SECOND_DISTRICT, DISTRICT] : [] });
    }
    if (sent.path === `${GEO}/mahallas`) {
      const q = (sent.query["q"] ?? "").toLowerCase();
      const district = sent.query["district"];
      const found = [LOOSE, LOOSE_TWIN, MAHALLA].filter(
        (item) => item.name.toLowerCase().includes(q) && (district === undefined || item.district_id === null || item.district_id === district),
      );
      return ok({ items: sent.query["region"] === REGION.id ? found : [], more: false });
    }
    if (sent.path === `${GEO}/streets`) {
      const q = (sent.query["q"] ?? "").toLowerCase();
      return ok({ items: sent.query["mahalla"] === MAHALLA.id && STREET.name.toLowerCase().includes(q) ? [STREET] : [], more: false });
    }
    if (sent.path === `${GEO}/last`) {
      return ok({ address: options.last ?? null });
    }
    return (options.onOther ?? (() => ok(customerBody({ id: CUSTOMER_ID, balance: 0, address: null }), 201)))(sent);
  });
}

const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });
const choose = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const region = () => screen.getByLabelText<HTMLSelectElement>("Viloyat");
const district = () => screen.getByLabelText<HTMLSelectElement>("Tuman yoki shahar");
const street = () => screen.getByLabelText<HTMLInputElement>("Ko'cha yoki qishloq");
const save = () => screen.getByRole<HTMLButtonElement>("button", { name: "Yangi mijoz" });
const mahallas = () => within(screen.getByRole("list", { name: "Mahallalar" })).getAllByRole<HTMLButtonElement>("button");
const mahalla = (place: number) => {
  const button = mahallas()[place];
  if (button === undefined) {
    throw new Error("no such mahalla is offered");
  }
  return button;
};
const territoryReads = (made: ReturnType<typeof fakeServer>) => made.sent.filter((sent) => sent.path.startsWith(GEO));
const posted = (made: ReturnType<typeof fakeServer>) => made.sent.find((sent) => sent.method === "POST")?.body;

async function openNew(made: ReturnType<typeof fakeServer>) {
  renderScreen(<NewCustomerScreen />, { fetch: made.fetch, features: { address: true } });
  await waitFor(() => expect(region().options.length).toBe(3));
  type(screen.getByLabelText("Ism"), "Sinov Mijoz");
}

describe("the rules of an address being chosen", () => {
  const full: CustomerAddress = { region: REGION, district: DISTRICT, mahalla: MAHALLA, street: STREET, streetText: null };

  it("sends identifiers, and a typed street only when none was picked", () => {
    expect(addressInput(NO_ADDRESS)).toBeNull();
    expect(addressInput(draftOf(full))).toEqual({
      regionId: REGION.id,
      districtId: DISTRICT.id,
      mahallaId: MAHALLA.id,
      streetId: STREET.id,
      streetText: null,
    });
    expect(addressInput({ ...NO_ADDRESS, region: REGION, streetText: "  Bog'   ko'chasi 12 " })).toEqual({
      regionId: REGION.id,
      districtId: null,
      mahallaId: null,
      streetId: null,
      streetText: "Bog' ko'chasi 12",
    });
    // Without a region there is no address, whatever else was typed: the form says so instead of sending it.
    expect(addressInput({ ...NO_ADDRESS, streetText: "Bog' 12" })).toBeNull();
    expect(addressProblem({ ...NO_ADDRESS, streetText: "Bog' 12" }, (key) => key)).toBe("address.regionRequired");
    expect(addressProblem(NO_ADDRESS, (key) => key)).toBeNull();
    expect(addressProblem({ ...NO_ADDRESS, region: REGION, streetText: "Bog' 12" }, (key) => key)).toBeNull();
  });

  it("knows an unchanged address from a changed one", () => {
    const same = addressInput(draftOf(full));
    expect(sameAddress(same, addressInput(draftOf(full)))).toBe(true);
    expect(sameAddress(null, null)).toBe(true);
    expect(sameAddress(same, null)).toBe(false);
    expect(sameAddress(same, addressInput(draftOf({ ...full, street: null, streetText: "Bog' 12" })))).toBe(false);
    expect(sameAddress(same, addressInput(draftOf({ ...full, district: SECOND_DISTRICT })))).toBe(false);
  });

  it("writes an address in one line, widest place first, and starts the next customer without a street", () => {
    expect(addressLine(full)).toBe("Sinov viloyati, Qo'rg'on tumani, Oqchobsoy, Beklarsoy");
    expect(addressLine({ region: REGION, district: null, mahalla: null, street: null, streetText: "Bog' 12" })).toBe(
      "Sinov viloyati, Bog' 12",
    );
    expect(draftOf({ region: REGION, district: DISTRICT, mahalla: MAHALLA })).toEqual({
      region: REGION,
      district: DISTRICT,
      mahalla: MAHALLA,
      street: null,
      streetText: "",
    });
  });
});

describe("the header that says addresses are on", () => {
  it("is read as on only when the server says on", async () => {
    const answer = async (headers: Record<string, string>) => {
      const made = fakeServer(() => ({ status: 200, body: { items: [], active_shop: null }, headers }));
      return createApi({ fetch: made.fetch, auth: { kind: "bearer", token: "test-session" } }).myShops();
    };
    expect((await answer({})).addressOn).toBe(false);
    expect((await answer({ [ADDRESS_HEADER]: "off" })).addressOn).toBe(false);
    expect((await answer({ [ADDRESS_HEADER]: "on" })).addressOn).toBe(true);
  });
});

describe("adding a customer while addresses are off", () => {
  it("shows no address, asks the reference nothing and sends what it always sent", async () => {
    const made = server();
    renderScreen(<NewCustomerScreen />, { fetch: made.fetch });
    expect(screen.queryByRole("group", { name: "Manzil (ixtiyoriy)" })).toBeNull();
    expect(screen.queryByLabelText("Viloyat")).toBeNull();
    type(screen.getByLabelText("Ism"), "Sinov Mijoz");
    fireEvent.click(save());
    await waitFor(() => expect(made.sent).toHaveLength(1));
    expect(territoryReads(made)).toEqual([]);
    expect(made.sent[0]?.body).toEqual({ display_name: "Sinov Mijoz" });
  });
});

describe("adding a customer while addresses are on", () => {
  it("keeps the address optional: a customer saved without one names none", async () => {
    const made = server();
    await openNew(made);
    expect(screen.getByRole("group", { name: "Manzil (ixtiyoriy)" })).toBeTruthy();
    expect(screen.queryByLabelText("Tuman yoki shahar")).toBeNull();
    expect(screen.getByLabelText<HTMLInputElement>("Mahalla").disabled).toBe(true);
    fireEvent.click(save());
    await waitFor(() => expect(posted(made)).toBeDefined());
    expect(posted(made)).toEqual({ display_name: "Sinov Mijoz" });
  });

  it("goes from region to district to a mahalla found by typing to a street picked from the list", async () => {
    const made = server();
    await openNew(made);
    choose("Viloyat", REGION.id);
    await waitFor(() => expect(district().options.length).toBe(3));
    choose("Tuman yoki shahar", DISTRICT.id);
    await waitFor(() => expect(mahallas().map((button) => button.textContent)).toContain("Oqchobsoy"));

    type(screen.getByLabelText("Mahalla"), "oqch");
    await waitFor(() => expect(mahallas()).toHaveLength(1));
    const searched = made.sent.filter((sent) => sent.path === `${GEO}/mahallas`).at(-1);
    expect(searched?.query).toMatchObject({ region: REGION.id, district: DISTRICT.id, q: "oqch" });
    fireEvent.click(mahalla(0));

    const streets = await screen.findByRole("list", { name: "Ko'chalar" });
    fireEvent.click(within(streets).getByRole("button", { name: "Beklarsoy" }));
    expect(screen.queryByRole("textbox", { name: "Ko'cha yoki qishloq" })).toBeNull();
    expect(screen.getByText("Beklarsoy")).toBeTruthy();

    fireEvent.click(save());
    await waitFor(() => expect(posted(made)).toBeDefined());
    expect(posted(made)).toEqual({
      display_name: "Sinov Mijoz",
      address: { region_id: REGION.id, district_id: DISTRICT.id, mahalla_id: MAHALLA.id, street_id: STREET.id },
    });
  });

  it("brings the district with a mahalla whose district is known, and invents none for one whose is not", async () => {
    const made = server();
    await openNew(made);
    choose("Viloyat", REGION.id);
    await waitFor(() => expect(district().options.length).toBe(3));

    // No district chosen: the region's mahallas are offered. Two of one name show their group; a name
    // that is there once does not.
    await waitFor(() => expect(mahallas()).toHaveLength(3));
    expect(mahallas().map((button) => button.textContent)).toEqual([
      "Bo'stonRo'yxatdagi guruh: 101-",
      "Bo'stonRo'yxatdagi guruh: 102-",
      "Oqchobsoy",
    ]);

    fireEvent.click(mahalla(0));
    expect(district().value).toBe("");
    fireEvent.click(save());
    await waitFor(() => expect(posted(made)).toBeDefined());
    expect(posted(made)).toEqual({ display_name: "Sinov Mijoz", address: { region_id: REGION.id, mahalla_id: LOOSE.id } });
    cleanup();

    const again = server();
    await openNew(again);
    choose("Viloyat", REGION.id);
    await waitFor(() => expect(mahallas()).toHaveLength(3));
    fireEvent.click(mahalla(2));
    await waitFor(() => expect(district().value).toBe(DISTRICT.id));
  });

  it("takes a typed street where the reference has none, and says so when no region was chosen", async () => {
    const made = server();
    await openNew(made);
    type(street(), "Bog' ko'chasi 12");
    fireEvent.click(save());
    expect(screen.getByText("Ko'cha yozish uchun avval viloyatni tanlang.")).toBeTruthy();
    expect(made.sent.filter((sent) => sent.method === "POST")).toEqual([]);

    choose("Viloyat", OTHER_REGION.id);
    expect(street().value).toBe("Bog' ko'chasi 12");
    fireEvent.click(save());
    await waitFor(() => expect(posted(made)).toBeDefined());
    expect(posted(made)).toEqual({
      display_name: "Sinov Mijoz",
      address: { region_id: OTHER_REGION.id, street_text: "Bog' ko'chasi 12" },
    });
  });

  it("starts from the place the shop used last, lets it be cleared, and never fills in a street", async () => {
    const made = server({ last: { region: REGION, district: DISTRICT, mahalla: { id: MAHALLA.id, name: MAHALLA.name } } });
    renderScreen(<NewCustomerScreen />, { fetch: made.fetch, features: { address: true } });
    await screen.findByText("Oxirgi kiritilgan manzil bo'yicha to'ldirildi. Kerak bo'lsa o'zgartiring.");
    await waitFor(() => expect(region().value).toBe(REGION.id));
    await waitFor(() => expect(district().value).toBe(DISTRICT.id));
    expect(screen.getByText("Oqchobsoy")).toBeTruthy();
    expect(street().value).toBe("");

    type(screen.getByLabelText("Ism"), "Sinov Mijoz");
    fireEvent.click(save());
    await waitFor(() => expect(posted(made)).toBeDefined());
    expect(posted(made)).toEqual({
      display_name: "Sinov Mijoz",
      address: { region_id: REGION.id, district_id: DISTRICT.id, mahalla_id: MAHALLA.id },
    });
    cleanup();

    const again = server({ last: { region: REGION, district: DISTRICT, mahalla: null } });
    renderScreen(<NewCustomerScreen />, { fetch: again.fetch, features: { address: true } });
    await waitFor(() => expect(region().value).toBe(REGION.id));
    fireEvent.click(screen.getByRole("button", { name: "Manzilni tozalash" }));
    expect(region().value).toBe("");
    expect(screen.queryByText("Oxirgi kiritilgan manzil bo'yicha to'ldirildi. Kerak bo'lsa o'zgartiring.")).toBeNull();
    type(screen.getByLabelText("Ism"), "Sinov Mijoz");
    fireEvent.click(save());
    await waitFor(() => expect(posted(again)).toBeDefined());
    expect(posted(again)).toEqual({ display_name: "Sinov Mijoz" });
  });

  it("shows the server's refusal of an address under the address", async () => {
    const made = server({
      onOther: () => refusal(422, "VALIDATION", "Ma'lumot noto'g'ri.", { "address.mahalla_id": "not a mahalla of this district" }),
    });
    await openNew(made);
    choose("Viloyat", REGION.id);
    fireEvent.click(save());
    expect(await screen.findByText("Manzil noto'g'ri. Qaytadan tanlang.")).toBeTruthy();
  });
});

describe("a customer's page", () => {
  const address = {
    region: REGION,
    district: DISTRICT,
    mahalla: { id: MAHALLA.id, name: MAHALLA.name },
    street: null,
    street_text: "Bog' ko'chasi 12",
  };

  function page(detail: Record<string, unknown>, features: { address: boolean }) {
    const made = server({ onOther: (sent) => (sent.method === "PATCH" ? ok(customerBody(detail)) : ok(detailBody(detail))) });
    window.location.hash = `#/customers/${CUSTOMER_ID}`;
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: made.fetch, role: "manager", features });
    return made;
  }
  const patched = (made: ReturnType<typeof fakeServer>) => made.sent.find((sent) => sent.method === "PATCH")?.body;

  it("says where the customer lives, and nothing when they have no address or addresses are off", async () => {
    page({ address }, { address: true });
    expect(await screen.findByText("Sinov viloyati, Qo'rg'on tumani, Oqchobsoy, Bog' ko'chasi 12")).toBeTruthy();
    cleanup();

    page({ address: null }, { address: true });
    await screen.findByRole("heading", { name: "Ali Valiyev" });
    expect(screen.queryByText(/Sinov viloyati/u)).toBeNull();
    cleanup();

    const off = page({}, { address: false });
    await screen.findByRole("heading", { name: "Ali Valiyev" });
    fireEvent.click(screen.getByRole("button", { name: "Tahrirlash" }));
    expect(screen.queryByRole("group", { name: "Manzil (ixtiyoriy)" })).toBeNull();
    expect(territoryReads(off)).toEqual([]);
  });

  it("sends the address only when it was changed, as a whole, and null when it was cleared", async () => {
    const unchanged = page({ address }, { address: true });
    await screen.findByRole("heading", { name: "Ali Valiyev" });
    fireEvent.click(screen.getByRole("button", { name: "Tahrirlash" }));
    await waitFor(() => expect(region().value).toBe(REGION.id));
    expect(street().value).toBe("Bog' ko'chasi 12");
    type(screen.getByLabelText("Ism"), "Vali Aliyev");
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    await waitFor(() => expect(patched(unchanged)).toBeDefined());
    expect(patched(unchanged)).toEqual({ display_name: "Vali Aliyev" });
    cleanup();

    const changed = page({ address }, { address: true });
    await screen.findByRole("heading", { name: "Ali Valiyev" });
    fireEvent.click(screen.getByRole("button", { name: "Tahrirlash" }));
    await waitFor(() => expect(region().value).toBe(REGION.id));
    type(street(), "Yangi ko'cha 3");
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    await waitFor(() => expect(patched(changed)).toBeDefined());
    expect(patched(changed)).toEqual({
      address: { region_id: REGION.id, district_id: DISTRICT.id, mahalla_id: MAHALLA.id, street_text: "Yangi ko'cha 3" },
    });
    cleanup();

    const cleared = page({ address }, { address: true });
    await screen.findByRole("heading", { name: "Ali Valiyev" });
    fireEvent.click(screen.getByRole("button", { name: "Tahrirlash" }));
    await waitFor(() => expect(region().value).toBe(REGION.id));
    fireEvent.click(screen.getByRole("button", { name: "Manzilni tozalash" }));
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    await waitFor(() => expect(patched(cleared)).toBeDefined());
    expect(patched(cleared)).toEqual({ address: null });
  });
});
