import { readFileSync, readdirSync } from "node:fs";
import { relative, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { findHardcodedText } from "./hardcodedText";

const SRC = resolve(import.meta.dirname, "..");

/** Every .tsx file under src/, except the catalogs' own folder and test files. */
function interfaceFiles(): string[] {
  return readdirSync(SRC, { recursive: true, encoding: "utf8" })
    .map((name) => name.replaceAll("\\", "/"))
    .filter((name) => name.endsWith(".tsx") && !name.endsWith(".test.tsx") && !name.startsWith("i18n/"))
    .sort();
}

const reasons = (source: string) => findHardcodedText("fixture.tsx", source).map((finding) => finding.reason);

describe("interface text comes only from the catalogs (ADR-021)", () => {
  it("finds the .tsx files of the application", () => {
    const files = interfaceFiles();
    expect(files).toContain("shared/Shell.tsx");
    expect(files).toContain("app/main.tsx");
    expect(files.some((name) => name.startsWith("i18n/"))).toBe(false);
  });

  it.each(interfaceFiles())("%s has no hard-coded text", (name) => {
    const path = resolve(SRC, name);
    expect(findHardcodedText(relative(SRC, path), readFileSync(path, "utf8"))).toEqual([]);
  });
});

describe("findHardcodedText", () => {
  it("accepts text that comes from the catalog", () => {
    const source = `
      export function Screen({ title }: { title: string }) {
        const { t } = useI18n();
        return (
          <main className="shell" data-kind="screen">
            <h1>{title}</h1>
            <p>{t("screen.placeholder")}</p>
            <a href="#/" aria-current="page" aria-label={t("notFound.home")}>{t("notFound.home")}</a>
            <span>: </span>
          </main>
        );
      }`;
    expect(findHardcodedText("fixture.tsx", source)).toEqual([]);
  });

  it("rejects an Uzbek phrase written as a JSX text node", () => {
    expect(reasons("export const A = () => <h1>Xodimlar ish joyi</h1>;")).toEqual(["text written in JSX"]);
    expect(findHardcodedText("fixture.tsx", "const a = 1;\nexport const A = () => <p>Mijozlar</p>;")).toEqual([
      { line: 2, text: "Mijozlar", reason: "text written in JSX" },
    ]);
  });

  it("rejects a Cyrillic letter anywhere, including comments and strings", () => {
    expect(reasons("export const A = () => <p>Клиенты</p>;")).toContain("Cyrillic letter in a .tsx file");
    expect(reasons('const label = "Клиенты";')).toEqual(["Cyrillic letter in a .tsx file"]);
    expect(reasons("// клиенты\nexport const a = 1;")).toEqual(["Cyrillic letter in a .tsx file"]);
  });

  it("rejects a string literal used as a JSX child", () => {
    expect(reasons('export const A = () => <p>{"Mijozlar"}</p>;')).toEqual(["string literal in JSX"]);
    expect(reasons("export const A = () => <p>{`Mijozlar`}</p>;")).toEqual(["string literal in JSX"]);
  });

  it("rejects text in attributes that people see or hear", () => {
    expect(reasons('export const A = () => <button aria-label="Yopish" />;')).toEqual([
      'text in the "aria-label" attribute',
    ]);
    expect(reasons('export const A = () => <input placeholder={"Qidirish"} />;')).toEqual([
      'text in the "placeholder" attribute',
    ]);
    // The placeholder Shell this story replaced was used exactly like this.
    expect(reasons('export const A = () => <Shell title="Xodimlar ish joyi" />;')).toEqual([
      'text in the "title" attribute',
    ]);
  });

  it("does not see text that reaches JSX through a variable (documented limit)", () => {
    expect(reasons('const label = "Mijozlar";\nexport const A = () => <p>{label}</p>;')).toEqual([]);
  });
});
