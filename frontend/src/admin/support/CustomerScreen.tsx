import { useMemo } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { type Column, DataTable } from "../../panel/DataTable";
import type { CustomerDetail, Entry } from "../../shared/api";
import { formatMoney } from "../../shared/format";
import { useLoad } from "../../shared/hooks";
import { dayText } from "../../shared/promiseParts";
import { Link } from "../../shared/router";
import { NotFoundScreen } from "../../shared/screens";
import { Empty, ENTRY_KIND_LABELS, Failure, formatInstant, Loading } from "../../shared/workspace/parts";
import type { AdminApi } from "../adminApi";
import "../messages";
import { NONE } from "../ShopsScreen";
import { supportCustomers } from "./customers";
import { AccessRequired, Recorded } from "./CustomersScreen";

function Detail({ customer }: { customer: CustomerDetail }) {
  const { t, language } = useI18n();
  const money = (amount: number) => formatMoney(amount, language);
  const kind = (entry: Entry) => {
    const key = ENTRY_KIND_LABELS[entry.kind];
    return key ? t(key) : entry.kind;
  };
  const mark = (entry: Entry) =>
    [entry.reversed ? t("admin.support.entry.reversed") : null, entry.disputed ? t("admin.support.entry.disputed") : null]
      .filter((part) => part !== null)
      .join(", ") || NONE;
  const columns: Column<Entry>[] = [
    { id: "at", header: t("admin.audit.at"), rowHeader: true, cell: (entry) => formatInstant(entry.createdAt, language) },
    { id: "kind", header: t("admin.support.entry.kind"), cell: kind },
    { id: "amount", header: t("admin.support.entry.amount"), numeric: true, cell: (entry) => money(entry.amount) },
    { id: "due", header: t("admin.support.entry.due"), cell: (entry) => (entry.promisedDate ? dayText(entry.promisedDate, language) : NONE) },
    { id: "note", header: t("admin.support.entry.note"), cell: (entry) => entry.note ?? NONE },
    { id: "mark", header: t("admin.support.entry.mark"), cell: mark },
  ];

  return (
    <>
      <h2 className="subject">{customer.displayName}</h2>
      <dl className="facts">
        <dt>{t("admin.support.customers.phone")}</dt>
        <dd>{customer.phone ?? NONE}</dd>
        <dt>{t("admin.support.customers.status")}</dt>
        <dd>
          {customer.status === "active" || customer.status === "archived" ? t(`admin.support.customers.status.${customer.status}`) : customer.status}
        </dd>
        <dt>{t("admin.support.customers.debt")}</dt>
        <dd>{money(customer.balance)}</dd>
        <dt>{t("admin.support.customer.overdue")}</dt>
        <dd>
          {customer.overdue.amount > 0
            ? t("admin.support.customer.overdue.days", { amount: money(customer.overdue.amount), days: customer.overdue.days })
            : NONE}
        </dd>
        <dt>{t("admin.support.customer.limit")}</dt>
        <dd>{customer.creditLimit === null ? t("admin.support.customer.limit.default") : money(customer.creditLimit)}</dd>
      </dl>
      <section aria-labelledby="support-entries">
        <h2 id="support-entries">{t("admin.support.customer.entries")}</h2>
        {customer.entries.length === 0 ? (
          <Empty>{t("admin.support.customer.entries.none")}</Empty>
        ) : (
          <>
            <p className="hint">{t("admin.support.customer.entries.shown", { shown: customer.entries.length, total: customer.entriesTotal })}</p>
            <DataTable caption={t("admin.support.customer.entries")} columns={columns} items={customer.entries} rowKey={(entry) => entry.id} />
          </>
        )}
      </section>
    </>
  );
}

/**
 * One customer of a shop as an administrator may read them under an open support access (REQ-059):
 * who they are, what they owe, and their entries. Nothing here records, reverses or changes anything:
 * the administrator's side has no call that would. Each look is recorded and shown to the owner.
 */
export function CustomerScreen({ api, shopId, customerId }: { api: AdminApi; shopId: string; customerId: string }) {
  const { t } = useI18n();
  const client = useMemo(() => supportCustomers(api), [api]);
  const { state, reload } = useLoad((signal) => client.read(shopId, customerId, signal), [client, shopId, customerId]);

  if (state.status === "error") {
    if (state.error.code === "SUPPORT_ACCESS_REQUIRED") {
      return <AccessRequired shopId={shopId} />;
    }
    return state.error.code === "NOT_FOUND" ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  return (
    <>
      <Recorded />
      <p className="actions">
        <Link to={`/shops/${shopId}/customers`} className="button">
          {t("admin.support.customers.back")}
        </Link>
      </p>
      {state.status === "loading" ? <Loading /> : <Detail customer={state.data} />}
    </>
  );
}
