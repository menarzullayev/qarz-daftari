// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { catalogs, compareCatalogs, placeholdersOf, translate } from "../../i18n/catalog";
import { ruImports } from "../../i18n/imports/ru";
import { uzImports } from "../../i18n/imports/uz";
import type { MessageKey } from "../../i18n/types";
import { uz } from "../../i18n/uz";
import { deferred, fakeServer, IMPORT_ID, importBody, importPreviewBody, ok, refusal, type Reply, type Sent, SHOP_BASE, SHOP_ID } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { createApi } from "../api";
import type { Role } from "../navigation";
import type { ShopMode } from "../workspace/shopMode";
import {
  canDiscard,
  FILE_PROBLEMS,
  IMPORT_COLUMNS,
  IMPORT_MAX_BYTES,
  IMPORT_MAX_ROWS,
  IMPORT_STATES,
  importsOf,
  isUnfinished,
  ROW_PROBLEMS,
  STEP_REFUSALS,
  UNDO_HOURS,
  UNDO_REFUSALS,
} from "./importsApi";
import ImportScreen, { POLL_MS, PREVIEW_ROWS, uploadProblem } from "./ImportScreen";

beforeEach(() => {
  window.location.hash = "";
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:template"), revokeObjectURL: vi.fn() }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PATH = `${SHOP_BASE}/imports`;
const ONE = `${PATH}/${IMPORT_ID}`;
const NEW_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeef";
const OTHER_MEMBER = "33333333-3333-4333-8333-3333333d4e5f";
const FAST = 15;
const QUIET = 90;
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const NEXT: Record<string, string> = { apply: "applying", undo: "undoing", discard: "discarded" };
const applied = (overrides: Record<string, unknown> = {}) =>
  importBody({
    status: "applied",
    applied_at: "2026-10-06T06:40:00+00:00",
    undo_until: "2026-10-07T06:40:00+00:00",
    applied: { new_customers: 1, existing_customers: 1, entries: 3, amount: 450000 },
    ...overrides,
  });

type Item = ReturnType<typeof importBody>;

/** A shop's imports behind a fake server: a list that can be changed, and each step answered as the API does. */
function shop(items: Item[] = [], onOther: (sent: Sent) => Reply | null = () => null) {
  const held = { items, preview: importPreviewBody() as unknown, list: null as Reply | null };
  const server = fakeServer((sent) => {
    const special = onOther(sent);
    if (special !== null) {
      return special;
    }
    if (sent.path === PATH && sent.method === "GET") {
      return held.list ?? ok({ items: held.items });
    }
    if (sent.path === PATH) {
      const made = importBody({ id: NEW_ID, status: "uploaded", rows: 0, created_at: "2026-10-06T07:00:00+00:00" });
      held.items = [made, ...held.items];
      return ok(made, 201);
    }
    if (sent.path === `${PATH}/template`) {
      return ok({ a: "workbook" });
    }
    const [, id, step] = /\/imports\/([^/]+)(?:\/([a-z]+))?$/.exec(sent.path) ?? [];
    const found = held.items.find((item) => item.id === id);
    if (found === undefined) {
      return refusal(404, "NOT_FOUND", "Topilmadi.");
    }
    if (sent.method === "GET") {
      return ok({ ...found, preview: held.preview });
    }
    const changed = { ...found, status: NEXT[step ?? ""] ?? found.status };
    held.items = held.items.map((item) => (item.id === id ? changed : item));
    return ok(changed, step === "discard" ? 200 : 202);
  });
  return { ...server, held };
}

type Shop = ReturnType<typeof shop>;
const lists = (server: Shop) => server.sent.filter((sent) => sent.method === "GET" && sent.path === PATH);
const reads = (server: Shop) => server.sent.filter((sent) => sent.method === "GET" && sent.path.startsWith(`${PATH}/`) && !sent.path.endsWith("/template"));
const show = (server: Shop, options: { role?: Role; shopMode?: ShopMode | null; language?: "uz" | "ru"; pollMs?: number } = {}) =>
  renderScreen(<ImportScreen pollMs={options.pollMs ?? FAST} />, {
    fetch: server.fetch,
    role: options.role ?? "manager",
    shopMode: options.shopMode ?? null,
    language: options.language ?? "uz",
  });
const plain = (node: Element | null | undefined) => node?.textContent?.replace(/\s/g, " ");
const rows = (name: string) => within(screen.getByRole("list", { name })).getAllByRole("listitem");
const rowText = (name: string) => rows(name).map((row) => [...row.querySelectorAll("p")].map(plain));
const current = () => screen.getByRole("region", { name: "Tanlangan import" });
const file = (name = "mijozlar.xlsx", content: BlobPart[] = ["PK-not-really"]) => new File(content, name);
const choose = (chosen: File | null) => fireEvent.change(screen.getByLabelText("Fayl"), { target: { files: chosen === null ? [] : [chosen] } });
const upload = () => fireEvent.click(screen.getByRole("button", { name: "Faylni yuklash" }));
/** Opens the one import of the list, as the person does. */
async function opened(server: Shop, options: Parameters<typeof show>[1] = {}) {
  show(server, options);
  fireEvent.click(await screen.findByRole("button", { name: "Ochish" }));
  return current();
}

describe("the import API", () => {
  const api = (server: ReturnType<typeof fakeServer>) => importsOf(createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "t" } }).shop(SHOP_ID));
  const KEY = "key-0000-0001";

  it("sends the file itself as the body with a key, the plan when applying, and no body to undo or discard", async () => {
    const server = shop([importBody()]);
    const client = api(server);
    const sheet = file();
    expect(await client.upload(sheet, KEY)).toMatchObject({ id: NEW_ID, status: "uploaded", rows: 0 });
    await client.template();
    await client.list();
    expect(await client.read(IMPORT_ID)).toMatchObject({ id: IMPORT_ID, preview: { plan: "plan-0001", counts: { newCustomers: 1, existingCustomers: 1, entries: 3, amount: 450000 } } });
    await client.apply(IMPORT_ID, "plan-0001", "key-0000-0002");
    await client.undo(IMPORT_ID, "key-0000-0003");
    await client.discard(IMPORT_ID, "key-0000-0004");
    expect(server.sent.map((sent) => [sent.method, sent.path, sent.body, sent.headers["Idempotency-Key"]])).toEqual([
      ["POST", PATH, undefined, KEY],
      ["GET", `${PATH}/template`, undefined, undefined],
      ["GET", PATH, undefined, undefined],
      ["GET", ONE, undefined, undefined],
      ["POST", `${ONE}/apply`, { plan: "plan-0001" }, "key-0000-0002"],
      ["POST", `${ONE}/undo`, undefined, "key-0000-0003"],
      ["POST", `${ONE}/discard`, undefined, "key-0000-0004"],
    ]);
    // The body is the file, not a form and not JSON: the server reads what it is from its bytes.
    expect(server.sent[0]?.raw).toBe(sheet);
    expect(server.sent[0]?.form).toBeUndefined();
    expect(server.sent[0]?.headers["Content-Type"]).toBeUndefined();
  });

  it("reads a batch as the server gives it: errors by row and column, what was applied, undone and refused", async () => {
    const body = applied({
      errors: [{ row: 5, column: "amount", code: "amount_invalid" }],
      undone: { reversed: 3, archived: 1 },
      refused: { step: "undo", reason: "balance_used" },
    });
    expect((await api(fakeServer(() => ok({ items: [body] }))).list())[0]).toEqual({
      id: IMPORT_ID,
      status: "applied",
      format: "xlsx",
      authorId: "33333333-3333-4333-8333-333333333333",
      createdAt: "2026-10-06T06:30:00+00:00",
      appliedAt: "2026-10-06T06:40:00+00:00",
      undoUntil: "2026-10-07T06:40:00+00:00",
      rows: 3,
      fileProblem: null,
      errors: [{ row: 5, column: "amount", code: "amount_invalid" }],
      applied: { newCustomers: 1, existingCustomers: 1, entries: 3, amount: 450000 },
      undone: { reversed: 3, archived: 1 },
      refused: { step: "undo", reason: "balance_used" },
    });
  });

  it("refuses answers that are not the contract", async () => {
    for (const wrong of [{ rows: 1.5 }, { id: 7 }, { errors: [{ row: "2", column: "name", code: "x" }] }, { applied: { entries: 1 } }, { refused: { step: "apply" } }]) {
      await expect(api(fakeServer(() => ok({ items: [importBody(wrong)] }))).list()).rejects.toMatchObject({ code: "BAD_RESPONSE" });
    }
    await expect(api(fakeServer(() => ok({ ...importBody(), preview: importPreviewBody({ counts: { amount: 1.5 } }) }))).read(IMPORT_ID)).rejects.toMatchObject({ code: "BAD_RESPONSE" });
  });

  it("repeats the server's limits and knows which states the worker still works on and which can be given up", () => {
    expect([IMPORT_MAX_BYTES, IMPORT_MAX_ROWS, UNDO_HOURS, POLL_MS]).toEqual([5 * 1024 * 1024, 2000, 24, 5000]);
    expect(IMPORT_STATES.filter((status) => isUnfinished({ status }))).toEqual(["uploaded", "applying", "undoing"]);
    expect(IMPORT_STATES.filter((status) => canDiscard({ status }))).toEqual(["uploaded", "validated", "rejected", "failed"]);
  });

  it("finds what can be told about a file before it is sent: none, another type, empty, larger than 5 MiB", () => {
    expect(uploadProblem(null)).toBe("required");
    expect(uploadProblem({ name: "a.pdf", size: 10 })).toBe("type");
    expect(uploadProblem({ name: "a.xlsx.exe", size: 10 })).toBe("type");
    expect(uploadProblem({ name: "a.csv", size: 0 })).toBe("empty");
    expect(uploadProblem({ name: "A.XLSX", size: IMPORT_MAX_BYTES })).toBeNull();
    expect(uploadProblem({ name: "a.csv", size: IMPORT_MAX_BYTES + 1 })).toBe("too_large");
  });
});

