import { useState, type FormEvent, type ReactNode } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { Language, MessageKey } from "../../i18n/types";
import { type ApiError, cleanReason, NETWORK_ERROR, NO_ANSWER, type Overdue, REASON_MAX, REASON_MIN, TIMEOUT } from "../api";
import { formatDateTime, formatMoney } from "../format";
import { AlertIcon, CheckIcon, ClockIcon, ListIcon } from "../icons";
import { CURRENCIES, type Currency } from "../money";

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
  if (error.code === TIMEOUT) {
    return t("error.timeout");
  }
  // A write this page stopped waiting for: it may have been applied, and the words must not deny it.
  if (error.code === NO_ANSWER) {
    return t("error.noAnswer");
  }
  return error.code === NETWORK_ERROR ? t("error.network") : t("state.error");
}

/**
 * A list that is on its way: grey lines in the shape of rows. The words are there for a screen reader
 * (and for anything that waits for them to go); the lines themselves say nothing to it.
 */
export function Loading() {
  const { t } = useI18n();
  return (
    <div className="loading" role="status">
      <span className="visually-hidden">{t("state.loading")}</span>
      {[0, 1, 2].map((row) => (
        <span key={row} className="loading__row" aria-hidden="true">
          <span className="loading__text">
            <span className="skeleton skeleton--line" />
            <span className="skeleton skeleton--short" />
          </span>
          <span className="skeleton loading__amount" />
        </span>
      ))}
    </div>
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

/** Nothing to list yet: an icon and the sentence that says what is missing, where the list would be. */
export function Empty({ children, icon }: { children: ReactNode; icon?: ReactNode }) {
  return (
    <div className="empty">
      <span className="empty__icon">{icon ?? <ListIcon />}</span>
      <p className="empty__text">{children}</p>
    </div>
  );
}

type BadgeTone = "danger" | "warning" | "success" | "accent";

const BADGE_ICONS: Readonly<Record<BadgeTone, (() => ReactNode) | null>> = {
  danger: AlertIcon,
  warning: ClockIcon,
  success: CheckIcon,
  accent: null,
};

/**
 * A short status beside a name or an amount. The words say it; the icon of an overdue, due or paid
 * badge and its color only repeat them, so nothing is told by color alone.
 */
export function Badge({ tone, children }: { tone?: BadgeTone; children: ReactNode }) {
  const ToneIcon = tone ? BADGE_ICONS[tone] : null;
  return (
    <span className={tone ? `badge badge--${tone}` : "badge"}>
      {ToneIcon ? <ToneIcon /> : null}
      <span>{children}</span>
    </span>
  );
}

/** The first letters of the first two words of a name: "Dilnoza Karimova" is "DK". */
export function initials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => Array.from(word)[0] ?? "")
    .join("")
    .toLocaleUpperCase();
}

/**
 * Decoration beside a person's name, which is always written next to it. The letters are drawn by the
 * style sheet from the attribute, so they are not part of the row's text: nothing reads, copies or
 * searches "DK" in front of the name.
 */
export function Avatar({ name }: { name: string }) {
  return <span className="avatar" aria-hidden="true" data-initials={initials(name)} />;
}

/** Message under a form field; `id` is what the field's aria-describedby points at. */
export function FieldError({ id, message }: { id: string; message: string | null }) {
  return message ? (
    <p className="field__error" id={id} role="alert">
      {message}
    </p>
  ) : null;
}

/**
 * "45 000 so'm muddati o'tgan · 12 kun kechikkan", and what falls due today, when there is any. The
 * dollar debt has lines of its own, with `currency`.
 */
export function OverdueLines({ overdue, currency }: { overdue: Overdue; currency?: Currency }) {
  const { t, language } = useI18n();
  return (
    <>
      {overdue.amount > 0 ? (
        <p className="row__warning">
          <span>{t("overdue.amount", { amount: formatMoney(overdue.amount, language, currency) })}</span>
          {overdue.days > 0 ? <Badge tone="danger">{t("overdue.days", { count: overdue.days })}</Badge> : null}
        </p>
      ) : null}
      {overdue.dueToday > 0 ? (
        <p className="row__meta">
          <Badge tone="warning">
            {t("due.todayAmount", { amount: formatMoney(overdue.dueToday, language, currency) })}
          </Badge>
        </p>
      ) : null}
    </>
  );
}

