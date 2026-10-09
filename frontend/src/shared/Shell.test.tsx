// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AdminApp } from "../admin/AdminApp";
import { translate } from "../i18n/catalog";
import { LANGUAGE_STORAGE_KEY } from "../i18n/detect";
import type { Language } from "../i18n/types";
import { BRAND_NAME } from "./brand";
import type { Role } from "./navigation";
import { StaffApp } from "./StaffApp";

function go(hash: string) {
  act(() => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
}

function renderStaff(role: Role | null, language: Language = "uz", shopName = "Baraka savdo") {
  return render(
    <StaffApp entryKey="entry.app" session={role ? { role, shopName } : null} initialLanguage={language} />,
  );
}

const navLabels = () =>
  within(screen.getByRole("navigation"))
    .getAllByRole("link")
    .map((link) => link.textContent);

const heading = () => screen.getByRole("heading", { level: 1 }).textContent;

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
  document.documentElement.lang = "";
});

afterEach(cleanup);

describe("staff shell", () => {
  it("always names the active shop (REQ-064)", () => {
    renderStaff("seller", "uz", "Baraka savdo");
    const banner = screen.getByRole("banner");
    expect(within(banner).getByText("Baraka savdo")).toBeTruthy();
    expect(within(banner).getByText(translate("uz", "shell.activeShop"))).toBeTruthy();
    go("#/customers");
    expect(within(screen.getByRole("banner")).getByText("Baraka savdo")).toBeTruthy();
    go("#/no-such-page");
    expect(within(screen.getByRole("banner")).getByText("Baraka savdo")).toBeTruthy();
  });

  it("offers a seller only the seller sections", () => {
    renderStaff("seller");
    expect(navLabels()).toEqual(["Umumiy ko'rinish", "Mijozlar", "Yangi yozuv", "Katalog"]);
    expect(screen.queryByText("Do'kon sozlamalari")).toBeNull();
    expect(screen.queryByText("Hisobotlar")).toBeNull();
    expect(screen.queryByText("Yana")).toBeNull();
  });

  it("offers an owner every section plus the phone's More tab", () => {
    renderStaff("owner", "ru");
    expect(navLabels()).toEqual([
      "Обзор",
      "Клиенты",
      "Новая запись",
      "Каталог",
      "Напоминания",
      "Отчёты",
      "Споры и запросы",
      "Импорт и экспорт",
      "Сотрудники",
      "Журнал действий",
      "Подписка",
      "Настройки магазина",
      "Ещё",
    ]);
  });

  it("has the landmarks, one h1, and marks the current section", () => {
    renderStaff("manager");
    go("#/reports");
    expect(screen.getByRole("banner")).toBeTruthy();
    expect(screen.getByRole("navigation").getAttribute("aria-label")).toBe("Asosiy menyu");
    expect(screen.getByRole("main")).toBeTruthy();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(heading()).toBe("Hisobotlar");
    const current = within(screen.getByRole("navigation"))
      .getAllByRole("link")
      .filter((link) => link.getAttribute("aria-current") === "page");
    expect(current.map((link) => link.textContent)).toEqual(["Hisobotlar"]);
    expect(document.title).toBe(`Hisobotlar — ${BRAND_NAME}`);
  });

  it("moves focus to the screen after a route change and from the skip button", () => {
    renderStaff("manager");
    go("#/catalog");
    expect(document.activeElement).toBe(screen.getByRole("main"));
    const skip = screen.getByRole("button", { name: "Asosiy qismga o'tish" });
    skip.focus();
    fireEvent.click(skip);
    expect(document.activeElement).toBe(screen.getByRole("main"));
  });

  it("lists the remaining sections on the More screen", () => {
    renderStaff("manager");
    go("#/more");
    expect(heading()).toBe("Yana");
    const links = within(screen.getByRole("main")).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "Katalog",
      "Eslatmalar",
      "Hisobotlar",
      "E'tirozlar va so'rovlar",
      "Import va eksport",
      "Do'kon sozlamalari",
    ]);
    expect(links[0]?.getAttribute("href")).toBe("#/catalog");
  });

  it("shows no navigation and no shop without a session", () => {
    renderStaff(null);
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(heading()).toBe("Kirish talab qilinadi");
    expect(within(screen.getByRole("banner")).getByText("Do'kon tanlanmagan")).toBeTruthy();
    go("#/reports");
    expect(heading()).toBe("Kirish talab qilinadi");
  });
});

describe("routing", () => {
  it("shows the not-found screen in the current language for an unknown route", () => {
    renderStaff("owner", "uz");
    go("#/no-such-page");
    expect(heading()).toBe("Sahifa topilmadi");
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Bosh sahifaga qaytish" }).getAttribute("href")).toBe("#/");
    cleanup();
    renderStaff("owner", "ru");
    expect(heading()).toBe("Страница не найдена");
  });

  it("does not open a section the role may not open, even by direct address", () => {
    renderStaff("seller");
    go("#/reports");
    expect(heading()).toBe("Sahifa topilmadi");
    go("#/shop-settings");
    expect(heading()).toBe("Sahifa topilmadi");
    go("#/more");
    expect(heading()).toBe("Sahifa topilmadi");
    cleanup();
    renderStaff("manager");
    go("#/staff");
    expect(heading()).toBe("Sahifa topilmadi");
    go("#/reports");
    expect(heading()).toBe("Hisobotlar");
  });

  it("opens the overview for Telegram's launch fragment", () => {
    renderStaff("seller");
    go("#tgWebAppData=query_id%3DAAE&tgWebAppVersion=8.0");
    expect(heading()).toBe("Umumiy ko'rinish");
  });
});

describe("language picker", () => {
  it("switches every text, the lang attribute, and persists the choice", () => {
    renderStaff("seller", "uz");
    expect(document.documentElement.lang).toBe("uz");
    const picker = screen.getByRole<HTMLSelectElement>("combobox", { name: "Til" });
    expect(picker.value).toBe("uz");
    expect(within(picker).getByRole("option", { name: "Русский" }).getAttribute("lang")).toBe("ru");
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBeNull();

    fireEvent.change(picker, { target: { value: "ru" } });

    expect(document.documentElement.lang).toBe("ru");
    expect(navLabels()).toEqual(["Обзор", "Клиенты", "Новая запись", "Каталог"]);
    expect(heading()).toBe("Обзор");
    expect(screen.getByRole<HTMLSelectElement>("combobox", { name: "Язык" }).value).toBe("ru");
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("ru");
    expect(screen.queryByText("Mijozlar")).toBeNull();
  });
});

describe("admin shell", () => {
  it("has its own navigation and no shop", () => {
    render(<AdminApp signedIn initialLanguage="uz" />);
    expect(navLabels()).toEqual(["Do'konlar", "To'lov cheklari", "Sozlamalar", "Yordam uchun kirish", "Audit jurnali"]);
    expect(screen.queryByText("Mijozlar")).toBeNull();
    expect(screen.queryByText(translate("uz", "shell.activeShop"))).toBeNull();
    expect(within(screen.getByRole("banner")).getByText("Platforma boshqaruvi")).toBeTruthy();
  });

  it("does not open staff routes", () => {
    render(<AdminApp signedIn initialLanguage="ru" />);
    go("#/customers");
    expect(heading()).toBe("Страница не найдена");
    go("#/audit");
    expect(heading()).toBe("Журнал аудита");
  });

  it("shows nothing but the sign-in notice when not signed in", () => {
    render(<AdminApp signedIn={false} initialLanguage="uz" />);
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(heading()).toBe("Kirish talab qilinadi");
  });
});
