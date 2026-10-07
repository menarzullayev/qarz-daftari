import { useState, type FormEvent } from "react";

import { useI18n, type Translate } from "../i18n/I18nProvider";
import type { Language, MessageKey } from "../i18n/types";
import type { ApiError, PromiseRecord } from "./api";
import { DATE_REASON_MAX, type DateChoice, type DateProblem, type DayRange, reasonFits, tidyReason } from "./dateRules";
import { formatCalendarDay } from "./format";
import { MAX_PROMISE_DAYS, parseIsoDate, toIsoDate } from "./promise";
import { errorText, FieldError, formatInstant } from "./workspace/parts";

const PROBLEMS: Readonly<Record<DateProblem, MessageKey>> = {
  required: "dates.date.required",
  before_sale: "dates.date.beforeSale",
  too_far: "dates.date.tooFar",
  not_later: "dates.date.notLater",
  unchanged: "dates.date.unchanged",
};

export function dateProblemText(problem: DateProblem, t: Translate): string {
  return t(PROBLEMS[problem], problem === "too_far" ? { days: MAX_PROMISE_DAYS } : {});
}

/** What the server says about a date it refused (`fields.promised_date` or `fields.requested_date`). */
const REFUSED_DATES: Readonly<Record<string, DateProblem>> = {
  PROMISE_BEFORE_SALE: "before_sale",
  PROMISE_TOO_FAR: "too_far",
  PROMISE_UNCHANGED: "unchanged",
};

function refusedFields(error: ApiError | null, dateField: string, t: Translate): { date: string | null; reason: string | null } {
  if (error?.code !== "VALIDATION") {
    return { date: null, reason: null };
  }
  const problem = REFUSED_DATES[error.fields[dateField] ?? ""];
  return {
    // A refusal of the date that this client does not know still belongs next to the date.
    date: problem ? dateProblemText(problem, t) : dateField in error.fields ? errorText(error, t) : null,
    reason: "reason" in error.fields ? t("dates.reason.tooLong", { max: DATE_REASON_MAX }) : null,
  };
}

/** An ISO date from the server as a day in words; text that is not a date is shown as it came. */
export function dayText(iso: string, language: Language): string {
  const day = parseIsoDate(iso);
  return day === null ? iso : formatCalendarDay(day, language);
}

const ACTORS: Readonly<Record<string, MessageKey>> = {
  default: "promise.actor.default",
  staff: "promise.actor.staff",
  customer_request: "promise.actor.request",
};

/**
 * Every promised date an entry has carried, oldest first, with where each came from (INV-9). Nothing
 * is drawn for an entry whose date never changed: its one date is already on the row.
 */
export function PromiseHistory({ promises, mine = false }: { promises: readonly PromiseRecord[]; /** Read by the customer themselves. */ mine?: boolean }) {
  const { t, language } = useI18n();
  if (promises.length < 2) {
    return null;
  }
  return (
    <div className="history">
      <p className="row__meta">{t("promise.history")}</p>
      <ol className="history__list">
        {promises.map((promise, index) => {
          const day = dayText(promise.promisedDate, language);
          const actor =
            mine && promise.actor === "customer_request"
              ? t("promise.actor.request.mine")
              : t(ACTORS[promise.actor] ?? "promise.actor.other");
          return (
            <li key={`${promise.createdAt}/${index}`}>
              <span>{index === promises.length - 1 ? t("promise.history.current", { date: day }) : day}</span>
              <span className="row__meta">
                {t("promise.history.meta", { actor, date: formatInstant(promise.createdAt, language) })}
              </span>
              {promise.reason ? <span className="row__note">{t("promise.history.reason", { reason: promise.reason })}</span> : null}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/**
 * A date within a range and an optional reason: a customer asking for a later date, or the shop setting
 * one. `choose` says whether the typed date may be sent; nothing is sent while it or the reason does
 * not fit. The server's refusal of a field is shown next to that field.
 */
export function DateReasonForm({
  id,
  label,
  range,
  hint,
  submitLabel,
  pending,
  error = null,
  errorDetail = null,
  name,
  choose,
  onSubmit,
  onCancel,
}: {
  id: string;
  label: string;
  range: DayRange;
  hint: string;
  submitLabel: string;
  pending: boolean;
  error?: ApiError | null;
  /** What the catalog can add to the server's refusal. */
  errorDetail?: string | null;
  /** The name the server gives the date in a VALIDATION refusal ("promised_date", "requested_date"). */
  name: string;
  choose: (text: string) => DateChoice;
  onSubmit: (date: string, reason: string | null) => void;
  onCancel: () => void;
}) {
  const { t, language } = useI18n();
  const [date, setDate] = useState("");
  const [reason, setReason] = useState("");
  const [problems, setProblems] = useState<{ date: string | null; reason: string | null }>({ date: null, reason: null });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const chosen = choose(date);
    const clean = tidyReason(reason);
    const found = {
      date: chosen.ok ? null : dateProblemText(chosen.problem, t),
      reason: reasonFits(clean) ? null : t("dates.reason.tooLong", { max: DATE_REASON_MAX }),
    };
    setProblems(found);
    if (chosen.ok && found.reason === null) {
      onSubmit(chosen.date, clean);
    }
  };

  const refused = refusedFields(error, name, t);
  const shown = { date: problems.date ?? refused.date, reason: problems.reason ?? refused.reason };
  // A refusal shown next to its field is not repeated above the form.
  const general = error !== null && refused.date === null && refused.reason === null;

  return (
    <form className="form notice" onSubmit={submit} noValidate>
      {general ? (
        <div role="alert">
          <p className="field__error">{errorText(error, t)}</p>
          {errorDetail ? <p className="field__error">{errorDetail}</p> : null}
        </div>
      ) : null}
      <div className="field">
        <label htmlFor={`${id}-date`}>{label}</label>
        <input
          id={`${id}-date`}
          type="date"
          className="input"
          value={date}
          min={toIsoDate(range.min)}
          max={toIsoDate(range.max)}
          aria-invalid={shown.date !== null}
          aria-describedby={`${id}-range ${id}-date-error`}
          onChange={(event) => {
            setDate(event.target.value);
            setProblems((current) => ({ ...current, date: null }));
          }}
        />
        <p className="field__hint" id={`${id}-range`}>
          {t("dates.range", { from: formatCalendarDay(range.min, language), to: formatCalendarDay(range.max, language) })}
        </p>
        <FieldError id={`${id}-date-error`} message={shown.date} />
      </div>
      <div className="field">
        <label htmlFor={`${id}-reason`}>{t("dates.reason")}</label>
        <textarea
          id={`${id}-reason`}
          className="input input--text"
          rows={2}
          value={reason}
          maxLength={1000}
          aria-invalid={shown.reason !== null}
          aria-describedby={`${id}-hint ${id}-reason-error`}
          onChange={(event) => {
            setReason(event.target.value);
            setProblems((current) => ({ ...current, reason: null }));
          }}
        />
        <p className="field__hint" id={`${id}-hint`}>
          {hint}
        </p>
        <FieldError id={`${id}-reason-error`} message={shown.reason} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : submitLabel}
        </button>
        <button type="button" className="button" onClick={onCancel} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </form>
  );
}
