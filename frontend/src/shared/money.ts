/**
 * Money is whole UZS (REQ-N06). The server is the authority on amounts; the client only parses what a
 * person typed and formats what the server returned.
 */

export const MIN_AMOUNT = 100;
export const MAX_AMOUNT = 100_000_000;
const MAX_INPUT_LENGTH = 500;

/** Formats whole UZS with thin spaces between thousands, for example 45 000. */
export function formatUzs(amount: number): string {
  if (!Number.isSafeInteger(amount)) {
    throw new RangeError("amount must be a whole number of UZS");
  }
  const sign = amount < 0 ? "-" : "";
  const digits = Math.abs(amount).toString();
  return sign + digits.replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
}

export type AmountProblem = "empty" | "invalid" | "not_whole" | "too_small" | "too_large";
export type AmountResult = { ok: true; amount: number } | { ok: false; problem: AmountProblem };

/*
 * The rules below are a port of the amount part of the chat parser, which is the authority:
 * backend/src/qarz/domain/chat_entry.py (`_split_number`, `_read_amount`). Keep the two in step; the
 * cases in money.test.ts are taken from backend/tests/test_chat_entry.py.
 *
 * The field holds an amount only, so three things the chat grammar allows around an amount are refused
 * here: a minus sign and a payment word (the form already says whether it is a sale or a payment), and
 * a note after the amount.
 */

