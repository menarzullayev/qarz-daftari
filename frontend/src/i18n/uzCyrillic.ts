/**
 * Uzbek written in Cyrillic, made from Uzbek written in Latin: the `uz-Cyrl` texts are not typed.
 *
 * One deterministic pass over a text. It is the twin of `backend/src/qarz/domain/uz_cyrillic.py`; both
 * are held to the same cases and the same tables by `backend/tests/data/uz_cyrillic_cases.json`.
 *
 * What is left exactly as it is:
 *
 * - placeholders (`{name}`), links, e-mail addresses, `@names`, bot commands (`/obuna`), markup tags
 *   and file names;
 * - the product's name and other brands (`BRANDS`); an Uzbek ending after a brand is still written in
 *   Cyrillic: "Telegramda" becomes "Telegramда";
 * - codes: the ones in `CODES`, any word in capitals of two letters or more that is not a known Uzbek
 *   word ("SMS", "QR", "UZS"), and letters that touch a digit ("45k");
 * - everything that is not a Latin letter: digits, punctuation, currency signs, Cyrillic text.
 *
 * The rules are the 1995 Latin alphabet read back into the 1940 Cyrillic one. Where the Latin spelling
 * lost what Cyrillic keeps (the soft sign of "октябрь", the "ц" of "цирк"), the word is in `WORDS` or
 * `STEMS`; those tables are short and grow by review, never by guessing in the rules.
 *
 * No look-behind in the expressions: some web views that open the Mini App do not have it.
 */

/** Product and brand names, kept in Latin; an ending after one is still transliterated. */
export const BRANDS: readonly string[] = [
  "Anorbank",
  "Authenticator",
  "Click",
  "Eskiz",
  "Excel",
  "Google",
  "Humo",
  "Payme",
  "Telegram",
  "Uzcard",
  "Visa",
  "WebP",
];

/** The product's own name is two ordinary words; only together are they the name. */
export const PRODUCT = "Qarz Daftari";

/** Codes written in lower case too. Compared without regard to case. */
export const CODES: readonly string[] = [
  "api",
  "csv",
  "id",
  "jpeg",
  "jpg",
  "pdf",
  "png",
  "pwa",
  "qr",
  "runbook",
  "sms",
  "totp",
  "url",
  "usd",
  "utf",
  "uuid",
  "uzs",
  "webp",
  "xls",
  "xlsx",
];

/** Uzbek words that the texts write in capitals: these are words, not codes. */
export const UPPER_WORDS: readonly string[] = ["diqqat", "sinov"];

/** Whole words the rules get wrong: mostly the soft sign that the Latin spelling dropped. */
export const WORDS: Readonly<Record<string, string>> = {
  aprel: "апрель",
  dekabr: "декабрь",
  fevral: "февраль",
  iyul: "июль",
  iyun: "июнь",
  mebel: "мебель",
  nol: "ноль",
  noyabr: "ноябрь",
  oktabr: "октябрь",
  panel: "панель",
  parol: "пароль",
  rol: "роль",
  rubl: "рубль",
  sentabr: "сентябрь",
  sex: "цех",
  sirk: "цирк",
  yanvar: "январь",
};

/** Beginnings of words the rules get wrong; the rest of the word follows the rules. */
export const STEMS: Readonly<Record<string, string>> = {
  aksiya: "акция",
  albom: "альбом",
  filtr: "фильтр",
  film: "фильм",
  funksiya: "функция",
  intervyu: "интервью",
  kalkulator: "калькулятор",
  kompyuter: "компьютер",
  konsert: "концерт",
  kvitansiya: "квитанция",
  litsenziya: "лицензия",
  litsey: "лицей",
  mayor: "майор",
  "mo'jiza": "мўъжиза",
  "mo'tabar": "мўътабар",
  "mo'tadil": "мўътадил",
  obyekt: "объект",
  oktabr: "октябр",
  rayon: "район",
  sement: "цемент",
  sentabr: "сентябр",
  subyekt: "субъект",
};

