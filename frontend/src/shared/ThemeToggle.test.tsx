// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AdminApp } from "../admin/AdminApp";
import type { Language } from "../i18n/types";
import { StaffApp } from "./StaffApp";
import { initTheme, THEME_STORAGE_KEY } from "./theme";

type TelegramWindow = { Telegram?: unknown };

function renderStaff(language: Language = "uz") {
  return render(<StaffApp entryKey="entry.app" session={{ role: "seller", shopName: "Baraka savdo" }} initialLanguage={language} />);
}

const root = () => document.documentElement;
const toggle = () => screen.getByRole("group", { name: "Mavzu" });
const option = (name: string) => within(toggle()).getByRole("button", { name });
const pressed = () =>
  within(toggle())
    .getAllByRole("button")
    .filter((button) => button.getAttribute("aria-pressed") === "true")
    .map((button) => button.getAttribute("aria-label"));

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
  window.sessionStorage.clear();
  root().removeAttribute("data-theme");
  delete (window as TelegramWindow).Telegram;
});

afterEach(cleanup);

describe("theme toggle", () => {
  it("sits in the header with three named options, System chosen and nothing stored by default", () => {
    renderStaff();
    expect(within(screen.getByRole("banner")).getByRole("group", { name: "Mavzu" })).toBe(toggle());
    expect(
      within(toggle())
        .getAllByRole("button")
        .map((button) => button.getAttribute("aria-label")),
    ).toEqual(["Yorug'", "Tungi", "Tizim"]);
    expect(pressed()).toEqual(["Tizim"]);
    // System is the absence of the attribute: the style sheet then follows the device.
    expect(root().hasAttribute("data-theme")).toBe(false);
    expect(window.localStorage.length).toBe(0);
  });

  it("gives every option an icon that a screen reader skips, and a name it reads", () => {
    renderStaff();
    for (const button of within(toggle()).getAllByRole("button")) {
      expect(button.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
      expect(button.getAttribute("aria-label")).toBe(button.textContent);
      expect(button.getAttribute("type")).toBe("button");
    }
  });

  it("sets the chosen theme on <html> and remembers it on this device", () => {
    renderStaff();
    fireEvent.click(option("Tungi"));
    expect(root().getAttribute("data-theme")).toBe("dark");
    expect(pressed()).toEqual(["Tungi"]);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    fireEvent.click(option("Yorug'"));
    expect(root().getAttribute("data-theme")).toBe("light");
    expect(pressed()).toEqual(["Yorug'"]);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });

  it("goes back to the device's scheme with System, leaving nothing stored", () => {
    renderStaff();
    fireEvent.click(option("Tungi"));
    fireEvent.click(option("Tizim"));
    expect(root().hasAttribute("data-theme")).toBe(false);
    expect(pressed()).toEqual(["Tizim"]);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
    expect(window.localStorage.length).toBe(0);
  });

  it("starts from the stored choice on the next visit", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    // What each entry point does before it renders.
    expect(initTheme()).toBe("dark");
    expect(root().getAttribute("data-theme")).toBe("dark");
    renderStaff();
    expect(pressed()).toEqual(["Tungi"]);
  });

  it("treats a stored value that is not a theme as no choice", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "sepia");
    expect(initTheme()).toBe("system");
    expect(root().hasAttribute("data-theme")).toBe(false);
    renderStaff();
    expect(pressed()).toEqual(["Tizim"]);
  });

  it("keeps the Mini App's session storage out of it", () => {
    renderStaff();
    fireEvent.click(option("Tungi"));
    expect(window.sessionStorage.length).toBe(0);
  });

  it("is named in Russian too, and keeps the choice when the language changes", () => {
    renderStaff("ru");
    const group = screen.getByRole("group", { name: "Тема" });
    fireEvent.click(within(group).getByRole("button", { name: "Тёмная" }));
    expect(root().getAttribute("data-theme")).toBe("dark");
    fireEvent.change(screen.getByRole("combobox", { name: "Язык" }), { target: { value: "uz" } });
    expect(pressed()).toEqual(["Tungi"]);
    expect(root().getAttribute("data-theme")).toBe("dark");
  });

  it("is in the admin panel's header as well", () => {
    render(<AdminApp signedIn initialLanguage="uz" />);
    fireEvent.click(option("Tungi"));
    expect(root().getAttribute("data-theme")).toBe("dark");
  });
});

describe("theme toggle inside Telegram", () => {
  function openInTelegram(initData: string) {
    (window as TelegramWindow).Telegram = {
      WebApp: { initData, initDataUnsafe: {}, themeParams: {}, ready: () => undefined, expand: () => undefined },
    };
  }

  it("is not shown: Telegram's theme decides the colors there", () => {
    openInTelegram("query_id=AAE&user=%7B%22id%22%3A1%7D&hash=abc");
    renderStaff();
    expect(screen.queryByRole("group", { name: "Mavzu" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Tungi" })).toBeNull();
    // The rest of the header is as it was.
    expect(screen.getByRole("combobox", { name: "Til" })).toBeTruthy();
    expect(within(screen.getByRole("banner")).getByText("Baraka savdo")).toBeTruthy();
  });

  it("is shown when Telegram's script is loaded in an ordinary browser, where it has no launch data", () => {
    openInTelegram("");
    renderStaff();
    expect(toggle()).toBeTruthy();
  });
});
