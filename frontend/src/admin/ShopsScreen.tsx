import { type FormEvent, type ReactNode, useState } from "react";

import { catalogs } from "../i18n/catalog";
import { useI18n, type Translate } from "../i18n/I18nProvider";
import type { MessageKey } from "../i18n/types";
import { type Column, DataTable } from "../panel/DataTable";
import { usePagedList } from "../shared/hooks";
import { dayText } from "../shared/promiseParts";
import { Link } from "../shared/router";
import { Empty, Failure, formatInstant, Loading, LoadMore } from "../shared/workspace/parts";
import type { AdminApi, AdminShop } from "./adminApi";
import "./messages";

/** The subscription states a shop can be filtered by (application/admin.py, `STATES`). */
export const STATES = ["trial", "active", "limited", "suspended"] as const;
/**
 * With the free plan switched on a shop without a period that the plan holds is "free", and the list
 * can be filtered by it; "limited" then leaves those shops out. The server knows neither while it is off.
 */
export const STATES_WITH_PLAN = ["trial", "active", "free", "limited", "suspended"] as const;
const MAX_QUERY_LENGTH = 80;
export const NONE = "—";

/** The catalog's word for one of the server's words, or the server's own when the catalog has none. */
export function known(prefix: string, word: string, t: Translate): string {
  const key = `${prefix}.${word}`;
  return Object.hasOwn(catalogs.uz, key) ? t(key as MessageKey) : word;
}

export function stateText(state: string, t: Translate): string {
  return known("admin.state", state, t);
}

/** How much of the free plan a shop uses, "12 / 30"; nothing for a shop the server sent without it. */
export function planText(shop: AdminShop, t: Translate): string {
  return shop.plan === null ? NONE : t("admin.shops.plan.value", { used: shop.plan.customers, limit: shop.plan.freeCustomers });
}

function planColumn(t: Translate): Column<AdminShop> {
  return { id: "plan", header: t("admin.shops.plan"), numeric: true, cell: (shop) => planText(shop, t) };
}

/**
 * Every shop on the platform, newest first: its name, its subscription, and how many staff and
 * customers it has. Nothing of what a shop sells or is owed is here, because the server gives none of it.
 */
export function ShopsScreen({ api }: { api: AdminApi }) {
  const { t, language } = useI18n();
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [state, setState] = useState("");
  // Whether the free plan is on: the server says so by sending each shop's use of it, and not otherwise.
  const [planOn, setPlanOn] = useState(false);
  const list = usePagedList(
    (cursor, signal) =>
      api.listShops({ q: query, state, cursor }, signal).then((page) => {
        if (page.items.some((shop) => shop.plan !== null)) {
          setPlanOn(true);
        }
        return page;
      }),
    [api, query, state],
  );

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setQuery(text.split(/\s+/).filter(Boolean).join(" "));
  };

  const columns: Column<AdminShop>[] = [
    { id: "name", header: t("admin.shops.name"), rowHeader: true, cell: (shop) => <Link to={`/shops/${shop.id}`}>{shop.name}</Link> },
    { id: "state", header: t("admin.shops.state"), cell: (shop) => stateText(shop.subscription.state, t) },
    {
      id: "trial",
      header: t("admin.shops.trialEnds"),
      cell: (shop) => (shop.subscription.trialEnds ? dayText(shop.subscription.trialEnds, language) : NONE),
    },
    {
      id: "paid",
      header: t("admin.shops.paidThrough"),
      cell: (shop) => (shop.subscription.paidThrough ? dayText(shop.subscription.paidThrough, language) : NONE),
    },
    { id: "staff", header: t("admin.shops.staff"), numeric: true, cell: (shop) => shop.staffCount },
    { id: "customers", header: t("admin.shops.customers"), numeric: true, cell: (shop) => shop.customerCount },
    ...(planOn ? [planColumn(t)] : []),
    { id: "created", header: t("admin.shops.created"), cell: (shop) => formatInstant(shop.createdAt, language) },
  ];

  let body: ReactNode;
  if (list.state.status === "loading") {
    body = <Loading />;
  } else if (list.state.status === "error") {
    body = <Failure error={list.state.error} onRetry={list.reload} />;
  } else if (list.state.items.length === 0) {
    body = <Empty>{query !== "" || state !== "" ? t("admin.shops.noMatch") : t("admin.shops.none")}</Empty>;
  } else {
    body = (
      <>
        <DataTable caption={t("admin.nav.shops")} columns={columns} items={list.state.items} rowKey={(shop) => shop.id} />
        {list.state.nextCursor !== null ? (
          <LoadMore loading={list.state.loadingMore} error={list.state.moreError} onClick={list.loadMore} />
        ) : null}
      </>
    );
  }

  return (
    <>
      <form className="filters" role="search" onSubmit={onSubmit}>
        <div className="field field--inline">
          <label htmlFor="shops-q">{t("admin.shops.search")}</label>
          <input
            id="shops-q"
            type="search"
            className="input"
            value={text}
            maxLength={MAX_QUERY_LENGTH}
            onChange={(event) => setText(event.target.value)}
          />
        </div>
        <div className="field field--inline">
          <label htmlFor="shops-state">{t("admin.shops.state")}</label>
          <select id="shops-state" className="input" value={state} onChange={(event) => setState(event.target.value)}>
            <option value="">{t("admin.shops.state.all")}</option>
            {(planOn ? STATES_WITH_PLAN : STATES).map((option) => (
              <option key={option} value={option}>
                {stateText(option, t)}
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
