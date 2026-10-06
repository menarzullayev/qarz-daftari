import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const TELEGRAM_SCRIPT = "https://telegram.org/js/telegram-web-app.js";

const page = (entry: string) => readFileSync(resolve(import.meta.dirname, "..", entry, "index.html"), "utf8");

/** Every absolute or protocol-relative URL a page would load something from. */
function externalUrls(html: string): string[] {
  const withoutComments = html.replace(/<!--[\s\S]*?-->/g, "");
  return [...withoutComments.matchAll(/(?:src|href)\s*=\s*["']((?:https?:)?\/\/[^"']+)["']/gi)].map(
    (match) => match[1] ?? "",
  );
}

describe("third-party resources", () => {
  it("loads Telegram's official script in the staff workspace and nothing else", () => {
    expect(externalUrls(page("app"))).toEqual([TELEGRAM_SCRIPT]);
  });

  it.each(["panel", "admin"])("loads nothing from another host in %s", (entry) => {
    expect(externalUrls(page(entry))).toEqual([]);
    expect(page(entry)).not.toContain("telegram");
  });

  it("would notice a font or analytics tag", () => {
    expect(externalUrls('<link href="https://fonts.googleapis.com/css2?family=Inter" rel="stylesheet">')).toHaveLength(1);
    expect(externalUrls('<script src="//cdn.example.com/a.js"></script>')).toHaveLength(1);
    expect(externalUrls('<script type="module" src="/src/app/main.tsx"></script>')).toEqual([]);
  });
});
