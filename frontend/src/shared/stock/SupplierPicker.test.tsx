// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { fakeServer, ok, refusal, type Reply } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { PICK_DEBOUNCE_MS, type PickOption } from "./OptionPicker";
import { SUPPLIER_PAGE, SupplierPicker } from "./SupplierPicker";
import { manySuppliers, supplierBody, supplierListBody, SUPPLIERS } from "./testing";

/**
 * The choice of a supplier out of all of them. Before it, a `<select>` was filled with the first page
 * of the list (a hundred suppliers; fifty in the network), and a supplier after that page could not be
 * chosen at all. Here the shop has forty-five working suppliers and three archived ones, the choice
 * asks for twenty at a time, and every one of them must be reachable: by typing, by paging, and with
 * the keyboard alone.
 */

afterEach(cleanup);

const ARCHIVED = ["Eski ulgurji", "Eski bozor", "Eski savdo"].map((name, index) =>
  supplierBody({ id: `77777777-7777-4777-8777-9999999999${String(index).padStart(2, "0")}`, name, status: "archived" }),
);
const ALL = [...manySuppliers(45), ...ARCHIVED];
const LABEL = "Ta'minotchi";
const NONE = "Barcha ta'minotchilar";
const MORE = "Yana ko'rsatish";
const idOf = (number: number) => `77777777-7777-4777-8777-${String(number).padStart(12, "0")}`;

function backend(answer: (query: Record<string, string>) => Reply = (query) => ok(supplierListBody(ALL, query))) {
  return fakeServer((sent) => (sent.path === SUPPLIERS ? answer(sent.query) : refusal(404, "NOT_FOUND", "Topilmadi.")));
}

function Host({ archived, required, picked }: { archived: boolean; required: boolean; picked: (PickOption | null)[] }) {
  const [value, setValue] = useState<PickOption | null>(null);
  return (
    <>
      <SupplierPicker
        id="supplier"
        value={value}
        archived={archived}
        noneLabel={required ? undefined : NONE}
        placeholder={required ? "Ta'minotchini tanlang" : undefined}
        onChange={(chosen) => {
          picked.push(chosen);
          setValue(chosen);
        }}
      />
      <button type="button">Keyingi</button>
    </>
  );
}

function show(options: { archived?: boolean; required?: boolean; server?: ReturnType<typeof backend>; permissions?: string[] } = {}) {
  const server = options.server ?? backend();
  const picked: (PickOption | null)[] = [];
  renderScreen(<Host archived={options.archived ?? false} required={options.required ?? false} picked={picked} />, {
    fetch: server.fetch,
    role: "manager",
  });
  const field = screen.getByRole("combobox", { name: LABEL }) as HTMLInputElement;
  return { server, picked, field };
}

const asked = (server: ReturnType<typeof backend>) => server.sent.map((sent) => sent.query);
const options = () => screen.getAllByRole("option").map((option) => option.textContent);
/** The option the arrow keys are on, as a screen reader is told: by the field's `aria-activedescendant`. */
const activeOption = (field: HTMLElement) => {
  const id = field.getAttribute("aria-activedescendant");
  return id === null ? null : (document.getElementById(id)?.textContent ?? "(no such element)");
};
const key = (field: HTMLElement, name: string, times = 1) => {
  for (let press = 0; press < times; press += 1) {
    fireEvent.keyDown(field, { key: name });
  }
};
const type = (field: HTMLElement, text: string) => fireEvent.change(field, { target: { value: text } });
/** The list as first read: nothing chosen, the first page, and the row that asks for more. */
const firstPage = () => waitFor(() => expect(options()).toHaveLength(SUPPLIER_PAGE + 2));

