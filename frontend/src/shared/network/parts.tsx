import { type ReactNode, useMemo, useState } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { Language, MessageKey } from "../../i18n/types";
import type { ApiError } from "../api";
import { formatMoney } from "../format";
import type { Currency } from "../money";
import { Link } from "../router";
import { labelOf, useStockSettings } from "../stock/parts";
import { tidy } from "../stock/quantity";
import { useWorkspace } from "../workspace/context";
import { Badge, errorText, FieldError, formatInstant } from "../workspace/parts";
import "./messages";
import "./network.css";
import {
  type LinkRole,
  type LinkState,
  type NetEvent,
  type NetworkApi,
  networkOf,
  type NoteStatus,
  type NoteTerms,
  type OrderStatus,
  type PaymentStatus,
} from "./networkApi";

export const NONE = "—";

/** A reason for declining, cancelling, rejecting or correcting: 3 to 200 characters (the server's rule). */
export const REASON_MIN = 3;
export const REASON_MAX = 200;
export const MAX_NOTE = 200;
export const MAX_NAME = 80;
export const MAX_LINES = 100;

/** The units goods are counted in: the stock's own. Their names come from the stock's settings. */
export const UNITS = ["dona", "kg", "g", "l", "ml", "m", "quti", "paket", "juft", "qop", "blok"] as const;

/** The network's calls for the active shop. */
export function useNetwork(): NetworkApi {
  const { api } = useWorkspace();
  return useMemo(() => networkOf(api), [api]);
}

/** Names a unit in the reader's language, by the stock's settings; the key itself until they arrive. */
export function useUnitLabel(): (unit: string) => string {
  const { language } = useI18n();
  const settings = useStockSettings();
  const units = settings.state.status === "ready" ? settings.state.data.units : undefined;
  return (unit) => labelOf(units, unit, language);
}

/** The partner's name; a shop that is gone has none, and one that never gave a name is said so. */
export function partnerText(partner: { name: string | null; removed?: boolean }, t: Translate): string {
  if (partner.removed === true) {
    return t("net.partner.removed");
  }
  return partner.name ?? t("net.partner.unnamed");
}

export function money(amount: number | null | undefined, currency: Currency | null | undefined, language: Language): string {
  return amount === null || amount === undefined ? NONE : formatMoney(amount, language, currency ?? "UZS");
}

export const ROLE_LABELS = {
  buyer: "net.role.buyer",
  supplier: "net.role.supplier",
} as const satisfies Record<LinkRole, MessageKey>;

export const LINK_STATE_LABELS = {
  requested: "net.link.state.requested",
  active: "net.link.state.active",
  declined: "net.link.state.declined",
  ended: "net.link.state.ended",
} as const satisfies Record<LinkState, MessageKey>;

export const ORDER_STATUS_LABELS = {
  sent: "net.order.status.sent",
  accepted: "net.order.status.accepted",
  delivered: "net.order.status.delivered",
  received: "net.order.status.received",
  declined: "net.order.status.declined",
  cancelled: "net.order.status.cancelled",
} as const satisfies Record<OrderStatus, MessageKey>;

export const NOTE_STATUS_LABELS = {
  issued: "net.note.status.issued",
  received: "net.note.status.received",
  rejected: "net.note.status.rejected",
  superseded: "net.note.status.superseded",
  void: "net.note.status.void",
} as const satisfies Record<NoteStatus, MessageKey>;

export const NOTE_TERMS_LABELS = {
  paid: "net.note.terms.paid",
  credit: "net.note.terms.credit",
  part: "net.note.terms.part",
} as const satisfies Record<NoteTerms, MessageKey>;

export const PAYMENT_STATUS_LABELS = {
  awaiting: "net.payment.status.awaiting",
  confirmed: "net.payment.status.confirmed",
  declined: "net.payment.status.declined",
  withdrawn: "net.payment.status.withdrawn",
  lapsed: "net.payment.status.lapsed",
} as const satisfies Record<PaymentStatus, MessageKey>;

type Tone = "danger" | "warning" | "success" | "accent";

const STATUS_TONES: Readonly<Record<string, Tone | undefined>> = {
  requested: "warning",
  sent: "warning",
  issued: "warning",
  awaiting: "warning",
  active: "success",
  received: "success",
  confirmed: "success",
  accepted: "accent",
  delivered: "accent",
  declined: "danger",
  rejected: "danger",
};

/** A status in words, with the tone that repeats it. */
export function Status({ status, label }: { status: string; label: MessageKey }) {
  const { t } = useI18n();
  const tone = STATUS_TONES[status];
  return tone ? <Badge tone={tone}>{t(label)}</Badge> : <Badge>{t(label)}</Badge>;
}

const EVENT_LABELS: Readonly<Record<string, MessageKey>> = {
  link_requested: "net.event.link_requested",
  link_accepted: "net.event.link_accepted",
  link_declined: "net.event.link_declined",
  link_ended: "net.event.link_ended",
  link_attached: "net.event.link_attached",
  partner_removed: "net.event.partner_removed",
  order_sent: "net.event.order_sent",
  order_accepted: "net.event.order_accepted",
  order_declined: "net.event.order_declined",
  order_cancelled: "net.event.order_cancelled",
  note_issued: "net.event.note_issued",
  note_corrected: "net.event.note_corrected",
  note_received: "net.event.note_received",
  note_rejected: "net.event.note_rejected",
  payment_recorded: "net.event.payment_recorded",
  payment_confirmed: "net.event.payment_confirmed",
  payment_declined: "net.event.payment_declined",
  payment_withdrawn: "net.event.payment_withdrawn",
};

