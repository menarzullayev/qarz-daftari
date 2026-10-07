import { execFileSync, spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * The stack under test, as `e2e/stack.sh up` started it. Its settings are read from the env file that
 * script generated: throwaway values that exist on this machine only and are never committed.
 */
const ENV_FILE =
  process.env["E2E_ENV_FILE"] ?? resolve(import.meta.dirname, "../../deploy/production/.local-e2e/local.env");

function readEnvFile(): Map<string, string> {
  let text: string;
  try {
    text = readFileSync(ENV_FILE, "utf8");
  } catch {
    throw new Error(`the stack's env file is missing (${ENV_FILE}): run e2e/stack.sh up first`);
  }
  const values = new Map<string, string>();
  for (const line of text.split(/\r?\n/)) {
    const match = /^([A-Z_]+)=(.*)$/.exec(line);
    if (match?.[1] !== undefined && match[2] !== undefined) {
      values.set(match[1], match[2]);
    }
  }
  return values;
}

const env = readEnvFile();

function setting(name: string): string {
  const value = env.get(name);
  if (!value) {
    throw new Error(`${name} is not set in ${ENV_FILE}`);
  }
  return value;
}

export const stack = {
  /** Everything goes through the proxy, as a browser on the internet would. */
  baseURL: `https://127.0.0.1:${setting("DEPLOY_HTTPS_PORT")}`,
  /** A made-up token of the right shape: it signs what Telegram would sign, and reaches nobody. */
  botToken: setting("QD_BOT_TOKEN"),
  webhookSecret: setting("QD_WEBHOOK_SECRET"),
  adminTgIds: setting("QD_ADMIN_TG_IDS").split(",").map(Number),
  dbContainer: process.env["E2E_DB_CONTAINER"] ?? "qd-e2e-db-1",
};

const FIELD = "\u001f";

/**
 * Rows of one query, read as the owner of the database (so across every shop), in a read-only
 * transaction: the suite looks at what the application wrote and never writes beside it.
 */
export function sql(query: string): string[][] {
  const output = execFileSync(
    "docker",
    [
      "exec", "-i", "-e", "PGOPTIONS=-c default_transaction_read_only=on", stack.dbContainer,
      "psql", "-U", "postgres", "-d", "qarz", "-AtX", "-F", FIELD, "-v", "ON_ERROR_STOP=1", "-f", "-",
    ],
    { input: query, encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] },
  );
  return output
    .split(/\r?\n/)
    .filter((line) => line !== "")
    .map((line) => line.split(FIELD));
}

/** The single value of a query that answers one row with one column. */
export function sqlValue(query: string): string {
  const rows = sql(query);
  if (rows.length !== 1 || rows[0]?.length !== 1) {
    throw new Error(`expected one value, got ${rows.length} rows: ${query}`);
  }
  return rows[0][0] ?? "";
}

/** A text as an SQL literal. */
export function lit(value: string | number): string {
  return `'${String(value).replaceAll("'", "''")}'`;
}

/** Polls a query until `ready` accepts its rows; for what the worker does on its own schedule. */
export async function waitForRows(
  query: string,
  ready: (rows: string[][]) => boolean,
  timeoutMs = 30_000,
): Promise<string[][]> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const rows = sql(query);
    if (ready(rows)) {
      return rows;
    }
    if (Date.now() > deadline) {
      throw new Error(`the database did not reach the expected state in ${timeoutMs} ms: ${query}`);
    }
    await new Promise((done) => setTimeout(done, 300));
  }
}

/** Everything a service of the stack has written to its log so far (both of its streams). */
export function serviceLog(service: "proxy" | "api" | "worker"): string {
  const container = stack.dbContainer.replace(/db-1$/, `${service}-1`);
  const result = spawnSync("docker", ["logs", container], { encoding: "utf8", maxBuffer: 256 * 1024 * 1024 });
  if (result.status !== 0) {
    throw new Error(`docker logs ${container} failed`);
  }
  return result.stdout + result.stderr;
}
