import { type FormEvent, useState } from "react";

import { withMessages } from "../../i18n/catalog";
import { useI18n } from "../../i18n/I18nProvider";
import type { ApiError, ChangedPromise, Customer, CustomerDetail, CustomerPatch, Entry } from "../api";
import { changedDate, changeRange, DATE_REASON_MAX, isDebtKind, saleDay } from "../dateRules";
import { formatCalendarDay, formatDateTime, formatMoney } from "../format";
import { useLoad, useSubmit } from "../hooks";
import { onDemand } from "../onDemand";
import { may } from "../permissions";
import { currencyOf } from "../money";
import { parseIsoDate } from "../promise";
import { DateReasonForm, dayText, PromiseHistory } from "../promiseParts";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { canAddGoods, mayAddGoods } from "./AddGoodsScreen";
import { useMay, useWorkspace } from "./context";
import { CreditLimitSection } from "./CreditLimitSection";
import { owesAnything } from "./CustomersScreen";
import { GoodsList } from "./GoodsEditor";
import { LinkSection } from "./LinkSection";
import { cleanName, customerFieldErrors, nameProblem } from "./NewCustomerScreen";
import { BalanceLine, ENTRY_KIND_LABELS, errorText, Failure, FieldError, Loading, OverdueLines } from "./parts";
import { PaymentHistoryNote } from "./PaymentHistoryNote";
import { ReminderAction } from "./ReminderAction";

// A customer's read-only link, for whoever holds `customers.share`: behind a platform switch, so its code is
// fetched apart and shows nothing until the server has said the switch is on.
const ShareSection = onDemand(() => withMessages(import("../share/ShareSection")));

/**
 * Only a manager or an owner is offered a reversal, and only for an entry the server would accept: not
 * a reversal itself and not one that is already reversed.
 */
export function canReverse(entry: Entry, mayManage: boolean): boolean {
  return mayManage && entry.kind !== "reversal" && !entry.reversed;
}

/**
 * Only a manager or an owner is offered a change of the promised date (REQ-067), and only for a debt
 * that still stands: a credit sale or an opening balance that is not reversed.
 */
export function canChangePromise(entry: Entry, mayManage: boolean): boolean {
  return mayManage && isDebtKind(entry.kind) && !entry.reversed && saleDay(entry.createdAt) !== null;
}

/** What a change of date did, as the server answered it, and the request it left waiting, if any. */
export type PromiseOutcome = {
  changed: ChangedPromise;
  /** The date the customer's request asked for, if one was open; it is still open unless `changed` closed it. */
  asked: string | null;
};

const CHANGE_REFUSALS: Readonly<Record<string, "dates.refused.notADebt" | "dates.refused.reversed">> = {
  not_a_debt: "dates.refused.notADebt",
  reversed: "dates.refused.reversed",
};

function PromiseChange({
  entry,
  onChanged,
  onCancel,
}: {
  entry: Entry;
  onChanged: (outcome: PromiseOutcome) => void;
  onCancel: () => void;
}) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const sale = saleDay(entry.createdAt);
  const current = entry.promisedDate === null ? null : parseIsoDate(entry.promisedDate);
  const { state, submit } = useSubmit((payload: { date: string; reason: string | null }, key) =>
    api.changePromise(entry.id, payload.date, payload.reason, key).then((changed) => {
      const open = entry.dateRequest?.status === "open" ? entry.dateRequest : null;
      onChanged({ changed, asked: open ? open.requestedDate : null });
    }),
  );
  if (sale === null) {
    return null;
  }
  const failure: ApiError | null = state.status === "error" ? state.error : null;
  const refusal = failure?.code === "PROMISE_NOT_CHANGEABLE" ? CHANGE_REFUSALS[failure.fields["reason"] ?? ""] : undefined;
  return (
    <DateReasonForm
      id={`promise-${entry.id}`}
      label={t("dates.change.label")}
      range={changeRange(sale)}
      hint={t("dates.change.hint", { max: DATE_REASON_MAX })}
      submitLabel={t("dates.change.submit")}
      pending={state.status === "pending"}
      error={failure}
      errorDetail={refusal ? t(refusal) : null}
      name="promised_date"
      choose={(text) => changedDate(text, sale, current)}
      onSubmit={(date, reason) => submit({ date, reason })}
      onCancel={onCancel}
    />
  );
}

