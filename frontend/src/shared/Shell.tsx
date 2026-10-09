import { useEffect, useRef, type ReactNode } from "react";

import { useI18n } from "../i18n/I18nProvider";
import { LANGUAGES, type Language, type MessageKey } from "../i18n/types";
import { BrandMark } from "./BrandMark";
import { NavIcon } from "./icons";
import { Link } from "./router";
import { ThemeToggle } from "./ThemeToggle";
import "./tokens.css";
import "./shell.css";

export type ShellNavItem = {
  id: string;
  path: string;
  labelKey: MessageKey;
};

type ShellProps = {
  /** Which client this is ("entry.app", "entry.panel", "entry.admin"); shown next to the product name. */
  entryKey?: MessageKey | undefined;
  /**
   * The context every screen names: the active shop for staff (REQ-064). `label` is read to screen
   * reader users before the value. Omitted by the admin panel, which has no active shop.
   */
  context?: { label: string; value: string };
  items: readonly ShellNavItem[];
  /** Items from this index on are hidden on a phone and reached through `moreItem`. */
  primaryCount: number;
  moreItem?: ShellNavItem;
  currentPath: string;
  /**
   * Path of the navigation item to mark as current, when the screen sits inside a section (one customer
   * inside "Customers"). Defaults to `currentPath`.
   */
  navPath?: string;
  /** Title of the current screen; becomes the page heading and the document title. */
  title: string;
  /** Controls under the navigation list of a wide screen (the web panel's shop switcher and sign-out). */
  side?: ReactNode;
  /** Shown between the page heading and the screen. */
  banner?: ReactNode;
  children?: ReactNode;
};

/** One shell to a page, so one picker: a fixed name keeps the markup the same from one render to the next. */
const PICKER_ID = "qd-language";

const isLanguage = (code: string): code is Language => LANGUAGES.some((language) => language === code);

/**
 * The language picker: a native list, so that six languages take the room of one, the phone shows its
 * own chooser, and a keyboard or a screen reader needs nothing explained. Each language is named in
 * itself and marked with its own `lang`, so that it is read aloud as that language.
 */
function LanguagePicker() {
  const { language, setLanguage, t } = useI18n();
  return (
    <div className="language">
      <label className="visually-hidden" htmlFor={PICKER_ID}>
        {t("shell.language")}
      </label>
      <select
        id={PICKER_ID}
        className="input language__picker"
        value={language}
        onChange={(event) => {
          if (isLanguage(event.target.value)) {
            setLanguage(event.target.value);
          }
        }}
      >
        {LANGUAGES.map((code) => (
          <option key={code} value={code} lang={code}>
            {t(`lang.${code}`)}
          </option>
        ))}
      </select>
    </div>
  );
}

/**
 * Frame shared by the three entry points (ADR-011): header, navigation, and the main region. One markup
 * serves every width; the style sheet turns the navigation into a bottom tab bar on a phone and a side
 * list from 720px up.
 */
export function Shell({
  entryKey,
  context,
  items,
  primaryCount,
  moreItem,
  currentPath,
  navPath = currentPath,
  title,
  side,
  banner,
  children,
}: ShellProps) {
  const { t } = useI18n();
  const mainRef = useRef<HTMLElement>(null);
  const previousPath = useRef(currentPath);
  const appName = t("app.name");

  useEffect(() => {
    document.title = `${title} — ${appName}`;
  }, [title, appName]);

  // After a route change, move focus to the new screen so keyboard and screen reader users land on it.
  useEffect(() => {
    if (previousPath.current !== currentPath) {
      previousPath.current = currentPath;
      mainRef.current?.focus();
    }
  }, [currentPath]);

  const showMore = moreItem !== undefined && items.length > primaryCount;

  return (
    <div className="shell">
      <button type="button" className="skip-link" onClick={() => mainRef.current?.focus()}>
        {t("shell.skipToContent")}
      </button>

      <header className="shell__header">
        <div className="shell__identity">
          <p className="shell__brand">
            <BrandMark />
            <span className="shell__product">{appName}</span>
            {entryKey ? <span className="shell__entry badge">{t(entryKey)}</span> : null}
          </p>
          {context ? (
            <p className="shell__context">
              <span className="visually-hidden">{context.label}</span>
              <strong>{context.value}</strong>
            </p>
          ) : null}
        </div>
        <div className="shell__tools">
          <ThemeToggle />
          <LanguagePicker />
        </div>
      </header>

      {items.length > 0 ? (
        <nav className="shell__nav" aria-label={t("shell.mainNav")}>
          <ul className="nav">
            {items.map((item, index) => (
              <li
                key={item.id}
                className={index < primaryCount ? "nav__item" : "nav__item nav__item--secondary"}
              >
                <Link to={item.path} current={item.path === navPath} className="nav__link">
                  <NavIcon id={item.id} />
                  <span>{t(item.labelKey)}</span>
                </Link>
              </li>
            ))}
            {showMore ? (
              <li className="nav__item nav__item--more">
                <Link to={moreItem.path} current={moreItem.path === navPath} className="nav__link">
                  <NavIcon id={moreItem.id} />
                  <span>{t(moreItem.labelKey)}</span>
                </Link>
              </li>
            ) : null}
          </ul>
          {side ? <div className="nav__side">{side}</div> : null}
        </nav>
      ) : null}

      <main className="shell__main" ref={mainRef} tabIndex={-1}>
        <h1 className="shell__title">{title}</h1>
        {banner}
        {children}
      </main>
    </div>
  );
}
