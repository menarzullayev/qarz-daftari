import { useState, type ReactNode } from "react";

import { useI18n } from "../i18n/I18nProvider";
import type { MessageKey } from "../i18n/types";
import { MonitorIcon, MoonIcon, SunIcon } from "./icons";
import { isInsideTelegram } from "./telegram";
import { applyTheme, readStoredTheme, THEMES, type Theme, writeStoredTheme } from "./theme";

const OPTIONS: Readonly<Record<Theme, { labelKey: MessageKey; icon: () => ReactNode }>> = {
  light: { labelKey: "theme.light", icon: SunIcon },
  dark: { labelKey: "theme.dark", icon: MoonIcon },
  system: { labelKey: "theme.system", icon: MonitorIcon },
};

/**
 * Light, Dark or System, remembered on this device. Inside Telegram it is not shown: Telegram's theme
 * decides the colors there (shared/telegram.ts), and a second switch would fight it.
 *
 * A narrow header shows the icons alone, so each button carries its name in `aria-label` as well.
 */
export function ThemeToggle() {
  const { t } = useI18n();
  const [theme, setTheme] = useState<Theme>(() => readStoredTheme());
  // Asked once: a page does not move into or out of Telegram while it is open.
  const [hidden] = useState(() => isInsideTelegram());

  if (hidden) {
    return null;
  }

  const choose = (next: Theme) => {
    writeStoredTheme(next);
    applyTheme(next);
    setTheme(next);
  };

  return (
    <div className="theme-toggle" role="group" aria-label={t("shell.theme")}>
      {THEMES.map((option) => {
        const { labelKey, icon: OptionIcon } = OPTIONS[option];
        return (
          <button
            key={option}
            type="button"
            className="theme-toggle__option"
            aria-label={t(labelKey)}
            aria-pressed={theme === option}
            onClick={() => choose(option)}
          >
            <OptionIcon />
            <span className="theme-toggle__label">{t(labelKey)}</span>
          </button>
        );
      })}
    </div>
  );
}
