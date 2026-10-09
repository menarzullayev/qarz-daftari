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
    const { channel, amount, usdAmount = 0, usdUnstated = 0 } = state.result;
    // What the message stated: the so'm that are due, the dollars that are due, or both, each by itself.
    const stated = [
      amount > 0 || usdAmount === 0 ? formatMoney(amount, language) : null,
      usdAmount > 0 ? formatMoney(usdAmount, language, "USD") : null,
    ].filter((part) => part !== null);
    return (
      <>
        <p className="notice notice--done" role="status">
          {t(SENT_LABELS[channel] ?? "reminders.sent.other", { amount: stated.join(" · ") })}
        </p>
        {/* Due in dollars and not in the message: whoever sent it must not take the SMS for the whole debt. */}
        {usdUnstated > 0 ? <p className="hint">{t("reminders.sent.usdUnstated", { amount: formatMoney(usdUnstated, language, "USD") })}</p> : null}
      </>
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
