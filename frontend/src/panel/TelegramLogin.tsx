import { useEffect, useRef, useState } from "react";

import { useI18n } from "../i18n/I18nProvider";
import type { Language } from "../i18n/types";
import "./messages";

/** Telegram's Login Widget. It is the panel's only third-party script and is added on this screen only. */
export const WIDGET_SRC = "https://telegram.org/js/telegram-widget.js?22";

/**
 * The language Telegram's button is written in. The widget is Telegram's and speaks Telegram's
 * languages: Uzbek for the readers it has nothing closer for.
 */
export const WIDGET_LANGUAGE: Readonly<Record<Language, string>> = {
  uz: "uz",
  "uz-Cyrl": "uz",
  ru: "ru",
  tg: "uz",
  kaa: "uz",
  en: "en",
};

export type LoginWidgetProps = {
  botUsername: string;
  language: Language;
};

/**
 * Where Telegram sends the browser once the person has confirmed: this very page, without its query
 * string and fragment. Telegram's script adds the signed fields as a query string; a fragment would
 * end up in front of them and swallow them.
 */
export function authUrl(location: Pick<Location, "origin" | "pathname"> = window.location): string {
  return location.origin + location.pathname;
}

/**
 * The "Log in with Telegram" button (ADR-017). Telegram's script replaces itself with a frame served by
 * Telegram; the person confirms there, and the script sends the browser to `data-auth-url` with their
 * signed data in the query string (the widget's redirect mode). The page that loads then takes it from
 * there: see `loginReturn.ts`.
 *
 * The widget's other mode, a callback named in `data-onauth`, is not used on purpose: Telegram's script
 * turns that attribute into a function with `eval()`, which the page's Content-Security-Policy would
 * then have to allow for every script (`'unsafe-eval'`).
 */
export function TelegramLogin({ botUsername, language }: LoginWidgetProps) {
  const { t } = useI18n();
  const host = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const container = host.current;
    if (!container) {
      return;
    }
    setFailed(false);
    const script = document.createElement("script");
    script.async = true;
    script.src = WIDGET_SRC;
    script.setAttribute("data-telegram-login", botUsername);
    script.setAttribute("data-size", "large");
    script.setAttribute("data-lang", WIDGET_LANGUAGE[language]);
    script.setAttribute("data-auth-url", authUrl());
    script.addEventListener("error", () => setFailed(true));
    container.append(script);
    return () => container.replaceChildren();
  }, [botUsername, language]);

  // Telegram's script puts a frame in the place of itself, and gives it no title: a screen reader then
  // announces "frame" and nothing more. The frame's content is Telegram's, but the element is in this
  // page, so its name is given here, whenever the script adds or replaces it.
  const title = t("panel.signIn.widget");
  useEffect(() => {
    const container = host.current;
    if (!container) {
      return;
    }
    const name = () => {
      for (const frame of container.querySelectorAll("iframe")) {
        if (frame.title !== title) {
          frame.title = title;
        }
      }
    };
    name();
    const observer = new MutationObserver(name);
    observer.observe(container, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [title]);

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
