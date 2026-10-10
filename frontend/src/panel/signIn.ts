import { type ApiAuth, call, type Fetch, reading } from "../shared/api";

/**
 * The web panel's session (ADR-017, backend/src/qarz/interface/auth_api.py).
 *
 * Telegram's Login Widget sends the browser back to the page with the person's signed data in the query
 * string, which the page takes out of the address at once (`loginReturn.ts`).
 * `POST /api/v1/auth/telegram-login` verifies it, sets the session as an HTTP-only cookie the page cannot read, and answers a CSRF token
 * in the body. Every later request that changes something must carry that token in `X-CSRF-Token`;
 * a cookie alone is refused. The token is kept in memory only: never in storage, never in an address.
 */

/** What the widget returns: the fields Telegram signed, and the signature in `hash`. */
export type TelegramLoginData = Readonly<Record<string, string | number>>;

/**
 * The widget's data as the server accepts it, or null when it is not that. Every field is sent as it
 * came: the signature covers all of them, so dropping or adding one would make it invalid.
 */
export function readLoginData(raw: unknown): TelegramLoginData | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return null;
  }
  const data: Record<string, string | number> = {};
  for (const [name, value] of Object.entries(raw)) {
    if (typeof value === "string" || (typeof value === "number" && Number.isFinite(value))) {
      data[name] = value;
    } else if (value !== null && value !== undefined) {
      return null;
    }
  }
  return typeof data["hash"] === "string" && data["hash"] !== "" && "id" in data && "auth_date" in data ? data : null;
}

/** Signs in with the widget's data. The cookie is set by the answer; the result is the CSRF token. */
export async function signInPanel(fetch: Fetch, data: TelegramLoginData): Promise<ApiAuth> {
  const csrfToken = await call(
    // "cookie" makes the request same-origin with credentials, so the browser keeps the session cookie.
    { fetch, auth: { kind: "cookie", csrfToken: null } },
    {
      method: "POST",
      path: "/api/v1/auth/telegram-login",
      body: data,
      read: (value) => reading.text(reading.record(value)["csrf_token"]),
    },
  );
  return { kind: "cookie", csrfToken };
}

/**
 * Signs an administrator in with a login and a password (`POST /api/v1/auth/admin-password`). The answer
 * is the one the Telegram sign-in gives: the cookie is set and the CSRF token comes in the body. The
 * password goes to the server and nowhere else: it is not kept, not even in memory, after the call.
 */
export async function signInWithPassword(fetch: Fetch, login: string, password: string): Promise<ApiAuth> {
  const csrfToken = await call(
    { fetch, auth: { kind: "cookie", csrfToken: null } },
    {
      method: "POST",
      path: "/api/v1/auth/admin-password",
      body: { login, password },
      read: (value) => reading.text(reading.record(value)["csrf_token"]),
    },
  );
  return { kind: "cookie", csrfToken };
}

const PASSKEY_PATH = "/api/v1/auth/admin-passkey";

/**
 * What a passkey must answer (`WWW-Authenticate` of the refusal an empty question gets), or null when
 * this server has no passkeys. Asked with the page's own fetch: the header is what is read, not a body.
 */
export async function askPasskeyQuestion(fetch: Fetch): Promise<string | null> {
  const answer = await fetch(PASSKEY_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: "{}",
    credentials: "same-origin",
  });
  return answer.status === 401 ? answer.headers.get("WWW-Authenticate") : null;
}

/** Signs an administrator in with a device's answer. The cookie is set; the result is the CSRF token. */
export async function signInWithPasskey(
  fetch: Fetch,
  answer: Readonly<{ id: string; clientData: string; authenticatorData: string; signature: string }>,
): Promise<ApiAuth> {
  const csrfToken = await call(
    { fetch, auth: { kind: "cookie", csrfToken: null } },
    {
      method: "POST",
      path: PASSKEY_PATH,
      body: {
        id: answer.id,
        client_data: answer.clientData,
        authenticator_data: answer.authenticatorData,
        signature: answer.signature,
      },
      read: (value) => reading.text(reading.record(value)["csrf_token"]),
    },
  );
  return { kind: "cookie", csrfToken };
}

/** Ends the session on the server, which also removes the cookie. */
export function signOutPanel(fetch: Fetch, auth: ApiAuth): Promise<void> {
  return call({ fetch, auth }, { method: "POST", path: "/api/v1/auth/sign-out", read: () => undefined });
}
