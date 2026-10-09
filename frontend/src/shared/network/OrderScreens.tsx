import { useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { formatMoney } from "../format";
import { useLoad, usePagedList, useSubmit } from "../hooks";
import { ListIcon } from "../icons";
import type { Column } from "../layout";
import { type Currency, parseMoney } from "../money";
import { Link, navigate } from "../router";
import { NotFoundScreen } from "../screens";
import { ItemFinder } from "../stock/ItemFinder";
import { Fact, Listing, qtyWithUnit, useStockSettings } from "../stock/parts";
import { lineCost, readCount, readUnitCost } from "../stock/quantity";
import type { ItemFilter } from "../stock/stockApi";
import { useMay } from "../workspace/context";
import { Badge, CurrencyToggle, Empty, errorText, Failure, FieldError, formatInstant, Loading, LoadMore } from "../workspace/parts";
import { type AcceptLine, type Draft, type LinkRole, type Order, ORDER_STATUSES, type OrderLine, type OrderStatus, type OrderSummary } from "./networkApi";
import { History, money, NONE, NOTE_STATUS_LABELS, ORDER_STATUS_LABELS, ReasonBox, roleText, Status, TAB, Tabs, useNetwork, useUnitLabel } from "./parts";

const ALL = "all";
/** A line may name any item of the supplier's catalogue, counted in the stock or not. */
const ANY_ITEM: ItemFilter = "all";
type Filter = OrderStatus | typeof ALL;

/** The drafts the buyer is still writing: nothing of them has reached a supplier. */
function Drafts({ drafts }: { drafts: readonly Draft[] }) {
  const { t, language } = useI18n();
  if (drafts.length === 0) {
    return null;
  }
  return (
    <>
      <h3 className="section-label">{t("net.drafts")}</h3>
      <ul className="rows" aria-label={t("net.drafts")}>
        {drafts.map((draft) => (
          <li key={draft.id} className="row">
            <p className="row__name">
              <Link to={`/network/drafts/${draft.id}`}>{draft.partnerName ?? t("net.partner.unnamed")}</Link>
            </p>
            <p className="row__meta">{t("net.draft.meta", { count: draft.lines.length, when: formatInstant(draft.updatedAt, language) })}</p>
          </li>
        ))}
      </ul>
    </>
  );
}

/** The orders this shop sent (with its drafts), or the ones it was sent. */
export function OrdersScreen({ role }: { role: LinkRole }) {
  const { t, language } = useI18n();
  const can = useMay();
  const network = useNetwork();
  const [status, setStatus] = useState<Filter>(ALL);
  const outgoing = role === "buyer";
  const drafts = useLoad((signal) => (outgoing ? network.drafts(signal) : Promise.resolve([])), [network, outgoing]);
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) => network.orders({ role, status: status === ALL ? undefined : status, cursor }, signal),
    [network, role, status],
  );

  const columns: Column<OrderSummary>[] = [
    {
      id: "order",
      header: t("net.col.order"),
      rowHeader: true,
      cell: (row) => <Link to={`/network/orders/${row.id}`}>{t("net.order.ref", { number: row.number })}</Link>,
    },
    { id: "partner", header: t("net.col.partner"), cell: (row) => row.partnerName ?? t("net.partner.unnamed") },
    { id: "status", header: t("net.col.state"), cell: (row) => <Status status={row.status} label={ORDER_STATUS_LABELS[row.status]} /> },
    { id: "wanted", header: t("net.order.wanted"), cell: (row) => row.wantedDate },
    {
      id: "total",
      header: t("net.col.total"),
      numeric: true,
      cell: (row) => (row.total === null ? null : <span className="money">{money(row.total, row.currency, language)}</span>),
    },
    { id: "sent", header: t("net.col.sent"), cell: (row) => formatInstant(row.sentAt, language) },
  ];

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    body = <Empty icon={<ListIcon />}>{t(outgoing ? "net.orders.out.none" : "net.orders.in.none")}</Empty>;
  } else {
    body = (
      <>
        <Listing caption={t(outgoing ? "net.tab.out" : "net.tab.in")} columns={columns} items={state.items} rowKey={(row) => row.id} />
        {state.nextCursor !== null ? <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} /> : null}
      </>
    );
  }

  return (
    <>
      <Tabs current={outgoing ? TAB.out : TAB.in} />
      <div className="bar">
        <div className="field net-filter">
          <label htmlFor="net-orders-status">{t("net.col.state")}</label>
          <select
            id="net-orders-status"
            className="input"
            value={status}
            onChange={(event) => setStatus(ORDER_STATUSES.find((known) => known === event.target.value) ?? ALL)}
          >
            <option value={ALL}>{t("net.filter.all")}</option>
            {ORDER_STATUSES.map((option) => (
              <option key={option} value={option}>
                {t(ORDER_STATUS_LABELS[option])}
              </option>
            ))}
          </select>
        </div>
        {outgoing && can("network.order") ? (
          <Link to="/network/orders/new" className="button button--primary">
            {t("net.order.new")}
          </Link>
        ) : null}
      </div>
      {outgoing && drafts.state.status === "ready" ? <Drafts drafts={drafts.state.data} /> : null}
      {outgoing && drafts.state.status === "error" ? <Failure error={drafts.state.error} onRetry={drafts.reload} /> : null}
      {outgoing ? <h3 className="section-label">{t("net.orders.sent")}</h3> : null}
      {body}
    </>
  );
}

