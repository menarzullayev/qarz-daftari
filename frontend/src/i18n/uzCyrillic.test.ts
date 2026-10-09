import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { BRAND_NAME } from "../shared/brand";
import { placeholdersOf } from "./catalog";
import type { Message } from "./types";
import { BRANDS, CODES, PRODUCT, STEMS, toCyrillic, UPPER_WORDS, WORDS } from "./uzCyrillic";

/**
 * Uzbek Cyrillic is made, not typed: this is the rule that makes it. The cases and the tables are in one
 * file that the server's twin (`backend/src/qarz/domain/uz_cyrillic.py`) is held to as well, so the bot
 * and the screens write the same word the same way.
 */
type Fixture = {
  brands: string[];
  codes: string[];
  upper_words: string[];
  words: Record<string, string>;
  stems: Record<string, string>;
  cases: [string, string][];
  wrong: [string, string][];
};

const fixture = JSON.parse(
  readFileSync(resolve(import.meta.dirname, "../../../backend/tests/data/uz_cyrillic_cases.json"), "utf8"),
) as Fixture;

const catalogModules = import.meta.glob<Record<string, Record<string, Message>>>(["./uz.ts", "./*/uz.ts"], { eager: true });
const uzbekTexts = Object.values(catalogModules)
  .flatMap((module) => Object.values(module))
  .flatMap((catalog) => Object.values(catalog))
  .flatMap((message) => (typeof message === "string" ? [message] : Object.values(message)));

describe("the shared cases", () => {
  it("has the cases to run", () => {
    expect(fixture.cases.length).toBeGreaterThan(100);
    expect(fixture.wrong.length).toBeGreaterThan(10);
  });

  it.each(fixture.cases)("writes %j as %j", (latin, cyrillic) => {
    expect(toCyrillic(latin)).toBe(cyrillic);
  });

  it.each(fixture.wrong)("never writes %j as %j", (latin, wrong) => {
    expect(toCyrillic(latin)).not.toBe(wrong);
  });

  it("has the same tables as the server's twin", () => {
    expect([...BRANDS].sort()).toEqual(fixture.brands);
    expect(PRODUCT).toBe(BRAND_NAME);
    expect([...CODES].sort()).toEqual(fixture.codes);
    expect([...UPPER_WORDS].sort()).toEqual(fixture.upper_words);
    expect(WORDS).toEqual(fixture.words);
    expect(STEMS).toEqual(fixture.stems);
  });
});

describe("what is never touched", () => {
  it.each([
    ["{name}", "a placeholder"],
    ["{count}", "a placeholder"],
    ["/obuna", "a bot command"],
    ["/start", "a bot command"],
    ["https://t.me/qarz_bot?start=abc", "a link"],
    ["yordam@qarz.uz", "an e-mail address"],
    ["@qarz_bot", "a Telegram name"],
    [BRAND_NAME, "the product's name"],
    ["Telegram", "a brand"],
    ["Excel", "a brand"],
    ["SMS", "a code"],
    ["UZS", "a code"],
    ["USD", "a code"],
    ["QR", "a code"],
    ["hisobot.xlsx", "a file name"],
    ["1 250.50 $", "an amount in dollars"],
    ["45 000", "a number"],
    ["Русский", "Cyrillic text"],
  ])("leaves %j as it is: %s", (text) => {
    expect(toCyrillic(text)).toBe(text);
    // Inside a sentence too, with Uzbek on both sides of it.
    expect(toCyrillic(`Bu ${text} uchun`)).toBe(`Бу ${text} учун`);
  });

  it("gives back what is already Cyrillic, so applying it twice changes nothing", () => {
    for (const [latin] of fixture.cases) {
      const once = toCyrillic(latin);
      expect(toCyrillic(once), latin).toBe(once);
    }
  });

  it("would be noticed if it touched a placeholder: the negative of the checks below", () => {
    const careless = (text: string) => text.replace(/name/g, "наме");
    expect(placeholdersOf(careless("Salom, {name}"))).not.toEqual(placeholdersOf("Salom, {name}"));
    expect(placeholdersOf(toCyrillic("Salom, {name}"))).toEqual(placeholdersOf("Salom, {name}"));
  });
});

describe("every Uzbek text of the product", () => {
  it("has texts to check", () => {
    expect(uzbekTexts.length).toBeGreaterThan(1200);
  });

  it("keeps its placeholders, its commands and its line breaks", () => {
    for (const text of uzbekTexts) {
      const made = toCyrillic(text);
      expect(placeholdersOf(made), text).toEqual(placeholdersOf(text));
      expect(made.match(/(?:^|\s)\/[a-z_]+/g) ?? [], text).toEqual(text.match(/(?:^|\s)\/[a-z_]+/g) ?? []);
      expect(made.split("\n").length, text).toBe(text.split("\n").length);
      expect(made.trim(), text).not.toBe("");
    }
  });

  it("leaves no Uzbek letter pair behind: no o', g', sh or ch in a word that was written out", () => {
    const leftovers = uzbekTexts
      .map((text) => toCyrillic(text))
      .filter((made) => /[\p{Script=Cyrillic}][a-z']|[a-z'][\p{Script=Cyrillic}]/u.test(made.replace(/\{[^{}]*\}/g, " ")))
      // A brand with an Uzbek ending is the one place where the two scripts meet in a word.
      .filter((made) => !new RegExp(`(?:${BRANDS.join("|")})'?[\\p{Script=Cyrillic}]`, "u").test(made));
    expect(leftovers).toEqual([]);
  });

  it("writes the same digits, signs and emoji", () => {
    const rest = (text: string) => text.replace(/\{[^{}]*\}/g, "").replace(/[\p{L}\p{M}'ʻʼ’‘`]/gu, "");
    for (const text of uzbekTexts) {
      // The apostrophe of o' and g' is part of a letter and goes with it; everything else stays.
      expect(rest(toCyrillic(text)), text).toBe(rest(text));
    }
  });
});
