import { useState, type FormEvent } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import { PAYMENT_METHODS } from "../api";
import type { ApiError, ChosenPromise, CustomerDetail, EntryKind, NewEntry, PaymentMethod, RecordedEntry } from "../api";
import { type CalendarDay, formatCalendarDay, formatMoney, tashkentDay } from "../format";
import { linesSumProblem, MAX_LINES_SUM, MIN_LINES_SUM } from "../goods";
import { useLoad, useSubmit } from "../hooks";
import {
  type AmountProblem,
  amountInput,
  type Currency,
  currencyOf,
  ENTRY_RANGE,
  formatDollars,
  formatUzs,
  MAX_AMOUNT,
  MIN_AMOUNT,
  parseMoney,
} from "../money";
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
import { useMay, useWorkspace } from "./context";
import { exceedsLimit, limitIn, refusedLimit } from "./creditRules";
import { type DraftLine, GoodsEditor, GoodsList, readDrafts } from "./GoodsEditor";
import { CurrencyToggle, errorText, Failure, FieldError, Loading, Money } from "./parts";

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

/**
 * Why a typed amount is not one, in words; shared by every form that takes an amount. A dollar amount
 * has its own words: its decimals are welcome, and its range is another.
 */
export function amountMessage(problem: AmountProblem, t: Translate, currency: Currency = "UZS"): string {
  if (currency === "USD" && problem !== "empty") {
    if (problem === "too_small" || problem === "too_large") {
      const { min, max } = ENTRY_RANGE.USD;
      return t("entry.amount.usd.range", { min: formatDollars(min), max: formatDollars(max) });
    }
    return t(problem === "not_whole" ? "entry.amount.usd.notWhole" : "entry.amount.usd.invalid");
  }
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
function refusedFields(error: ApiError | null, t: Translate, currency: Currency): FieldErrors {
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
    amount: "amount" in error.fields ? amountMessage("invalid", t, currency) : null,
    note: "note" in error.fields ? t("entry.note.tooLong", { max: MAX_NOTE_LENGTH }) : null,
    promise: "promised_date" in error.fields ? t("promise.range", { days: MAX_PROMISE_DAYS }) : null,
  };
}

/** What the form saved, and whether the sale took the shop's usual term instead of a chosen date. */
type Saved = { recorded: RecordedEntry; offerPromise: boolean };

/** A refusal that sending the same choice again would not change: the offer is over. */
function isFinalRefusal(error: ApiError): boolean {
  return error.status >= 400 && error.status < 500;
}

