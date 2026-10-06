// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import jsQR from "jsqr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CUSTOMER_ID,
  deferred,
  fakeServer,
  linkBody,
  ok,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
  START,
} from "../../testing/fakeServer";
import { paintQr } from "../../testing/qr";
import { BOT, renderScreen } from "../../testing/renderScreen";
import { LinkSection } from "./LinkSection";

const LINK = `https://t.me/${BOT}?start=${START}`;
const ISSUED = { token: START.slice(2), start: START, expires_at: "2026-10-13T07:00:00+00:00" };
const LOGS = ["log", "info", "warn", "error", "debug"] as const;

beforeEach(() => {
  window.location.hash = "#/customers/x";
  window.localStorage.clear();
  window.sessionStorage.clear();
  for (const level of LOGS) {
    vi.spyOn(console, level).mockImplementation(() => undefined);
  }
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/** A customer who is not connected; `onCreate` answers the request for a link. */
function shop(onCreate: (sent: Sent, attempt: number) => Reply = () => ok(ISSUED, 201), state: () => Reply = () => ok(linkBody())) {
  let attempt = 0;
  return fakeServer((sent) => (sent.method === "GET" ? state() : onCreate(sent, attempt++)));
}

async function open(server: ReturnType<typeof fakeServer>, options: { archived?: boolean; botUsername?: string | null } = {}) {
  const view = renderScreen(<LinkSection customerId={CUSTOMER_ID} archived={options.archived ?? false} />, {
    fetch: server.fetch,
    ...(options.botUsername === undefined ? {} : { botUsername: options.botUsername }),
  });
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  return view;
}

const createButton = () => screen.getByRole("button", { name: "Shaxsiy havola yaratish" });
const section = () => screen.getByRole("region", { name: "Telegramga ulash" });

/** What a phone would read from the QR code on the screen. */
async function scanned(): Promise<string | null> {
  const svg = await screen.findByRole("img", { name: "Havolaning QR kodi" });
  const side = Number(svg.getAttribute("viewBox")?.split(" ")[2]);
  const image = paintQr({ side, path: svg.querySelector("path")?.getAttribute("d") ?? "" }, 4);
  return jsQR(image.data, image.width, image.height)?.data ?? null;
}

describe("link state", () => {
  it("says a customer is not connected and offers a personal link", async () => {
    const server = shop();
    renderScreen(<LinkSection customerId={CUSTOMER_ID} archived={false} />, { fetch: server.fetch });
    expect(screen.getByRole("status").textContent).toBe("Yuklanmoqda…");
    expect(await screen.findByText("Mijoz hali Telegramga ulanmagan.")).toBeTruthy();
    expect(server.sent).toHaveLength(1);
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}/link` });
    expect(createButton()).toBeTruthy();
  });

  it("says since when a customer is connected, in Tashkent time, and offers no link", async () => {
    const server = shop(undefined, () => ok(linkBody({ linked: true, status: "active", since: "2026-10-01T19:30:00+00:00" })));
    await open(server);
    expect(section().textContent).toContain("Mijoz Telegramga ulangan: 2026-yil 2-oktabr, 00:30.");
    expect(screen.queryByRole("button")).toBeNull();
    expect(section().textContent).not.toContain("yetib bormayapti");
  });

  it("warns when the customer has stopped the bot", async () => {
    const server = shop(undefined, () => ok(linkBody({ linked: true, status: "unreachable", since: "2026-10-01T05:00:00+00:00" })));
    await open(server);
    expect(screen.getByText("Mijoz botni to'xtatgan: xabarlar unga yetib bormayapti.")).toBeTruthy();
  });

  it("offers no link for an archived customer", async () => {
    await open(shop(), { archived: true });
    expect(screen.getByText("Arxivdagi mijoz uchun havola yaratilmaydi.")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows the server's message when the state cannot be read, and retries", async () => {
    let fail = true;
    const server = shop(undefined, () => (fail ? refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.") : ok(linkBody())));
    await open(server);
    expect(screen.getByRole("alert").textContent).toContain("Do'kon to'xtatilgan.");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Mijoz hali Telegramga ulanmagan.")).toBeTruthy();
  });
});

describe("creating a personal link", () => {
  it("posts one keyed request and shows the deep link as text and as a QR code that reads the same", async () => {
    const server = shop();
    await open(server);
    fireEvent.click(createButton());

    expect(await screen.findByText(LINK)).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}/link`, query: {} });
    expect(server.writes()[0]?.body).toBeUndefined();
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(await scanned()).toBe(LINK);
    expect(section().textContent).toContain("Havola 2026-yil 13-oktabr, 12:00 gacha amal qiladi.");
    // The link is issued; offering another one would cancel nothing and only confuse.
    expect(screen.queryByRole("button", { name: "Shaxsiy havola yaratish" })).toBeNull();
  });

  it("says on screen that the link is shown once", async () => {
    await open(shop());
    expect(section().textContent).not.toContain("bir marta");
    fireEvent.click(createButton());
    await screen.findByText(LINK);
    expect(section().textContent).toContain("Bu faqat hozir, bir marta ko'rsatiladi.");
  });

  it("shows the start code and the /start command when the build names no bot, and no QR code", async () => {
    await open(shop(), { botUsername: null });
    fireEvent.click(createButton());
    expect(await screen.findByText(`/start ${START}`)).toBeTruthy();
    expect(screen.getByText("Mijoz Telegramda do'kon botiga shu buyruqni yuborsin:")).toBeTruthy();
    expect(section().textContent).not.toContain("t.me");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("keeps the link out of the address, of storage and of the log", async () => {
    await open(shop());
    const hashBefore = window.location.hash;
    fireEvent.click(createButton());
    await screen.findByText(LINK);
    await scanned();

    expect(window.location.hash).toBe(hashBefore);
    expect(window.location.href).not.toContain(START.slice(2));
    const stored = [window.localStorage, window.sessionStorage].flatMap((store) =>
      Array.from({ length: store.length }, (_, index) => `${store.key(index)}=${store.getItem(store.key(index) ?? "")}`),
    );
    expect(stored.join("\n")).not.toContain(START.slice(2));
    for (const level of LOGS) {
      expect(console[level]).not.toHaveBeenCalled();
    }
  });

  it("forgets the link when the screen is left: it is shown once", async () => {
    const server = shop();
    const view = await open(server);
    fireEvent.click(createButton());
    await screen.findByText(LINK);

    view.unmount();
    await open(server);
    expect(screen.queryByText(LINK)).toBeNull();
    expect(document.body.textContent).not.toContain(START);
    expect(screen.queryByRole("img")).toBeNull();
    // Coming back reads the state again and creates nothing by itself.
    expect(server.writes()).toHaveLength(1);
  });

  it("hides the link on request", async () => {
    await open(shop());
    fireEvent.click(createButton());
    await screen.findByText(LINK);
    fireEvent.click(screen.getByRole("button", { name: "Havolani yashirish" }));
    expect(document.body.textContent).not.toContain(START);
    expect(createButton()).toBeTruthy();
  });

  it("sends one request for a double tap and disables the button while it is pending", async () => {
    const held = deferred<Reply>();
    const server = shop(() => held.promise as Reply);
    await open(server);
    fireEvent.click(createButton());
    fireEvent.click(screen.getByRole("button", { name: "Saqlanmoqda…" }));
    expect((screen.getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement).disabled).toBe(true);
    held.resolve(ok(ISSUED, 201));
    await screen.findByText(LINK);
    expect(server.writes()).toHaveLength(1);
  });

  it.each([
    [409, "CUSTOMER_ALREADY_LINKED", "Bu mijoz allaqachon Telegram hisobiga ulangan."],
    [409, "CUSTOMER_ARCHIVED", "Bu mijoz arxivda. Avval arxivdan chiqaring."],
    [403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan."],
  ])("shows the server's refusal %i %s and no link", async (status, code, message) => {
    await open(shop(() => refusal(status, code, message)));
    fireEvent.click(createButton());
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    expect(screen.queryByRole("img")).toBeNull();
    expect(section().textContent).not.toContain("t.me");
  });

  it("retries with the same key after a lost connection", async () => {
    const server = shop((_sent, attempt) => (attempt === 0 ? "offline" : ok(ISSUED, 201)));
    await open(server);
    fireEvent.click(createButton());
    expect((await screen.findByRole("alert")).textContent).toBe("Serverga ulanib bo'lmadi. Internetni tekshirib, qayta urinib ko'ring.");
    fireEvent.click(createButton());
    await screen.findByText(LINK);
    const keys = server.writes().map((sent) => sent.headers["Idempotency-Key"]);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });

  it("says so when a repeated answer no longer carries the code, and creates a new link with a new key", async () => {
    const server = shop((_sent, attempt) => (attempt === 0 ? ok({ token: null, start: null, expires_at: null }, 201) : ok(ISSUED, 201)));
    await open(server);
    fireEvent.click(createButton());
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Havola yaratildi, lekin uning matni bu ekranga yetib kelmadi. Yangisini yarating.",
    );
    expect(screen.queryByRole("img")).toBeNull();
    fireEvent.click(createButton());
    await screen.findByText(LINK);
    const keys = server.writes().map((sent) => sent.headers["Idempotency-Key"]);
    expect(keys[0]).not.toBe(keys[1]);
  });
});

describe("copying the link", () => {
  it("puts exactly the link on the clipboard and says so", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...window.navigator, clipboard: { writeText } });
    await open(shop());
    fireEvent.click(createButton());
    await screen.findByText(LINK);
    fireEvent.click(screen.getByRole("button", { name: "Nusxalash" }));
    expect(await screen.findByRole("button", { name: "Nusxalandi" })).toBeTruthy();
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith(LINK);
  });

  it("copies the command when there is no bot name", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...window.navigator, clipboard: { writeText } });
    await open(shop(), { botUsername: null });
    fireEvent.click(createButton());
    await screen.findByText(`/start ${START}`);
    fireEvent.click(screen.getByRole("button", { name: "Nusxalash" }));
    await screen.findByRole("button", { name: "Nusxalandi" });
    expect(writeText).toHaveBeenCalledWith(`/start ${START}`);
  });

  it("says so when the clipboard is not available, and the text stays on screen", async () => {
    await open(shop());
    fireEvent.click(createButton());
    await screen.findByText(LINK);
    fireEvent.click(screen.getByRole("button", { name: "Nusxalash" }));
    expect(await screen.findByRole("button", { name: "Nusxalab bo'lmadi: matnni qo'lda belgilang" })).toBeTruthy();
    expect(screen.getByText(LINK)).toBeTruthy();
  });
});