function EditForm({ customer, onSaved, onCancel }: { customer: Customer; onSaved: () => void; onCancel: () => void }) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const [name, setName] = useState(customer.displayName);
  const [phone, setPhone] = useState(customer.phone ?? "");
  const [remindersOff, setRemindersOff] = useState(customer.remindersOff);
  const [nameError, setNameError] = useState<string | null>(null);
  const { state, submit } = useSubmit((patch: CustomerPatch, key) => api.updateCustomer(customer.id, patch, key).then(onSaved));

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const displayName = cleanName(name);
    const problem = nameProblem(displayName, t);
    setNameError(problem);
    if (problem !== null) {
      return;
    }
    // Send only what changed; the server refuses an empty change.
    const patch: CustomerPatch = {};
    if (displayName !== customer.displayName) {
      patch.displayName = displayName;
    }
    if (phone.trim() !== (customer.phone ?? "")) {
      patch.phone = phone.trim() === "" ? null : phone.trim();
    }
    if (remindersOff !== customer.remindersOff) {
      patch.remindersOff = remindersOff;
    }
    if (Object.keys(patch).length === 0) {
      onCancel();
      return;
    }
    submit(patch);
  };

  const failure = state.status === "error" ? state.error : null;
  const refused = customerFieldErrors(failure, t);
  const shownNameError = nameError ?? refused.name;
  const pending = state.status === "pending";

  return (
    <form className="form" onSubmit={onSubmit} noValidate aria-label={t("customer.edit")}>
      {failure ? (
        <p className="notice notice--error" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="edit-name">{t("customer.name")}</label>
        <input
          id="edit-name"
          className="input"
          value={name}
          autoComplete="off"
          aria-invalid={shownNameError !== null}
          aria-describedby="edit-name-error"
          onChange={(event) => {
            setName(event.target.value);
            setNameError(null);
          }}
        />
        <FieldError id="edit-name-error" message={shownNameError} />
      </div>
      <div className="field">
        <label htmlFor="edit-phone">{t("customer.phone.optional")}</label>
        <input
          id="edit-phone"
          className="input"
          type="tel"
          inputMode="tel"
          value={phone}
          maxLength={40}
          autoComplete="off"
          aria-invalid={refused.phone !== null}
          aria-describedby="edit-phone-error"
          onChange={(event) => setPhone(event.target.value)}
        />
        <FieldError id="edit-phone-error" message={refused.phone} />
      </div>
      <label className="choice">
        <input type="checkbox" checked={remindersOff} onChange={(event) => setRemindersOff(event.target.checked)} />
        <span>{t("customer.remindersOff.label")}</span>
      </label>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("action.save")}
        </button>
        <button type="button" className="button" onClick={onCancel} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </form>
  );
}

function EntryRow({
  entry,
  goodsPath,
  mayReverse,
  mayChangePromise,
  mayDecideDisputes,
  confirming,
  pending,
  onAsk,
  onConfirm,
  onCancel,
  changing,
  onAskChange,
  onChanged,
  onCancelChange,
}: {
  /** Whether the form that changes this entry's promised date is open. */
  changing: boolean;
  onAskChange: () => void;
  onChanged: (outcome: PromiseOutcome) => void;
  onCancelChange: () => void;
  entry: Entry;
  /** Where goods can be added to this entry, or null when that is not offered. */
  goodsPath: string | null;
  /** What the signed-in member may do with an entry; the server checks each of them again. */
  mayReverse: boolean;
  mayChangePromise: boolean;
  mayDecideDisputes: boolean;
  confirming: boolean;
  pending: boolean;
  onAsk: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t, language } = useI18n();
  const promised = entry.promisedDate === null ? null : parseIsoDate(entry.promisedDate);
  const made = new Date(entry.createdAt);
  // An entry in dollars says so with its amount: "12.50 $", never a bare number beside the so'm ones.
  const amount = formatMoney(entry.amount, language, currencyOf(entry));
  return (
    <li className={entry.reversed ? "row row--struck" : "row"}>
      <p className="row__link">
        <span className="row__name">{t(ENTRY_KIND_LABELS[entry.kind] ?? "entry.kind.other")}</span>
        <span className="row__amount">{amount}</span>
      </p>
      <p className="row__meta">{Number.isNaN(made.getTime()) ? entry.createdAt : formatDateTime(made, language)}</p>
      {entry.note ? <p className="row__note">{entry.note}</p> : null}
      {entry.lines.length > 0 ? <GoodsList lines={entry.lines} /> : null}
      {promised && !entry.reversed ? (
        <p className="row__meta">{t("entry.promised", { date: formatCalendarDay(promised, language) })}</p>
      ) : null}
      {entry.reversed ? <p className="row__meta">{t("entry.reversed")}</p> : null}
      <PromiseHistory promises={entry.promises} />
      {entry.dateRequest?.status === "open" ? (
        <div className="notice">
          <p className="row__warning">
            <span>{t("dates.request.open", { date: dayText(entry.dateRequest.requestedDate, language) })}</span>
            {/* Only a manager or an owner has the list where a request is answered. */}
            {mayChangePromise ? <Link to="/date-requests">{t("dates.title")}</Link> : null}
          </p>
          {entry.dateRequest.reason ? <p>{t("dates.row.reason", { reason: entry.dateRequest.reason })}</p> : null}
        </div>
      ) : null}
      {canChangePromise(entry, mayChangePromise) ? (
        changing ? (
          <PromiseChange entry={entry} onChanged={onChanged} onCancel={onCancelChange} />
        ) : (
          <button type="button" className="button button--small" onClick={onAskChange} disabled={pending}>
            {t("dates.change")}
          </button>
        )
      ) : null}
      {entry.disputed ? (
        <p className="row__warning">
          <span>{t("entry.disputed")}</span>
          {/* Only a manager or an owner has the list where a dispute is answered. */}
          {mayDecideDisputes ? <Link to="/disputes">{t("disputes.open")}</Link> : null}
        </p>
      ) : null}
      {goodsPath !== null ? (
        <Link to={goodsPath} className="button button--small">
          {t("goods.later.action")}
        </Link>
      ) : null}
      {canReverse(entry, mayReverse) ? (
        confirming ? (
          <div className="notice">
            <p>{t("reversal.confirm", { amount })}</p>
            <p className="actions">
              <button type="button" className="button button--primary" onClick={onConfirm} disabled={pending}>
                {pending ? t("state.saving") : t("reversal.confirm.yes")}
              </button>
              <button type="button" className="button" onClick={onCancel} disabled={pending}>
                {t("reversal.confirm.no")}
              </button>
            </p>
          </div>
        ) : (
          <button type="button" className="button button--small" onClick={onAsk} disabled={pending}>
            {t("reversal.action")}
          </button>
        )
      ) : null}
    </li>
  );
}

