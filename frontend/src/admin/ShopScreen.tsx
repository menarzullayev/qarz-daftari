import { type FormEvent, useState } from "react";

import { useI18n, type Translate } from "../i18n/I18nProvider";
import type { MessageKey } from "../i18n/types";
import { type Column, DataTable } from "../panel/DataTable";
import type { ApiError } from "../shared/api";
import { formatCalendarDay, formatMoney, tashkentDay } from "../shared/format";
import { useLoad, useSubmit } from "../shared/hooks";
import { toIsoDate } from "../shared/promise";
import { dayText } from "../shared/promiseParts";
import { Link } from "../shared/router";
import { NotFoundScreen } from "../shared/screens";
import { Confirm, Empty, Failure, FieldError, formatInstant, Loading } from "../shared/workspace/parts";
import type { AdminApi, AdminShop, AdminShopDetail, AuditRow, SubscriptionAction, SubscriptionReceipt, SubscriptionState } from "./adminApi";
import "./messages";
import { OwnerSection } from "./OwnerSection";
import { actorName, cleanReason, dateInRange, dateRange, isChangeRefusal, offeredActions, REASON_MAX, REASON_MIN } from "./rules";
import { known, NONE, planText, stateText } from "./ShopsScreen";
import { SupportSection, type Who } from "./SupportSection";

const ACTION_LABELS: Readonly<Record<SubscriptionAction, MessageKey>> = {
  trial: "admin.change.trial",
  endTrial: "admin.change.endTrial",
  paidThrough: "admin.change.paidThrough",
  suspend: "admin.change.suspend",
  unsuspend: "admin.change.unsuspend",
};
const DATE_LABELS: Readonly<Partial<Record<SubscriptionAction, MessageKey>>> = {
  trial: "admin.change.trial.date",
  paidThrough: "admin.change.paidThrough.date",
};

type Draft = { action: SubscriptionAction; reason: string; date: string | null };

/** The subscription as the audit recorded it before or after a change, in one line. */
function subscriptionLine(value: unknown, t: Translate, day: (iso: string) => string): string {
  if (typeof value !== "object" || value === null) {
    return NONE;
  }
  const sub = value as Readonly<Record<string, unknown>>;
  const parts = [typeof sub["state"] === "string" ? stateText(sub["state"], t) : NONE];
  if (typeof sub["trial_ends"] === "string") {
    parts.push(t("admin.change.line.trial", { date: day(sub["trial_ends"]) }));
  }
  if (typeof sub["paid_through"] === "string") {
    parts.push(t("admin.change.line.paid", { date: day(sub["paid_through"]) }));
  }
  return parts.join(", ");
}

/**
 * One change of a shop's subscription: what, a date where the change sets one, the reason, and then
 * the question. Nothing is sent before the "yes" that follows the summary of what will change.
 */
function ChangeForm({
  api,
  shop,
  action,
  now,
  onChanged,
  onCancel,
}: {
  api: AdminApi;
  shop: AdminShop;
  action: SubscriptionAction;
  now: () => Date;
  onChanged: (changed: AdminShop, action: SubscriptionAction) => void;
  onCancel: () => void;
}) {
  const { t, language } = useI18n();
  const range = dateRange(action, tashkentDay(now()));
  const [date, setDate] = useState("");
  const [reason, setReason] = useState("");
  const [problems, setProblems] = useState<{ date: string | null; reason: string | null }>({ date: null, reason: null });
  const [draft, setDraft] = useState<Draft | null>(null);
  const { state, submit, reset } = useSubmit((payload: Draft, key) =>
    api.changeSubscription(shop.id, payload.action, payload, key).then((changed) => onChanged(changed, payload.action)),
  );
  const pending = state.status === "pending";
  const failure: ApiError | null = state.status === "error" ? state.error : null;
  const reasonMessage = t("admin.reason.invalid", { min: REASON_MIN, max: REASON_MAX });
  const dateMessage = range
    ? t("admin.change.date.invalid", { from: formatCalendarDay(range.min, language), to: formatCalendarDay(range.max, language) })
    : null;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const clean = cleanReason(reason);
    const chosen = range ? dateInRange(date, range) : null;
    const found = { date: range && chosen === null ? dateMessage : null, reason: clean === null ? reasonMessage : null };
    setProblems(found);
    if (clean !== null && found.date === null) {
      reset();
      setDraft({ action, reason: clean, date: chosen });
    }
  };

  if (draft !== null) {
    const refusal = failure?.code === "SUBSCRIPTION_CHANGE_REFUSED" ? failure.fields["reason"] : undefined;
    return (
      <Confirm
        question={
          <>
            {isChangeRefusal(refusal) ? <p className="field__error">{t(`admin.refused.${refusal}`)}</p> : null}
            <p>{t("admin.change.confirm", { shop: shop.name, change: t(ACTION_LABELS[draft.action]) })}</p>
            {draft.date !== null ? <p>{t("admin.change.confirm.date", { date: dayText(draft.date, language) })}</p> : null}
            <p>{t("admin.change.confirm.reason", { reason: draft.reason })}</p>
            {draft.action === "suspend" || draft.action === "unsuspend" ? <p>{t("admin.change.ownerTold")}</p> : null}
          </>
        }
        yes={t("admin.change.yes")}
        no={t("action.back")}
        pending={pending}
        error={failure}
        onYes={() => submit(draft)}
        onNo={() => {
          setDraft(null);
          reset();
        }}
      />
    );
  }

  const dateLabel = DATE_LABELS[action];
  return (
    <form className="form notice" onSubmit={onSubmit} noValidate aria-label={t(ACTION_LABELS[action])}>
      {range && dateLabel ? (
        <div className="field">
          <label htmlFor="change-date">{t(dateLabel)}</label>
          <input
            id="change-date"
            type="date"
            className="input"
            value={date}
            min={toIsoDate(range.min)}
            max={toIsoDate(range.max)}
            aria-invalid={problems.date !== null}
            aria-describedby="change-date-error"
            onChange={(event) => {
              setDate(event.target.value);
              setProblems((current) => ({ ...current, date: null }));
            }}
          />
          <FieldError id="change-date-error" message={problems.date} />
        </div>
      ) : null}
      <div className="field">
        <label htmlFor="change-reason">{t("admin.reason")}</label>
        <textarea
          id="change-reason"
          className="input input--text"
          rows={2}
          maxLength={2000}
          value={reason}
          aria-invalid={problems.reason !== null}
          aria-describedby="change-reason-hint change-reason-error"
          onChange={(event) => {
            setReason(event.target.value);
            setProblems((current) => ({ ...current, reason: null }));
          }}
        />
        <p className="field__hint" id="change-reason-hint">
          {t("admin.reason.hint")}
        </p>
        <FieldError id="change-reason-error" message={problems.reason} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary">
          {t("admin.change.continue")}
        </button>
        <button type="button" className="button" onClick={onCancel}>
          {t("action.cancel")}
        </button>
      </p>
    </form>
  );
}

