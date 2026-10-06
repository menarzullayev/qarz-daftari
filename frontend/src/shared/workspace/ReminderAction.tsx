import { useI18n } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import { formatMoney } from "../format";
import { useSubmit } from "../hooks";
import { useWorkspace } from "./context";
import { errorText } from "./parts";

const SENT_LABELS: Readonly<Record<string, MessageKey>> = {
  telegram: "reminders.sent.telegram",
  sms: "reminders.sent.sms",
};

/**
 * "Send a reminder" on a customer's page, for a manager or an owner (REQ-025). The server decides
 * whether one goes out and through which channel; what it answers is shown as it is: sent, already sent
 * today, nothing due, reminders off, or no way to reach the customer.
 */
export function ReminderAction({ customerId }: { customerId: string }) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  // The payload is the customer, so a retry after a lost answer resends the same key and the server
  // answers the reminder it already sent instead of sending a second one.
  const { state, submit } = useSubmit((id: string, key) => api.sendReminder(id, key));
  const pending = state.status === "pending";

  if (state.status === "done") {
    return (
      <p className="notice notice--done" role="status">
        {t(SENT_LABELS[state.result.channel] ?? "reminders.sent.other", {
          amount: formatMoney(state.result.amount, language),
        })}
      </p>
    );
  }
  return (
    <>
      {state.status === "error" ? (
        <p className="notice notice--error" role="alert">
          {errorText(state.error, t)}
        </p>
      ) : null}
      <p className="actions">
        <button type="button" className="button" onClick={() => submit(customerId)} disabled={pending}>
          {pending ? t("reminders.send.pending") : t("reminders.send")}
        </button>
      </p>
    </>
  );
}