// What Python's str.split() treats as white space; JavaScript's \s differs (it includes U+FEFF).
const SPACE = "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const SPACES = new RegExp(`[${SPACE}]+`, "u");
const OUTER_SPACES = new RegExp(`^[${SPACE}]+|[${SPACE}]+$`, "gu");
// Kept as a string: a regular expression with control characters is hard to read and to lint.
const LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029";
const APOSTROPHES = /['\u02bc\u2019\u2018\u02bb`]/gu;
const NUMERIC_HEAD = /^[0-9.,]*/;

const THOUSAND_WORDS: ReadonlySet<string> = new Set(["k", "ming", "тыс", "к", "минг"]);
const CURRENCY_WORDS: ReadonlySet<string> = new Set(["so'm", "som", "sum", "uzs", "сум", "сўм", "сом"]);

/** A token as a keyword candidate: one trailing "." or "," dropped, apostrophes unified, lower case. */
function word(token: string): string {
  const bare = token.endsWith(".") || token.endsWith(",") ? token.slice(0, -1) : token;
  return bare.replace(APOSTROPHES, "'").toLowerCase();
}

/** Splits a token into its numeric head, the word glued to it, and whether punctuation closed it. */
function splitNumber(token: string): { head: string; tail: string; closed: boolean } {
  let head = NUMERIC_HEAD.exec(token)?.[0] ?? "";
  const tail = word(token.slice(head.length));
  let closed = false;
  if (head.length === token.length && head.length > 1 && (head.endsWith(".") || head.endsWith(","))) {
    head = head.slice(0, -1);
    closed = true;
  }
  return { head, tail, closed };
}

const fail = (problem: AmountProblem): AmountResult => ({ ok: false, problem });

/**
 * Parses an amount as a seller types it: "45000", "45 000", "45.000", "45k", "45 ming", "45 тыс",
 * "45000 so'm". Decimals are refused, never rounded, and so is anything that could be read two ways.
 */
export function parseAmount(input: string): AmountResult {
  return parseWholeUzs(input, MIN_AMOUNT, MAX_AMOUNT);
}

/**
 * The same reading with other bounds: a unit price may be as small as 1 UZS, an entry amount may not.
 * `min` and `max` are whole UZS, inclusive.
 */
export function parseWholeUzs(input: string, min: number, max: number): AmountResult {
  if (input.length > MAX_INPUT_LENGTH) {
    return fail("invalid");
  }
  const text = input.replace(OUTER_SPACES, "");
  if (text === "") {
    return fail("empty");
  }
  if ([...text].some((character) => LINE_BREAKS.includes(character))) {
    return fail("invalid");
  }
  const tokens = text.split(SPACES);
  const first = tokens[0] ?? "";
  if (!/^[0-9]/.test(first)) {
    return fail("invalid");
  }

  const start = splitNumber(first);
  if (start.head.includes(",")) {
    return fail("not_whole");
  }
  const groups = start.head.split(".");
  if (groups.some((group) => group === "")) {
    return fail("invalid");
  }
  const leading = groups[0] ?? "";
  if (groups.length > 1 && (leading.length > 3 || leading.startsWith("0") || groups.slice(1).some((g) => g.length !== 3))) {
    return fail("not_whole"); // "45.5", "45.00", "0.500": a decimal, not thousand groups
  }

  // Thousand groups separated by spaces: "45 000", "1 250 000", also mixed as "1 250.000".
  let { tail, closed } = start;
  let index = 1;
  const openGroup = leading.length <= 3 && !leading.startsWith("0");
  while (openGroup && tail === "" && !closed && index < tokens.length) {
    const next = splitNumber(tokens[index] ?? "");
    const more = next.head.split(".");
    if (next.head.includes(",") || more.some((group) => group.length !== 3)) {
      break;
    }
    groups.push(...more);
    tail = next.tail;
    closed = next.closed;
    index += 1;
  }

  let multiplier = 1;
  let currencySeen = false;
  if (tail !== "") {
    if (THOUSAND_WORDS.has(tail)) {
      multiplier = 1000;
    } else if (CURRENCY_WORDS.has(tail)) {
      currencySeen = true;
    } else {
      return fail("invalid"); // "45000abc", "1e5"
    }
  } else if (index < tokens.length && THOUSAND_WORDS.has(word(tokens[index] ?? ""))) {
    multiplier = 1000;
    index += 1;
  }
  if (!currencySeen && index < tokens.length && CURRENCY_WORDS.has(word(tokens[index] ?? ""))) {
    index += 1;
  }
  if (index < tokens.length) {
    return fail("invalid"); // a second number, a payment word, or a note
  }

  // The digits may be far longer than a safe integer; judge the length before converting.
  const digits = groups.join("").replace(/^0+/, "");
  if (digits.length > 12) {
    return fail("too_large");
  }
  const amount = Number(digits === "" ? "0" : digits) * multiplier;
  if (amount < min) {
    return fail("too_small");
  }
  if (amount > max) {
    return fail("too_large");
  }
  return { ok: true, amount };
}

/** The amount, or null for anything that is not a whole amount within the accepted range. */
export function parseUzs(input: string): number | null {
  const result = parseAmount(input);
  return result.ok ? result.amount : null;
}

/*
 * ---------- Currencies ----------
 *
 * A shop keeps its debts in so'm and, when its owner turned that on, in US dollars beside them: two
 * books, no exchange rate, and nothing anywhere is a sum of the two. This module is the one place in
 * the client that knows the currencies: their minor units, how an amount of each is written and read,
 * and the ranges the server accepts (backend/src/qarz/domain/money.py, `RULES`; keep the two in step).
 *
 * So'm are whole so'm. Dollars are whole cents end to end: 12.50 $ is 1250. No amount is ever held or
 * computed as a fraction, so 0.1 + 0.2 cannot happen here.
 */

export type Currency = "UZS" | "USD";

/** So'm first: the order the two are offered and shown in. */
export const CURRENCIES: readonly Currency[] = ["UZS", "USD"];

export type AmountRange = { min: number; max: number };

/** One entry, in the currency's minor unit: 100 to 100 000 000 so'm, 0.01 $ to 10 000.00 $. */
export const ENTRY_RANGE: Readonly<Record<Currency, AmountRange>> = {
  UZS: { min: MIN_AMOUNT, max: MAX_AMOUNT },
  USD: { min: 1, max: 1_000_000 },
};

/** A dollar credit limit in cents: 1.00 $ to 1 000 000.00 $. The so'm bounds come with the settings. */
export const USD_LIMIT_RANGE: AmountRange = { min: 100, max: 100_000_000 };

/** The currency of something the API tagged: so'm is the absence of the tag. */
export function currencyOf(tagged: { currency?: Currency | undefined }): Currency {
  return tagged.currency ?? "UZS";
}

const CENTS = 100;

function centsParts(cents: number): { sign: string; dollars: string; rest: string } {
  if (!Number.isSafeInteger(cents)) {
    throw new RangeError("a dollar amount must be a whole number of cents");
  }
  const size = Math.abs(cents);
  // Both are exact for a safe integer: no fraction is ever formed.
  const rest = size % CENTS;
  return { sign: cents < 0 ? "-" : "", dollars: ((size - rest) / CENTS).toString(), rest: rest.toString().padStart(2, "0") };
}

/** Cents as dollars with two decimals and thousands apart, without the sign: 125050 is "1 250.50". */
export function formatUsd(cents: number): string {
  const { sign, dollars, rest } = centsParts(cents);
  return `${sign}${dollars.replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0")}.${rest}`;
}

/** Cents as they are shown everywhere: "1 250.50 $". The spaces do not break, so an amount stays on one line. */
export function formatDollars(cents: number): string {
  return `${formatUsd(cents)}\u00a0$`;
}

/** Cents as an amount field holds them, in the form `parseUsd` reads back: 125050 is "1250.50". */
export function usdInput(cents: number): string {
  const { sign, dollars, rest } = centsParts(cents);
  return `${sign}${dollars}.${rest}`;
}

/** An amount without its currency word, as a range or a field shows it. */
export function formatAmount(amount: number, currency: Currency): string {
  return currency === "USD" ? formatUsd(amount) : formatUzs(amount);
}

/** What an amount field starts with when it is filled for the person. */
export function amountInput(amount: number, currency: Currency): string {
  return currency === "USD" ? usdInput(amount) : formatUzs(amount);
}

const USD_TEXT = /^(\d+)(?:[.,](\d+))?$/;
/** Ten digits of dollars are twelve of cents: far above every range, far below an unsafe integer. */
const MAX_DOLLAR_DIGITS = 10;

/**
 * Dollars as a person types them into an amount field, read into cents: digits, then at most one "."
 * or "," with one or two digits after it. "12", "12.5", "12,50" and "0.01" are amounts; a third
 * decimal is refused, never rounded, and so is everything else: a sign, a "$", a space or a second
 * separator inside the number, letters, "12." and ".5". `min` and `max` are cents, inclusive.
 *
 * The digits are read as text and joined as whole numbers, so no float takes part: "19.99" is 1999.
 */
export function parseUsd(input: string, min: number, max: number): AmountResult {
  if (input.length > MAX_INPUT_LENGTH) {
    return fail("invalid");
  }
  const text = input.replace(OUTER_SPACES, "");
  if (text === "") {
    return fail("empty");
  }
  const match = USD_TEXT.exec(text);
  if (!match) {
    return fail("invalid");
  }
  const fraction = match[2] ?? "";
  if (fraction.length > 2) {
    return fail("not_whole");
  }
  const dollars = (match[1] ?? "").replace(/^0+/, "");
  if (dollars.length > MAX_DOLLAR_DIGITS) {
    return fail("too_large");
  }
  const amount = Number(dollars === "" ? "0" : dollars) * CENTS + Number(fraction.padEnd(2, "0"));
  if (amount < min) {
    return fail("too_small");
  }
  if (amount > max) {
    return fail("too_large");
  }
  return { ok: true, amount };
}

/** What a person typed into an amount field, in the minor unit of the currency the field is in. */
export function parseMoney(input: string, currency: Currency, range: AmountRange = ENTRY_RANGE[currency]): AmountResult {
  return currency === "USD" ? parseUsd(input, range.min, range.max) : parseWholeUzs(input, range.min, range.max);
}
