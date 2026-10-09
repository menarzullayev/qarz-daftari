import { useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { ApiError } from "../api";
import { formatMoney } from "../format";
import { parsePrice } from "../goods";
import { useLoad, usePagedList, useSubmit } from "../hooks";
import { ListIcon } from "../icons";
import type { Column } from "../layout";
import { parseMoney, usdInput } from "../money";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { ItemFinder } from "../stock/ItemFinder";
import { Fact, Listing, MethodChoice, qtyWithUnit, useStockSettings } from "../stock/parts";
import { readCount, readUnitCost } from "../stock/quantity";
import type { ItemFilter } from "../stock/stockApi";
import { useMay } from "../workspace/context";
import { Empty, errorText, Failure, FieldError, formatInstant, Loading, LoadMore } from "../workspace/parts";
import { type ConfirmLine, type Method, type Note, NOTE_STATUSES, type NoteLine, type NoteStatus, type NoteSummary } from "./networkApi";
import { History, money, NOTE_STATUS_LABELS, NOTE_TERMS_LABELS, ReasonBox, roleText, Status, TAB, Tabs, useNetwork, useUnitLabel } from "./parts";

const ALL = "all";
type Filter = NoteStatus | typeof ALL;
/** A delivered line goes onto any item of the buyer's catalogue, counted in the stock or not. */
const ANY_ITEM: ItemFilter = "all";

/** The delivery notes this shop issued or was issued; `waiting` opens on the ones that await an answer. */
export function NotesScreen({ waiting }: { waiting: boolean }) {
  const { t, language } = useI18n();
  const network = useNetwork();
  const [status, setStatus] = useState<Filter>(waiting ? "issued" : ALL);
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) => network.notes({ status: status === ALL ? undefined : status, cursor }, signal),
    [network, status],
  );
  const columns: Column<NoteSummary>[] = [
    {
      id: "note",
      header: t("net.col.note"),
      rowHeader: true,
      cell: (row) => <Link to={`/network/notes/${row.id}`}>{t("net.note.ref", { number: row.number })}</Link>,
    },
    { id: "order", header: t("net.col.order"), cell: (row) => <Link to={`/network/orders/${row.orderId}`}>{t("net.order.ref", { number: row.orderNumber })}</Link> },
    { id: "partner", header: t("net.col.partner"), cell: (row) => row.partnerName ?? t("net.partner.unnamed") },
    { id: "role", header: t("net.col.role"), cell: (row) => roleText(row.role, t) },
    { id: "status", header: t("net.col.state"), cell: (row) => <Status status={row.status} label={NOTE_STATUS_LABELS[row.status]} /> },
    { id: "total", header: t("net.col.total"), numeric: true, cell: (row) => <span className="money">{money(row.total, row.currency, language)}</span> },
    { id: "terms", header: t("net.col.terms"), cell: (row) => t(NOTE_TERMS_LABELS[row.terms]) },
    { id: "issued", header: t("net.col.issued"), cell: (row) => formatInstant(row.issuedAt, language) },
  ];
  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    body = <Empty icon={<ListIcon />}>{t(status === "issued" ? "net.notes.none.waiting" : "net.notes.none")}</Empty>;
  } else {
    body = (
      <>
        <Listing caption={t("net.tab.notes")} columns={columns} items={state.items} rowKey={(row) => row.id} />
        {state.nextCursor !== null ? <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} /> : null}
      </>
    );
  }
  return (
    <>
      <Tabs current={TAB.notes} />
      <div className="bar">
        <div className="field net-filter">
          <label htmlFor="net-notes-status">{t("net.col.state")}</label>
          <select
            id="net-notes-status"
            className="input"
            value={status}
            onChange={(event) => setStatus(NOTE_STATUSES.find((known) => known === event.target.value) ?? ALL)}
          >
            <option value={ALL}>{t("net.filter.all")}</option>
            {NOTE_STATUSES.map((option) => (
              <option key={option} value={option}>
                {t(NOTE_STATUS_LABELS[option])}
              </option>
            ))}
          </select>
        </div>
      </div>
      {body}
    </>
  );
}

/** Where one delivered line goes in the buyer's catalogue, as the form holds it. */
type Target =
  | { mode: "none" }
  | { mode: "item"; itemId: string; name: string | null }
  | { mode: "new"; price: string; barcode: string };

