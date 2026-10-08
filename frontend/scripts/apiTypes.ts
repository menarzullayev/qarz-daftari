/**
 * The API's types, generated from the back end's description (ADR-012, ADR-013).
 *
 * `backend/openapi.json` is written by the back end from its own code
 * (`python -m qarz.interface.api_description`). This script turns it into `src/shared/api.generated.ts`,
 * which is committed, so that building the front end needs no Python. The file holds types only and adds
 * nothing to the bundle.
 *
 * Usage: `npm run api:types` writes the file. `npm run api:check` writes nothing and fails when the
 * committed file is not what the description generates; CI runs it.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import openapiTS, { astToString, type OpenAPI3 } from "openapi-typescript";

const ROOT = resolve(import.meta.dirname, "..");
export const DESCRIPTION_PATH = resolve(ROOT, "..", "backend", "openapi.json");
export const TYPES_PATH = resolve(ROOT, "src", "shared", "api.generated.ts");

const HEADER = `/**
 * Generated from backend/openapi.json by scripts/apiTypes.ts. Do not edit: run \`npm run api:types\`.
 * Types only; nothing here reaches the bundle.
 */
`;

/** The exact text of the types file for a description. Line feeds only, whatever the platform. */
export async function generateTypes(description: string): Promise<string> {
  const ast = await openapiTS(JSON.parse(description) as OpenAPI3);
  return (HEADER + astToString(ast)).replace(/\r\n?/g, "\n");
}

/** Whether a committed file is exactly what was generated, byte for byte. */
export function isCurrent(committed: string | null, generated: string): boolean {
  return committed === generated;
}

function readOrNull(path: string): string | null {
  try {
    return readFileSync(path, "utf8");
  } catch {
    return null;
  }
}

async function main(): Promise<void> {
  const generated = await generateTypes(readFileSync(DESCRIPTION_PATH, "utf8"));
  if (process.argv.includes("--check")) {
    if (!isCurrent(readOrNull(TYPES_PATH), generated)) {
      console.error(
        "FAIL: src/shared/api.generated.ts is not what backend/openapi.json generates. Run `npm run api:types`.",
      );
      process.exit(1);
    }
    console.log("OK: src/shared/api.generated.ts matches backend/openapi.json");
    return;
  }
  writeFileSync(TYPES_PATH, generated);
}

if (import.meta.main) {
  await main();
}
