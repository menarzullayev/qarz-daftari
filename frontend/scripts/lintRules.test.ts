import { resolve } from "node:path";

import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

/**
 * The lint rules that hold the markup to accessibility and the hooks to their two rules are worth what
 * they refuse. Each case below is a mistake of the kind the rules are there for, given to the linter
 * with this project's own configuration (eslint.config.js) under the name of a file of the application;
 * and beside each, the same code written rightly, which must pass. `npm run lint` fails on an error and
 * lets a warning through, so every one of these has to be an error.
 */
const root = resolve(import.meta.dirname, "..");
const eslint = new ESLint({ cwd: root });

async function errorsOf(code: string): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath: resolve(root, "src/shared/Probe.tsx") });
  return (result?.messages ?? []).filter((message) => message.severity === 2).map((message) => message.ruleId ?? "(parser)");
}

const HOOKS = `import { useEffect, useState } from "react";\n`;

describe("the accessibility rules refuse", () => {
  it.each([
    ["a picture with no text", `export const A = () => <img src="/a.png" />;`, "jsx-a11y/alt-text"],
    ["a click on something that is not a control", `export const A = () => <div onClick={() => undefined}>Ochish</div>;`, "jsx-a11y/click-events-have-key-events"],
    ["a link that leads nowhere", `export const A = () => <a onClick={() => undefined}>Ochish</a>;`, "jsx-a11y/anchor-is-valid"],
    ["a frame with no title", `export const A = () => <iframe src="/x" />;`, "jsx-a11y/iframe-has-title"],
    ["a role that does not exist", `export const A = () => <div role="buton">Saqlash</div>;`, "jsx-a11y/aria-role"],
    ["a label that names no field", `export const A = () => <label>Ism</label>;`, "jsx-a11y/label-has-associated-control"],
    ["focus taken by itself", `export const A = () => <input autoFocus />;`, "jsx-a11y/no-autofocus"],
    ["a table's role on something else's element", `export const A = () => <button role="cell">1</button>;`, "jsx-a11y/no-interactive-element-to-noninteractive-role"],
    ["a role an element already has", `export const A = () => <button type="button" role="button">Saqlash</button>;`, "jsx-a11y/no-redundant-roles"],
  ])("%s", async (_name, code, rule) => {
    expect(await errorsOf(code)).toContain(rule);
  });

  it("and pass the same things written rightly", async () => {
    const code = `
      export const A = () => (
        <>
          <img src="/a.png" alt="QR kod" />
          <button type="button" onClick={() => undefined}>Ochish</button>
          <a href="#/customers">Mijozlar</a>
          <iframe src="/x" title="Telegram orqali kirish" />
          <label htmlFor="name">Ism</label>
          <input id="name" />
        </>
      );`;
    expect(await errorsOf(code)).toEqual([]);
  });

  it("pass the panel's table, whose roles are spelled out on purpose", async () => {
    const code = `
      export const T = () => (
        <table role="table">
          <thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">Mijoz</th></tr></thead>
          <tbody role="rowgroup"><tr role="row"><th scope="row" role="rowheader">Ali</th><td role="cell">1</td></tr></tbody>
          <tfoot role="rowgroup"><tr role="row"><td role="cell">1</td></tr></tfoot>
        </table>
      );`;
    expect(await errorsOf(code)).toEqual([]);
  });
});

describe("the rules of hooks refuse", () => {
  it("a hook called on one path and not on another", async () => {
    const code = `${HOOKS}export function A({ on }: { on: boolean }) {
      if (on) {
        const [value] = useState(0);
        return <p>{value}</p>;
      }
      return null;
    }`;
    expect(await errorsOf(code)).toContain("react-hooks/rules-of-hooks");
  });

  it("an effect that reads a value its list does not name", async () => {
    const code = `${HOOKS}export function A({ id }: { id: string }) {
      const [seen, setSeen] = useState("");
      useEffect(() => { setSeen(id); }, []);
      return <p>{seen}</p>;
    }`;
    expect(await errorsOf(code)).toEqual(["react-hooks/exhaustive-deps"]);
  });

  it("an effect whose list is a spread, which nothing can check", async () => {
    const code = `${HOOKS}export function useA(run: () => void, deps: readonly unknown[]) {
      useEffect(() => { run(); }, [...deps]);
    }`;
    expect(await errorsOf(code)).toContain("react-hooks/exhaustive-deps");
  });

  it("and pass an effect whose list is complete", async () => {
    const code = `${HOOKS}export function A({ id }: { id: string }) {
      const [seen, setSeen] = useState("");
      useEffect(() => { setSeen(id); }, [id]);
      return <p>{seen}</p>;
    }`;
    expect(await errorsOf(code)).toEqual([]);
  });
});

describe("the application's own source", () => {
  it("switches a rule off only on one line, and says why", async () => {
    // A whole-file \`eslint-disable\` would take a file out of the rules; a line without a reason hides one.
    const { readdirSync, readFileSync } = await import("node:fs");
    const files = readdirSync(resolve(root, "src"), { recursive: true, encoding: "utf8" }).filter((name) => /\.tsx?$/.test(name));
    const problems: string[] = [];
    for (const name of files) {
      const lines = readFileSync(resolve(root, "src", name), "utf8").split("\n");
      lines.forEach((line, index) => {
        if (!/eslint-disable/.test(line)) {
          return;
        }
        const reasoned = /eslint-disable-(?:next-)?line [\w@/-]+(?:, ?[\w@/-]+)* -- \S.{10,}/.test(line);
        if (!reasoned) {
          problems.push(`${name}:${index + 1}`);
        }
      });
    }
    expect(problems).toEqual([]);
  });
});
