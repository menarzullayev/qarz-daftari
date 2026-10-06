import { I18nProvider, useI18n } from "../i18n/I18nProvider";
import type { Language, MessageKey } from "../i18n/types";
import { MORE_ITEM, PRIMARY_TAB_COUNT, staffSections } from "./navigation";
import { useHashPath } from "./router";
import { MoreScreen, NotFoundScreen, PlaceholderScreen, SignInRequiredScreen } from "./screens";
import type { StaffSession } from "./session";
import { Shell } from "./Shell";

type StaffAppProps = {
  /** "entry.app" for the Telegram Mini App, "entry.panel" for the web panel. */
  entryKey: MessageKey;
  session: StaffSession | null;
  initialLanguage: Language;
};

function StaffRoutes({ entryKey, session }: Pick<StaffAppProps, "entryKey" | "session">) {
  const { t } = useI18n();
  const path = useHashPath();

  if (!session) {
    return (
      <Shell
        entryKey={entryKey}
        context={{ label: t("shell.activeShop"), value: t("shell.noShop") }}
        items={[]}
        primaryCount={0}
        currentPath={path}
        title={t("screen.signInRequired.title")}
      >
        <SignInRequiredScreen />
      </Shell>
    );
  }

  const sections = staffSections(session.role);
  const overflow = sections.slice(PRIMARY_TAB_COUNT);
  const section = sections.find((candidate) => candidate.path === path);
  const isMore = path === MORE_ITEM.path && overflow.length > 0;

  let title = t("notFound.title");
  let screen = <NotFoundScreen />;
  if (section) {
    title = t(section.labelKey);
    screen = <PlaceholderScreen />;
  } else if (isMore) {
    title = t(MORE_ITEM.labelKey);
    screen = <MoreScreen items={overflow} />;
  }

  return (
    <Shell
      entryKey={entryKey}
      context={{ label: t("shell.activeShop"), value: session.shopName }}
      items={sections}
      primaryCount={PRIMARY_TAB_COUNT}
      moreItem={MORE_ITEM}
      currentPath={path}
      title={title}
    >
      {screen}
    </Shell>
  );
}

/** The staff workspace: the same screens for the Mini App and the web panel, laid out by width. */
export function StaffApp({ entryKey, session, initialLanguage }: StaffAppProps) {
  return (
    <I18nProvider initialLanguage={initialLanguage}>
      <StaffRoutes entryKey={entryKey} session={session} />
    </I18nProvider>
  );
}
