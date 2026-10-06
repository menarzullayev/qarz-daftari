import { I18nProvider, useI18n } from "../i18n/I18nProvider";
import type { Language } from "../i18n/types";
import { useHashPath } from "../shared/router";
import { NotFoundScreen, PlaceholderScreen, SignInRequiredScreen } from "../shared/screens";
import { Shell } from "../shared/Shell";
import { ADMIN_SECTIONS } from "./navigation";

type AdminAppProps = {
  /** False until administrator sign-in exists; then the panel shows only the sign-in notice. */
  signedIn: boolean;
  initialLanguage: Language;
};

function AdminRoutes({ signedIn }: Pick<AdminAppProps, "signedIn">) {
  const { t } = useI18n();
  const path = useHashPath();

  if (!signedIn) {
    return (
      <Shell
        entryKey="entry.admin"
        items={[]}
        primaryCount={0}
        currentPath={path}
        title={t("screen.signInRequired.title")}
      >
        <SignInRequiredScreen />
      </Shell>
    );
  }

  const section = ADMIN_SECTIONS.find((candidate) => candidate.path === path);
  return (
    <Shell
      entryKey="entry.admin"
      items={ADMIN_SECTIONS}
      primaryCount={ADMIN_SECTIONS.length}
      currentPath={path}
      title={section ? t(section.labelKey) : t("notFound.title")}
    >
      {section ? <PlaceholderScreen /> : <NotFoundScreen />}
    </Shell>
  );
}

/** The administration panel: the shared frame with its own navigation and no shop context. */
export function AdminApp({ signedIn, initialLanguage }: AdminAppProps) {
  return (
    <I18nProvider initialLanguage={initialLanguage}>
      <AdminRoutes signedIn={signedIn} />
    </I18nProvider>
  );
}
