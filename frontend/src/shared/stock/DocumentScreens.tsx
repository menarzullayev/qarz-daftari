import { type ReactNode, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { Language } from "../../i18n/types";
import { formatCalendarDay, formatMoney } from "../format";
import { useLoad, usePagedList, useSubmit } from "../hooks";
import type { Column } from "../layout";
import { parseIsoDate } from "../promise";
import { Link, navigate } from "../router";
import { NotFoundScreen } from "../screens";
import { useMay, useWorkspace } from "../workspace/context";
import { Badge, Empty, Failure, formatInstant, Loading, LoadMore } from "../workspace/parts";
import type { ScanHost } from "./barcode";
import { draftOf } from "./documentDraft";
import { DocumentEditor, DocumentRefusal } from "./DocumentEditor";
import {
  CancelForm,
  DOCUMENT_KIND_LABELS,
  DOCUMENT_STATUS_LABELS,
  Fact,
  labelOf,
  Listing,
  NONE,
  permissionOfKind,
  qtyWithUnit,
  useStock,
  useStockSettings,
} from "./parts";
import {
  DOCUMENT_STATUSES,
  type DocumentLine,
  type DocumentStatus,
  type DocumentSummary,
  STOCK_DOCUMENT_KINDS,
  type StockDocument,
  type StockDocumentKind,
  type StockSettings,
} from "./stockApi";

/** The kind a quick receipt writes. */
const RECEIPT: StockDocumentKind = "receipt";

const STATUS_TONE = { draft: "warning", posted: "success", cancelled: "danger" } as const;

function dateText(iso: string, language: Language): string {
  const day = parseIsoDate(iso);
  return day === null ? iso : formatCalendarDay(day, language);
}

function StatusBadge({ status }: { status: DocumentStatus }) {
  const { t } = useI18n();
  return <Badge tone={STATUS_TONE[status]}>{t(DOCUMENT_STATUS_LABELS[status])}</Badge>;
}

/**
 * A document as it stands: what it says, its lines, and what may be done with it. A draft is changed,
 * posted or dropped; a posted one is only cancelled, with a reason, which reverses everything it did.
 * A stocktake's draft shows each count beside what the books hold now: the preview of what posting
 * will correct.
 */
export function DocumentView({
  document,
  settings,
  host,
  onChanged,
}: {
  document: StockDocument;
  settings: StockSettings;
  host?: ScanHost | undefined;
  /** The document after a change made here. */
  onChanged: (document: StockDocument) => void;
}) {
  const { t, language } = useI18n();
  const can = useMay();
  const stock = useStock();
  const [mode, setMode] = useState<"view" | "edit" | "cancel">("view");
  const post = useSubmit((id: string, key) => stock.postDocument(id, key).then(onChanged));
  const cancel = useSubmit((job: { id: string; reason: string }, key) =>
    stock.cancelDocument(job.id, job.reason, key).then((changed) => {
      setMode("view");
      onChanged(changed);
    }),
  );
  const allowed = can(permissionOfKind(document.kind));
  const { money } = document;
  const stocktake = document.kind === "stocktake";

  if (mode === "edit" && document.status === "draft") {
    return (
      <DocumentEditor
        kind={document.kind}
        settings={settings}
        initial={document}
        host={host}
        onClose={() => setMode("view")}
        onSaved={(saved) => {
          setMode("view");
          onChanged(saved);
        }}
      />
    );
  }

  const columns: Column<DocumentLine>[] = [
    { id: "item", header: t("stock.col.item"), rowHeader: true, cell: (line) => line.item.name },
    {
      id: "qty",
      header: t(stocktake ? "stock.col.counted" : "stock.col.qty"),
      numeric: true,
      cell: (line) => qtyWithUnit(line.qty, line.item.unit),
    },
  ];
  if (stocktake) {
    columns.push(
      {
        id: "expected",
        header: t("stock.col.expected"),
        numeric: true,
        cell: (line) => (line.expected == null ? NONE : qtyWithUnit(line.expected, line.item.unit)),
      },
      {
        id: "difference",
        header: t("stock.col.difference"),
        numeric: true,
        cell: (line) => (line.difference == null ? NONE : qtyWithUnit(line.difference, line.item.unit)),
      },
    );
  }
  // Prices are in the answer only for a member who may read them: without them there is no column.
  if (money && document.lines.some((line) => line.unitCost !== undefined)) {
    columns.push(
      {
        id: "cost",
        header: t(document.kind === "customer_return" ? "stock.line.price" : "stock.line.cost"),
        numeric: true,
        cell: (line) => (line.unitCost === undefined ? NONE : formatMoney(line.unitCost, language, money.currency)),
      },
      {
        id: "total",
        header: t("stock.doc.total"),
        numeric: true,
        cell: (line) => (line.lineTotal === undefined ? NONE : formatMoney(line.lineTotal, language, money.currency)),
      },
    );
  }
  const failure = post.state.status === "error" ? post.state.error : null;
  const editable = document.status === "draft" && draftOf(document) !== null;

  return (
    <>
      <h2 className="subject">
        {t("stock.doc.ref", { kind: t(DOCUMENT_KIND_LABELS[document.kind]), number: document.number })}{" "}
        <StatusBadge status={document.status} />
      </h2>
      {failure ? <DocumentRefusal error={failure} /> : null}
      <dl className="facts">
        <Fact name={t("stock.doc.date")}>{dateText(document.docDate, language)}</Fact>
        {document.supplier ? <Fact name={t("stock.doc.supplier")}>{document.supplier.name}</Fact> : null}
        {document.customer ? <Fact name={t("stock.doc.customer")}>{document.customer.name}</Fact> : null}
        {document.reason !== null ? (
          <Fact name={t("stock.doc.reason")}>{labelOf(settings.writeOffReasons, document.reason, language)}</Fact>
        ) : null}
        {document.note !== null ? <Fact name={t("stock.doc.note")}>{document.note}</Fact> : null}
        {money ? (
          <>
            <Fact name={t("stock.doc.total")}>{formatMoney(money.total, language, money.currency)}</Fact>
            <Fact name={t(document.kind === "customer_return" ? "stock.return.paid" : "stock.doc.paid")}>
              {formatMoney(money.paid, language, money.currency)}
            </Fact>
            {document.kind === "customer_return" ? (
              <Fact name={t("stock.return.offDebt")}>{formatMoney(money.total - money.paid, language, money.currency)}</Fact>
            ) : null}
            {document.kind === "receipt" && document.supplier ? (
              <Fact name={t("stock.doc.owed")}>{formatMoney(money.total - money.paid, language, money.currency)}</Fact>
            ) : null}
          </>
        ) : null}
        {document.postedAt !== null ? (
          <Fact name={t("stock.doc.postedAt")}>{formatInstant(document.postedAt, language)}</Fact>
        ) : null}
        {document.cancelledAt !== null ? (
          <Fact name={t("stock.doc.cancelledAt")}>{formatInstant(document.cancelledAt, language)}</Fact>
        ) : null}
        {document.cancelReason !== null ? <Fact name={t("stock.doc.cancelReason")}>{document.cancelReason}</Fact> : null}
      </dl>
      {stocktake && document.status === "draft" ? <p className="hint">{t("stock.take.preview")}</p> : null}
      <Listing caption={t("stock.doc.lines")} columns={columns} items={document.lines} rowKey={(line) => String(line.lineNo)} />

      {mode === "cancel" ? (
        <CancelForm
          id="stock-doc-cancel"
          label={t("stock.doc.cancel.reason")}
          submitLabel={t(document.status === "draft" ? "stock.doc.drop" : "stock.doc.cancel")}
          pending={cancel.state.status === "pending"}
          error={cancel.state.status === "error" ? cancel.state.error : null}
          onSubmit={(reason) => cancel.submit({ id: document.id, reason })}
          onClose={() => {
            setMode("view");
            cancel.reset();
          }}
        />
      ) : null}
      {/* What a member may not do with this kind of document is not offered; the server refuses it anyway. */}
      {allowed && mode === "view" && document.status !== "cancelled" ? (
        <p className="actions">
          {document.status === "draft" ? (
            <button
              type="button"
              className="button button--primary"
              disabled={post.state.status === "pending"}
              onClick={() => post.submit(document.id)}
            >
              {post.state.status === "pending" ? t("state.saving") : t("stock.doc.post")}
            </button>
          ) : null}
          {editable ? (
            <button type="button" className="button" onClick={() => setMode("edit")}>
              {t("action.edit")}
            </button>
          ) : null}
          <button type="button" className="button button--danger" onClick={() => setMode("cancel")}>
            {t(document.status === "draft" ? "stock.doc.drop" : "stock.doc.cancel")}
          </button>
        </p>
      ) : null}
    </>
  );
}

function withSettings(
  settings: ReturnType<typeof useStockSettings>,
  render: (settings: StockSettings) => ReactNode,
): ReactNode {
  if (settings.state.status === "loading") {
    return <Loading />;
  }
  if (settings.state.status === "error") {
    return <Failure error={settings.state.error} onRetry={settings.reload} />;
  }
  return render(settings.state.data);
}

/** One document by its address, in the web panel. */
export function DocumentScreen({ documentId, host }: { documentId: string; host?: ScanHost | undefined }) {
  const { t } = useI18n();
  const stock = useStock();
  const settings = useStockSettings();
  const { state, reload } = useLoad((signal) => stock.document(documentId, signal), [stock, documentId]);
  // What a change made here answered with; shown until the address changes.
  const [changed, setChanged] = useState<StockDocument | null>(null);
  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return state.error.status === 404 ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  const document = changed?.id === state.data.id ? changed : state.data;
  return (
    <>
      {withSettings(settings, (known) => (
        <DocumentView document={document} settings={known} host={host} onChanged={setChanged} />
      ))}
      <p className="actions">
        <Link to="/stock-documents" className="button">
          {t("nav.stockDocuments")}
        </Link>
      </p>
    </>
  );
}

/** A new document of a kind, in the web panel; once saved, its own page opens. */
export function NewDocumentScreen({ kind, host }: { kind: StockDocumentKind; host?: ScanHost | undefined }) {
  const can = useMay();
  // A kind the member may not write is not a screen for them, and nothing is asked on their behalf.
  return can(permissionOfKind(kind)) ? <NewDocument kind={kind} host={host} /> : <NotFoundScreen />;
}

function NewDocument({ kind, host }: { kind: StockDocumentKind; host?: ScanHost | undefined }) {
  const settings = useStockSettings();
  return withSettings(settings, (known) => (
    <DocumentEditor
      kind={kind}
      settings={known}
      host={host}
      onSaved={(saved) => navigate(`/stock-documents/${saved.id}`)}
      onClose={() => navigate("/stock-documents")}
    />
  ));
}

/**
 * A quick receipt at the counter: what came in, from whom, for how much, paid or on credit. Posted at
 * once or kept as a draft; either way what was saved is shown where it was written, so a draft can be
 * posted without leaving the screen.
 */
export function ReceiptScreen({ host }: { host?: ScanHost | undefined }) {
  const can = useMay();
  return can("stock.receive") ? <QuickReceipt host={host} /> : <NotFoundScreen />;
}

function QuickReceipt({ host }: { host?: ScanHost | undefined }) {
  const { t } = useI18n();
  const settings = useStockSettings();
  const [saved, setSaved] = useState<StockDocument | null>(null);
  const [round, setRound] = useState(0);
  return withSettings(settings, (known) =>
    saved === null ? (
      <DocumentEditor key={round} kind={RECEIPT} settings={known} host={host} onSaved={setSaved} onClose={() => navigate("/stock")} />
    ) : (
      <>
        <p className="notice notice--done" role="status">
          {t(saved.status === "posted" ? "stock.receipt.posted" : saved.status === "draft" ? "stock.receipt.draft" : "stock.receipt.cancelled", {
            number: saved.number,
          })}
        </p>
        <DocumentView document={saved} settings={known} host={host} onChanged={setSaved} />
        <p className="actions">
          <button
            type="button"
            className="button"
            onClick={() => {
              setSaved(null);
              setRound((count) => count + 1);
            }}
          >
            {t("stock.receipt.another")}
          </button>
          <Link to="/stock" className="button">
            {t("nav.stock")}
          </Link>
        </p>
      </>
    ),
  );
}

/** Every document of the stock, newest first, by kind and by state. */
export function DocumentsScreen() {
  const { t, language } = useI18n();
  const can = useMay();
  const stock = useStock();
  const { membershipId } = useWorkspace();
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) => stock.documents({ kind, status, cursor }, signal),
    [stock, kind, status],
  );
  // Each kind is offered to those who may write it: receiving goods is one permission, correcting another.
  const kinds = STOCK_DOCUMENT_KINDS.filter((known) => can(permissionOfKind(known)));

  const columns: Column<DocumentSummary>[] = [
    {
      id: "document",
      header: t("stock.col.document"),
      rowHeader: true,
      cell: (row) => (
        <Link to={`/stock-documents/${row.id}`}>
          {t("stock.doc.ref", { kind: t(DOCUMENT_KIND_LABELS[row.kind]), number: row.number })}
        </Link>
      ),
    },
    { id: "date", header: t("stock.doc.date"), cell: (row) => dateText(row.docDate, language) },
    { id: "status", header: t("stock.col.status"), cell: (row) => <StatusBadge status={row.status} /> },
    { id: "party", header: t("stock.col.party"), cell: (row) => row.supplier?.name ?? row.customer?.name ?? NONE },
    {
      id: "total",
      header: t("stock.doc.total"),
      numeric: true,
      cell: (row) => (row.money ? formatMoney(row.money.total, language, row.money.currency) : NONE),
    },
    {
      id: "author",
      header: t("stock.col.author"),
      cell: (row) => (row.createdBy === membershipId ? t("stock.author.you") : row.createdBy.slice(-6)),
    },
  ];

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    body = <Empty>{t("stock.docs.none")}</Empty>;
  } else {
    body = (
      <>
        <Listing caption={t("nav.stockDocuments")} columns={columns} items={state.items} rowKey={(row) => row.id} />
        {state.nextCursor !== null ? <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} /> : null}
      </>
    );
  }

  return (
    <>
      <nav className="actions" aria-label={t("stock.docs.new")}>
        {kinds.map((known) => (
          <Link key={known} to={`/stock-documents/new/${known}`} className="button">
            {t(DOCUMENT_KIND_LABELS[known])}
          </Link>
        ))}
      </nav>
      <section className="stock-filters" aria-label={t("stock.docs.filters")}>
        <div className="field">
          <label htmlFor="stock-docs-kind">{t("stock.docs.kind")}</label>
          <select id="stock-docs-kind" className="input" value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="">{t("stock.docs.kind.all")}</option>
            {STOCK_DOCUMENT_KINDS.map((known) => (
              <option key={known} value={known}>
                {t(DOCUMENT_KIND_LABELS[known])}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="stock-docs-status">{t("stock.col.status")}</label>
          <select id="stock-docs-status" className="input" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">{t("stock.docs.status.all")}</option>
            {DOCUMENT_STATUSES.map((known) => (
              <option key={known} value={known}>
                {t(DOCUMENT_STATUS_LABELS[known])}
              </option>
            ))}
          </select>
        </div>
      </section>
      {body}
    </>
  );
}
