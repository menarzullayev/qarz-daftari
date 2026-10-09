import { useState } from "react";

import { type Translate, useI18n } from "../../i18n/I18nProvider";
import { formatMoney } from "../format";
import { useLoad, usePagedList, useSubmit } from "../hooks";
import { CardIcon } from "../icons";
import type { Column } from "../layout";
import { type Currency, parseMoney } from "../money";
import { Listing, MethodChoice, useStockSettings } from "../stock/parts";
import { tidy } from "../stock/quantity";
import { useMay } from "../workspace/context";
import { Confirm, CurrencyToggle, Empty, errorText, Failure, FieldError, formatInstant, Loading, LoadMore } from "../workspace/parts";
import { type Method, type NetLink, type Payment, PAYMENT_STATUSES, type PaymentStatus } from "./networkApi";
import { MAX_NOTE, money, partnerText, PAYMENT_STATUS_LABELS, ReasonBox, roleText, Status, TAB, Tabs, useNetwork } from "./parts";

/** One payment, in the currency's minor unit (the range of a supplier's account entry). */
const PAYMENT_RANGE = { min: 1, max: 1_000_000_000_000 };

/** Whether the member may move the money a payment of this link is: a supplier's account, or a customer's. */
export function useMayPay(): (role: NetLink["role"]) => boolean {
  const can = useMay();
  return (role) => can("network.confirm") && can(role === "buyer" ? "suppliers.pay" : "payments.record");
}

export type PaymentSteps = { confirm: boolean; decline: boolean; withdraw: boolean };

/**
 * The steps an awaiting payment offers the member; each is the server's own check (`PaymentService`).
 * Every one asks for "network.confirm". Confirming one the partner recorded writes this shop's own
 * entry, so it also asks for that book's permission (`useMayPay`). Declining it writes nothing anywhere
 * and asks for nothing more. Taking back one this shop recorded CANCELS its own entry: a payment to a
 * supplier by "suppliers.pay", a customer's payment by "entries.cancel" (not "payments.record", which
 * only writes one); when that entry was already cancelled by hand, nothing more is asked.
 */
export function usePaymentSteps(): (payment: Pick<Payment, "role" | "recordedBy" | "inOwnBooks">) => PaymentSteps {
  const can = useMay();
  const mayPay = useMayPay();
  return (payment) => {
    const network = can("network.confirm");
    if (payment.recordedBy === "partner") {
      return { confirm: mayPay(payment.role), decline: network, withdraw: false };
    }
    const withdraw = network && (!payment.inOwnBooks || can(payment.role === "buyer" ? "suppliers.pay" : "entries.cancel"));
    return { confirm: false, decline: false, withdraw };
  };
}

/** Whether an awaiting payment offers the member any step at all. */
export function useMayStep(): (payment: Pick<Payment, "role" | "recordedBy" | "inOwnBooks">) => boolean {
  const steps = usePaymentSteps();
  return (payment) => {
    const offered = steps(payment);
    return offered.confirm || offered.decline || offered.withdraw;
  };
}

/** The shop keeps a cash book: money that moves is then asked how it was paid. */
function useCashBook(): boolean {
  const settings = useStockSettings();
  return settings.state.status === "ready" && settings.state.data.cashBook;
}

type NewPayment = { linkId: string; amount: number; currency: Currency; method: Method | null; note: string | null };

/**
 * Records that the buyer paid the supplier. Either side may: it is in this shop's books at once, and
 * takes effect on the other side only when that side confirms. The form says so before anything is sent.
 */
