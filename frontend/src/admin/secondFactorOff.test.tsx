// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { enAdmin } from "../i18n/admin/en";
import { kaaAdmin } from "../i18n/admin/kaa";
import { ruAdmin } from "../i18n/admin/ru";
import { tgAdmin } from "../i18n/admin/tg";
import { uzAdmin } from "../i18n/admin/uz";
import { loadLanguage } from "../i18n/catalog";
import { LANGUAGES } from "../i18n/types";
import type { LoginWidgetProps } from "../panel/TelegramLogin";
import { fakeServer, type Reply, type Sent } from "../testing/fakeServer";
import { AdminRoot } from "./AdminRoot";
import { SecondFactorOff } from "./secondFactor";
import { SettingsScreen } from "./SettingsScreen";
import { ShopScreen } from "./ShopScreen";
import { ADMIN, adminApi, authBody, CSRF, LOGIN, NOBODY, NOW, ok, platformBody, refusal, renderAdmin, SHOP_ID, shopBody, shopDetailBody } from "./testing";

/**
 * An installation whose server runs with `QD_ADMIN_SECOND_FACTOR=off`. The server says so (`second_factor`
 * in the door's status and in the settings); the panel asks for no code anywhere and says so on the
 * settings screen. Each case has its counterpart with the default beside it: the panel must not stop
 * asking on its own.
 */

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
  window.sessionStorage.clear();
});
afterEach(cleanup);

const NOTICE = "Bu o'rnatmada ikkinchi omil o'chirilgan: sozlamalar, jumladan to'lov kartalari va narx, kodsiz o'zgaradi.";
const CODE = "Autentifikator kodi";
const offSettings = (settings: Record<string, unknown> = {}) => platformBody({ needs_code: [], second_factor: "off" }, settings);

function StubWidget({ botUsername }: LoginWidgetProps) {
  return (
    <button type="button" data-bot={botUsername}>
      telegram
    </button>
  );
}

/** The whole entry over a server that answers the door with `status` and lets everybody behind it. */
function enter(status: Record<string, unknown>) {
  const server = fakeServer((sent) => {
    if (sent.path === "/api/v1/auth/telegram-login") {
      return ok({ csrf_token: CSRF, expires_at: "2026-10-21T07:00:00+00:00" });
    }
    if (sent.path === `${ADMIN}/auth`) {
      return ok(status);
    }
    if (sent.path === `${ADMIN}/shops`) {
      return ok({ items: [shopBody()], next_cursor: null });
    }
    return ok({ items: [], next_cursor: null });
  });
  render(
    <AdminRoot
      initialLanguage="uz"
      fetch={server.fetch}
      botUsername="qarz_daftari_bot"
      LoginWidget={StubWidget}
      loginReturn={{ status: "returned", data: LOGIN }}
      now={() => NOW}
    />,
  );
  return server;
}

describe("the door", () => {
  it("opens with no code and no enrolment when the server says the second factor is off, and offers no session to close", async () => {
    const server = enter(authBody({ elevated: true, second_factor: "off" }));
    await screen.findByRole("table", { name: "Do'konlar" });
    expect(server.sent.map((sent) => `${sent.method} ${sent.path}`)).toEqual([
      "POST /api/v1/auth/telegram-login",
      `GET ${ADMIN}/auth`,
      `GET ${ADMIN}/shops`,
    ]);
    expect(screen.queryByLabelText("6 xonali kod")).toBeNull();
    expect(screen.queryByRole("button", { name: "Admin sessiyasini yopish" })).toBeNull();
  });

  it("still asks for the code when the server does not say so", async () => {
    enter(authBody());
    expect(await screen.findByLabelText("6 xonali kod")).toBeTruthy();
    expect(screen.queryByRole("table", { name: "Do'konlar" })).toBeNull();
  });

  it("keeps the session's button where the second factor is required", async () => {
    enter(authBody({ elevated: true, expires_at: "2026-10-06T15:00:00+00:00" }));
    await screen.findByRole("table", { name: "Do'konlar" });
    expect(screen.getByRole("button", { name: "Admin sessiyasini yopish" })).toBeTruthy();
  });
});

