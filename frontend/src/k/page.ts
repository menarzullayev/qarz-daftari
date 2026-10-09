import type { Account, Entry, Fetch, Outcome } from "./account";
import { loadAccount, readToken } from "./account";
import {
  dayOfDate,
  dayOfInstant,
  dollars,
  type Language,
  LANGUAGES,
  type MessageKey,
  money,
  normalizeLanguage,
  say,
} from "./messages";

/**
 * The page behind a customer's read-only link, drawn with the DOM itself: no framework, so that a
 * customer's phone downloads a few kilobytes and not the staff application.
 *
 * Every piece of text reaches the page through `textContent`. Nothing the server sends, and nothing in
 * the address, is ever parsed as markup.
 */

export const LANGUAGE_STORAGE_KEY = "qd.language";

type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;

export type PageOptions = {
  root: HTMLElement;
  /** The address's fragment, "#" included. */
  hash: string;
  fetch: Fetch;
  /** Where the reader's choice of language is kept; null when the browser refuses storage. */
  storage: Storage | null;
  /** The browser's own languages, most wanted first. */
  browserLanguages: readonly string[];
};

const KIND_KEYS: Readonly<Record<string, MessageKey>> = {
  credit: "kind.credit",
  opening: "kind.opening",
  payment: "kind.payment",
  reversal: "kind.reversal",
};

function el<K extends keyof HTMLElementTagNameMap>(
  doc: Document,
  tag: K,
  className: string | null = null,
  text: string | null = null,
): HTMLElementTagNameMap[K] {
  const node = doc.createElement(tag);
  if (className !== null) {
    node.className = className;
  }
  if (text !== null) {
    node.textContent = text;
  }
  return node;
}

function storedLanguage(storage: Storage | null): Language | null {
  try {
    return normalizeLanguage(storage?.getItem(LANGUAGE_STORAGE_KEY));
  } catch {
    return null;
  }
}

/** The reader's own choice, else the first browser language the page speaks; null when neither says. */
export function knownLanguage(storage: Storage | null, browserLanguages: readonly string[]): Language | null {
  return (
    storedLanguage(storage) ??
    browserLanguages.map(normalizeLanguage).find((language): language is Language => language !== null) ??
    null
  );
}

function entryNode(doc: Document, language: Language, entry: Entry): HTMLElement {
  const item = el(doc, "li", entry.reversed ? "entry entry--reversed" : "entry");
  const head = el(doc, "div", "entry__head");
  head.append(
    el(doc, "span", "entry__kind", say(language, KIND_KEYS[entry.kind] ?? "kind.other")),
    el(
      doc,
      "span",
      entry.kind === "payment" ? "entry__amount entry__amount--paid" : "entry__amount",
      // An entry in dollars is written with its sign; its amount is cents, never read as so'm.
      entry.currency === "USD" ? dollars(entry.amount) : money(language, entry.amount),
    ),
  );
  item.append(head, el(doc, "p", "entry__meta", dayOfInstant(language, entry.createdAt)));
  if (entry.promisedDate !== null) {
    item.append(el(doc, "p", "entry__meta", say(language, "entry.promised", { date: dayOfDate(language, entry.promisedDate) })));
  }
  if (entry.reversed) {
    item.append(el(doc, "p", "entry__tag", say(language, "entry.reversed")));
  }
  if (entry.lines.length > 0) {
    const lines = el(doc, "ul", "entry__lines");
    for (const line of entry.lines) {
      lines.append(
        el(
          doc,
          "li",
          null,
          say(language, "line", {
            name: line.name,
            qty: line.qty,
            unit: line.unit,
            price: money(language, line.unitPrice),
            total: money(language, line.lineTotal),
          }),
        ),
      );
    }
    item.append(lines);
  }
  return item;
}

