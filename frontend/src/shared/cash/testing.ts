/**
 * Answers of the cash book as the server writes them (backend/src/qarz/interface/cash_answers.py), for
 * the tests of the cash screens. Not part of any build.
 */

type Body = Record<string, unknown>;

export function category(direction: string, name: string, overrides: Body = {}) {
  return { id: `cat-${direction}-${name}`, direction, name, archived: false, fixed: false, ...overrides };
}

/** The default set a shop starts with, shortened: two of income (one the ledger's), two of expense. */
export const CATEGORIES = [
  category("income", "Qarz qaytdi", { fixed: true }),
  category("income", "Savdo"),
  category("expense", "Ijara"),
  category("expense", "Transport"),
];

export function categoriesBody(overrides: Body = {}) {
  return { items: CATEGORIES, currencies: ["UZS"], ...overrides };
}

export function entry(overrides: Body = {}) {
  return {
    id: "e-1",
    direction: "income",
    method: "card",
    currency: "UZS",
    amount: 120000,
    category: { id: "cat-income-Savdo", name: "Savdo" },
    note: null,
    day: "2026-10-06",
    created_at: "2026-10-06T06:30:00+00:00",
    author_id: "m-1",
    source: "manual",
    customer: null,
    cancelled: null,
    ...overrides,
  };
}

export function line(method: string, overrides: Body = {}) {
  return { currency: "UZS", method, opening: 0, income: 0, expense: 0, closing: 0, count: 0, ...overrides };
}

export function total(overrides: Body = {}) {
  return { currency: "UZS", opening: 0, income: 0, expense: 0, closing: 0, count: 0, ...overrides };
}

/** Tuesday 6 October 2026: cash opened with 100 000, took in 500 000, paid out 300 000; the card took in 120 000. */
export function dayBody(overrides: Body = {}) {
  return {
    date: "2026-10-06",
    balances: [
      line("cash", { opening: 100000, income: 500000, expense: 300000, closing: 300000, count: 2 }),
      line("card", { income: 120000, closing: 120000, count: 1 }),
      line("transfer"),
    ],
    totals: [total({ opening: 100000, income: 620000, expense: 300000, closing: 420000, count: 3 })],
    entries: [
      entry(),
      entry({
        id: "e-2",
        direction: "expense",
        method: "cash",
        amount: 300000,
        category: { id: "cat-expense-Ijara", name: "Ijara" },
        note: "Oktabr uchun",
        created_at: "2026-10-06T05:00:00+00:00",
      }),
      entry({ id: "e-3", method: "cash", amount: 500000, created_at: "2026-10-06T04:00:00+00:00" }),
    ],
    next_cursor: null,
    ...overrides,
  };
}

export function summaryBody(overrides: Body = {}) {
  return {
    from: "2026-10-01",
    to: "2026-10-06",
    balances: [
      line("cash", { opening: 100000, income: 700000, expense: 300000, closing: 500000, count: 3 }),
      line("card"),
      line("transfer"),
    ],
    totals: [total({ opening: 100000, income: 700000, expense: 300000, closing: 500000, count: 3 })],
    categories: [
      { category: category("income", "Savdo"), currency: "UZS", amount: 700000, count: 2 },
      { category: category("expense", "Ijara"), currency: "UZS", amount: 300000, count: 1 },
    ],
    days: [{ date: "2026-10-05", currency: "UZS", income: 700000, expense: 300000 }],
    ...overrides,
  };
}
