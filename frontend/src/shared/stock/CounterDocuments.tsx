import { useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import { formatMoney } from "../format";
import { usePagedList, useSubmit } from "../hooks";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { useMay } from "../workspace/context";
import { Empty, Failure, Loading, LoadMore } from "../workspace/parts";
import { dateText, mayWriteDocuments, StatusBadge } from "./DocumentScreens";
import { CancelForm, DOCUMENT_KIND_LABELS, DOCUMENT_STATUS_LABELS, permissionOfKind, useStock } from "./parts";
import { DOCUMENT_STATUSES, type DocumentStatus, type DocumentSummary } from "./stockApi";

/** What the list shows: the drafts first of all, since they are what was left to be finished. */
type Shown = DocumentStatus | "all";
const SHOWN: readonly Shown[] = [...DOCUMENT_STATUSES, "all"];
const SHOWN_LABELS: Readonly<Record<Shown, MessageKey>> = { ...DOCUMENT_STATUS_LABELS, all: "stock.filter.all" };

/**
 * The stock's documents on a phone (the Mini App has no documents section): rows, newest first, the
 * drafts to begin with. A draft left by the quick receipt is found here, continued in its form, or
 * thrown away, which is a cancellation with a reason like any other. Read by whoever writes some kind
 * of document; each row offers its actions only to a member who may write that kind.
 */
export function CounterDocumentsScreen() {
  const can = useMay();
  return mayWriteDocuments(can) ? <CounterDocuments /> : <NotFoundScreen />;
}

function CounterDocuments() {
  const { t, language } = useI18n();
  const can = useMay();
  const stock = useStock();
  const [shown, setShown] = useState<Shown>("draft");
  const [dropping, setDropping] = useState<string | null>(null);
  const [dropped, setDropped] = useState(false);
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) => stock.documents({ status: shown === "all" ? "" : shown, cursor }, signal),
    [stock, shown],
  );
  const drop = useSubmit((job: { id: string; reason: string }, key) =>
    stock.cancelDocument(job.id, job.reason, key).then(() => {
      setDropping(null);
      setDropped(true);
      reload();
    }),
  );
  const closeDrop = () => {
    setDropping(null);
    drop.reset();
  };

  const row = (document: DocumentSummary) => {
    const address = `/stock/documents/${document.id}`;
    const party = document.supplier?.name ?? document.customer?.name ?? null;
    // What a member may not do with this kind of document is not offered; the server refuses it anyway.
    const mine = document.status === "draft" && can(permissionOfKind(document.kind));
    return (
      <li key={document.id} className="row">
        <p className="row__name">
          <Link to={address}>{t("stock.doc.ref", { kind: t(DOCUMENT_KIND_LABELS[document.kind]), number: document.number })}</Link>{" "}
          <StatusBadge status={document.status} />
        </p>
        <p className="row__meta">{party === null ? dateText(document.docDate, language) : `${dateText(document.docDate, language)} · ${party}`}</p>
        {document.money ? <p className="row__meta">{formatMoney(document.money.total, language, document.money.currency)}</p> : null}
        {mine && dropping !== document.id ? (
          <p className="actions">
            <Link to={address} className="button button--primary">
              {t("stock.docs.continue")}
            </Link>
            <button
              type="button"
              className="button button--danger"
              onClick={() => {
                drop.reset();
                setDropped(false);
                setDropping(document.id);
              }}
            >
              {t("stock.doc.drop")}
            </button>
          </p>
        ) : null}
        {mine && dropping === document.id ? (
          <CancelForm
            id="stock-draft-drop"
            label={t("stock.doc.cancel.reason")}
            submitLabel={t("stock.doc.drop")}
            pending={drop.state.status === "pending"}
            error={drop.state.status === "error" ? drop.state.error : null}
            onSubmit={(reason) => drop.submit({ id: document.id, reason })}
            onClose={closeDrop}
          />
        ) : null}
      </li>
    );
  };

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    body = <Empty>{t(shown === "draft" ? "stock.docs.drafts.none" : "stock.docs.none")}</Empty>;
  } else {
    body = (
      <>
        <ul className="rows" aria-label={t("nav.stockDocuments")}>
          {state.items.map(row)}
        </ul>
        {state.nextCursor !== null ? <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} /> : null}
      </>
    );
  }

  return (
    <>
      <div className="bar">
        <div className="toggle" role="group" aria-label={t("stock.docs.show")}>
          {SHOWN.map((option) => (
            <button
              key={option}
              type="button"
              className="toggle__option"
              aria-pressed={shown === option}
              onClick={() => {
                closeDrop();
                setDropped(false);
                setShown(option);
              }}
            >
              {t(SHOWN_LABELS[option])}
            </button>
          ))}
        </div>
        {can("stock.receive") ? (
          <Link to="/stock/receipt" className="button button--primary">
            {t("stock.receipt.quick")}
          </Link>
        ) : null}
      </div>
      {dropped ? (
        <p className="notice notice--done" role="status">
          {t("stock.docs.dropped")}
        </p>
      ) : null}
      {body}
      <p className="actions">
        <Link to="/stock" className="button">
          {t("nav.stock")}
        </Link>
      </p>
    </>
  );
}
