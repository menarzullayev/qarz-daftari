import { createHash, createHmac, randomBytes } from "node:crypto";

/**
 * What Telegram would hand the application, signed with the stack's bot token. The algorithms are the
 * ones `backend/src/qarz/domain/telegram_auth.py` verifies (and `backend/tests/api/conftest.py` signs
 * with): every field except `hash`, sorted, joined by newlines as `key=value`, then HMAC-SHA256.
 */
export type Person = {
  /** Telegram user identifier. */
  id: number;
  firstName: string;
  languageCode?: string;
};

let sequence = 0;

/** A person nobody has seen before: every run of the suite starts with new people. */
export function newPerson(firstName: string): Person {
  sequence += 1;
  const id = 7_000_000_000 + (randomBytes(4).readUInt32BE(0) % 900_000_000) + sequence;
  return { id, firstName, languageCode: "uz" };
}

function checkString(fields: Record<string, string>): string {
  return Object.keys(fields)
    .sort()
    .map((key) => `${key}=${fields[key]}`)
    .join("\n");
}

/**
 * Mini App launch data (`Telegram.WebApp.initData`). The key is HMAC("WebAppData", bot token). A new
 * `query_id` each time, as Telegram gives each launch: the server accepts one signed payload once.
 */
export function webAppInitData(botToken: string, person: Person, at: Date = new Date()): string {
  const fields: Record<string, string> = {
    auth_date: String(Math.floor(at.getTime() / 1000)),
    query_id: `AAE${randomBytes(12).toString("base64url")}`,
    user: JSON.stringify({ id: person.id, first_name: person.firstName, language_code: person.languageCode ?? "uz" }),
  };
  const secret = createHmac("sha256", "WebAppData").update(botToken).digest();
  const hash = createHmac("sha256", secret).update(checkString(fields)).digest("hex");
  return new URLSearchParams({ ...fields, hash }).toString();
}

/**
 * The fields the Login widget returns for a person who confirmed in Telegram. The key is the SHA-256 of
 * the bot token. In redirect mode Telegram puts exactly these in the query string, so all are strings.
 */
export function loginWidgetFields(botToken: string, person: Person, at: Date = new Date()): Record<string, string> {
  const fields: Record<string, string> = {
    id: String(person.id),
    first_name: person.firstName,
    // Unique per confirmation, so that two sign-ins in one second are two payloads.
    username: `e2e_${randomBytes(6).toString("hex")}`,
    auth_date: String(Math.floor(at.getTime() / 1000)),
  };
  const secret = createHash("sha256").update(botToken).digest();
  const hash = createHmac("sha256", secret).update(checkString(fields)).digest("hex");
  return { ...fields, hash };
}

const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function base32Decode(text: string): Buffer {
  let bits = "";
  for (const char of text.replace(/=+$/, "").toUpperCase()) {
    const value = BASE32.indexOf(char);
    if (value < 0) {
      throw new Error("not base32");
    }
    bits += value.toString(2).padStart(5, "0");
  }
  const bytes: number[] = [];
  for (let offset = 0; offset + 8 <= bits.length; offset += 8) {
    bytes.push(Number.parseInt(bits.slice(offset, offset + 8), 2));
  }
  return Buffer.from(bytes);
}

/** The code an authenticator application shows for an `otpauth://totp/...` address (RFC 6238). */
export function totpCode(otpauthUri: string, at: Date = new Date()): string {
  const parameters = new URL(otpauthUri).searchParams;
  const secret = parameters.get("secret");
  if (!secret) {
    throw new Error("the otpauth address carries no secret");
  }
  const digits = Number(parameters.get("digits") ?? "6");
  const period = Number(parameters.get("period") ?? "30");
  const algorithm = (parameters.get("algorithm") ?? "SHA1").toLowerCase();
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at.getTime() / 1000 / period)));
  const digest = createHmac(algorithm, base32Decode(secret)).update(counter).digest();
  const offset = (digest.at(-1) ?? 0) & 0x0f;
  const value = digest.readUInt32BE(offset) & 0x7fffffff;
  return String(value % 10 ** digits).padStart(digits, "0");
}

/** A person with a Telegram identifier that is given, such as one on the administrators' allow-list. */
export function personWithId(id: number, firstName: string): Person {
  return { id, firstName, languageCode: "uz" };
}
