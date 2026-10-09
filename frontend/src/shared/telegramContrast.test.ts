import { describe, expect, it } from "vitest";

import { readStyleSheet } from "../testing/styleGuard";
import { initTelegram, themeToCssVariables, type TelegramThemeParams, type TelegramWebApp } from "./telegram";
import {
  AA_NON_TEXT,
  AA_TEXT,
  BASE_TOKENS,
  type BaseColors,
  contrast,
  DESIGN,
  failingPairs,
  guardContrast,
  type Scheme,
} from "./telegramContrast";

function mapped(theme: TelegramThemeParams): BaseColors {
  const variables = themeToCssVariables(theme);
  if (!("--qd-bg" in variables)) {
    throw new Error("the theme has no background and text color");
  }
  return variables as BaseColors;
}

const DAY: TelegramThemeParams = {
  bg_color: "#ffffff",
  secondary_bg_color: "#efeff4",
  text_color: "#000000",
  hint_color: "#999999",
  link_color: "#2481cc",
  button_color: "#2481cc",
  button_text_color: "#ffffff",
};
const NIGHT: TelegramThemeParams = {
  bg_color: "#17212b",
  secondary_bg_color: "#232e3c",
  text_color: "#f5f5f5",
  hint_color: "#708499",
  link_color: "#6ab3f3",
  button_color: "#5288c1",
  button_text_color: "#ffffff",
};
const MID_GREY: TelegramThemeParams = {
  bg_color: "#808080",
  secondary_bg_color: "#777777",
  text_color: "#000000",
  hint_color: "#666666",
  link_color: "#3366cc",
  button_color: "#888888",
  button_text_color: "#ffffff",
};

/**
 * Themes of Telegram's own clients, with the colors those clients have reported in `themeParams` (a
 * client's version may differ by a shade). Each one is fine inside Telegram, where a hint is a caption
 * beside large text; here the same grey carries labels, dates and amounts, and the button color carries
 * a label of ordinary size.
 */
const TELEGRAM_THEMES: Readonly<Record<string, { scheme: Scheme; theme: TelegramThemeParams; fails: readonly string[] }>> = {
  "the default day theme (iOS, Android, Desktop)": {
    scheme: "light",
    theme: DAY,
    fails: [
      "--qd-muted on --qd-bg",
      "--qd-muted on --qd-surface",
      "--qd-link on --qd-bg",
      "--qd-link on --qd-surface",
      "--qd-on-accent on --qd-accent",
      "--qd-border on --qd-bg",
    ],
  },
  "the night theme of Telegram Desktop": {
    scheme: "dark",
    theme: NIGHT,
    fails: ["--qd-muted on --qd-bg", "--qd-muted on --qd-surface", "--qd-on-accent on --qd-accent"],
  },
  "the dark theme of Telegram Web": {
    scheme: "dark",
    theme: {
      bg_color: "#212121",
      secondary_bg_color: "#0f0f0f",
      text_color: "#ffffff",
      hint_color: "#aaaaaa",
      link_color: "#8774e1",
      button_color: "#8774e1",
      button_text_color: "#ffffff",
    },
    fails: ["--qd-link on --qd-bg", "--qd-on-accent on --qd-accent"],
  },
};

/** Themes a person can make in Telegram's theme editor, each wrong in one way. */
const BROKEN_THEMES: Readonly<Record<string, TelegramThemeParams>> = {
  "text almost the color of the ground": { bg_color: "#1c1c1e", text_color: "#3a3a3c" },
  "the same color for the ground and the text": { bg_color: "#ffffff", text_color: "#ffffff" },
  "a button the color of the page": {
    bg_color: "#ffffff",
    text_color: "#111111",
    button_color: "#ffffff",
    button_text_color: "#000000",
  },
  "a yellow button with a white label": {
    bg_color: "#ffffff",
    text_color: "#000000",
    button_color: "#ffd60a",
    button_text_color: "#ffffff",
  },
  "a second ground the color of the text": {
    bg_color: "#000000",
    secondary_bg_color: "#ffffff",
    text_color: "#ffffff",
    hint_color: "#8e8e93",
  },
  "a mid-grey ground, on which few colors read": MID_GREY,
  "pastel on pastel": {
    bg_color: "#fde2e4",
    secondary_bg_color: "#fad2e1",
    text_color: "#9d8189",
    hint_color: "#d8b4c0",
    link_color: "#e5989b",
    button_color: "#ffb4a2",
    button_text_color: "#ffffff",
  },
};

