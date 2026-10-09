import { type FormEvent, useState } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { ApiError } from "../api";
import { tashkentDay } from "../format";
import { useSubmit } from "../hooks";
import { addDays, compareDays, parseIsoDate, toIsoDate } from "../promise";
import { useWorkspace } from "../workspace/context";
import { amountMessage } from "../workspace/EntryScreen";
import { errorText, FieldError } from "../workspace/parts";
import { money, readAmount } from "./amounts";
import {
  type CashApi,
  type CashCategory,
  type CashCurrency,
  type CashEntry,
  type Direction,
  type Method,
  METHODS,
  type NewCashEntry,
} from "./cashApi";

export const MAX_NOTE_LENGTH = 200;
/** How far back an entry may be dated (backend/src/qarz/domain/cash.py, `BACKDATE_DAYS`). */
export const BACKDATE_DAYS = 31;

type Problems = { amount: string | null; category: string | null; note: string | null; day: string | null };
const NONE: Problems = { amount: null, category: null, note: null, day: null };

const DAY_PROBLEMS = ["TOO_OLD", "IN_FUTURE"] as const;

/** Places a server refusal next to the field it is about. */
function refused(error: ApiError | null, t: Translate, currency: CashCurrency): Problems {
  if (error === null) {
    return NONE;
  }
  if (error.code === "CASH_CATEGORY_ARCHIVED") {
    return { ...NONE, category: errorText(error, t) };
  }
  if (error.code !== "VALIDATION") {
    return NONE;
  }
  const day = DAY_PROBLEMS.find((problem) => problem === error.fields["day"]);
  return {
    amount: "amount" in error.fields || "currency" in error.fields ? amountMessage("invalid", t, currency) : null,
    category: "category_id" in error.fields ? t("cash.form.category.refused") : null,
    note: "note" in error.fields ? t("cash.form.note.tooLong", { max: MAX_NOTE_LENGTH }) : null,
    day: day ? t(`cash.form.day.${day}`, { days: BACKDATE_DAYS }) : null,
  };
}

/**
 * One income or one expense. The amount is in the chosen currency's own unit; the category is one of
 * the shop's that still takes entries, of this direction; the day is today unless another is chosen.
 */
