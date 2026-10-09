/**
 * The color theme: Light, Dark, or System (follow the device). The choice is `data-theme` on `<html>`;
 * "System" is the absence of the attribute, which the style sheet answers with `prefers-color-scheme`,
 * so the default needs no script at all. Only an explicit choice is stored, per device.
 */
export const THEMES = ["light", "dark", "system"] as const;
export type Theme = (typeof THEMES)[number];

export const DEFAULT_THEME: Theme = "system";
export const THEME_STORAGE_KEY = "qd.theme";
export const THEME_ATTRIBUTE = "data-theme";

export function normalizeTheme(value: unknown): Theme | null {
  return THEMES.find((theme) => theme === value) ?? null;
}

type ThemeStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function browserStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    // Access itself can throw when storage is blocked (private mode, some in-app browsers).
    return null;
  }
}

export function readStoredTheme(storage: Pick<ThemeStorage, "getItem"> | null = browserStorage()): Theme {
  try {
    return normalizeTheme(storage?.getItem(THEME_STORAGE_KEY)) ?? DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

/**
 * Remembers an explicit choice. "System" is the default, so choosing it forgets the stored one instead
 * of writing it. Returns false when storage refused; the choice still applies to this page.
 */
export function writeStoredTheme(theme: Theme, storage: ThemeStorage | null = browserStorage()): boolean {
  if (!storage) {
    return false;
  }
  try {
    if (theme === DEFAULT_THEME) {
      storage.removeItem(THEME_STORAGE_KEY);
    } else {
      storage.setItem(THEME_STORAGE_KEY, theme);
    }
    return true;
  } catch {
    return false;
  }
}

type ThemeTarget = Pick<Element, "setAttribute" | "removeAttribute">;

function documentRoot(): ThemeTarget | null {
  return typeof document === "undefined" ? null : document.documentElement;
}

export function applyTheme(theme: Theme, target: ThemeTarget | null = documentRoot()): void {
  if (theme === "system") {
    target?.removeAttribute(THEME_ATTRIBUTE);
  } else {
    target?.setAttribute(THEME_ATTRIBUTE, theme);
  }
}

/**
 * Applies the stored choice. Each entry point calls it before it renders anything: the page's content
 * policy allows no inline script, so this is the earliest point there is. Until then the page is an
 * empty body in the device's own scheme.
 */
export function initTheme(storage: Pick<ThemeStorage, "getItem"> | null = browserStorage(), target = documentRoot()): Theme {
  const theme = readStoredTheme(storage);
  applyTheme(theme, target);
  return theme;
}