type AnswerRow = { qty: string; price: string; itemId: string | null; itemName: string | null };

/**
 * The supplier's answer to an order: for EVERY line the quantity it will deliver (zero is an answer)
 * and the price of one unit, in one currency for the whole order. A line may name the supplier's own
 * catalogue item; its stock is then drawn when the buyer confirms the delivery.
 */
function AcceptForm({ order, onDone, onClose }: { order: Order; onDone: () => void; onClose: () => void }) {
  const { t, language } = useI18n();
  const can = useMay();
  const network = useNetwork();
  const settings = useStockSettings();
  const unitLabel = useUnitLabel();
  const [currency, setCurrency] = useState<Currency>("UZS");
  const [rows, setRows] = useState<Record<number, AnswerRow>>(() =>
    Object.fromEntries(order.lines.map((line) => [line.lineNo, { qty: line.qty.replace(".", ","), price: "", itemId: null, itemName: null }])),
  );
  const [checked, setChecked] = useState(false);
  const [finding, setFinding] = useState<number | null>(null);
  const save = useSubmit((job: { currency: Currency | null; lines: AcceptLine[] }, key) => network.acceptOrder(order.id, job, key).then(onDone));
  const pending = save.state.status === "pending";
  const dollars = settings.state.status === "ready" && settings.state.data.currencies.includes("USD");
  const row = (line: OrderLine): AnswerRow => rows[line.lineNo] ?? { qty: "", price: "", itemId: null, itemName: null };
  const change = (lineNo: number, patch: Partial<AnswerRow>) => {
    setRows((current) => ({ ...current, [lineNo]: { ...(current[lineNo] ?? { qty: "", price: "", itemId: null, itemName: null }), ...patch } }));
    setChecked(false);
  };
  const read = order.lines.map((line) => {
    const answer = row(line);
    return { line, answer, qty: readCount(answer.qty), price: readUnitCost(answer.price, currency) };
  });
  let total: number | null = 0;
  for (const entry of read) {
    const cost = entry.qty.ok && entry.price.ok ? lineCost(entry.qty.thousandths, entry.price.amount) : null;
    total = total === null || cost === null ? null : total + cost;
  }
  const send = () => {
    const lines: AcceptLine[] = [];
    for (const entry of read) {
      if (!entry.qty.ok || !entry.price.ok) {
        setChecked(true);
        return;
      }
      lines.push({ lineNo: entry.line.lineNo, qty: entry.qty.api, unitPrice: entry.price.amount, itemId: entry.answer.itemId });
    }
    save.submit({ currency: dollars ? currency : null, lines });
  };
  return (
    <div className="form notice" role="group" aria-label={t("net.order.accept")}>
      {save.state.status === "error" ? (
        <p className="field__error" role="alert">
          {errorText(save.state.error, t)}
        </p>
      ) : null}
      <p className="hint">{t("net.order.accept.hint")}</p>
      {dollars ? (
        <CurrencyToggle
          value={currency}
          disabled={pending}
          onChange={(next) => {
            setCurrency(next);
            setRows((current) => Object.fromEntries(Object.entries(current).map(([key, value]) => [key, { ...value, price: "" }])));
          }}
        />
      ) : null}
      <ul className="rows" aria-label={t("net.order.lines")}>
        {read.map(({ line, answer, qty, price }) => {
          const id = `net-accept-${line.lineNo}`;
          return (
            <li key={line.lineNo} className="row">
              <p className="row__name">{line.name}</p>
              <p className="row__meta">{t("net.order.asked", { qty: qtyWithUnit(line.qty, unitLabel(line.unit)) })}</p>
              <div className="net-line">
                <div className="field">
                  <label htmlFor={`${id}-qty`}>{t("net.order.accept.qty", { unit: unitLabel(line.unit) })}</label>
                  <input
                    id={`${id}-qty`}
                    className="input"
                    inputMode="decimal"
                    autoComplete="off"
                    value={answer.qty}
                    disabled={pending}
                    aria-invalid={checked && !qty.ok}
                    aria-describedby={`${id}-qty-error`}
                    onChange={(event) => change(line.lineNo, { qty: event.target.value })}
                  />
                  <FieldError id={`${id}-qty-error`} message={checked && !qty.ok ? t("net.qty.invalid") : null} />
                </div>
                <div className="field">
                  <label htmlFor={`${id}-price`}>{t("net.order.accept.price")}</label>
                  <input
                    id={`${id}-price`}
                    className="input input--amount"
                    inputMode={currency === "USD" ? "decimal" : "numeric"}
                    autoComplete="off"
                    value={answer.price}
                    disabled={pending}
                    aria-invalid={checked && !price.ok}
                    aria-describedby={`${id}-price-error`}
                    onChange={(event) => change(line.lineNo, { price: event.target.value })}
                  />
                  <FieldError id={`${id}-price-error`} message={checked && !price.ok ? t("net.price.invalid") : null} />
                </div>
              </div>
              {answer.itemId !== null ? (
                <p className="row__meta">
                  {t("net.line.ownItem", { name: answer.itemName ?? NONE })}{" "}
                  <button type="button" className="button button--small" disabled={pending} onClick={() => change(line.lineNo, { itemId: null, itemName: null })}>
                    {t("net.line.ownItem.clear")}
                  </button>
                </p>
              ) : can("stock.view") && finding !== line.lineNo ? (
                <button type="button" className="button button--small" disabled={pending} onClick={() => setFinding(line.lineNo)}>
                  {t("net.line.ownItem.choose")}
                </button>
              ) : null}
              {finding === line.lineNo && answer.itemId === null ? (
                <ItemFinder
                  id={`${id}-item`}
                  filter={ANY_ITEM}
                  disabled={pending}
                  onPick={(item) => {
                    change(line.lineNo, { itemId: item.id, itemName: item.name });
                    setFinding(null);
                  }}
                />
              ) : null}
            </li>
          );
        })}
      </ul>
      <p className="row__meta">{t("net.order.accept.total", { amount: total === null ? NONE : formatMoney(total, language, currency) })}</p>
      <p className="actions">
        <button type="button" className="button button--primary" onClick={send} disabled={pending}>
          {pending ? t("state.saving") : t("net.order.accept.submit")}
        </button>
        <button type="button" className="button" onClick={onClose} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </div>
  );
}

