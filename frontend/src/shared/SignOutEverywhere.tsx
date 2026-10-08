import { useRef, useState } from "react";

import { useI18n } from "../i18n/I18nProvider";
import { type ApiError, toApiError } from "./api";
import { errorText } from "./workspace/parts";

type Step = "idle" | "asking" | "pending";

/**
 * "Sign out everywhere" (security review, finding 9): ends every session of the person, on every
 * device, Mini App and web panel alike, and this one with them. It is asked twice: the button only
 * opens the question, and nothing is sent until the answer is yes.
 *
 * A session the server no longer knows answers 401, which the transport already treats as signed out,
 * so that case needs nothing here.
 */
export function SignOutEverywhere({ signOut, onDone }: { signOut: () => Promise<void>; onDone: () => void }) {
  const { t } = useI18n();
  const [step, setStep] = useState<Step>("idle");
  const [error, setError] = useState<ApiError | null>(null);
  const busy = useRef(false);

  const confirm = () => {
    if (busy.current) {
      return;
    }
    busy.current = true;
    setStep("pending");
    setError(null);
    signOut().then(onDone, (failure: unknown) => {
      const refusal = toApiError(failure);
      busy.current = false;
      if (refusal.status !== 401) {
        setStep("asking");
        setError(refusal);
      }
    });
  };

  if (step === "idle") {
    return (
      <p className="actions">
        <button type="button" className="button" onClick={() => setStep("asking")}>
          {t("session.everywhere.action")}
        </button>
      </p>
    );
  }
  const pending = step === "pending";
  return (
    <div className="notice">
      <p>{t("session.everywhere.confirm")}</p>
      <p className="actions">
        <button type="button" className="button button--primary" onClick={confirm} disabled={pending}>
          {pending ? t("session.everywhere.pending") : t("session.everywhere.yes")}
        </button>
        <button
          type="button"
          className="button"
          disabled={pending}
          onClick={() => {
            setStep("idle");
            setError(null);
          }}
        >
          {t("action.cancel")}
        </button>
      </p>
      {error ? (
        <p className="field__error" role="alert">
          {errorText(error, t)}
        </p>
      ) : null}
    </div>
  );
}