describe("the design system's own colors", () => {
  const css = readStyleSheet("shared", "tokens.css");
  const blocks: Record<Scheme, string> = {
    light: /(?:^|\n):root \{([^}]*)\}/.exec(css)?.[1] ?? "",
    dark: /\n:root\[data-theme="dark"\] \{([^}]*)\}/.exec(css)?.[1] ?? "",
  };

  it.each(["light", "dark"] as const)("are the ones tokens.css has (%s)", (scheme) => {
    const inSheet = Object.fromEntries(
      [...blocks[scheme].matchAll(/(--qd-[\w-]+):\s*(#[0-9a-f]{6})\s*;/gi)].map((match) => [match[1], match[2]]),
    );
    for (const token of BASE_TOKENS) {
      expect(DESIGN[scheme][token], token).toBe(inSheet[token]);
    }
  });

  it.each(["light", "dark"] as const)("keep every pair the guard promises (%s)", (scheme) => {
    expect(failingPairs(DESIGN[scheme])).toEqual([]);
  });

  it.each(["light", "dark"] as const)("pass through the guard unchanged (%s)", (scheme) => {
    expect(guardContrast(DESIGN[scheme], scheme)).toEqual(DESIGN[scheme]);
    expect(guardContrast(DESIGN[scheme])).toEqual(DESIGN[scheme]);
  });
});

describe("contrast", () => {
  it("is 21 for black on white, 1 for a color on itself, and the same both ways round", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#336699", "#336699")).toBe(1);
    expect(contrast("#2481cc", "#ffffff")).toBe(contrast("#ffffff", "#2481cc"));
  });

  it("puts Telegram's blue on white below the threshold for text and above the one for shapes", () => {
    const ratio = contrast("#2481cc", "#ffffff");
    expect(ratio).toBeLessThan(AA_TEXT);
    expect(ratio).toBeGreaterThan(AA_NON_TEXT);
  });
});

describe("Telegram's own themes", () => {
  for (const [name, { scheme, theme, fails }] of Object.entries(TELEGRAM_THEMES)) {
    it(`${name}: the pairs that are hard to read are found, and only those are replaced`, () => {
      const before = mapped(theme);
      // What is wrong with the theme as Telegram gives it: without the guard this is what is drawn.
      expect(failingPairs(before)).toEqual(fails);

      const after = guardContrast(before, scheme);
      expect(failingPairs(after)).toEqual([]);
      // The ground and the text were readable: they stay Telegram's.
      expect(after["--qd-bg"]).toBe(before["--qd-bg"]);
      expect(after["--qd-text"]).toBe(before["--qd-text"]);
      expect(after["--qd-surface"]).toBe(before["--qd-surface"]);
      // A token no failing pair names is the theme's own still.
      const named = fails.join(" ");
      for (const token of BASE_TOKENS) {
        const touched = named.includes(token) || (token === "--qd-accent" && named.includes("--qd-on-accent"));
        if (!touched) {
          expect(after[token], token).toBe(before[token]);
        }
      }
    });
  }

  it("the default day theme: grey hints and the blue button take the design system's light colors", () => {
    const after = guardContrast(mapped(DAY), "light");
    expect(after["--qd-muted"]).toBe(DESIGN.light["--qd-muted"]);
    expect(after["--qd-link"]).toBe(DESIGN.light["--qd-link"]);
    expect(after["--qd-border"]).toBe(DESIGN.light["--qd-border"]);
    expect(after["--qd-accent"]).toBe(DESIGN.light["--qd-accent"]);
    expect(after["--qd-on-accent"]).toBe(DESIGN.light["--qd-on-accent"]);
    // Telegram's blue reaches 3:1 on white: enough for the focus ring, which is a shape and not text.
    expect(after["--qd-focus"]).toBe("#2481cc");
  });

  it("the night theme of Telegram Desktop: the dark set's colors, not the light set's", () => {
    const after = guardContrast(mapped(NIGHT), "dark");
    expect(after["--qd-muted"]).toBe(DESIGN.dark["--qd-muted"]);
    expect(after["--qd-accent"]).toBe(DESIGN.dark["--qd-accent"]);
    expect(after["--qd-on-accent"]).toBe(DESIGN.dark["--qd-on-accent"]);
    expect(after["--qd-link"]).toBe("#6ab3f3");
  });
});