/**
 * Issuing the delivery note from the accepted lines. What the buyer handed over on delivery is said
 * here; the rest of the total is on credit, and nothing reaches either shop's books until the buyer
 * confirms what it received.
 */
function DeliverForm({ order, onClose }: { order: Order; onClose: () => void }) {
  const { t, language } = useI18n();
  const network = useNetwork();
  const currency = order.currency ?? "UZS";
  const [paid, setPaid] = useState("");
  const [problem, setProblem] = useState(false);
  const save = useSubmit((amount: number | null, key) => network.deliver(order.id, amount, key).then((note) => navigate(`/network/notes/${note.id}`)));
  const pending = save.state.status === "pending";
  const total = order.total ?? 0;
  const parsed = paid.trim() === "" ? ({ ok: true, amount: 0 } as const) : parseMoney(paid, currency, { min: 0, max: Math.max(total, 0) });
  const send = () => {
    if (!parsed.ok) {
      setProblem(true);
      return;
    }
    save.submit(parsed.amount === 0 ? null : parsed.amount);
  };
  return (
    <div className="form notice" role="group" aria-label={t("net.order.deliver")}>
      {save.state.status === "error" ? (
        <p className="field__error" role="alert">
          {errorText(save.state.error, t)}
        </p>
      ) : null}
      <p className="hint">{t("net.order.deliver.hint", { total: formatMoney(total, language, currency) })}</p>
      <div className="field">
        <label htmlFor="net-deliver-paid">{t("net.order.deliver.paid")}</label>
        <input
          id="net-deliver-paid"
          className="input input--amount"
          inputMode={currency === "USD" ? "decimal" : "numeric"}
          autoComplete="off"
          value={paid}
          disabled={pending}
          aria-invalid={problem}
          aria-describedby="net-deliver-paid-error net-deliver-paid-hint"
          onChange={(event) => {
            setPaid(event.target.value);
            setProblem(false);
          }}
        />
        <p className="field__hint" id="net-deliver-paid-hint">
          {parsed.ok ? t("net.order.deliver.credit", { amount: formatMoney(total - parsed.amount, language, currency) }) : ""}
        </p>
        <FieldError id="net-deliver-paid-error" message={problem ? t("net.order.deliver.paid.invalid") : null} />
      </div>
      <p className="actions">
        <button type="button" className="button button--primary" onClick={send} disabled={pending}>
          {pending ? t("state.saving") : t("net.order.deliver.submit")}
        </button>
        <button type="button" className="button" onClick={onClose} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </div>
  );
}

