import { type ReactNode, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { type Loaded, useLoad } from "../hooks";
import { Link } from "../router";
import { useMay } from "../workspace/context";
import { errorText, Failure, formatInstant, Loading } from "../workspace/parts";
import { LinksList, WaitingFigures } from "./LinkScreens";
import type { NoteSummary, OrderSummary, Overview, Payment } from "./networkApi";
import { money, TAB, Tabs, useNetwork } from "./parts";
import { directionText, PaymentActions } from "./PaymentScreens";

/** One list of things that await this shop, under its name; nothing is drawn while it is empty. */
function Awaiting<T>({ title, state, children }: { title: string; state: Loaded<T[]>; children: (item: T) => ReactNode }) {
  const { t } = useI18n();
  // The figures above already say that something awaits; the list takes its place when it arrives.
  if (state.status === "loading") {
    return null;
  }
  if (state.status === "error") {
    return (
      <p className="notice notice--error" role="alert">
        {errorText(state.error, t)}
      </p>
    );
  }
  if (state.data.length === 0) {
    return null;
  }
  return (
    <>
      <h3 className="section-label">{title}</h3>
      <ul className="rows" aria-label={title}>
        {state.data.map(children)}
      </ul>
    </>
  );
}

/**
 * The section in the Telegram Mini App, kept to what the counter needs: what awaits this shop's step,
 * each with its answer or a way to it, a new order, and the partners. The heavy lists stay in the web
 * panel. A list is asked only when the overview says it holds something.
 */
export function MiniScreen() {
  const { t, language } = useI18n();
  const can = useMay();
  const network = useNetwork();
  const { state, reload } = useLoad((signal) => network.overview(signal), [network]);
  const [shown, setShown] = useState<Overview | null>(null);
  if (state.status === "ready" && state.data !== shown) {
    setShown(state.data);
  }
  const waiting = shown?.waiting;
  const orders = useLoad(
    async (signal): Promise<OrderSummary[]> => {
      const [sent, accepted] = await Promise.all([
        (waiting?.orders ?? 0) > 0 ? network.orders({ role: "supplier", status: "sent" }, signal).then((page) => page.items) : [],
        (waiting?.deliveries ?? 0) > 0 ? network.orders({ role: "supplier", status: "accepted" }, signal).then((page) => page.items) : [],
      ]);
      return [...sent, ...accepted];
    },
    [network, waiting?.orders, waiting?.deliveries, shown],
  );
  const notes = useLoad(
    async (signal): Promise<NoteSummary[]> => {
      const [issued, rejected] = await Promise.all([
        (waiting?.notes ?? 0) > 0 ? network.notes({ role: "buyer", status: "issued" }, signal).then((page) => page.items) : [],
        (waiting?.rejectedNotes ?? 0) > 0 ? network.notes({ role: "supplier", status: "rejected" }, signal).then((page) => page.items) : [],
      ]);
      return [...issued, ...rejected];
    },
    [network, waiting?.notes, waiting?.rejectedNotes, shown],
  );
  const payments = useLoad(
    async (signal): Promise<Payment[]> =>
      (waiting?.payments ?? 0) > 0
        ? network.payments({ status: "awaiting" }, signal).then((page) => page.items.filter((payment) => payment.recordedBy === "partner"))
        : [],
    [network, waiting?.payments, shown],
  );

  if (state.status === "error") {
    return <Failure error={state.error} onRetry={reload} />;
  }
  if (shown === null) {
    return <Loading />;
  }
  const buys = shown.links.some((link) => link.role === "buyer" && link.state === "active");
  return (
    <>
      <WaitingFigures waiting={shown.waiting} />
      <Awaiting title={t("net.mini.orders")} state={orders.state}>
        {(order) => (
          <li key={order.id} className="row">
            <p className="row__name">
              <Link to={`/network/orders/${order.id}`}>{t("net.order.ref", { number: order.number })}</Link>
            </p>
            <p className="row__meta">{order.partnerName ?? t("net.partner.unnamed")}</p>
            <p className="row__meta">{t(order.status === "sent" ? "net.mini.order.answer" : "net.mini.order.deliver")}</p>
          </li>
        )}
      </Awaiting>
      <Awaiting title={t("net.mini.notes")} state={notes.state}>
        {(note) => (
          <li key={note.id} className="row">
            <p className="row__name">
              <Link to={`/network/notes/${note.id}`}>{t("net.note.ref", { number: note.number })}</Link>
            </p>
            <p className="row__meta">
              {note.partnerName ?? t("net.partner.unnamed")}
              {" · "}
              <span className="money">{money(note.total, note.currency, language)}</span>
            </p>
            <p className="row__meta">{t(note.status === "issued" ? "net.mini.note.confirm" : "net.mini.note.correct")}</p>
          </li>
        )}
      </Awaiting>
      <Awaiting title={t("net.mini.payments")} state={payments.state}>
        {(payment) => (
          <li key={payment.id} className="row">
            <p className="row__name">
              <span className="money">{money(payment.amount, payment.currency, language)}</span>
            </p>
            <p className="row__meta">
              {payment.partnerName ?? t("net.partner.unnamed")}
              {" · "}
              {directionText(payment, t)}
              {" · "}
              {formatInstant(payment.recordedAt, language)}
            </p>
            {payment.note !== null ? <p className="row__note">{payment.note}</p> : null}
            <PaymentActions payment={payment} onChanged={reload} />
          </li>
        )}
      </Awaiting>

      {can("network.order") && buys ? (
        <p className="actions">
          <Link to="/network/orders/new" className="button button--primary">
            {t("net.order.new")}
          </Link>
        </p>
      ) : null}
      <h3 className="section-label">{t("net.links")}</h3>
      <LinksList links={shown.links} onChanged={reload} />
      <h3 className="section-label">{t("net.mini.all")}</h3>
      <Tabs current={TAB.home} />
    </>
  );
}
