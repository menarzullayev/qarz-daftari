import jsQR from "jsqr";
import { describe, expect, it } from "vitest";

import { START } from "../testing/fakeServer";
import { paintQr } from "../testing/qr";
import { drawQr, QUIET_ZONE } from "./qr";
import { deepLink, readBotUsername } from "./settings";

/** What a phone's scanner would read from the drawing. */
function scan(drawing: { side: number; path: string }): string | null {
  const image = paintQr(drawing, 4);
  return jsQR(image.data, image.width, image.height)?.data ?? null;
}

describe("drawQr", () => {
  const link = deepLink("qarz_daftari_bot", START);

  it("draws a code that a scanner reads back as exactly the link", () => {
    expect(scan(drawQr(link))).toBe(link);
  });

  it("draws a different code for a different link", () => {
    const other = deepLink("qarz_daftari_bot", `${START}x`);
    expect(drawQr(other).path).not.toBe(drawQr(link).path);
    expect(scan(drawQr(other))).toBe(other);
  });

  it("leaves the quiet zone a scanner needs, and stays inside the view box", () => {
    const { side, path } = drawQr(link);
    expect(QUIET_ZONE).toBe(4);
    const runs = [...path.matchAll(/M(\d+) (\d+)h(\d+)v1h-\d+z/g)].map((run) => run.slice(1, 4).map(Number));
    expect(runs.length).toBeGreaterThan(50);
    for (const [x = -1, y = -1, width = 0] of runs) {
      expect(x).toBeGreaterThanOrEqual(QUIET_ZONE);
      expect(y).toBeGreaterThanOrEqual(QUIET_ZONE);
      expect(x + width).toBeLessThanOrEqual(side - QUIET_ZONE);
      expect(y).toBeLessThan(side - QUIET_ZONE);
    }
  });
});

describe("the bot setting", () => {
  it("builds the deep link Telegram opens with /start", () => {
    expect(deepLink("qarz_daftari_bot", START)).toBe(`https://t.me/qarz_daftari_bot?start=${START}`);
    // A start code is letters, digits, "_" and "-"; anything else could not change the address.
    expect(deepLink("qarz_daftari_bot", "a&b=c#d")).toBe("https://t.me/qarz_daftari_bot?start=a%26b%3Dc%23d");
  });

  it("reads a username and refuses anything that is not one", () => {
    expect(readBotUsername("qarz_daftari_bot")).toBe("qarz_daftari_bot");
    expect(readBotUsername(" @QarzDaftariBot ")).toBe("QarzDaftariBot");
    expect(readBotUsername(undefined)).toBeNull();
    expect(readBotUsername("")).toBeNull();
    expect(readBotUsername("bot")).toBeNull(); // shorter than Telegram allows
    expect(readBotUsername("1qarz_bot")).toBeNull();
    expect(readBotUsername("qarz bot")).toBeNull();
    expect(readBotUsername("evil.example/x?")).toBeNull();
    expect(readBotUsername("a".repeat(33))).toBeNull();
    expect(readBotUsername(42)).toBeNull();
  });
});