type Mode = "view" | "accept" | "decline" | "cancel" | "deliver";

/** One order: what was asked, what the supplier answered, what may be done now, and its history. */
export function OrderScreen({ orderId }: { orderId: string }) {
  const { t, language } = useI18n();
  const can = useMay();
  const network = useNetwork();
  const unitLabel = useUnitLabel();
  const [mode, setMode] = useState<Mode>("view");
  const { state, reload } = useLoad((signal) => network.order(orderId, signal), [network, orderId]);
  const done = () => {
    setMode("view");
    reload();
  };
  const decline = useSubmit((reason: string, key) => network.declineOrder(orderId, reason, key).then(done));
  const cancel = useSubmit((reason: string, key) => network.cancelOrder(orderId, reason, key).then(done));

  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return state.error.status === 404 ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  const order = state.data;
  const supplier = order.role === "supplier";
  const answers = supplier && order.status === "sent" && can("network.fulfil");
  // Issuing the note writes a sale on credit when the buyer confirms: it needs that permission too.
  const delivers = supplier && order.status === "accepted" && can("network.fulfil") && can("credits.record");
  const cancels = !supplier && (order.status === "sent" || order.status === "accepted") && can("network.order");
  const answered = order.lines.some((line) => line.acceptedQty !== null);

  const columns: Column<OrderLine>[] = [
    { id: "name", header: t("net.col.item"), rowHeader: true, cell: (line) => line.name },
    { id: "asked", header: t("net.col.asked"), numeric: true, cell: (line) => qtyWithUnit(line.qty, unitLabel(line.unit)) },
    ...(answered
      ? ([
          {
            id: "accepted",
            header: t("net.col.accepted"),
            numeric: true,
            cell: (line) =>
              line.acceptedQty === null ? null : (
                <>
                  {qtyWithUnit(line.acceptedQty, unitLabel(line.unit))}
                  {line.changed ? (
                    <>
                      {" "}
                      <Badge tone="warning">{t("net.order.changed")}</Badge>
                    </>
                  ) : null}
                </>
              ),
          },
          {
            id: "price",
            header: t("net.col.price"),
            numeric: true,
            cell: (line) => (line.unitPrice === null ? null : money(line.unitPrice, order.currency, language)),
          },
          {
            id: "total",
            header: t("net.col.total"),
            numeric: true,
            cell: (line) => (line.lineTotal === null ? null : money(line.lineTotal, order.currency, language)),
          },
        ] satisfies Column<OrderLine>[])
      : []),
  ];

  return (
    <>
      <Tabs current={supplier ? TAB.in : TAB.out} />
      <h2 className="subject">{t("net.order.ref", { number: order.number })}</h2>
      <dl className="facts">
        <Fact name={t("net.col.partner")}>
          <Link to={`/network/links/${order.linkId}`}>{order.partnerName ?? t("net.partner.unnamed")}</Link>
        </Fact>
        <Fact name={t("net.col.role")}>{roleText(order.role, t)}</Fact>
        <Fact name={t("net.col.state")}>
          <Status status={order.status} label={ORDER_STATUS_LABELS[order.status]} />
        </Fact>
        <Fact name={t("net.col.sent")}>{formatInstant(order.sentAt, language)}</Fact>
        {order.wantedDate !== null ? <Fact name={t("net.order.wanted")}>{order.wantedDate}</Fact> : null}
        {order.note !== null ? <Fact name={t("net.note.label")}>{order.note}</Fact> : null}
        {order.total !== null ? (
          <Fact name={t("net.col.total")}>
            <span className="money">{money(order.total, order.currency, language)}</span>
          </Fact>
        ) : null}
        {order.closedReason !== null ? <Fact name={t("net.reason")}>{order.closedReason}</Fact> : null}
        {order.deliveryNote !== null ? (
          <Fact name={t("net.order.note")}>
            <Link to={`/network/notes/${order.deliveryNote.id}`}>{t("net.note.ref", { number: order.deliveryNote.number })}</Link>
            {" · "}
            {t(NOTE_STATUS_LABELS[order.deliveryNote.status])}
          </Fact>
        ) : null}
      </dl>
      {order.lines.some((line) => line.changed) ? (
        <p className="notice notice--warning" role="status">
          {t(supplier ? "net.order.changed.supplier" : "net.order.changed.buyer")}
        </p>
      ) : null}

      <h3 className="section-label">{t("net.order.lines")}</h3>
      {mode === "accept" ? (
        <AcceptForm order={order} onDone={done} onClose={() => setMode("view")} />
      ) : (
        <Listing caption={t("net.order.lines")} columns={columns} items={order.lines} rowKey={(line) => String(line.lineNo)} />
      )}

      {mode === "view" && (answers || delivers || cancels) ? (
        <p className="actions">
          {answers ? (
            <>
              <button type="button" className="button button--primary" onClick={() => setMode("accept")}>
                {t("net.order.accept")}
              </button>
              <button type="button" className="button" onClick={() => setMode("decline")}>
                {t("net.order.decline")}
              </button>
            </>
          ) : null}
          {delivers ? (
            <button type="button" className="button button--primary" onClick={() => setMode("deliver")}>
              {t("net.order.deliver")}
            </button>
          ) : null}
          {cancels ? (
            <button type="button" className="button" onClick={() => setMode("cancel")}>
              {t("net.order.cancel")}
            </button>
          ) : null}
        </p>
      ) : null}
      {mode === "decline" || mode === "cancel" ? (
        <ReasonBox
          id="net-order-reason"
          label={t(mode === "decline" ? "net.order.decline.reason" : "net.order.cancel.reason")}
          submitLabel={t(mode === "decline" ? "net.order.decline" : "net.order.cancel")}
          pending={(mode === "decline" ? decline : cancel).state.status === "pending"}
          error={((job) => (job.state.status === "error" ? job.state.error : null))(mode === "decline" ? decline : cancel)}
          onSubmit={(mode === "decline" ? decline : cancel).submit}
          onClose={() => {
            setMode("view");
            decline.reset();
            cancel.reset();
          }}
        />
      ) : null}
      {mode === "deliver" ? <DeliverForm order={order} onClose={() => setMode("view")} /> : null}

      <History events={order.events} />
      <p className="actions">
        <Link to={supplier ? "/network/orders/in" : "/network/orders/out"} className="button">
          {t(supplier ? "net.tab.in" : "net.tab.out")}
        </Link>
      </p>
    </>
  );
}
