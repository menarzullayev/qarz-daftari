import { useState, type FormEvent } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { ApiError, OpenPaymentNotice, ReceiptLink } from "../api";
import { formatMoney } from "../format";
import { useLoad, useSubmit } from "../hooks";
import { parseAmount } from "../money";
import { Link } from "../router";
import { useWorkspace } from "./context";
import { amountMessage } from "./EntryScreen";
import { Empty, errorText, Failure, FieldError, formatInstant, Loading, ReasonForm } from "./parts";

type Panel = { kind: "accept" | "decline"; id: string } | null;

/** The server's word for a notice that was decided already or has expired. */
function isClosed(error: ApiError): boolean {
  return error.code === "PAYMENT_NOTICE_NOT_OPEN";
}

/**
 * A receipt is opened in two steps. First a link is asked for with the session; then the link, which
 * needs no session and works for five minutes, is opened in a new tab. Opening it straight from the
 * answer would be a pop-up no one clicked, which browsers block. The link is a credential: it lives in
 * this component's state only. A link that has run out answers "not found" in its tab, where this page
 * cannot see it, so a new one can always be asked for.
 */
function Receipt({ noticeId }: { noticeId: string }) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const { state, submit } = useSubmit((id: string): Promise<ReceiptLink> => api.receiptLink(id));
  const pending = state.status === "pending";

  return (
    <div className="receipt">
      {state.status === "error" ? (
        <p className="field__error" role="alert">
          {errorText(state.error, t)}
        </p>
      ) : null}
      {state.status === "done" ? (
        <>
          <p className="actions">
            <a className="button" href={state.result.url} target="_blank" rel="noopener noreferrer">
              {t("notices.receipt.open")}
            </a>
          </p>
          <p className="row__meta">{t("notices.receipt.valid", { date: formatInstant(state.result.expiresAt, language) })}</p>
        </>
      ) : null}
      <p className="actions">
        <button type="button" className="button button--small" onClick={() => submit(noticeId)} disabled={pending}>
          {pending ? t("state.loading") : state.status === "done" ? t("notices.receipt.again") : t("notices.receipt.get")}
        </button>
      </p>
    </div>
  );
}

/**
 * Accepting is the question and its answer in one: the amount starts as the customer stated it and can
 * be corrected, and nothing is recorded until the button that says "yes, record the payment".
 */
function AcceptForm({
  notice,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  notice: OpenPaymentNotice;
  pending: boolean;
  error: ApiError | null;
  onSubmit: (amount: number) => void;
  onCancel: () => void;
}) {
  const { t, language } = useI18n();
  const [text, setText] = useState(String(notice.amount));
  const [problem, setProblem] = useState<string | null>(null);
  const id = `accept-${notice.id}`;
  const exceeds = t("notices.accept.exceeds", { balance: formatMoney(notice.customerBalance, language) });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const parsed = parseAmount(text);
    if (!parsed.ok) {
      setProblem(amountMessage(parsed.problem, t));
    } else if (parsed.amount > notice.customerBalance) {
      // A payment cannot exceed the debt; the balance here is the one the list was read with.
      setProblem(exceeds);
    } else {
      onSubmit(parsed.amount);
    }
  };

  const refused = error?.code === "EXCEEDS_BALANCE" ? errorText(error, t) : error?.code === "VALIDATION" && "amount" in error.fields ? amountMessage("invalid", t) : null;
  const shown = problem ?? refused;
  return (
    <form className="form notice" onSubmit={submit} noValidate>
      {error && refused === null ? (
        <p className="field__error" role="alert">
          {errorText(error, t)}
        </p>
      ) : null}
      <p>{t("notices.accept.question", { name: notice.customerName })}</p>
      <div className="field">
        <label htmlFor={id}>{t("notices.accept.amount")}</label>
        <input
          id={id}
          className="input input--amount"
          inputMode="numeric"
          autoComplete="off"
          value={text}
          aria-invalid={shown !== null}
          aria-describedby={`${id}-hint ${id}-error`}
          onChange={(event) => {
            setText(event.target.value);
            setProblem(null);
          }}
        />
        <p className="field__hint" id={`${id}-hint`}>
          {t("notices.accept.hint", { amount: formatMoney(notice.amount, language) })}
        </p>
        <FieldError id={`${id}-error`} message={shown} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("notices.accept.yes")}
        </button>
        <button type="button" className="button" onClick={onCancel} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </form>
  );
}

