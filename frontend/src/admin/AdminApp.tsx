import { type ReactNode, useMemo, useState } from "react";

import { I18nProvider, useI18n } from "../i18n/I18nProvider";
import type { Language, MessageKey } from "../i18n/types";
import "../panel/panel.css";
import { type ApiError, toApiError } from "../shared/api";
import { useHashPath } from "../shared/router";
import { NotFoundScreen, PlaceholderScreen, SignInRequiredScreen } from "../shared/screens";
import { Shell } from "../shared/Shell";
import { errorText, formatInstant } from "../shared/workspace/parts";
import "../shared/workspace/workspace.css";
import type { AdminApi } from "./adminApi";
import { AuditScreen } from "./AuditScreen";
import "./messages";
import { ADMIN_SECTIONS } from "./navigation";
import { isUuid } from "./rules";
import { SettingsScreen } from "./SettingsScreen";
import { ShopScreen } from "./ShopScreen";
import { ShopsScreen } from "./ShopsScreen";
import { CustomerScreen } from "./support/CustomerScreen";
import { CustomersScreen } from "./support/CustomersScreen";
import { SupportAccessScreen } from "./SupportAccessScreen";
import type { Who } from "./SupportSection";

type AdminAppProps = {
  /** False shows only the sign-in notice. Used to look at the frame while developing; see `AdminRoot`. */
  signedIn: boolean;
  initialLanguage: Language;
};

export type AdminRoutesProps = {
  /**
   * The administrator's API behind an open admin session. Without it (a developer looking at the frame
   * with no server) every section shows the "coming soon" text and calls nothing.
   */
  api?: AdminApi | undefined;
  now?: (() => Date) | undefined;
  /** When the admin session ends; shown under the navigation. */
  sessionEnds?: string | null | undefined;
  /** Called once the admin session is closed, to read where the person now stands. */
  onSessionClosed?: (() => void) | undefined;
};

type Match = { sectionPath: string; titleKey: MessageKey; screen: ReactNode };

const systemClock = () => new Date();

/**
 * The screens that show a shop's customers (REQ-059). They are reached under the shop, and only they
 * name a customer anywhere in this panel; the server answers them only under an open support access.
 */
function customers(shopId: string, rest: readonly string[], api: AdminApi): Match | null {
  const [customerId, ...more] = rest;
  if (more.length > 0) {
    return null;
  }
  if (customerId === undefined) {
    return { sectionPath: "/", titleKey: "admin.support.customers.title", screen: <CustomersScreen key={shopId} api={api} shopId={shopId} /> };
  }
  return isUuid(customerId)
    ? {
        sectionPath: "/",
        titleKey: "admin.support.customer.title",
        screen: <CustomerScreen key={customerId} api={api} shopId={shopId} customerId={customerId} />,
      }
    : null;
}

/** The screen for an address, or null. An identifier is a UUID; anything else is an unknown address. */
function match(path: string, api: AdminApi, now: () => Date, who: Who): Match | null {
  const [, first, second, third, ...rest] = path.split("/");
  if (first === "shops" && second !== undefined && isUuid(second) && third === "customers") {
    return customers(second, rest, api);
  }
  if (third !== undefined) {
    return null;
  }
  if (path === "/") {
    return { sectionPath: "/", titleKey: "admin.nav.shops", screen: <ShopsScreen api={api} /> };
  }
  if (first === "shops" && second !== undefined && isUuid(second)) {
    return { sectionPath: "/", titleKey: "admin.shop.title", screen: <ShopScreen key={second} api={api} shopId={second} now={now} who={who} /> };
  }
  if (first === "support-access" && (second === undefined || isUuid(second))) {
    return {
      sectionPath: "/support-access",
      titleKey: "admin.nav.supportAccess",
      screen: <SupportAccessScreen key={second ?? ""} api={api} shopId={second ?? null} now={now} who={who} />,
    };
  }
  if (path === "/settings") {
    return { sectionPath: "/settings", titleKey: "admin.nav.settings", screen: <SettingsScreen api={api} /> };
  }
  if (first === "audit" && (second === undefined || isUuid(second))) {
    return { sectionPath: "/audit", titleKey: "admin.nav.audit", screen: <AuditScreen api={api} shopId={second ?? null} /> };
  }
  return null;
}

/** Closing the admin session: the second cookie is taken away, the Telegram sign-in stays. */
function CloseSession({ api, sessionEnds, onClosed }: { api: AdminApi; sessionEnds: string | null; onClosed: () => void }) {
  const { t, language } = useI18n();
  const [state, setState] = useState<{ pending: boolean; error: ApiError | null }>({ pending: false, error: null });
  const close = () => {
    setState({ pending: true, error: null });
    api.closeSession().then(onClosed, (error: unknown) => setState({ pending: false, error: toApiError(error) }));
  };
  return (
    <>
      {sessionEnds ? <p className="row__meta">{t("admin.session.ends", { date: formatInstant(sessionEnds, language) })}</p> : null}
      <button type="button" className="button" onClick={close} disabled={state.pending}>
        {state.pending ? t("state.saving") : t("admin.session.close")}
      </button>
      {state.error ? (
        <span className="field__error" role="alert">
          {errorText(state.error, t)}
        </span>
      ) : null}
    </>
  );
}

/**
 * The administrator's panel behind the door: its own navigation and no shop. No customer data is
 * anywhere in it but on the two screens of an open support access.
 */
export function AdminRoutes({ api, now = systemClock, sessionEnds = null, onSessionClosed }: AdminRoutesProps) {
  const { t } = useI18n();
  const path = useHashPath();
  const section = ADMIN_SECTIONS.find((candidate) => candidate.path === path);
  // Which administrator this is, once an access opened here has said so; see `Who`. Memory only.
  const [me, setMe] = useState<string | null>(null);
  const who = useMemo<Who>(() => ({ me, learn: setMe }), [me]);
  const found = api ? match(path, api, now, who) : null;

  let title = t("notFound.title");
  let screen: ReactNode = <NotFoundScreen />;
  if (found) {
    title = t(found.titleKey);
    screen = found.screen;
  } else if (section) {
    // Subscription receipts are another story: their section waits, and calls nothing.
    title = t(section.labelKey);
    screen = <PlaceholderScreen />;
  }

  return (
    <Shell
      entryKey="entry.admin"
      items={ADMIN_SECTIONS}
      primaryCount={ADMIN_SECTIONS.length}
      currentPath={path}
      navPath={found?.sectionPath ?? path}
      title={title}
    >
      {screen}
      {api && onSessionClosed ? (
        <footer className="actions admin-session">
          <CloseSession api={api} sessionEnds={sessionEnds} onClosed={onSessionClosed} />
        </footer>
      ) : null}
    </Shell>
  );
}

function Preview({ signedIn }: Pick<AdminAppProps, "signedIn">) {
  const { t } = useI18n();
  const path = useHashPath();
  if (signedIn) {
    return <AdminRoutes />;
  }
  return (
    <Shell entryKey="entry.admin" items={[]} primaryCount={0} currentPath={path} title={t("screen.signInRequired.title")}>
      <SignInRequiredScreen />
    </Shell>
  );
}

/** The frame of the administration panel with no server behind it: a developer's preview. */
export function AdminApp({ signedIn, initialLanguage }: AdminAppProps) {
  return (
    <I18nProvider initialLanguage={initialLanguage}>
      <Preview signedIn={signedIn} />
    </I18nProvider>
  );
}
