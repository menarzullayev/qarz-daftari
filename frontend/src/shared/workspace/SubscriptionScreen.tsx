import { lazy, Suspense, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import { groupedCard, type Subscription } from "../api";
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
  free: "subscription.state.free",
};

/** What follows the running period if it is not paid, in words; nothing when the server did not say. */
const THEN_TEXT: Readonly<Record<string, MessageKey>> = {
  free: "subscription.plan.then.free",
  limited: "subscription.plan.then.limited",
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

/**
 * A button that puts a card's number on the clipboard: the digits alone, as a bank's form takes them.
 * `label` names the card for a reader that cannot see which number the button is beside.
 */
function CopyNumber({ number, label, small = false }: { number: string; label: string; small?: boolean }) {
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
      <button type="button" className={small ? "button button--small" : "button"} aria-label={`${t(COPY_LABELS[copied])}: ${label}`} onClick={copy}>
        {t(COPY_LABELS[copied])}
      </button>
      <span className="visually-hidden" role="status">
        {copied === "idle" ? "" : t(COPY_LABELS[copied])}
      </span>
    </>
  );
}

/**
 * Where to pay: the card being paid to, in full, with its name; the primary one until the owner chooses
 * another. The rest are behind "other cards", each with its number to copy and a button that makes it
 * the card being paid to. That card goes with the receipt sent from this page.
 */
function Cards({ subscription, chosen, onChoose }: { subscription: Subscription; chosen: number; onChoose: (place: number) => void }) {
  const { t } = useI18n();
  const { cards } = subscription;
  const card = cards[chosen];
  if (card === undefined) {
    return <p className="state">{t("subscription.card.none")}</p>;
  }
  return (
    <>
      <p className="row__name">
        {card.label}
        {chosen === 0 && cards.length > 1 ? <span className="facts__note">{t("subscription.card.primary")}</span> : null}
      </p>
      <p className="startcode__text">{groupedCard(card.number)}</p>
      <p className="actions">
        {/* Keyed by the card: a "copied" said of one card is not said of the next. */}
        <CopyNumber key={card.number} number={card.number} label={card.label} />
      </p>
      {cards.length > 1 ? (
        <details className="more">
          <summary>{t("subscription.card.others", { count: cards.length - 1 })}</summary>
          <ul className="rows" aria-label={t("subscription.card.others", { count: cards.length - 1 })}>
            {cards.map((other, place) =>
              place === chosen ? null : (
                <li key={other.number} className="row">
                  <p className="row__link">
                    <span className="row__name">{other.label}</span>
                    <span className="row__amount">{groupedCard(other.number)}</span>
                  </p>
                  <p className="actions">
                    <CopyNumber number={other.number} label={other.label} small />
                    <button type="button" className="button button--small" aria-label={`${t("subscription.card.choose")}: ${other.label}`} onClick={() => onChoose(place)}>
                      {t("subscription.card.choose")}
                    </button>
                  </p>
                </li>
              ),
            )}
          </ul>
        </details>
      ) : null}
    </>
  );
}

function Details({ subscription }: { subscription: Subscription }) {
  const { t, language } = useI18n();
  const ends = subscription.endsOn === null ? null : parseIsoDate(subscription.endsOn);
  const label = STATE_LABELS[subscription.state];
  const days = subscription.daysLeft;
  // The place, in the list, of the card being paid to. The primary one until another is chosen.
  const [chosen, setChosen] = useState(0);
  // The free plan, while the platform has it on. A suspended shop is told about its suspension alone.
  const plan = subscription.state === "suspended" ? null : subscription.plan;
  const then = plan === null || plan.afterPeriod === null ? undefined : THEN_TEXT[plan.afterPeriod];
  // Limited mode is not what awaits a shop the free plan holds: saying so would be untrue.
  const mayBeLimited = plan === null || (subscription.state !== "free" && plan.afterPeriod !== "free");
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
        {plan ? (
          <>
            <dt>{t("subscription.plan.customers")}</dt>
            <dd>{t("subscription.plan.customers.value", { used: plan.customers, limit: plan.freeCustomers })}</dd>
          </>
        ) : null}
        {plan?.sms.included ? (
          <>
            <dt>{t("subscription.plan.sms")}</dt>
            <dd>{t("subscription.plan.sms.value", { left: plan.sms.left, quota: plan.sms.quota })}</dd>
          </>
        ) : null}
        <dt>{t("subscription.price")}</dt>
        <dd>{t("subscription.price.value", { amount: formatMoney(subscription.priceUzs, language) })}</dd>
      </dl>

      {then ? <p className="hint">{t(then)}</p> : null}
      {plan && subscription.state !== "active" ? (
        <p className="hint">
          {plan.sms.offered ? t("subscription.plan.adds.sms", { quota: plan.sms.quota }) : t("subscription.plan.adds")}
        </p>
      ) : null}

      {subscription.state === "suspended" ? (
        <p className="notice notice--error">{t("subscription.suspended.body")}</p>
      ) : null}

      <section aria-labelledby="subscription-card-title">
        <h2 id="subscription-card-title">{t("subscription.card")}</h2>
        <Cards subscription={subscription} chosen={chosen} onChoose={setChosen} />
        {/* The bot takes a receipt too; the section below sends one from here. */}
        <p className="hint">{t("subscription.receipt")}</p>
      </section>

      <Suspense fallback={<Loading />}>
        <ReceiptSection priceUzs={subscription.priceUzs} card={subscription.cards[chosen] ?? null} />
      </Suspense>

      {mayBeLimited ? (
        <section aria-labelledby="subscription-limited-title">
          <h2 id="subscription-limited-title">{t("subscription.limited.title")}</h2>
          <p>{t("subscription.limited.body")}</p>
        </section>
      ) : null}
    </>
  );
}

/**
 * The shop's subscription for its owner (REQ-053, REQ-054): the state, when the period ends, the price,
 * where to pay, and what the limited mode still allows. While the free plan is on (BR-33 to BR-35): the
 * plan, the customers used of those it holds, what follows the period, and what paying adds. Nobody else
 * is shown it or asks the server.
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
    // With the free plan on, what follows the period is said too: free for a shop it holds, else limited.
    const then = THEN_TEXT[subscription.plan?.afterPeriod ?? ""];
    if (then) {
      text = `${text} ${t(then)}`;
    }
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
