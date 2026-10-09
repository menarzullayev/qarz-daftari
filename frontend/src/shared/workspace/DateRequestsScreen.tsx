import { useState, type FormEvent } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import type { ApiError, OpenDateRequest } from "../api";
import { DATE_REASON_MAX, reasonFits, tidyReason } from "../dateRules";
import { formatMoney } from "../format";
import { currencyOf } from "../money";
import { useLoad, useSubmit } from "../hooks";
import { dayText } from "../promiseParts";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { useMay, useWorkspace } from "./context";
import { Confirm, Empty, errorText, Failure, FieldError, formatInstant, Loading } from "./parts";

type Panel = { kind: "accept" | "decline"; id: string } | null;

/** The server's word for a request someone else has answered already (`fields.reason`). */
function isClosed(error: ApiError): boolean {
  return error.code === "DATE_REQUEST_NOT_ALLOWED" && error.fields["reason"] === "not_open";
}

/**
 * Declining is the question and its answer in one: the reason is optional, and nothing is sent until
 * the button that says "yes, decline" is pressed.
 */
function DeclineForm({
  request,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  request: OpenDateRequest;
  pending: boolean;
  error: ApiError | null;
  onSubmit: (reason: string | null) => void;
  onCancel: () => void;
}) {
  const { t, language } = useI18n();
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const id = `decline-date-${request.id}`;
  const tooLong = t("dates.reason.tooLong", { max: DATE_REASON_MAX });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const reason = tidyReason(text);
    if (!reasonFits(reason)) {
      setProblem(tooLong);
      return;
    }
    onSubmit(reason);
  };

  const refusedReason = error?.code === "VALIDATION" && "reason" in error.fields;
  const shown = problem ?? (refusedReason ? tooLong : null);
  return (
    <form className="form notice" onSubmit={submit} noValidate>
      {error && !refusedReason ? (
        <p className="field__error" role="alert">
          {errorText(error, t)}
        </p>
      ) : null}
      <p>{t("dates.decline.confirm", { name: request.customerName, date: dayText(request.requestedDate, language) })}</p>
      <div className="field">
        <label htmlFor={id}>{t("dates.decline.reason")}</label>
        <textarea
          id={id}
          className="input input--text"
          rows={2}
          value={text}
          maxLength={1000}
          aria-invalid={shown !== null}
          aria-describedby={`${id}-hint ${id}-error`}
          onChange={(event) => {
            setText(event.target.value);
            setProblem(null);
          }}
        />
        <p className="field__hint" id={`${id}-hint`}>
          {t("dates.decline.hint", { max: DATE_REASON_MAX })}
        </p>
        <FieldError id={`${id}-error`} message={shown} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("dates.decline.submit")}
        </button>
        <button type="button" className="button" onClick={onCancel} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </form>
  );
}

function OpenRequests() {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const { state, reload } = useLoad((signal) => api.listDateRequests(signal), [api]);
  const [panel, setPanel] = useState<Panel>(null);
  const [done, setDone] = useState<MessageKey | null>(null);
  // A request another manager answered first: the list is read again and the reason stays on screen.
  const [stale, setStale] = useState<ApiError | null>(null);

  const settle = (write: Promise<void>, message: MessageKey) =>
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
  const accept = useSubmit((id: string, key) => settle(api.acceptDateRequest(id, key), "dates.done.accepted"));
  const decline = useSubmit((payload: { id: string; reason: string | null }, key) =>
    settle(api.declineDateRequest(payload.id, payload.reason, key), "dates.done.declined"),
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
    body = <Empty>{t("dates.none")}</Empty>;
  } else {
    body = (
      <ul className="rows">
        {state.data.map((request) => {
          const requested = dayText(request.requestedDate, language);
          return (
            <li key={request.id} className="row">
              <Link to={`/customers/${request.customerId}`} className="row__link">
                <span className="row__name">{request.customerName}</span>
                <span className="row__amount">{formatMoney(request.amount, language, currencyOf(request))}</span>
              </Link>
              <p className="row__note">
                {t("dates.row.dates", {
                  current: request.promisedDate === null ? "—" : dayText(request.promisedDate, language),
                  requested,
                })}
              </p>
              {request.reason ? <p className="row__note">{t("dates.row.reason", { reason: request.reason })}</p> : null}
              <p className="row__meta">{t("dates.row.since", { date: formatInstant(request.createdAt, language) })}</p>
              {panel?.id !== request.id ? (
                <p className="actions">
                  <button
                    type="button"
                    className="button button--small"
                    onClick={() => open({ kind: "accept", id: request.id })}
                    disabled={busy}
                  >
                    {t("dates.accept")}
                  </button>
                  <button
                    type="button"
                    className="button button--small"
                    onClick={() => open({ kind: "decline", id: request.id })}
                    disabled={busy}
                  >
                    {t("dates.decline")}
                  </button>
                </p>
              ) : panel.kind === "accept" ? (
                <Confirm
                  question={t("dates.accept.confirm", {
                    name: request.customerName,
                    amount: formatMoney(request.amount, language, currencyOf(request)),
                    date: requested,
                  })}
                  yes={t("dates.accept.yes")}
                  no={t("action.cancel")}
                  pending={busy}
                  error={accept.state.status === "error" ? accept.state.error : null}
                  onYes={() => accept.submit(request.id)}
                  onNo={() => open(null)}
                />
              ) : (
                <DeclineForm
                  request={request}
                  pending={busy}
                  error={decline.state.status === "error" ? decline.state.error : null}
                  onSubmit={(reason) => decline.submit({ id: request.id, reason })}
                  onCancel={() => open(null)}
                />
              )}
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <>
      <nav className="actions" aria-label={t("nav.disputes")}>
        <Link to="/disputes" className="button">
          {t("disputes.title")}
        </Link>
      </nav>
      <p className="hint">{t("dates.hint")}</p>
      {done !== null ? (
        <p className="notice notice--done" role="status">
          {t(done)}
        </p>
      ) : null}
      {stale ? (
        <div className="notice notice--error" role="alert">
          <p>{errorText(stale, t)}</p>
          <p>{t("dates.refused.notOpen")}</p>
        </div>
      ) : null}
      {body}
    </>
  );
}

/**
 * Open requests to move a promised date (REQ-067). A manager or an owner accepts one, and the date
 * asked for becomes the entry's promised date, or declines it, with a reason if they wish. A seller has
 * no such list: the route is not in their navigation, and this screen calls nothing for them.
 */
export default function DateRequestsScreen() {
  const can = useMay();
  return can("promises.change") ? <OpenRequests /> : <NotFoundScreen />;
}
