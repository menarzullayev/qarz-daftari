// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { fakeServer, type Reply, type Sent } from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import { AdminRoot } from "./AdminRoot";
import { type LoginReturn, NO_RETURN } from "../panel/loginReturn";
import type { LoginWidgetProps } from "../panel/TelegramLogin";
import { ADMIN, authBody, CSRF, LOGIN, NOW, ok, OTPAUTH, platformBody, refusal, shopBody, shopDetailBody } from "./testing";

/** Stands in for Telegram's widget: a button in its place. Loads nothing and leads nowhere. */
function StubWidget({ botUsername }: LoginWidgetProps) {
  return (
    <button type="button" data-bot={botUsername}>
      telegram
    </button>
  );
}

const RETURNED: LoginReturn = { status: "returned", data: LOGIN };

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
  window.sessionStorage.clear();
});
afterEach(cleanup);

const NOT_FOUND = () => refusal(404, "NOT_FOUND", "Topilmadi.");
const SIGN_IN = "/api/v1/auth/telegram-login";

type Options = {
  /** What GET /auth answers; a function is asked each time. */
  auth?: () => Reply;
  extra?: (sent: Sent) => Reply | null;
};

/**
 * The server as the admin entry meets it. Sign-in answers a CSRF token; every write without it is
 * refused as unauthenticated; behind the door the routes answer only while `held.elevated` is true.
 */
function backend({ auth, extra = () => null }: Options = {}) {
  const held = { status: authBody(), elevated: false, enrolments: 0 };
  const server = fakeServer((sent) => {
    const special = extra(sent);
    if (special !== null) {
      return special;
    }
    if (sent.path === SIGN_IN) {
      return ok({ csrf_token: CSRF, expires_at: "2026-10-21T07:00:00+00:00" });
    }
    if (sent.method !== "GET" && sent.headers["X-CSRF-Token"] !== CSRF) {
      return refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring.");
    }
    if (sent.path === "/api/v1/auth/sign-out") {
      return ok(null);
    }
    if (sent.path === `${ADMIN}/auth`) {
      return auth ? auth() : ok({ ...held.status, elevated: held.elevated, expires_at: held.elevated ? "2026-10-06T15:00:00+00:00" : null });
    }
    if (sent.path === `${ADMIN}/auth/enrolment`) {
      held.enrolments += 1;
      held.status = authBody({ enrolled: true, confirmed: false });
      return ok({ enrolled: true, otpauth_uri: OTPAUTH }, 201);
    }
    if (sent.path === `${ADMIN}/auth/session`) {
      if (sent.method === "DELETE") {
        held.elevated = false;
        return ok(null);
      }
      held.elevated = true;
      held.status = authBody({ enrolled: true, confirmed: true });
      return ok({ expires_at: "2026-10-06T15:00:00+00:00" }, 201);
    }
    if (!held.elevated) {
      return NOT_FOUND();
    }
    if (sent.path === `${ADMIN}/shops`) {
      return ok({ items: [shopBody()], next_cursor: null });
    }
    if (sent.path.startsWith(`${ADMIN}/shops/`)) {
      return ok(shopDetailBody());
    }
    if (sent.path === `${ADMIN}/settings`) {
      return ok(platformBody());
    }
    return ok({ items: [], next_cursor: null });
  });
  return { ...server, held };
}

let loaded: { server: ReturnType<typeof backend>; botUsername: string | null } | null = null;

function start(server: ReturnType<typeof backend>, botUsername: string | null = "qarz_daftari_bot", loginReturn: LoginReturn = NO_RETURN) {
  loaded = { server, botUsername };
  return render(
    <AdminRoot initialLanguage="uz" fetch={server.fetch} botUsername={botUsername} LoginWidget={StubWidget} loginReturn={loginReturn} now={() => NOW} />,
  );
}

const heading = () => screen.getByRole("heading", { level: 1 }).textContent;
/**
 * The person presses the button and confirms in Telegram, and Telegram sends the browser back: the page
 * is loaded again, this time with the signed fields taken from its address.
 */
function signIn() {
  if (loaded === null) {
    throw new Error("the page was never opened");
  }
  cleanup();
  return start(loaded.server, loaded.botUsername, RETURNED);
}
const paths = (server: ReturnType<typeof backend>) => server.sent.map((sent) => `${sent.method} ${sent.path}`);
const typeCode = (code: string) => {
  fireEvent.change(screen.getByLabelText("6 xonali kod"), { target: { value: code } });
  fireEvent.click(screen.getByRole("button", { name: "Tasdiqlash" }));
};
/** Words that would tell a stranger what this address is. */
const ADMIN_WORDS = /admin|platforma|boshqaruv|ikkinchi omil|autentifikator|do'konlar|sozlamalar|audit/i;