/**
 * The line of the form a refusal speaks of. The server names it in `fields.line`: its place in the
 * receipt, counted from zero, which is its place among the note's lines (a receipt has every line of the
 * note, in the note's order). Without one, the only new item there is.
 */
function refusedLine(error: ApiError, note: Note, targets: Readonly<Record<number, Target>>): number | null {
  const said = error.fields["line"];
  const line = said !== undefined && /^\d+$/.test(said) ? note.lines[Number(said)] : undefined;
  if (line && targets[line.lineNo]?.mode === "new") {
    return line.lineNo;
  }
  const added = note.lines.filter((line) => targets[line.lineNo]?.mode === "new");
  return added.length === 1 ? (added[0]?.lineNo ?? null) : null;
}

/**
 * The buyer confirms what was delivered. Every line must end on one of the buyer's own catalogue items
 * in the same unit (the one chosen on the order is there already), or be added as a new item with the
 * price it will be sold for. Confirming receives the goods into the stock and writes the supplier's
 * sale on credit in one step: the button says so, and nothing is sent before it is pressed.
 */
function ConfirmForm({ note, onDone, onClose }: { note: Note; onDone: () => void; onClose: () => void }) {
  const { t } = useI18n();
  const can = useMay();
  const network = useNetwork();
  const settings = useStockSettings();
  const unitLabel = useUnitLabel();
  const [targets, setTargets] = useState<Record<number, Target>>(() =>
    Object.fromEntries(
      note.lines.map((line) => [line.lineNo, line.itemId === null ? ({ mode: "none" } as Target) : ({ mode: "item", itemId: line.itemId, name: null } as Target)]),
    ),
  );
  const [method, setMethod] = useState<Method>("cash");
  const [checked, setChecked] = useState(false);
  const [wrongUnit, setWrongUnit] = useState<number | null>(null);
  const save = useSubmit((job: { lines: ConfirmLine[]; method: Method | null }, key) => network.confirmNote(note.id, job, key).then(onDone));
  const pending = save.state.status === "pending";
  // How the money handed over on delivery was paid: asked only in a shop that keeps a cash book.
  const asksMethod = note.paid > 0 && settings.state.status === "ready" && settings.state.data.cashBook;
  const target = (line: NoteLine): Target => targets[line.lineNo] ?? { mode: "none" };
  const set = (lineNo: number, next: Target) => {
    setTargets((current) => ({ ...current, [lineNo]: next }));
    setChecked(false);
    setWrongUnit(null);
  };
  const send = () => {
    const lines: ConfirmLine[] = [];
    for (const line of note.lines) {
      const chosen = target(line);
      if (chosen.mode === "item") {
        lines.push({ lineNo: line.lineNo, itemId: chosen.itemId });
        continue;
      }
      const price = chosen.mode === "new" ? parsePrice(chosen.price) : null;
      if (chosen.mode !== "new" || price === null || !price.ok) {
        setChecked(true);
        return;
      }
      const barcode = chosen.barcode.trim();
      lines.push({ lineNo: line.lineNo, newPrice: price.amount, newBarcode: barcode === "" ? null : barcode });
    }
    save.submit({ lines, method: asksMethod ? method : null });
  };
  const failure = save.state.status === "error" ? save.state.error : null;
  // The catalogue already has an item of that name: the server names it, and it can be used as it is.
  const existing = failure?.code === "CATALOG_NAME_TAKEN" ? (failure.fields["existing_id"] ?? null) : null;
  const taken = failure !== null && existing !== null ? refusedLine(failure, note, targets) : null;
  return (
    <div className="form notice" role="group" aria-label={t("net.note.confirm")}>
      {failure ? (
        <div role="alert">
          <p className="field__error">{errorText(failure, t)}</p>
          {existing !== null && taken !== null ? (
            <p className="actions">
              <button
                type="button"
                className="button button--small"
                onClick={() => {
                  set(taken, { mode: "item", itemId: existing, name: note.lines.find((line) => line.lineNo === taken)?.name ?? null });
                  save.reset();
                }}
              >
                {t("net.note.confirm.useExisting")}
              </button>
            </p>
          ) : null}
        </div>
      ) : null}
      <p className="hint">{t("net.note.confirm.hint")}</p>
      <ul className="rows" aria-label={t("net.note.lines")}>
        {note.lines.map((line) => {
          const id = `net-confirm-${line.lineNo}`;
          const chosen = target(line);
          const price = chosen.mode === "new" ? parsePrice(chosen.price) : null;
          return (
            <li key={line.lineNo} className="row">
              <p className="row__name">{line.name}</p>
              <p className="row__meta">{qtyWithUnit(line.qty, unitLabel(line.unit))}</p>
              {chosen.mode === "item" ? (
                <p className="row__meta">{chosen.name === null ? t("net.note.confirm.fromOrder") : t("net.line.ownItem", { name: chosen.name })}</p>
              ) : null}
              {chosen.mode === "none" && checked ? (
                <p className="field__error" role="alert">
                  {t("net.note.confirm.problem.line")}
                </p>
              ) : null}
              {wrongUnit === line.lineNo ? (
                <p className="field__error" role="alert">
                  {t("net.note.confirm.problem.unit", { unit: unitLabel(line.unit) })}
                </p>
              ) : null}
              {chosen.mode === "new" ? (
                <div className="net-line">
                  <div className="field">
                    <label htmlFor={`${id}-price`}>{t("net.note.confirm.newPrice")}</label>
                    <input
                      id={`${id}-price`}
                      className="input input--amount"
                      inputMode="numeric"
                      autoComplete="off"
                      value={chosen.price}
                      disabled={pending}
                      aria-invalid={checked && !(price?.ok ?? false)}
                      aria-describedby={`${id}-price-error`}
                      onChange={(event) => set(line.lineNo, { ...chosen, price: event.target.value })}
                    />
                    <FieldError id={`${id}-price-error`} message={checked && !(price?.ok ?? false) ? t("net.price.invalid") : null} />
                  </div>
                  <div className="field">
                    <label htmlFor={`${id}-barcode`}>{t("net.note.confirm.newBarcode")}</label>
                    <input
                      id={`${id}-barcode`}
                      className="input"
                      inputMode="numeric"
                      autoComplete="off"
                      maxLength={64}
                      value={chosen.barcode}
                      disabled={pending}
                      onChange={(event) => set(line.lineNo, { ...chosen, barcode: event.target.value })}
                    />
                  </div>
                </div>
              ) : null}
              <p className="actions">
                {chosen.mode !== "none" ? (
                  <button type="button" className="button button--small" disabled={pending} onClick={() => set(line.lineNo, { mode: "none" })}>
                    {t("net.note.confirm.change")}
                  </button>
                ) : (
                  <button type="button" className="button button--small" disabled={pending} onClick={() => set(line.lineNo, { mode: "new", price: "", barcode: "" })}>
                    {t("net.note.confirm.asNew")}
                  </button>
                )}
              </p>
              {chosen.mode === "none" && can("stock.view") ? (
                <ItemFinder
                  id={`${id}-item`}
                  filter={ANY_ITEM}
                  disabled={pending}
                  onPick={(item) => {
                    // The same unit, or the stock would count kilograms as pieces.
                    if (item.unit !== line.unit) {
                      setWrongUnit(line.lineNo);
                      return;
                    }
                    set(line.lineNo, { mode: "item", itemId: item.id, name: item.name });
                  }}
                />
              ) : null}
            </li>
          );
        })}
      </ul>
      {asksMethod ? <MethodChoice id="net-confirm-method" value={method} disabled={pending} onChange={setMethod} /> : null}
      <p className="actions">
        <button type="button" className="button button--primary" onClick={send} disabled={pending}>
          {pending ? t("state.saving") : t("net.note.confirm.submit")}
        </button>
        <button type="button" className="button" onClick={onClose} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </div>
  );
}