/**
 * A balance in words. What is owed is the amount as it always was. A balance below zero is money the
 * shop holds of a customer who paid ahead (an advance): it is said as "in credit" with the amount
 * itself, never as a debt with a minus sign and never by a color. `mine` words it for the customer's
 * own page.
 */
export function owedText(amount: number, language: Language, t: Translate, currency?: Currency, mine = false): string {
  return amount < 0
    ? t(mine ? "balance.inCredit.mine" : "balance.inCredit", { amount: formatMoney(-amount, language, currency) })
    : formatMoney(amount, language, currency);
}

/** Nothing is owed in either currency and an advance is held in at least one: "Debt:" would be the wrong label. */
export function inCredit(uzs: number, usd: number | undefined): boolean {
  const cents = usd ?? 0;
  return uzs <= 0 && cents <= 0 && (uzs < 0 || cents < 0);
}

/** A debt in one currency and an advance in the other: two things to say, each under its own name. */
export function isMixed(uzs: number, usd: number | undefined): boolean {
  const cents = usd ?? 0;
  return (uzs > 0 && cents < 0) || (uzs < 0 && cents > 0);
}

/**
 * What is owed, in so'm and, for a shop that works in dollars, in dollars beside it. The two are never
 * added. Without a dollar figure this is the so'm amount as plain text, exactly as it always was. An
 * advance is worded by `owedText`.
 *
 * A debt in one currency with an advance in the other is written as two parts, the debt first and each
 * with its own label: "Qarz: 25.00 $ · Haqdor: 15 000 so'm". `label` names the debt there; without it
 * the usual one does ("Qarz:", or "Qarzingiz:" on the customer's own page).
 */
export function Money({
  uzs,
  usd,
  mine = false,
  label,
}: {
  uzs: number;
  usd: number | undefined;
  mine?: boolean;
  label?: string;
}) {
  const { t, language } = useI18n();
  if (usd === undefined) {
    return owedText(uzs, language, t, undefined, mine);
  }
  if (isMixed(uzs, usd)) {
    const [debt, held] =
      uzs > 0
        ? [formatMoney(uzs, language), owedText(usd, language, t, "USD", mine)]
        : [formatMoney(usd, language, "USD"), owedText(uzs, language, t, undefined, mine)];
    return (
      <>
        <span className="money">
          {label ?? t(mine ? "my.balance" : "customer.balance")} {debt}
        </span>
        {" · "}
        <span className="money">{held}</span>
      </>
    );
  }
  return (
    <>
      <span className="money">{owedText(uzs, language, t, undefined, mine)}</span>{" "}
      <span className="money">{owedText(usd, language, t, "USD", mine)}</span>
    </>
  );
}

/**
 * The line that names a balance: the label, then the amounts. A customer who is only in credit has no
 * "Debt:" in front: the words of the advance say what the figure is. With a debt in one currency and an
 * advance in the other, the label goes to the debt alone (see `Money`).
 */
export function BalanceLine({
  label,
  uzs,
  usd,
  large = false,
  mine = false,
}: {
  label: string;
  uzs: number;
  usd: number | undefined;
  large?: boolean;
  mine?: boolean;
}) {
  const mixed = isMixed(uzs, usd);
  return (
    <p className={large ? "balance balance--large" : "balance"}>
      {mixed || inCredit(uzs, usd) ? null : (
        <>
          <span>{label}</span>{" "}
        </>
      )}
      <strong>
        <Money uzs={uzs} usd={usd} mine={mine} {...(mixed ? { label } : {})} />
      </strong>
    </p>
  );
}

/** So'm or dollars: the choice a form or a list offers only in a shop that works in dollars. */
export function CurrencyToggle({
  value,
  onChange,
  disabled = false,
}: {
  value: Currency;
  onChange: (currency: Currency) => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  return (
    <div className="toggle" role="group" aria-label={t("currency.choose")}>
      {CURRENCIES.map((currency) => (
        <button
          key={currency}
          type="button"
          className="toggle__option"
          aria-pressed={value === currency}
          disabled={disabled}
          onClick={() => onChange(currency)}
        >
          {t(currency === "USD" ? "currency.usd" : "currency.uzs")}
        </button>
      ))}
    </div>
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
        {loading ? <span className="spinner" aria-hidden="true" /> : null}
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
