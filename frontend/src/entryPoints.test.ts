import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { brandedHtml, TAGLINE_PLACEHOLDER } from "../vite.config";
import { BRAND_NAME, BRAND_PLACEHOLDER, BRAND_TAGLINE } from "./shared/brand";

const TELEGRAM_SCRIPT = "https://telegram.org/js/telegram-web-app.js";

/** A page as it is written: the product's name is a placeholder there. */
const source = (entry: string) => readFileSync(resolve(import.meta.dirname, "..", entry, "index.html"), "utf8");
/** The page as the build and the development server send it, with the name filled in. */
const page = (entry: string) => brandedHtml(source(entry));

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

/** What a page says of itself to whoever lists it or shares its link: the one description, or null. */
function description(html: string): string | null {
  const tags = [...html.replace(/<!--[\s\S]*?-->/g, "").matchAll(/<meta\s+name="description"\s+content="([^"]*)"\s*\/?>/gi)];
  return tags.length === 1 ? (tags[0]?.[1] ?? null) : null;
}

describe("each page describes itself", () => {
  it.each(["app", "panel", "admin", "k"])("%s has one description, in the page's language, of a length a result can show", (entry) => {
    const text = description(page(entry));
    expect(text).not.toBeNull();
    expect((text ?? "").length).toBeGreaterThanOrEqual(50);
    expect((text ?? "").length).toBeLessThanOrEqual(160);
    expect(text).toContain(BRAND_NAME);
    expect(text).not.toMatch(/[{}]/);
    // Uzbek, as `<html lang="uz">` says the page is until the application sets the reader's language.
    expect(page(entry)).toContain('<html lang="uz">');
  });

  it("no two pages share a description", () => {
    const texts = ["app", "panel", "admin", "k"].map((entry) => description(page(entry)));
    expect(new Set(texts).size).toBe(4);
  });

  it("would notice a page without one, with an empty one's length, or with two", () => {
    expect(description("<head><title>A page</title></head>")).toBeNull();
    expect(description('<meta name="description" content="a" /><meta name="description" content="b" />')).toBeNull();
    expect(description('<!-- <meta name="description" content="a" /> -->')).toBeNull();
    expect(description('<meta name="description" content="" />')).toBe("");
  });
});

describe("each page is named from the brand's definition", () => {
  it.each(["app", "panel", "admin", "k"])("%s writes a placeholder, and the build fills in the name", (entry) => {
    const written = source(entry);
    expect(written).not.toContain(BRAND_NAME);
    expect(/<title>([^<]*)<\/title>/.exec(written)?.[1]).toContain(BRAND_PLACEHOLDER);
    const title = /<title>([^<]*)<\/title>/.exec(page(entry))?.[1] ?? "";
    expect(title.startsWith(BRAND_NAME)).toBe(true);
    expect(page(entry)).not.toContain(BRAND_PLACEHOLDER);
    expect(page(entry)).not.toContain(TAGLINE_PLACEHOLDER);
  });

  it("the staff workspace is described by the line on what the product is", () => {
    expect(description(page("app"))).toContain(`${BRAND_NAME} — ${BRAND_TAGLINE}.`);
  });

  it("stops the build at a placeholder it does not know, and writes a name safely into an attribute", () => {
    expect(() => brandedHtml('<head><meta name="description" content="{product}" /></head>')).toThrow("{product}");
    expect(brandedHtml("<head><!-- {note} --></head><body>{kept}</body>")).toContain("{kept}");
  });

  it.each(["app", "panel", "admin"])("%s shows the brand's mark in the browser's tab", (entry) => {
    expect(source(entry)).toContain('<link rel="icon" type="image/svg+xml" href="/panel/icons/icon.svg" />');
  });

  it("the customer's page loads no picture at all: its policy allows none", () => {
    expect(source("k")).not.toMatch(/rel="icon"|<img|\.svg|\.png/);
  });
});
