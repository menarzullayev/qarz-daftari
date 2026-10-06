import { useMemo, type ReactNode } from "react";

import { I18nProvider, useI18n } from "../i18n/I18nProvider";
import type { Language, MessageKey } from "../i18n/types";
import type { ShopApi } from "./api";
import { MORE_ITEM, primaryTabCount, staffSections } from "./navigation";
import { useHashPath } from "./router";
import { MoreScreen, NotFoundScreen, PlaceholderScreen, SignInRequiredScreen } from "./screens";
import type { StaffSession } from "./session";
import { BOT_USERNAME } from "./settings";
import { Shell } from "./Shell";
import { AddGoodsScreen } from "./workspace/AddGoodsScreen";
import { CatalogScreen } from "./workspace/CatalogScreen";
import { WorkspaceProvider } from "./workspace/context";
import { CounterCodeScreen } from "./workspace/CounterCodeScreen";
import { CustomerScreen } from "./workspace/CustomerScreen";
import { CustomersScreen } from "./workspace/CustomersScreen";
import { DisputesScreen } from "./workspace/DisputesScreen";
import { EntryScreen } from "./workspace/EntryScreen";
import { NewCustomerScreen } from "./workspace/NewCustomerScreen";
import { OverviewScreen } from "./workspace/OverviewScreen";
import { matchWorkspaceRoute, type WorkspaceRoute } from "./workspace/routes";
import { ShopSettingsScreen } from "./workspace/ShopSettingsScreen";
import { WaitingScreen } from "./workspace/WaitingScreen";
import "./workspace/workspace.css";

export type StaffRoutesProps = {
  /** "entry.app" for the Telegram Mini App, "entry.panel" for the web panel. */
  entryKey: MessageKey;
  session: StaffSession | null;
  /**
   * API of the active shop. Without it (a developer previewing the shell with no server) the data
   * screens show the "coming soon" text and call nothing.
   */
  api?: ShopApi | undefined;
  /** Clock for the screens; tests pass a fixed one. */
  now?: (() => Date) | undefined;
  /** The bot whose deep links connect customers; by default the build's `VITE_BOT_USERNAME`. */
  botUsername?: string | null | undefined;
  /** Shown under the overview, for example the control that switches shops. */
  overviewFooter?: ReactNode;
};

type StaffAppProps = StaffRoutesProps & { initialLanguage: Language };

const systemClock = () => new Date();

function workspaceScreen(route: WorkspaceRoute, overviewFooter: ReactNode): ReactNode {
  switch (route.screen) {
    case "overview":
      return <OverviewScreen footer={overviewFooter} />;
    case "customers":
      return <CustomersScreen />;
    case "pickCustomer":
      return <CustomersScreen pick />;
    case "newCustomer":
      return <NewCustomerScreen />;
    case "customer":
      return <CustomerScreen key={route.customerId} customerId={route.customerId} />;
    case "entry":
      return <EntryScreen key={`${route.customerId}/${route.kind}`} customerId={route.customerId} kind={route.kind} />;
    case "addGoods":
      return <AddGoodsScreen key={route.entryId} customerId={route.customerId} entryId={route.entryId} />;
    case "waiting":
      return <WaitingScreen />;
    case "counterCode":
      return <CounterCodeScreen />;
    case "disputes":
      return <DisputesScreen />;
    case "catalog":
      return <CatalogScreen />;
    case "shopSettings":
      return <ShopSettingsScreen />;
  }
}

/** The routes of the staff workspace inside the shell. The caller provides the language context. */
export function StaffRoutes({
  entryKey,
  session,
  api,
  now = systemClock,
  botUsername = BOT_USERNAME,
  overviewFooter,
}: StaffRoutesProps) {
  const { t } = useI18n();
  const path = useHashPath();
  const role = session?.role;
  const membershipId = session?.membershipId ?? null;
  const workspace = useMemo(
    () => (api && role ? { api, role, membershipId, botUsername, now } : null),
    [api, role, membershipId, botUsername, now],
  );

  if (!session) {
    return (
      <Shell
        entryKey={entryKey}
        context={{ label: t("shell.activeShop"), value: t("shell.noShop") }}
        items={[]}
        primaryCount={0}
        currentPath={path}
        title={t("screen.signInRequired.title")}
      >
        <SignInRequiredScreen />
      </Shell>
    );
  }

  const sections = staffSections(session.role);
  const primaryCount = primaryTabCount(sections);
  const overflow = sections.slice(primaryCount);
  const match = matchWorkspaceRoute(path);
  // A screen inside a section, such as one customer, belongs to that section and obeys its role rule.
  const sectionPath = match?.sectionPath ?? path;
  const section = sections.find((candidate) => candidate.path === sectionPath);
  const isMore = path === MORE_ITEM.path && overflow.length > 0;

  let title = t("notFound.title");
  let screen: ReactNode = <NotFoundScreen />;
  if (section) {
    title = t(match?.titleKey ?? section.labelKey);
    screen =
      match && workspace ? (
        <WorkspaceProvider value={workspace}>{workspaceScreen(match.route, overviewFooter)}</WorkspaceProvider>
      ) : (
        <PlaceholderScreen />
      );
  } else if (isMore) {
    title = t(MORE_ITEM.labelKey);
    screen = <MoreScreen items={overflow} />;
  }

  return (
    <Shell
      entryKey={entryKey}
      context={{ label: t("shell.activeShop"), value: session.shopName }}
      items={sections}
      primaryCount={primaryCount}
      moreItem={MORE_ITEM}
      currentPath={path}
      navPath={sectionPath}
      title={title}
    >
      {screen}
    </Shell>
  );
}

/** The staff workspace: the same screens for the Mini App and the web panel, laid out by width. */
export function StaffApp({ initialLanguage, ...routes }: StaffAppProps) {
  return (
    <I18nProvider initialLanguage={initialLanguage}>
      <StaffRoutes {...routes} />
    </I18nProvider>
  );
}
