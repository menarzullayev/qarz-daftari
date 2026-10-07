import { type ComponentType, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { I18nProvider, useI18n } from "../i18n/I18nProvider";
import type { Language } from "../i18n/types";
import { type ApiAuth, type ApiError, type Fetch, toApiError } from "../shared/api";
import type { ShopSwitch, WorkspaceExtension } from "../shared/layout";
import { useHashPath } from "../shared/router";
import { BOT_USERNAME } from "../shared/settings";
import { Shell } from "../shared/Shell";
import { type PanelParts, StaffWorkspace } from "../shared/StaffRoot";
import SupportAccessSection from "../shared/support/SupportAccessSection";
import { errorText } from "../shared/workspace/parts";
import { ActivityScreen } from "./ActivityScreen";
import { DeletionSection } from "./DeletionSection";
import "./messages";
import { OfficeProvider, useOffice } from "./office";
import { OfficeBanner } from "./OfficeBanner";
import { OwnerTotals } from "./OwnerTotals";
import "./panel.css";
import { type LoginReturn, NO_RETURN } from "./loginReturn";
import { signInPanel, signOutPanel } from "./signIn";
import { StaffScreen } from "./StaffScreen";
import { DesktopLayout } from "./tables";
import { type LoginWidgetProps, TelegramLogin } from "./TelegramLogin";

/**
 * Under the shop's settings, the owner's alone: support access, whose ending here also takes the notice
 * off every screen, and the deletion of the shop.
 */
function SettingsExtra() {
  const { reloadSupport } = useOffice();
  return (
    <>
      <SupportAccessSection onChanged={reloadSupport} />
      <DeletionSection />
    </>
  );
}

/** What the web panel adds to the shared workspace: the owner's back office (REQ-050). */
export const PANEL_EXTENSION: WorkspaceExtension = {
  Provider: OfficeProvider,
  Banner: OfficeBanner,
  sections: { staff: StaffScreen, activityLog: ActivityScreen },
  SettingsExtra,
  OverviewExtra: OwnerTotals,
};

type PanelRootProps = {
  initialLanguage: Language;
  fetch?: Fetch;
  now?: () => Date;
  /** The bot people sign in through; by default the build's `VITE_BOT_USERNAME`. */
  botUsername?: string | null;
  /** The sign-in button; tests and previews replace Telegram's with one that loads nothing. */
  LoginWidget?: ComponentType<LoginWidgetProps>;
  /** What Telegram sent the browser back with, taken from the address when the page loaded. */
  loginReturn?: LoginReturn;
};

/** Why the sign-in screen is shown again, when it is not the first time. */
type Ended = "expired" | "signedOut";

const browserFetch: Fetch = (input, init) => window.fetch(input, init);

/** The active shop as a choice among the person's shops (REQ-064); the choosing itself is StaffRoot's. */
function ShopSwitcher({ shops, activeShopId, pending, error, choose }: ShopSwitch) {
  const { t } = useI18n();
  if (shops.length < 2) {
    return null;
  }
  return (
    <div className="field">
      <label htmlFor="panel-shop">{t("panel.shop.switch")}</label>
      <select
        id="panel-shop"
        className="input"
        value={activeShopId}
        disabled={pending}
        onChange={(event) => {
          const chosen = shops.find((shop) => shop.shopId === event.target.value);
          if (chosen && chosen.shopId !== activeShopId) {
            choose(chosen);
          }
        }}
      >
        {shops.map((shop) => (
          <option key={shop.shopId} value={shop.shopId}>
            {t("panel.shop.option", { name: shop.name, role: t(`role.${shop.role}`) })}
          </option>
        ))}
      </select>
      {error ? (
        <p className="field__error" role="alert">
          {errorText(error, t)}
        </p>
      ) : null}
    </div>
  );
}

function SignOut({ pending, error, onClick }: { pending: boolean; error: ApiError | null; onClick: () => void }) {
  const { t } = useI18n();
  return (
    <>
      <button type="button" className="button" onClick={onClick} disabled={pending}>
        {pending ? t("panel.signOut.pending") : t("panel.signOut")}
      </button>
      {error ? (
        <span className="field__error" role="alert">
          {errorText(error, t)}
        </span>
      ) : null}
    </>
  );
}

function SignedIn({
  auth,
  fetch,
  now,
  botUsername,
  onEnded,
}: {
  auth: ApiAuth;
  fetch: Fetch;
  now: (() => Date) | undefined;
  botUsername: string | null;
  onEnded: (why: Ended) => void;
}) {
  const [leaving, setLeaving] = useState<{ pending: boolean; error: ApiError | null }>({ pending: false, error: null });
  const busy = useRef(false);

  const signOut = useCallback(() => {
    if (busy.current) {
      return;
    }
    busy.current = true;
    setLeaving({ pending: true, error: null });
    signOutPanel(fetch, auth).then(
      () => onEnded("signedOut"),
      (error: unknown) => {
        const failure = toApiError(error);
        // A session the server no longer knows is already signed out.
        if (failure.status === 401) {
          onEnded("signedOut");
          return;
        }
        busy.current = false;
        setLeaving({ pending: false, error: failure });
      },
    );
  }, [auth, fetch, onEnded]);

  const panel = useMemo<PanelParts>(() => {
    const button = <SignOut pending={leaving.pending} error={leaving.error} onClick={signOut} />;
    return {
      extension: PANEL_EXTENSION,
      onSignedOut: () => onEnded("expired"),
      side: (shops) => (
        <>
          <ShopSwitcher {...shops} />
          {button}
        </>
      ),
      footer: button,
    };
  }, [leaving, signOut, onEnded]);
  const connect = useCallback(async () => auth, [auth]);

  return (
    <DesktopLayout>
      <StaffWorkspace
        entryKey="entry.panel"
        connect={connect}
        fetch={fetch}
        botUsername={botUsername}
        panel={panel}
        {...(now ? { now } : {})}
      />
    </DesktopLayout>
  );
}

type Attempt = { status: "idle" } | { status: "pending" } | { status: "failed"; error: ApiError | null };

/**
 * The panel's first screen: "Log in with Telegram" (ADR-017). The widget sends the browser away and back;
 * what it came back with goes to the server once, whose answer is the session cookie and the CSRF token
 * that the caller keeps in memory.
 */
function SignIn({
  fetch,
  botUsername,
  LoginWidget,
  takeReturn,
  ended,
  onSignedIn,
}: {
  fetch: Fetch;
  botUsername: string | null;
  LoginWidget: ComponentType<LoginWidgetProps>;
  takeReturn: () => LoginReturn;
  ended: Ended | null;
  onSignedIn: (auth: ApiAuth) => void;
}) {
  const { t, language } = useI18n();
  const path = useHashPath();
  const [attempt, setAttempt] = useState<Attempt>({ status: "idle" });
  const busy = useRef(false);

  // Runs once, when the screen appears: what Telegram sent this page back with, if it did, goes to the
  // server. The fields are taken, so showing the screen again (after signing out) sends nothing.
  useEffect(() => {
    const returned = takeReturn();
    if (returned.status === "none" || busy.current) {
      return;
    }
    if (returned.status === "refused") {
      setAttempt({ status: "failed", error: null });
      return;
    }
    busy.current = true;
    setAttempt({ status: "pending" });
    signInPanel(fetch, returned.data).then(
      (auth) => {
        busy.current = false;
        onSignedIn(auth);
      },
      (error: unknown) => {
        busy.current = false;
        setAttempt({ status: "failed", error: toApiError(error) });
      },
    );
    // `fetch` and the two callbacks are fixed for the life of the page.
  }, []);

  // The server's 401 and data that is not Telegram's both mean the same to the person: press again.
  const refused = attempt.status === "failed" && (attempt.error === null || attempt.error.status === 401);

  return (
    <Shell
      entryKey="entry.panel"
      context={{ label: t("shell.activeShop"), value: t("shell.noShop") }}
      items={[]}
      primaryCount={0}
      currentPath={path}
      title={t("panel.signIn.title")}
    >
      {ended ? (
        <p className="notice" role="status">
          {t(ended === "expired" ? "panel.signIn.expired" : "panel.signIn.signedOut")}
        </p>
      ) : null}
      <p>{t("panel.signIn.body")}</p>
      {botUsername === null ? (
        <p className="notice notice--error">{t("panel.signIn.noBot")}</p>
      ) : (
        <>
          {attempt.status === "failed" ? (
            <div className="notice notice--error" role="alert">
              {attempt.error ? <p>{errorText(attempt.error, t)}</p> : null}
              {refused ? <p>{t("panel.signIn.refused")}</p> : null}
            </div>
          ) : null}
          {attempt.status === "pending" ? (
            <p className="state" role="status">
              {t("panel.signIn.pending")}
            </p>
          ) : null}
          {attempt.status === "pending" ? null : <LoginWidget botUsername={botUsername} language={language} />}
          <p className="hint">{t("panel.signIn.reload")}</p>
        </>
      )}
    </Shell>
  );
}

function Panel({
  fetch = browserFetch,
  now,
  botUsername = BOT_USERNAME,
  LoginWidget = TelegramLogin,
  loginReturn = NO_RETURN,
}: Omit<PanelRootProps, "initialLanguage">) {
  // How later calls prove the session: the cookie the browser holds, and this CSRF token. It lives here,
  // in memory, and nowhere else: a reload of the page starts at the sign-in screen again.
  const [auth, setAuth] = useState<ApiAuth | null>(null);
  const [ended, setEnded] = useState<Ended | null>(null);
  const onEnded = useCallback((why: Ended) => {
    setAuth(null);
    setEnded(why);
  }, []);
  // Used once: the server accepts signed fields a single time, and they must not outlive this load.
  const returned = useRef(loginReturn);
  const takeReturn = useCallback(() => {
    const taken = returned.current;
    returned.current = NO_RETURN;
    return taken;
  }, []);

  if (auth === null) {
    return (
      <SignIn fetch={fetch} botUsername={botUsername} LoginWidget={LoginWidget} takeReturn={takeReturn} ended={ended} onSignedIn={setAuth} />
    );
  }
  return <SignedIn auth={auth} fetch={fetch} now={now} botUsername={botUsername} onEnded={onEnded} />;
}

/**
 * The web panel (REQ-050): sign-in through Telegram, then the staff workspace on a desktop layout with
 * the owner's back office added. A session that ends, here or on the server, leads back to sign-in.
 */
export function PanelRoot({ initialLanguage, ...panel }: PanelRootProps) {
  return (
    <I18nProvider initialLanguage={initialLanguage}>
      <Panel {...panel} />
    </I18nProvider>
  );
}
