import type { ReactNode } from "react";

/**
 * The icons of the design system: outline, on a 24px grid, drawn in the text color (`.icon` in
 * shell.css sets the stroke). Every one is decorative and hidden from a screen reader: the control or
 * the text beside it carries the name, so an icon-only button needs its own `aria-label`.
 *
 * Path data sits in constants so that the hard-coded text check, which reads string attributes in JSX,
 * keeps looking only at what a person can read.
 */
function Icon({ children }: { children: ReactNode }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
      {children}
    </svg>
  );
}

const HOME = "M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z";
const USERS_BODY = "M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5";
const USERS_SECOND = "M16 4.8a3.5 3.5 0 0 1 0 6.4M18.5 14.9c1.7.9 2.7 2.6 3 5.1";
const USER_BODY = "M5 20c.6-3.6 3.4-5.5 7-5.5s6.4 1.9 7 5.5";
const PLUS = "M12 5v14M5 12h14";
const BOX = "M4 8l8-4 8 4v8l-8 4-8-4z";
const BOX_EDGES = "M4 8l8 4 8-4M12 12v8";
const BELL = "M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z";
const BELL_CLAPPER = "M10 20a2 2 0 0 0 4 0";
const CHART_AXES = "M4 20V4M4 20h16";
const CHART_BARS = "M8 16v-5M12 16V8M16 16v-7";
const MESSAGE = "M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H10l-5 4v-4a2 2 0 0 1-1-2z";
const DOWNLOAD = "M12 4v11M7.5 11l4.5 4.5 4.5-4.5M5 20h14";
const LIST = "M9 7h11M9 12h11M9 17h11M4.5 7h.3M4.5 12h.3M4.5 17h.3";
const CARD_LINES = "M3 10h18M16 15h2";
const SLIDERS = "M4 7h10M18 7h2M4 17h2M10 17h10";
const ALERT = "M12 4l9 16H3z";
const ALERT_MARK = "M12 10v4.5M12 17.2v.3";
const CLOCK_HANDS = "M12 7.5V12l3 2";
const CHECK = "M5 12.5l4.5 4.5L19 7.5";
const SEARCH_HANDLE = "M16 16l4.5 4.5";
const SUN_RAYS =
  "M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8";
const MOON = "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z";
const MONITOR_STAND = "M9 20h6M12 16.5V20";

export function HomeIcon() {
  return (
    <Icon>
      <path d={HOME} />
    </Icon>
  );
}

export function UsersIcon() {
  return (
    <Icon>
      <circle cx="9" cy="8" r="3.5" />
      <path d={USERS_BODY} />
      <path d={USERS_SECOND} />
    </Icon>
  );
}

/** One person: the shop's staff, next to the customers' two. Drawn for the application in the system's style. */
export function UserIcon() {
  return (
    <Icon>
      <circle cx="12" cy="8" r="3.5" />
      <path d={USER_BODY} />
    </Icon>
  );
}

export function PlusIcon() {
  return (
    <Icon>
      <path d={PLUS} />
    </Icon>
  );
}

export function BoxIcon() {
  return (
    <Icon>
      <path d={BOX} />
      <path d={BOX_EDGES} />
    </Icon>
  );
}

export function BellIcon() {
  return (
    <Icon>
      <path d={BELL} />
      <path d={BELL_CLAPPER} />
    </Icon>
  );
}

export function ChartIcon() {
  return (
    <Icon>
      <path d={CHART_AXES} />
      <path d={CHART_BARS} />
    </Icon>
  );
}

export function MessageIcon() {
  return (
    <Icon>
      <path d={MESSAGE} />
    </Icon>
  );
}

export function DownloadIcon() {
  return (
    <Icon>
      <path d={DOWNLOAD} />
    </Icon>
  );
}

/** A list of lines: journals. Drawn for the application in the system's style. */
export function ListIcon() {
  return (
    <Icon>
      <path d={LIST} />
    </Icon>
  );
}

export function CardIcon() {
  return (
    <Icon>
      <rect x="3" y="6" width="18" height="13" rx="2" />
      <path d={CARD_LINES} />
    </Icon>
  );
}

export function SlidersIcon() {
  return (
    <Icon>
      <path d={SLIDERS} />
      <circle cx="16" cy="7" r="2" />
      <circle cx="8" cy="17" r="2" />
    </Icon>
  );
}

export function MoreIcon() {
  return (
    <Icon>
      <circle cx="5" cy="12" r="1.2" />
      <circle cx="12" cy="12" r="1.2" />
      <circle cx="19" cy="12" r="1.2" />
    </Icon>
  );
}

export function AlertIcon() {
  return (
    <Icon>
      <path d={ALERT} />
      <path d={ALERT_MARK} />
    </Icon>
  );
}

export function ClockIcon() {
  return (
    <Icon>
      <circle cx="12" cy="12" r="8.5" />
      <path d={CLOCK_HANDS} />
    </Icon>
  );
}

export function CheckIcon() {
  return (
    <Icon>
      <path d={CHECK} />
    </Icon>
  );
}

export function SearchIcon() {
  return (
    <Icon>
      <circle cx="11" cy="11" r="6.5" />
      <path d={SEARCH_HANDLE} />
    </Icon>
  );
}

export function SunIcon() {
  return (
    <Icon>
      <circle cx="12" cy="12" r="4" />
      <path d={SUN_RAYS} />
    </Icon>
  );
}

export function MoonIcon() {
  return (
    <Icon>
      <path d={MOON} />
    </Icon>
  );
}

export function MonitorIcon() {
  return (
    <Icon>
      <rect x="3" y="4.5" width="18" height="12" rx="2" />
      <path d={MONITOR_STAND} />
    </Icon>
  );
}

type IconComponent = () => ReactNode;

/**
 * The icon of each navigation item, by the item's id, for the three entry points. Kept here and not
 * in the navigation models, which stay lists of ids, paths and labels.
 */
const NAV_ICONS: Readonly<Record<string, IconComponent | undefined>> = {
  overview: HomeIcon,
  customers: UsersIcon,
  newEntry: PlusIcon,
  catalog: BoxIcon,
  stock: BoxIcon,
  stockDocuments: ListIcon,
  suppliers: UsersIcon,
  network: UsersIcon,
  reminders: BellIcon,
  reports: ChartIcon,
  disputes: MessageIcon,
  importExport: DownloadIcon,
  staff: UserIcon,
  activityLog: ListIcon,
  subscription: CardIcon,
  shopSettings: SlidersIcon,
  more: MoreIcon,
  mine: CardIcon,
  adminShops: HomeIcon,
  adminReceipts: CardIcon,
  adminSettings: SlidersIcon,
  adminSupportAccess: MessageIcon,
  adminAudit: ListIcon,
};

/** An item added later without an icon of its own still gets one: no link is ever a bare word in the bar. */
export function NavIcon({ id }: { id: string }) {
  const Found = NAV_ICONS[id] ?? ListIcon;
  return <Found />;
}
