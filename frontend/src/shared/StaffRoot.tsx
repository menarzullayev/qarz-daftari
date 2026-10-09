import { lazy, Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { I18nProvider, useI18n } from "../i18n/I18nProvider";
import type { Language, MessageKey } from "../i18n/types";
import { type Api, type ApiAuth, type ApiError, createApi, type Fetch, type ShopMembership, toApiError } from "./api";
import { useLatest } from "./hooks";
import { isCustomerPath, MY_PATH } from "./customer/paths";
import { type ShopSwitch, useDesktop, type WorkspaceExtension } from "./layout";
import type { Held } from "./permissions";
import { Link, navigate, useHashPath } from "./router";
import { SignInRequiredScreen } from "./screens";
import { SignOutEverywhere } from "./SignOutEverywhere";
import { Shell } from "./Shell";
import { StaffRoutes } from "./StaffApp";
import { Failure, Loading } from "./workspace/parts";
import { modeOfRefusal, type ShopMode } from "./workspace/shopMode";

/** What the web panel adds to the workspace; the Mini App passes none of it. */
export type PanelParts = {
  extension: WorkspaceExtension;
  /**
   * Called when the session is not, or is no longer, accepted: the panel shows its sign-in screen.
   * `everywhere` is true when the person has just ended all of their sessions themselves.
   */
  onSignedOut: (everywhere?: boolean) => void;
  /** The controls under the side navigation of a wide screen, around the shop switcher's state. */
  side: (shops: ShopSwitch) => ReactNode;
  /** The same controls for a narrow screen, next to the overview's buttons. */
  footer: ReactNode;
};

type StaffRootProps = {
  entryKey: MessageKey;
  initialLanguage: Language;
  /**
   * Signs the person in and answers how later calls prove it, or null when there is nothing to sign in
   * with (the Mini App opened outside Telegram).
   */
  connect: () => Promise<ApiAuth | null>;
  fetch?: Fetch;
  now?: () => Date;
  /**
   * Whether this client also serves a person as a customer: their own accounts and what they owe. On
   * for the Telegram Mini App, off for the web panel, which is for staff only.
   */
  customerPage?: boolean;
  /** The bot whose deep links the screens show; by default the build's `VITE_BOT_USERNAME`. */
  botUsername?: string | null | undefined;
  panel?: PanelParts | undefined;
};

type Phase =
  | { kind: "connecting" }
  | { kind: "signedOut"; everywhere: boolean }
  | { kind: "failed"; error: ApiError }
  | {
      kind: "ready";
      api: Api;
      shops: ShopMembership[];
      activeShop: string | null;
      permissionsOn: boolean;
      cashBookOn: boolean;
      stockOn: boolean;
      isCustomer: boolean;
    };

const browserFetch: Fetch = (input, init) => window.fetch(input, init);

// Loaded only for a person who has a customer account, so staff who have none never download it.
const CustomerArea = lazy(() => import("./customer/CustomerArea"));

/** Frame for the moments before there is an active shop: no navigation, the given title, one message. */
function Gate({ entryKey, title, children }: { entryKey: MessageKey; title: string; children: ReactNode }) {
  const { t } = useI18n();
  const path = useHashPath();
  return (
    <Shell
      entryKey={entryKey}
      context={{ label: t("shell.activeShop"), value: t("shell.noShop") }}
      items={[]}
      primaryCount={0}
      currentPath={path}
      title={title}
    >
      {children}
    </Shell>
  );
}

function ShopChooser({
  shops,
  error,
  pending,
  onChoose,
}: {
  shops: readonly ShopMembership[];
  error: ApiError | null;
  pending: boolean;
  onChoose: (shop: ShopMembership) => void;
}) {
  const { t } = useI18n();
  // The shop that was asked for last: "try again" after a failure asks for the same one.
  const [asked, setAsked] = useState<ShopMembership | null>(null);
  const choose = (shop: ShopMembership) => {
    setAsked(shop);
    onChoose(shop);
  };
  return (
    <>
      {error ? <Failure error={error} {...(asked && !pending ? { onRetry: () => choose(asked) } : {})} /> : null}
      <ul className="rows">
        {shops.map((shop) => (
          <li key={shop.shopId} className="row">
            <button type="button" className="row__link row__button" onClick={() => choose(shop)} disabled={pending}>
              <span className="row__name">{shop.name}</span>
              <span className="row__meta">{t(`role.${shop.role}`)}</span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

/** `StaffRoot` without the language context, for a caller that provides one of its own (the web panel). */
export function StaffWorkspace({
  entryKey,
  connect,
  fetch = browserFetch,
  now,
  customerPage = false,
  botUsername,
  panel,
}: Omit<StaffRootProps, "initialLanguage">) {
  const { t } = useI18n();
  const path = useHashPath();
  const desktop = useDesktop();
  const [phase, setPhase] = useState<Phase>({ kind: "connecting" });
  const [attempt, setAttempt] = useState(0);
  const [choosing, setChoosing] = useState(false);
  const [choice, setChoice] = useState<{ pending: boolean; error: ApiError | null }>({ pending: false, error: null });
  // What the active shop's refusals have said about it. Only the owner may read the subscription, so
  // for other staff this is the one source; it is forgotten when another shop is chosen.
  const [shopMode, setShopMode] = useState<ShopMode | null>(null);

  // What the page was given, read when a connection is made: only a retry makes one again.
  const given = useLatest({ connect, fetch, customerPage, panel });
  useEffect(() => {
    let cancelled = false;
    const signedOut = () => {
      if (!cancelled) {
        setPhase({ kind: "signedOut", everywhere: false });
        given.current.panel?.onSignedOut();
      }
    };
    setPhase({ kind: "connecting" });
    (async () => {
      const { connect, fetch, customerPage } = given.current;
      const auth = await connect();
      if (auth === null) {
        signedOut();
        return;
      }
      // A session that ends later (expired, or signed out elsewhere) leads back to the sign-in notice.
      const api = createApi({
        fetch,
        auth,
        onUnauthenticated: signedOut,
        onRefusal: (error) => {
          const mode = modeOfRefusal(error);
          if (mode !== null && !cancelled) {
            setShopMode(mode);
          }
        },
      });
      // A failure to read the accounts must not keep a member of staff from their work: for them it
      // only means "my debts" is not offered. For a person with no shop it is the whole page, so it fails.
      const [mine, accounts] = await Promise.all([
        api.myShops(),
        customerPage ? api.myAccounts().catch((error: unknown) => toApiError(error)) : [],
      ]);
      if (mine.items.length === 0 && !Array.isArray(accounts)) {
        throw accounts;
      }
      if (!cancelled) {
        setPhase({
          kind: "ready",
          api,
          shops: mine.items,
          activeShop: mine.activeShop,
          permissionsOn: mine.permissionsOn,
          cashBookOn: mine.cashBookOn,
          stockOn: mine.stockOn,
          isCustomer: Array.isArray(accounts) && accounts.length > 0,
        });
      }
    })().catch((error: unknown) => {
      const failure = toApiError(error);
      if (cancelled) {
        return;
      }
      if (failure.status === 401) {
        signedOut();
      } else {
        setPhase({ kind: "failed", error: failure });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [attempt, given]);

  const ready = phase.kind === "ready" ? phase : null;
  const shop = useMemo(() => {
    if (!ready) {
      return null;
    }
    // With one shop there is nothing to choose; with several, the one the user last made active.
    const only = ready.shops.length === 1 ? ready.shops[0] : undefined;
    return ready.shops.find((candidate) => candidate.shopId === ready.activeShop) ?? only ?? null;
  }, [ready]);
  const shopApi = useMemo(() => (ready && shop ? ready.api.shop(shop.shopId) : undefined), [ready, shop]);
  // What the member may do in the active shop, when the server says (the permission matrix is on). Until
  // it answers, and whenever it does not, the role decides what is offered; the server decides each call.
  const [held, setHeld] = useState<{ shopId: string; permissions: Held } | null>(null);
  const permissionsOn = ready?.permissionsOn ?? false;
  useEffect(() => {
    // Asked only when the server said there is something to ask: with the matrix off, nothing changes.
    if (!shopApi || !shop || !permissionsOn) {
      return undefined;
    }
    let cancelled = false;
    const shopId = shop.shopId;
    shopApi.myPermissions().then(
      (permissions) => {
        if (!cancelled) {
          setHeld({ shopId, permissions });
        }
      },
      () => {
        if (!cancelled) {
          setHeld({ shopId, permissions: null });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [shopApi, shop, permissionsOn]);
  const reloadSession = useCallback(() => setAttempt((count) => count + 1), []);
  // The parts of the product the platform has switched on; one object for as long as none changes.
  const cashBookOn = ready?.cashBookOn ?? false;
  const stockOn = ready?.stockOn ?? false;
  const features = useMemo(() => ({ cashBook: cashBookOn, stock: stockOn }), [cashBookOn, stockOn]);

  if (phase.kind === "connecting") {
    return (
      <Gate entryKey={entryKey} title={t("app.name")}>
        <Loading />
      </Gate>
    );
  }
  if (phase.kind === "signedOut") {
    return (
      <Gate entryKey={entryKey} title={t("screen.signInRequired.title")}>
        {phase.everywhere ? (
          <p className="notice" role="status">
            {t("session.everywhere.done")}
          </p>
        ) : null}
        <SignInRequiredScreen />
      </Gate>
    );
  }
  if (phase.kind === "failed") {
    return (
      <Gate entryKey={entryKey} title={t("state.error")}>
        <Failure error={phase.error} onRetry={() => setAttempt((count) => count + 1)} />
      </Gate>
    );
  }
  // The person ends all of their sessions, this one included: the same screen as for a session that
  // ended by itself, with a line saying what happened. In the panel that screen is the sign-in.
  const api = phase.api;
  const everywhere = (
    <SignOutEverywhere
      signOut={() => api.signOutEverywhere()}
      onDone={() => {
        setPhase({ kind: "signedOut", everywhere: true });
        panel?.onSignedOut(true);
      }}
    />
  );
  // A person may work in one shop and owe in another. The address decides which side is shown: the
  // paths under /my are their own accounts, every other path is the staff workspace. A person with
  // accounts and no shop only has the first side, whatever the path.
  if (phase.isCustomer && (phase.shops.length === 0 || isCustomerPath(path))) {
    return (
      <Suspense
        fallback={
          <Gate entryKey={entryKey} title={t("app.name")}>
            <Loading />
          </Gate>
        }
      >
        <CustomerArea api={phase.api} staffHome={phase.shops.length > 0} now={now} accountsFooter={everywhere} />
      </Suspense>
    );
  }
  if (phase.shops.length === 0) {
    return (
      <Gate entryKey={entryKey} title={t("shops.none.title")}>
        <p>{t("shops.none.body")}</p>
        {everywhere}
      </Gate>
    );
  }

  const choose = (chosen: ShopMembership) => {
    setChoice({ pending: true, error: null });
    phase.api.setActiveShop(chosen.shopId).then(
      () => {
        setChoice({ pending: false, error: null });
        setChoosing(false);
        setShopMode(null);
        setPhase({ ...phase, activeShop: chosen.shopId });
        navigate("/");
      },
      (error: unknown) => setChoice({ pending: false, error: toApiError(error) }),
    );
  };

  if (!shop || choosing) {
    return (
      <Gate entryKey={entryKey} title={t("shops.choose")}>
        <ShopChooser shops={phase.shops} error={choice.error} pending={choice.pending} onChoose={choose} />
      </Gate>
    );
  }

  // On a wide screen of the web panel the shop switcher and sign-out sit under the side navigation;
  // on a narrow one they join the overview's buttons, where the Mini App has its switcher.
  const side =
    panel && desktop
      ? panel.side({ shops: phase.shops, activeShopId: shop.shopId, pending: choice.pending, error: choice.error, choose })
      : undefined;
  const footer = panel && !desktop ? panel.footer : null;
  const switchHere = phase.shops.length > 1 && side === undefined;

  return (
    <StaffRoutes
      entryKey={entryKey}
      session={{
        shopName: shop.name,
        role: shop.role,
        membershipId: shop.membershipId,
        permissions: held?.shopId === shop.shopId ? held.permissions : null,
        features,
      }}
      api={shopApi}
      now={now}
      botUsername={botUsername}
      shopMode={shopMode}
      extension={panel?.extension}
      side={side}
      shops={phase.shops}
      reloadSession={reloadSession}
      overviewFooter={
        <>
          {switchHere || phase.isCustomer || footer ? (
            <p className="actions">
              {switchHere ? (
                <button type="button" className="button" onClick={() => setChoosing(true)}>
                  {t("shops.switch")}
                </button>
              ) : null}
              {phase.isCustomer ? (
                <Link to={MY_PATH} className="button">
                  {t("my.nav.accounts")}
                </Link>
              ) : null}
              {footer}
            </p>
          ) : null}
          {everywhere}
        </>
      }
    />
  );
}

/**
 * The application connected to the server: signs in, finds the active shop (asking which one when the
 * person works in several), and then shows the screens for that shop and that role. With `customerPage`
 * it also serves a person who is a customer of a shop: see the comment where the two sides are chosen.
 */
export function StaffRoot({ initialLanguage, ...root }: StaffRootProps) {
  return (
    <I18nProvider initialLanguage={initialLanguage}>
      <StaffWorkspace {...root} />
    </I18nProvider>
  );
}
