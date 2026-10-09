// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import jsQR from "jsqr";
import { Suspense } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { deferred, fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../../testing/fakeServer";
import { findHardcodedText } from "../../testing/hardcodedText";
import { paintQr } from "../../testing/qr";
import { renderScreen } from "../../testing/renderScreen";
import type { Role } from "../navigation";
import ShareContactSection from "./ShareContactSection";
import ShareSection, { PRINT_SOLO } from "./ShareSection";
import { shareUrl } from "./shareApi";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.documentElement.classList.remove(PRINT_SOLO);
});

const CUSTOMER = "11111111-0000-4000-8000-000000000001";
const PATH = `${SHOP_BASE}/customers/${CUSTOMER}/share`;
const TOKEN = "Zm9vYmFyLXNoYXJlLXRva2VuLTAxMjM0NTY3ODktYWJ";
const ORIGIN = "https://qarz.example.uz";
const LINK = `${ORIGIN}/k/#${TOKEN}`;
const NONE = { exists: false, expired: false, created_at: null, expires_at: null, last_opened_at: null };
const ALIVE = {
  exists: true,
  expired: false,
  created_at: "2026-10-01T05:00:00+00:00",
  expires_at: "2026-12-30T05:00:00+00:00",
  last_opened_at: null,
};
const ISSUED = { token: TOKEN, created_at: "2026-10-06T07:00:00+00:00", expires_at: "2027-01-04T07:00:00+00:00" };
const OFF = refusal(404, "NOT_FOUND", "Topilmadi.");

/** A shop whose link state changes as the server would change it: made by POST, ended by DELETE. */
function shop(initial: unknown, onWrite: (sent: Sent) => Reply | null = () => null) {
  let state = initial;
  return fakeServer((sent) => {
    if (sent.method === "GET") {
      return ok(state);
    }
    const refused = onWrite(sent);
    if (refused !== null) {
      return refused;
    }
    if (sent.method === "POST") {
      state = { ...ALIVE, expires_at: ISSUED.expires_at };
      return ok(ISSUED, 201);
    }
    state = NONE;
    return ok({ revoked: true });
  });
}

async function open(server: ReturnType<typeof fakeServer>, options: { role?: Role; archived?: boolean } = {}) {
  renderScreen(
    <Suspense fallback={null}>
      <ShareSection customerId={CUSTOMER} archived={options.archived ?? false} origin={ORIGIN} />
    </Suspense>,
    { fetch: server.fetch, role: options.role ?? "manager", shopName: "Baraka do'koni" },
  );
  await waitFor(() => expect(server.sent.length).toBeGreaterThan(0));
  // One more turn, so that an answer that shows nothing has been taken in too.
  await waitFor(() => expect(screen.queryByText("Yuklanmoqda…")).toBeNull());
}

const button = (name: string) => screen.queryByRole<HTMLButtonElement>("button", { name });

