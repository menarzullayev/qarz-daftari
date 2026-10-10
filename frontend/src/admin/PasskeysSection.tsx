import { type FormEvent, useRef, useState } from "react";

import { useI18n } from "../i18n/I18nProvider";
import { type ApiError, newIdempotencyKey, toApiError } from "../shared/api";
import { useLoad } from "../shared/hooks";
import { errorText, Failure, formatInstant, Loading } from "../shared/workspace/parts";
import type { AdminApi } from "./adminApi";
import { browserPasskeys, PasskeyDeclined, type PasskeyDevice } from "../shared/passkey";

type Step =
  | { status: "idle" }
  | { status: "pending" }
  | { status: "declined" }
  | { status: "error"; error: ApiError };

/**
 * The administrator's own passkeys: the devices that can sign them in with a fingerprint or a face
 * instead of Telegram. Adding one asks this device to make a key for this site; the server keeps its
 * public half. Removing one ends it at once.
 */
export function PasskeysSection({ api, device = browserPasskeys }: { api: AdminApi; device?: PasskeyDevice }) {
  const { t, language } = useI18n();
  const { state, reload } = useLoad((signal) => api.listPasskeys(signal), [api]);
  const [label, setLabel] = useState("");
  const [step, setStep] = useState<Step>({ status: "idle" });
  const busy = useRef(false);

  const run = (work: () => Promise<unknown>, after: () => void) => {
    if (busy.current) {
      return;
    }
    busy.current = true;
    setStep({ status: "pending" });
    work().then(
      () => {
        busy.current = false;
        setStep({ status: "idle" });
        after();
        reload();
      },
      (error: unknown) => {
        busy.current = false;
        setStep(error instanceof PasskeyDeclined ? { status: "declined" } : { status: "error", error: toApiError(error) });
      },
    );
  };

  const onAdd = (event: FormEvent) => {
    event.preventDefault();
    const name = label.trim();
    if (name === "") {
      return;
    }
    run(
      async () => api.addPasskey(name, await device.create(await api.passkeyChallenge()), newIdempotencyKey()),
      () => setLabel(""),
    );
  };
  const pending = step.status === "pending";

  return (
    <section aria-labelledby="passkeys-title">
      <h2 id="passkeys-title">{t("passkeys.title")}</h2>
      <p className="hint">{t("passkeys.body")}</p>
      {state.status === "loading" ? <Loading /> : null}
      {state.status === "error" ? <Failure error={state.error} onRetry={reload} /> : null}
      {state.status === "ready" && state.data.length === 0 ? <p>{t("passkeys.none")}</p> : null}
      {state.status === "ready" && state.data.length > 0 ? (
        <ul className="rows" aria-label={t("passkeys.title")}>
          {state.data.map((one) => (
            <li key={one.id} className="row">
              <span>
                <strong>{one.label}</strong>{" "}
                <span className="hint">
                  {one.lastUsedAt === null
                    ? t("passkeys.neverUsed")
                    : t("passkeys.lastUsed", { at: formatInstant(one.lastUsedAt, language) })}
                </span>
              </span>
              <button
                type="button"
                className="button"
                disabled={pending}
                onClick={() => run(() => api.removePasskey(one.id, newIdempotencyKey()), () => undefined)}
              >
                {t("passkeys.remove")}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {step.status === "declined" ? (
        <p className="notice" role="status">
          {t("passkeys.declined")}
        </p>
      ) : null}
      {step.status === "error" ? (
        <p className="notice notice--error" role="alert">
          {errorText(step.error, t)}
        </p>
      ) : null}
      {device.available() ? (
        <form className="form" onSubmit={onAdd}>
          <div className="field">
            <label htmlFor="passkey-label">{t("passkeys.label")}</label>
            <input
              id="passkey-label"
              className="input"
              maxLength={60}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
            />
          </div>
          <p className="actions">
            <button type="submit" className="button" disabled={pending || label.trim() === ""}>
              {t("passkeys.add")}
            </button>
          </p>
        </form>
      ) : (
        <p className="notice">{t("passkeys.unsupported")}</p>
      )}
    </section>
  );
}
