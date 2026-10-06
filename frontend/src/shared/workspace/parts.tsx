import type { ReactNode } from "react";

import { type ApiError, NETWORK_ERROR, type Overdue } from "../api";
import { formatMoney } from "../format";
import { useI18n, type Translate } from "../../i18n/I18nProvider";

/**
 * Text for a failed call. The server's message is already in the user's language and is shown as it is;
 * the catalog only speaks when the server could not (no connection, or an answer that is not the API's).
 */
export function errorText(error: ApiError, t: Translate): string {
  if (error.serverMessage) {
    return error.serverMessage;
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