describe("with the platform switch off there is nothing on the customer's page", () => {
  it("shows nothing at all when the server says there is no such route", async () => {
    const server = fakeServer(() => OFF);
    await open(server);
    expect(server.sent).toHaveLength(1);
    expect(server.sent[0]).toMatchObject({ method: "GET", path: PATH });
    expect(document.body.textContent).toBe("");
    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it.each([
    ["the network is down", "offline" as const],
    ["the shop is suspended", refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.")],
    ["the answer is something else's", ok({ id: CUSTOMER, display_name: "Ali" })],
    ["the answer is not an object", ok("on")],
  ])("shows nothing when %s", async (_what, reply) => {
    await open(fakeServer(() => reply));
    expect(document.body.textContent).toBe("");
  });

  it("the check above would notice a section that showed: with the switch on there is one", async () => {
    await open(shop(NONE));
    expect(screen.getByRole("heading", { name: "Mijoz uchun havola (Telegramsiz)" })).toBeTruthy();
    expect(button("Havola yaratish")).toBeTruthy();
  });
});

describe("making a link", () => {
  it("shows the link once, as text and as a QR code that reads the same", async () => {
    const server = shop(NONE);
    await open(server);
    expect(screen.getByText("Bu mijoz uchun havola yo'q.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Havola yaratish" }));
    expect(await screen.findByText(LINK)).toBeTruthy();

    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: PATH });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    const svg = await screen.findByRole("img", { name: "Havolaning QR kodi" });
    const side = Number(svg.getAttribute("viewBox")?.split(" ")[2]);
    const image = paintQr({ side, path: svg.querySelector("path")?.getAttribute("d") ?? "" }, 4);
    expect(jsQR(image.data, image.width, image.height)?.data).toBe(LINK);
    expect(document.body.textContent).toContain("Havola faqat hozir ko'rsatiladi");
    expect(screen.getByText("Baraka do'koni")).toBeTruthy();
    expect(screen.getByText("Qarzingizni ko'rish uchun shu kodni skanerlang")).toBeTruthy();
    expect(screen.getByText("Havola amalda: 2027-yil 4-yanvar, 12:00 gacha.")).toBeTruthy();
  });

  it("keeps the secret out of the address and out of storage", async () => {
    await open(shop(NONE));
    fireEvent.click(screen.getByRole("button", { name: "Havola yaratish" }));
    await screen.findByText(LINK);
    expect(window.location.href).not.toContain(TOKEN);
    expect(JSON.stringify({ ...window.localStorage, ...window.sessionStorage })).not.toContain(TOKEN);
  });

  it("forgets the link when it is hidden, and then says only that one exists", async () => {
    const server = shop(NONE);
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Havola yaratish" }));
    await screen.findByText(LINK);
    fireEvent.click(screen.getByRole("button", { name: "Havolani yashirish" }));
    expect(await screen.findByText("Havola amalda: 2027-yil 4-yanvar, 12:00 gacha.")).toBeTruthy();
    expect(document.body.textContent).not.toContain(TOKEN);
    expect(screen.queryByRole("img")).toBeNull();
    expect(button("Yangi havola yaratish")).toBeTruthy();
  });

  it("copies the link on request and says so", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await open(shop(NONE));
    fireEvent.click(screen.getByRole("button", { name: "Havola yaratish" }));
    await screen.findByText(LINK);
    fireEvent.click(screen.getByRole("button", { name: "Nusxalash" }));
    expect(await screen.findByRole("button", { name: "Nusxalandi" })).toBeTruthy();
    expect(writeText).toHaveBeenCalledWith(LINK);
  });

  it("says so when the clipboard refuses, and leaves the text to select", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: () => Promise.reject(new Error("denied")) },
      configurable: true,
    });
    await open(shop(NONE));
    fireEvent.click(screen.getByRole("button", { name: "Havola yaratish" }));
    await screen.findByText(LINK);
    fireEvent.click(screen.getByRole("button", { name: "Nusxalash" }));
    expect(await screen.findByRole("button", { name: "Nusxalab bo'lmadi: matnni qo'lda belgilang" })).toBeTruthy();
    expect(screen.getByText(LINK)).toBeTruthy();
  });

  it("prints the link alone, and puts the page back as it was afterwards", async () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
    await open(shop(NONE));
    fireEvent.click(screen.getByRole("button", { name: "Havola yaratish" }));
    await screen.findByText(LINK);
    expect(document.documentElement.classList.contains(PRINT_SOLO)).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Chop etish" }));
    expect(print).toHaveBeenCalledTimes(1);
    expect(document.documentElement.classList.contains(PRINT_SOLO)).toBe(true);
    window.dispatchEvent(new Event("afterprint"));
    expect(document.documentElement.classList.contains(PRINT_SOLO)).toBe(false);
  });

  it("sends one request for a double tap", async () => {
    const held = deferred<Reply>();
    const server = shop(NONE, () => held.promise as Reply);
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Havola yaratish" }));
    const pending = screen.getByRole<HTMLButtonElement>("button", { name: "Saqlanmoqda…" });
    expect(pending.disabled).toBe(true);
    fireEvent.click(pending);
    held.resolve(ok(ISSUED, 201));
    await screen.findByText(LINK);
    expect(server.writes()).toHaveLength(1);
  });

  it("says that the link did not arrive when the server answers a repeat without it", async () => {
    const server = shop(NONE, () => ok({ ...ISSUED, token: null }, 201));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Havola yaratish" }));
    expect((await screen.findByRole("alert")).textContent).toContain("uning matni bu ekranga yetib kelmadi");
    expect(screen.queryByRole("img")).toBeNull();
    expect(button("Yangi havola yaratish")).toBeTruthy();
  });

  it.each(["not-a-token", `${TOKEN}/../admin`, "javascript:alert(1)", ""])(
    "refuses to build a link from %j",
    (token) => {
      expect(() => shareUrl(ORIGIN, token)).toThrow("not a share token");
    },
  );

  it("shows the server's refusal and makes no link", async () => {
    const server = shop(NONE, () => refusal(409, "CUSTOMER_ARCHIVED", "Mijoz arxivda."));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Havola yaratish" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Mijoz arxivda.");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("offers no link for an archived customer", async () => {
    const server = shop(NONE);
    await open(server, { archived: true });
    expect(screen.getByText("Arxivdagi mijoz uchun havola yaratilmaydi.")).toBeTruthy();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(server.writes()).toHaveLength(0);
  });
});