function Detail({
  customer,
  reload,
  outcome,
  onPromiseChanged,
}: {
  customer: CustomerDetail;
  reload: () => void;
  /** What the last change of a promised date on this screen did; it outlives the reload that follows. */
  outcome: PromiseOutcome | null;
  onPromiseChanged: (outcome: PromiseOutcome) => void;
}) {
  const { api, role, permissions, membershipId, now } = useWorkspace();
  const can = useMay();
  const { t, language } = useI18n();
  const viewer = { role, permissions, membershipId };
  const today = now();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [changing, setChanging] = useState<string | null>(null);

  // A finished write reloads the customer, which replaces this component with a fresh one.
  const reversal = useSubmit((entryId: string, key) => api.reverseEntry(entryId, key).then(reload));
  const archiving = useSubmit((archived: boolean, key) => api.setArchived(customer.id, archived, key).then(reload));

  const archived = customer.status === "archived";
  const busy = reversal.state.status === "pending" || archiving.state.status === "pending";

  return (
    <>
      {/* Who it is and what they owe, with the two things a seller records: one card. */}
      <div className="card">
        <h2 className="subject">{customer.displayName}</h2>
        {customer.phone ? (
          <p className="row__meta">
            <a href={`tel:${customer.phone}`}>{customer.phone}</a>
          </p>
        ) : null}
        {archived ? <p className="notice">{t("customer.archived")}</p> : null}
        {customer.remindersOff ? <p className="row__meta">{t("customer.remindersOff")}</p> : null}

        <BalanceLine label={t("customer.balance")} uzs={customer.balance} usd={customer.usd?.balance} large />
        <OverdueLines overdue={customer.overdue} />
        {customer.usd?.overdue ? <OverdueLines overdue={customer.usd.overdue} currency="USD" /> : null}

        {archived || !(can("credits.record") || can("payments.record")) ? null : (
          <p className="actions">
            {can("credits.record") ? (
              <Link to={`/customers/${customer.id}/credit`} className="button button--primary">
                {t("entry.credit.title")}
              </Link>
            ) : null}
            {/* A debt in either currency can be paid, by a member who may record payments. */}
            {owesAnything(customer) && can("payments.record") ? (
              <Link to={`/customers/${customer.id}/payment`} className="button">
                {t("entry.payment.title")}
              </Link>
            ) : null}
          </p>
        )}
      </div>

      {can("customers.edit") || can("reminders.send") ? (
        <section aria-label={t("customer.manage")}>
          {archiving.state.status === "error" ? (
            <p className="notice notice--error" role="alert">
              {errorText(archiving.state.error, t)}
            </p>
          ) : null}
          {!can("customers.edit") ? null : editing ? (
            <EditForm customer={customer} onSaved={reload} onCancel={() => setEditing(false)} />
          ) : (
            <p className="actions">
              <button type="button" className="button" onClick={() => setEditing(true)} disabled={busy}>
                {t("customer.edit")}
              </button>
              <button type="button" className="button" onClick={() => archiving.submit(!archived)} disabled={busy}>
                {archiving.state.status === "pending"
                  ? t("state.saving")
                  : archived
                    ? t("customer.unarchive")
                    : t("customer.archive")}
              </button>
            </p>
          )}
          {/* Sending a reminder is a manager's and an owner's action by role; a seller is not shown it. */}
          {archived || editing || !can("reminders.send") ? null : <ReminderAction customerId={customer.id} />}
        </section>
      ) : null}

      {customer.paymentNotices.length > 0 ? (
        <p className="notice">
          <span>{t("notices.card.open", { count: customer.paymentNotices.length })}</span>{" "}
          {can("payment_notices.decide") ? <Link to="/payment-notices">{t("notices.title")}</Link> : null}
        </p>
      ) : null}
      <PaymentHistoryNote
        history={customer.paymentHistory}
        {...(customer.usd ? { usd: customer.usd.paymentHistory ?? null } : {})}
      />
      <CreditLimitSection customer={customer} onSaved={reload} />
      <LinkSection customerId={customer.id} archived={archived} />
      {may(viewer, "customers.share") ? <ShareSection customerId={customer.id} archived={archived} /> : null}

      <section aria-labelledby="entries-title">
        <h2 id="entries-title">{t("customer.entries")}</h2>
        {reversal.state.status === "error" ? (
          <p className="notice notice--error" role="alert">
            {errorText(reversal.state.error, t)}
          </p>
        ) : null}
        {outcome ? (
          <div className="notice notice--done" role="status">
            <p>
              {t("dates.changed", {
                previous: dayText(outcome.changed.previousDate, language),
                date: dayText(outcome.changed.promisedDate, language),
              })}
            </p>
            {outcome.changed.dateRequest ? (
              <p>{t("dates.changed.requestClosed", { date: dayText(outcome.changed.dateRequest.requestedDate, language) })}</p>
            ) : outcome.asked !== null ? (
              <p>{t("dates.changed.requestOpen", { date: dayText(outcome.asked, language) })}</p>
            ) : null}
          </div>
        ) : null}
        {customer.entries.length === 0 ? (
          <p className="state">{t("customer.entries.none")}</p>
        ) : (
          <ul className="rows">
            {customer.entries.map((entry) => (
              <EntryRow
                key={entry.id}
                entry={entry}
                goodsPath={
                  // Goods are priced in so'm: none can be added to a sale in dollars.
                  currencyOf(entry) === "UZS" && canAddGoods(entry, today) && mayAddGoods(entry, viewer)
                    ? `/customers/${customer.id}/entries/${entry.id}/goods`
                    : null
                }
                mayReverse={can("entries.cancel")}
                mayChangePromise={can("promises.change")}
                mayDecideDisputes={can("disputes.decide")}
                confirming={confirming === entry.id}
                pending={busy}
                onAsk={() => setConfirming(entry.id)}
                onConfirm={() => reversal.submit(entry.id)}
                onCancel={() => setConfirming(null)}
                changing={changing === entry.id}
                onAskChange={() => setChanging(entry.id)}
                onChanged={(changed) => {
                  // The reload that follows may bring the new entries into this same component.
                  setChanging(null);
                  onPromiseChanged(changed);
                }}
                onCancelChange={() => setChanging(null)}
              />
            ))}
          </ul>
        )}
        {customer.entriesTotal > customer.entries.length ? (
          <p className="row__meta">
            {t("customer.entries.partial", { shown: customer.entries.length, total: customer.entriesTotal })}
          </p>
        ) : null}
      </section>
    </>
  );
}

/** One customer: what they owe, whether it is late, their entries, and the actions the role may take. */
export function CustomerScreen({ customerId }: { customerId: string }) {
  const { api } = useWorkspace();
  const { state, reload } = useLoad((signal) => api.readCustomer(customerId, signal), [api, customerId]);
  const [outcome, setOutcome] = useState<PromiseOutcome | null>(null);

  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return state.error.code === "NOT_FOUND" ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  return (
    <Detail
      customer={state.data}
      reload={reload}
      outcome={outcome}
      onPromiseChanged={(changed) => {
        setOutcome(changed);
        reload();
      }}
    />
  );
}
