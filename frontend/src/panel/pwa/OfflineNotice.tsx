import { useEffect, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import "../messages";

type Connection = {
  /** Whether the device says it has a network connection now. */
  read: () => boolean;
  /** Where the browser announces that the connection came or went. */
  events: Pick<Window, "addEventListener" | "removeEventListener">;
};

const browser = (): Connection => ({ read: () => navigator.onLine, events: window });

/**
 * Whether the device has a connection, as the browser reports it, kept current. "Online" here means
 * only that there is a network; a request can still fail, and each screen says so when one does.
 */
export function useOnline(connection: Connection = browser()): boolean {
  const { read, events } = connection;
  const [online, setOnline] = useState(read);
  useEffect(() => {
    const update = () => setOnline(read());
    events.addEventListener("online", update);
    events.addEventListener("offline", update);
    // The connection may have changed between the first render and this effect.
    update();
    return () => {
      events.removeEventListener("online", update);
      events.removeEventListener("offline", update);
    };
    // The browser's own objects: fixed for the life of the page.
  }, []);
  return online;
}

/**
 * Across the top of the panel while the device has no connection. An installed panel opens without one
 * (its page and scripts are kept by the service worker), and this is what it then says: nothing of a
 * shop's book is kept on the device, so there is nothing to show until the connection is back.
 */
export function OfflineNotice({ online }: { online: boolean }) {
  const { t } = useI18n();
  if (online) {
    return null;
  }
  return (
    <div className="offline" role="alert">
      <p>{t("panel.offline.title")}</p>
      <p className="offline__detail">{t("panel.offline.body")}</p>
    </div>
  );
}