/** The buyer says the note is not what arrived: why, and for the lines that differ, what did arrive. */
function RejectForm({ note, onDone, onClose }: { note: Note; onDone: () => void; onClose: () => void }) {
  const { t } = useI18n();
  const network = useNetwork();
  const unitLabel = useUnitLabel();
  const [received, setReceived] = useState<Record<number, string>>({});
  const [problem, setProblem] = useState(false);
  const save = useSubmit((job: { reason: string; lines: { lineNo: number; receivedQty: string }[] }, key) =>
    network.rejectNote(note.id, job, key).then(onDone),
  );
  const send = (reason: string) => {
    const lines: { lineNo: number; receivedQty: string }[] = [];
    for (const line of note.lines) {
      const typed = (received[line.lineNo] ?? "").trim();
      if (typed === "") {
        continue;
      }
      const qty = readCount(typed);
      if (!qty.ok) {
        setProblem(true);
        return;
      }
      lines.push({ lineNo: line.lineNo, receivedQty: qty.api });
    }
    save.submit({ reason, lines });
  };
  return (
    <ReasonBox
      id="net-reject-reason"
      label={t("net.note.reject.reason")}
      submitLabel={t("net.note.reject")}
      pending={save.state.status === "pending"}
      error={save.state.status === "error" ? save.state.error : null}
      onSubmit={send}
      onClose={onClose}
    >
      <p className="hint">{t("net.note.reject.hint")}</p>
      {note.lines.map((line) => (
        <div key={line.lineNo} className="field">
          <label htmlFor={`net-reject-${line.lineNo}`}>
            {t("net.note.reject.received", { name: line.name, qty: qtyWithUnit(line.qty, unitLabel(line.unit)) })}
          </label>
          <input
            id={`net-reject-${line.lineNo}`}
            className="input"
            inputMode="decimal"
            autoComplete="off"
            value={received[line.lineNo] ?? ""}
            disabled={save.state.status === "pending"}
            onChange={(event) => {
              setReceived((current) => ({ ...current, [line.lineNo]: event.target.value }));
              setProblem(false);
            }}
          />
        </div>
      ))}
      {problem ? (
        <p className="field__error" role="alert">
          {t("net.qty.invalid")}
        </p>
      ) : null}
    </ReasonBox>
  );
}