export function PaymentForm({ links, onDone, onClose }: { links: readonly NetLink[]; onDone: () => void; onClose: () => void }) {
  const { t, language } = useI18n();
  const network = useNetwork();
  const settings = useStockSettings();
  const cashBook = useCashBook();
  const [linkId, setLinkId] = useState(links[0]?.id ?? "");
  const [currency, setCurrency] = useState<Currency>("UZS");
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<Method>("cash");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState(false);
  const save = useSubmit((job: NewPayment, key) => network.recordPayment(job, key).then(onDone));
  const pending = save.state.status === "pending";
  const link = links.find((candidate) => candidate.id === linkId);
  const dollars = settings.state.status === "ready" && settings.state.data.currencies.includes("USD");
  const parsed = parseMoney(amount, currency, PAYMENT_RANGE);
  const send = () => {
    if (!parsed.ok || !link) {
      setProblem(true);
      return;
    }
    const cleanNote = tidy(note);
    save.submit({
      linkId: link.id,
      amount: parsed.amount,
      currency,
      method: cashBook ? method : null,
      note: cleanNote === null ? null : cleanNote.slice(0, MAX_NOTE),
    });
  };
  return (
    <div className="form notice" role="group" aria-label={t("net.payment.record")}>
      {save.state.status === "error" ? (
        <p className="field__error" role="alert">
          {errorText(save.state.error, t)}
        </p>
      ) : null}
      {links.length > 1 ? (
        <div className="field">
          <label htmlFor="net-payment-link">{t("net.col.partner")}</label>
          <select id="net-payment-link" className="input" value={linkId} disabled={pending} onChange={(event) => setLinkId(event.target.value)}>
            {links.map((option) => (
              <option key={option.id} value={option.id}>
                {`${partnerText(option.partner, t)} · ${roleText(option.role, t)}`}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      {link ? <p className="hint">{t(link.role === "buyer" ? "net.payment.hint.buyer" : "net.payment.hint.supplier", { name: partnerText(link.partner, t) })}</p> : null}
      {dollars ? (
        <CurrencyToggle
          value={currency}
          disabled={pending}
          onChange={(next) => {
            setCurrency(next);
            setAmount("");
            setProblem(false);
          }}
        />
      ) : null}
      <div className="field">
        <label htmlFor="net-payment-amount">{t("net.payment.amount")}</label>
        <input
          id="net-payment-amount"
          className="input input--amount"
          inputMode={currency === "USD" ? "decimal" : "numeric"}
          autoComplete="off"
          value={amount}
          disabled={pending}
          aria-invalid={problem}
          aria-describedby="net-payment-amount-error net-payment-amount-hint"
          onChange={(event) => {
            setAmount(event.target.value);
            setProblem(false);
          }}
        />
        <p className="field__hint" id="net-payment-amount-hint">
          {parsed.ok ? formatMoney(parsed.amount, language, currency) : ""}
        </p>
        <FieldError id="net-payment-amount-error" message={problem ? t("net.amount.invalid") : null} />
      </div>
      {cashBook ? <MethodChoice id="net-payment-method" value={method} disabled={pending} onChange={setMethod} /> : null}
      <div className="field">
        <label htmlFor="net-payment-note">{t("net.note.label")}</label>
        <input id="net-payment-note" className="input" value={note} maxLength={MAX_NOTE} autoComplete="off" disabled={pending} onChange={(event) => setNote(event.target.value)} />
      </div>
      <p className="hint">{t("net.payment.awaits")}</p>
      <p className="actions">
        <button type="button" className="button button--primary" onClick={send} disabled={pending}>
          {pending ? t("state.saving") : t("net.payment.submit")}
        </button>
        <button type="button" className="button" onClick={onClose} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </div>
  );
}

/** Who paid whom, from this shop's side: the buyer pays, the supplier is paid. */
export function directionText(payment: Pick<Payment, "role">, t: Translate): string {
  return t(payment.role === "buyer" ? "net.payment.direction.buyer" : "net.payment.direction.supplier");
}

type Step = "confirm" | "decline" | "withdraw";

/**
 * What may be done with one payment that awaits an answer: the side that did not record it confirms or
 * declines, the side that did may take it back. Each step is offered by what the server asks for it
 * (`usePaymentSteps`): declining by "network.confirm" alone, the other two also by the book's permission.
 */
export function PaymentActions({ payment, onChanged }: { payment: Payment; onChanged: () => void }) {
  const { t } = useI18n();
  const steps = usePaymentSteps();
  const network = useNetwork();
  const cashBook = useCashBook();
  const [step, setStep] = useState<Step | null>(null);
  const [method, setMethod] = useState<Method>("cash");
  const done = () => {
    setStep(null);
    onChanged();
  };
  const confirm = useSubmit((chosen: Method | null, key) => network.confirmPayment(payment.id, chosen, key).then(done));
  const decline = useSubmit((reason: string, key) => network.declinePayment(payment.id, reason, key).then(done));
  const withdraw = useSubmit((_: null, key) => network.withdrawPayment(payment.id, key).then(done));
  const offered = steps(payment);
  if (payment.status !== "awaiting" || !(offered.confirm || offered.decline || offered.withdraw)) {
    return null;
  }
  const id = `net-payment-${payment.id}`;
  if (step === "confirm" && offered.confirm) {
    const pending = confirm.state.status === "pending";
    return (
      <div className="form notice" role="group" aria-label={t("net.payment.confirm")}>
        {confirm.state.status === "error" ? (
          <p className="field__error" role="alert">
            {errorText(confirm.state.error, t)}
          </p>
        ) : null}
        <p>{t(payment.role === "buyer" ? "net.payment.confirm.hint.buyer" : "net.payment.confirm.hint.supplier")}</p>
        {cashBook ? <MethodChoice id={`${id}-method`} value={method} disabled={pending} onChange={setMethod} /> : null}
        <p className="actions">
          <button type="button" className="button button--primary" disabled={pending} onClick={() => confirm.submit(cashBook ? method : null)}>
            {pending ? t("state.saving") : t("net.payment.confirm")}
          </button>
          <button
            type="button"
            className="button"
            disabled={pending}
            onClick={() => {
              setStep(null);
              confirm.reset();
            }}
          >
            {t("action.cancel")}
          </button>
        </p>
      </div>
    );
  }
  if (step === "decline" && offered.decline) {
    return (
      <ReasonBox
        id={`${id}-reason`}
        label={t("net.payment.decline.reason")}
        submitLabel={t("net.payment.decline")}
        pending={decline.state.status === "pending"}
        error={decline.state.status === "error" ? decline.state.error : null}
        onSubmit={decline.submit}
        onClose={() => {
          setStep(null);
          decline.reset();
        }}
      />
    );
  }
  if (step === "withdraw" && offered.withdraw) {
    return (
      <Confirm
        question={t("net.payment.withdraw.question")}
        yes={t("net.payment.withdraw")}
        no={t("action.cancel")}
        pending={withdraw.state.status === "pending"}
        error={withdraw.state.status === "error" ? withdraw.state.error : null}
        onYes={() => withdraw.submit(null)}
        onNo={() => {
          setStep(null);
          withdraw.reset();
        }}
      />
    );
  }
  return (
    <p className="actions">
      {offered.confirm ? (
        <button type="button" className="button button--primary button--small" onClick={() => setStep("confirm")}>
          {t("net.payment.confirm")}
        </button>
      ) : null}
      {offered.decline ? (
        <button type="button" className="button button--small" onClick={() => setStep("decline")}>
          {t("net.payment.decline")}
        </button>
      ) : null}
      {offered.withdraw ? (
        <button type="button" className="button button--small" onClick={() => setStep("withdraw")}>
          {t("net.payment.withdraw")}
        </button>
      ) : null}
    </p>
  );
}

/** Where a payment stands, in a sentence: whose step it is, or what its ending left in this shop's books. */
export function paymentNote(payment: Payment, t: Translate): string | null {
  if (payment.status === "awaiting") {
    return t(payment.recordedBy === "own" ? "net.payment.awaiting.partner" : "net.payment.awaiting.own");
  }
  if (payment.status === "declined") {
    const said = payment.declineReason === null ? null : t("net.payment.declined.reason", { reason: payment.declineReason });
    // Declined by the partner and still standing here: the shop's own entry is its own to cancel.
    const stands =
      payment.recordedBy === "own" && payment.inOwnBooks
        ? t(payment.role === "buyer" ? "net.payment.declined.stands.buyer" : "net.payment.declined.stands.supplier")
        : null;
    const parts = [stands, said].filter((part) => part !== null);
    return parts.length === 0 ? null : parts.join(" ");
  }
  return null;
}

const ALL = "all";
type Filter = PaymentStatus | typeof ALL;

/** The payments between this shop and its partners, with what awaits an answer first in mind. */
export function PaymentsScreen() {
  const { t, language } = useI18n();
  const mayPay = useMayPay();
  const mayStep = useMayStep();
  const network = useNetwork();
  const [status, setStatus] = useState<Filter>(ALL);
  const [adding, setAdding] = useState(false);
  const links = useLoad((signal) => network.overview(signal).then((overview) => overview.links), [network]);
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) => network.payments({ status: status === ALL ? undefined : status, cursor }, signal),
    [network, status],
  );
  // A payment is recorded with a partner the shop is linked with now, by one who may move that money.
  const payable = links.state.status === "ready" ? links.state.data.filter((link) => link.state === "active" && mayPay(link.role)) : [];

  const columns: Column<Payment>[] = [
    { id: "when", header: t("net.col.when"), rowHeader: true, cell: (row) => formatInstant(row.recordedAt, language) },
    { id: "partner", header: t("net.col.partner"), cell: (row) => row.partnerName ?? t("net.partner.unnamed") },
    { id: "direction", header: t("net.col.direction"), cell: (row) => directionText(row, t) },
    { id: "amount", header: t("net.col.amount"), numeric: true, cell: (row) => <span className="money">{money(row.amount, row.currency, language)}</span> },
    { id: "by", header: t("net.col.recordedBy"), cell: (row) => t(row.recordedBy === "own" ? "net.by.own" : "net.by.partner") },
    { id: "status", header: t("net.col.state"), cell: (row) => <Status status={row.status} label={PAYMENT_STATUS_LABELS[row.status]} /> },
    { id: "books", header: t("net.col.books"), cell: (row) => t(row.inOwnBooks ? "net.payment.books.in" : "net.payment.books.out") },
    { id: "note", header: t("net.note.label"), cell: (row) => row.note },
    { id: "state", header: t("net.col.awaits"), cell: (row) => paymentNote(row, t) },
  ];

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    body = <Empty icon={<CardIcon />}>{t("net.payments.none")}</Empty>;
  } else {
    body = (
      <>
        <Listing
          caption={t("net.tab.payments")}
          columns={columns}
          items={state.items}
          rowKey={(row) => row.id}
          expanded={(row) => (row.status === "awaiting" && mayStep(row) ? <PaymentActions payment={row} onChanged={reload} /> : null)}
        />
        {state.nextCursor !== null ? <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} /> : null}
      </>
    );
  }

  return (
    <>
      <Tabs current={TAB.payments} />
      <div className="bar">
        <div className="field net-filter">
          <label htmlFor="net-payments-status">{t("net.col.state")}</label>
          <select
            id="net-payments-status"
            className="input"
            value={status}
            onChange={(event) => setStatus(PAYMENT_STATUSES.find((known) => known === event.target.value) ?? ALL)}
          >
            <option value={ALL}>{t("net.filter.all")}</option>
            {PAYMENT_STATUSES.map((option) => (
              <option key={option} value={option}>
                {t(PAYMENT_STATUS_LABELS[option])}
              </option>
            ))}
          </select>
        </div>
        {payable.length > 0 && !adding ? (
          <button type="button" className="button button--primary" onClick={() => setAdding(true)}>
            {t("net.payment.record")}
          </button>
        ) : null}
      </div>
      {adding && payable.length > 0 ? (
        <PaymentForm
          links={payable}
          onDone={() => {
            setAdding(false);
            reload();
          }}
          onClose={() => setAdding(false)}
        />
      ) : null}
      {body}
    </>
  );
}
