import { describe, expect, it } from "vitest";

import { applyTheme, initTheme, normalizeTheme, readStoredTheme, THEME_STORAGE_KEY, writeStoredTheme } from "./theme";

function fakeStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    values,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
    removeItem: (key: string) => void values.delete(key),
  };
}

const blocked = {
  getItem: () => {
    throw new Error("storage is blocked");
  },
  setItem: () => {
    throw new Error("storage is blocked");
  },
  removeItem: () => {
    throw new Error("storage is blocked");
  },
};

function fakeRoot() {
  const attributes = new Map<string, string>();
  return {
    attributes,
    setAttribute: (name: string, value: string) => void attributes.set(name, value),
    removeAttribute: (name: string) => void attributes.delete(name),
  };
}

describe("the stored theme", () => {
  it("is System until a person chooses", () => {
    expect(readStoredTheme(fakeStorage())).toBe("system");
    expect(readStoredTheme(null)).toBe("system");
  });

  it("reads back an explicit choice", () => {
    expect(readStoredTheme(fakeStorage({ [THEME_STORAGE_KEY]: "dark" }))).toBe("dark");
    expect(readStoredTheme(fakeStorage({ [THEME_STORAGE_KEY]: "light" }))).toBe("light");
  });

  it("ignores anything that is not a theme", () => {
    expect(readStoredTheme(fakeStorage({ [THEME_STORAGE_KEY]: "sepia" }))).toBe("system");
    expect(readStoredTheme(fakeStorage({ [THEME_STORAGE_KEY]: '"><script>' }))).toBe("system");
    expect(normalizeTheme("Dark")).toBeNull();
    expect(normalizeTheme(null)).toBeNull();
    expect(normalizeTheme(1)).toBeNull();
  });

  it("stores an explicit choice and forgets it when System is chosen again", () => {
    const storage = fakeStorage();
    expect(writeStoredTheme("dark", storage)).toBe(true);
    expect(storage.values.get(THEME_STORAGE_KEY)).toBe("dark");
    expect(writeStoredTheme("system", storage)).toBe(true);
    // Nothing is left behind: a device that never chose and one that went back to System are the same.
    expect(storage.values.size).toBe(0);
  });

  it("survives a browser that refuses storage", () => {
    expect(readStoredTheme(blocked)).toBe("system");
    expect(writeStoredTheme("dark", blocked)).toBe(false);
    expect(writeStoredTheme("dark", null)).toBe(false);
    expect(() => initTheme(blocked, fakeRoot())).not.toThrow();
  });
});

describe("the theme on the page", () => {
  it("is the data-theme attribute for Light and Dark, and its absence for System", () => {
    const root = fakeRoot();
    applyTheme("dark", root);
    expect(root.attributes.get("data-theme")).toBe("dark");
    applyTheme("light", root);
    expect(root.attributes.get("data-theme")).toBe("light");
    applyTheme("system", root);
    expect(root.attributes.has("data-theme")).toBe(false);
  });

  it("applies the stored choice at start-up", () => {
    const root = fakeRoot();
    expect(initTheme(fakeStorage({ [THEME_STORAGE_KEY]: "dark" }), root)).toBe("dark");
    expect(root.attributes.get("data-theme")).toBe("dark");
  });

  it("leaves the page to the device when nothing is stored", () => {
    const root = fakeRoot();
    expect(initTheme(fakeStorage(), root)).toBe("system");
    expect(root.attributes.size).toBe(0);
  });

  it("does nothing where there is no page (this test run has no document)", () => {
    expect(typeof document).toBe("undefined");
    expect(() => applyTheme("dark")).not.toThrow();
    expect(initTheme(fakeStorage({ [THEME_STORAGE_KEY]: "dark" }))).toBe("dark");
  });
});