/**
 * The supplier issues a new note in place of this one: why, what was handed over on delivery, and the
 * lines as they should have been. The new note is always said in full. Left out, the server reads
 * `paid` as nothing paid and the lines as the ORDER's accepted ones, which are not this note's after an
 * earlier correction. A line of which nothing was delivered is typed as 0 and left out of the new note.
 */
function CorrectForm({ note, onDone, onClose }: { note: Note; onDone: (corrected: Note) => void; onClose: () => void }) {
  const { t, language } = useI18n();
  const network = useNetwork();
  const unitLabel = useUnitLabel();
  const initial = () => Object.fromEntries(note.lines.map((line) => [line.lineNo, { qty: line.qty.replace(".", ","), price: priceInput(line.unitPrice, note) }]));
  const [rows, setRows] = useState<Record<number, { qty: string; price: string }>>(initial);
  const [paid, setPaid] = useState(priceInput(note.paid, note));
  const [problem, setProblem] = useState(false);
  const save = useSubmit(
    (job: { reason: string; paid: number | null; lines: { lineNo: number; qty: string; unitPrice: number }[] | null }, key) =>
      network.correctNote(note.id, job, key).then(onDone),
  );
  const pending = save.state.status === "pending";
  const send = (reason: string) => {
    const paidRead = parseMoney(paid.trim() === "" ? "0" : paid, note.currency, { min: 0, max: 1_000_000_000_000 });
    const lines: { lineNo: number; qty: string; unitPrice: number }[] = [];
    for (const line of note.lines) {
      const row = rows[line.lineNo] ?? { qty: "", price: "" };
      const qty = readCount(row.qty);
      const price = readUnitCost(row.price, note.currency);
      if (!qty.ok || !price.ok) {
        setProblem(true);
        return;
      }
      if (qty.thousandths > 0) {
        lines.push({ lineNo: line.lineNo, qty: qty.api, unitPrice: price.amount });
      }
    }
    if (!paidRead.ok || lines.length === 0) {
      setProblem(true);
      return;
    }
    save.submit({ reason, paid: paidRead.amount, lines });
  };
  const change = (lineNo: number, patch: Partial<{ qty: string; price: string }>) => {
    setRows((current) => ({ ...current, [lineNo]: { ...(current[lineNo] ?? { qty: "", price: "" }), ...patch } }));
    setProblem(false);
  };
  return (
    <ReasonBox
      id="net-correct-reason"
      label={t("net.note.correct.reason")}
      submitLabel={t("net.note.correct.submit")}
      pending={pending}
      error={save.state.status === "error" ? save.state.error : null}
      onSubmit={send}
      onClose={onClose}
    >
      <p className="hint">{t("net.note.correct.hint")}</p>
      {note.lines.map((line) => {
        const id = `net-correct-${line.lineNo}`;
        const row = rows[line.lineNo] ?? { qty: "", price: "" };
        return (
          <div key={line.lineNo} className="net-line">
            <div className="field">
              <label htmlFor={`${id}-qty`}>{t("net.note.correct.qty", { name: line.name, unit: unitLabel(line.unit) })}</label>
              <input id={`${id}-qty`} className="input" inputMode="decimal" autoComplete="off" value={row.qty} disabled={pending} onChange={(event) => change(line.lineNo, { qty: event.target.value })} />
            </div>
            <div className="field">
              <label htmlFor={`${id}-price`}>{t("net.order.accept.price")}</label>
              <input
                id={`${id}-price`}
                className="input input--amount"
                inputMode={note.currency === "USD" ? "decimal" : "numeric"}
                autoComplete="off"
                value={row.price}
                disabled={pending}
                onChange={(event) => change(line.lineNo, { price: event.target.value })}
              />
            </div>
          </div>
        );
      })}
      <div className="field">
        <label htmlFor="net-correct-paid">{t("net.order.deliver.paid")}</label>
        <input
          id="net-correct-paid"
          className="input input--amount"
          inputMode={note.currency === "USD" ? "decimal" : "numeric"}
          autoComplete="off"
          value={paid}
          disabled={pending}
          onChange={(event) => {
            setPaid(event.target.value);
            setProblem(false);
          }}
        />
        <p className="field__hint">{t("net.note.total", { amount: formatMoney(note.total, language, note.currency) })}</p>
      </div>
      {problem ? (
        <p className="field__error" role="alert">
          {t("net.note.correct.problem")}
        </p>
      ) : null}
    </ReasonBox>
  );
}