export function CashEntryForm({
  calls,
  direction,
  categories,
  currencies,
  day,
  onSaved,
  onCancel,
}: {
  calls: CashApi;
  direction: Direction;
  categories: readonly CashCategory[];
  /** The currencies an entry may be written in now; a choice is offered only when there are two. */
  currencies: readonly CashCurrency[];
  /** The day the form opens on ("YYYY-MM-DD"), when it is one an entry may be dated. */
  day: string;
  onSaved: (entry: CashEntry) => void;
  onCancel: () => void;
}) {
  const { now } = useWorkspace();
  const { t, language } = useI18n();
  const today = tashkentDay(now());
  const oldest = addDays(today, -BACKDATE_DAYS);
  const opened = parseIsoDate(day);
  const usable = opened !== null && compareDays(opened, oldest) >= 0 && compareDays(opened, today) <= 0;
  const choices = categories.filter((category) => category.direction === direction && !category.archived && !category.fixed);

  const [amountText, setAmountText] = useState("");
  const [currency, setCurrency] = useState<CashCurrency>(currencies[0] ?? "UZS");
  const [method, setMethod] = useState<Method>("cash");
  const [categoryId, setCategoryId] = useState(choices.length === 1 ? (choices[0]?.id ?? "") : "");
  const [note, setNote] = useState("");
  const [dated, setDated] = useState(usable ? day : toIsoDate(today));
  const [problems, setProblems] = useState<Problems>(NONE);
  const { state, submit } = useSubmit((entry: NewCashEntry, key) => calls.record(entry, key).then(onSaved));

  const clear = (field: keyof Problems) => setProblems((current) => ({ ...current, [field]: null }));
  const parsed = readAmount(amountText, currency);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const amount = readAmount(amountText, currency);
    const cleanNote = note.split(/\s+/u).filter(Boolean).join(" ");
    const chosen = parseIsoDate(dated);
    let dayProblem: string | null = null;
    if (chosen === null || compareDays(chosen, today) > 0) {
      dayProblem = t("cash.form.day.IN_FUTURE");
    } else if (compareDays(chosen, oldest) < 0) {
      dayProblem = t("cash.form.day.TOO_OLD", { days: BACKDATE_DAYS });
    }
    const found: Problems = {
      amount: amount.ok ? null : amountMessage(amount.problem, t, currency),
      category: categoryId === "" ? t("cash.form.category.required") : null,
      note: [...cleanNote].length > MAX_NOTE_LENGTH ? t("cash.form.note.tooLong", { max: MAX_NOTE_LENGTH }) : null,
      day: dayProblem,
    };
    setProblems(found);
    if (!amount.ok || found.category !== null || found.note !== null || found.day !== null) {
      return;
    }
    submit({
      direction,
      method,
      currency,
      amount: amount.amount,
      categoryId,
      note: cleanNote === "" ? null : cleanNote,
      // Today is the server's to say: a form left open past midnight must not date its entry yesterday.
      day: dated === toIsoDate(today) ? null : dated,
    });
  };

  const failure = state.status === "error" ? state.error : null;
  const fromServer = refused(failure, t, currency);
  const shown: Problems = {
    amount: problems.amount ?? fromServer.amount,
    category: problems.category ?? fromServer.category,
    note: problems.note ?? fromServer.note,
    day: problems.day ?? fromServer.day,
  };
  const placed = Object.values(fromServer).some((problem) => problem !== null);
  const pending = state.status === "pending";
  const title = t(`cash.form.${direction}`);

  if (choices.length === 0) {
    return (
      <div className="notice" role="group" aria-label={title}>
        <p>{t("cash.form.category.none")}</p>
        <p className="actions">
          <button type="button" className="button" onClick={onCancel}>
            {t("action.cancel")}
          </button>
        </p>
      </div>
    );
  }

  return (
    <form className="form" onSubmit={onSubmit} noValidate aria-label={title}>
      <h3>{title}</h3>
      {failure && !placed ? (
        <p className="notice notice--error" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="cash-amount">{t("cash.form.amount")}</label>
        <input
          id="cash-amount"
          className="input input--amount"
          inputMode={currency === "UZS" ? "numeric" : "decimal"}
          autoComplete="off"
          value={amountText}
          aria-invalid={shown.amount !== null}
          aria-describedby="cash-amount-error cash-amount-hint"
          onChange={(event) => {
            setAmountText(event.target.value);
            clear("amount");
          }}
        />
        <p className="field__hint" id="cash-amount-hint">
          {parsed.ok
            ? money(parsed.amount, currency, language)
            : t(currency === "UZS" ? "entry.amount.hint" : "cash.form.amount.hintUSD")}
        </p>
        <FieldError id="cash-amount-error" message={shown.amount} />
      </div>
      {currencies.length > 1 ? (
        <div className="field">
          <label htmlFor="cash-currency">{t("cash.form.currency")}</label>
          <select
            id="cash-currency"
            className="input"
            value={currency}
            onChange={(event) => {
              setCurrency(currencies.find((code) => code === event.target.value) ?? "UZS");
              clear("amount");
            }}
          >
            {currencies.map((code) => (
              <option key={code} value={code}>
                {t(`cash.currency.${code}`)}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <div className="field">
        <label htmlFor="cash-method">{t("cash.form.method")}</label>
        <select
          id="cash-method"
          className="input"
          value={method}
          onChange={(event) => setMethod(METHODS.find((way) => way === event.target.value) ?? "cash")}
        >
          {METHODS.map((way) => (
            <option key={way} value={way}>
              {t(`cash.method.${way}`)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="cash-category">{t("cash.form.category")}</label>
        <select
          id="cash-category"
          className="input"
          value={categoryId}
          aria-invalid={shown.category !== null}
          aria-describedby="cash-category-error"
          onChange={(event) => {
            setCategoryId(event.target.value);
            clear("category");
          }}
        >
          <option value="">{t("cash.form.category.choose")}</option>
          {choices.map((category) => (
            <option key={category.id} value={category.id}>
              {category.name}
            </option>
          ))}
        </select>
        <FieldError id="cash-category-error" message={shown.category} />
      </div>
      <div className="field">
        <label htmlFor="cash-note">{t("cash.form.note")}</label>
        <input
          id="cash-note"
          className="input"
          value={note}
          autoComplete="off"
          aria-invalid={shown.note !== null}
          aria-describedby="cash-note-error"
          onChange={(event) => {
            setNote(event.target.value);
            clear("note");
          }}
        />
        <FieldError id="cash-note-error" message={shown.note} />
      </div>
      <div className="field">
        <label htmlFor="cash-entry-day">{t("cash.form.day")}</label>
        <input
          id="cash-entry-day"
          type="date"
          className="input"
          value={dated}
          min={toIsoDate(oldest)}
          max={toIsoDate(today)}
          aria-invalid={shown.day !== null}
          aria-describedby="cash-entry-day-error cash-entry-day-hint"
          onChange={(event) => {
            setDated(event.target.value);
            clear("day");
          }}
        />
        <p className="field__hint" id="cash-entry-day-hint">
          {t("cash.form.day.hint", { days: BACKDATE_DAYS })}
        </p>
        <FieldError id="cash-entry-day-error" message={shown.day} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t(`cash.form.submit.${direction}`)}
        </button>
        <button type="button" className="button" onClick={onCancel} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </form>
  );
}
