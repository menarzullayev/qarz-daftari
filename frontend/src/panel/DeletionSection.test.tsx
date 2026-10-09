// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { deferred, fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../testing/fakeServer";
import { DeletionSection, WAITING_DAYS } from "./DeletionSection";
import { OfficeBanner } from "./OfficeBanner";
import { CSRF, NO_DELETION, PENDING_DELETION, renderOffice, tabStops } from "./testing";

beforeEach(() => {
  window.location.hash = "";
});
afterEach(cleanup);

const PATH = `${SHOP_BASE}/deletion`;

/** A shop whose deletion state the test holds; `extra` answers first. */
function backend(initial: unknown = NO_DELETION, extra: (sent: Sent) => Reply | null = () => null) {
  const held = { deletion: initial };
  const server = fakeServer((sent) => {
    const special = extra(sent);
    if (special !== null) {
      return special;
    }
    if (sent.path === `${SHOP_BASE}/ownership-transfer`) {
      return ok({ pending: null });
    }
    if (sent.path !== PATH) {
      return refusal(404, "NOT_FOUND", "Topilmadi.");
    }
    if (sent.method === "POST") {
      held.deletion = PENDING_DELETION;
      return ok(PENDING_DELETION, 201);
    }
    if (sent.method === "DELETE") {
      held.deletion = NO_DELETION;
      return ok(NO_DELETION);
    }
    return ok(held.deletion);
  });
  return { ...server, held };
}

const page = (
  <>
    <OfficeBanner />
    <DeletionSection />
  </>
);
const nameField = () => screen.findByLabelText("Tasdiqlash uchun do'kon nomini yozing");
/** The notice above every screen while the shop waits to be deleted: the status that holds the way to cancel. */
const bannerOf = () =>
  screen.queryAllByRole("status").find((status) => within(status).queryByRole("link", { name: "Sozlamalarda bekor qilish" }) !== null) ?? null;
/** What the section itself says was done: every status that is not the banner. */
const statuses = () =>
  screen
    .queryAllByRole("status")
    .filter((status) => status !== bannerOf())
    .map((status) => status.textContent);
const ask = () => fireEvent.click(screen.getByRole("button", { name: "Do'konni o'chirishni so'rash" }));

