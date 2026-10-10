import { type ComponentType, type FormEvent, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { I18nProvider, useI18n } from "../i18n/I18nProvider";
import type { Language } from "../i18n/types";
import { type LoginReturn, NO_RETURN } from "../panel/loginReturn";
import { signInPanel, signOutPanel } from "../panel/signIn";
import { type LoginWidgetProps, TelegramLogin } from "../panel/TelegramLogin";
import { type ApiAuth, type ApiError, type Fetch, toApiError } from "../shared/api";
import { useLatest, useSubmit } from "../shared/hooks";
import { useHashPath } from "../shared/router";
import { NotFoundScreen } from "../shared/screens";
import { BOT_USERNAME } from "../shared/settings";
import { Shell } from "../shared/Shell";
import { errorText, Failure, FieldError, formatInstant, Loading } from "../shared/workspace/parts";
import { QrCode } from "../shared/workspace/StartCode";
import "../shared/workspace/workspace.css";
import { type AdminApi, type AuthStatus, createAdminApi } from "./adminApi";
import { AdminRoutes } from "./AdminApp";
import "./messages";
import { isCode } from "./rules";
import { SecondFactorOff } from "./secondFactor";

type AdminRootProps = {
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

const browserFetch: Fetch = (input, init) => window.fetch(input, init);
const systemClock = () => new Date();
const SECOND_MS = 1000;

/**
 * The frame of everything before the admin session is open. It names the product and nothing else:
 * no section of the administrator's side, no navigation, no word that says what lies behind.
 */
function Door({ title, children }: { title: string; children: ReactNode }) {
  const path = useHashPath();
  return (
    <Shell items={[]} primaryCount={0} currentPath={path} title={title}>
      {children}
    </Shell>
  );
}

/**
 * What a signed-in person who is not an administrator sees, at every address: the screen of an address
 * that does not exist. The server answers them "not found" on every admin route for the same reason.
 */
function Nowhere() {
  const { t } = useI18n();
  return (
    <Door title={t("notFound.title")}>
      <NotFoundScreen />
    </Door>
  );
}

type Attempt = { status: "idle" } | { status: "pending" } | { status: "failed"; error: ApiError | null };

function SignIn({
  fetch,
  botUsername,
  LoginWidget,
  takeReturn,
  expired,
  onSignedIn,
}: {
  fetch: Fetch;
  botUsername: string | null;
  LoginWidget: ComponentType<LoginWidgetProps>;
  takeReturn: () => LoginReturn;
  expired: boolean;
  onSignedIn: (auth: ApiAuth) => void;
}) {
  const { t, language } = useI18n();
  const [attempt, setAttempt] = useState<Attempt>({ status: "idle" });
  const busy = useRef(false);

  // Runs once, when the screen appears: what Telegram sent this page back with, if it did, goes to the
  // server. The fields are taken, so showing the screen again (after signing out) sends nothing.
  const given = useLatest({ fetch, takeReturn, onSignedIn });
  useEffect(() => {
    const returned = given.current.takeReturn();
    if (returned.status === "none" || busy.current) {
      return;
    }
    if (returned.status === "refused") {
      setAttempt({ status: "failed", error: null });
      return;
    }
    busy.current = true;
    setAttempt({ status: "pending" });
    signInPanel(given.current.fetch, returned.data).then(
      (auth) => {
        busy.current = false;
        given.current.onSignedIn(auth);
      },
      (error: unknown) => {
        busy.current = false;
        setAttempt({ status: "failed", error: toApiError(error) });
      },
    );
    // `given` is one box for the life of the screen, so this runs when the screen appears and not again.
  }, [given]);

  const refused = attempt.status === "failed" && (attempt.error === null || attempt.error.status === 401);
  return (
    <Door title={t("door.signIn.title")}>
      {expired ? (
        <p className="notice" role="status">
          {t("door.signIn.expired")}
        </p>
      ) : null}
      <p>{t("door.signIn.body")}</p>
      {botUsername === null ? (
        <p className="notice notice--error">{t("door.signIn.noBot")}</p>
      ) : (
        <>
          {attempt.status === "failed" ? (
            <div className="notice notice--error" role="alert">
              {attempt.error ? <p>{errorText(attempt.error, t)}</p> : null}
              {refused ? <p>{t("door.signIn.refused")}</p> : null}
            </div>
          ) : null}
          {attempt.status === "pending" ? <Loading /> : <LoginWidget botUsername={botUsername} language={language} />}
        </>
      )}
    </Door>
  );
}

/** The instant a lock ends, from the server's refusal: `retry_after_seconds`, counted from now. */
function lockEnd(error: ApiError, now: Date): string | null {
  const seconds = Number(error.fields["retry_after_seconds"] ?? error.retryAfter ?? Number.NaN);
  return Number.isFinite(seconds) && seconds > 0 ? new Date(now.getTime() + seconds * SECOND_MS).toISOString() : null;
}

/** Six digits from the authenticator. The code is sent once and kept nowhere: the field is emptied at once. */
function CodeForm({ api, now, onOpened, onLocked }: { api: AdminApi; now: () => Date; onOpened: () => void; onLocked: (until: string | null) => void }) {
  const { t } = useI18n();
  const [code, setCode] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const { state, submit } = useSubmit((typed: string) =>
    api.openSession(typed).then(onOpened, (error: ApiError) => {
      if (error.code === "SECOND_FACTOR_LOCKED") {
        onLocked(lockEnd(error, now()));
      }
      throw error;
    }),
  );
  const pending = state.status === "pending";

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const typed = code.trim();
    if (!isCode(typed)) {
      setProblem(t("door.code.invalid"));
      return;
    }
    setCode("");
    submit(typed);
  };

  const failure = state.status === "error" ? state.error : null;
  const shown = problem ?? (failure?.code === "VALIDATION" ? t("door.code.invalid") : null);
  return (
    <form className="form" onSubmit={onSubmit} noValidate>
      {failure && failure.code !== "VALIDATION" ? (
        <p className="notice notice--error" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="door-code">{t("door.code.label")}</label>
        <input
          id="door-code"
          className="input input--amount"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          value={code}
          aria-invalid={shown !== null}
          aria-describedby="door-code-error"
          onChange={(event) => {
            setCode(event.target.value);
            setProblem(null);
          }}
        />
        <FieldError id="door-code-error" message={shown} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("door.code.submit")}
        </button>
      </p>
    </form>
  );
}

/**
 * Giving the person their second factor. The server returns its secret once; it is drawn here as text
 * and as a QR code, kept in this component's state only, and gone when the screen is left.
 */
function Enrolment({ api, started, onEnrolled, children }: { api: AdminApi; started: boolean; onEnrolled: () => void; children: ReactNode }) {
  const { t } = useI18n();
  // After an answer, the next press is a new enrolment with a new key; after a failure the same key is
  // sent again, and a secret the server had already created comes back as "not shown again".
  const { state, submit } = useSubmit((_: null, key) =>
    api.enrol(key).catch((error: ApiError) => {
      if (error.code === "ADMIN_ALREADY_ENROLLED") {
        onEnrolled();
      }
      throw error;
    }),
  );
  const pending = state.status === "pending";
  const create = () => submit(null);
  const uri = state.status === "done" ? state.result.otpauthUri : null;

  return (
    <>
      <p>{t("door.enrol.body")}</p>
      {started && state.status !== "done" ? <p className="notice">{t("door.enrol.again")}</p> : null}
      {state.status === "error" ? (
        <p className="notice notice--error" role="alert">
          {errorText(state.error, t)}
        </p>
      ) : null}
      {state.status === "done" && uri === null ? (
        <p className="notice notice--error" role="alert">
          {t("door.enrol.lost")}
        </p>
      ) : null}
      {uri !== null ? (
        <>
          <p className="notice">{t("door.enrol.once")}</p>
          <p className="startcode__text">{uri}</p>
          <QrCode text={uri} />
          <p className="hint">{t("door.enrol.next")}</p>
          {children}
        </>
      ) : (
        <p className="actions">
          <button type="button" className="button button--primary" onClick={create} disabled={pending}>
            {pending ? t("state.saving") : t("door.enrol.create")}
          </button>
        </p>
      )}
    </>
  );
}

function SecondFactor({
  api,
  status,
  now,
  onChanged,
  onSignOut,
}: {
  api: AdminApi;
  status: AuthStatus;
  now: () => Date;
  /** Reads the status again: a session was opened, or the status on screen is behind the server. */
  onChanged: () => void;
  onSignOut: () => void;
}) {
  const { t, language } = useI18n();
  // A lock learned from a refusal, until the status is read again.
  const [lock, setLock] = useState<{ until: string | null } | null>(null);
  const until = lock ? lock.until : status.lockedUntil;
  const locked = lock !== null || (until !== null && new Date(until).getTime() > now().getTime());
  const code = <CodeForm api={api} now={now} onOpened={onChanged} onLocked={(end) => setLock({ until: end })} />;

  return (
    <Door title={t("door.title")}>
      {locked ? (
        <>
          <p className="notice notice--error" role="alert">
            {until ? t("door.locked", { date: formatInstant(until, language) }) : t("door.locked.noDate")}
          </p>
          <p className="actions">
            <button
              type="button"
              className="button"
              onClick={() => {
                setLock(null);
                onChanged();
              }}
            >
              {t("door.locked.check")}
            </button>
          </p>
        </>
      ) : status.confirmed ? (
        <>
          <p>{t("door.code.body")}</p>
          {code}
        </>
      ) : (
        <Enrolment api={api} started={status.enrolled} onEnrolled={onChanged}>
          {code}
        </Enrolment>
      )}
      <p className="actions">
        <button type="button" className="button" onClick={onSignOut}>
          {t("door.signOut")}
        </button>
      </p>
    </Door>
  );
}

type Phase =
  | { kind: "signIn"; expired: boolean }
  | { kind: "checking"; auth: ApiAuth }
  | { kind: "failed"; auth: ApiAuth; error: ApiError }
  | { kind: "nowhere" }
  | { kind: "door"; auth: ApiAuth; status: AuthStatus }
  | { kind: "in"; auth: ApiAuth; status: AuthStatus };

function Root({
  fetch = browserFetch,
  now = systemClock,
  botUsername = BOT_USERNAME,
  LoginWidget = TelegramLogin,
  loginReturn = NO_RETURN,
}: Omit<AdminRootProps, "initialLanguage">) {
  const { t } = useI18n();
  // How calls prove the session: the cookie the browser holds, and the CSRF token, which lives here in
  // memory and nowhere else. A page that is loaded again starts at sign-in.
  const [phase, setPhase] = useState<Phase>({ kind: "signIn", expired: false });
  const [checks, setChecks] = useState(0);
  const auth = "auth" in phase ? phase.auth : null;
  const recheck = useCallback(() => setChecks((count) => count + 1), []);
  // Used once: the server accepts signed fields a single time, and they must not outlive this load.
  const returned = useRef(loginReturn);
  const takeReturn = useCallback(() => {
    const taken = returned.current;
    returned.current = NO_RETURN;
    return taken;
  }, []);

  const api = useMemo(
    () =>
      auth === null
        ? null
        : createAdminApi({
            fetch,
            auth,
            onUnauthenticated: () => setPhase({ kind: "signIn", expired: true }),
            // "Not found" from behind the door may mean the admin session ended: the status says which.
            onRefusal: (error) => {
              if (error.status === 404) {
                recheck();
              }
            },
          }),
    [auth, fetch, recheck],
  );

  // Reads where the person stands: on entering, after a code, and whenever the server says "not found".
  useEffect(() => {
    if (auth === null) {
      return;
    }
    let cancelled = false;
    createAdminApi({ fetch, auth })
      .readAuth()
      .then(
        (status) => {
          if (!cancelled) {
            setPhase({ kind: status.elevated ? "in" : "door", auth, status });
          }
        },
        (error: unknown) => {
          if (cancelled) {
            return;
          }
          const failure = toApiError(error);
          if (failure.status === 401) {
            setPhase({ kind: "signIn", expired: true });
          } else if (failure.status === 404) {
            setPhase({ kind: "nowhere" });
          } else {
            setPhase({ kind: "failed", auth, error: failure });
          }
        },
      );
    return () => {
      cancelled = true;
    };
  }, [auth, checks, fetch]);

  const signOut = useCallback(() => {
    if (auth !== null) {
      // Whatever the server answers, this page forgets the session.
      signOutPanel(fetch, auth).catch(() => undefined);
    }
    setPhase({ kind: "signIn", expired: false });
  }, [auth, fetch]);

  switch (phase.kind) {
    case "signIn":
      return (
        <SignIn
          fetch={fetch}
          botUsername={botUsername}
          LoginWidget={LoginWidget}
          takeReturn={takeReturn}
          expired={phase.expired}
          onSignedIn={(signedIn) => setPhase({ kind: "checking", auth: signedIn })}
        />
      );
    case "checking":
      return (
        <Door title={t("app.name")}>
          <Loading />
        </Door>
      );
    case "failed":
      return (
        <Door title={t("state.error")}>
          <Failure error={phase.error} onRetry={recheck} />
        </Door>
      );
    case "nowhere":
      return <Nowhere />;
    case "door":
      return api ? <SecondFactor api={api} status={phase.status} now={now} onChanged={recheck} onSignOut={signOut} /> : null;
    case "in":
      if (!api) {
        return null;
      }
      // Second factor off on this installation: there is no admin session to end, so nothing to close.
      return (
        <SecondFactorOff.Provider value={phase.status.secondFactorOff}>
          <AdminRoutes
            api={api}
            now={now}
            sessionEnds={phase.status.expiresAt}
            onSessionClosed={phase.status.secondFactorOff ? undefined : recheck}
          />
        </SecondFactorOff.Provider>
      );
  }
}

/**
 * The administrator's panel (REQ-058, ADR-017): Telegram sign-in, then the second factor, then the
 * panel. Each step is decided by the server's answer; this page keeps no secret and no code.
 */
export function AdminRoot({ initialLanguage, ...root }: AdminRootProps) {
  return (
    <I18nProvider initialLanguage={initialLanguage}>
      <Root {...root} />
    </I18nProvider>
  );
}
