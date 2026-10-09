import { useI18n } from "../i18n/I18nProvider";
import { NavIcon } from "./icons";
import { Link } from "./router";
import type { ShellNavItem } from "./Shell";

/** Body of a section whose real screen arrives in a later story. It shows no data and calls nothing. */
export function PlaceholderScreen() {
  const { t } = useI18n();
  return <p>{t("screen.placeholder")}</p>;
}

export function SignInRequiredScreen() {
  const { t } = useI18n();
  return <p>{t("screen.signInRequired.body")}</p>;
}

/**
 * Shown for an unknown route and for a section the current role may not open. The two cases look the
 * same on purpose, as they do in the API, which answers NOT_FOUND to both.
 */
export function NotFoundScreen() {
  const { t } = useI18n();
  return (
    <>
      <p>{t("notFound.body")}</p>
      <p>
        <Link to="/">{t("notFound.home")}</Link>
      </p>
    </>
  );
}

/** The sections that do not fit in the phone's tab bar, as tiles: an icon and the section's name. */
export function MoreScreen({ items }: { items: readonly ShellNavItem[] }) {
  const { t } = useI18n();
  return (
    <ul className="tiles">
      {items.map((item) => (
        <li key={item.id}>
          <Link to={item.path} className="tile">
            <span className="tile__icon">
              <NavIcon id={item.id} />
            </span>
            <span className="tile__title">{t(item.labelKey)}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
