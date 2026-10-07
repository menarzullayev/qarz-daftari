import { useState, type FormEvent, type ReactNode } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { Language, MessageKey } from "../../i18n/types";
import { type ApiError, cleanReason, NETWORK_ERROR, type Overdue, REASON_MAX, REASON_MIN } from "../api";
import { formatDateTime, formatMoney } from "../format";

/**
 * Text for a failed call. The server's message is already in the user's language and is shown as it is;
 * the catalog only speaks when the server could not (no connection, or an answer that is not the API's).
 */
export function errorText(error: ApiError, t: Translate): string {
  // The server words this one in Uzbek only, whatever the reader's language: the catalog speaks instead.
  if (error.code === "BODY_TOO_LARGE") {
    return t("error.bodyTooLarge");
  }
  if (error.code === "RATE_LIMITED") {
    const said = error.serverMessage ?? t("error.rateLimited");
    return error.retryAfter === null ? said : `${said} ${t("error.wait", { count: error.retryAfter })}`;
  }
  if (error.serverMessage) {
    return error.serverMessage;
  }
  if (error.code === "TIMEOUT") {
    return t("error.timeout");
  }
  return error.code === NETWORK_ERROR ? t("error.network") : t("state.error");
}

export function Loading() {
  const { t } = useI18n();
  return (
    <p className="state" role="status">
      {t("state.loading")}
    </p>
  );
}

export function Failure({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const { t } = useI18n();
  return (
    <div className="notice notice--error" role="alert">
      <p>{errorText(error, t)}</p>
      {onRetry ? (
        <button type="button" className="button" onClick={onRetry}>
          {t("action.retry")}
        </button>
      ) : null}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="state">{children}</p>;
}

/** Message under a form field; `id` is what the field's aria-describedby points at. */
export function FieldError({ id, message }: { id: string; message: string | null }) {
  return message ? (
    <p className="field__error" id={id} role="alert">
      {message}
    </p>
  ) : null;
}

/** "45 000 so'm muddati o'tgan · 12 kun kechikkan", and what falls due today, when there is any. */
export function OverdueLines({ overdue }: { overdue: Overdue }) {
  const { t, language } = useI18n();
  return (
    <>
      {overdue.amount > 0 ? (
        <p className="row__warning">
          <span>{t("overdue.amount", { amount: formatMoney(overdue.amount, language) })}</span>
          {overdue.days > 0 ? <span>{t("overdue.days", { count: overdue.days })}</span> : null}
        </p>
      ) : null}
      {overdue.dueToday > 0 ? (
        <p className="row__meta">{t("due.todayAmount", { amount: formatMoney(overdue.dueToday, language) })}</p>
      ) : null}
    </>
  );
}

export function LoadMore({
  loading,
  error,
  onClick,
}: {
  loading: boolean;
  error: ApiError | null;
  onClick: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="more">
      {error ? (
        <p className="field__error" role="alert">
          {errorText(error, t)}
        </p>
      ) : null}
      <button type="button" className="button" onClick={onClick} disabled={loading}>
        {loading ? t("state.loading") : t("list.more")}
      </button>
    </div>
  );
}

export const ENTRY_KIND_LABELS: Readonly<Record<string, MessageKey>> = {
  credit: "entry.kind.credit",
  payment: "entry.kind.payment",
  reversal: "entry.kind.reversal",
  opening: "entry.kind.opening",
};

/** An instant from the server as a Tashkent date and time; text that is not a date is shown as it came. */
export function formatInstant(iso: string, language: Language): string {
  const instant = new Date(iso);
  return Number.isNaN(instant.getTime()) ? iso : formatDateTime(instant, language);
}

/** A question asked before something that cannot be taken back; nothing is sent until "yes". */
export function Confirm({
  question,
  yes,
  no,
  pending,
  error = null,
  onYes,
  onNo,
}: {
  question: ReactNode;
  yes: string;
  no: string;
  pending: boolean;
  error?: ApiError | null;
  onYes: () => void;
  onNo: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="notice" role="group">
      {error ? (
        <p className="field__error" role="alert">
          {errorText(error, t)}
        </p>
      ) : null}
      {typeof question === "string" ? <p>{question}</p> : question}
      <p className="actions">
        <button type="button" className="button button--primary" onClick={onYes} disabled={pending}>
          {pending ? t("state.saving") : yes}
        </button>
        <button type="button" className="button" onClick={onNo} disabled={pending}>
          {no}
        </button>
      </p>
    </div>
  );
}

/**
 * A short reason, 3 to 300 characters: a customer's objection to an entry, or the shop's reason for
 * declining one. `onSubmit` receives the tidied text and is not called while the text does not fit.
 */
export function ReasonForm({
  id,
  label,
  hint,
  submitLabel,
  pending,
  error = null,
  errorDetail = null,
  onSubmit,
  onCancel,
}: {
  id: string;
  label: string;
  hint: string;
  submitLabel: string;
  pending: boolean;
  error?: ApiError | null;
  /** What the catalog can add to the server's refusal. */
  errorDetail?: string | null;
  onSubmit: (reason: string) => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const reason = cleanReason(text);
    if (reason === null) {
      setProblem(t("reason.invalid", { min: REASON_MIN, max: REASON_MAX }));
      return;
    }
    onSubmit(reason);
  };

  return (
    <form className="form notice" onSubmit={submit} noValidate>
      {error ? (
        <div role="alert">
          <p className="field__error">{errorText(error, t)}</p>
          {errorDetail ? <p className="field__error">{errorDetail}</p> : null}
        </div>
      ) : null}
      <div className="field">
        <label htmlFor={id}>{label}</label>
        <textarea
          id={id}
          className="input input--text"
          rows={3}
          value={text}
          maxLength={1000}
          aria-invalid={problem !== null}
          aria-describedby={`${id}-hint ${id}-error`}
          onChange={(event) => {
            setText(event.target.value);
            setProblem(null);
          }}
        />
        <p className="field__hint" id={`${id}-hint`}>
          {hint}
        </p>
        <FieldError id={`${id}-error`} message={problem} />
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