const LETTERS: Readonly<Record<string, string>> = {
  a: "а",
  b: "б",
  d: "д",
  f: "ф",
  g: "г",
  h: "ҳ",
  i: "и",
  j: "ж",
  k: "к",
  l: "л",
  m: "м",
  n: "н",
  o: "о",
  p: "п",
  q: "қ",
  r: "р",
  s: "с",
  t: "т",
  u: "у",
  v: "в",
  x: "х",
  z: "з",
};
const Y_PAIRS: Readonly<Record<string, string>> = { a: "я", o: "ё", u: "ю" };
const VOWELS = "aeiou";
const APOSTROPHES = "'ʻʼ’‘`";
const EXTENSIONS = "xlsx|xls|csv|pdf|png|jpe?g|webp|json|txt|zip|md";
const DOMAINS = "uz|com|org|net|me|ru|io|app";
const NAME = "[A-Za-z0-9_]";

const escaped = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const PROTECTED = new RegExp(
  [
    "\\{[^{}]*\\}",
    "<[^<>]+>",
    'https?://[^\\s<>"«»]+',
    `[A-Za-z0-9_.+-]+@(?:${NAME}|-)+(?:\\.(?:${NAME}|-)+)+`,
    `@${NAME}+`,
    `(?:${NAME}|-)+(?:\\.(?:${NAME}|-)+)*\\.(?:${DOMAINS})(?!${NAME})(?:/[^\\s<>"«»]*)?`,
    // A bot command is taken together with the one character before it, which is never a Latin letter
    // and so is kept as it is anyway.
    `(?:^|[^A-Za-z0-9_/.])/[A-Za-z]${NAME}*`,
    `(?:(?:${NAME}|[-.])+/)*(?:${NAME}|-)*\\.(?:${EXTENSIONS})(?!${NAME})`,
    `${escaped(PRODUCT)}(?!${NAME})`,
  ].join("|"),
  "g",
);
// A word: Latin letters, with an apostrophe inside it or after "o" and "g".
const WORD = new RegExp(
  `(?:[oOgG][${APOSTROPHES}]|[A-Za-z])(?:[oOgG][${APOSTROPHES}]|[A-Za-z]|[${APOSTROPHES}](?=[A-Za-z]))*`,
  "g",
);
const ANY_APOSTROPHE = new RegExp(`[${APOSTROPHES}]`, "g");
const BY_LENGTH = (a: string, b: string) => b.length - a.length;
const BRANDS_LONGEST_FIRST = [...BRANDS].sort(BY_LENGTH);
const STEMS_LONGEST_FIRST = Object.keys(STEMS).sort(BY_LENGTH);

const isUpper = (text: string) => text === text.toUpperCase() && text !== text.toLowerCase();
const isLower = (text: string) => text === text.toLowerCase() && text !== text.toUpperCase();
const isDigit = (char: string) => char >= "0" && char <= "9" && char.length === 1;
const isApostrophe = (char: string) => char !== "" && APOSTROPHES.includes(char);

type Before = "v" | "c" | null;
const kind = (letter: string): Before => (VOWELS.includes(letter.toLowerCase()) ? "v" : "c");

/** `cyrillic` with the capitals of `latin`: all of them, the first one, or none. */
function cased(cyrillic: string, latin: string): string {
  if (latin.length > 1 && isUpper(latin)) {
    return cyrillic.toUpperCase();
  }
  if (isUpper(latin.slice(0, 1))) {
    return cyrillic.slice(0, 1).toUpperCase() + cyrillic.slice(1);
  }
  return cyrillic;
}

/** One letter or pair read as a whole: its first capital decides, the second only for a second letter. */
function unit(cyrillic: string, latin: string): string {
  if (!isUpper(latin.slice(0, 1))) {
    return cyrillic;
  }
  if (cyrillic.length === 1 || !isUpper(latin.slice(1, 2))) {
    return cyrillic.slice(0, 1).toUpperCase() + cyrillic.slice(1);
  }
  return cyrillic.toUpperCase();
}