describe("a link that exists", () => {
  it("says until when it works and whether it was opened, and never shows it", async () => {
    await open(shop({ ...ALIVE, last_opened_at: "2026-10-05T04:30:00+00:00" }));
    expect(screen.getByText("Havola amalda: 2026-yil 30-dekabr, 10:00 gacha.")).toBeTruthy();
    expect(screen.getByText("Oxirgi marta ochilgan: 2026-yil 5-oktabr, 09:30.")).toBeTruthy();
    expect(document.body.textContent).not.toContain("/k/");
    expect(screen.queryByRole("img")).toBeNull();
    cleanup();

    await open(shop(ALIVE));
    expect(screen.getByText("Hali ochilmagan.")).toBeTruthy();
  });

  it("warns that the old link dies and sends nothing until the answer is yes", async () => {
    const server = shop(ALIVE);
    await open(server);
    expect(button("Havola yaratish")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Yangi havola yaratish" }));
    expect(server.writes()).toHaveLength(0);
    expect(document.body.textContent).toContain("avvalgisi shu zahoti ishlamay qoladi");

    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    expect(server.writes()).toHaveLength(0);
    expect(document.body.textContent).not.toContain("ishlamay qoladi");

    fireEvent.click(screen.getByRole("button", { name: "Yangi havola yaratish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, yangisini yaratish" }));
    expect(await screen.findByText(LINK)).toBeTruthy();
    expect(server.writes().map((sent) => sent.method)).toEqual(["POST"]);
  });

  it("ends the link only after a yes, and then says there is none", async () => {
    const server = shop(ALIVE);
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Havolani bekor qilish" }));
    expect(server.writes()).toHaveLength(0);
    expect(document.body.textContent).toContain("Mijoz uni ocha olmaydi");
    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    expect(server.writes()).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Havolani bekor qilish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, bekor qilish" }));
    expect(await screen.findByText("Havola bekor qilindi.")).toBeTruthy();
    expect(await screen.findByText("Bu mijoz uchun havola yo'q.")).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "DELETE", path: PATH });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(button("Havola yaratish")).toBeTruthy();
    expect(button("Havolani bekor qilish")).toBeNull();
  });

  it("keeps the question open and shows why when ending it is refused", async () => {
    const server = shop(ALIVE, () => refusal(403, "FORBIDDEN_ROLE", "Bu amal uchun ruxsat yo'q."));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Havolani bekor qilish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, bekor qilish" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Bu amal uchun ruxsat yo'q.");
    expect(screen.getByText("Havola amalda: 2026-yil 30-dekabr, 10:00 gacha.")).toBeTruthy();
  });

  it("says that an expired link no longer opens and offers a new one without a warning", async () => {
    const server = shop({ ...ALIVE, expired: true });
    await open(server);
    expect(screen.getByText("Havolaning muddati 2026-yil 30-dekabr, 10:00 da tugagan. U endi ochilmaydi.")).toBeTruthy();
    expect(button("Havolani bekor qilish")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Havola yaratish" }));
    expect(await screen.findByText(LINK)).toBeTruthy();
  });
});

