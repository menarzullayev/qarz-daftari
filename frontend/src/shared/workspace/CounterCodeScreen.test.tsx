// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import jsQR from "jsqr";
import { afterEach, describe, expect, it, vi } from "vitest";

import { COUNTER_START, deferred, fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../../testing/fakeServer";
import { paintQr } from "../../testing/qr";
import { BOT, renderScreen } from "../../testing/renderScreen";
import type { Role } from "../navigation";
import { CounterCodeScreen } from "./CounterCodeScreen";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const LINK = `https://t.me/${BOT}?start=${COUNTER_START}`;
const ISSUED = { token: COUNTER_START.slice(2), start: COUNTER_START };
const EXISTS = { exists: true, since: "2026-09-01T05:00:00+00:00" };
const NONE = { exists: false, since: null };

function shop(state: unknown, onRotate: (sent: Sent, attempt: number) => Reply = () => ok(ISSUED, 201)) {
  let attempt = 0;
  return fakeServer((sent) => (sent.method === "GET" ? ok(state) : onRotate(sent, attempt++)));
}

async function open(server: ReturnType<typeof fakeServer>, role: Role, botUsername?: string | null) {
  renderScreen(<CounterCodeScreen />, { fetch: server.fetch, role, ...(botUsername === undefined ? {} : { botUsername }) });
  await waitFor(() => expect(screen.queryByText("Yuklanmoqda…")).toBeNull());
}

const button = (name: string) => screen.queryByRole("button", { name });

describe("counter code: what each role sees", () => {
  it("reads whether the shop has a code", async () => {
    const server = shop(EXISTS);
    await open(server, "manager");
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/counter-code` });
    expect(screen.getByText("Peshtaxta kodi bor: 2026-yil 1-sentabr, 10:00 da yaratilgan.")).toBeTruthy();
    expect(document.body.textContent).not.toContain("t.me");
  });

  it.each(["manager", "owner"] as Role[])("offers a %s to issue a first code or to replace the one there is", async (role) => {
    await open(shop(NONE), role);
    expect(screen.getByText("Peshtaxta kodi hali yaratilmagan.")).toBeTruthy();
    expect(button("Kod yaratish")).toBeTruthy();
    expect(button("Kodni almashtirish")).toBeNull();
    cleanup();

    await open(shop(EXISTS), role);
    expect(button("Kodni almashtirish")).toBeTruthy();
    expect(button("Kod yaratish")).toBeNull();
  });

  it.each([
    ["no code", NONE, "Peshtaxta kodi hali yaratilmagan."],
    ["a code", EXISTS, "Peshtaxta kodi bor: 2026-yil 1-sentabr, 10:00 da yaratilgan."],
  ])("shows a seller the state with %s and nothing to press", async (_what, state, text) => {
    const server = shop(state);
    await open(server, "seller");
    expect(screen.getByText(text)).toBeTruthy();
    expect(screen.getByText("Kodni menejer yoki do'kon egasi yaratadi va almashtiradi.")).toBeTruthy();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(server.writes()).toHaveLength(0);
  });

  it("shows the server's message when the state cannot be read", async () => {
    const server = fakeServer(() => refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan."));
    await open(server, "owner");
    expect(screen.getByRole("alert").textContent).toContain("Do'kon to'xtatilgan.");
    expect(button("Kod yaratish")).toBeNull();
  });
});

describe("issuing and replacing the counter code", () => {
  it("issues a first code at once and shows it as a link and a QR code that reads the same", async () => {
    const server = shop(NONE);
    await open(server, "manager");
    fireEvent.click(screen.getByRole("button", { name: "Kod yaratish" }));
    expect(await screen.findByText(LINK)).toBeTruthy();

    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/counter-code` });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    const svg = await screen.findByRole("img", { name: "Havolaning QR kodi" });
    const side = Number(svg.getAttribute("viewBox")?.split(" ")[2]);
    const image = paintQr({ side, path: svg.querySelector("path")?.getAttribute("d") ?? "" }, 4);
    expect(jsQR(image.data, image.width, image.height)?.data).toBe(LINK);
    expect(document.body.textContent).toContain("Bu faqat hozir, bir marta ko'rsatiladi.");
    expect(screen.getByText("Qarzingizni Telegramda ko'rish uchun shu kodni skanerlang.")).toBeTruthy();
    // There is a code now, so the next press replaces it and must be confirmed.
    expect(button("Kodni almashtirish")).toBeTruthy();
    expect(button("Kod yaratish")).toBeNull();
  });

  it("prints the code on request", async () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
    await open(shop(NONE), "owner");
    fireEvent.click(screen.getByRole("button", { name: "Kod yaratish" }));
    await screen.findByText(LINK);
    fireEvent.click(screen.getByRole("button", { name: "Chop etish" }));
    expect(print).toHaveBeenCalledTimes(1);
  });

  it("warns that the printed code stops working and sends nothing until the answer is yes", async () => {
    const server = shop(EXISTS);
    await open(server, "manager");
    fireEvent.click(screen.getByRole("button", { name: "Kodni almashtirish" }));
    expect(server.writes()).toHaveLength(0);
    expect(document.body.textContent).toContain("Yangi kod yaratilsa, chop etilgan eski kod ishlamay qoladi");

    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    expect(server.writes()).toHaveLength(0);
    expect(document.body.textContent).not.toContain("ishlamay qoladi");
    expect(button("Kodni almashtirish")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Kodni almashtirish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, almashtirilsin" }));
    expect(await screen.findByText(LINK)).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
  });

  it("asks again before replacing a code it has just issued", async () => {
    const server = shop(NONE);
    await open(server, "manager");
    fireEvent.click(screen.getByRole("button", { name: "Kod yaratish" }));
    await screen.findByText(LINK);
    fireEvent.click(screen.getByRole("button", { name: "Kodni almashtirish" }));
    expect(server.writes()).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Ha, almashtirilsin" })).toBeTruthy();
  });

  it("sends one request for a double tap", async () => {
    const held = deferred<Reply>();
    const server = shop(NONE, () => held.promise as Reply);
    await open(server, "manager");
    fireEvent.click(screen.getByRole("button", { name: "Kod yaratish" }));
    const pending = screen.getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement;
    expect(pending.disabled).toBe(true);
    fireEvent.click(pending);
    held.resolve(ok(ISSUED, 201));
    await screen.findByText(LINK);
    expect(server.writes()).toHaveLength(1);
  });

  it("shows the server's refusal and no code, then retries with the same key", async () => {
    const server = shop(NONE, (_sent, attempt) =>
      attempt === 0 ? refusal(403, "FORBIDDEN_ROLE", "Bu amal uchun sizning rolingiz yetarli emas.") : ok(ISSUED, 201),
    );
    await open(server, "manager");
    fireEvent.click(screen.getByRole("button", { name: "Kod yaratish" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Bu amal uchun sizning rolingiz yetarli emas.");
    expect(document.body.textContent).not.toContain("t.me");
    expect(screen.queryByRole("img")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Kod yaratish" }));
    await screen.findByText(LINK);
    const keys = server.writes().map((sent) => sent.headers["Idempotency-Key"]);
    expect(keys[0]).toBe(keys[1]);
  });

  it("shows the code and the /start command when the build names no bot, without QR or print", async () => {
    await open(shop(NONE), "manager", null);
    fireEvent.click(screen.getByRole("button", { name: "Kod yaratish" }));
    expect(await screen.findByText(`/start ${COUNTER_START}`)).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    expect(button("Chop etish")).toBeNull();
  });

  it("says so when a repeated answer no longer carries the code", async () => {
    await open(shop(NONE, () => ok({ token: null, start: null }, 201)), "manager");
    fireEvent.click(screen.getByRole("button", { name: "Kod yaratish" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Kod yaratildi, lekin uning matni bu ekranga yetib kelmadi. Kodni qaytadan almashtiring.",
    );
    expect(screen.queryByRole("img")).toBeNull();
  });
});
