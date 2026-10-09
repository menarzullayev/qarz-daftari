// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { fakeServer, ok, refusal, type Reply, type Sent } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { CounterDocumentsScreen } from "./CounterDocuments";
import { DocumentScreen } from "./DocumentScreens";
import { DOCUMENT_ID, documentBody, STOCK, stockSettingsBody, SUPPLIER_ID, SUPPLIERS } from "./testing";

/**
 * The stock's documents on a phone: the list the Mini App reaches from the stock, where a draft left by
 * the quick receipt is found, continued or thrown away. Who is offered what follows the permission of
 * each kind of document, never the role.
 */

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");
const MANAGER = { role: "manager" as const };
/** A seller by role whom the owner lets correct the books, and not receive goods. */
const ADJUSTER = { role: "seller" as const, permissions: ["stock.view", "stock.adjust"] };
const WRITE_OFF_ID = "88888888-8888-4888-8888-888888888882";
const POSTED_ID = "88888888-8888-4888-8888-888888888883";

const receiptDraft = (overrides: Record<string, unknown> = {}) =>
  documentBody({ status: "draft", posted_at: null, supplier: { id: SUPPLIER_ID, name: "Baraka ulgurji" }, paid: 0, ...overrides });
const writeOffDraft = () =>
  documentBody({ id: WRITE_OFF_ID, kind: "write_off", number: 2, status: "draft", posted_at: null, reason: "expired", currency: undefined, total: undefined, paid: undefined });
const summary = (body: Record<string, unknown>) => ({ ...body, lines: undefined });

type Page = { documents: unknown[]; next_cursor: string | null };

function backend(pages: (sent: Sent) => Page | Reply, write: (sent: Sent) => Reply = () => NOT_FOUND, document: () => unknown = receiptDraft) {
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return write(sent);
    }
    switch (sent.path) {
      case `${STOCK}/settings`:
        return ok(stockSettingsBody());
      case SUPPLIERS:
        return ok({ suppliers: [], totals: [], next_cursor: null });
      case `${STOCK}/documents`: {
        const answer = pages(sent);
        return typeof answer === "object" && "documents" in answer ? ok(answer) : answer;
      }
      case `${STOCK}/documents/${DOCUMENT_ID}`:
        return ok(document());
      default:
        return NOT_FOUND;
    }
  });
}

const drafts = (): Page => ({ documents: [summary(receiptDraft()), summary(writeOffDraft())], next_cursor: null });
const asked = (server: ReturnType<typeof fakeServer>) =>
  server.sent.filter((sent) => sent.path === `${STOCK}/documents`).map((sent) => sent.query);
const rows = () => within(screen.getByRole("list", { name: "Ombor hujjatlari" })).getAllByRole("listitem");

