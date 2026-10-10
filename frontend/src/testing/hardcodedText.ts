import ts from "typescript";

export type HardcodedText = { line: number; text: string; reason: string };

/**
 * Attributes whose string value is never shown or read to a person. A string literal with letters in
 * any other JSX attribute (title, aria-label, placeholder, alt, or a component's own text prop) counts
 * as hard-coded interface text.
 */
const TECHNICAL_ATTRIBUTES = new Set([
  "className",
  "id",
  "key",
  "type",
  "role",
  "href",
  "to",
  "rel",
  "target",
  "name",
  "htmlFor",
  "lang",
  "dir",
  "src",
  // How a picture is fetched and decoded ("lazy", "async"): tokens, read by nobody.
  "loading",
  "decoding",
  "method",
  "scope",
  "autoComplete",
  "inputMode",
  "entryKey",
  "labelKey",
  // The kind of a badge ("danger", "warning"): a class name's suffix, never shown.
  "tone",
  // A currency code ("UZS", "USD") that says how an amount is formatted; the code itself is never shown.
  "currency",
]);

const LETTER = /\p{L}/u;
const CYRILLIC = /\p{Script=Cyrillic}/u;

function isTechnicalAttribute(name: string): boolean {
  if (name.startsWith("data-")) {
    return true;
  }
  // ARIA attributes that hold text are the exception; the rest hold tokens such as "page" or "true".
  if (name.startsWith("aria-")) {
    return !["aria-label", "aria-description", "aria-placeholder", "aria-roledescription", "aria-valuetext"].includes(
      name,
    );
  }
  return TECHNICAL_ATTRIBUTES.has(name);
}

/**
 * Finds interface text written directly in a .tsx file instead of coming from the message catalogs:
 *  1. a Cyrillic letter anywhere in the file (code, comment, or string);
 *  2. a JSX text node that contains a letter (`<p>Mijozlar</p>`);
 *  3. a string literal with a letter as a JSX child (`<p>{"Mijozlar"}</p>`);
 *  4. a string literal with a letter in a JSX attribute that is not a technical one (`title="Mijozlar"`).
 *
 * Limits, on purpose, to keep this simple: it does not follow values. Text stored in a variable,
 * returned by a function, chosen by a conditional expression, or written in a .ts file is not found,
 * and Latin-script text outside JSX is indistinguishable from identifiers. Review still matters.
 */
export function findHardcodedText(fileName: string, source: string): HardcodedText[] {
  const file = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const found: HardcodedText[] = [];
  const lineOf = (position: number) => file.getLineAndCharacterOfPosition(position).line + 1;

  source.split("\n").forEach((text, index) => {
    if (CYRILLIC.test(text)) {
      found.push({ line: index + 1, text: text.trim(), reason: "Cyrillic letter in a .tsx file" });
    }
  });

  const isTextLiteral = (node: ts.Node): node is ts.StringLiteral | ts.NoSubstitutionTemplateLiteral =>
    (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) && LETTER.test(node.text);

  const visit = (node: ts.Node): void => {
    if (ts.isJsxText(node) && LETTER.test(node.text)) {
      found.push({ line: lineOf(node.getStart(file)), text: node.text.trim(), reason: "text written in JSX" });
    } else if (ts.isJsxExpression(node) && node.expression && isTextLiteral(node.expression)) {
      const parent = node.parent;
      if (ts.isJsxElement(parent) || ts.isJsxFragment(parent)) {
        found.push({ line: lineOf(node.getStart(file)), text: node.expression.text, reason: "string literal in JSX" });
      }
    } else if (ts.isJsxAttribute(node) && node.initializer && !isTechnicalAttribute(node.name.getText(file))) {
      const value = ts.isJsxExpression(node.initializer) ? node.initializer.expression : node.initializer;
      if (value && isTextLiteral(value)) {
        found.push({
          line: lineOf(node.getStart(file)),
          text: value.text,
          reason: `text in the "${node.name.getText(file)}" attribute`,
        });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);

  return found;
}
