import { type FormEvent, type ReactNode, useState } from "react";

import { catalogs } from "../i18n/catalog";
import { useI18n } from "../i18n/I18nProvider";
import type { MessageKey } from "../i18n/types";
import type { Customer } from "../shared/api";
import { useLoad, usePagedList } from "../shared/hooks";
import { Link } from "../shared/router";
import { NotFoundScreen } from "../shared/screens";
import { useWorkspace } from "../shared/workspace/context";
import { Empty, Failure, formatInstant, Loading, LoadMore } from "../shared/workspace/parts";
import type { Activity, Member } from "./backoffice";
import { type Column, DataTable } from "./DataTable";
import "./messages";
import { memberCode, memberLabel, useOffice } from "./office";

/**
 * The groups an owner can filter by. Each is the start of the action names the server records
 * ("customer" matches "customer.created"), which is how the API filters (application/account.py).
 * "reminder" also covers "reminders.settings_changed".
 */
export const ACTION_GROUPS = [
  "customer",
  "ledger",
  "catalog",
  "staff",
  "ownership",
  "shop",
  "reminder",
  "dispute",
  "counter_code",
  "subscription",
] as const;
type ActionGroup = (typeof ACTION_GROUPS)[number];

const MAX_QUERY_LENGTH = 80;
const PICK_LIMIT = 5;

/** The catalog's wording for an action, or the server's own name for one the catalog does not know yet. */
function actionText(action: string, t: (key: MessageKey) => string): string {
  const key = `activity.action.${action}`;
  return Object.hasOwn(catalogs.uz, key) ? t(key as MessageKey) : action;
}

type CustomerFilter = { id: string; name: string | null };