describe("who may delete the shop", () => {
  it.each(["manager", "seller"] as const)("shows a %s no section and no banner, and never asks for the state", async (role) => {
    const server = backend(PENDING_DELETION);
    const view = renderOffice(page, { fetch: server.fetch, role });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(view.container.querySelector("h2")).toBeNull();
    expect(screen.queryByText(/o'chiriladi/)).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(server.sent.filter((sent) => sent.path === PATH)).toHaveLength(0);
  });
});

describe("asking for the shop to be deleted", () => {
  it("explains the waiting period and what is erased before anything can be sent", async () => {
    const server = backend();
    renderOffice(page, { fetch: server.fetch });
    await nameField();
    expect(WAITING_DAYS).toBe(30);
    expect(screen.getByRole("heading", { level: 2, name: "Do'konni o'chirish" })).toBeTruthy();
    expect(screen.getByText(/do'kon yana 30 kun odatdagidek ishlaydi/)).toBeTruthy();
    expect(screen.getByText(/eksport qilishingiz yoki so'rovni bekor qilishingiz mumkin/)).toBeTruthy();
    expect(screen.getByText(/mijozlar, yozuvlar va qarzlar, katalog, sozlamalar/)).toBeTruthy();
    expect(screen.getByText(/Mijozlarning Telegram aloqasi uziladi, xodimlar do'konga kira olmay qoladi/)).toBeTruthy();
    expect(screen.getByText("Do'kon nomi: Baraka savdo")).toBeTruthy();
    // Nothing is pending, so there is no banner.
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("sends nothing until a name is typed", async () => {
    const server = backend();
    renderOffice(page, { fetch: server.fetch });
    const field = (await nameField()) as HTMLInputElement;
    ask();
    expect(screen.getByText("Do'kon nomini yozing.")).toBeTruthy();
    expect(field.getAttribute("aria-invalid")).toBe("true");
    fireEvent.change(field, { target: { value: "   " } });
    ask();
    expect(screen.getByText("Do'kon nomini yozing.")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
  });

  it("shows the server's refusal of a name that is not the shop's, next to the field", async () => {
    const server = backend(NO_DELETION, (sent) =>
      sent.method === "POST" && (sent.body as { confirm_name: string }).confirm_name !== "Baraka savdo"
        ? refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { confirm_name: "must be the shop's name" })
        : null,
    );
    renderOffice(page, { fetch: server.fetch });
    const field = (await nameField()) as HTMLInputElement;
    fireEvent.change(field, { target: { value: "Boshqa do'kon" } });
    ask();
    const message = await screen.findByText("Yozilgan nom do'kon nomiga mos kelmadi.");
    expect(field.getAttribute("aria-describedby")).toContain(message.id);
    expect(field.getAttribute("aria-invalid")).toBe("true");
    expect(server.held.deletion).toEqual(NO_DELETION);
    expect(screen.queryByRole("button", { name: "O'chirishni bekor qilish" })).toBeNull();
  });

  it("sends the typed name once, then shows the day of erasure, the way to cancel, and the banner", async () => {
    const server = backend();
    renderOffice(page, { fetch: server.fetch });
    fireEvent.change(await nameField(), { target: { value: "  Baraka savdo " } });
    ask();
    expect(await screen.findByRole("button", { name: "O'chirishni bekor qilish" })).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: PATH, body: { confirm_name: "Baraka savdo" } });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(server.writes()[0]?.headers["X-CSRF-Token"]).toBe(CSRF);
    // 5 November 07:00 UTC is 5 November in Tashkent.
    const pending = screen.getAllByText("Do'konni o'chirish so'ralgan. Do'kon 2026-yil 5-noyabr da butunlay o'chiriladi.");
    expect(pending).toHaveLength(2);
    const banner = bannerOf();
    expect(banner).not.toBeNull();
    expect(within(banner as HTMLElement).getByRole("link", { name: "Sozlamalarda bekor qilish" }).getAttribute("href")).toBe("#/shop-settings");
    expect(screen.queryByLabelText("Tasdiqlash uchun do'kon nomini yozing")).toBeNull();
    // What was done is said back, apart from the state that now shows it.
    expect(statuses()).toContain("Do'konni o'chirish so'raldi. Belgilangan kungacha so'rovni bekor qilishingiz mumkin.");
  });

  it("does not say the request was made when the server refused it", async () => {
    const server = backend(NO_DELETION, (sent) =>
      sent.method === "POST" ? refusal(422, "VALIDATION", "Xato.", { confirm_name: "mismatch" }) : null,
    );
    renderOffice(page, { fetch: server.fetch });
    fireEvent.change(await nameField(), { target: { value: "Boshqa nom" } });
    ask();
    expect(await screen.findByText("Yozilgan nom do'kon nomiga mos kelmadi.")).toBeTruthy();
    expect(statuses()).toEqual([]);
  });

  it("disables the button while the request is in flight, so a double tap sends one", async () => {
    const gate = deferred<ReturnType<typeof ok>>();
    const server = backend(NO_DELETION, (sent) => (sent.method === "POST" ? gate.promise : null));
    renderOffice(page, { fetch: server.fetch });
    fireEvent.change(await nameField(), { target: { value: "Baraka savdo" } });
    ask();
    const button = screen.getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    fireEvent.submit(button.closest("form") as HTMLFormElement);
    gate.resolve(refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan."));
    expect((await screen.findByRole("alert")).textContent).toContain("Do'kon to'xtatilgan.");
    expect(server.writes()).toHaveLength(1);
    // The same name again is the same action: the key is reused.
    ask();
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]?.headers["Idempotency-Key"]).toBe(server.writes()[0]?.headers["Idempotency-Key"]);
  });

  it("shows the refusal when deletion was already asked for, and then the waiting state", async () => {
    const server = backend(NO_DELETION, (sent) =>
      sent.method === "POST" ? refusal(409, "DELETION_ALREADY_REQUESTED", "Do'konni o'chirish allaqachon so'ralgan.") : null,
    );
    renderOffice(page, { fetch: server.fetch });
    fireEvent.change(await nameField(), { target: { value: "Baraka savdo" } });
    server.held.deletion = PENDING_DELETION;
    ask();
    expect(await screen.findByRole("button", { name: "O'chirishni bekor qilish" })).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Do'konni o'chirish allaqachon so'ralgan.");
    // A refusal is not a success: nothing says the request was made by this press.
    expect(statuses().join(" ")).not.toContain("so'raldi");

    // "Try again" reads the state once more and puts the refusal away.
    const reads = () => server.sent.filter((sent) => sent.method === "GET" && sent.path === PATH).length;
    const before = reads();
    fireEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Qayta urinish" }));
    await waitFor(() => expect(reads()).toBe(before + 1));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(await screen.findByRole("button", { name: "O'chirishni bekor qilish" })).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
  });

  it("can be reached and sent with the keyboard alone", async () => {
    const server = backend();
    const view = renderOffice(<DeletionSection />, { fetch: server.fetch });
    const field = (await nameField()) as HTMLInputElement;
    const stops = tabStops(view.container);
    // Tab reaches the way to an export first, then the field, then the button, and nothing takes focus out of turn.
    expect(stops.map((stop) => stop.tagName)).toEqual(["A", "INPUT", "BUTTON"]);
    expect(stops.every((stop) => stop.tabIndex === 0)).toBe(true);
    stops[1]?.focus();
    expect(document.activeElement).toBe(field);
    fireEvent.change(field, { target: { value: "Baraka savdo" } });
    // Enter in the field submits the form it belongs to.
    expect(field.form).toBe(stops[2]?.closest("form"));
    expect((stops[2] as HTMLButtonElement).type).toBe("submit");
    fireEvent.submit(field.form as HTMLFormElement);
    const cancel = await screen.findByRole("button", { name: "O'chirishni bekor qilish" });
    expect(server.writes()).toHaveLength(1);
    // The waiting state is keyboard-reachable too: the export, cancel, then the two answers.
    expect(tabStops(view.container)).toEqual([screen.getByRole("link", { name: "Eksportga o'tish" }), cancel]);
    cancel.focus();
    fireEvent.click(cancel);
    expect(tabStops(view.container).map((stop) => stop.textContent)).toEqual(["Eksportga o'tish", "Ha, bekor qilinsin", "Yo'q"]);
  });
});

describe("while the shop waits to be deleted", () => {
  it("shows the date and cancels only after a yes", async () => {
    const server = backend(PENDING_DELETION);
    renderOffice(page, { fetch: server.fetch });
    fireEvent.click(await screen.findByRole("button", { name: "O'chirishni bekor qilish" }));
    expect(screen.getByText("Do'konni o'chirish so'rovi bekor qilinsinmi? Do'kon odatdagidek ishlashda davom etadi.")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Yo'q" }));
    expect(server.writes()).toHaveLength(0);
    expect(bannerOf()).not.toBeNull();
    // Nothing was cancelled, and nothing says it was.
    expect(statuses().join(" ")).not.toContain("bekor qilindi");

    fireEvent.click(screen.getByRole("button", { name: "O'chirishni bekor qilish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, bekor qilinsin" }));
    expect(await nameField()).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "DELETE", path: PATH });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toBeTruthy();
    // The banner goes with the request, and the section says what was done.
    expect(bannerOf()).toBeNull();
    expect(statuses()).toEqual(["O'chirish so'rovi bekor qilindi. Do'kon odatdagidek ishlashda davom etadi."]);
  });

  it("shows the refusal when there is no request to cancel any more, and then the form", async () => {
    const server = backend(PENDING_DELETION, (sent) =>
      sent.method === "DELETE" ? refusal(409, "DELETION_NOT_REQUESTED", "Do'konni o'chirish so'ralmagan.") : null,
    );
    renderOffice(page, { fetch: server.fetch });
    fireEvent.click(await screen.findByRole("button", { name: "O'chirishni bekor qilish" }));
    server.held.deletion = NO_DELETION;
    fireEvent.click(screen.getByRole("button", { name: "Ha, bekor qilinsin" }));
    expect(await nameField()).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Do'konni o'chirish so'ralmagan.");
  });

  it("says only that deletion is asked for when the server gives no usable date", async () => {
    const server = backend({ status: "deletion_pending", deletion_due: "soon" });
    renderOffice(page, { fetch: server.fetch });
    expect(await screen.findAllByText("Do'konni o'chirish so'ralgan.")).toHaveLength(2);
  });

  it("shows a failure with a retry when the state cannot be read", async () => {
    let fail = true;
    const server = backend(NO_DELETION, (sent) => (fail && sent.path === PATH ? "offline" : null));
    renderOffice(page, { fetch: server.fetch });
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await nameField()).toBeTruthy();
  });
});