/**
 * Payment notices that wait for the shop (REQ-061). Any member of staff accepts one, which records a
 * payment in their name for the stated or a corrected amount, or declines it with a reason the customer
 * receives. The receipt, when there is one, is opened through a short-lived link.
 */
export default function PaymentNoticesScreen() {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const { state, reload } = useLoad((signal) => api.listPaymentNotices(signal), [api]);
  const [panel, setPanel] = useState<Panel>(null);
  const [done, setDone] = useState<string | null>(null);
  // A notice someone else decided first, or that expired: the list is read again and the reason stays.
  const [stale, setStale] = useState<ApiError | null>(null);

  const settle = (write: Promise<void>, message: string) =>
    write.then(
      () => {
        setPanel(null);
        setDone(message);
        reload();
      },
      (error: ApiError) => {
        if (isClosed(error)) {
          setPanel(null);
          setStale(error);
          reload();
        }
        throw error;
      },
    );
  const accept = useSubmit((payload: { id: string; amount: number; stated: number }, key) =>
    settle(
      // Only a correction is sent as an amount; the stated one is the server's to record.
      api.acceptPaymentNotice(payload.id, payload.amount === payload.stated ? null : payload.amount, key),
      t("notices.done.accepted", { amount: formatMoney(payload.amount, language) }),
    ),
  );
  const decline = useSubmit((payload: { id: string; reason: string }, key) =>
    settle(api.declinePaymentNotice(payload.id, payload.reason, key), t("notices.done.declined")),
  );
  const busy = accept.state.status === "pending" || decline.state.status === "pending";

  const open = (next: Panel) => {
    setPanel(next);
    setDone(null);
    setStale(null);
    accept.reset();
    decline.reset();
  };

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.data.length === 0) {
    body = <Empty>{t("notices.none")}</Empty>;
  } else {
    body = (
      <ul className="rows">
        {state.data.map((notice) => (
          <li key={notice.id} className="row">
            <Link to={`/customers/${notice.customerId}`} className="row__link">
              <span className="row__name">{notice.customerName}</span>
              <span className="row__amount">{formatMoney(notice.amount, language)}</span>
            </Link>
            <p className="row__note">
              {t("notices.row.stated", {
                amount: formatMoney(notice.amount, language),
                balance: formatMoney(notice.customerBalance, language),
              })}
            </p>
            <p className="row__meta">
              {t("notices.row.since", {
                date: formatInstant(notice.createdAt, language),
                expires: formatInstant(notice.expiresAt, language),
              })}
            </p>
            {notice.receiptSeenBefore ? <p className="row__warning">{t("notices.seenBefore")}</p> : null}
            {notice.hasReceipt ? <Receipt noticeId={notice.id} /> : <p className="row__meta">{t("notices.noReceipt")}</p>}
            {panel?.id !== notice.id ? (
              <p className="actions">
                <button
                  type="button"
                  className="button button--small"
                  onClick={() => open({ kind: "accept", id: notice.id })}
                  disabled={busy}
                >
                  {t("notices.accept")}
                </button>
                <button
                  type="button"
                  className="button button--small"
                  onClick={() => open({ kind: "decline", id: notice.id })}
                  disabled={busy}
                >
                  {t("notices.decline")}
                </button>
              </p>
            ) : panel.kind === "accept" ? (
              <AcceptForm
                notice={notice}
                pending={busy}
                error={accept.state.status === "error" ? accept.state.error : null}
                onSubmit={(amount) => accept.submit({ id: notice.id, amount, stated: notice.amount })}
                onCancel={() => open(null)}
              />
            ) : (
              <ReasonForm
                id={`decline-notice-${notice.id}`}
                label={t("disputes.decline.reason")}
                hint={t("disputes.decline.hint")}
                submitLabel={t("disputes.decline.submit")}
                pending={busy}
                error={decline.state.status === "error" ? decline.state.error : null}
                onSubmit={(reason) => decline.submit({ id: notice.id, reason })}
                onCancel={() => open(null)}
              />
            )}
          </li>
        ))}
      </ul>
    );
  }

  return (
    <>
      <p className="hint">{t("notices.hint")}</p>
      {done !== null ? (
        <p className="notice notice--done" role="status">
          {done}
        </p>
      ) : null}
      {stale ? (
        <div className="notice notice--error" role="alert">
          <p>{errorText(stale, t)}</p>
          <p>{t("notices.refused.notOpen")}</p>
        </div>
      ) : null}
      {body}
    </>
  );
}
