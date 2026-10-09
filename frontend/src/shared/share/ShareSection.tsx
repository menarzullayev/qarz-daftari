import { useEffect, useMemo, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import { useLoad, useSubmit } from "../hooks";
import { useWorkspace } from "../workspace/context";
import { Confirm, errorText, formatInstant } from "../workspace/parts";
import { QrCode } from "../workspace/StartCode";
import "./messages";
import { sharesOf, type ShareState, shareUrl } from "./shareApi";

/** On the page's root element while one link is being printed: the print style sheet hides the rest. */
export const PRINT_SOLO = "print-solo-active";

/** Prints the link alone, not the customer's whole page around it. */
function printAlone(): void {
  const root = document.documentElement;
  const done = () => {
    root.classList.remove(PRINT_SOLO);
    window.removeEventListener("afterprint", done);
  };
  root.classList.add(PRINT_SOLO);
  window.addEventListener("afterprint", done);
  window.print();
}

type Copied = "idle" | "done" | "failed";
const COPY_LABELS: Readonly<Record<Copied, MessageKey>> = {
  idle: "link.copy",
  done: "link.copied",
  failed: "link.copy.failed",
};

/**
 * A link that the server has just made, as the customer needs it: the address as text with a copy
 * button, and as a QR code to print. The address is a credential and stays in the caller's state: this
 * component puts it on the screen and, when asked, on the clipboard or on paper, and nowhere else.
 */
function ShareLink({ url }: { url: string }) {
  const { shopName } = useWorkspace();
  const { t } = useI18n();
  const [copied, setCopied] = useState<Copied>("idle");

  const copy = () => {
    // The clipboard is absent outside a secure context and may be refused; the text stays selectable.
    new Promise<void>((resolve) => resolve(navigator.clipboard.writeText(url))).then(
      () => setCopied("done"),
      () => setCopied("failed"),
    );
  };

  return (
    <div className="startcode print-solo">
      <p className="notice no-print">{t("share.once")}</p>
      {shopName ? <p className="startcode__caption">{shopName}</p> : null}
      <p className="startcode__caption">{t("share.caption")}</p>
      <QrCode text={url} />
      <p className="startcode__text">{url}</p>
      <p className="actions no-print">
        <button type="button" className="button" onClick={copy}>
          {t(COPY_LABELS[copied])}
        </button>
        <button type="button" className="button" onClick={printAlone}>
          {t("link.print")}
        </button>
      </p>
      <p className="visually-hidden" role="status">
        {copied === "idle" ? "" : t(COPY_LABELS[copied])}
      </p>
    </div>
  );
}

/**
 * "A link for the customer" on a customer's page, for a manager or an owner: whether the customer has a
 * read-only link, and making, replacing and ending it. The server keeps only a hash of the link, so the
 * link is on the screen once, right after it is made, and is gone when the screen is left.
 *
 * The whole feature is behind a platform switch. Until the server has said that it is on, and whenever
 * it says it is not, this renders nothing at all: no heading, no placeholder.
 */
export default function ShareSection({
  customerId,
  archived,
  origin = window.location.origin,
}: {
  customerId: string;
  archived: boolean;
  /** Where the page behind the link is served; the address this page itself was opened at. */
  origin?: string;
}) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const shares = useMemo(() => sharesOf(api), [api]);
  const { state, reload } = useLoad((signal) => shares.read(customerId, signal), [shares, customerId]);
  const issue = useSubmit((id: string, key) => shares.create(id, key));
  const [asking, setAsking] = useState<"replace" | "revoke" | null>(null);
  const [revoked, setRevoked] = useState(false);
  const revoke = useSubmit((id: string, key) =>
    shares.revoke(id, key).then(() => {
      setAsking(null);
      setRevoked(true);
      reload();
    }),
  );

  // Nothing is shown until the server has answered in this feature's own words: not while it is asked,
  // not when it says there is no such route (the switch is off), and not for any other failure. The
  // section is an addition to a page that works without it. Once it has answered, what it said stays on
  // the screen while it is asked again, so the section does not blink after every change.
  const [known, setKnown] = useState<{ customerId: string; data: ShareState } | null>(null);
  useEffect(() => {
    if (state.status === "ready") {
      setKnown({ customerId, data: state.data });
    }
  }, [state, customerId]);
  const data = state.status === "ready" ? state.data : known?.customerId === customerId ? known.data : null;
  if (data === null) {
    return null;
  }

  const create = () => {
    setAsking(null);
    setRevoked(false);
    issue.submit(customerId);
  };
  const creating = issue.state.status === "pending";
  const issued = issue.state.status === "done" ? issue.state.result : null;
  const createButton = (label: MessageKey) => (
    <button type="button" className="button" onClick={create} disabled={creating}>
      {creating ? t("state.saving") : t(label)}
    </button>
  );

  let body;
  if (issued !== null) {
    body =
      issued.token === null ? (
        // A repeated request is answered from the server's record, which no longer holds the link.
        <>
          <p className="notice notice--error" role="alert">
            {t("share.lost")}
          </p>
          <p className="actions">{createButton("share.replace")}</p>
        </>
      ) : (
        <>
          <ShareLink url={shareUrl(origin, issued.token)} />
          <p className="row__meta">{t("share.active", { date: formatInstant(issued.expiresAt, language) })}</p>
          <p className="actions no-print">
            <button
              type="button"
              className="button"
              onClick={() => {
                issue.reset();
                reload();
              }}
            >
              {t("share.hide")}
            </button>
          </p>
        </>
      );
  } else {
    const { exists, expired, expiresAt, lastOpenedAt } = data;
    const until = expiresAt === null ? "" : formatInstant(expiresAt, language);
    const alive = exists && !expired;
    body = (
      <>
        {revoked && !exists ? (
          <p className="notice notice--done" role="status">
            {t("share.revoked")}
          </p>
        ) : null}
        {!exists ? <p>{t("share.none")}</p> : null}
        {alive ? <p>{t("share.active", { date: until })}</p> : null}
        {exists && expired ? <p className="row__warning">{t("share.expired", { date: until })}</p> : null}
        {exists ? (
          <p className="row__meta">
            {lastOpenedAt === null
              ? t("share.neverOpened")
              : t("share.opened", { date: formatInstant(lastOpenedAt, language) })}
          </p>
        ) : null}
        {issue.state.status === "error" ? (
          <p className="notice notice--error" role="alert">
            {errorText(issue.state.error, t)}
          </p>
        ) : null}
        {asking === "replace" ? (
          <Confirm
            question={t("share.replace.question")}
            yes={t("share.replace.yes")}
            no={t("action.cancel")}
            pending={creating}
            onYes={create}
            onNo={() => setAsking(null)}
          />
        ) : null}
        {asking === "revoke" ? (
          <Confirm
            question={t("share.revoke.question")}
            yes={t("share.revoke.yes")}
            no={t("action.cancel")}
            pending={revoke.state.status === "pending"}
            error={revoke.state.status === "error" ? revoke.state.error : null}
            onYes={() => revoke.submit(customerId)}
            onNo={() => {
              setAsking(null);
              revoke.reset();
            }}
          />
        ) : null}
        {asking !== null ? null : archived && !alive ? (
          <p className="hint">{t("share.archived")}</p>
        ) : (
          <p className="actions">
            {archived ? null : alive ? (
              <button type="button" className="button" onClick={() => setAsking("replace")} disabled={creating}>
                {t("share.replace")}
              </button>
            ) : (
              createButton("share.create")
            )}
            {alive ? (
              <button type="button" className="button button--danger" onClick={() => setAsking("revoke")}>
                {t("share.revoke")}
              </button>
            ) : null}
          </p>
        )}
      </>
    );
  }

  return (
    <section aria-labelledby="share-title">
      <h2 id="share-title" className="no-print">
        {t("share.title")}
      </h2>
      <p className="hint no-print">{t("share.explain")}</p>
      {body}
    </section>
  );
}
