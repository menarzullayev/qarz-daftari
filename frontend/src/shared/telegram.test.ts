import { describe, expect, it, vi } from "vitest";

import {
  getInitData,
  getTelegramLanguageCode,
  getWebApp,
  initTelegram,
  isInsideTelegram,
  telegramColorScheme,
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
  const attributes = new Map<string, string>();
  return {
    values,
    attributes,
    style: { setProperty: (name: string, value: string | null) => void values.set(name, value ?? "") },
    setAttribute: (name: string, value: string) => void attributes.set(name, value),
  };
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
    // Not marked as themed by Telegram either: the person's own theme choice stays in charge.
    expect(target.attributes.size).toBe(0);
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

describe("the tokens Telegram has no color for", () => {
  it("come from the dark set when Telegram says its theme is dark, and the page is marked as Telegram's", () => {
    const target = fakeTarget();
    initTelegram(fakeWebApp({ colorScheme: "dark" }), target);
    expect(target.attributes.get("data-theme")).toBe("dark");
    expect(target.attributes.get("data-telegram")).toBe("");
  });

  it("come from the light set for a light theme, whatever the device's own scheme", () => {
    const target = fakeTarget();
    initTelegram(fakeWebApp({ colorScheme: "light", themeParams: { bg_color: "#ffffff", text_color: "#000000" } }), target);
    expect(target.attributes.get("data-theme")).toBe("light");
  });

  it("follow the background color when an old client does not say which scheme it has", () => {
    expect(telegramColorScheme(fakeWebApp())).toBe("dark");
    expect(telegramColorScheme(fakeWebApp({ themeParams: { bg_color: "#ffffff", text_color: "#000000" } }))).toBe("light");
    expect(telegramColorScheme(fakeWebApp({ themeParams: { bg_color: "#f1f1f1" } }))).toBe("light");
    expect(telegramColorScheme(fakeWebApp({ themeParams: { bg_color: "#0e1621" } }))).toBe("dark");
  });

  it("trust what the client says over what the background looks like", () => {
    expect(telegramColorScheme(fakeWebApp({ colorScheme: "light" }))).toBe("light");
  });

  it("are left to the device when Telegram gives neither a scheme nor a usable color", () => {
    expect(telegramColorScheme(fakeWebApp({ themeParams: {} }))).toBeNull();
    expect(telegramColorScheme(fakeWebApp({ colorScheme: "sepia", themeParams: { bg_color: "red" } }))).toBeNull();
    expect(telegramColorScheme(null)).toBeNull();
    const target = fakeTarget();
    initTelegram(fakeWebApp({ themeParams: {} }), target);
    expect(target.attributes.has("data-theme")).toBe(false);
    expect(target.attributes.get("data-telegram")).toBe("");
  });

  it("change with Telegram's theme", () => {
    let onThemeChanged: (() => void) | undefined;
    const webApp = fakeWebApp({ colorScheme: "dark", onEvent: (_event, handler) => void (onThemeChanged = handler) });
    const target = fakeTarget();
    initTelegram(webApp, target);
    webApp.themeParams = { bg_color: "#ffffff", text_color: "#000000" };
    webApp.colorScheme = "light";
    onThemeChanged?.();
    expect(target.attributes.get("data-theme")).toBe("light");
  });

  it("do not need a target that can hold attributes", () => {
    const values = new Map<string, string>();
    const bare = { style: { setProperty: (name: string, value: string | null) => void values.set(name, value ?? "") } };
    expect(() => initTelegram(fakeWebApp({ colorScheme: "dark" }), bare)).not.toThrow();
    expect(values.get("--qd-bg")).toBe("#17212b");
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
