/**
 * An administrator's passkey in the browser (WebAuthn; backend/src/qarz/application/admin_passkeys.py).
 *
 * The device keeps the private key and signs only after it has verified its holder (a fingerprint, a
 * face, a PIN) and only for this site. The page passes bytes between the server and the device and
 * keeps nothing: no key, no challenge, no answer outlives the call.
 */

/** What the server says a new credential must be made with (`GET /api/admin/v1/passkeys/challenge`). */
export type PasskeyStart = Readonly<{
  challenge: string;
  rpId: string;
  rpName: string;
  userId: string;
  userName: string;
  algorithms: readonly number[];
  exclude: readonly string[];
  timeout: number;
}>;

/** What a device made, as the server takes it (`POST /api/admin/v1/passkeys`, without the label). */
export type NewCredential = Readonly<{
  clientData: string;
  authenticatorData: string;
  publicKey: string;
  algorithm: number;
}>;

/** What a sign-in must answer: read from the refusal's `WWW-Authenticate`. */
export type PasskeyQuestion = Readonly<{ challenge: string; rpId: string; timeout: number }>;

/** A device's answer to a sign-in question (`POST /api/v1/auth/admin-passkey`). */
export type PasskeyAnswer = Readonly<{ id: string; clientData: string; authenticatorData: string; signature: string }>;

/** The device as the screens use it; a test passes its own. */
export type PasskeyDevice = Readonly<{
  available: () => boolean;
  create: (start: PasskeyStart) => Promise<NewCredential>;
  answer: (question: PasskeyQuestion) => Promise<PasskeyAnswer>;
}>;

/** The device said no, or the person closed its window: nothing went wrong that the page can name. */
export class PasskeyDeclined extends Error {}

export function toBase64Url(bytes: ArrayBuffer): string {
  let text = "";
  for (const byte of new Uint8Array(bytes)) {
    text += String.fromCharCode(byte);
  }
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function fromBase64Url(text: string): ArrayBuffer {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  const raw = atob(padded);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) {
    bytes[index] = raw.charCodeAt(index);
  }
  return bytes.buffer;
}

/** Reads `QD-Passkey challenge="…", rp_id="…", timeout=…`; null when the header is not that. */
export function readQuestion(header: string | null): PasskeyQuestion | null {
  if (header === null || !header.startsWith("QD-Passkey ")) {
    return null;
  }
  const challenge = /challenge="([A-Za-z0-9_-]+)"/.exec(header)?.[1];
  const rpId = /rp_id="([a-z0-9.-]+)"/.exec(header)?.[1];
  const timeout = Number(/timeout=(\d+)/.exec(header)?.[1] ?? "120000");
  return challenge && rpId ? { challenge, rpId, timeout } : null;
}

async function asked<T>(ask: () => Promise<T | null>): Promise<T> {
  let got: T | null;
  try {
    got = await ask();
  } catch (error) {
    throw new PasskeyDeclined(error instanceof Error ? error.name : "declined");
  }
  if (got === null) {
    throw new PasskeyDeclined("nothing");
  }
  return got;
}

/** The browser's own device. */
export const browserPasskeys: PasskeyDevice = {
  available: () => typeof window !== "undefined" && typeof window.PublicKeyCredential === "function",

  async create(start) {
    const made = await asked(() =>
      navigator.credentials.create({
        publicKey: {
          challenge: fromBase64Url(start.challenge),
          rp: { id: start.rpId, name: start.rpName },
          user: { id: fromBase64Url(start.userId), name: start.userName, displayName: start.userName },
          pubKeyCredParams: start.algorithms.map((alg) => ({ type: "public-key", alg })),
          excludeCredentials: start.exclude.map((id) => ({ type: "public-key", id: fromBase64Url(id) })),
          authenticatorSelection: { residentKey: "required", userVerification: "required" },
          attestation: "none",
          timeout: start.timeout,
        },
      }),
    );
    const response = (made as PublicKeyCredential).response as AuthenticatorAttestationResponse;
    const publicKey = response.getPublicKey();
    if (publicKey === null) {
      // An algorithm the browser cannot hand the key of: the server could not use it either.
      throw new PasskeyDeclined("no public key");
    }
    return {
      clientData: toBase64Url(response.clientDataJSON),
      authenticatorData: toBase64Url(response.getAuthenticatorData()),
      publicKey: toBase64Url(publicKey),
      algorithm: response.getPublicKeyAlgorithm(),
    };
  },

  async answer(question) {
    const got = await asked(() =>
      navigator.credentials.get({
        publicKey: {
          challenge: fromBase64Url(question.challenge),
          rpId: question.rpId,
          userVerification: "required",
          timeout: question.timeout,
        },
      }),
    );
    const credential = got as PublicKeyCredential;
    const response = credential.response as AuthenticatorAssertionResponse;
    return {
      id: toBase64Url(credential.rawId),
      clientData: toBase64Url(response.clientDataJSON),
      authenticatorData: toBase64Url(response.authenticatorData),
      signature: toBase64Url(response.signature),
    };
  },
};
