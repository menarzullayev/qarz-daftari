import { useEffect, useRef, useState } from "react";

import { useI18n } from "../i18n/I18nProvider";
import type { Language } from "../i18n/types";
import "./messages";

/** Telegram's Login Widget. It is the panel's only third-party script and is added on this screen only. */
export const WIDGET_SRC = "https://telegram.org/js/telegram-widget.js?22";

/** The global function the widget calls with the signed data; it exists only while the widget is shown. */
export const WIDGET_CALLBACK = "qarzDaftariTelegramAuth";

export type LoginWidgetProps = {
  botUsername: string;
  language: Language;
  /** Receives whatever the widget hands over; the caller checks its shape. */
  onAuth: (data: unknown) => void;
};

type WidgetWindow = Window & { [WIDGET_CALLBACK]?: (data: unknown) => void };

/**
 * The "Log in with Telegram" button (ADR-017). Telegram's script replaces itself with a frame served by
 * Telegram; the person confirms there, and the script calls back with their signed data. Nothing about
 * the session is known to this component: it only passes the data on.
 */
export function TelegramLogin({ botUsername, language, onAuth }: LoginWidgetProps) {
  const { t } = useI18n();
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef(onAuth);
  latest.current = onAuth;
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const container = host.current;
    if (!container) {
      return;
    }
    setFailed(false);
    const page: WidgetWindow = window;
    page[WIDGET_CALLBACK] = (data) => latest.current(data);
    const script = document.createElement("script");
    script.async = true;
    script.src = WIDGET_SRC;
    script.setAttribute("data-telegram-login", botUsername);
    script.setAttribute("data-size", "large");
    script.setAttribute("data-lang", language);
    script.setAttribute("data-onauth", `${WIDGET_CALLBACK}(user)`);
    script.addEventListener("error", () => setFailed(true));
    container.append(script);
    return () => {
      Reflect.deleteProperty(page, WIDGET_CALLBACK);
      container.replaceChildren();
    };
  }, [botUsername, language]);

  return (
    <>
      <div className="signin__widget" ref={host} role="group" aria-label={t("panel.signIn.widget")} />
      {failed ? (
        <p className="notice notice--error" role="alert">
          {t("panel.signIn.widgetFailed")}
        </p>
      ) : null}
    </>
  );
}
