import { type FormEvent, type ReactNode, useMemo, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { type Column, DataTable } from "../../panel/DataTable";
import type { Customer } from "../../shared/api";
import { formatMoney } from "../../shared/format";
import { usePagedList } from "../../shared/hooks";
import { Link } from "../../shared/router";
import { Empty, Failure, Loading, LoadMore } from "../../shared/workspace/parts";
import type { AdminApi } from "../adminApi";
import "../messages";
import { NONE } from "../ShopsScreen";
import { supportCustomers } from "./customers";

const MAX_QUERY_LENGTH = 80;
const STATUSES = ["active", "archived"] as const;
type Status = (typeof STATUSES)[number];

/**
 * What is shown in place of a shop's data when the caller has no open support access to it: that it is
 * so, and what to do. Nothing of the shop is on the screen, not even the form that would search it.
 */
export function AccessRequired({ shopId }: { shopId: string }) {
  const { t } = useI18n();
  return (
    <div className="notice notice--error" role="alert">
      <p>{t("admin.support.required")}</p>
      <p>{t("admin.support.required.do")}</p>
      <p className="actions">
        <Link to={`/shops/${shopId}`} className="button">
          {t("admin.support.toShop")}
        </Link>
      </p>
    </div>
  );
}

/** Said on every screen that shows a shop's customers: reading here is never unseen. */
export function Recorded() {
  const { t } = useI18n();
  return (
    <p className="notice" role="note">
      {t("admin.support.recorded")}
    </p>
  );
}

/**
 * A shop's customers as an administrator may read them under an open support access (REQ-059): names,
 * phones, state and debt, and nothing that changes any of it. Each list read is recorded by the server
 * and shown to the owner, so the list is asked for when the screen opens and when a search is sent,
 * never while typing.
 */
export function CustomersScreen({ api, shopId }: { api: AdminApi; shopId: string }) {
  const { t, language } = useI18n();
  const client = useMemo(() => supportCustomers(api), [api]);
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<Status>("active");
  const list = usePagedList((cursor, signal) => client.list(shopId, { q: query, status, cursor }, signal), [client, shopId, query, status]);

  if (list.state.status === "error" && list.state.error.code === "SUPPORT_ACCESS_REQUIRED") {
    return <AccessRequired shopId={shopId} />;
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setQuery(text.split(/\s+/).filter(Boolean).join(" "));
  };

  const columns: Column<Customer>[] = [
    {
      id: "name",
      header: t("admin.support.customers.name"),
      rowHeader: true,
      cell: (customer) => <Link to={`/shops/${shopId}/customers/${customer.id}`}>{customer.displayName}</Link>,
    },
    { id: "phone", header: t("admin.support.customers.phone"), cell: (customer) => customer.phone ?? NONE },
    {
      id: "status",
      header: t("admin.support.customers.status"),
      cell: (customer) => (customer.status === "active" || customer.status === "archived" ? t(`admin.support.customers.status.${customer.status}`) : customer.status),
    },
    { id: "debt", header: t("admin.support.customers.debt"), numeric: true, cell: (customer) => formatMoney(customer.balance, language) },
  ];

  let body: ReactNode;
  if (list.state.status === "loading") {
    body = <Loading />;
  } else if (list.state.status === "error") {
    body = <Failure error={list.state.error} onRetry={list.reload} />;
  } else if (list.state.items.length === 0) {
    body = <Empty>{t("admin.support.customers.none")}</Empty>;
  } else {
    body = (
      <>
        <DataTable caption={t("admin.support.customers.title")} columns={columns} items={list.state.items} rowKey={(customer) => customer.id} />
        {list.state.nextCursor !== null ? (
          <LoadMore loading={list.state.loadingMore} error={list.state.moreError} onClick={list.loadMore} />
        ) : null}
      </>
    );
  }

  return (
    <>
      <Recorded />
      <p className="actions">
        <Link to={`/shops/${shopId}`} className="button">
          {t("admin.support.toShop")}
        </Link>
      </p>
      <form className="filters" role="search" onSubmit={onSubmit}>
        <div className="field field--inline">
          <label htmlFor="support-q">{t("admin.support.customers.search")}</label>
          <input id="support-q" type="search" className="input" value={text} maxLength={MAX_QUERY_LENGTH} onChange={(event) => setText(event.target.value)} />
        </div>
        <div className="field field--inline">
          <label htmlFor="support-status">{t("admin.support.customers.status")}</label>
          <select
            id="support-status"
            className="input"
            value={status}
            onChange={(event) => setStatus(STATUSES.find((option) => option === event.target.value) ?? "active")}
          >
            {STATUSES.map((option) => (
              <option key={option} value={option}>
                {t(`admin.support.customers.status.${option}`)}
              </option>
            ))}
          </select>
        </div>
        <p className="actions">
          <button type="submit" className="button">
            {t("action.search")}
          </button>
        </p>
      </form>
      {body}
    </>
  );
}