describe("themes that are wrong", () => {
  for (const [name, theme] of Object.entries(BROKEN_THEMES)) {
    it(`${name}: something fails before the guard and nothing after it`, () => {
      const before = mapped(theme);
      expect(failingPairs(before).length).toBeGreaterThan(0);
      expect(failingPairs(guardContrast(before))).toEqual([]);
      expect(failingPairs(guardContrast(before, "light"))).toEqual([]);
      expect(failingPairs(guardContrast(before, "dark"))).toEqual([]);
    });
  }

  it("unreadable text takes the whole ground of the design system with it, for the scheme the ground has", () => {
    const after = guardContrast(mapped({ bg_color: "#1c1c1e", text_color: "#3a3a3c" }));
    expect(after["--qd-bg"]).toBe(DESIGN.dark["--qd-bg"]);
    expect(after["--qd-text"]).toBe(DESIGN.dark["--qd-text"]);
    expect(after["--qd-surface"]).toBe(DESIGN.dark["--qd-surface"]);
  });

  it("what Telegram says the scheme is decides, when it says", () => {
    const colors = mapped({ bg_color: "#ffffff", text_color: "#ffffff" });
    expect(guardContrast(colors, "dark")["--qd-bg"]).toBe(DESIGN.dark["--qd-bg"]);
    expect(guardContrast(colors, "light")["--qd-bg"]).toBe(DESIGN.light["--qd-bg"]);
  });

  it("on a ground where the design system's color does not read either, the text color is used", () => {
    const after = guardContrast(mapped(MID_GREY));
    expect(after["--qd-bg"]).toBe("#808080");
    expect(after["--qd-muted"]).toBe("#000000");
    expect(after["--qd-link"]).toBe("#000000");
    // The button is the text and the ground the other way round.
    expect(after["--qd-accent"]).toBe("#000000");
    expect(after["--qd-on-accent"]).toBe("#808080");
  });

  it("holds for any nine colors: a thousand themes drawn at random", () => {
    // A fixed sequence, so a failure can be repeated.
    let seed = 20261009;
    const next = () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return seed;
    };
    const hex = () => `#${(next() & 0xffffff).toString(16).padStart(6, "0")}`;
    for (let round = 0; round < 1000; round += 1) {
      const colors = Object.fromEntries(BASE_TOKENS.map((token) => [token, hex()])) as BaseColors;
      for (const scheme of [null, "light", "dark"] as const) {
        expect(failingPairs(guardContrast(colors, scheme)), JSON.stringify(colors)).toEqual([]);
      }
    }
  });
});

describe("what reaches the page", () => {
  function launch(theme: TelegramThemeParams, colorScheme?: string) {
    const values = new Map<string, string>();
    const webApp: TelegramWebApp = {
      initData: "query_id=AAE&user=%7B%22id%22%3A1%7D&hash=abc",
      initDataUnsafe: {},
      themeParams: theme,
      ready: () => undefined,
      expand: () => undefined,
      ...(colorScheme === undefined ? {} : { colorScheme }),
    };
    initTelegram(webApp, { style: { setProperty: (name: string, value: string | null) => void values.set(name, value ?? "") } });
    return values;
  }

  it("is the guarded theme: Telegram's grey hint is not what labels are drawn in", () => {
    const values = launch(DAY, "light");
    expect(values.get("--qd-bg")).toBe("#ffffff");
    expect(values.get("--qd-muted")).toBe(DESIGN.light["--qd-muted"]);
    expect(failingPairs(Object.fromEntries(values) as BaseColors)).toEqual([]);
  });

  it("is Telegram's theme untouched when all of it reads well", () => {
    const theme = {
      bg_color: "#ffffff",
      secondary_bg_color: "#f1f3f6",
      text_color: "#0f172a",
      hint_color: "#55627a",
      link_color: "#0b5cc4",
      button_color: "#0b63ce",
      button_text_color: "#ffffff",
    };
    expect(Object.fromEntries(launch(theme))).toEqual(themeToCssVariables(theme));
  });
});
