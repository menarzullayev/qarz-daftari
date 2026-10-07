import { useEffect, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import type { QrDrawing } from "../qr";
import { deepLink } from "../settings";
import { useWorkspace } from "./context";

// Always dark on white, whatever the theme: a scanner needs the contrast, and so does a printer.
const PAPER = "#ffffff";
const INK = "#000000";
const CRISP = "crispEdges";

/** The link as a QR code. The encoder is fetched when a code is first shown, not on first load. */
export function QrCode({ text }: { text: string }) {
  const { t } = useI18n();
  const [drawing, setDrawing] = useState<QrDrawing | "failed" | null>(null);

  useEffect(() => {
    let wanted = true;
    setDrawing(null);
    import("../qr").then(
      ({ drawQr }) => {
        if (wanted) {
          setDrawing(drawQr(text));
        }
      },
      () => {
        if (wanted) {
          setDrawing("failed");
        }
      },
    );
    return () => {
      wanted = false;
    };
  }, [text]);

  if (drawing === "failed") {
    return <p className="field__error">{t("link.qr.failed")}</p>;
  }
  if (drawing === null) {
    return (
      <p className="state" role="status">
        {t("state.loading")}
      </p>
    );
  }
  return (
    <svg
      className="qr"
      role="img"
      aria-label={t("link.qr")}
      viewBox={`0 0 ${drawing.side} ${drawing.side}`}
      shapeRendering={CRISP}
    >
      <rect width={drawing.side} height={drawing.side} fill={PAPER} />
      <path d={drawing.path} fill={INK} />
    </svg>
  );
}

type Copied = "idle" | "done" | "failed";
const COPY_LABELS: Readonly<Record<Copied, MessageKey>> = {
  idle: "link.copy",
  done: "link.copied",
  failed: "link.copy.failed",
};

/**
 * A start code that the server has just issued, as the customer needs it: the bot's deep link as text
 * with a copy button and as a QR code. When the build does not name the bot, the code itself with the
 * command to send. The code is a credential and stays in the caller's state: this component puts it on
 * the screen and, when asked, on the clipboard, and nowhere else.
 */
export function StartCode({
  start,
  caption,
  printable = false,
  noBotHint,
}: {
  start: string;
  caption?: string;
  printable?: boolean;
  /** Who sends the command when the build names no bot; by default the customer. */
  noBotHint?: string;
}) {
  const { botUsername } = useWorkspace();
  const { t } = useI18n();
  const [copied, setCopied] = useState<Copied>("idle");
  const link = botUsername === null ? null : deepLink(botUsername, start);
  const shown = link ?? t("link.command", { code: start });

  const copy = () => {
    // The clipboard is absent outside a secure context and may be refused; the text stays selectable.
    new Promise<void>((resolve) => resolve(navigator.clipboard.writeText(shown))).then(
      () => setCopied("done"),
      () => setCopied("failed"),
    );
  };

  return (
    <div className="startcode">
      <p className="notice no-print">{t("link.once")}</p>
      {caption ? <p className="startcode__caption">{caption}</p> : null}
      {link === null ? <p className="hint">{noBotHint ?? t("link.noBot")}</p> : null}
      <p className="startcode__text">{shown}</p>
      {link === null ? null : <QrCode text={link} />}
      <p className="actions no-print">
        <button type="button" className="button" onClick={copy}>
          {t(COPY_LABELS[copied])}
        </button>
        {printable && link !== null ? (
          <button type="button" className="button" onClick={() => window.print()}>
            {t("link.print")}
          </button>
        ) : null}
      </p>
      <p className="visually-hidden" role="status">
        {copied === "idle" ? "" : t(COPY_LABELS[copied])}
      </p>
    </div>
  );
}
