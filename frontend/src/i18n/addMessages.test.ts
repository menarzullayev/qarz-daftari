import { describe, expect, it } from "vitest";

import { catalogs, translate } from "./catalog";
import { uzPanel } from "./panel/uz";
import type { MessageKey } from "./types";
import { uz } from "./uz";

// This file never imports the panel's `messages` module: it is an entry point that added no catalog,
// which is what the Telegram Mini App is.
describe("an entry point that did not add the panel's catalog", () => {
  it("has loaded the main catalog and nothing of the panel's", () => {
    expect(Object.keys(catalogs.uz)).toEqual(Object.keys(uz));
    expect(Object.keys(catalogs.ru)).toEqual(Object.keys(uz));
    expect(Object.keys(uzPanel).some((key) => key in catalogs.uz)).toBe(false);
  });

  it("fails loudly on a panel message instead of printing the key or nothing", () => {
    const key: MessageKey = "panel.signOut";
    expect(() => translate("uz", key)).toThrow('message "panel.signOut" is not loaded');
    expect(() => translate("ru", "deletion.pending", { date: "x" })).toThrow("is not loaded");
    expect(translate("uz", "nav.staff")).toBe("Xodimlar");
  });
});