describe("in Russian", () => {
  it("says everything in Russian", async () => {
    const server = shop(ALIVE);
    renderScreen(
      <Suspense fallback={null}>
        <ShareSection customerId={CUSTOMER} archived={false} origin={ORIGIN} />
      </Suspense>,
      { fetch: server.fetch, role: "owner", language: "ru" },
    );
    expect(await screen.findByRole("heading", { name: "Ссылка для клиента (без Telegram)" })).toBeTruthy();
    expect(screen.getByText("Ссылка действует до 30 декабря 2026 г., 10:00.")).toBeTruthy();
    expect(button("Отозвать ссылку")).toBeTruthy();
  });
});

describe("the phone a shop shows on the page behind a link", () => {
  const CONTACT = `${SHOP_BASE}/share-contact`;

  async function openContact(server: ReturnType<typeof fakeServer>, editable: boolean) {
    renderScreen(<ShareContactSection editable={editable} />, { fetch: server.fetch, role: editable ? "owner" : "manager" });
    await waitFor(() => expect(server.sent.length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.queryByText("Yuklanmoqda…")).toBeNull());
  }

  it.each([
    ["the switch is off", OFF],
    ["the answer is the shop's settings and not a phone", ok({ id: "x", name: "Baraka", lang: "uz" })],
    ["the network is down", "offline" as const],
  ])("shows nothing when %s", async (_what, reply) => {
    const server = fakeServer(() => reply);
    await openContact(server, true);
    expect(server.sent[0]).toMatchObject({ method: "GET", path: CONTACT });
    expect(document.body.textContent).toBe("");
  });

  it("lets the owner set the phone and shows it as the server keeps it", async () => {
    const server = fakeServer((sent) => (sent.method === "GET" ? ok({ phone: null }) : ok({ phone: "+998901234567" })));
    await openContact(server, true);
    const field = await screen.findByLabelText<HTMLInputElement>("Do'kon telefoni");
    expect(field.value).toBe("");
    fireEvent.change(field, { target: { value: " 90 123 45 67 " } });
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    expect(await screen.findByText("Saqlandi.")).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "PUT", path: CONTACT, body: { phone: "90 123 45 67" } });
    expect(field.value).toBe("+998901234567");
  });

  it("clears the phone when the field is emptied", async () => {
    const server = fakeServer((sent) => (sent.method === "GET" ? ok({ phone: "+998901234567" }) : ok({ phone: null })));
    await openContact(server, true);
    const field = await screen.findByLabelText<HTMLInputElement>("Do'kon telefoni");
    expect(field.value).toBe("+998901234567");
    fireEvent.change(field, { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    await screen.findByText("Saqlandi.");
    expect(server.writes()[0]?.body).toEqual({ phone: null });
  });

  it("says what a phone number looks like when the server refuses one", async () => {
    const server = fakeServer((sent) =>
      sent.method === "GET"
        ? ok({ phone: null })
        : refusal(422, "VALIDATION", "Ma'lumot noto'g'ri.", { phone: "not a phone number" }),
    );
    await openContact(server, true);
    fireEvent.change(await screen.findByLabelText("Do'kon telefoni"), { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Telefon raqami noto'g'ri. Namuna: +998 90 123 45 67");
    expect(screen.queryByText("Saqlandi.")).toBeNull();
  });

  it("shows a manager the phone and nothing to change", async () => {
    const server = fakeServer(() => ok({ phone: "+998901234567" }));
    await openContact(server, false);
    expect(await screen.findByText("+998901234567")).toBeTruthy();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByRole("textbox")).toBeNull();
    cleanup();

    await openContact(fakeServer(() => ok({ phone: null })), false);
    expect(await screen.findByText("Telefon ko'rsatilmagan.")).toBeTruthy();
  });
});

describe("no text is written in the components", () => {
  it.each(["ShareSection.tsx", "ShareContactSection.tsx"])("%s takes every word from the catalog", async (name) => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const source = readFileSync(resolve(import.meta.dirname, name), "utf8");
    expect(findHardcodedText(name, source)).toEqual([]);
  });
});
