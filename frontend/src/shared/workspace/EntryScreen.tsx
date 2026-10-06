import { useState, type FormEvent } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { ApiError, CustomerDetail, EntryKind, NewEntry, RecordedEntry } from "../api";
import { type CalendarDay, formatCalendarDay, formatMoney, tashkentDay } from "../format";
import { useLoad, useSubmit } from "../hooks";
import { type AmountProblem, formatUzs, MAX_AMOUNT, MIN_AMOUNT, parseAmount } from "../money";
import {
  addDays,
  MAX_PROMISE_DAYS,
  parseIsoDate,
  promiseDateProblem,
  QUICK_CHOICES,
  type QuickChoice,
  quickChoiceDate,
  toIsoDate,
} from "../promise";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { useWorkspace } from "./context";
import { errorText, Failure, FieldError, Loading } from "./parts";

export const MAX_NOTE_LENGTH = 200;

/** "default" sends no date, so the server applies the shop's usual term; "picked" sends the chosen date. */
export type PromiseChoice = "default" | QuickChoice | "picked";

const CHOICE_LABELS = {
  default: "promise.default",
  tomorrow: "promise.tomorrow",
  end_of_week: "promise.endOfWeek",
  in_two_weeks: "promise.inTwoWeeks",
  in_a_month: "promise.inAMonth",
  picked: "promise.pick",
} as const;

function amountMessage(problem: AmountProblem, t: Translate): string {
  switch (problem) {
    case "empty":
      return t("entry.amount.required");
    case "not_whole":
      return t("entry.amount.notWhole");
    case "too_small":
      return t("entry.amount.tooSmall", { min: formatUzs(MIN_AMOUNT) });
    case "too_large":
      return t("entry.amount.tooLarge", { max: formatUzs(MAX_AMOUNT) });
    case "invalid":
      return t("entry.amount.invalid");
  }
}

export type PromiseResult = { ok: true; date: string | null } | { ok: false; problem: "required" | "range" };

/** The promised date to send for a choice made on `today` (the Tashkent date of the sale). */
export function promisedDateFor(choice: PromiseChoice, picked: string, today: CalendarDay): PromiseResult {
  if (choice === "default") {
    return { ok: true, date: null };
  }
  if (choice !== "picked") {
    return { ok: true, date: toIsoDate(quickChoiceDate(choice, today)) };
  }
  const day = parseIsoDate(picked);
  if (day === null) {
    return { ok: false, problem: "required" };
  }
  return promiseDateProblem(today, day) === null ? { ok: true, date: picked } : { ok: false, problem: "range" };
}

type FieldErrors = { amount: string | null; note: string | null; promise: string | null };
const NO_ERRORS: FieldErrors = { amount: null, note: null, promise: null };

/** Places a server refusal next to the field it is about. */
function refusedFields(error: ApiError | null, t: Translate): FieldErrors {
  if (error === null) {
    return NO_ERRORS;
  }
  if (error.code === "EXCEEDS_BALANCE") {
    return { ...NO_ERRORS, amount: t("entry.amount.exceedsBalance") };
  }
  if (error.code !== "VALIDATION") {
    return NO_ERRORS;
  }
  return {
    amount: "amount" in error.fields ? amountMessage("invalid", t) : null,
    note: "note" in error.fields ? t("entry.note.tooLong", { max: MAX_NOTE_LENGTH }) : null,
    promise: "promised_date" in error.fields ? t("promise.range", { days: MAX_PROMISE_DAYS }) : null,
  };
}

function Recorded({ recorded, onAnother }: { recorded: RecordedEntry; onAnother: () => void }) {
  const { t, language } = useI18n();
  const { entry, customer } = recorded;
  const promised = entry.promisedDate === null ? null : parseIsoDate(entry.promisedDate);
  return (
    <div className="notice notice--done" role="status">
      <p>
        {t(entry.kind === "payment" ? "entry.done.payment" : "entry.done.credit", {
          name: customer.displayName,
          amount: formatMoney(entry.amount, language),
        })}
      </p>
      <p className="balance">
        <span>{t("customer.balance.new")}</span> <strong>{formatMoney(customer.balance, language)}</strong>
      </p>
      {promised ? <p>{t("entry.promised", { date: formatCalendarDay(promised, language) })}</p> : null}
      <p className="actions">
        <Link to={`/customers/${customer.id}`} className="button button--primary">
          {t("customer.open")}
        </Link>
        <button type="button" className="button" onClick={onAnother}>
          {t("entry.another")}
        </button>
        <Link to="/" className="button">
          {t("nav.overview")}
        </Link>
      </p>
    </div>
  );
}