describe("what an import is", () => {
  it("says what the file is, its limits, that nothing is written before a yes, and that limited mode refuses it", async () => {
    show(shop());
    const section = screen.getByRole("region", { name: "Import" });
    expect(within(section).getByText("Fayl 5 MB dan, qatorlar soni 2000 tadan oshmasligi kerak.")).toBeTruthy();
    expect(within(section).getByText(/Siz tasdiqlamaguningizcha daftarga hech narsa yozilmaydi\./)).toBeTruthy();
    expect(within(section).getByText(/Cheklangan rejimda \(obuna tugaganda\) va to'xtatilgan do'konda fayl yuklash va importni qo'llash rad etiladi\./)).toBeTruthy();
    expect(await screen.findByText("Hali fayl yuklanmagan.")).toBeTruthy();
  });

  it.each([
    ["limited", "Obuna tugagan: hozir fayl yuklash va importni qo'llash rad etiladi."],
    ["suspended", "Do'kon to'xtatilgan: hozir fayl yuklash va importni qo'llash rad etiladi."],
  ] as const)("says it plainly once the shop is known to be %s", (shopMode, text) => {
    show(shop(), { shopMode });
    expect(screen.getByText(text).className).toContain("notice--error");
  });

  it("gives a seller the not-found screen and asks nothing", async () => {
    const server = shop([importBody()]);
    show(server, { role: "seller" });
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Faylni yuklash" })).toBeNull();
    await pause(QUIET);
    expect(server.sent).toEqual([]);
  });

  it("says the same in Russian", async () => {
    show(shop([importBody({ rows: 3 })]), { language: "ru" });
    expect(screen.getByText("Файл — не больше 5 МБ, строк — не больше 2000.")).toBeTruthy();
    await waitFor(() => expect(rowText("Последние импорты")[0]).toEqual(["6 октября 2026 г., 11:30Проверен, ждёт подтверждения", "Вы · 3 строки", "Открыть"]));
  });
});

describe("the template", () => {
  it("is asked for on request and then saved by the person from a link: nothing is saved by itself", async () => {
    const server = shop();
    show(server);
    await screen.findByText("Hali fayl yuklanmagan.");
    expect(server.sent.some((sent) => sent.path.endsWith("/template"))).toBe(false);
    expect(screen.queryByRole("link", { name: "Namuna faylni saqlash" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Namuna faylni tayyorlash" }));
    const link = await screen.findByRole("link", { name: "Namuna faylni saqlash" });
    expect([link.getAttribute("href"), link.getAttribute("download")]).toEqual(["blob:template", "qarz-daftari-import.xlsx"]);
    expect(server.sent.filter((sent) => sent.path.endsWith("/template")).map((sent) => sent.method)).toEqual(["GET"]);
    expect(server.writes()).toEqual([]);
  });

  it("shows the server's words when it cannot be had", async () => {
    show(shop([], (sent) => (sent.path.endsWith("/template") ? refusal(503, "FILE_STORE_UNAVAILABLE", "Fayl ombori ishlamayapti.") : null)));
    fireEvent.click(screen.getByRole("button", { name: "Namuna faylni tayyorlash" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Fayl ombori ishlamayapti.");
    expect(screen.queryByRole("link", { name: "Namuna faylni saqlash" })).toBeNull();
  });
});

describe("uploading a file", () => {
  it("sends nothing for no file, another type, an empty file or one over 5 MiB, and says which", async () => {
    const server = shop();
    show(server);
    await screen.findByText("Hali fayl yuklanmagan.");
    const said = () => screen.getByLabelText("Fayl").parentElement?.querySelector("[role=alert]")?.textContent;
    upload();
    expect(said()).toBe("Faylni tanlang.");
    for (const [chosen, text] of [
      [file("rasm.png"), "Faqat .xlsx yoki .csv fayl yuklanadi."],
      [file("bosh.csv", []), "Fayl bo'sh."],
      [file("katta.xlsx", [new Uint8Array(IMPORT_MAX_BYTES + 1)]), "Fayl 5 MB dan katta."],
    ] as const) {
      choose(chosen);
      expect(said()).toBeUndefined();
      upload();
      expect(said()).toBe(text);
    }
    await pause(QUIET);
    expect(server.writes()).toEqual([]);
  });

  it("sends the file once with a key however often the button is pressed, then opens the new import and reads the list again", async () => {
    const gate = deferred<Exclude<Reply, Promise<unknown>>>();
    const server = shop([], (sent) => (sent.method === "POST" ? gate.promise : null));
    show(server);
    await screen.findByText("Hali fayl yuklanmagan.");
    const sheet = file();
    choose(sheet);
    const form = screen.getByRole("form", { name: "Faylni yuklash" });
    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect((screen.getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement).disabled).toBe(true));
    fireEvent.submit(form);
    server.held.items = [importBody({ id: NEW_ID, status: "uploaded", rows: 0 })];
    gate.resolve(ok(server.held.items[0], 201));
    expect(await screen.findByText("Fayl qabul qilindi. Tekshiruv natijasi quyida ko'rinadi.")).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: PATH });
    expect(server.writes()[0]?.raw).toBe(sheet);
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    await waitFor(() => expect(within(current()).getByText("Tekshirilmoqda")).toBeTruthy());
    expect(within(current()).getByText("Holat o'zi yangilanib turadi.")).toBeTruthy();
    expect(lists(server).length).toBeGreaterThan(1);
  });

  it.each([
    [refusal(422, "VALIDATION", "Ma'lumot noto'g'ri.", { file: "not_a_spreadsheet" }), "Bu .xlsx jadval ham, matnli CSV ham emas."],
    [refusal(422, "VALIDATION", "Ma'lumot noto'g'ri.", { file: "too_large" }), "Fayl 5 MB dan katta."],
    [refusal(413, "BODY_TOO_LARGE", "So'rov juda katta."), "Fayl 5 MB dan katta."],
    [refusal(422, "VALIDATION", "Ma'lumot noto'g'ri.", { file: "something_new" }), "Faylni import qilib bo'lmadi."],
  ])("words the server's refusal of the file next to the file (%#)", async (answer, text) => {
    show(shop([], (sent) => (sent.method === "POST" ? answer : null)));
    choose(file());
    upload();
    await waitFor(() => expect(screen.getByLabelText("Fayl").parentElement?.querySelector("[role=alert]")?.textContent).toBe(text));
    expect(screen.getByLabelText("Fayl").getAttribute("aria-invalid")).toBe("true");
  });

  it("shows the server's own words when the shop is limited, and resends the same key for the same file", async () => {
    const server = shop([], (sent) => (sent.method === "POST" ? refusal(402, "SUBSCRIPTION_LIMITED", "Obuna tugagan: yangi nasiya yozilmaydi.") : null));
    show(server);
    choose(file());
    upload();
    expect(await screen.findByText("Obuna tugagan: yangi nasiya yozilmaydi.")).toBeTruthy();
    upload();
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toBe(server.writes()[1]?.headers["Idempotency-Key"]);
    choose(file("boshqa.csv"));
    upload();
    await waitFor(() => expect(server.writes()).toHaveLength(3));
    expect(server.writes()[2]?.headers["Idempotency-Key"]).not.toBe(server.writes()[0]?.headers["Idempotency-Key"]);
  });
});

describe("reading the list again while the worker works", () => {
  it("asks once when every import rests", async () => {
    const server = shop([importBody(), applied({ id: NEW_ID }), importBody({ id: "x3", status: "rejected" })]);
    show(server);
    await waitFor(() => expect(rows("Oxirgi importlar")).toHaveLength(3));
    await pause(QUIET);
    expect(lists(server)).toHaveLength(1);
  });

  it.each(["uploaded", "applying", "undoing"])("reads again while one is %s, and stops once it rests", async (status) => {
    const server = shop([importBody({ status })]);
    show(server);
    await waitFor(() => expect(lists(server).length).toBeGreaterThanOrEqual(3));
    server.held.items = [importBody({ status: "undone", undone: { reversed: 3, archived: 1 } })];
    await waitFor(() => expect(rowText("Oxirgi importlar")[0]?.[0]).toContain("Bekor qilingan"));
    const settled = lists(server).length;
    await pause(QUIET);
    expect(lists(server)).toHaveLength(settled);
  });

  it("waits the interval between two reads", async () => {
    const server = shop([importBody({ status: "uploaded" })]);
    show(server, { pollMs: 400 });
    await waitFor(() => expect(lists(server)).toHaveLength(1));
    await pause(QUIET);
    expect(lists(server)).toHaveLength(1);
  });

  it("stops when the screen is left, even with a read due", async () => {
    const server = shop([importBody({ status: "applying" })]);
    const view = show(server);
    await waitFor(() => expect(lists(server).length).toBeGreaterThanOrEqual(2));
    view.unmount();
    const left = lists(server).length;
    await pause(QUIET);
    expect(lists(server)).toHaveLength(left);
  });

  it("keeps the list when a later read fails, says so, and stops until asked", async () => {
    const server = shop([importBody({ status: "uploaded" })]);
    show(server);
    await waitFor(() => expect(rows("Oxirgi importlar")).toHaveLength(1));
    server.held.list = "offline";
    expect(await screen.findByText("Holatni yangilab bo'lmadi.")).toBeTruthy();
    expect(rows("Oxirgi importlar")).toHaveLength(1);
    const failed = lists(server).length;
    await pause(QUIET);
    expect(lists(server)).toHaveLength(failed);
    server.held.list = null;
    fireEvent.click(screen.getByRole("button", { name: "Yangilash" }));
    await waitFor(() => expect(screen.queryByText("Holatni yangilab bo'lmadi.")).toBeNull());
  });

  it("shows the server's refusal with a retry when the list cannot be read at all", async () => {
    const server = shop();
    server.held.list = refusal(500, "ERROR", "Xatolik yuz berdi.");
    show(server);
    expect(await screen.findByText("Xatolik yuz berdi.")).toBeTruthy();
    server.held.list = null;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Hali fayl yuklanmagan.")).toBeTruthy();
  });
});

describe("the list of imports", () => {
  it("says when each was uploaded, by whom, its state and its rows; a state it does not know keeps the server's word", async () => {
    show(shop([importBody(), applied({ id: NEW_ID, author_id: OTHER_MEMBER, rows: 40 }), importBody({ id: "x3", status: "paused" })]));
    await waitFor(() =>
      expect(rowText("Oxirgi importlar")).toEqual([
        ["2026-yil 6-oktabr, 11:30Tekshirildi, tasdiq kutmoqda", "Siz · 3 qator", "Ochish"],
        ["2026-yil 6-oktabr, 11:30Qo'llangan", "Xodim · 3d4e5f · 40 qator", "Ochish"],
        ["2026-yil 6-oktabr, 11:30paused", "Siz · 3 qator", "Ochish"],
      ]),
    );
    expect(screen.queryByRole("region", { name: "Tanlangan import" })).toBeNull();
  });

  it("opens one on request, marks it, and reads its preview only when there is one to read", async () => {
    const server = shop([applied(), importBody({ id: NEW_ID })]);
    show(server);
    const buttons = await screen.findAllByRole("button", { name: "Ochish" });
    fireEvent.click(buttons[0] as HTMLElement);
    expect(within(current()).getByText("Qo'llangan")).toBeTruthy();
    expect(rowText("Oxirgi importlar")[0]?.[2]).toBe("Quyida ochilgan");
    await pause(QUIET);
    expect(reads(server)).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Ochish" }));
    await waitFor(() => expect(reads(server).map((sent) => sent.path)).toEqual([`${PATH}/${NEW_ID}`]));
  });
});

describe("a file that cannot be applied", () => {
  it.each([
    ["missing_column", "Ism yoki summa ustuni yo'q. Namuna fayldan foydalaning."],
    ["too_many_rows", "Faylda qatorlar juda ko'p: ko'pi bilan 2000 ta. Faylni bo'lib yuklang."],
    ["encoding", "CSV fayl UTF-8 kodlashida emas. Uni UTF-8 qilib saqlab, qayta yuklang."],
    ["something_new", "Faylni import qilib bo'lmadi."],
  ])("words the refusal of the whole file: %s", async (problem, text) => {
    const section = await opened(shop([importBody({ status: "rejected", rows: 0, file_problem: problem })]));
    expect(within(section).getByText(text)).toBeTruthy();
    expect(within(section).queryByRole("button", { name: "Importni qo'llash" })).toBeNull();
  });

  it("lists the problems of the rows by row and column with a worded code, and offers no apply", async () => {
    const errors = [
      { row: 2, column: "amount", code: "amount_not_whole" },
      { row: 2, column: "phone", code: "phone_invalid" },
      { row: 7, column: "name", code: "ambiguous_customer" },
      { row: 9, column: "extra", code: "brand_new" },
    ];
    const server = shop([importBody({ status: "rejected", rows: 8, errors })]);
    const section = await opened(server);
    expect(within(section).getByText(/Katak ichidagi matn bu yerda ko'rsatilmaydi\./)).toBeTruthy();
    expect(rowText("Qatorlardagi xatolar")).toEqual([
      ["Qator 2 · Summa", "Summa butun so'mda bo'lishi kerak, tiyinsiz."],
      ["Qator 2 · Telefon", "Telefon raqami noto'g'ri."],
      ["Qator 7 · Ism", "Bu qator do'konning bir nechta mijoziga mos keladi. Telefon raqamini aniq yozing."],
      ["Qator 9 · extra", "brand_new"],
    ]);
    expect(within(section).queryByRole("button", { name: "Importni qo'llash" })).toBeNull();
    await pause(QUIET);
    expect(reads(server)).toEqual([]);
  });

  it("says why a check, an apply or an undo the worker was asked for was not done", async () => {
    for (const [body, lines] of [
      [importBody({ status: "failed", refused: { step: "check", reason: "timeout" } }), ["Faylni tekshirish bajarilmadi.", "Ish juda uzoq davom etdi va to'xtatildi. Qayta urinib ko'ring."]],
      [importBody({ refused: { step: "apply", reason: "file_gone" } }), ["Importni qo'llash bajarilmadi.", "Bu importning fayli endi saqlanmaydi. Faylni qayta yuklang."]],
      [applied({ refused: { step: "undo", reason: "balance_used" } }), ["Importni bekor qilish bajarilmadi.", "Bekor qilib bo'lmadi: import yozgan qarzlar bo'yicha keyin to'lov yoki boshqa yozuv qilingan."]],
      [importBody({ status: "failed", refused: { step: "later", reason: "odd" } }), ["Sabab kodi: odd"]],
    ] as const) {
      const section = await opened(shop([body]));
      expect([...(within(section).getByRole("note").querySelectorAll("p") ?? [])].map(plain)).toEqual(lines);
      cleanup();
    }
  });
});

describe("the preview and applying", () => {
  it("shows the counts and, row by row, what will be created and what matches an existing customer and how", async () => {
    const server = shop([importBody()]);
    const section = await opened(server);
    await within(section).findByRole("heading", { name: "Nima yoziladi" });
    expect([...section.querySelectorAll("dl.facts")].at(-1)?.textContent?.replace(/\s/g, " ")).toBe("Yangi mijozlar1Mavjud mijozlar1Qarz yozuvlari3Jami summa450 000 so'm");
    expect(rowText("Qatorlar")).toEqual([
      ["2. Sardor Aliyev200 000 so'm", "+998901112233 · 2026-yil 20-oktabr · eski daftar", "Yangi mijoz ochiladi"],
      ["3. Sardor Aliyev50 000 so'm", "", "2-qatordagi yangi mijozga qo'shiladi"],
      ["4. Ali Valiyev200 000 so'm", "", "Mavjud mijozga qo'shiladi (ism mos keldi): Ali Valiyev, hozirgi qarzi 120 000 so'm"],
    ]);
    expect(reads(server).map((sent) => sent.path)).toEqual([ONE]);
    expect(server.writes()).toEqual([]);
  });

  it("draws the first hundred rows of a long file and the rest on request", async () => {
    const one = importPreviewBody().rows[0];
    const server = shop([importBody({ rows: 130 })]);
    server.held.preview = importPreviewBody({ rows: Array.from({ length: 130 }, (_, index) => ({ ...one, row: index + 2 })) });
    const section = await opened(server);
    await waitFor(() => expect(rows("Qatorlar")).toHaveLength(PREVIEW_ROWS));
    expect(within(section).getByText("Dastlabki 100 qator ko'rsatilgan; fayldagi qatorlar: 130.")).toBeTruthy();
    fireEvent.click(within(section).getByRole("button", { name: "Barchasini ko'rsatish" }));
    expect(rows("Qatorlar")).toHaveLength(130);
  });

  it("asks before applying, sends nothing on going back, and on yes one request with a key and the plan that was shown", async () => {
    const server = shop([importBody()]);
    const section = await opened(server);
    fireEvent.click(await within(section).findByRole("button", { name: "Importni qo'llash" }));
    expect(plain(within(section).getByRole("group").querySelector("p"))).toBe("Daftarga yozilsinmi? Qarz yozuvlari: 3. Jami: 450 000 so'm. Yangi mijozlar: 1.");
    fireEvent.click(within(section).getByRole("button", { name: "Orqaga" }));
    await pause(QUIET);
    expect(server.writes()).toEqual([]);
    fireEvent.click(within(section).getByRole("button", { name: "Importni qo'llash" }));
    // The server's preview moves on meanwhile; what is sent is still the plan the person is looking at.
    server.held.preview = importPreviewBody({ plan: "plan-0002" });
    const yes = within(section).getByRole("button", { name: "Ha, qo'llash" });
    fireEvent.click(yes);
    fireEvent.click(yes);
    await waitFor(() => expect(within(current()).getByText("Daftarga yozilmoqda")).toBeTruthy());
    expect(server.writes().map((sent) => [sent.method, sent.path, sent.body])).toEqual([["POST", `${ONE}/apply`, { plan: "plan-0001" }]]);
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(within(current()).queryByRole("button", { name: "Importni qo'llash" })).toBeNull();
  });

  it("says a stale preview is stale, reads the new one, and applies that one only after a new yes with a new key", async () => {
    let stale = true;
    const server = shop([importBody()], (sent) => (sent.path.endsWith("/apply") && stale ? refusal(409, "IMPORT_NOT_APPLICABLE", "Bu importni hozirgi holatida qo'llab bo'lmaydi.", { reason: "stale" }) : null));
    const section = await opened(server);
    fireEvent.click(await within(section).findByRole("button", { name: "Importni qo'llash" }));
    server.held.preview = importPreviewBody({ plan: "plan-0002", counts: { new_customers: 0, existing_customers: 2, entries: 3, amount: 450000 } });
    fireEvent.click(within(section).getByRole("button", { name: "Ha, qo'llash" }));
    expect(await within(section).findByText(/Bu ko'rib chiqish eskirgan: undan keyin mijozlar o'zgargan\./)).toBeTruthy();
    await waitFor(() => expect(reads(server)).toHaveLength(2));
    // The question is gone: nothing more is sent until the person has seen the new preview and says yes.
    await within(section).findByRole("button", { name: "Importni qo'llash" });
    expect(within(section).queryByRole("button", { name: "Ha, qo'llash" })).toBeNull();
    await pause(QUIET);
    expect(server.writes()).toHaveLength(1);
    stale = false;
    fireEvent.click(within(section).getByRole("button", { name: "Importni qo'llash" }));
    expect(plain(within(section).getByRole("group").querySelector("p"))).toContain("Yangi mijozlar: 0.");
    fireEvent.click(within(section).getByRole("button", { name: "Ha, qo'llash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]?.body).toEqual({ plan: "plan-0002" });
    expect(server.writes()[1]?.headers["Idempotency-Key"]).not.toBe(server.writes()[0]?.headers["Idempotency-Key"]);
  });

  it("says the import is no longer in that state for another reason, and reads the list again", async () => {
    const server = shop([importBody()], (sent) => (sent.path.endsWith("/apply") ? refusal(409, "IMPORT_NOT_APPLICABLE", "Qo'llab bo'lmaydi.", { reason: "discarded" }) : null));
    const section = await opened(server);
    fireEvent.click(await within(section).findByRole("button", { name: "Importni qo'llash" }));
    const before = lists(server).length;
    fireEvent.click(within(section).getByRole("button", { name: "Ha, qo'llash" }));
    expect(await within(section).findByText("Importning holati o'zgargan. Ro'yxat yangilandi.")).toBeTruthy();
    await waitFor(() => expect(lists(server).length).toBeGreaterThan(before));
  });

  it("leaves the question open with the server's words when the shop is limited, and answers it again with the same key", async () => {
    const server = shop([importBody()], (sent) => (sent.path.endsWith("/apply") ? refusal(402, "SUBSCRIPTION_LIMITED", "Obuna tugagan: yangi nasiya yozilmaydi.") : null));
    const section = await opened(server);
    fireEvent.click(await within(section).findByRole("button", { name: "Importni qo'llash" }));
    fireEvent.click(within(section).getByRole("button", { name: "Ha, qo'llash" }));
    expect(await within(section).findByText("Obuna tugagan: yangi nasiya yozilmaydi.")).toBeTruthy();
    fireEvent.click(within(section).getByRole("button", { name: "Ha, qo'llash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toBe(server.writes()[1]?.headers["Idempotency-Key"]);
  });

  it("offers no apply when the preview names no plan", async () => {
    const server = shop([importBody()]);
    server.held.preview = importPreviewBody({ plan: null });
    const section = await opened(server);
    expect(await within(section).findByText("Ko'rib chiqish ma'lumoti topilmadi. Faylni qayta yuklang.")).toBeTruthy();
    expect(within(section).queryByRole("button", { name: "Importni qo'llash" })).toBeNull();
  });
});

describe("the result and the undo", () => {
  it("says what was written and until when it can be undone", async () => {
    const section = await opened(shop([applied()]));
    expect(plain(within(section).getByText(/^Daftarga yozildi\./))).toBe("Daftarga yozildi. Qarz yozuvlari: 3. Jami: 450 000 so'm. Yangi mijozlar: 1. Mavjud mijozlar: 1.");
    expect(within(section).getByText("Importni 2026-yil 7-oktabr, 11:40 gacha bekor qilish mumkin.")).toBeTruthy();
    expect(within(section).queryByRole("button", { name: "Fayldan voz kechish" })).toBeNull();
  });

  it("asks before undoing, sends nothing on going back, and one request with a key and no body on yes", async () => {
    const server = shop([applied()]);
    const section = await opened(server);
    fireEvent.click(within(section).getByRole("button", { name: "Importni bekor qilish" }));
    expect(within(section).getByText("Bu import yozgan barcha qarz yozuvlari teskari yozuv bilan bekor qilinadi. Davom etilsinmi?")).toBeTruthy();
    fireEvent.click(within(section).getByRole("button", { name: "Orqaga" }));
    await pause(QUIET);
    expect(server.writes()).toEqual([]);
    fireEvent.click(within(section).getByRole("button", { name: "Importni bekor qilish" }));
    fireEvent.click(within(section).getByRole("button", { name: "Ha, bekor qilish" }));
    await waitFor(() => expect(within(current()).getByText("Bekor qilinmoqda")).toBeTruthy());
    expect(server.writes().map((sent) => [sent.method, sent.path, sent.body])).toEqual([["POST", `${ONE}/undo`, undefined]]);
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
  });

  it.each([
    ["too_late", "Bekor qilish muddati o'tgan: import qo'llanganidan keyin 24 soat ichida bekor qilinadi."],
    ["not_applied", "Bu import qo'llanmagan: bekor qilinadigan yozuv yo'q."],
    ["balance_used", "Bekor qilib bo'lmadi: import yozgan qarzlar bo'yicha keyin to'lov yoki boshqa yozuv qilingan."],
  ])("words a refused undo: %s", async (reason, text) => {
    const server = shop([applied()], (sent) => (sent.path.endsWith("/undo") ? refusal(409, "IMPORT_UNDO_REFUSED", "Bu importni bekor qilib bo'lmaydi.", { reason }) : null));
    const section = await opened(server);
    const before = lists(server).length;
    fireEvent.click(within(section).getByRole("button", { name: "Importni bekor qilish" }));
    fireEvent.click(within(section).getByRole("button", { name: "Ha, bekor qilish" }));
    expect((await within(section).findByRole("alert")).textContent).toBe(text);
    expect(within(section).queryByRole("button", { name: "Ha, bekor qilish" })).toBeNull();
    await waitFor(() => expect(lists(server).length).toBeGreaterThan(before));
  });

  it("keeps the question open with the server's words for a reason it does not know", async () => {
    const server = shop([applied()], (sent) => (sent.path.endsWith("/undo") ? refusal(409, "IMPORT_UNDO_REFUSED", "Bu importni bekor qilib bo'lmaydi.", { reason: "new_rule" }) : null));
    const section = await opened(server);
    fireEvent.click(within(section).getByRole("button", { name: "Importni bekor qilish" }));
    fireEvent.click(within(section).getByRole("button", { name: "Ha, bekor qilish" }));
    expect((await within(section).findByRole("alert")).textContent).toBe("Bu importni bekor qilib bo'lmaydi.");
  });

  it("says what an undo took back, and offers nothing more for it", async () => {
    const section = await opened(shop([importBody({ status: "undone", undone: { reversed: 3, archived: 1 } })]));
    expect(within(section).getByText("Bekor qilindi. Qaytarilgan yozuvlar: 3. Arxivlangan mijozlar: 1.")).toBeTruthy();
    expect(within(section).queryAllByRole("button")).toEqual([]);
  });
});

describe("giving a file up", () => {
  it("asks first, sends nothing on going back, and one request with a key on yes", async () => {
    const server = shop([importBody({ status: "rejected", rows: 0, file_problem: "no_rows" })]);
    const section = await opened(server);
    fireEvent.click(within(section).getByRole("button", { name: "Fayldan voz kechish" }));
    expect(within(section).getByText("Bu fayldan voz kechilsinmi? Daftarga undan hech narsa yozilmaydi.")).toBeTruthy();
    fireEvent.click(within(section).getByRole("button", { name: "Orqaga" }));
    await pause(QUIET);
    expect(server.writes()).toEqual([]);
    fireEvent.click(within(section).getByRole("button", { name: "Fayldan voz kechish" }));
    fireEvent.click(within(section).getByRole("button", { name: "Ha, voz kechish" }));
    await waitFor(() => expect(within(current()).getByText("Voz kechilgan")).toBeTruthy());
    expect(server.writes().map((sent) => [sent.method, sent.path, sent.body])).toEqual([["POST", `${ONE}/discard`, undefined]]);
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(within(current()).queryByRole("button", { name: "Fayldan voz kechish" })).toBeNull();
  });

  it.each(["applying", "applied", "undoing", "undone", "discarded"])("is not offered for an import that is %s", async (status) => {
    const section = await opened(shop([importBody({ status })]));
    expect(within(section).queryByRole("button", { name: "Fayldan voz kechish" })).toBeNull();
  });

  it("says the import was applied meanwhile when the server refuses, and reads the list again", async () => {
    const server = shop([importBody({ status: "failed" })], (sent) => (sent.path.endsWith("/discard") ? refusal(409, "IMPORT_NOT_APPLICABLE", "Qo'llab bo'lmaydi.", { reason: "applied" }) : null));
    const section = await opened(server);
    const before = lists(server).length;
    fireEvent.click(within(section).getByRole("button", { name: "Fayldan voz kechish" }));
    fireEvent.click(within(section).getByRole("button", { name: "Ha, voz kechish" }));
    expect((await within(section).findByRole("alert")).textContent).toBe("Importning holati o'zgargan. Ro'yxat yangilandi.");
    await waitFor(() => expect(lists(server).length).toBeGreaterThan(before));
  });
});

describe("the import screen's catalog", () => {
  it("has the same keys, kinds and placeholders in both languages, and shares none with the main catalog", () => {
    expect(compareCatalogs(uzImports, ruImports)).toEqual({ missingInSecond: [], missingInFirst: [], kindMismatch: [], placeholderMismatch: [] });
    expect(Object.keys(uzImports).filter((key) => key in uz)).toEqual([]);
    const without = Object.fromEntries(Object.entries(ruImports).filter(([key]) => key !== "imports.apply"));
    expect(compareCatalogs(uzImports, without).missingInSecond).toEqual(["imports.apply"]);
    expect(compareCatalogs(uzImports, { ...ruImports, "imports.rows": "{count}" }).kindMismatch).toEqual(["imports.rows"]);
  });

  it("resolves every message in both languages", () => {
    for (const lang of ["uz", "ru"] as const) {
      for (const key of Object.keys(uzImports) as MessageKey[]) {
        const message = catalogs[lang][key];
        const template = typeof message === "string" ? message : (Object.values(message)[0] ?? "");
        const params = Object.fromEntries(placeholdersOf(template).map((name) => [name, 3]));
        expect(translate(lang, key, typeof message === "string" ? params : { ...params, count: 3 })).not.toMatch(/[{}]/);
      }
    }
  });

  it("uses only the plain apostrophe and no Cyrillic in Uzbek, and ends no Russian sentence with a date", () => {
    const entries = Object.entries(uzImports);
    expect(entries.filter(([, message]) => /[`‘’ʻʼ´]/.test(JSON.stringify(message))).map(([key]) => key)).toEqual([]);
    expect(entries.filter(([, message]) => /\p{Script=Cyrillic}/u.test(JSON.stringify(message))).map(([key]) => key)).toEqual([]);
    expect(Object.entries(ruImports).filter(([, message]) => typeof message === "string" && /\{date\}[.!?]$/.test(message)).map(([key]) => key)).toEqual([]);
  });

  it("has a word for every state, file problem, row problem, column and refusal the screen names by the server's word", () => {
    const keys = [
      ...IMPORT_STATES.map((state) => `imports.state.${state}`),
      ...FILE_PROBLEMS.map((problem) => `imports.problem.${problem}`),
      ...ROW_PROBLEMS.map((problem) => `imports.row.${problem}`),
      ...IMPORT_COLUMNS.map((column) => `imports.column.${column}`),
      ...STEP_REFUSALS.filter((reason) => reason !== "balance_used").map((reason) => `imports.refused.reason.${reason}`),
      ...UNDO_REFUSALS.map((reason) => `imports.undo.refused.${reason}`),
      ...["check", "apply", "undo"].map((step) => `imports.refused.step.${step}`),
      ...["limited", "suspended"].map((mode) => `imports.mode.${mode}`),
    ];
    expect(keys.filter((key) => !(key in uzImports))).toEqual([]);
  });
});

describe("the import of a shop that works in dollars", () => {
  const api = (server: ReturnType<typeof fakeServer>) => importsOf(createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "t" } }).shop(SHOP_ID));
  const row = { phone: null, promised_date: null, note: null, matched_by: null, same_as_row: null, customer: null };
  const both = () =>
    importPreviewBody({
      counts: { new_customers: 1, existing_customers: 1, entries: 3, amount: 200000, usd: { amount: 2050 } },
      rows: [
        { ...row, row: 2, name: "Sardor Aliyev", amount: 200000, action: "create" },
        { ...row, row: 3, name: "Sardor Aliyev", amount: 1250, currency: "USD", action: "same_as_row", matched_by: "name", same_as_row: 2 },
        {
          ...row,
          row: 4,
          name: "Ali Valiyev",
          amount: 800,
          currency: "USD",
          action: "existing",
          matched_by: "name",
          customer: { id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", display_name: "Ali Valiyev", phone: null, balance: 120000, usd: { balance: 700 } },
        },
      ],
    });
  /** The shop of `shop()`, whose list says that its files may have the currency column. */
  const dollarShop = (items: Item[] = [importBody()]) => {
    const server = shop(items);
    server.held.list = null;
    const fetch: typeof server.fetch = async (input, init) => {
      const response = await server.fetch(input, init);
      const listed = (init?.method ?? "GET") === "GET" && String(input).endsWith("/imports");
      return listed ? new Response(JSON.stringify({ ...(await response.json()), currency_column: true }), { status: 200, headers: { "content-type": "application/json" } }) : response;
    };
    server.held.preview = both();
    return { ...server, fetch };
  };

  it("reads a dollar row's currency, the dollar total and a matched customer's dollar debt, and nothing of them for a so'm shop", async () => {
    const dollars = dollarShop();
    const read = await api(dollars).read(IMPORT_ID);
    expect(read.preview?.counts).toEqual({ newCustomers: 1, existingCustomers: 1, entries: 3, amount: 200000, usd: 2050 });
    expect(read.preview?.rows.map((each) => [each.amount, each.currency])).toEqual([
      [200000, undefined],
      [1250, "USD"],
      [800, "USD"],
    ]);
    expect(read.preview?.rows[2]?.customer).toMatchObject({ balance: 120000, usd: 700 });
    expect(await api(dollars).listed()).toMatchObject({ currencyColumn: true });
    // A shop without dollars: no key of the answer, none in what is read.
    const plainShop = shop([importBody()]);
    const before = await api(plainShop).read(IMPORT_ID);
    expect(before.preview?.counts).toEqual({ newCustomers: 1, existingCustomers: 1, entries: 3, amount: 450000 });
    expect(before.preview?.rows.every((each) => !("currency" in each) && (each.customer === null || !("usd" in each.customer)))).toBe(true);
    expect(await api(plainShop).listed()).toMatchObject({ currencyColumn: false });
    await expect(api(fakeServer(() => ok({ ...importBody(), preview: importPreviewBody({ counts: { ...both().counts, usd: { amount: 1.5 } } }) }))).read(IMPORT_ID)).rejects.toMatchObject({
      code: "BAD_RESPONSE",
    });
  });

  it("tells of the currency column, shows each row in its own currency and a total for each currency, never one sum", async () => {
    const server = dollarShop();
    const section = await opened(server);
    expect(screen.getByText(/«Valyuta» ustuniga UZS yoki USD yoziladi, bo'sh katak — so'm\./)).toBeTruthy();
    await within(section).findByRole("heading", { name: "Nima yoziladi" });
    expect([...section.querySelectorAll("dl.facts")].at(-1)?.textContent?.replace(/\s/g, " ")).toBe(
      "Yangi mijozlar1Mavjud mijozlar1Qarz yozuvlari3Jami summa200 000 so'mJami summa, dollarda20.50 $",
    );
    expect(rowText("Qatorlar")).toEqual([
      ["2. Sardor Aliyev200 000 so'm", "", "Yangi mijoz ochiladi"],
      ["3. Sardor Aliyev12.50 $", "", "2-qatordagi yangi mijozga qo'shiladi"],
      ["4. Ali Valiyev8.00 $", "", "Mavjud mijozga qo'shiladi (ism mos keldi): Ali Valiyev, hozirgi qarzi 120 000 so'm · 7.00 $"],
    ]);
    expect(plain(section)).not.toContain("202 050");
    fireEvent.click(within(section).getByRole("button", { name: "Importni qo'llash" }));
    expect(plain(within(section).getByRole("group").querySelector("p"))).toBe("Daftarga yozilsinmi? Qarz yozuvlari: 3. Jami: 200 000 so'm va 20.50 $. Yangi mijozlar: 1.");
  });

  it("says each currency's total of what was written, and the dollars alone when no row was so'm", async () => {
    const written = { new_customers: 1, existing_customers: 1, entries: 3, amount: 200000, usd: { amount: 2050 } };
    const section = await opened(dollarShop([applied({ applied: written })]));
    expect(plain(within(section).getByText(/^Daftarga yozildi\./))).toBe("Daftarga yozildi. Qarz yozuvlari: 3. Jami: 200 000 so'm va 20.50 $. Yangi mijozlar: 1. Mavjud mijozlar: 1.");
    cleanup();
    const onlyDollars = await opened(dollarShop([applied({ applied: { ...written, amount: 0 } })]));
    expect(plain(within(onlyDollars).getByText(/^Daftarga yozildi\./))).toContain("Jami: 20.50 $. Yangi");
    cleanup();
    // A dollar shop whose file had no dollar row reads as a so'm shop's does.
    const noDollars = await opened(dollarShop([applied({ applied: { ...written, usd: { amount: 0 } } })]));
    expect(plain(within(noDollars).getByText(/^Daftarga yozildi\./))).toContain("Jami: 200 000 so'm. Yangi");
  });

  it("says nothing of a currency column or a dollar total to a shop without dollars", async () => {
    const section = await opened(shop([importBody()]));
    await within(section).findByRole("heading", { name: "Nima yoziladi" });
    expect(screen.queryByText(/Valyuta/)).toBeNull();
    expect(screen.queryByText("Jami summa, dollarda")).toBeNull();
    expect(plain(document.body)).not.toContain("$");
  });

  it("words the problems of a currency cell and of a dollar amount, and the refusal of a shop that left dollars", async () => {
    const errors = [
      { row: 2, column: "currency", code: "currency_unknown" },
      { row: 3, column: "amount", code: "amount_too_precise" },
    ];
    const section = await opened(dollarShop([importBody({ status: "rejected", errors })]));
    expect(rowText("Qatorlardagi xatolar")).toEqual([
      ["Qator 2 · Valyuta", "Valyuta UZS yoki USD bo'lishi kerak. Bo'sh katak — so'm."],
      ["Qator 3 · Summa", "Dollardagi summada nuqtadan keyin ko'pi bilan ikki raqam yoziladi, masalan 12.50."],
    ]);
    expect(within(section).queryByRole("button", { name: "Importni qo'llash" })).toBeNull();
    cleanup();
    const refused = await opened(dollarShop([importBody({ refused: { step: "apply", reason: "usd_off" } })]));
    expect(within(refused).getByText(/Faylda dollardagi qatorlar bor, do'kon esa endi dollarda ishlamaydi\./)).toBeTruthy();
  });
});