describe("the documents on a phone", () => {
  it("asks for the drafts first and shows each with what may be done with it", async () => {
    const server = backend(drafts);
    renderScreen(<CounterDocumentsScreen />, { fetch: server.fetch, ...MANAGER });
    await screen.findByRole("list", { name: "Ombor hujjatlari" });
    expect(asked(server)).toEqual([{ status: "draft" }]);
    expect(rows().map((row) => row.textContent)).toEqual([
      "Kirim № 7 Qoralama2026-yil 6-oktabr · Baraka ulgurji24 000 so'mDavom ettirishQoralamani o'chirish",
      "Hisobdan chiqarish № 2 Qoralama2026-yil 6-oktabrDavom ettirishQoralamani o'chirish",
    ]);
    const first = rows()[0] as HTMLElement;
    expect(within(first).getByRole("link", { name: "Davom ettirish" }).getAttribute("href")).toBe(`#/stock/documents/${DOCUMENT_ID}`);
    expect(within(first).getByRole("link", { name: "Kirim № 7" }).getAttribute("href")).toBe(`#/stock/documents/${DOCUMENT_ID}`);
    expect(screen.getByRole("button", { name: "Qoralama" }).getAttribute("aria-pressed")).toBe("true");
    // No table: a phone reads rows.
    expect(document.querySelector("table")).toBeNull();
  });

  it("offers a member without stock.receive nothing on a receipt's draft, and still their own kind", async () => {
    const server = backend(drafts);
    renderScreen(<CounterDocumentsScreen />, { fetch: server.fetch, ...ADJUSTER });
    await screen.findByRole("list", { name: "Ombor hujjatlari" });
    const [receipt, writeOff] = rows() as [HTMLElement, HTMLElement];
    // The receipt can be read, since the server lets them, and that is all.
    expect(within(receipt).getByRole("link", { name: "Kirim № 7" })).toBeTruthy();
    expect(within(receipt).queryByRole("link", { name: "Davom ettirish" })).toBeNull();
    expect(within(receipt).queryByRole("button", { name: "Qoralamani o'chirish" })).toBeNull();
    expect(within(writeOff).getByRole("link", { name: "Davom ettirish" }).getAttribute("href")).toBe(`#/stock/documents/${WRITE_OFF_ID}`);
    expect(within(writeOff).getByRole("button", { name: "Qoralamani o'chirish" })).toBeTruthy();
    // Nor the quick receipt, which is the same permission.
    expect(screen.queryByRole("link", { name: "Tez kirim" })).toBeNull();
  });

  it("follows the permission and not the role: a manager from whom receiving was taken is offered nothing on a receipt", async () => {
    const server = backend(drafts);
    renderScreen(<CounterDocumentsScreen />, { fetch: server.fetch, role: "manager", permissions: ["stock.view", "stock.adjust"] });
    await screen.findByRole("list", { name: "Ombor hujjatlari" });
    expect(within(rows()[0] as HTMLElement).queryByRole("link", { name: "Davom ettirish" })).toBeNull();
    cleanup();
    // And a seller who was given it is offered what a manager is.
    renderScreen(<CounterDocumentsScreen />, { fetch: server.fetch, role: "seller", permissions: ["stock.view", "stock.receive"] });
    await screen.findByRole("list", { name: "Ombor hujjatlari" });
    expect(within(rows()[0] as HTMLElement).getByRole("link", { name: "Davom ettirish" })).toBeTruthy();
    expect(within(rows()[1] as HTMLElement).queryByRole("link", { name: "Davom ettirish" })).toBeNull();
  });

  it("is not a screen for a member who writes no kind of document, and nothing is asked for them", async () => {
    const server = backend(drafts);
    renderScreen(<CounterDocumentsScreen />, { fetch: server.fetch, role: "seller" });
    expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
    cleanup();
    renderScreen(<DocumentScreen documentId={DOCUMENT_ID} counter />, { fetch: server.fetch, role: "manager", permissions: ["stock.view", "suppliers.view"] });
    expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
    expect(server.sent).toEqual([]);
  });

  it("filters by state, and a posted or cancelled document offers nothing in the list", async () => {
    const posted = summary(documentBody({ id: POSTED_ID, number: 6 }));
    const server = backend((sent) => ({ documents: sent.query["status"] === "draft" ? [summary(receiptDraft())] : [posted], next_cursor: null }));
    renderScreen(<CounterDocumentsScreen />, { fetch: server.fetch, ...MANAGER });
    await screen.findByRole("link", { name: "Kirim № 7" });
    fireEvent.click(screen.getByRole("button", { name: "O'tkazilgan" }));
    const link = await screen.findByRole("link", { name: "Kirim № 6" });
    expect(link.getAttribute("href")).toBe(`#/stock/documents/${POSTED_ID}`);
    expect(rows().map((row) => row.textContent)).toEqual(["Kirim № 6 O'tkazilgan2026-yil 6-oktabr24 000 so'm"]);
    fireEvent.click(screen.getByRole("button", { name: "Bekor qilingan" }));
    fireEvent.click(screen.getByRole("button", { name: "Hammasi" }));
    await waitFor(() => expect(asked(server)).toEqual([{ status: "draft" }, { status: "posted" }, { status: "cancelled" }, {}]));
  });

  it("says that there is no draft, and in other words that a filter found nothing", async () => {
    const server = backend(() => ({ documents: [], next_cursor: null }));
    renderScreen(<CounterDocumentsScreen />, { fetch: server.fetch, ...MANAGER });
    expect(await screen.findByText("Qoralama yo'q. Saqlab qo'yilgan kirim shu yerda turadi.")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Ombor hujjatlari" })).toBeNull();
    // The way to write one is still there for those who may.
    expect(screen.getByRole("link", { name: "Tez kirim" }).getAttribute("href")).toBe("#/stock/receipt");
    fireEvent.click(screen.getByRole("button", { name: "O'tkazilgan" }));
    expect(await screen.findByText("Bunday hujjat yo'q.")).toBeTruthy();
    expect(screen.queryByText("Qoralama yo'q. Saqlab qo'yilgan kirim shu yerda turadi.")).toBeNull();
  });

  it("shows the server's refusal instead of an empty list, and asks again on request", async () => {
    let failing = true;
    const server = backend(() => (failing ? refusal(500, "INTERNAL", "Serverda xatolik.") : drafts()));
    renderScreen(<CounterDocumentsScreen />, { fetch: server.fetch, ...MANAGER });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Serverda xatolik.");
    expect(screen.queryByText("Qoralama yo'q. Saqlab qo'yilgan kirim shu yerda turadi.")).toBeNull();
    failing = false;
    fireEvent.click(within(alert).getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByRole("link", { name: "Kirim № 7" })).toBeTruthy();
    expect(asked(server)).toEqual([{ status: "draft" }, { status: "draft" }]);
  });

  it("reads the next page with the server's cursor, and offers no more after the last", async () => {
    const server = backend((sent) =>
      sent.query["cursor"] === "c2"
        ? { documents: [summary(writeOffDraft())], next_cursor: null }
        : { documents: [summary(receiptDraft())], next_cursor: "c2" },
    );
    renderScreen(<CounterDocumentsScreen />, { fetch: server.fetch, ...MANAGER });
    await screen.findByRole("link", { name: "Kirim № 7" });
    expect(rows()).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Yana ko'rsatish" }));
    await screen.findByRole("link", { name: "Hisobdan chiqarish № 2" });
    expect(rows()).toHaveLength(2);
    expect(asked(server)).toEqual([{ status: "draft" }, { status: "draft", cursor: "c2" }]);
    expect(screen.queryByRole("button", { name: "Yana ko'rsatish" })).toBeNull();
  });

  it("throws a draft away only with a reason, as a cancellation, and reads the list again", async () => {
    let gone = false;
    const server = backend(
      () => ({ documents: gone ? [] : [summary(receiptDraft())], next_cursor: null }),
      (sent) => {
        gone = true;
        return sent.path === `${STOCK}/documents/${DOCUMENT_ID}/cancel` ? ok(receiptDraft({ status: "cancelled", cancel_reason: "Kerak emas" })) : NOT_FOUND;
      },
    );
    renderScreen(<CounterDocumentsScreen />, { fetch: server.fetch, ...MANAGER });
    fireEvent.click(await screen.findByRole("button", { name: "Qoralamani o'chirish" }));
    const form = screen.getByRole("group", { name: "Nima uchun bekor qilinmoqda?" });
    fireEvent.click(within(form).getByRole("button", { name: "Qoralamani o'chirish" }));
    expect(await screen.findByText("Sababni yozing: 1 dan 200 tagacha belgi.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.change(screen.getByRole("textbox", { name: "Nima uchun bekor qilinmoqda?" }), { target: { value: " Kerak  emas " } });
    fireEvent.click(within(form).getByRole("button", { name: "Qoralamani o'chirish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    const write = server.writes()[0];
    // A draft is not deleted: it is cancelled, and stays in the books with its reason.
    expect(write?.method).toBe("POST");
    expect(write?.path).toBe(`${STOCK}/documents/${DOCUMENT_ID}/cancel`);
    expect(write?.body).toEqual({ reason: "Kerak emas" });
    expect(write?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect((await screen.findByRole("status")).textContent).toBe("Qoralama o'chirildi.");
    expect(await screen.findByText("Qoralama yo'q. Saqlab qo'yilgan kirim shu yerda turadi.")).toBeTruthy();
    expect(asked(server)).toHaveLength(2);
  });

  it("keeps the draft and shows why when the server refuses to cancel it", async () => {
    const server = backend(drafts, () => refusal(403, "FORBIDDEN", "Bu amalga ruxsatingiz yo'q."));
    renderScreen(<CounterDocumentsScreen />, { fetch: server.fetch, ...MANAGER });
    fireEvent.click((await screen.findAllByRole("button", { name: "Qoralamani o'chirish" }))[0] as HTMLElement);
    fireEvent.change(screen.getByRole("textbox", { name: "Nima uchun bekor qilinmoqda?" }), { target: { value: "xato" } });
    fireEvent.click(within(screen.getByRole("group", { name: "Nima uchun bekor qilinmoqda?" })).getByRole("button", { name: "Qoralamani o'chirish" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Bu amalga ruxsatingiz yo'q.");
    expect(rows()).toHaveLength(2);
    expect(screen.queryByRole("status")).toBeNull();
    expect(asked(server)).toHaveLength(1);
  });
});

describe("a document opened from the phone's list", () => {
  it("continues a draft in the document form, and leads back to the list", async () => {
    const server = backend(drafts, (sent) => (sent.method === "PUT" ? ok(receiptDraft({ note: "davomi" })) : NOT_FOUND));
    renderScreen(<DocumentScreen documentId={DOCUMENT_ID} counter host={{}} />, { fetch: server.fetch, ...MANAGER });
    // The form, with what the draft holds: not the page that only shows it.
    expect(((await screen.findByLabelText("Miqdor (kg)")) as HTMLInputElement).value).toBe("2");
    expect((screen.getByLabelText("Bir birlik narxi") as HTMLInputElement).value).toBe("12 000");
    expect(screen.getByRole("link", { name: "Ombor hujjatlari" }).getAttribute("href")).toBe("#/stock/documents");
    fireEvent.click(screen.getByRole("button", { name: "Qoralama sifatida saqlash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.method).toBe("PUT");
    expect(server.writes()[0]?.path).toBe(`${STOCK}/documents/${DOCUMENT_ID}`);
    // Saved: the draft as it stands, with the button that makes it take effect.
    expect(await screen.findByRole("button", { name: "O'tkazish" })).toBeTruthy();
  });

  it("shows a draft of a kind the member may not write as it stands: no form, no action", async () => {
    const server = backend(drafts);
    renderScreen(<DocumentScreen documentId={DOCUMENT_ID} counter host={{}} />, { fetch: server.fetch, ...ADJUSTER });
    expect(await screen.findByRole("heading", { name: /Kirim № 7/ })).toBeTruthy();
    expect(screen.queryByLabelText("Miqdor (kg)")).toBeNull();
    for (const name of ["O'tkazish", "Tahrirlash", "Qoralamani o'chirish", "Qoralama sifatida saqlash"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    expect(server.writes()).toEqual([]);
  });

  it("shows a posted document read-only, with the cancellation it always had", async () => {
    const server = backend(drafts, undefined, () => documentBody());
    renderScreen(<DocumentScreen documentId={DOCUMENT_ID} counter host={{}} />, { fetch: server.fetch, ...MANAGER });
    expect(await screen.findByRole("button", { name: "Hujjatni bekor qilish" })).toBeTruthy();
    expect(screen.queryByLabelText("Miqdor (kg)")).toBeNull();
    expect(screen.queryByRole("button", { name: "Tahrirlash" })).toBeNull();
  });

  it("opens the page and not the form in the web panel, as before", async () => {
    const server = backend(drafts);
    renderScreen(<DocumentScreen documentId={DOCUMENT_ID} host={{}} />, { fetch: server.fetch, ...MANAGER });
    expect(await screen.findByRole("button", { name: "Tahrirlash" })).toBeTruthy();
    expect(screen.queryByLabelText("Miqdor (kg)")).toBeNull();
    expect(screen.getByRole("link", { name: "Ombor hujjatlari" }).getAttribute("href")).toBe("#/stock-documents");
  });
});