describe("the settings screen", () => {
  const open = (loaded: unknown, onWrite: (sent: Sent) => Reply) => {
    const made = adminApi((sent) => (sent.method === "GET" ? ok(loaded) : onWrite(sent)));
    renderAdmin(<SettingsScreen api={made.api} />);
    return made.server;
  };
  const SMS = "SMS yoqilgan";
  const save = () => fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));

  it("says plainly that the second factor is off, keeps saying it after a change, and sends a sensitive change with no code", async () => {
    const server = open(offSettings(), (sent) => ok(offSettings((sent.body as { changes: Record<string, unknown> }).changes)));
    fireEvent.click(await screen.findByLabelText(SMS));
    expect(screen.getByText(NOTICE).id).toBe("settings-second-factor-off");
    expect(screen.queryByLabelText(CODE)).toBeNull();
    expect(document.getElementById("setting-price_uzs-hint")?.textContent).toBe("1000 dan 10000000 gacha butun son.");
    save();
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ changes: { sms_on: true } });
    await screen.findByRole("status");
    expect(screen.getByText(NOTICE)).toBeTruthy();
  });

  it("says nothing of the kind, and asks for the code, where the second factor is required", async () => {
    const server = open(platformBody(), () => refusal(500, "INTERNAL", "x"));
    fireEvent.click(await screen.findByLabelText(SMS));
    expect(screen.queryByText(NOTICE)).toBeNull();
    expect(screen.getByLabelText(CODE)).toBeTruthy();
    save();
    await waitFor(() => expect(document.getElementById("settings-code-error")?.textContent).toBe("Kod aynan 6 ta raqamdan iborat."));
    expect(server.writes()).toHaveLength(0);
  });

  it("has the notice in each of the six languages, in that language's own words", async () => {
    const written: Record<string, string | undefined> = {
      uz: uzAdmin["admin.settings.secondFactorOff"],
      ru: ruAdmin["admin.settings.secondFactorOff"],
      tg: tgAdmin["admin.settings.secondFactorOff"],
      kaa: kaaAdmin["admin.settings.secondFactorOff"],
      en: enAdmin["admin.settings.secondFactorOff"],
    };
    expect(written["uz"]).toBe(NOTICE);
    const shown: string[] = [];
    for (const language of LANGUAGES) {
      const made = adminApi(() => ok(offSettings()));
      await loadLanguage(language);
      renderAdmin(<SettingsScreen api={made.api} />, language);
      const notice = () => document.getElementById("settings-second-factor-off")?.textContent ?? "";
      if (language === "uz-Cyrl") {
        // Written from the Latin text by the transliteration: Cyrillic letters, and nothing of Russian.
        await waitFor(() => expect(notice()).toMatch(/^[Ѐ-ӿ]/));
        expect(notice()).not.toBe(written["ru"]);
      } else {
        await waitFor(() => expect(notice()).toBe(written[language]));
      }
      shown.push(notice());
      cleanup();
    }
    expect(LANGUAGES).toHaveLength(6);
    expect(new Set(shown).size).toBe(6);
  });
});

describe("replacing a shop's owner", () => {
  const NEW_OWNER = 987654321;
  const REASON = "Egasi hisobini yo'qotdi, pasport bilan tasdiqlandi";
  const open = (off: boolean) => {
    const made = adminApi((sent) =>
      sent.method === "GET" ? ok(sent.path.endsWith("/support-access") ? { items: [], next_cursor: null } : shopDetailBody()) : ok(shopBody({ owner_tg_id: NEW_OWNER })),
    );
    renderAdmin(
      <SecondFactorOff.Provider value={off}>
        <ShopScreen api={made.api} shopId={SHOP_ID} now={() => NOW} who={NOBODY} />
      </SecondFactorOff.Provider>,
    );
    return made.server;
  };
  const fill = async () => {
    fireEvent.click(await screen.findByRole("button", { name: "Egani almashtirish" }));
    const form = within(screen.getByRole("region", { name: "Do'kon egasi" }));
    fireEvent.change(form.getByLabelText("Yangi eganing Telegram ID raqami"), { target: { value: String(NEW_OWNER) } });
    fireEvent.change(form.getByLabelText("Sabab"), { target: { value: REASON } });
    return form;
  };

  it("asks for no code and sends none when the second factor is off", async () => {
    const server = open(true);
    const form = await fill();
    expect(form.queryByLabelText(CODE)).toBeNull();
    fireEvent.click(form.getByRole("button", { name: "Davom etish" }));
    fireEvent.click(await screen.findByRole("button", { name: "Ha, ega almashtirilsin" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ new_owner_tg_id: NEW_OWNER, reason: REASON });
  });

  it("does not go on without a code where the second factor is required", async () => {
    const server = open(false);
    const form = await fill();
    expect(form.getByLabelText(CODE)).toBeTruthy();
    fireEvent.click(form.getByRole("button", { name: "Davom etish" }));
    expect(document.getElementById("owner-code-error")?.textContent).toBe("Kod aynan 6 ta raqamdan iborat.");
    expect(screen.queryByRole("button", { name: "Ha, ega almashtirilsin" })).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });
});