function accountNodes(doc: Document, language: Language, account: Account): HTMLElement[] {
  const shop = el(doc, "header", "shop");
  shop.append(el(doc, "p", "shop__name", account.shopName));
  if (account.shopPhone !== null) {
    const phone = el(doc, "p", "shop__phone", say(language, "shop.phone"));
    const call = el(doc, "a", null, account.shopPhone);
    // Only what is a phone number becomes a link to dial; anything else stays text.
    if (/^\+[0-9]{8,15}$/.test(account.shopPhone)) {
      call.href = `tel:${account.shopPhone}`;
    }
    phone.append(call);
    shop.append(phone);
  }

  const summary = el(doc, "section", "summary");
  const greeting =
    account.firstName === "" ? say(language, "greeting.noName") : say(language, "greeting", { name: account.firstName });
  summary.append(el(doc, "h1", "summary__greeting", greeting));
  // The so'm debt and, in a shop that works in dollars, the dollar debt: two amounts, each written by
  // itself and never added. A debt that is nothing is not written; "no debt" needs both to be nothing.
  const usd = account.usd;
  const each = (uzs: number, cents: number): string[] => [
    ...(uzs > 0 ? [money(language, uzs)] : []),
    ...(cents > 0 ? [dollars(cents)] : []),
  ];
  const owed = each(account.balance, usd?.balance ?? 0);
  const overpaid = each(-account.balance, -(usd?.balance ?? 0));
  if (owed.length > 0) {
    summary.append(el(doc, "p", "summary__label", say(language, "balance.owed")));
    summary.append(...owed.map((amount) => el(doc, "p", "summary__amount", amount)));
  }
  if (overpaid.length > 0) {
    summary.append(el(doc, "p", "summary__label", say(language, "balance.credit")));
    summary.append(...overpaid.map((amount) => el(doc, "p", "summary__amount summary__amount--clear", amount)));
  }
  if (owed.length === 0 && overpaid.length === 0) {
    summary.append(el(doc, "p", "summary__amount summary__amount--clear", say(language, "balance.none")));
  }
  for (const amount of each(account.overdue, usd?.overdue ?? 0)) {
    summary.append(el(doc, "p", "summary__overdue", say(language, "overdue", { amount })));
  }
  for (const amount of each(account.dueToday, usd?.dueToday ?? 0)) {
    summary.append(el(doc, "p", "summary__due", say(language, "dueToday", { amount })));
  }

  const entries = el(doc, "section", "entries");
  entries.append(el(doc, "h2", "entries__title", say(language, "entries.title")));
  if (account.entries.length === 0) {
    entries.append(el(doc, "p", "muted", say(language, "entries.empty")));
  } else {
    const items = el(doc, "ul", "entries__list");
    items.append(...account.entries.map((entry) => entryNode(doc, language, entry)));
    entries.append(items);
    if (account.entriesTotal > account.entries.length) {
      entries.append(
        el(doc, "p", "muted", say(language, "entries.shown", { shown: account.entries.length, total: account.entriesTotal })),
      );
    }
  }

  const foot = el(doc, "footer", "foot");
  foot.append(
    el(doc, "p", null, say(language, "foot.readOnly")),
    el(doc, "p", null, say(language, "foot.expires", { date: dayOfInstant(language, account.expiresAt) })),
    el(doc, "p", null, say(language, "foot.secret")),
  );
  return [shop, summary, entries, foot];
}

function messageNodes(doc: Document, language: Language, kind: "gone" | "limited" | "offline", onRetry: (() => void) | null) {
  const box = el(doc, "section", "message");
  box.setAttribute("role", "alert");
  box.append(el(doc, "h1", "message__title", say(language, `${kind}.title`)), el(doc, "p", null, say(language, `${kind}.body`)));
  if (onRetry !== null) {
    const retry = el(doc, "button", "button", say(language, "action.retry"));
    retry.type = "button";
    retry.addEventListener("click", onRetry);
    box.append(retry);
  }
  return [box];
}

function languageNodes(doc: Document, language: Language, onChoose: (language: Language) => void): HTMLElement {
  const nav = el(doc, "nav", "languages");
  nav.setAttribute("aria-label", say(language, "lang.choose"));
  for (const code of LANGUAGES) {
    const button = el(doc, "button", code === language ? "languages__item languages__item--current" : "languages__item");
    button.type = "button";
    button.lang = code;
    button.textContent = say(language, `lang.${code}`);
    button.setAttribute("aria-pressed", code === language ? "true" : "false");
    button.addEventListener("click", () => onChoose(code));
    nav.append(button);
  }
  return nav;
}

/**
 * Starts the page: reads the secret from the fragment, asks for the account, and draws what came of it.
 * Returns when the first answer has been drawn. The language can be changed afterwards without asking
 * the server again.
 */
export async function startPage(options: PageOptions): Promise<void> {
  const { root, storage } = options;
  const doc = root.ownerDocument;
  let chosen = knownLanguage(storage, options.browserLanguages);
  let outcome: Outcome | "loading" = "loading";

  const language = (): Language => {
    if (chosen !== null) {
      return chosen;
    }
    // Nobody chose: the language the shop keeps for this customer, once it is known.
    return (outcome !== "loading" && outcome.status === "ok" ? normalizeLanguage(outcome.account.lang) : null) ?? "uz";
  };

  const draw = (): void => {
    const current = language();
    doc.documentElement.lang = current;
    doc.title = say(current, "page.title");
    const nodes: HTMLElement[] = [
      languageNodes(doc, current, (code) => {
        chosen = code;
        try {
          storage?.setItem(LANGUAGE_STORAGE_KEY, code);
        } catch {
          // Storage refused (private mode): the choice still holds for this page.
        }
        draw();
      }),
    ];
    if (outcome === "loading") {
      const waiting = el(doc, "p", "muted", say(current, "state.loading"));
      waiting.setAttribute("role", "status");
      nodes.push(waiting);
    } else if (outcome.status === "ok") {
      nodes.push(...accountNodes(doc, current, outcome.account));
    } else {
      // Asking again helps only when the request did not arrive; a link that is gone stays gone.
      nodes.push(...messageNodes(doc, current, outcome.status, outcome.status === "gone" ? null : () => void load()));
    }
    root.replaceChildren(...nodes);
  };

  const load = async (): Promise<void> => {
    const token = readToken(options.hash);
    if (token === null) {
      // Not a secret at all: nothing is asked of the server.
      outcome = { status: "gone" };
      draw();
      return;
    }
    outcome = "loading";
    draw();
    outcome = await loadAccount(options.fetch, token);
    draw();
  };

  await load();
}