describe("before the server has said who this is", () => {
  it("opens with a Telegram sign-in that names no administration, no navigation, and asks the server nothing", async () => {
    const server = backend();
    const view = start(server);
    expect(heading()).toBe("Kirish");
    expect(screen.getByRole("button", { name: "telegram" })).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(view.container.textContent).not.toMatch(ADMIN_WORDS);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent).toHaveLength(0);
  });

  it("explains plainly when the build names no bot", () => {
    start(backend(), null);
    expect(screen.getByText(/Telegram boti ko'rsatilmagan/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "telegram" })).toBeNull();
  });

  it("posts the widget's data once and then asks where the person stands, with the cookie alone", async () => {
    const server = backend();
    start(server);
    signIn();
    await screen.findByLabelText("6 xonali kod");
    expect(paths(server)).toEqual([`POST ${SIGN_IN}`, `GET ${ADMIN}/auth`]);
    expect(server.sent[0]?.body).toEqual(LOGIN);
    expect(server.sent[1]?.headers["X-CSRF-Token"]).toBeUndefined();
    expect(server.sent[1]?.headers["Authorization"]).toBeUndefined();
  });

  it("says to press again when Telegram's data is refused", async () => {
    const server = backend({ extra: (sent) => (sent.path === SIGN_IN ? refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring.") : null) });
    start(server);
    signIn();
    expect((await screen.findByRole("alert")).textContent).toContain("Kirish tugmasini qayta bosing.");
    expect(heading()).toBe("Kirish");
  });
});

describe("someone who is not an administrator", () => {
  it("sees the screen of an address that does not exist, with nothing that hints at an admin panel", async () => {
    const server = backend({ auth: NOT_FOUND });
    const view = start(server);
    signIn();
    expect(await screen.findByRole("heading", { level: 1, name: "Sahifa topilmadi" })).toBeTruthy();
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(view.container.textContent).not.toMatch(ADMIN_WORDS);
    // The title is set in an effect, after the heading is drawn: it is waited for, not read at once.
    await waitFor(() => expect(document.title).toBe("Sahifa topilmadi — Qarz Daftari"));
    expect(screen.queryByLabelText("6 xonali kod")).toBeNull();
    expect(screen.queryByRole("button", { name: "Maxfiy kalit yaratish" })).toBeNull();
  });

  it("sees the same at every address, and nothing more is asked of the server", async () => {
    const server = backend({ auth: NOT_FOUND });
    start(server);
    signIn();
    await screen.findByRole("heading", { level: 1, name: "Sahifa topilmadi" });
    const here = document.querySelector("main")?.innerHTML;
    for (const hash of ["#/settings", "#/audit", "#/shops/5a0c6d3e-0000-4000-8000-00000000aaaa", "#/nothing"]) {
      go(hash);
      expect(document.querySelector("main")?.innerHTML).toBe(here);
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(paths(server)).toEqual([`POST ${SIGN_IN}`, `GET ${ADMIN}/auth`]);
  });

  it("sees exactly what an administrator sees at an address that does not exist", async () => {
    const stranger = backend({ auth: NOT_FOUND });
    const first = start(stranger);
    signIn();
    await screen.findByRole("heading", { level: 1, name: "Sahifa topilmadi" });
    const forStranger = document.querySelector("main")?.innerHTML;
    first.unmount();

    const admin = backend();
    admin.held.elevated = true;
    window.location.hash = "#/no-such-page";
    start(admin);
    signIn();
    await screen.findByRole("navigation");
    // The administrator's page has one thing more, the control that closes their session; the rest is the same.
    const main = document.querySelector("main")?.cloneNode(true) as HTMLElement;
    main.querySelector("footer")?.remove();
    expect(main.innerHTML).toBe(forStranger);
  });
});

describe("failures at the door", () => {
  it("returns to sign-in when the session is not accepted", async () => {
    const server = backend({ auth: () => refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring.") });
    start(server);
    signIn();
    expect(await screen.findByText("Sessiya tugadi. Qayta kiring.")).toBeTruthy();
    expect(heading()).toBe("Kirish");
  });

  it("offers a retry for any other failure, without saying what lies behind", async () => {
    let fail = true;
    const server = backend({ auth: () => (fail ? "offline" : ok(authBody())) });
    const view = start(server);
    signIn();
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    expect(view.container.textContent).not.toMatch(ADMIN_WORDS);
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByLabelText("6 xonali kod")).toBeTruthy();
  });
});

describe("enrolling the second factor", () => {
  const fresh = () => {
    const server = backend();
    server.held.status = authBody({ enrolled: false, confirmed: false });
    return server;
  };

  it("creates the secret on request, with a key and the CSRF token, and shows it once as text and as a QR code", async () => {
    const server = fresh();
    start(server);
    signIn();
    const create = await screen.findByRole("button", { name: "Maxfiy kalit yaratish" });
    expect(screen.queryByLabelText("6 xonali kod")).toBeNull();
    expect(server.writes()).toHaveLength(1);

    fireEvent.click(create);
    expect(await screen.findByText(OTPAUTH)).toBeTruthy();
    const sent = server.writes()[1];
    expect(sent).toMatchObject({ method: "POST", path: `${ADMIN}/auth/enrolment` });
    expect(sent?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(sent?.headers["X-CSRF-Token"]).toBe(CSRF);
    expect(screen.getByText(/faqat hozir, bir marta ko'rsatiladi va qayta ko'rsatilmaydi/)).toBeTruthy();
    expect((await screen.findByRole("img", { name: "Havolaning QR kodi" })).tagName).toBe("svg");
    // The code that confirms it is asked for on the same screen.
    expect(screen.getByLabelText("6 xonali kod")).toBeTruthy();
  });

  it("keeps the secret out of the address and out of storage, and forgets it when the screen is left", async () => {
    const server = fresh();
    const view = start(server);
    signIn();
    fireEvent.click(await screen.findByRole("button", { name: "Maxfiy kalit yaratish" }));
    await screen.findByText(OTPAUTH);
    expect(window.location.href).not.toContain("ORSXG5BAMZXXEIDUMVZXI4Y");
    expect(JSON.stringify({ ...window.localStorage, ...window.sessionStorage })).not.toContain("ORSXG5BAMZXXEIDUMVZXI4Y");
    fireEvent.click(screen.getByRole("button", { name: "Chiqish" }));
    expect(heading()).toBe("Kirish");
    expect(view.container.innerHTML).not.toContain("ORSXG5BAMZXXEIDUMVZXI4Y");
  });

  it("opens the panel once a first code is accepted", async () => {
    const server = fresh();
    start(server);
    signIn();
    fireEvent.click(await screen.findByRole("button", { name: "Maxfiy kalit yaratish" }));
    await screen.findByText(OTPAUTH);
    typeCode("123456");
    expect(await screen.findByRole("table", { name: "Do'konlar" })).toBeTruthy();
    expect(server.writes().at(-1)).toMatchObject({ method: "POST", path: `${ADMIN}/auth/session`, body: { code: "123456" } });
  });

  it("says so, and creates another with a new key, when the answer no longer carries the secret", async () => {
    let first = true;
    const server = backend({
      extra: (sent) => {
        if (sent.path !== `${ADMIN}/auth/enrolment` || !first) {
          return null;
        }
        first = false;
        return ok({ enrolled: true, otpauth_uri: null }, 201);
      },
    });
    server.held.status = authBody({ enrolled: false, confirmed: false });
    start(server);
    signIn();
    fireEvent.click(await screen.findByRole("button", { name: "Maxfiy kalit yaratish" }));
    expect((await screen.findByRole("alert")).textContent).toContain("matni bu ekranga yetib kelmadi");
    expect(screen.queryByRole("img")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Maxfiy kalit yaratish" }));
    await screen.findByText(OTPAUTH);
    const keys = server.sent.filter((sent) => sent.path === `${ADMIN}/auth/enrolment`).map((sent) => sent.headers["Idempotency-Key"]);
    expect(keys).toHaveLength(2);
    expect(keys[1]).not.toBe(keys[0]);
  });

  it("says a secret created earlier and never confirmed will be replaced", async () => {
    const server = backend();
    server.held.status = authBody({ enrolled: true, confirmed: false });
    start(server);
    signIn();
    expect(await screen.findByText(/Yangi kalit yaratilsa, avvalgisi ishlamay qoladi/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Maxfiy kalit yaratish" })).toBeTruthy();
  });

  it("goes on to the code when the server says the factor is already confirmed", async () => {
    const message = "Ikkinchi omil allaqachon ulangan. Almashtirish uchun operatorga murojaat qiling.";
    let status = authBody({ enrolled: true, confirmed: false });
    const server = backend({
      auth: () => ok(status),
      extra: (sent) => {
        if (sent.path !== `${ADMIN}/auth/enrolment`) {
          return null;
        }
        status = authBody();
        return refusal(409, "ADMIN_ALREADY_ENROLLED", message);
      },
    });
    start(server);
    signIn();
    fireEvent.click(await screen.findByRole("button", { name: "Maxfiy kalit yaratish" }));
    expect(await screen.findByText("Autentifikator ilovangizdagi joriy kodni kiriting.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Maxfiy kalit yaratish" })).toBeNull();
  });
});

describe("entering the code", () => {
  it.each(["", "12345", "1234567", "12a456", "12 345"])("sends nothing for %j", async (code) => {
    const server = backend();
    start(server);
    signIn();
    await screen.findByLabelText("6 xonali kod");
    typeCode(code);
    expect(screen.getByText("Kod aynan 6 ta raqamdan iborat.").id).toBe("door-code-error");
    expect(server.writes()).toHaveLength(1);
  });

  it("sends the code once with the CSRF token and no key, empties the field, and opens the panel", async () => {
    const server = backend();
    start(server);
    signIn();
    await screen.findByLabelText("6 xonali kod");
    typeCode("654321");
    expect((screen.getByLabelText("6 xonali kod") as HTMLInputElement).value).toBe("");
    expect(await screen.findByRole("table", { name: "Do'konlar" })).toBeTruthy();
    const sent = server.writes()[1];
    expect(sent).toMatchObject({ method: "POST", path: `${ADMIN}/auth/session`, body: { code: "654321" } });
    expect(sent?.headers["X-CSRF-Token"]).toBe(CSRF);
    expect(sent?.headers["Idempotency-Key"]).toBeUndefined();
    expect(JSON.stringify({ ...window.localStorage, ...window.sessionStorage })).not.toContain("654321");
    expect(window.location.href).not.toContain("654321");
  });

  it("shows the server's refusal of a wrong code and lets another be typed", async () => {
    const message = "Kod noto'g'ri, eskirgan yoki allaqachon ishlatilgan. Yangi kodni kiriting.";
    let wrong = true;
    const server = backend({
      extra: (sent) => (wrong && sent.path === `${ADMIN}/auth/session` ? refusal(403, "SECOND_FACTOR_INVALID", message) : null),
    });
    start(server);
    signIn();
    await screen.findByLabelText("6 xonali kod");
    typeCode("111111");
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    expect(screen.queryByRole("navigation")).toBeNull();
    wrong = false;
    typeCode("222222");
    expect(await screen.findByRole("table", { name: "Do'konlar" })).toBeTruthy();
  });

  it("shows the lock with when it ends when too many codes were wrong, and takes no more codes", async () => {
    const server = backend({
      extra: (sent) =>
        sent.path === `${ADMIN}/auth/session`
          ? refusal(429, "SECOND_FACTOR_LOCKED", "Juda ko'p noto'g'ri kod kiritildi.", { retry_after_seconds: "600" })
          : null,
    });
    start(server);
    signIn();
    await screen.findByLabelText("6 xonali kod");
    typeCode("111111");
    // Ten minutes after noon in Tashkent.
    expect((await screen.findByRole("alert")).textContent).toBe("Juda ko'p noto'g'ri kod kiritildi. 2026-yil 6-oktabr, 12:10 gacha kod qabul qilinmaydi.");
    expect(screen.queryByLabelText("6 xonali kod")).toBeNull();
  });

  it("shows a lock the status reports, and reads the status again on request", async () => {
    let status = authBody({ locked_until: "2026-10-06T07:15:00+00:00" });
    const server = backend({ auth: () => ok(status) });
    start(server);
    signIn();
    expect((await screen.findByRole("alert")).textContent).toContain("2026-yil 6-oktabr, 12:15 gacha kod qabul qilinmaydi.");
    expect(screen.queryByLabelText("6 xonali kod")).toBeNull();
    status = authBody();
    fireEvent.click(screen.getByRole("button", { name: "Qayta tekshirish" }));
    expect(await screen.findByLabelText("6 xonali kod")).toBeTruthy();
  });

  it("takes codes again once a reported lock is in the past", async () => {
    const server = backend({ auth: () => ok(authBody({ locked_until: "2026-10-06T06:59:59+00:00" })) });
    start(server);
    signIn();
    expect(await screen.findByLabelText("6 xonali kod")).toBeTruthy();
  });
});

describe("behind the door", () => {
  const inside = async (server = backend()) => {
    server.held.elevated = true;
    start(server);
    signIn();
    await screen.findByRole("table", { name: "Do'konlar" });
    return server;
  };

  it("goes straight in when an admin session is still open: no code is asked for again", async () => {
    const server = await inside();
    expect(paths(server)).toEqual([`POST ${SIGN_IN}`, `GET ${ADMIN}/auth`, `GET ${ADMIN}/shops`]);
    expect(within(screen.getByRole("banner")).getByText("Platforma boshqaruvi")).toBeTruthy();
    expect(within(screen.getByRole("navigation")).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Do'konlar",
      "To'lov cheklari",
      "Sozlamalar",
      "Yordam uchun kirish",
      "Audit jurnali",
    ]);
    expect(screen.getByText("Admin sessiyasi 2026-yil 6-oktabr, 20:00 gacha amal qiladi.")).toBeTruthy();
  });

  it("opens the queue of receipts at its address, and one receipt under it: neither is a placeholder any more", async () => {
    const server = await inside();
    const before = server.sent.length;
    go("#/receipts");
    expect(heading()).toBe("To'lov cheklari");
    expect(screen.queryByText("Bu bo'lim tez orada tayyor bo'ladi.")).toBeNull();
    await waitFor(() => expect(server.sent.slice(before).map((sent) => `${sent.method} ${sent.path}`)).toEqual(["GET /api/admin/v1/receipts"]));
    go("#/receipts/55555555-5555-4555-8555-555555555551");
    expect(heading()).toBe("Obuna to'lovi cheki");
    await waitFor(() => expect(server.sent.at(-1)?.path).toBe("/api/admin/v1/receipts/55555555-5555-4555-8555-555555555551"));
    expect(within(screen.getByRole("navigation")).getByRole("link", { name: "To'lov cheklari" }).getAttribute("aria-current")).toBe("page");
    go("#/receipts/not-a-receipt");
    expect(heading()).toBe("Sahifa topilmadi");
  });

  it("opens no staff address and no malformed one", async () => {
    const server = await inside();
    const before = server.sent.length;
    for (const hash of ["#/customers", "#/shops", "#/shops/42", "#/shops/5a0c6d3e-0000-4000-8000-00000000aaaa/x", "#/audit/42", "#/settings/x"]) {
      go(hash);
      expect(heading()).toBe("Sahifa topilmadi");
    }
    expect(server.sent).toHaveLength(before);
  });

  it("closes the admin session on request, with the CSRF token, and asks for a code again", async () => {
    const server = await inside();
    fireEvent.click(screen.getByRole("button", { name: "Admin sessiyasini yopish" }));
    expect(await screen.findByLabelText("6 xonali kod")).toBeTruthy();
    const closed = server.writes().at(-1);
    expect(closed).toMatchObject({ method: "DELETE", path: `${ADMIN}/auth/session` });
    expect(closed?.headers["X-CSRF-Token"]).toBe(CSRF);
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("stays inside and says why when the session cannot be closed", async () => {
    const server = await inside(backend({ extra: (sent) => (sent.method === "DELETE" ? "offline" : null) }));
    fireEvent.click(screen.getByRole("button", { name: "Admin sessiyasini yopish" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    expect(screen.getByRole("navigation")).toBeTruthy();
    expect(server.held.elevated).toBe(true);
  });

  it("asks for a code again when the admin session has ended on the server", async () => {
    const server = await inside();
    server.held.elevated = false;
    go("#/settings");
    expect(await screen.findByLabelText("6 xonali kod")).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("shows the screen of no address when the person is no longer an administrator", async () => {
    let gone = false;
    const server = await inside(backend({ extra: (sent) => (gone && sent.path.startsWith(ADMIN) ? NOT_FOUND() : null) }));
    gone = true;
    go("#/audit");
    expect(await screen.findByRole("heading", { level: 1, name: "Sahifa topilmadi" })).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("navigation")).toBeNull());
    expect(server.sent.at(-1)?.path).toBe(`${ADMIN}/auth`);
  });

  it("returns to sign-in when the web session ends", async () => {
    let expired = false;
    await inside(backend({ extra: () => (expired ? refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring.") : null) }));
    expired = true;
    go("#/settings");
    expect(await screen.findByRole("heading", { level: 1, name: "Kirish" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Sessiya tugadi. Qayta kiring.");
  });

  it("stays on a shop that does not exist with the not-found screen, inside the panel", async () => {
    await inside(backend({ extra: (sent) => (sent.path.startsWith(`${ADMIN}/shops/`) ? NOT_FOUND() : null) }));
    go("#/shops/5a0c6d3e-0000-4000-8000-00000000cccc");
    expect(await screen.findByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 30));
    // The status was asked and still says the session is open: the panel stays.
    expect(screen.getByRole("navigation")).toBeTruthy();
  });
});