function Subscription({ subscription }: { subscription: SubscriptionState }) {
  const { t, language } = useI18n();
  return (
    <dl className="facts">
      <dt>{t("admin.shops.state")}</dt>
      <dd>
        {stateText(subscription.state, t)}
        {/* A free shop's row says "limited" and stays so: free is never stored, and no review changes it. */}
        {subscription.storedState !== subscription.state && subscription.state !== "free" ? (
          <span className="facts__note">{t("admin.shop.storedState", { state: stateText(subscription.storedState, t) })}</span>
        ) : null}
        {subscription.priorState ? (
          <span className="facts__note">{t("admin.shop.priorState", { state: stateText(subscription.priorState, t) })}</span>
        ) : null}
      </dd>
      <dt>{t("admin.shops.trialEnds")}</dt>
      <dd>{subscription.trialEnds ? dayText(subscription.trialEnds, language) : NONE}</dd>
      <dt>{t("admin.shops.paidThrough")}</dt>
      <dd>{subscription.paidThrough ? dayText(subscription.paidThrough, language) : NONE}</dd>
    </dl>
  );
}

function Detail({ api, loaded, now, who, reload }: { api: AdminApi; loaded: AdminShopDetail; now: () => Date; who: Who; reload: () => void }) {
  const { t, language } = useI18n();
  // The subscription as the last change answered it; the page is read again for its history.
  const [shop, setShop] = useState<AdminShop>(loaded);
  const [action, setAction] = useState<SubscriptionAction | null>(null);
  const [done, setDone] = useState<SubscriptionAction | null>(null);
  const day = (iso: string) => dayText(iso, language);

  const receipts: Column<SubscriptionReceipt>[] = [
    {
      id: "created",
      header: t("admin.receipts.created"),
      rowHeader: true,
      cell: (receipt) => <Link to={`/receipts/${receipt.id}`}>{formatInstant(receipt.createdAt, language)}</Link>,
    },
    {
      id: "amount",
      header: t("admin.receipts.amount"),
      numeric: true,
      cell: (receipt) => (receipt.statedAmount === null ? NONE : formatMoney(receipt.statedAmount, language)),
    },
    { id: "status", header: t("admin.receipts.status"), cell: (receipt) => known("admin.receipt", receipt.status, t) },
    { id: "months", header: t("admin.receipts.months"), numeric: true, cell: (receipt) => receipt.months ?? NONE },
    { id: "decided", header: t("admin.receipts.decided"), cell: (receipt) => (receipt.decidedAt ? formatInstant(receipt.decidedAt, language) : NONE) },
    { id: "reject", header: t("admin.receipts.rejectReason"), cell: (receipt) => receipt.rejectReason ?? NONE },
  ];
  const changes: Column<AuditRow>[] = [
    { id: "at", header: t("admin.audit.at"), rowHeader: true, cell: (row) => formatInstant(row.at, language) },
    { id: "action", header: t("admin.audit.action"), cell: (row) => known("admin.action", row.action, t) },
    { id: "before", header: t("admin.audit.before"), cell: (row) => subscriptionLine(row.detail["before"], t, day) },
    { id: "after", header: t("admin.audit.after"), cell: (row) => subscriptionLine(row.detail["after"], t, day) },
    { id: "reason", header: t("admin.reason"), cell: (row) => row.reason ?? NONE },
    {
      id: "admin",
      header: t("admin.audit.admin"),
      cell: (row) => actorName(row.adminId, row.actorTgId, (id) => t("admin.actor.groupAdmin", { id }), NONE),
    },
  ];

  return (
    <>
      <h2 className="subject">{shop.name}</h2>
      {done ? (
        <p className="notice notice--done" role="status">
          {t("admin.change.done", { change: t(ACTION_LABELS[done]) })}
        </p>
      ) : null}
      <dl className="facts">
        <dt>{t("admin.shop.status")}</dt>
        <dd>
          {known("admin.shopStatus", shop.status, t)}
          {loaded.deletionDue ? (
            <span className="facts__note">{t("admin.shop.deletionDue", { date: formatInstant(loaded.deletionDue, language) })}</span>
          ) : null}
        </dd>
        <dt>{t("admin.shop.lang")}</dt>
        <dd>{loaded.lang}</dd>
        <dt>{t("admin.shops.created")}</dt>
        <dd>{formatInstant(shop.createdAt, language)}</dd>
        <dt>{t("admin.shop.owner")}</dt>
        <dd>{shop.ownerTgId ?? NONE}</dd>
        <dt>{t("admin.shops.staff")}</dt>
        <dd>{shop.staffCount}</dd>
        <dt>{t("admin.shops.customers")}</dt>
        <dd>{shop.customerCount}</dd>
        {shop.plan !== null ? (
          <>
            <dt>{t("admin.shops.plan")}</dt>
            <dd>{planText(shop, t)}</dd>
          </>
        ) : null}
      </dl>

      <section aria-labelledby="shop-subscription">
        <h2 id="shop-subscription">{t("admin.shop.subscription")}</h2>
        <Subscription subscription={shop.subscription} />
        {action === null ? (
          <p className="actions">
            {offeredActions(shop.subscription).map((offered) => (
              <button
                key={offered}
                type="button"
                className="button"
                onClick={() => {
                  setAction(offered);
                  setDone(null);
                }}
              >
                {t(ACTION_LABELS[offered])}
              </button>
            ))}
          </p>
        ) : (
          <ChangeForm
            key={action}
            api={api}
            shop={shop}
            action={action}
            now={now}
            onChanged={(changed, made) => {
              setShop(changed);
              setAction(null);
              setDone(made);
              reload();
            }}
            onCancel={() => setAction(null)}
          />
        )}
      </section>

      <OwnerSection
        api={api}
        shop={shop}
        deletionPending={loaded.deletionDue !== null}
        onChanged={(changed) => {
          setShop(changed);
          reload();
        }}
      />
      <SupportSection api={api} shop={shop} now={now} who={who} />
      <section aria-labelledby="shop-receipts">
        <h2 id="shop-receipts">{t("admin.shop.receipts")}</h2>
        {loaded.receipts.length === 0 ? (
          <Empty>{t("admin.shop.receipts.none")}</Empty>
        ) : (
          <DataTable caption={t("admin.shop.receipts")} columns={receipts} items={loaded.receipts} rowKey={(receipt) => receipt.id} />
        )}
      </section>

      <section aria-labelledby="shop-changes">
        <h2 id="shop-changes">{t("admin.shop.changes")}</h2>
        {loaded.changes.length === 0 ? (
          <Empty>{t("admin.shop.changes.none")}</Empty>
        ) : (
          <DataTable caption={t("admin.shop.changes")} columns={changes} items={loaded.changes} rowKey={(row) => row.id} />
        )}
        <p className="actions">
          <Link to={`/audit/${shop.id}`} className="button">
            {t("admin.shop.audit")}
          </Link>
        </p>
      </section>
    </>
  );
}

/**
 * One shop as an administrator sees it (REQ-058): its subscription, how many people it has, the
 * receipts of its subscription and the changes administrators made, with the changes the API offers.
 * Each look is recorded by the server. Nothing of the shop's customers or entries is shown here: the
 * support access opened on this page leads to screens of their own for that (REQ-059).
 */
export function ShopScreen({ api, shopId, now, who }: { api: AdminApi; shopId: string; now: () => Date; who: Who }) {
  const { state, reload } = useLoad((signal) => api.readShop(shopId, signal), [api, shopId]);
  // The page keeps showing what it had while it is read again after a change.
  const [last, setLast] = useState<AdminShopDetail | null>(null);
  if (state.status === "ready" && state.data !== last) {
    setLast(state.data);
  }
  if (state.status === "error") {
    return state.error.code === "NOT_FOUND" ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  const shown = state.status === "ready" ? state.data : last;
  return shown === null ? <Loading /> : <Detail key={shown.id} api={api} loaded={shown} now={now} who={who} reload={reload} />;
}