describe("the choice of a supplier", () => {
  it("is a combobox with its label, hint and list, and asks the server nothing until it is opened", async () => {
    const { server, field } = show();
    expect(field.getAttribute("aria-expanded")).toBe("false");
    expect(field.getAttribute("aria-autocomplete")).toBe("list");
    expect(field.getAttribute("aria-haspopup")).toBe("listbox");
    expect(field.getAttribute("placeholder")).toBe(NONE);
    expect(document.getElementById(field.getAttribute("aria-describedby") ?? "")?.textContent).toBe("Nomini yozib qidiring yoki ro'yxatdan tanlang.");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(server.sent).toEqual([]);

    fireEvent.click(field);
    expect(field.getAttribute("aria-expanded")).toBe("true");
    const list = screen.getByRole("listbox", { name: LABEL });
    expect(field.getAttribute("aria-controls")).toBe(list.id);
    await firstPage();
    expect(asked(server)).toEqual([{ status: "active", limit: String(SUPPLIER_PAGE) }]);
    // Nothing but options inside the list, each with a state a screen reader can read.
    expect([...list.children].every((child) => child.getAttribute("role") === "option" && child.hasAttribute("aria-selected"))).toBe(true);
    expect(options().slice(0, 3)).toEqual([NONE, "Ta'minotchi 01", "Ta'minotchi 02"]);
    expect(options().at(-1)).toBe(MORE);
  });

  it("finds a supplier beyond the first page by typing, with one request for what was typed", async () => {
    const { server, picked, field } = show();
    fireEvent.click(field);
    await firstPage();
    // The forty-fifth is on the third page: not among what the first page offers.
    expect(options()).not.toContain("Ta'minotchi 45");
    type(field, "Ta'minotchi 4");
    type(field, "Ta'minotchi 45");
    await waitFor(() => expect(options()).toEqual(["Ta'minotchi 45"]));
    // The typing rested once: the server was asked for the whole of it, never for the half-typed name.
    expect(asked(server).map((query) => query["q"])).toEqual([undefined, "Ta'minotchi 45"]);
    fireEvent.click(screen.getByRole("option", { name: "Ta'minotchi 45" }));
    expect(picked).toEqual([{ id: idOf(45), name: "Ta'minotchi 45", detail: undefined }]);
    expect(field.value).toBe("Ta'minotchi 45");
    expect(field.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("does not ask while the typing has not rested, and then asks once", async () => {
    const { server, field } = show();
    type(field, "Ta");
    type(field, "Ta'm");
    // The list is open and says that it is looking; the server has not been asked for half a name.
    expect(field.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("status").textContent).toBe("Yuklanmoqda…");
    await new Promise((done) => setTimeout(done, PICK_DEBOUNCE_MS - 100));
    expect(asked(server)).toEqual([]);
    await waitFor(() => expect(asked(server)).toHaveLength(1));
    expect(asked(server)).toEqual([{ q: "Ta'm", status: "active", limit: String(SUPPLIER_PAGE) }]);
    await waitFor(() => expect(options()).toHaveLength(SUPPLIER_PAGE + 1));
  });

  it("drops the answer to a search that was typed over", async () => {
    const { field } = show();
    fireEvent.click(field);
    await firstPage();
    type(field, "07");
    // The whole list is not offered under a half-typed name, even for a moment.
    expect(screen.queryAllByRole("option")).toEqual([]);
    await waitFor(() => expect(options()).toEqual(["Ta'minotchi 07"]));
    type(field, "08");
    expect(screen.queryAllByRole("option")).toEqual([]);
    await waitFor(() => expect(options()).toEqual(["Ta'minotchi 08"]));
  });

  it("pages through all of them with the server's cursor, the archived ones after the working ones", async () => {
    const { server, field } = show({ archived: true });
    fireEvent.click(field);
    await firstPage();
    fireEvent.click(screen.getByRole("option", { name: MORE }));
    await waitFor(() => expect(options()).toHaveLength(2 * SUPPLIER_PAGE + 2));
    fireEvent.click(screen.getByRole("option", { name: MORE }));
    // The last five working suppliers, and with them the archived ones: the list that ended gave way.
    await waitFor(() => expect(options()).toHaveLength(1 + 45 + 3));
    expect(options().slice(-4)).toEqual(["Ta'minotchi 45", "Eski ulgurjiArxivda", "Eski bozorArxivda", "Eski savdoArxivda"]);
    expect(screen.queryByRole("option", { name: MORE })).toBeNull();
    const page = String(SUPPLIER_PAGE);
    expect(asked(server)).toEqual([
      { status: "active", limit: page },
      { status: "active", limit: page, cursor: "c20" },
      { status: "active", limit: page, cursor: "c40" },
      { status: "archived", limit: page },
    ]);
  });

  it("offers no archived supplier where a document is being written", async () => {
    const { server, field } = show();
    fireEvent.click(field);
    await firstPage();
    fireEvent.click(screen.getByRole("option", { name: MORE }));
    await waitFor(() => expect(options()).toHaveLength(2 * SUPPLIER_PAGE + 2));
    fireEvent.click(screen.getByRole("option", { name: MORE }));
    await waitFor(() => expect(options()).toHaveLength(1 + 45));
    expect(asked(server).every((query) => query["status"] === "active")).toBe(true);
    type(field, "Eski");
    expect(await screen.findByText("Hech narsa topilmadi.")).toBeTruthy();
    expect(screen.queryAllByRole("option")).toEqual([]);
  });

  it("is worked with the keyboard alone: arrows, Enter, Escape, and the next page as one more row", async () => {
    const { server, picked, field } = show();
    field.focus();
    // Arriving at the field opens nothing; the arrow does.
    expect(field.getAttribute("aria-expanded")).toBe("false");
    key(field, "ArrowDown");
    expect(field.getAttribute("aria-expanded")).toBe("true");
    await firstPage();
    expect(activeOption(field)).toBeNull();
    key(field, "ArrowDown");
    expect(activeOption(field)).toBe(NONE);
    key(field, "ArrowDown", 2);
    expect(activeOption(field)).toBe("Ta'minotchi 02");
    // Exactly one option is the selected one, and it is the one the field points at.
    expect(screen.getAllByRole("option", { selected: true }).map((option) => option.textContent)).toEqual(["Ta'minotchi 02"]);
    key(field, "ArrowUp");
    expect(activeOption(field)).toBe("Ta'minotchi 01");
    // The first row is where the arrows stop: they do not leave the list.
    key(field, "ArrowUp", 5);
    expect(activeOption(field)).toBe(NONE);

    // Escape closes and changes nothing.
    key(field, "Escape");
    expect(field.getAttribute("aria-expanded")).toBe("false");
    expect(picked).toEqual([]);
    expect(document.activeElement).toBe(field);

    // Up from the closed field's fresh list is its last row: the one that reads the next page.
    key(field, "ArrowDown");
    await firstPage();
    key(field, "ArrowUp");
    expect(activeOption(field)).toBe(MORE);
    key(field, "Enter");
    await waitFor(() => expect(options()).toHaveLength(2 * SUPPLIER_PAGE + 2));
    expect(asked(server).at(-1)).toEqual({ status: "active", limit: String(SUPPLIER_PAGE), cursor: "c20" });
    // The keys go on from the first of the new ones, and the list is still open.
    expect(activeOption(field)).toBe("Ta'minotchi 21");
    key(field, "ArrowDown", 2);
    expect(activeOption(field)).toBe("Ta'minotchi 23");
    key(field, "Enter");
    expect(picked).toEqual([{ id: idOf(23), name: "Ta'minotchi 23", detail: undefined }]);
    expect(field.value).toBe("Ta'minotchi 23");
    expect(field.getAttribute("aria-expanded")).toBe("false");
    // The focus never left the field, so Tab goes on to what follows it.
    expect(document.activeElement).toBe(field);
    expect(server.sent.every((sent) => sent.method === "GET")).toBe(true);
  });

  it("types, arrows and Enter: a supplier of the last page without the pointer", async () => {
    const { picked, field } = show();
    field.focus();
    type(field, "45");
    await waitFor(() => expect(options()).toEqual(["Ta'minotchi 45"]));
    // Enter with no option under the keys chooses nothing: a name half typed is not a choice.
    key(field, "Enter");
    expect(picked).toEqual([]);
    key(field, "ArrowDown");
    key(field, "Enter");
    expect(picked.map((chosen) => chosen?.id)).toEqual([idOf(45)]);
  });

  it("takes an emptied field for 'none' where none is a choice, and puts the name back where one must be chosen", async () => {
    const { picked, field } = show();
    fireEvent.click(field);
    fireEvent.click(await screen.findByRole("option", { name: "Ta'minotchi 03" }));
    expect(picked).toHaveLength(1);
    type(field, "");
    fireEvent.blur(field);
    expect(picked).toEqual([{ id: idOf(3), name: "Ta'minotchi 03", detail: undefined }, null]);
    expect(field.value).toBe("");
    cleanup();

    const required = show({ required: true });
    expect(required.field.getAttribute("placeholder")).toBe("Ta'minotchini tanlang");
    fireEvent.click(required.field);
    await waitFor(() => expect(options()).toHaveLength(SUPPLIER_PAGE + 1));
    // No row for "none": one of them must be chosen.
    expect(options()[0]).toBe("Ta'minotchi 01");
    fireEvent.click(screen.getByRole("option", { name: "Ta'minotchi 03" }));
    type(required.field, "");
    fireEvent.blur(required.field);
    expect(required.picked).toHaveLength(1);
    expect(required.field.value).toBe("Ta'minotchi 03");
    // Text typed and left without choosing is not a choice either.
    type(required.field, "Ta'minotchi 4");
    fireEvent.blur(required.field);
    expect(required.picked).toHaveLength(1);
    expect(required.field.value).toBe("Ta'minotchi 03");
  });

  it("stays open while the focus moves inside it, and closes when it leaves", async () => {
    const { field } = show();
    fireEvent.click(field);
    await firstPage();
    fireEvent.blur(field, { relatedTarget: screen.getByRole("listbox") });
    expect(field.getAttribute("aria-expanded")).toBe("true");
    fireEvent.blur(field, { relatedTarget: screen.getByRole("button", { name: "Keyingi" }) });
    expect(field.getAttribute("aria-expanded")).toBe("false");
  });

  it("shows the server's refusal in place of the list, and asks again when the search changes", async () => {
    let fail = true;
    const server = backend((query) => (fail ? refusal(500, "INTERNAL", "Serverda xatolik.") : ok(supplierListBody(ALL, query))));
    const { field } = show({ server });
    fireEvent.click(field);
    expect((await screen.findByRole("alert")).textContent).toContain("Serverda xatolik.");
    expect(screen.queryAllByRole("option").map((option) => option.textContent)).toEqual([NONE]);
    fail = false;
    type(field, "07");
    await waitFor(() => expect(options()).toEqual(["Ta'minotchi 07"]));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps what was read when the next page fails, says so, and reads it on a second try", async () => {
    let fail = false;
    const server = backend((query) => (fail ? refusal(500, "INTERNAL", "Serverda xatolik.") : ok(supplierListBody(ALL, query))));
    const { field } = show({ server });
    fireEvent.click(field);
    await firstPage();
    fail = true;
    fireEvent.click(screen.getByRole("option", { name: MORE }));
    expect((await screen.findByRole("alert")).textContent).toContain("Serverda xatolik.");
    expect(options()).toHaveLength(SUPPLIER_PAGE + 2);
    fail = false;
    fireEvent.click(screen.getByRole("option", { name: MORE }));
    await waitFor(() => expect(options()).toHaveLength(2 * SUPPLIER_PAGE + 2));
    expect(within(screen.getByRole("listbox")).queryByRole("alert")).toBeNull();
  });
});
