import { lazy, Suspense, useMemo, type ReactNode } from "react";

import { I18nProvider, useI18n } from "../i18n/I18nProvider";
import type { Language, MessageKey } from "../i18n/types";
import type { ShopApi, ShopMembership } from "./api";
import { useDesktop, type WorkspaceExtension } from "./layout";
import { MORE_ITEM, primaryTabCount, staffSections } from "./navigation";
import { useHashPath } from "./router";
import { MoreScreen, NotFoundScreen, PlaceholderScreen, SignInRequiredScreen } from "./screens";
import type { StaffSession } from "./session";
import { BOT_USERNAME } from "./settings";
import { Shell } from "./Shell";
import { AddGoodsScreen } from "./workspace/AddGoodsScreen";
import { CatalogScreen } from "./workspace/CatalogScreen";
import { useWorkspace, WorkspaceProvider } from "./workspace/context";
import { CounterCodeScreen } from "./workspace/CounterCodeScreen";
import { CreditSettingsSection } from "./workspace/CreditSettingsSection";
import { CustomerScreen } from "./workspace/CustomerScreen";
import { CustomersScreen } from "./workspace/CustomersScreen";
import { DisputesScreen } from "./workspace/DisputesScreen";
import { EntryScreen } from "./workspace/EntryScreen";
import { NewCustomerScreen } from "./workspace/NewCustomerScreen";
import { OverviewScreen } from "./workspace/OverviewScreen";
import { Loading } from "./workspace/parts";
import { RemindersScreen } from "./workspace/RemindersScreen";
import { matchWorkspaceRoute, type WorkspaceRoute } from "./workspace/routes";
import type { ShopMode } from "./workspace/shopMode";
import { ShopSettingsScreen } from "./workspace/ShopSettingsScreen";
import { SubscriptionScreen } from "./workspace/SubscriptionScreen";
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
  /** What the server's refusals have said about the shop: limited, suspended, or nothing so far. */
  shopMode?: ShopMode | null | undefined;
  /** The web panel's screens and sections; the Mini App passes none. */
  extension?: WorkspaceExtension | undefined;
  /** Controls under the side navigation of a wide screen. */
  side?: ReactNode;
  /** Every shop the person works in. */
  shops?: readonly ShopMembership[] | undefined;
  /** Reads the person's shops and roles again. */
  reloadSession?: (() => void) | undefined;
};

type StaffAppProps = StaffRoutesProps & { initialLanguage: Language };

const systemClock = () => new Date();

// Opened by managers and owners now and then, never on the way to recording a sale: loaded on demand,
// so the first load of the Mini App does not carry them (NFR-010).
const PaymentNoticesScreen = lazy(() => import("./workspace/PaymentNoticesScreen"));
const DateRequestsScreen = lazy(() => import("./workspace/DateRequestsScreen"));
const ReportsScreen = lazy(() => import("./reports/ReportsScreen"));
const ExportsScreen = lazy(() => import("./exports/ExportsScreen"));
const ImportScreen = lazy(() => import("./imports/ImportScreen"));
const SupportAccessSection = lazy(() => import("./support/SupportAccessSection"));

/**
 * Support access under the settings of the Mini App, for the owner alone: nobody else may read it, so
 * for nobody else is its code loaded or anything asked. The web panel shows the same section through
 * its extension, where the notice above every screen shares its state.
 */
function OwnerSupportAccess() {
  const { role } = useWorkspace();
  return role === "owner" ? (
    <Suspense fallback={<Loading />}>
      <SupportAccessSection />
    </Suspense>
  ) : null;
}

/**
 * One customer. On a wide screen of the web panel the customer book stays beside it: the list on the
 * left, the customer on the right, each under its own address as before.
 */
