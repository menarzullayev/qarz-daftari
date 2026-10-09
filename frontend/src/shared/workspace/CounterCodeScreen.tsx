import { useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { useLoad, useSubmit } from "../hooks";
import { Link } from "../router";
import { useMay, useWorkspace } from "./context";
import { Confirm, errorText, Failure, formatInstant, Loading } from "./parts";
import { StartCode } from "./StartCode";

/**
 * The shop's counter code (REQ-013): one QR code on the counter that any customer scans to ask to be
 * connected. Every member of staff sees whether there is one; a manager or an owner issues it, and
 * replaces it only after confirming that the printed one stops working.
 */
export function CounterCodeScreen() {
  const { api } = useWorkspace();
  const can = useMay();
  const { t, language } = useI18n();
  const mayManage = can("settings.edit");
  const { state, reload } = useLoad((signal) => api.readCounterCode(signal), [api]);
  const rotate = useSubmit((_action: "rotate", key) => api.rotateCounterCode(key));
  const [confirming, setConfirming] = useState(false);

  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return <Failure error={state.error} onRetry={reload} />;
  }

  const issued = rotate.state.status === "done" ? rotate.state.result : null;
  const exists = state.data.exists || issued !== null;
  const pending = rotate.state.status === "pending";
  const failure = rotate.state.status === "error" ? rotate.state.error : null;
  const send = () => {
    setConfirming(false);
    rotate.submit("rotate");
  };

  return (
    <>
      <p className="hint no-print">{t("counter.hint")}</p>
      {issued !== null ? null : (
        <p>
          {!exists
            ? t("counter.none")
            : state.data.since === null
              ? t("counter.exists")
              : t("counter.exists.since", { date: formatInstant(state.data.since, language) })}
        </p>
      )}

      {failure ? (
        <p className="notice notice--error no-print" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}

      {issued === null ? null : issued.start === null ? (
        <p className="notice notice--error" role="alert">
          {t("counter.lost")}
        </p>
      ) : (
        <StartCode start={issued.start} caption={t("counter.caption")} printable />
      )}

      {!mayManage ? (
        <p className="hint">{t("counter.readOnly")}</p>
      ) : confirming ? (
        <Confirm
          question={t("counter.replace.confirm")}
          yes={t("counter.replace.yes")}
          no={t("action.cancel")}
          pending={pending}
          onYes={send}
          onNo={() => setConfirming(false)}
        />
      ) : (
        <p className="actions no-print">
          {/* A first code replaces nothing, so there is nothing to confirm. */}
          <button
            type="button"
            className="button"
            onClick={exists ? () => setConfirming(true) : send}
            disabled={pending}
          >
            {pending ? t("state.saving") : exists ? t("counter.replace") : t("counter.issue")}
          </button>
        </p>
      )}

      <p className="actions no-print">
        <Link to="/customers/waiting" className="button">
          {t("waiting.title")}
        </Link>
      </p>
    </>
  );
}
