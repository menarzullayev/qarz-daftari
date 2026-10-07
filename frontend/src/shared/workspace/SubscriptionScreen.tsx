import { lazy, Suspense, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import type { Subscription } from "../api";
import { formatCalendarDay, formatMoney } from "../format";
import { useLoad } from "../hooks";
import { parseIsoDate } from "../promise";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { useWorkspace } from "./context";
import { Failure, Loading } from "./parts";
import { endsSoon, modeOfState, type ShopMode } from "./shopMode";

const STATE_LABELS: Readonly<Record<string, MessageKey>> = {
  trial: "subscription.state.trial",
  active: "subscription.state.active",
  limited: "subscription.state.limited",
  suspended: "subscription.state.suspended",
};

const MODE_TEXT: Readonly<Record<ShopMode, MessageKey>> = {
  limited: "subscription.banner.limited",
  suspended: "subscription.banner.suspended",
};

// Sending a receipt is done now and then, by the owner alone: its code and text are loaded on demand.
const ReceiptSection = lazy(() => import("../receipts/ReceiptSection"));

type Copied = "idle" | "done" | "failed";
const COPY_LABELS: Readonly<Record<Copied, MessageKey>> = {
  idle: "link.copy",
  done: "link.copied",
  failed: "link.copy.failed",
};

/** The card to pay to, with a button that puts its number on the clipboard. */
function Card({ number }: { number: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState<Copied>("idle");
  const copy = () => {
    // The clipboard is absent outside a secure context and may be refused; the text stays selectable.
    new Promise<void>((resolve) => resolve(navigator.clipboard.writeText(number))).then(
      () => setCopied("done"),
      () => setCopied("failed"),
    );
  };
  return (
    <>
      <p className="startcode__text">{number}</p>
      <p className="actions">
        <button type="button" className="button" onClick={copy}>
          {t(COPY_LABELS[copied])}
        </button>
      </p>
      <p className="visually-hidden" role="status">
        {copied === "idle" ? "" : t(COPY_LABELS[copied])}
      </p>
    </>
  );
}

function Details({ subscription }: { subscription: Subscription }) {
  const { t, language } = useI18n();
  const ends = subscription.endsOn === null ? null : parseIsoDate(subscription.endsOn);
  const label = STATE_LABELS[subscription.state];
  const days = subscription.daysLeft;
  return (
    <>
      <dl className="facts">
        <dt>{t("subscription.state")}</dt>
        <dd>{label ? t(label) : subscription.state}</dd>
        {ends ? (
          <>
            <dt>{t("subscription.endsOn")}</dt>
            <dd>
              {formatCalendarDay(ends, language)}
              {days === null || days < 0 ? null : (
                <span className="facts__note">
                  {days === 0 ? t("subscription.lastDay") : t("due.inDays", { count: days })}
                </span>
              )}
            </dd>
          </>
        ) : null}
        <dt>{t("subscription.price")}</dt>
        <dd>{t("subscription.price.value", { amount: formatMoney(subscription.priceUzs, language) })}</dd>
      </dl>

      {subscription.state === "suspended" ? (
        <p className="notice notice--error">{t("subscription.suspended.body")}</p>
      ) : null}

      <section aria-labelledby="subscription-card-title">
        <h2 id="subscription-card-title">{t("subscription.card")}</h2>
        {subscription.cardNumber === null ? (
          <p className="state">{t("subscription.card.none")}</p>
        ) : (
          <Card number={subscription.cardNumber} />
        )}
        {/* The bot takes a receipt too; the section below sends one from here. */}
        <p className="hint">{t("subscription.receipt")}</p>
      </section>

      <Suspense fallback={<Loading />}>
        <ReceiptSection priceUzs={subscription.priceUzs} />
      </Suspense>

      <section aria-labelledby="subscription-limited-title">
        <h2 id="subscription-limited-title">{t("subscription.limited.title")}</h2>
        <p>{t("subscription.limited.body")}</p>
      </section>
    </>
  );
}

/**
 * The shop's subscription for its owner (REQ-053, REQ-054): the state, when the period ends, the price,
 * where to pay, and what the limited mode still allows. Nobody else is shown it or asks the server.
 */
export function SubscriptionScreen() {
  const { api, role } = useWorkspace();
  const isOwner = role === "owner";
  const { state, reload } = useLoad((signal) => (isOwner ? api.readSubscription(signal) : Promise.resolve(null)), [
    api,
    isOwner,
  ]);
  if (!isOwner) {
    return <NotFoundScreen />;
  }
  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return <Failure error={state.error} onRetry={reload} />;
  }
  return state.data === null ? null : <Details subscription={state.data} />;
}

/**
 * On the overview (REQ-057): the owner is told when the period ends within seven days and when the
 * shop is limited or suspended, with the way to the subscription. Other staff may not read the
 * subscription; they get a short line once a refusal of the server has said the shop is limited or
 * suspended. A subscription that cannot be read shows nothing here: its own screen shows the failure.
 */
export function SubscriptionBanner() {
  const { api, role, shopMode = null } = useWorkspace();
  const { t } = useI18n();
  const isOwner = role === "owner";
  const { state } = useLoad((signal) => (isOwner ? api.readSubscription(signal) : Promise.resolve(null)), [
    api,
    isOwner,
  ]);
  const subscription = state.status === "ready" ? state.data : null;
  const mode = subscription ? modeOfState(subscription.state) : shopMode;

  let text: string | null = null;
  if (mode !== null) {
    text = t(MODE_TEXT[mode]);
  } else if (subscription && subscription.daysLeft !== null && endsSoon(subscription.daysLeft)) {
    text =
      subscription.daysLeft <= 0
        ? t("subscription.banner.lastDay")
        : t("subscription.banner.ending", { count: subscription.daysLeft });
  }
  if (text === null) {
    return null;
  }
  return (
    <div className={mode === null ? "notice" : "notice notice--error"} role="note">
      <p>{text}</p>
      {isOwner ? (
        <p>
          <Link to="/subscription" className="button button--small">
            {t("subscription.banner.open")}
          </Link>
        </p>
      ) : null}
    </div>
  );
}