/** An amount as a person would type it into a field of the note's currency: so'm whole, dollars with cents. */
function priceInput(amount: number, note: Pick<Note, "currency">): string {
  return note.currency === "USD" ? usdInput(amount) : String(amount);
}

type Mode = "view" | "confirm" | "reject" | "correct";

/**
 * One delivery note: who delivered what to whom and on what terms. It can be printed; it is a page of
 * the application only, and there is deliberately no link to it for anybody outside.
 */
export function NoteScreen({ noteId, office }: { noteId: string; office: boolean }) {
  const { t, language } = useI18n();
  const can = useMay();
  const network = useNetwork();
  const unitLabel = useUnitLabel();
  const [mode, setMode] = useState<Mode>("view");
  const [corrected, setCorrected] = useState<Note | null>(null);
  const { state, reload } = useLoad((signal) => network.note(noteId, signal), [network, noteId]);
  const done = () => {
    setMode("view");
    reload();
  };
  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return state.error.status === 404 ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  const note = state.data;
  const buyer = note.role === "buyer";
  // Confirming receives goods into the stock, and pays the supplier when something was handed over.
  const confirms = buyer && note.status === "issued" && can("network.confirm") && can("stock.receive") && (note.paid === 0 || can("suppliers.pay"));
  const rejects = buyer && note.status === "issued" && can("network.confirm");
  const corrects = !buyer && (note.status === "issued" || note.status === "rejected") && can("network.fulfil");
  const hasReceived = note.lines.some((line) => line.receivedQty !== null);

  const columns: Column<NoteLine>[] = [
    { id: "name", header: t("net.col.item"), rowHeader: true, cell: (line) => line.name },
    { id: "qty", header: t("net.col.qty"), numeric: true, cell: (line) => qtyWithUnit(line.qty, unitLabel(line.unit)) },
    ...(hasReceived
      ? ([
          {
            id: "received",
            header: t("net.col.received"),
            numeric: true,
            cell: (line) => (line.receivedQty === null ? null : qtyWithUnit(line.receivedQty, unitLabel(line.unit))),
          },
        ] satisfies Column<NoteLine>[])
      : []),
    { id: "price", header: t("net.col.price"), numeric: true, cell: (line) => money(line.unitPrice, note.currency, language) },
    { id: "total", header: t("net.col.total"), numeric: true, cell: (line) => money(line.lineTotal, note.currency, language) },
  ];

  return (
    <>
      <Tabs current={TAB.notes} />
      <article className="net-note">
        <h2 className="subject">{t("net.note.ref", { number: note.number })}</h2>
        <dl className="facts">
          <Fact name={t(buyer ? "net.note.from" : "net.note.to")}>{note.partnerName ?? t("net.partner.unnamed")}</Fact>
          <Fact name={t("net.col.order")}>
            <Link to={`/network/orders/${note.orderId}`}>{t("net.order.ref", { number: note.orderNumber })}</Link>
          </Fact>
          <Fact name={t("net.col.issued")}>{formatInstant(note.issuedAt, language)}</Fact>
          <Fact name={t("net.col.state")}>
            <Status status={note.status} label={NOTE_STATUS_LABELS[note.status]} />
          </Fact>
          {note.decidedAt !== null ? <Fact name={t("net.note.decidedAt")}>{formatInstant(note.decidedAt, language)}</Fact> : null}
          {note.rejectReason !== null ? <Fact name={t("net.note.rejectReason")}>{note.rejectReason}</Fact> : null}
          {note.supersedesId !== null ? (
            <Fact name={t("net.note.supersedes")}>
              <Link to={`/network/notes/${note.supersedesId}`}>{t("net.note.supersedes.open")}</Link>
              {note.supersedeReason !== null ? ` · ${note.supersedeReason}` : ""}
            </Fact>
          ) : null}
        </dl>
        <Listing caption={t("net.note.lines")} columns={columns} items={note.lines} rowKey={(line) => String(line.lineNo)} />
        <dl className="facts">
          <Fact name={t("net.col.total")}>
            <span className="money">{money(note.total, note.currency, language)}</span>
          </Fact>
          <Fact name={t("net.note.paid")}>
            <span className="money">{money(note.paid, note.currency, language)}</span>
          </Fact>
          <Fact name={t("net.note.credit")}>
            <span className="money">{money(note.total - note.paid, note.currency, language)}</span>
          </Fact>
          <Fact name={t("net.col.terms")}>{t(NOTE_TERMS_LABELS[note.terms])}</Fact>
        </dl>
        {note.status === "issued" ? <p className="hint">{t(buyer ? "net.note.awaits.own" : "net.note.awaits.partner")}</p> : null}
      </article>

      {corrected !== null ? (
        <p className="notice notice--done no-print" role="status">
          {t("net.note.corrected")} <Link to={`/network/notes/${corrected.id}`}>{t("net.note.ref", { number: corrected.number })}</Link>
        </p>
      ) : null}
      {note.status === "received" ? (
        <p className="notice notice--done no-print" role="status">
          {t(buyer ? "net.note.received.buyer" : "net.note.received.supplier")}{" "}
          {buyer && note.stockDocumentId !== undefined && office && can("stock.receive") ? (
            <Link to={`/stock-documents/${note.stockDocumentId}`}>{t("net.note.stockDocument")}</Link>
          ) : null}
          {!buyer && note.customerId !== undefined ? <Link to={`/customers/${note.customerId}`}>{t("net.note.customer")}</Link> : null}
        </p>
      ) : null}

      <div className="no-print">
        {mode === "view" ? (
          <p className="actions">
            {confirms ? (
              <button type="button" className="button button--primary" onClick={() => setMode("confirm")}>
                {t("net.note.confirm")}
              </button>
            ) : null}
            {rejects ? (
              <button type="button" className="button" onClick={() => setMode("reject")}>
                {t("net.note.reject")}
              </button>
            ) : null}
            {corrects ? (
              <button type="button" className="button" onClick={() => setMode("correct")}>
                {t("net.note.correct")}
              </button>
            ) : null}
            <button type="button" className="button" onClick={() => window.print()}>
              {t("net.note.print")}
            </button>
          </p>
        ) : null}
        {mode === "confirm" ? <ConfirmForm note={note} onDone={done} onClose={() => setMode("view")} /> : null}
        {mode === "reject" ? <RejectForm note={note} onDone={done} onClose={() => setMode("view")} /> : null}
        {mode === "correct" ? (
          <CorrectForm
            note={note}
            onDone={(next) => {
              setCorrected(next);
              done();
            }}
            onClose={() => setMode("view")}
          />
        ) : null}
        <History events={note.events} />
        <p className="actions">
          <Link to="/network/notes" className="button">
            {t("net.tab.notes")}
          </Link>
        </p>
      </div>
    </>
  );
}