function EntryForm({ customer, kind, onRecorded }: { customer: CustomerDetail; kind: EntryKind; onRecorded: () => void }) {
  const { api, now } = useWorkspace();
  const { t, language } = useI18n();
  const [amountText, setAmountText] = useState("");
  const [note, setNote] = useState("");
  const [choice, setChoice] = useState<PromiseChoice>("default");
  const [picked, setPicked] = useState("");
  const [errors, setErrors] = useState<FieldErrors>(NO_ERRORS);
  const { state, submit } = useSubmit((entry: NewEntry, key) => api.recordEntry(customer.id, entry, key));

  if (state.status === "done") {
    // Reloading the customer mounts a fresh form that starts from the new balance.
    return <Recorded recorded={state.result} onAnother={onRecorded} />;
  }

  const today = tashkentDay(now());
  const parsed = parseAmount(amountText);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const amount = parseAmount(amountText);
    const cleanNote = note.split(/\s+/u).filter(Boolean).join(" ");
    const promise: PromiseResult = kind === "credit" ? promisedDateFor(choice, picked, today) : { ok: true, date: null };
    const found: FieldErrors = {
      amount: amount.ok ? null : amountMessage(amount.problem, t),
      note: [...cleanNote].length > MAX_NOTE_LENGTH ? t("entry.note.tooLong", { max: MAX_NOTE_LENGTH }) : null,
      promise: promise.ok
        ? null
        : promise.problem === "required"
          ? t("promise.required")
          : t("promise.range", { days: MAX_PROMISE_DAYS }),
    };
    setErrors(found);
    if (amount.ok && promise.ok && found.note === null) {
      submit({ kind, amount: amount.amount, note: cleanNote === "" ? null : cleanNote, promisedDate: promise.date });
    }
  };

  const failure = state.status === "error" ? state.error : null;
  const refused = refusedFields(failure, t);
  const shown: FieldErrors = {
    amount: errors.amount ?? refused.amount,
    note: errors.note ?? refused.note,
    promise: errors.promise ?? refused.promise,
  };
  const pending = state.status === "pending";
  const clear = (field: keyof FieldErrors) => setErrors((current) => ({ ...current, [field]: null }));

  return (
    <form className="form" onSubmit={onSubmit} noValidate>
      <p className="balance">
        <span>{t("customer.balance")}</span> <strong>{formatMoney(customer.balance, language)}</strong>
      </p>
      {failure ? (
        <div className="notice notice--error" role="alert">
          <p>{errorText(failure, t)}</p>
          {failure.serverMessage === null ? <p>{t("entry.retrySafe")}</p> : null}
        </div>
      ) : null}

      <div className="field">
        <label htmlFor="entry-amount">{t("entry.amount")}</label>
        <input
          id="entry-amount"
          className="input input--amount"
          inputMode="numeric"
          autoComplete="off"
          value={amountText}
          aria-invalid={shown.amount !== null}
          aria-describedby="entry-amount-error entry-amount-hint"
          onChange={(event) => {
            setAmountText(event.target.value);
            clear("amount");
          }}
        />
        <p className="field__hint" id="entry-amount-hint">
          {parsed.ok ? formatMoney(parsed.amount, language) : t("entry.amount.hint")}
        </p>
        <FieldError id="entry-amount-error" message={shown.amount} />
        {kind === "payment" && customer.balance >= MIN_AMOUNT ? (
          <button type="button" className="button" onClick={() => setAmountText(formatUzs(customer.balance))}>
            {t("entry.payAll", { amount: formatMoney(customer.balance, language) })}
          </button>
        ) : null}
      </div>

      <div className="field">
        <label htmlFor="entry-note">{t("entry.note")}</label>
        <input
          id="entry-note"
          className="input"
          value={note}
          autoComplete="off"
          aria-invalid={shown.note !== null}
          aria-describedby="entry-note-error"
          onChange={(event) => {
            setNote(event.target.value);
            clear("note");
          }}
        />
        <FieldError id="entry-note-error" message={shown.note} />
      </div>

      {kind === "credit" ? (
        <fieldset className="field choices">
          <legend>{t("promise.legend")}</legend>
          {(["default", ...QUICK_CHOICES, "picked"] as const).map((option) => (
            <label key={option} className="choice">
              <input
                type="radio"
                name="promise"
                value={option}
                checked={choice === option}
                onChange={() => {
                  setChoice(option);
                  clear("promise");
                }}
              />
              <span>{t(CHOICE_LABELS[option])}</span>
              {option !== "default" && option !== "picked" ? (
                <span className="choice__date">{formatCalendarDay(quickChoiceDate(option, today), language)}</span>
              ) : null}
            </label>
          ))}
          {choice === "picked" ? (
            <input
              type="date"
              className="input"
              value={picked}
              min={toIsoDate(today)}
              max={toIsoDate(addDays(today, MAX_PROMISE_DAYS))}
              aria-label={t("promise.pick")}
              aria-invalid={shown.promise !== null}
              aria-describedby="entry-promise-error"
              onChange={(event) => {
                setPicked(event.target.value);
                clear("promise");
              }}
            />
          ) : null}
          <FieldError id="entry-promise-error" message={shown.promise} />
        </fieldset>
      ) : null}

      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t(kind === "credit" ? "entry.credit.submit" : "entry.payment.submit")}
        </button>
        <Link to={`/customers/${customer.id}`} className="button">
          {t("action.cancel")}
        </Link>
      </p>
    </form>
  );
}

/** Records a credit sale or a payment for one customer and shows the balance the server answers with. */
export function EntryScreen({ customerId, kind }: { customerId: string; kind: EntryKind }) {
  const { api } = useWorkspace();
  const { state, reload } = useLoad((signal) => api.readCustomer(customerId, signal), [api, customerId]);

  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return state.error.code === "NOT_FOUND" ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  const customer = state.data;
  return (
    <>
      <h2 className="subject">{customer.displayName}</h2>
      <EntryForm customer={customer} kind={kind} onRecorded={reload} />
    </>
  );
}
