import { useMemo, type ReactNode } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { Api, MyAccount } from "../api";
import { formatMoney } from "../format";
import { useLoad } from "../hooks";
import { Link, useHashPath } from "../router";
import { NotFoundScreen } from "../screens";
import { Shell, type ShellNavItem } from "../Shell";
import { Empty, Failure, Loading } from "../workspace/parts";
import "../workspace/workspace.css";
import { AccountScreen } from "./AccountScreen";
import { MY_PATH } from "./paths";

const ID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const ACCOUNT = new RegExp(`^${MY_PATH}/(${ID})$`, "i");

export type CustomerRoute = { screen: "accounts" } | { screen: "account"; linkId: string };

/**
 * The customer screen for a path, or null. A link identifier is an opaque UUID; anything else in its
 * place is an unknown route, not a request to the server. For a person with no shop the home route is
 * their accounts.
 */
export function matchCustomerRoute(path: string, staffHome: boolean): CustomerRoute | null {
  if (path === MY_PATH || (!staffHome && path === "/")) {
    return { screen: "accounts" };
  }
  const account = ACCOUNT.exec(path);
  return account?.[1] ? { screen: "account", linkId: account[1] } : null;
}

const STAFF_AND_MINE: readonly ShellNavItem[] = [
  { id: "staff", path: "/", labelKey: "my.nav.staff" },
  { id: "mine", path: MY_PATH, labelKey: "my.nav.accounts" },
];

function AccountRow({ account }: { account: MyAccount }) {
  const { language } = useI18n();
  return (
    <li className="row">
      <Link to={`${MY_PATH}/${account.linkId}`} className="row__link">
        <span className="row__name">{account.shopName}</span>
        <span className="row__amount">{formatMoney(account.balance, language)}</span>
      </Link>
    </li>
  );
}

/** The shops where the person is a customer. With a single one there is nothing to choose: it is shown. */
function Accounts({ api }: { api: Api }) {
  const { t } = useI18n();
  const { state, reload } = useLoad((signal) => api.myAccounts(signal), [api]);
  const only = state.status === "ready" && state.data.length === 1 ? state.data[0] : undefined;
  const onlyApi = useMemo(() => (only ? api.account(only.linkId) : null), [api, only?.linkId]);

  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return <Failure error={state.error} onRetry={reload} />;
  }
  if (state.data.length === 0) {
    return <Empty>{t("my.none")}</Empty>;
  }
  if (onlyApi) {
    // This is already the list's address, so "back" reads the list again instead of going anywhere.
    return (
      <AccountScreen
        api={onlyApi}
        back={
          <button type="button" className="button" onClick={reload}>
            {t("my.back")}
          </button>
        }
      />
    );
  }
  return (
    <>
      <p className="hint">{t("my.choose")}</p>
      <ul className="rows">
        {state.data.map((account) => (
          <AccountRow key={account.linkId} account={account} />
        ))}
      </ul>
    </>
  );
}

function OneAccount({ api, linkId }: { api: Api; linkId: string }) {
  const { t } = useI18n();
  const account = useMemo(() => api.account(linkId), [api, linkId]);
  return (
    <AccountScreen
      api={account}
      back={
        <Link to={MY_PATH} className="button">
          {t("my.back")}
        </Link>
      }
    />
  );
}

export type CustomerAreaProps = {
  api: Api;
  /** True when the person also works in a shop: the navigation then offers the way back to it. */
  staffHome: boolean;
};

/**
 * The customer's own pages inside the shell: their accounts and one account. It is loaded on demand,
 * so a member of staff who is nobody's customer never downloads it.
 */
export default function CustomerArea({ api, staffHome }: CustomerAreaProps) {
  const { t } = useI18n();
  const path = useHashPath();
  const route = matchCustomerRoute(path, staffHome);
  const items = staffHome ? STAFF_AND_MINE : [];

  let title = t("notFound.title");
  let screen: ReactNode = <NotFoundScreen />;
  if (route?.screen === "accounts") {
    title = t("my.title");
    screen = <Accounts api={api} />;
  } else if (route?.screen === "account") {
    title = t("my.account.title");
    screen = <OneAccount key={route.linkId} api={api} linkId={route.linkId} />;
  }

  return (
    <Shell
      entryKey="entry.customer"
      items={items}
      primaryCount={items.length}
      currentPath={path}
      navPath={MY_PATH}
      title={title}
    >
      {screen}
    </Shell>
  );
}
