import { type ReactNode, useState } from "react";

import { useI18n } from "../i18n/I18nProvider";
import { type Column, DataTable } from "../panel/DataTable";
import { formatMoney } from "../shared/format";
import { usePagedList } from "../shared/hooks";
import { Link } from "../shared/router";
import { Empty, Failure, formatInstant, Loading, LoadMore } from "../shared/workspace/parts";
import type { AdminApi, QueuedReceipt } from "./adminApi";
import "./messages";
import { RECEIPT_STATUSES } from "./rules";
import { known, NONE } from "./ShopsScreen";

/**
 * The receipts of subscription payments across all shops (REQ-055): those waiting for a decision first,
 * the oldest at the top, as the server orders them. A receipt whose file was sent in other receipts too
 * is marked. Each row opens the receipt; nothing is decided from the list.
 */
export function ReceiptsScreen({ api }: { api: AdminApi }) {
  const { t, language } = useI18n();
  const [status, setStatus] = useState<string>(RECEIPT_STATUSES[0]);
  const list = usePagedList((cursor, signal) => api.listReceipts({ status, cursor }, signal), [api, status]);

  const columns: Column<QueuedReceipt>[] = [
    {
      id: "created",
      header: t("admin.receipts.created"),
      rowHeader: true,
      cell: (receipt) => <Link to={`/receipts/${receipt.id}`}>{formatInstant(receipt.createdAt, language)}</Link>,
    },
    { id: "shop", header: t("admin.shops.name"), cell: (receipt) => receipt.shopName },
    {
      id: "amount",
      header: t("admin.receipts.amount"),
      numeric: true,
      cell: (receipt) => (receipt.statedAmount === null ? NONE : formatMoney(receipt.statedAmount, language)),
    },
    { id: "stated", header: t("admin.queue.statedMonths"), numeric: true, cell: (receipt) => receipt.statedMonths ?? NONE },
    { id: "status", header: t("admin.receipts.status"), cell: (receipt) => known("admin.receipt", receipt.status, t) },
    {
      id: "copies",
      header: t("admin.queue.copies"),
      cell: (receipt) => (receipt.copies > 0 ? <strong>{t("admin.queue.copies.some", { count: receipt.copies })}</strong> : NONE),
    },
  ];

  let body: ReactNode;
  if (list.state.status === "loading") {
    body = <Loading />;
  } else if (list.state.status === "error") {
    body = <Failure error={list.state.error} onRetry={list.reload} />;
  } else if (list.state.items.length === 0) {
    body = <Empty>{t("admin.queue.none")}</Empty>;
  } else {
    body = (
      <>
        <DataTable caption={t("admin.nav.receipts")} columns={columns} items={list.state.items} rowKey={(receipt) => receipt.id} />
        {list.state.nextCursor !== null ? (
          <LoadMore loading={list.state.loadingMore} error={list.state.moreError} onClick={list.loadMore} />
        ) : null}
      </>
    );
  }

  return (
    <>
      <form className="filters" onSubmit={(event) => event.preventDefault()} noValidate aria-label={t("admin.queue.filters")}>
        <div className="field field--inline">
          <label htmlFor="receipts-status">{t("admin.receipts.status")}</label>
          <select id="receipts-status" className="input" value={status} onChange={(event) => setStatus(event.target.value)}>
            {RECEIPT_STATUSES.map((option) => (
              <option key={option} value={option}>
                {known("admin.receipt", option, t)}
              </option>
            ))}
          </select>
        </div>
      </form>
      <p className="hint">{t("admin.queue.order")}</p>
      {body}
    </>
  );
}