function Recorded({ saved, today, onAnother }: { saved: Saved; today: CalendarDay; onAnother: () => void }) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const { entry, customer, limitWarning } = saved.recorded;
  // A sale saved with the usual term may get its date once, with one tap (REQ-008).
  const choice = useSubmit((date: string, key): Promise<ChosenPromise> => api.choosePromise(entry.id, date, key));
  const chosen = choice.state.status === "done" ? choice.state.result.promisedDate : null;
  const refusal = choice.state.status === "error" ? choice.state.error : null;
  const offered = saved.offerPromise && chosen === null && !(refusal !== null && isFinalRefusal(refusal));
  const promisedText = chosen ?? entry.promisedDate;
  const promised = promisedText === null ? null : parseIsoDate(promisedText);
  // The entry's own currency: its amount and the limit it met are in it; the balances are shown apart.
  const currency = currencyOf(entry);
  return (
    <div className="notice notice--done" role="status">
      <p>
        {t(entry.kind === "payment" ? "entry.done.payment" : "entry.done.credit", {
          name: customer.displayName,
          amount: formatMoney(entry.amount, language, currency),
        })}
      </p>
      <p className="balance">
        <span>{t("customer.balance.new")}</span>{" "}
        <strong>
          <Money uzs={customer.balance} usd={customer.usd?.balance} />
        </strong>
      </p>
      {/* The server saved the sale above the limit and says so to its author (REQ-044). */}
      {limitWarning ? (
        <p className="row__warning">
          {t("credit.saved.over", {
            balance: formatMoney(limitWarning.balance, language, currency),
            limit: formatMoney(limitWarning.limit, language, currency),
          })}
        </p>
      ) : null}
      {entry.lines.length > 0 ? <GoodsList lines={entry.lines} /> : null}
      {promised ? <p>{t("entry.promised", { date: formatCalendarDay(promised, language) })}</p> : null}
      {refusal ? (
        <p className="field__error" role="alert">
          {errorText(refusal, t)}
        </p>
      ) : null}
      {offered ? (
        <div className="offer" role="group" aria-label={t("promise.legend")}>
          <p>{t("promise.offer")}</p>
          <p className="actions">
            {QUICK_CHOICES.map((option) => {
              const date = quickChoiceDate(option, today);
              return (
                <button
                  key={option}
                  type="button"
                  className="button button--stacked"
                  disabled={choice.state.status === "pending"}
                  onClick={() => choice.submit(toIsoDate(date))}
                >
                  <span>{t(CHOICE_LABELS[option])}</span>
                  <span className="choice__date">{formatCalendarDay(date, language)}</span>
                </button>
              );
            })}
          </p>
        </div>
      ) : null}
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
  const { api, now, features } = useWorkspace();
  const can = useMay();
  const { t, language } = useI18n();
  // The shop's default limit and its rule for sellers; a payment meets no limit and asks for nothing.
  const credit = useLoad(
    (signal) => (kind === "credit" ? api.readCreditSettings(signal) : Promise.resolve(null)),
    [api, kind],
  );
  const [amountText, setAmountText] = useState("");
  const [note, setNote] = useState("");
  // While the cash book is on, a payment says how the money came: it goes to that balance of the book.
  const askMethod = kind === "payment" && features?.cashBook === true;
  const [method, setMethod] = useState<PaymentMethod>("cash");
  const [choice, setChoice] = useState<PromiseChoice>("default");
  const [picked, setPicked] = useState("");
  const [errors, setErrors] = useState<FieldErrors>(NO_ERRORS);
  const [goodsOpen, setGoodsOpen] = useState(false);
  const [goods, setGoods] = useState<DraftLine[]>([]);
  const [goodsChecked, setGoodsChecked] = useState(false);
  // So'm unless the seller says dollars, which only a shop that works in dollars offers. A payment
  // starts in dollars when dollars are all that is owed: there is nothing to pay in so'm.
  const dollars = customer.usd;
  const [chosen, setChosen] = useState<Currency>(
    kind === "payment" && dollars !== undefined && customer.balance <= 0 && dollars.balance > 0 ? "USD" : "UZS",
  );
  const currency: Currency = dollars === undefined ? "UZS" : chosen;
  const inDollars = currency === "USD";
  const { state, submit } = useSubmit(
    (entry: NewEntry, key): Promise<Saved> =>
      api
        .recordEntry(customer.id, entry, key)
        .then((recorded) => ({ recorded, offerPromise: entry.kind === "credit" && entry.promisedDate === null })),
  );

  const today = tashkentDay(now());
  if (state.status === "done") {
    // Reloading the customer mounts a fresh form that starts from the new balance.
    return <Recorded saved={state.result} today={today} onAnother={onRecorded} />;
  }

  const parsed = parseMoney(amountText, currency);
  // With goods the amount is their sum: the amount field is not shown and not sent (REQ-037). Goods are
  // priced in so'm, so a sale in dollars has none and is recorded by its amount.
  const itemized = kind === "credit" && !inDollars && goods.length > 0;
  const reading = readDrafts(goods);
  const sumProblem = itemized && reading.lines !== null ? linesSumProblem(reading.sum) : null;

  // A warning before saving, never a block: the server decides (BR-8). When the shop's settings could
  // not be read, the customer's own limit is still known and still warns.
  const creditSettings = credit.state.status === "ready" ? credit.state.data : null;
  // The limit and the debt of the sale's own currency: a sale in dollars meets the dollar limit only.
  const owed = limitIn(currency, customer, creditSettings);
  const limit = owed?.limit ?? null;
  const balance = owed?.balance ?? 0;
  const sale = itemized ? reading.sum : parsed.ok ? parsed.amount : null;
  const overLimit = kind === "credit" && limit !== null && sale !== null && exceedsLimit(limit, balance, sale);
  const mayProceed = can("entries.over_limit") ? true : (creditSettings?.sellersMayExceed ?? null);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const amount = parseMoney(amountText, currency);
    const cleanNote = note.split(/\s+/u).filter(Boolean).join(" ");
    const promise: PromiseResult = kind === "credit" ? promisedDateFor(choice, picked, today) : { ok: true, date: null };
    const found: FieldErrors = {
      amount: itemized || amount.ok ? null : amountMessage(amount.problem, t, currency),
      note: [...cleanNote].length > MAX_NOTE_LENGTH ? t("entry.note.tooLong", { max: MAX_NOTE_LENGTH }) : null,
      promise: promise.ok
        ? null
        : promise.problem === "required"
          ? t("promise.required")
          : t("promise.range", { days: MAX_PROMISE_DAYS }),
    };
    setErrors(found);
    setGoodsChecked(true);
    if (!promise.ok || found.note !== null) {
      return;
    }
    const rest = {
      kind,
      note: cleanNote === "" ? null : cleanNote,
      promisedDate: promise.date,
      // Named only where the server knows the field: for a payment, while the cash book is on.
      ...(askMethod ? { method } : {}),
    };
    if (itemized) {
      if (reading.lines !== null && linesSumProblem(reading.sum) === null) {
        submit({ ...rest, lines: reading.lines });
      }
    } else if (amount.ok) {
      // Dollars are named and sent in cents; so'm is sent as it always was, with no currency beside it.
      submit(inDollars ? { ...rest, amount: amount.amount, currency } : { ...rest, amount: amount.amount });
    }
  };

  const failure = state.status === "error" ? state.error : null;
  const refused = refusedFields(failure, t, currency);
  const refusedAt = refusedLimit(failure);
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
        <span>{t("customer.balance")}</span>{" "}
        <strong>
          <Money uzs={customer.balance} usd={dollars?.balance} />
        </strong>
      </p>
      {dollars === undefined ? null : (
        <CurrencyToggle
          value={currency}
          disabled={pending}
          onChange={(next) => {
            setChosen(next);
            // An amount typed for one currency is not an amount of the other.
            setAmountText("");
            clear("amount");
          }}
        />
      )}
      {failure ? (
        <div className="notice notice--error" role="alert">
          <p>{errorText(failure, t)}</p>
          {refusedAt ? (
            <p>
              {t("credit.refused.figures", {
                limit: formatMoney(refusedAt.limit, language, currency),
                balance: formatMoney(refusedAt.balance, language, currency),
              })}
            </p>
          ) : null}
          {failure.serverMessage === null ? <p>{t("entry.retrySafe")}</p> : null}
        </div>
      ) : null}

      {kind === "credit" && inDollars ? <p className="hint">{t("entry.usd.noGoods")}</p> : null}
      {kind === "credit" && !inDollars && !goodsOpen ? (
        <p className="actions">
          <button type="button" className="button" onClick={() => setGoodsOpen(true)}>
            {t("goods.open")}
          </button>
        </p>
      ) : null}
      {kind === "credit" && !inDollars && goodsOpen ? (
        <>
          <GoodsEditor
            drafts={goods}
            onChange={(next) => {
              setGoods(next);
              setGoodsChecked(false);
            }}
            showAllProblems={goodsChecked}
            disabled={pending}
          />
          {sumProblem && goodsChecked ? (
            <p className="field__error" role="alert">
              {sumProblem === "too_small"
                ? t("goods.sum.tooSmall", { min: formatUzs(MIN_LINES_SUM) })
                : t("goods.sum.tooLarge", { max: formatUzs(MAX_LINES_SUM) })}
            </p>
          ) : null}
        </>
      ) : null}

      {itemized ? null : (
        <div className="field">
          <label htmlFor="entry-amount">{t(inDollars ? "entry.amount.usd" : "entry.amount")}</label>
          <input
            id="entry-amount"
            className="input input--amount"
            inputMode={inDollars ? "decimal" : "numeric"}
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
            {parsed.ok
              ? formatMoney(parsed.amount, language, currency)
              : t(inDollars ? "entry.amount.usd.hint" : "entry.amount.hint")}
          </p>
          <FieldError id="entry-amount-error" message={shown.amount} />
          {kind === "payment" && balance >= ENTRY_RANGE[currency].min ? (
            <button type="button" className="button" onClick={() => setAmountText(amountInput(balance, currency))}>
              {t("entry.payAll", { amount: formatMoney(balance, language, currency) })}
            </button>
          ) : null}
        </div>
      )}

      {overLimit && limit !== null && sale !== null ? (
        <div className="notice" role="note">
          <p>
            {t("credit.warn.over", {
              balance: formatMoney(balance + sale, language, currency),
              limit: formatMoney(limit, language, currency),
            })}
          </p>
          {mayProceed === null ? null : (
            <p>{t(mayProceed ? "credit.warn.allowed" : "credit.limit.sellersStopped")}</p>
          )}
        </div>
      ) : null}

      {askMethod ? (
        <div className="field">
          <label htmlFor="entry-method">{t("entry.method")}</label>
          <select
            id="entry-method"
            className="input"
            value={method}
            onChange={(event) => setMethod(PAYMENT_METHODS.find((way) => way === event.target.value) ?? "cash")}
          >
            {PAYMENT_METHODS.map((way) => (
              <option key={way} value={way}>
                {t(`entry.method.${way}`)}
              </option>
            ))}
          </select>
        </div>
      ) : null}

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