/** Finds a customer by name or phone to filter the log by. Nothing is searched until it is asked. */
function CustomerPicker({ onPick }: { onPick: (customer: Customer) => void }) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [query, setQuery] = useState<string | null>(null);
  const found = useLoad(
    (signal) =>
      query === null ? Promise.resolve([]) : api.listCustomers({ q: query, limit: PICK_LIMIT }, signal).then((page) => page.items),
    [api, query],
  );

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const clean = text.trim();
    setQuery(clean === "" ? null : clean);
  };

  return (
    <>
      <form className="search" role="search" onSubmit={onSubmit}>
        <div className="field field--inline">
          <label htmlFor="activity-customer">{t("activity.filter.customer")}</label>
          <input
            id="activity-customer"
            type="search"
            className="input"
            value={text}
            maxLength={MAX_QUERY_LENGTH}
            placeholder={t("customers.search")}
            onChange={(event) => setText(event.target.value)}
          />
        </div>
        <button type="submit" className="button">
          {t("action.search")}
        </button>
      </form>
      {query === null ? null : found.state.status === "loading" ? (
        <Loading />
      ) : found.state.status === "error" ? (
        <Failure error={found.state.error} onRetry={found.reload} />
      ) : found.state.data.length === 0 ? (
        <Empty>{t("customers.noMatch")}</Empty>
      ) : (
        <ul className="picks" aria-label={t("activity.filter.customer.list")}>
          {found.state.data.map((customer) => (
            <li key={customer.id}>
              <button
                type="button"
                className="pick"
                onClick={() => {
                  setText("");
                  setQuery(null);
                  onPick(customer);
                }}
              >
                <span className="row__name">{customer.displayName}</span>
                {customer.phone ? <span className="row__meta">{customer.phone}</span> : null}
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function Log() {
  const { membershipId } = useWorkspace();
  const { office } = useOffice();
  const { t, language } = useI18n();
  const [group, setGroup] = useState<ActionGroup | "">("");
  const [actor, setActor] = useState("");
  const [customer, setCustomer] = useState<CustomerFilter | null>(null);
  // The staff list gives the filter its choices and the rows their roles. The log is useful without it.
  const staff = useLoad((signal) => office.listStaff(signal), [office]);
  const members: readonly Member[] = staff.state.status === "ready" ? staff.state.data : [];
  const subject = customer?.id ?? null;
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) => office.listActivity({ actor: actor || null, action: group || null, subject, cursor }, signal),
    [office, actor, group, subject],
  );

  const who = (row: Activity): string => {
    if (row.actorKind === "customer") {
      return t("activity.actor.customer");
    }
    if (row.actorKind !== "staff" || row.actorId === null) {
      return t("activity.actor.system");
    }
    const member = members.find((candidate) => candidate.id === row.actorId);
    // Someone removed from the shop is no longer in the staff list; their code still tells them apart.
    return member
      ? memberLabel(member, membershipId, t)
      : t("staff.member.label", { role: t("activity.actor.staff"), code: memberCode(row.actorId) });
  };

  const about = (row: Activity): ReactNode => {
    const id = row.subjectId;
    if (row.subjectType === "customer" && id !== null) {
      return (
        <span className="table__actions">
          <Link to={`/customers/${id}`}>{customer?.id === id && customer.name ? customer.name : t("customer.open")}</Link>
          {customer?.id === id ? null : (
            <button type="button" className="button button--small" onClick={() => setCustomer({ id, name: null })}>
              {t("activity.filter.customer.only")}
            </button>
          )}
        </span>
      );
    }
    if (row.subjectType === "membership" && id !== null) {
      const member = members.find((candidate) => candidate.id === id);
      return member
        ? memberLabel(member, membershipId, t)
        : t("staff.member.label", { role: t("activity.actor.staff"), code: memberCode(id) });
    }
    const key = `activity.subject.${row.subjectType}`;
    return Object.hasOwn(catalogs.uz, key) ? t(key as MessageKey) : row.subjectType;
  };

  const columns: Column<Activity>[] = [
    { id: "when", header: t("activity.when"), rowHeader: true, cell: (row) => formatInstant(row.at, language) },
    { id: "who", header: t("activity.who"), cell: who },
    { id: "what", header: t("activity.what"), cell: (row) => actionText(row.action, t) },
    { id: "about", header: t("activity.about"), cell: about },
  ];

  let body: ReactNode;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    body = <Empty>{t("activity.none")}</Empty>;
  } else {
    body = (
      <>
        <DataTable caption={t("nav.activityLog")} columns={columns} items={state.items} rowKey={(row) => row.id} />
        {state.nextCursor !== null ? (
          <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} />
        ) : null}
      </>
    );
  }

  return (
    <>
      <section className="filters" aria-label={t("activity.filters")}>
        <div className="field field--inline">
          <label htmlFor="activity-action">{t("activity.filter.action")}</label>
          <select
            id="activity-action"
            className="input"
            value={group}
            onChange={(event) => setGroup(ACTION_GROUPS.find((option) => option === event.target.value) ?? "")}
          >
            <option value="">{t("activity.filter.action.all")}</option>
            {ACTION_GROUPS.map((option) => (
              <option key={option} value={option}>
                {t(`activity.group.${option}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="field field--inline">
          <label htmlFor="activity-actor">{t("activity.filter.actor")}</label>
          <select id="activity-actor" className="input" value={actor} onChange={(event) => setActor(event.target.value)}>
            <option value="">{t("activity.filter.actor.all")}</option>
            {members.map((member) => (
              <option key={member.id} value={member.id}>
                {memberLabel(member, membershipId, t)}
              </option>
            ))}
          </select>
        </div>
        {customer === null ? (
          <CustomerPicker onPick={(picked) => setCustomer({ id: picked.id, name: picked.displayName })} />
        ) : (
          <p className="actions">
            <span>
              {t("activity.filter.customer.active", {
                name: customer.name ?? t("activity.filter.customer.unnamed"),
              })}
            </span>
            <button type="button" className="button button--small" onClick={() => setCustomer(null)}>
              {t("activity.filter.customer.clear")}
            </button>
          </p>
        )}
      </section>
      {body}
    </>
  );
}

/**
 * The shop's activity log (REQ-047): who did what and when, newest first, a page at a time, filtered by
 * the kind of action, the member of staff and the customer. It is the owner's alone.
 */
export function ActivityScreen() {
  const { role } = useWorkspace();
  return role === "owner" ? <Log /> : <NotFoundScreen />;
}
