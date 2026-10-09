import { useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { tashkentDay } from "../format";
import { usePagedList, useSubmit } from "../hooks";
import { compareDays, parseIsoDate, toIsoDate } from "../promise";
import { dayText } from "../promiseParts";
import { Link } from "../router";
import { useMay, useWorkspace } from "../workspace/context";
import { Empty, Failure, FieldError, formatInstant, Loading, LoadMore, ReasonForm } from "../workspace/parts";
import { money } from "./amounts";
import { Balances } from "./Balances";
import type { CashApi, CashDay, CashEntry } from "./cashApi";

function EntryRow({ calls, entry, onChanged }: { calls: CashApi; entry: CashEntry; onChanged: () => void }) {
  const { t, language } = useI18n();
  const can = useMay();
  const [asking, setAsking] = useState(false);
  const cancelling = useSubmit((reason: string, key) => calls.cancel(entry.id, reason, key).then(onChanged));
  const amount = money(entry.amount, entry.currency, language);
  const cancelled = entry.cancelled;
  let title = <>{entry.category.name}</>;
  if (entry.source === "ledger") {
    // Whose payment it is, for a reader who may open the customer book; otherwise only that it is one.
    title = entry.customer ? (
      <Link to={`/customers/${entry.customer.id}`}>
        {t("cash.entry.paymentOf", { name: entry.customer.displayName ?? "" })}
      </Link>
    ) : (
      <>{t("cash.entry.payment")}</>
    );
  }
  return (
    <li className={cancelled ? "row row--struck" : "row"}>
      <p className="row__link">
        <span className="row__name">{title}</span>
        <span className={entry.direction === "income" ? "row__amount row__amount--in" : "row__amount"}>
          {t(`cash.entry.${entry.direction}`, { amount })}
        </span>
      </p>
      <p className="row__meta">
        {t("cash.entry.meta", {
          method: t(`cash.method.${entry.method}`),
          time: formatInstant(entry.createdAt, language),
        })}
      </p>
      {entry.note ? <p className="row__note">{entry.note}</p> : null}
      {cancelled ? (
        <p className="row__meta">
          {cancelled.reason === null
            ? t("cash.entry.cancelledByLedger")
            : t("cash.entry.cancelled", { reason: cancelled.reason })}
        </p>
      ) : null}
      {/* A customer's payment is cancelled where it was recorded, in the ledger; here it is only said. */}
      {!cancelled && can("cash.cancel") && entry.source === "ledger" ? (
        <p className="row__meta">{t("cash.entry.ofLedger")}</p>
      ) : null}
      {!cancelled && can("cash.cancel") && entry.source === "manual" && !asking ? (
        <button type="button" className="button button--small" onClick={() => setAsking(true)}>
          {t("cash.cancel")}
        </button>
      ) : null}
      {asking && !cancelled ? (
        <ReasonForm
          id={`cash-cancel-${entry.id}`}
          label={t("cash.cancel.label")}
          hint={t("cash.cancel.hint")}
          submitLabel={t("cash.cancel.submit")}
          pending={cancelling.state.status === "pending"}
          error={cancelling.state.status === "error" ? cancelling.state.error : null}
          onSubmit={cancelling.submit}
          onCancel={() => {
            cancelling.reset();
            setAsking(false);
          }}
        />
      ) : null}
    </li>
  );
}

/**
 * One day of the cash book: what each way of paying opened with, took in, paid out and closed with, and
 * the day's entries, newest first. A cancelled entry stays in the list, struck through, with its reason.
 */
export function DayBook({
  calls,
  day,
  onDay,
  version,
  onChanged,
}: {
  calls: CashApi;
  /** The day shown, "YYYY-MM-DD". */
  day: string;
  onDay: (day: string) => void;
  /** Changes when something was written, so that the day is read again. */
  version: number;
  onChanged: () => void;
}) {
  const { now } = useWorkspace();
  const { t, language } = useI18n();
  const today = toIsoDate(tashkentDay(now()));
  const [typed, setTyped] = useState(day);
  const [problem, setProblem] = useState<string | null>(null);
  // The balances come with the first page of the day and are of the whole day.
  const [book, setBook] = useState<CashDay | null>(null);
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) =>
      calls.day(day, cursor, signal).then((answer) => {
        if (cursor === null) {
          setBook(answer);
        }
        return { items: answer.entries, nextCursor: answer.nextCursor };
      }),
    [calls, day, version],
  );

  const choose = (value: string) => {
    setTyped(value);
    const chosen = parseIsoDate(value);
    const last = parseIsoDate(today);
    if (chosen === null) {
      setProblem(t("cash.day.problem.DATE_INVALID"));
    } else if (last !== null && compareDays(chosen, last) > 0) {
      setProblem(t("cash.day.problem.IN_FUTURE"));
    } else {
      setProblem(null);
      onDay(toIsoDate(chosen));
    }
  };

  return (
    <>
      <div className="field">
        <label htmlFor="cash-day">{t("cash.day")}</label>
        <input
          id="cash-day"
          type="date"
          className="input"
          value={typed}
          max={today}
          aria-invalid={problem !== null}
          aria-describedby="cash-day-error"
          onChange={(event) => choose(event.target.value)}
        />
        <FieldError id="cash-day-error" message={problem} />
      </div>
      {state.status === "loading" ? <Loading /> : null}
      {state.status === "error" ? <Failure error={state.error} onRetry={reload} /> : null}
      {state.status === "ready" && book !== null ? (
        <section aria-labelledby="cash-day-title">
          <h2 id="cash-day-title">{t("cash.day.title", { date: dayText(book.date, language) })}</h2>
          <Balances lines={book.balances} totals={book.totals} labelKey="cash.balances.opening" />
          <section aria-labelledby="cash-entries">
            <h3 id="cash-entries">{t("cash.entries")}</h3>
            {state.items.length === 0 ? (
              <Empty>{t("cash.day.none")}</Empty>
            ) : (
              <ul className="rows" aria-label={t("cash.entries")}>
                {state.items.map((entry) => (
                  <EntryRow key={entry.id} calls={calls} entry={entry} onChanged={onChanged} />
                ))}
              </ul>
            )}
            {state.nextCursor !== null ? (
              <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} />
            ) : null}
          </section>
        </section>
      ) : null}
    </>
  );
}
