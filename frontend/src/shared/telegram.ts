/**
 * Typed, defensive access to the Telegram Mini App bridge (`window.Telegram.WebApp`). Only the staff
 * workspace loads Telegram's script; the web panel and the admin panel never have the object, and a
 * browser that opens the staff workspace outside Telegram gets the object with empty launch data.
 * Every function here returns a neutral value in those cases instead of throwing.
 */

export type TelegramThemeParams = {
  bg_color?: string;
  secondary_bg_color?: string;
  text_color?: string;
  hint_color?: string;
  link_color?: string;
  button_color?: string;
  button_text_color?: string;
};

export type TelegramWebApp = {
  initData: string;
  initDataUnsafe: { user?: { language_code?: string } };
  themeParams: TelegramThemeParams;
  /** "light" or "dark", as the Telegram client reports it; older clients do not. */
  colorScheme?: string;
  ready: () => void;
  expand: () => void;
  onEvent?: (eventType: "themeChanged", handler: () => void) => void;
};

type TelegramHost = { Telegram?: { WebApp?: unknown } };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function currentHost(): TelegramHost | null {
  return typeof window === "undefined" ? null : (window as unknown as TelegramHost);
}

/** The bridge object, or null when the script is absent or the object does not look like the bridge. */
export function getWebApp(host: TelegramHost | null = currentHost()): TelegramWebApp | null {
  const candidate: unknown = host?.Telegram?.WebApp;
  if (!isRecord(candidate)) {
    return null;
  }
  if (typeof candidate["ready"] !== "function" || typeof candidate["expand"] !== "function") {
    return null;
  }
  return candidate as unknown as TelegramWebApp;
}

/**
 * The signed launch string to send once to `POST /api/v1/auth/telegram-webapp`. Null outside Telegram.
 * It is a credential: never log it and never put it in a URL.
 */
export function getInitData(webApp: TelegramWebApp | null = getWebApp()): string | null {
  const initData: unknown = webApp?.initData;
  return typeof initData === "string" && initData.length > 0 ? initData : null;
}

export function isInsideTelegram(webApp: TelegramWebApp | null = getWebApp()): boolean {
  return getInitData(webApp) !== null;
}

/** Interface language of the Telegram user. Unverified: good for picking a language, nothing else. */
export function getTelegramLanguageCode(webApp: TelegramWebApp | null = getWebApp()): string | null {
  const unsafe: unknown = webApp?.initDataUnsafe;
  const user = isRecord(unsafe) ? unsafe["user"] : undefined;
  const code = isRecord(user) ? user["language_code"] : undefined;
  return typeof code === "string" && code.length > 0 ? code : null;
}

const HEX_COLOR = /^#[0-9a-f]{6}$/i;

function color(value: unknown): string | null {
  return typeof value === "string" && HEX_COLOR.test(value) ? value : null;
}

/**
 * Maps Telegram theme colors to the application's CSS variables. Colors are applied in pairs that were
 * designed together: without both a background and a text color nothing is applied, so the default
 * theme is never mixed with half of a Telegram one (which could leave dark text on a dark background).
 * Anything that is not a six-digit hex color is ignored.
 */
export function themeToCssVariables(theme: unknown): Record<string, string> {
  if (!isRecord(theme)) {
    return {};
  }
  const background = color(theme["bg_color"]);
  const text = color(theme["text_color"]);
  if (!background || !text) {
    return {};
  }
  const variables: Record<string, string> = {
    "--qd-bg": background,
    "--qd-text": text,
    "--qd-surface": color(theme["secondary_bg_color"]) ?? background,
    "--qd-muted": color(theme["hint_color"]) ?? text,
    "--qd-link": color(theme["link_color"]) ?? text,
    "--qd-border": color(theme["hint_color"]) ?? text,
    "--qd-focus": color(theme["link_color"]) ?? text,
  };
  const button = color(theme["button_color"]);
  const buttonText = color(theme["button_text_color"]);
  variables["--qd-accent"] = button && buttonText ? button : text;
  variables["--qd-on-accent"] = button && buttonText ? buttonText : background;
  return variables;
}

/** Relative luminance (WCAG) of a six-digit hex color. */
function luminance(hex: string): number {
  const channels = [1, 3, 5].map((start) => {
    const value = Number.parseInt(hex.slice(start, start + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * (channels[0] ?? 0) + 0.7152 * (channels[1] ?? 0) + 0.0722 * (channels[2] ?? 0);
}

/** Below this luminance a background reads as dark: light text on it has the better contrast. */
const DARK_BACKGROUND = 0.18;

/**
 * Whether Telegram's theme is light or dark: what the client says, or else what its background color
 * shows. Null when neither is known; the device's own scheme then stays in charge.
 */
export function telegramColorScheme(webApp: TelegramWebApp | null): "light" | "dark" | null {
  const said: unknown = webApp?.colorScheme;
  if (said === "light" || said === "dark") {
    return said;
  }
  const theme: unknown = webApp?.themeParams;
  const background = isRecord(theme) ? color(theme["bg_color"]) : null;
  if (!background) {
    return null;
  }
  return luminance(background) < DARK_BACKGROUND ? "dark" : "light";
}

type StyleTarget = {
  style: Pick<CSSStyleDeclaration, "setProperty">;
  setAttribute?: (name: string, value: string) => void;
};

/**
 * Telegram's colors replace the nine base tokens. The tokens Telegram has no color for (soft grounds,
 * hairlines, status colors, shadows) come from the application's light or dark set, whichever matches
 * Telegram's scheme: `data-theme` chooses it, and `data-telegram` marks the page as themed by Telegram.
 */
export function applyTelegramTheme(webApp: TelegramWebApp | null, target: StyleTarget): void {
  for (const [name, value] of Object.entries(themeToCssVariables(webApp?.themeParams))) {
    target.style.setProperty(name, value);
  }
  const scheme = telegramColorScheme(webApp);
  if (scheme) {
    target.setAttribute?.("data-theme", scheme);
  }
}

export type TelegramLaunch = {
  insideTelegram: boolean;
  initData: string | null;
  languageCode: string | null;
};

/**
 * Call once at start-up of the staff workspace. Inside Telegram it tells the client the page is ready,
 * expands the sheet to full height, and follows the Telegram theme. Outside Telegram it does nothing.
 */
export function initTelegram(
  webApp: TelegramWebApp | null = getWebApp(),
  target: StyleTarget | null = typeof document === "undefined" ? null : document.documentElement,
): TelegramLaunch {
  const launch: TelegramLaunch = {
    insideTelegram: isInsideTelegram(webApp),
    initData: getInitData(webApp),
    languageCode: getTelegramLanguageCode(webApp),
  };
  if (!webApp || !launch.insideTelegram) {
    return launch;
  }
  try {
    webApp.ready();
    webApp.expand();
    if (target) {
      target.setAttribute?.("data-telegram", "");
      applyTelegramTheme(webApp, target);
      webApp.onEvent?.("themeChanged", () => applyTelegramTheme(webApp, target));
    }
  } catch {
    // An old or unusual Telegram client must not stop the workspace from opening.
  }
  return launch;
}