/** `before` is what precedes: null at the start of a word, else "v" after a vowel, "c" after a consonant. */
function byRules(word: string, start: Before): string {
  let before = start;
  let out = "";
  const low = word.toLowerCase();
  const size = word.length;
  let i = 0;
  while (i < size) {
    const ch = low.charAt(i);
    const next = low.charAt(i + 1);
    const after = low.charAt(i + 2);
    if ((ch === "o" || ch === "g") && isApostrophe(next)) {
      out += unit(ch === "o" ? "ў" : "ғ", word.charAt(i));
      before = ch === "o" ? "v" : "c";
      i += 2;
    } else if ((ch === "s" || ch === "c") && next === "h") {
      out += unit(ch === "s" ? "ш" : "ч", word.slice(i, i + 2));
      before = "c";
      i += 2;
    } else if (ch === "y" && next !== "" && "aou".includes(next) && !(next === "o" && isApostrophe(after))) {
      out += unit(Y_PAIRS[next] ?? "", word.slice(i, i + 2));
      before = "v";
      i += 2;
    } else if (ch === "y" && next === "e") {
      out += unit(before === "c" ? "ье" : "е", word.slice(i, i + 2));
      before = "v";
      i += 2;
    } else if (ch === "e") {
      out += unit(before === "c" ? "е" : "э", word.charAt(i));
      before = "v";
      i += 1;
    } else if (ch === "t" && before === "v" && ["tsiya", "tsion"].includes(low.slice(i, i + 5))) {
      out += unit("ц", word.slice(i, i + 2));
      before = "c";
      i += 2;
    } else if (isApostrophe(ch)) {
      // Between "s" and "h" it only keeps the two letters apart ("Is'hoq"); elsewhere inside a word it
      // is the hard sign ("ma'lumot").
      if (!(low.charAt(i - 1) === "s" && next === "h") && i > 0 && next !== "") {
        out += "ъ";
      } else if (i === 0 || next === "") {
        out += word.charAt(i);
      }
      i += 1;
    } else if (ch === "y") {
      out += unit("й", word.charAt(i));
      before = "c";
      i += 1;
    } else if (Object.hasOwn(LETTERS, ch)) {
      out += unit(LETTERS[ch] ?? "", word.charAt(i));
      before = kind(ch);
      i += 1;
    } else {
      // A Latin letter Uzbek does not have ("w", a lone "c"): left as it is.
      out += word.charAt(i);
      before = "c";
      i += 1;
    }
  }
  return out;
}

function oneWord(word: string): string {
  const low = word.toLowerCase().replace(ANY_APOSTROPHE, "'");
  if (CODES.includes(low)) {
    return word;
  }
  for (const brand of BRANDS_LONGEST_FIRST) {
    if (word.startsWith(brand)) {
      const ending = word.slice(brand.length);
      if (ending === "" || isLower(ending)) {
        return brand + byRules(ending, kind(brand.slice(-1)));
      }
    }
  }
  if (word.length > 1 && isUpper(word) && !UPPER_WORDS.includes(low) && !low.includes("'")) {
    return word;
  }
  const whole = Object.hasOwn(WORDS, low) ? WORDS[low] : undefined;
  if (whole !== undefined) {
    return cased(whole, word);
  }
  for (const stem of STEMS_LONGEST_FIRST) {
    if (low.startsWith(stem)) {
      return cased(STEMS[stem] ?? "", word.slice(0, stem.length)) + byRules(word.slice(stem.length), kind(stem.slice(-1)));
    }
  }
  return byRules(word, null);
}

function plain(text: string): string {
  return text.replace(WORD, (word: string, offset: number) =>
    isDigit(text.charAt(offset - 1)) || isDigit(text.charAt(offset + word.length)) ? word : oneWord(word),
  );
}

/** The text in Uzbek Cyrillic. Text that is already Cyrillic comes back unchanged. */
export function toCyrillic(text: string): string {
  let out = "";
  let position = 0;
  for (const found of text.matchAll(PROTECTED)) {
    out += plain(text.slice(position, found.index)) + found[0];
    position = found.index + found[0].length;
  }
  return out + plain(text.slice(position));
}