/** What a step of the history says beyond its kind: a reason given, or the amount of a payment. */
function eventDetail(event: NetEvent, language: Language): string | null {
  const detail = event.detail;
  if (detail === null) {
    return null;
  }
  const reason = detail["reason"];
  if (typeof reason === "string" && reason !== "") {
    return reason;
  }
  const amount = detail["amount"];
  const currency = detail["currency"];
  if (typeof amount === "number" && Number.isSafeInteger(amount) && (currency === "UZS" || currency === "USD")) {
    return formatMoney(amount, language, currency);
  }
  return null;
}

/**
 * What happened, step by step: when, who (this shop is "we", the other "the partner"; no member of
 * either is named) and what. A kind this client does not know yet is shown by the server's own word.
 */
export function History({ events }: { events: readonly NetEvent[] }) {
  const { t, language } = useI18n();
  return (
    <>
      <h3 className="section-label">{t("net.history")}</h3>
      {events.length === 0 ? (
        <p className="state">{t("net.history.none")}</p>
      ) : (
        <ol className="rows" aria-label={t("net.history")}>
          {events.map((event, index) => {
            const label = EVENT_LABELS[event.kind];
            const detail = eventDetail(event, language);
            return (
              <li key={`${event.at}/${event.kind}/${index}`} className="row">
                <p className="row__name">{label ? t(label) : event.kind}</p>
                <p className="row__meta">
                  {t("net.history.line", {
                    who: t(event.by === "own" ? "net.by.own" : "net.by.partner"),
                    when: formatInstant(event.at, language),
                  })}
                </p>
                {detail !== null ? <p className="row__note">{detail}</p> : null}
              </li>
            );
          })}
        </ol>
      )}
    </>
  );
}

/** The lists of the section, by name: what a screen says to mark its own as the current one. */
export const TAB = { home: "home", out: "out", in: "in", notes: "notes", payments: "payments" } as const;
export type Tab = keyof typeof TAB;

const TABS: readonly { id: Tab; to: string; label: MessageKey }[] = [
  { id: "home", to: "/network", label: "net.tab.home" },
  { id: "out", to: "/network/orders/out", label: "net.tab.out" },
  { id: "in", to: "/network/orders/in", label: "net.tab.in" },
  { id: "notes", to: "/network/notes", label: "net.tab.notes" },
  { id: "payments", to: "/network/payments", label: "net.tab.payments" },
];

/** The lists of the section, one beside the other; left off the paper when a note is printed. */
export function Tabs({ current }: { current: Tab | null }) {
  const { t } = useI18n();
  return (
    <nav className="bar net-tabs no-print" aria-label={t("net.tabs")}>
      {TABS.map((tab) => (
        <Link key={tab.id} to={tab.to} current={tab.id === current} className="button button--small">
          {t(tab.label)}
        </Link>
      ))}
    </nav>
  );
}

/** A short reason typed before something is declined, cancelled or rejected; nothing is sent without one. */
export function ReasonBox({
  id,
  label,
  submitLabel,
  pending,
  error,
  children,
  onSubmit,
  onClose,
}: {
  id: string;
  label: string;
  submitLabel: string;
  pending: boolean;
  error: ApiError | null;
  /** More fields of the same form, above the buttons. */
  children?: ReactNode;
  onSubmit: (reason: string) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [problem, setProblem] = useState(false);
  const send = () => {
    const reason = tidy(text);
    const length = reason === null ? 0 : [...reason].length;
    if (reason === null || length < REASON_MIN || length > REASON_MAX) {
      setProblem(true);
      return;
    }
    onSubmit(reason);
  };
  return (
    <div className="form notice" role="group" aria-label={label}>
      {error ? (
        <p className="field__error" role="alert">
          {errorText(error, t)}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor={id}>{label}</label>
        <textarea
          id={id}
          className="input input--text"
          rows={2}
          value={text}
          maxLength={400}
          disabled={pending}
          aria-invalid={problem}
          aria-describedby={`${id}-error`}
          onChange={(event) => {
            setText(event.target.value);
            setProblem(false);
          }}
        />
        <FieldError id={`${id}-error`} message={problem ? t("net.reason.invalid", { min: REASON_MIN, max: REASON_MAX }) : null} />
      </div>
      {children}
      <p className="actions">
        <button type="button" className="button button--danger" onClick={send} disabled={pending}>
          {pending ? t("state.saving") : submitLabel}
        </button>
        <button type="button" className="button" onClick={onClose} disabled={pending}>
          {t("action.close")}
        </button>
      </p>
    </div>
  );
}

/** "We are the buyer" or "we are the supplier": what this shop is in a link. */
export function roleText(role: LinkRole, t: Translate): string {
  return t(ROLE_LABELS[role]);
}
