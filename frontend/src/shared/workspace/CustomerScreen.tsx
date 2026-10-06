import { useState, type FormEvent } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { Customer, CustomerDetail, CustomerPatch, Entry } from "../api";
import { formatCalendarDay, formatDateTime, formatMoney } from "../format";
import { useLoad, useSubmit } from "../hooks";
import { canManage } from "../navigation";
import { parseIsoDate } from "../promise";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { canAddGoods, mayAddGoods } from "./AddGoodsScreen";
import { useWorkspace } from "./context";
import { GoodsList } from "./GoodsEditor";
import { LinkSection } from "./LinkSection";
import { cleanName, customerFieldErrors, nameProblem } from "./NewCustomerScreen";
import { ENTRY_KIND_LABELS, errorText, Failure, FieldError, Loading, OverdueLines } from "./parts";

/**
 * Only a manager or an owner is offered a reversal, and only for an entry the server would accept: not
 * a reversal itself and not one that is already reversed.
 */
export function canReverse(entry: Entry, mayManage: boolean): boolean {
  return mayManage && entry.kind !== "reversal" && !entry.reversed;
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
  mayManage,
  confirming,
  pending,
  onAsk,
  onConfirm,
  onCancel,
}: {
  entry: Entry;
  /** Where goods can be added to this entry, or null when that is not offered. */
  goodsPath: string | null;
  mayManage: boolean;
  confirming: boolean;
  pending: boolean;
  onAsk: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t, language } = useI18n();
  const promised = entry.promisedDate === null ? null : parseIsoDate(entry.promisedDate);
  const made = new Date(entry.createdAt);
  return (
    <li className={entry.reversed ? "row row--struck" : "row"}>
      <p className="row__link">
        <span className="row__name">{t(ENTRY_KIND_LABELS[entry.kind] ?? "entry.kind.other")}</span>
        <span className="row__amount">{formatMoney(entry.amount, language)}</span>
      </p>
      <p className="row__meta">{Number.isNaN(made.getTime()) ? entry.createdAt : formatDateTime(made, language)}</p>
      {entry.note ? <p className="row__note">{entry.note}</p> : null}
      {entry.lines.length > 0 ? <GoodsList lines={entry.lines} /> : null}
      {promised && !entry.reversed ? (
        <p className="row__meta">{t("entry.promised", { date: formatCalendarDay(promised, language) })}</p>
      ) : null}
      {entry.reversed ? <p className="row__meta">{t("entry.reversed")}</p> : null}
      {entry.disputed ? (
        <p className="row__warning">
          <span>{t("entry.disputed")}</span>
          {/* Only a manager or an owner has the list where a dispute is answered. */}
          {mayManage ? <Link to="/disputes">{t("disputes.open")}</Link> : null}
        </p>
      ) : null}
      {goodsPath !== null ? (
        <Link to={goodsPath} className="button button--small">
          {t("goods.later.action")}
        </Link>
      ) : null}
      {canReverse(entry, mayManage) ? (
        confirming ? (
          <div className="notice">
            <p>{t("reversal.confirm", { amount: formatMoney(entry.amount, language) })}</p>
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

function Detail({ customer, reload }: { customer: CustomerDetail; reload: () => void }) {
  const { api, role, membershipId, now } = useWorkspace();
  const { t, language } = useI18n();
  const mayManage = canManage(role);
  const viewer = { role, membershipId };
  const today = now();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);

  // A finished write reloads the customer, which replaces this component with a fresh one.
  const reversal = useSubmit((entryId: string, key) => api.reverseEntry(entryId, key).then(reload));
  const archiving = useSubmit((archived: boolean, key) => api.setArchived(customer.id, archived, key).then(reload));

  const archived = customer.status === "archived";
  const history = customer.paymentHistory;
  const busy = reversal.state.status === "pending" || archiving.state.status === "pending";

  return (
    <>
      <h2 className="subject">{customer.displayName}</h2>
      {customer.phone ? (
        <p className="row__meta">
          <a href={`tel:${customer.phone}`}>{customer.phone}</a>
        </p>
      ) : null}
      {archived ? <p className="notice">{t("customer.archived")}</p> : null}
      {customer.remindersOff ? <p className="row__meta">{t("customer.remindersOff")}</p> : null}

      <p className="balance balance--large">
        <span>{t("customer.balance")}</span> <strong>{formatMoney(customer.balance, language)}</strong>
      </p>
      <OverdueLines overdue={customer.overdue} />
      {history ? (
        <p className="row__meta">
          <span>{t("customer.history.onTime", { percent: history.onTimePercent })}</span>
          {history.longestDelayDays > 0 ? (
            <span> {t("customer.history.longestDelay", { count: history.longestDelayDays })}</span>
          ) : null}
        </p>
      ) : null}

      {archived ? null : (
        <p className="actions">
          <Link to={`/customers/${customer.id}/credit`} className="button button--primary">
            {t("entry.credit.title")}
          </Link>
          {customer.balance > 0 ? (
            <Link to={`/customers/${customer.id}/payment`} className="button">
              {t("entry.payment.title")}
            </Link>
          ) : null}
        </p>
      )}

      {mayManage ? (
        <section aria-label={t("customer.manage")}>
          {archiving.state.status === "error" ? (
            <p className="notice notice--error" role="alert">
              {errorText(archiving.state.error, t)}
            </p>
          ) : null}
          {editing ? (
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
        </section>
      ) : null}

      <LinkSection customerId={customer.id} archived={archived} />

      <section aria-labelledby="entries-title">
        <h2 id="entries-title">{t("customer.entries")}</h2>
        {reversal.state.status === "error" ? (
          <p className="notice notice--error" role="alert">
            {errorText(reversal.state.error, t)}
          </p>
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
                  canAddGoods(entry, today) && mayAddGoods(entry, viewer)
                    ? `/customers/${customer.id}/entries/${entry.id}/goods`
                    : null
                }
                mayManage={mayManage}
                confirming={confirming === entry.id}
                pending={busy}
                onAsk={() => setConfirming(entry.id)}
                onConfirm={() => reversal.submit(entry.id)}
                onCancel={() => setConfirming(null)}
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

  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return state.error.code === "NOT_FOUND" ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  return <Detail customer={state.data} reload={reload} />;
}
