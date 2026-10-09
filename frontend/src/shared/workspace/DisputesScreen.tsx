import { useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import { formatMoney } from "../format";
import { useLoad, useSubmit } from "../hooks";
import { currencyOf } from "../money";
import { canManage } from "../navigation";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { useWorkspace } from "./context";
import { Confirm, Empty, Failure, formatInstant, Loading, ReasonForm } from "./parts";

type Panel = { kind: "reverse" | "decline"; id: string } | null;

function OpenDisputes() {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const { state, reload } = useLoad((signal) => api.listDisputes(signal), [api]);
  const [panel, setPanel] = useState<Panel>(null);
  const [done, setDone] = useState<MessageKey | null>(null);

  const settled = (message: MessageKey) => () => {
    setPanel(null);
    setDone(message);
    reload();
  };
  // Reversing the entry is the shop agreeing with the customer: the server closes the dispute with it.
  const reversal = useSubmit((entryId: string, key) =>
    api.reverseEntry(entryId, key).then(settled("disputes.done.reversed")),
  );
  const decline = useSubmit((payload: { id: string; reason: string }, key) =>
    api.declineDispute(payload.id, payload.reason, key).then(settled("disputes.done.declined")),
  );
  const busy = reversal.state.status === "pending" || decline.state.status === "pending";

  const open = (next: Panel) => {
    setPanel(next);
    setDone(null);
    reversal.reset();
    decline.reset();
  };

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.data.length === 0) {
    body = <Empty>{t("disputes.none")}</Empty>;
  } else {
    body = (
      <ul className="rows">
        {state.data.map((dispute) => (
          <li key={dispute.id} className="row">
            <Link to={`/customers/${dispute.customerId}`} className="row__link">
              <span className="row__name">{dispute.customerName}</span>
              <span className="row__amount">{formatMoney(dispute.amount, language, currencyOf(dispute))}</span>
            </Link>
            <p className="row__note">{t("disputes.reason", { reason: dispute.reason })}</p>
            <p className="row__meta">{t("disputes.since", { date: formatInstant(dispute.createdAt, language) })}</p>
            {panel?.id !== dispute.id ? (
              <p className="actions">
                <button
                  type="button"
                  className="button button--small"
                  onClick={() => open({ kind: "reverse", id: dispute.id })}
                  disabled={busy}
                >
                  {t("reversal.action")}
                </button>
                <button
                  type="button"
                  className="button button--small"
                  onClick={() => open({ kind: "decline", id: dispute.id })}
                  disabled={busy}
                >
                  {t("disputes.decline")}
                </button>
              </p>
            ) : panel.kind === "reverse" ? (
              <Confirm
                question={t("reversal.confirm", { amount: formatMoney(dispute.amount, language, currencyOf(dispute)) })}
                yes={t("reversal.confirm.yes")}
                no={t("reversal.confirm.no")}
                pending={busy}
                error={reversal.state.status === "error" ? reversal.state.error : null}
                onYes={() => reversal.submit(dispute.entryId)}
                onNo={() => open(null)}
              />
            ) : (
              <ReasonForm
                id={`decline-${dispute.id}`}
                label={t("disputes.decline.reason")}
                hint={t("disputes.decline.hint")}
                submitLabel={t("disputes.decline.submit")}
                pending={busy}
                error={decline.state.status === "error" ? decline.state.error : null}
                onSubmit={(reason) => decline.submit({ id: dispute.id, reason })}
                onCancel={() => open(null)}
              />
            )}
          </li>
        ))}
      </ul>
    );
  }

  return (
    <>
      <nav className="actions" aria-label={t("nav.disputes")}>
        <Link to="/date-requests" className="button">
          {t("dates.title")}
        </Link>
      </nav>
      <p className="hint">{t("disputes.hint")}</p>
      {done !== null ? (
        <p className="notice notice--done" role="status">
          {t(done)}
        </p>
      ) : null}
      {body}
    </>
  );
}

/**
 * Open disputes of the shop (REQ-017). A manager or an owner ends each one by reversing the entry or by
 * declining with a reason that the customer receives. A seller has no such list: the route is not in
 * their navigation, and this screen calls nothing for them.
 */
export function DisputesScreen() {
  const { role } = useWorkspace();
  return canManage(role) ? <OpenDisputes /> : <NotFoundScreen />;
}
