import { describe, expect, it, vi } from "vitest";

import {
  getInitData,
  getTelegramLanguageCode,
  getWebApp,
  initTelegram,
  isInsideTelegram,
  themeToCssVariables,
  type TelegramWebApp,
} from "./telegram";

function fakeWebApp(overrides: Partial<TelegramWebApp> = {}): TelegramWebApp {
  return {
    initData: "query_id=AAE&user=%7B%22id%22%3A1%7D&hash=abc",
    initDataUnsafe: { user: { language_code: "ru" } },
    themeParams: { bg_color: "#17212b", text_color: "#f5f5f5" },
    ready: vi.fn(),
    expand: vi.fn(),
    ...overrides,
  };
}

function fakeTarget() {
  const values = new Map<string, string>();
  return { values, style: { setProperty: (name: string, value: string | null) => void values.set(name, value ?? "") } };
}

describe("outside Telegram", () => {
  it("works in this test run, where there is no window at all", () => {
    expect(typeof window).toBe("undefined");
    expect(getWebApp()).toBeNull();
    expect(getInitData()).toBeNull();
    expect(isInsideTelegram()).toBe(false);
    expect(getTelegramLanguageCode()).toBeNull();
    expect(initTelegram()).toEqual({ insideTelegram: false, initData: null, languageCode: null });
  });

  it("returns null when the page has no Telegram object (web panel, admin panel)", () => {
    expect(getWebApp({})).toBeNull();
    expect(getWebApp({ Telegram: {} })).toBeNull();
    expect(getWebApp(null)).toBeNull();
  });

  it("rejects an object that is not the bridge", () => {
    expect(getWebApp({ Telegram: { WebApp: "nope" } })).toBeNull();
    expect(getWebApp({ Telegram: { WebApp: { initData: "x" } } })).toBeNull();
  });

  it("does not call the bridge when the script is loaded in an ordinary browser", () => {
    // Telegram's script defines the object everywhere; outside Telegram the launch data is empty.
    const webApp = fakeWebApp({ initData: "", initDataUnsafe: {}, themeParams: {} });
    const target = fakeTarget();
    expect(initTelegram(webApp, target)).toEqual({ insideTelegram: false, initData: null, languageCode: null });
    expect(webApp.ready).not.toHaveBeenCalled();
    expect(webApp.expand).not.toHaveBeenCalled();
    expect(target.values.size).toBe(0);
  });
});

describe("inside Telegram", () => {
  it("finds the bridge and reads launch data", () => {
    const webApp = fakeWebApp();
    expect(getWebApp({ Telegram: { WebApp: webApp } })).toBe(webApp);
    expect(getInitData(webApp)).toBe(webApp.initData);
    expect(isInsideTelegram(webApp)).toBe(true);
    expect(getTelegramLanguageCode(webApp)).toBe("ru");
  });

  it("signals ready, expands, and applies the theme", () => {
    const webApp = fakeWebApp();
    const target = fakeTarget();
    const launch = initTelegram(webApp, target);
    expect(launch).toEqual({ insideTelegram: true, initData: webApp.initData, languageCode: "ru" });
    expect(webApp.ready).toHaveBeenCalledOnce();
    expect(webApp.expand).toHaveBeenCalledOnce();
    expect(target.values.get("--qd-bg")).toBe("#17212b");
    expect(target.values.get("--qd-text")).toBe("#f5f5f5");
  });

  it("follows a theme change", () => {
    let onThemeChanged: (() => void) | undefined;
    const webApp = fakeWebApp({ onEvent: (_event, handler) => void (onThemeChanged = handler) });
    const target = fakeTarget();
    initTelegram(webApp, target);
    webApp.themeParams = { bg_color: "#ffffff", text_color: "#000000" };
    onThemeChanged?.();
    expect(target.values.get("--qd-bg")).toBe("#ffffff");
  });

  it("still opens when the Telegram client throws", () => {
    const webApp = fakeWebApp({
      ready: () => {
        throw new Error("unsupported");
      },
    });
    expect(() => initTelegram(webApp, fakeTarget())).not.toThrow();
    expect(initTelegram(webApp, fakeTarget()).insideTelegram).toBe(true);
  });

  it("tolerates launch data without a user or a language", () => {
    expect(getTelegramLanguageCode(fakeWebApp({ initDataUnsafe: {} }))).toBeNull();
    expect(getTelegramLanguageCode(fakeWebApp({ initDataUnsafe: { user: {} } }))).toBeNull();
  });
});

describe("themeToCssVariables", () => {
  it("maps a full theme", () => {
    expect(
      themeToCssVariables({
        bg_color: "#17212b",
        secondary_bg_color: "#232e3c",
        text_color: "#f5f5f5",
        hint_color: "#708499",
        link_color: "#6ab3f3",
        button_color: "#5288c1",
        button_text_color: "#ffffff",
      }),
    ).toEqual({
      "--qd-bg": "#17212b",
      "--qd-surface": "#232e3c",
      "--qd-text": "#f5f5f5",
      "--qd-muted": "#708499",
      "--qd-link": "#6ab3f3",
      "--qd-border": "#708499",
      "--qd-focus": "#6ab3f3",
      "--qd-accent": "#5288c1",
      "--qd-on-accent": "#ffffff",
    });
  });

  it("applies nothing without both a background and a text color", () => {
    expect(themeToCssVariables({ bg_color: "#17212b" })).toEqual({});
    expect(themeToCssVariables({ text_color: "#f5f5f5", button_color: "#5288c1" })).toEqual({});
    expect(themeToCssVariables({})).toEqual({});
    expect(themeToCssVariables(undefined)).toEqual({});
  });

  it("falls back to the text and background pair when a color is missing", () => {
    const variables = themeToCssVariables({ bg_color: "#17212b", text_color: "#f5f5f5", button_color: "#5288c1" });
    expect(variables["--qd-accent"]).toBe("#f5f5f5");
    expect(variables["--qd-on-accent"]).toBe("#17212b");
    expect(variables["--qd-muted"]).toBe("#f5f5f5");
  });

  it("ignores values that are not six-digit hex colors", () => {
    expect(themeToCssVariables({ bg_color: "red; background: url(x)", text_color: "#f5f5f5" })).toEqual({});
    expect(themeToCssVariables({ bg_color: "#fff", text_color: "#000" })).toEqual({});
    const variables = themeToCssVariables({ bg_color: "#17212b", text_color: "#f5f5f5", link_color: "javascript:1" });
    expect(variables["--qd-link"]).toBe("#f5f5f5");
  });
});
