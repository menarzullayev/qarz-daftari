/**
 * Bundle budget (NFR-010): the staff Mini App's first load must stay at or under 300 KB compressed.
 *
 * "First load" here is what the browser fetches before the application can start: every script,
 * preloaded module, and style sheet referenced by dist/app/index.html, each gzip-compressed and summed.
 * Code loaded later through a dynamic import is not referenced there and is not counted. Telegram's own
 * script is loaded from telegram.org, is not part of the build, and is listed but not counted.
 *
 * The page behind a customer's read-only link (/k/) has a budget of its own, far smaller: it is opened
 * by a shop's customer on a phone, and must never come to carry the staff application. 20 KB compressed
 * holds it to its own few modules and the design tokens.
 *
 * Usage: `npm run size` (builds first). `node scripts/size.ts --budget-kb=1` after a build shows the
 * check failing; that is the negative check for this script. `--customer-page-budget-kb=1` does the
 * same for the customer's page.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gzipSync } from "node:zlib";

export const DEFAULT_BUDGET_KB = 300;
export const CUSTOMER_PAGE_BUDGET_KB = 20;
const BYTES_PER_KB = 1024;

export type InitialAssets = {
  /** Paths as written in the HTML, served by this build. */
  local: string[];
  /** Absolute URLs on other hosts; not part of the build. */
  external: string[];
};

function attribute(tag: string, name: string): string | null {
  const match = new RegExp(`\\s${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`, "i").exec(tag);
  return match ? (match[1] ?? match[2] ?? null) : null;
}

/** Scripts, preloaded modules, and style sheets an HTML page asks for on first load. */
export function collectInitialAssets(html: string): InitialAssets {
  const urls: string[] = [];
  for (const [tag] of html.matchAll(/<script\b[^>]*>/gi)) {
    const src = attribute(tag, "src");
    if (src) {
      urls.push(src);
    }
  }
  for (const [tag] of html.matchAll(/<link\b[^>]*>/gi)) {
    const rel = (attribute(tag, "rel") ?? "").toLowerCase().split(/\s+/);
    const href = attribute(tag, "href");
    if (href && (rel.includes("stylesheet") || rel.includes("modulepreload"))) {
      urls.push(href);
    }
  }
  const unique = [...new Set(urls)];
  const isExternal = (url: string) => /^(?:[a-z][a-z0-9+.-]*:)?\/\//i.test(url);
  return {
    local: unique.filter((url) => !isExternal(url)),
    external: unique.filter(isExternal),
  };
}

export type BudgetResult = { totalBytes: number; budgetBytes: number; withinBudget: boolean };

export function checkBudget(sizes: readonly number[], budgetKb: number): BudgetResult {
  if (!Number.isFinite(budgetKb) || budgetKb <= 0) {
    throw new RangeError("budget must be a positive number of KB");
  }
  const totalBytes = sizes.reduce((sum, size) => sum + size, 0);
  const budgetBytes = budgetKb * BYTES_PER_KB;
  return { totalBytes, budgetBytes, withinBudget: totalBytes <= budgetBytes };
}

export function parseBudgetKb(args: readonly string[], name = "--budget-kb", fallback = DEFAULT_BUDGET_KB): number {
  const option = args.find((arg) => arg.startsWith(`${name}=`));
  if (option === undefined) {
    return fallback;
  }
  const value = Number(option.slice(name.length + 1));
  if (!Number.isFinite(value) || value <= 0) {
    throw new RangeError(`invalid budget: ${option}`);
  }
  return value;
}

function kb(bytes: number): string {
  return (bytes / BYTES_PER_KB).toFixed(2);
}

function measureEntry(distDir: string, entry: string): { totalBytes: number; lines: string[] } {
  const html = readFileSync(resolve(distDir, entry, "index.html"), "utf8");
  const assets = collectInitialAssets(html);
  if (assets.local.length === 0) {
    throw new Error(`${entry}: no scripts or style sheets found in index.html; is the build output intact?`);
  }
  const lines: string[] = [];
  let totalBytes = 0;
  for (const url of assets.local) {
    const size = gzipSync(readFileSync(resolve(distDir, url.replace(/^\//, ""))), { level: 9 }).length;
    totalBytes += size;
    lines.push(`    ${kb(size).padStart(8)} KB  ${url}`);
  }
  for (const url of assets.external) {
    lines.push(`    not counted  ${url}`);
  }
  return { totalBytes, lines };
}

function main(): void {
  const budgetKb = parseBudgetKb(process.argv.slice(2));
  const distDir = resolve(import.meta.dirname, "..", "dist");
  const customerPageBudgetKb = parseBudgetKb(process.argv.slice(2), "--customer-page-budget-kb", CUSTOMER_PAGE_BUDGET_KB);
  let appBytes = 0;
  let customerPageBytes = 0;
  for (const entry of ["app", "panel", "admin", "k"]) {
    const { totalBytes, lines } = measureEntry(distDir, entry);
    console.log(`${entry}: ${kb(totalBytes)} KB gzip (initial JS + CSS)`);
    console.log(lines.join("\n"));
    if (entry === "app") {
      appBytes = totalBytes;
    }
    if (entry === "k") {
      customerPageBytes = totalBytes;
    }
  }
  const result = checkBudget([appBytes], budgetKb);
  if (!result.withinBudget) {
    console.error(`FAIL: staff Mini App first load is ${kb(result.totalBytes)} KB gzip, budget is ${budgetKb} KB`);
    process.exit(1);
  }
  console.log(`OK: staff Mini App first load is ${kb(result.totalBytes)} KB gzip, budget is ${budgetKb} KB`);
  const customerPage = checkBudget([customerPageBytes], customerPageBudgetKb);
  if (!customerPage.withinBudget) {
    console.error(`FAIL: the customer's page is ${kb(customerPage.totalBytes)} KB gzip, budget is ${customerPageBudgetKb} KB`);
    process.exit(1);
  }
  console.log(`OK: the customer's page is ${kb(customerPage.totalBytes)} KB gzip, budget is ${customerPageBudgetKb} KB`);
}

if (import.meta.main) {
  main();
}
