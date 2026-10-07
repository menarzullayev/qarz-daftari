import { useState } from "react";

import { useI18n } from "../i18n/I18nProvider";
import { useSubmit } from "../shared/hooks";
import { Link } from "../shared/router";
import { isOpen } from "../shared/support/supportApi";
import { useWorkspace } from "../shared/workspace/context";
import { Confirm, formatInstant } from "../shared/workspace/parts";
import type { Transfer } from "./backoffice";
import { erasureDay } from "./DeletionSection";
import "./messages";
import { useOffice } from "./office";

type Answer = "accept" | "decline";

/** The owner's offer of the shop, as the manager it is addressed to sees it (REQ-036). */
function Offer({ offer }: { offer: Transfer }) {
  const { reloadSession } = useWorkspace();
  const { office, reloadTransfer } = useOffice();
  const { t, language } = useI18n();
  const [asking, setAsking] = useState<Answer | null>(null);
  const answer = useSubmit((chosen: Answer, key) =>
    office.answerTransfer(chosen, key).then(() => {
      setAsking(null);
      // Accepting makes this person the owner: their shops and roles are read again.
      if (chosen === "accept" && reloadSession) {
        reloadSession();
      } else {
        reloadTransfer();
      }
    }),
  );

  return (
    <div className="notice" role="region" aria-label={t("ownership.title")}>
      <p>{t("ownership.offered", { date: formatInstant(offer.expiresAt, language) })}</p>
      {asking ? (
        <Confirm
          question={t(asking === "accept" ? "ownership.accept.confirm" : "ownership.decline.confirm")}
          yes={t(asking === "accept" ? "ownership.accept.yes" : "ownership.decline.yes")}
          no={t("confirm.no")}
          pending={answer.state.status === "pending"}
          error={answer.state.status === "error" ? answer.state.error : null}
          onYes={() => answer.submit(asking)}
          onNo={() => {
            setAsking(null);
            answer.reset();
          }}
        />
      ) : (
        <p className="actions">
          <button type="button" className="button button--primary" onClick={() => setAsking("accept")}>
            {t("ownership.accept")}
          </button>
          <button type="button" className="button" onClick={() => setAsking("decline")}>
            {t("ownership.decline")}
          </button>
        </p>
      )}
    </div>
  );
}

/** For the owner, while an administrator can read the shop: that it is so, why, and until when (REQ-059). */
function SupportNotice() {
  const { role, now } = useWorkspace();
  const { support } = useOffice();
  const { t, language } = useI18n();
  if (role !== "owner" || support.status !== "ready") {
    return null;
  }
  // The clock is asked again on every render: an access whose time has run out is no longer announced.
  return support.data
    .filter((access) => isOpen(access, now()))
    .map((access) => (
      <p key={access.id} className="notice notice--error" role="status">
        <span>{t("support.banner", { date: formatInstant(access.endsAt, language) })}</span>{" "}
        <span>{t("support.banner.reason", { reason: access.reason })}</span>{" "}
        <Link to="/shop-settings">{t("support.banner.open")}</Link>
      </p>
    ));
}

/**
 * Above every screen: for the owner, that an administrator can read the shop now, and that the shop is
 * waiting to be deleted and on which day; for a manager, an ownership offer addressed to them. Anyone
 * else sees nothing.
 */
export function OfficeBanner() {
  return (
    <>
      <SupportNotice />
      <OtherNotice />
    </>
  );
}

function OtherNotice() {
  const { role, membershipId } = useWorkspace();
  const { deletion, transfer } = useOffice();
  const { t, language } = useI18n();

  if (role === "owner" && deletion.status === "ready" && deletion.data?.status === "deletion_pending") {
    const day = erasureDay(deletion.data, language);
    return (
      <p className="notice notice--error" role="status">
        <span>{day === null ? t("deletion.pending.noDate") : t("deletion.pending", { date: day })}</span>{" "}
        <Link to="/shop-settings">{t("deletion.banner.open")}</Link>
      </p>
    );
  }
  // Only the manager the shop was offered to has an answer to give; the offer names their membership.
  if (
    membershipId !== null &&
    transfer.status === "ready" &&
    transfer.data !== null &&
    transfer.data.toMembership === membershipId
  ) {
    return <Offer offer={transfer.data} />;
  }
  return null;
}
