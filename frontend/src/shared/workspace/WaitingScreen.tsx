import { useEffect, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { ApiError, Customer, WaitingPerson } from "../api";
import { formatMoney } from "../format";
import { useLoad, usePagedList, useSubmit } from "../hooks";
import { Link } from "../router";
import { useMay, useWorkspace } from "./context";
import { SEARCH_DELAY_MS } from "./CustomersScreen";
import { Confirm, Empty, errorText, Failure, formatInstant, Loading, LoadMore } from "./parts";

const MAX_QUERY_LENGTH = 80;
const PICK_PAGE = 10;

/** Finds the customer record a waiting person belongs to, then asks before attaching. */
function AttachPanel({
  person,
  pending,
  error,
  onAttach,
  onCancel,
}: {
  person: WaitingPerson;
  pending: boolean;
  error: ApiError | null;
  onAttach: (customer: Customer) => void;
  onCancel: () => void;
}) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [target, setTarget] = useState<Customer | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(text.trim()), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  // Only an active customer can be connected; the server refuses an archived one.
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) => api.listCustomers({ q: query, status: "active", cursor, limit: PICK_PAGE }, signal),
    [api, query],
  );

  if (target !== null) {
    return (
      <Confirm
        question={t("waiting.attach.confirm", { name: person.name, customer: target.displayName })}
        yes={t("waiting.attach.yes")}
        no={t("action.back")}
        pending={pending}
        error={error}
        onYes={() => onAttach(target)}
        onNo={() => setTarget(null)}
      />
    );
  }

  return (
    <div className="notice">
      <input
        type="search"
        className="input"
        value={text}
        maxLength={MAX_QUERY_LENGTH}
        autoComplete="off"
        aria-label={t("waiting.attach.search")}
        placeholder={t("waiting.attach.search")}
        onChange={(event) => setText(event.target.value)}
      />
      {state.status === "loading" ? <Loading /> : null}
      {state.status === "error" ? <Failure error={state.error} onRetry={reload} /> : null}
      {state.status === "ready" && state.items.length === 0 ? <p className="state">{t("customers.noMatch")}</p> : null}
      {state.status === "ready" && state.items.length > 0 ? (
        <>
          <ul className="picks" aria-label={t("waiting.attach.list")}>
            {state.items.map((customer) => (
              <li key={customer.id}>
                <button type="button" className="pick" onClick={() => setTarget(customer)}>
                  <span className="row__name">{customer.displayName}</span>
                  <span className="row__meta">{formatMoney(customer.balance, language)}</span>
                </button>
              </li>
            ))}
          </ul>
          {state.nextCursor !== null ? (
            <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} />
          ) : null}
        </>
      ) : null}
      <p className="actions">
        <button type="button" className="button" onClick={onCancel}>
          {t("action.cancel")}
        </button>
      </p>
    </div>
  );
}

/**
 * People who scanned the counter code and agreed to be connected (REQ-013). A member who may add
 * customers attaches one to the customer record that is theirs, or dismisses the request; one who only
 * reads the book sees who is waiting.
 */
export function WaitingScreen() {
  const { api } = useWorkspace();
  const can = useMay();
  const mayDecide = can("customers.create");
  const { t, language } = useI18n();
  const { state, reload } = useLoad((signal) => api.listWaiting(signal), [api]);
  const [attaching, setAttaching] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const settled = (message: string) => () => {
    setAttaching(null);
    setDone(message);
    reload();
  };
  const attach = useSubmit((payload: { waitingId: string; customerId: string; message: string }, key) =>
    api.attachWaiting(payload.waitingId, payload.customerId, key).then(settled(payload.message)),
  );
  const dismiss = useSubmit((payload: { waitingId: string; message: string }, key) =>
    api.dismissWaiting(payload.waitingId, key).then(settled(payload.message)),
  );
  // One write at a time for the whole list: a second tap, on this row or another, waits for the first.
  const busy = attach.state.status === "pending" || dismiss.state.status === "pending";

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.data.length === 0) {
    body = <Empty>{t("waiting.none")}</Empty>;
  } else {
    body = (
      <ul className="rows">
        {state.data.map((person) => (
          <li key={person.id} className="row">
            <p className="row__name">{person.name}</p>
            <p className="row__meta">{t("waiting.since", { date: formatInstant(person.since, language) })}</p>
            {attaching === person.id ? (
              <AttachPanel
                person={person}
                pending={busy}
                error={attach.state.status === "error" ? attach.state.error : null}
                onAttach={(customer) =>
                  attach.submit({
                    waitingId: person.id,
                    customerId: customer.id,
                    message: t("waiting.attached", { name: person.name, customer: customer.displayName }),
                  })
                }
                onCancel={() => {
                  setAttaching(null);
                  attach.reset();
                }}
              />
            ) : !mayDecide ? null : (
              <p className="actions">
                <button
                  type="button"
                  className="button button--primary"
                  onClick={() => {
                    setAttaching(person.id);
                    setDone(null);
                    attach.reset();
                  }}
                  disabled={busy}
                >
                  {t("waiting.attach")}
                </button>
                <button
                  type="button"
                  className="button"
                  onClick={() => {
                    setDone(null);
                    dismiss.submit({ waitingId: person.id, message: t("waiting.dismissed", { name: person.name }) });
                  }}
                  disabled={busy}
                >
                  {t("waiting.dismiss")}
                </button>
              </p>
            )}
          </li>
        ))}
      </ul>
    );
  }

  return (
    <>
      <p className="hint">{t("waiting.hint")}</p>
      {done !== null ? (
        <p className="notice notice--done" role="status">
          {done}
        </p>
      ) : null}
      {dismiss.state.status === "error" ? (
        <p className="notice notice--error" role="alert">
          {errorText(dismiss.state.error, t)}
        </p>
      ) : null}
      {body}
      <p className="actions">
        <Link to="/customers/counter-code" className="button">
          {t("counter.title")}
        </Link>
      </p>
    </>
  );
}