function CustomerRoute({ customerId }: { customerId: string }) {
  const { t } = useI18n();
  const detail = <CustomerScreen key={customerId} customerId={customerId} />;
  if (!useDesktop()) {
    return detail;
  }
  return (
    <div className="panes">
      <section className="panes__list" aria-label={t("nav.customers")}>
        <CustomersScreen selectedId={customerId} />
      </section>
      <div className="panes__detail">{detail}</div>
    </div>
  );
}

function workspaceScreen(
  route: WorkspaceRoute,
  overviewFooter: ReactNode,
  extension: WorkspaceExtension | undefined,
): ReactNode {
  switch (route.screen) {
    case "overview":
      return <OverviewScreen footer={overviewFooter} extra={extension ? <extension.OverviewExtra /> : undefined} />;
    case "customers":
      return <CustomersScreen />;
    case "pickCustomer":
      return <CustomersScreen pick />;
    case "newCustomer":
      return <NewCustomerScreen />;
    case "customer":
      return <CustomerRoute customerId={route.customerId} />;
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
    case "paymentNotices":
      return (
        <Suspense fallback={<Loading />}>
          <PaymentNoticesScreen />
        </Suspense>
      );
    case "dateRequests":
      return (
        <Suspense fallback={<Loading />}>
          <DateRequestsScreen />
        </Suspense>
      );
    case "reports":
      return (
        <Suspense fallback={<Loading />}>
          <ReportsScreen />
        </Suspense>
      );
    case "exports":
      return (
        <Suspense fallback={<Loading />}>
          <ExportsScreen
            after={
              <Suspense fallback={<Loading />}>
                <ImportScreen />
              </Suspense>
            }
          />
        </Suspense>
      );
    case "catalog":
      return <CatalogScreen />;
    case "reminders":
      return <RemindersScreen />;
    case "subscription":
      return <SubscriptionScreen />;
    case "shopSettings":
      // The shop's own settings are the owner's to change; its credit rules are a manager's too.
      return (
        <>
          <ShopSettingsScreen />
          <CreditSettingsSection />
          {extension ? <extension.SettingsExtra /> : <OwnerSupportAccess />}
        </>
      );
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
  shopMode = null,
  extension,
  side,
  shops,
  reloadSession,
}: StaffRoutesProps) {
  const { t } = useI18n();
  const path = useHashPath();
  const role = session?.role;
  const membershipId = session?.membershipId ?? null;
  const shopName = session?.shopName;
  const permissions = session?.permissions;
  const workspace = useMemo(
    () =>
      api && role
        ? { api, role, permissions, membershipId, botUsername, now, shopName, shopMode, shops, reloadSession }
        : null,
    [api, role, permissions, membershipId, botUsername, now, shopName, shopMode, shops, reloadSession],
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

  const sections = staffSections(session.role, session.permissions);
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
    // A section the shared workspace has no screen for may have one in the web panel.
    const Added = match ? undefined : extension?.sections[section.id];
    if (match && workspace) {
      screen = workspaceScreen(match.route, overviewFooter, extension);
    } else if (Added && workspace) {
      screen = <Added />;
    } else {
      screen = <PlaceholderScreen />;
    }
  } else if (isMore) {
    title = t(MORE_ITEM.labelKey);
    screen = <MoreScreen items={overflow} />;
  }

  const shell = (
    <Shell
      entryKey={entryKey}
      context={{ label: t("shell.activeShop"), value: session.shopName }}
      items={sections}
      primaryCount={primaryCount}
      moreItem={MORE_ITEM}
      currentPath={path}
      navPath={sectionPath}
      title={title}
      side={side}
      banner={extension && workspace ? <extension.Banner /> : undefined}
    >
      {screen}
    </Shell>
  );
  if (!workspace) {
    return shell;
  }
  // The providers add nothing to the page; they sit around the shell so the banner can use them too.
  return (
    <WorkspaceProvider value={workspace}>
      {extension ? <extension.Provider>{shell}</extension.Provider> : shell}
    </WorkspaceProvider>
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
