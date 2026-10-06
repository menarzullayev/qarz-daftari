import { useI18n } from "../../i18n/I18nProvider";
import { useLoad, useSubmit } from "../hooks";
import { useWorkspace } from "./context";
import { errorText, Failure, formatInstant, Loading } from "./parts";
import { StartCode } from "./StartCode";

/**
 * "Connect to Telegram" on a customer's page (REQ-013): whether the customer is connected, and, when
 * not, a personal link to hand over. The link is shown once and kept in this component's state only,
 * so it is gone as soon as the screen is left.
 */
export function LinkSection({ customerId, archived }: { customerId: string; archived: boolean }) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const { state, reload } = useLoad((signal) => api.readLink(customerId, signal), [api, customerId]);
  const issue = useSubmit((id: string, key) => api.createLink(id, key));

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.data.linked) {
    const since = state.data.since === null ? null : formatInstant(state.data.since, language);
    body = (
      <>
        <p>{since === null ? t("link.linked") : t("link.linked.since", { date: since })}</p>
        {state.data.status === "unreachable" ? <p className="row__warning">{t("link.unreachable")}</p> : null}
      </>
    );
  } else {
    const pending = issue.state.status === "pending";
    const issued = issue.state.status === "done" ? issue.state.result : null;
    const create = (
      <button type="button" className="button" onClick={() => issue.submit(customerId)} disabled={pending}>
        {pending ? t("state.saving") : t("link.create")}
      </button>
    );
    body = (
      <>
        <p>{t("link.notLinked")}</p>
        {issue.state.status === "error" ? (
          <p className="notice notice--error" role="alert">
            {errorText(issue.state.error, t)}
          </p>
        ) : null}
        {archived ? (
          <p className="hint">{t("link.archived")}</p>
        ) : issued === null ? (
          <p className="actions">{create}</p>
        ) : issued.start === null ? (
          // A repeated request is answered from the server's record, which no longer holds the code.
          <>
            <p className="notice notice--error" role="alert">
              {t("link.lost")}
            </p>
            <p className="actions">{create}</p>
          </>
        ) : (
          <>
            <StartCode start={issued.start} />
            {issued.expiresAt === null ? null : (
              <p className="row__meta">{t("link.expires", { date: formatInstant(issued.expiresAt, language) })}</p>
            )}
            <p className="actions">
              <button type="button" className="button" onClick={issue.reset}>
                {t("link.hide")}
              </button>
            </p>
          </>
        )}
      </>
    );
  }

  return (
    <section aria-labelledby="link-title">
      <h2 id="link-title">{t("link.title")}</h2>
      {body}
    </section>
  );
}
