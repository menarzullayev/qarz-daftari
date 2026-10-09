import { useState } from "react";

import { type Translate, useI18n } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import { type Loaded, useLoad, useSubmit } from "../hooks";
import { UsersIcon } from "../icons";
import type { Column } from "../layout";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { Fact, Listing, useStock } from "../stock/parts";
import { useMay, useWorkspace } from "../workspace/context";
import { Confirm, Empty, errorText, Failure, formatInstant, Loading } from "../workspace/parts";
import { type Invite, type IssuedInvite, LINK_ROLES, type LinkRole, type NetLink, type Overview, type Reconciliation, type Waiting } from "./networkApi";
import { History, LINK_STATE_LABELS, money, NONE, partnerText, roleText, Status, TAB, Tabs, useNetwork } from "./parts";
import { PaymentForm } from "./PaymentScreens";

/** Where each thing that awaits this shop is answered, and how it is named. */
const WAITING: readonly { key: keyof Waiting; label: MessageKey; to: string | null }[] = [
  { key: "links", label: "net.waiting.links", to: null },
  { key: "orders", label: "net.waiting.orders", to: "/network/orders/in" },
  { key: "deliveries", label: "net.waiting.deliveries", to: "/network/orders/in" },
  { key: "notes", label: "net.waiting.notes", to: "/network/notes/waiting" },
  { key: "rejectedNotes", label: "net.waiting.rejectedNotes", to: "/network/notes" },
  { key: "payments", label: "net.waiting.payments", to: "/network/payments" },
];

/** What awaits THIS shop's step, as the first thing of the section: a figure for each kind that is not zero. */
export function WaitingFigures({ waiting }: { waiting: Waiting }) {
  const { t } = useI18n();
  const open = WAITING.filter((entry) => waiting[entry.key] > 0);
  return (
    <section aria-label={t("net.waiting")}>
      <h2 className="section-label">{t("net.waiting")}</h2>
      {open.length === 0 ? (
        <p className="state">{t("net.waiting.none")}</p>
      ) : (
        <dl className="figures">
          {open.map((entry) => (
            <div key={entry.key} className="figure">
              <dt>{entry.to === null ? t(entry.label) : <Link to={entry.to}>{t(entry.label)}</Link>}</dt>
              <dd>{waiting[entry.key]}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

/** "We will be the buyer" or "we will be the supplier". */
function RoleChoice({ value, disabled, onChange }: { value: LinkRole; disabled: boolean; onChange: (role: LinkRole) => void }) {
  const { t } = useI18n();
  return (
    <div className="toggle" role="group" aria-label={t("net.role.choose")}>
      {LINK_ROLES.map((role) => (
        <button key={role} type="button" className="toggle__option" aria-pressed={value === role} disabled={disabled} onClick={() => onChange(role)}>
          {roleText(role, t)}
        </button>
      ))}
    </div>
  );
}

/** The code of an invitation at the one moment it is known: on the screen, and on the clipboard when asked. */
function IssuedCode({ invite, onClose }: { invite: IssuedInvite; onClose: () => void }) {
  const { t, language } = useI18n();
  const [copied, setCopied] = useState(false);
  const copy = () => {
    // The clipboard is absent outside a secure context and may be refused; the code stays selectable.
    new Promise<void>((resolve) => resolve(navigator.clipboard.writeText(invite.code))).then(
      () => setCopied(true),
      () => setCopied(false),
    );
  };
  return (
    <div className="notice notice--done" role="status">
      <p className="notice__title">{t("net.invite.made")}</p>
      <p className="net-code">{invite.code}</p>
      <p>{t("net.invite.once", { until: formatInstant(invite.expiresAt, language) })}</p>
      <p>{t("net.invite.handOver")}</p>
      <p className="actions">
        <button type="button" className="button button--primary" onClick={copy}>
          {t(copied ? "net.invite.copied" : "net.invite.copy")}
        </button>
        <button type="button" className="button" onClick={onClose}>
          {t("action.close")}
        </button>
      </p>
    </div>
  );
}

/** Invitations: making a code for another shop, the ones that stand, and taking one back. */
function Invites({ invites, onChanged }: { invites: readonly Invite[]; onChanged: () => void }) {
  const { t, language } = useI18n();
  const network = useNetwork();
  const [making, setMaking] = useState(false);
  const [role, setRole] = useState<LinkRole>("buyer");
  const [issued, setIssued] = useState<IssuedInvite | null>(null);
  const create = useSubmit((as: LinkRole, key) =>
    network.createInvite(as, key).then((made) => {
      setIssued(made);
      setMaking(false);
      onChanged();
    }),
  );
  const withdraw = useSubmit((inviteId: string, key) => network.withdrawInvite(inviteId, key).then(onChanged));
  const pending = create.state.status === "pending";
  return (
    <section aria-label={t("net.invites")}>
      <h3 className="section-label">{t("net.invites")}</h3>
      {issued ? <IssuedCode invite={issued} onClose={() => setIssued(null)} /> : null}
      {withdraw.state.status === "error" ? (
        <p className="notice notice--error" role="alert">
          {errorText(withdraw.state.error, t)}
        </p>
      ) : null}
      {invites.length === 0 ? (
        <p className="state">{t("net.invites.none")}</p>
      ) : (
        <ul className="rows" aria-label={t("net.invites")}>
          {invites.map((invite) => (
            <li key={invite.id} className="row">
              <p className="row__name">{roleText(invite.as, t)}</p>
              <p className="row__meta">{t("net.invite.until", { until: formatInstant(invite.expiresAt, language) })}</p>
              <button
                type="button"
                className="button button--small"
                disabled={withdraw.state.status === "pending"}
                onClick={() => withdraw.submit(invite.id)}
              >
                {t("net.invite.withdraw")}
              </button>
            </li>
          ))}
        </ul>
      )}
      {making ? (
        <div className="form notice" role="group" aria-label={t("net.invite.make")}>
          {create.state.status === "error" ? (
            <p className="field__error" role="alert">
              {errorText(create.state.error, t)}
            </p>
          ) : null}
          <p className="hint">{t("net.invite.hint")}</p>
          <RoleChoice value={role} disabled={pending} onChange={setRole} />
          <p className="actions">
            <button type="button" className="button button--primary" disabled={pending} onClick={() => create.submit(role)}>
              {pending ? t("state.saving") : t("net.invite.submit")}
            </button>
            <button
              type="button"
              className="button"
              disabled={pending}
              onClick={() => {
                setMaking(false);
                create.reset();
              }}
            >
              {t("action.cancel")}
            </button>
          </p>
        </div>
      ) : (
        <p className="actions">
          <button type="button" className="button" onClick={() => setMaking(true)}>
            {t("net.invite.make")}
          </button>
        </p>
      )}
    </section>
  );
}

/** Linking by a code another shop handed over: the code, and what this shop will be. */
function ConnectForm({ onDone }: { onDone: () => void }) {
  const { t } = useI18n();
  const network = useNetwork();
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [role, setRole] = useState<LinkRole>("buyer");
  const [problem, setProblem] = useState(false);
  const [sent, setSent] = useState(false);
  const request = useSubmit((job: { code: string; as: LinkRole }, key) =>
    network.requestLink(job.code, job.as, key).then(() => {
      setOpen(false);
      setCode("");
      setSent(true);
      onDone();
    }),
  );
  const pending = request.state.status === "pending";
  const send = () => {
    const clean = code.trim();
    if (clean === "") {
      setProblem(true);
      return;
    }
    request.submit({ code: clean, as: role });
  };
  if (!open) {
    return (
      <>
        {sent ? (
          <p className="notice notice--done" role="status">
            {t("net.connect.sent")}
          </p>
        ) : null}
        <p className="actions">
          <button
            type="button"
            className="button"
            onClick={() => {
              setOpen(true);
              setSent(false);
            }}
          >
            {t("net.connect")}
          </button>
        </p>
      </>
    );
  }
  return (
    <div className="form notice" role="group" aria-label={t("net.connect")}>
      {request.state.status === "error" ? (
        <p className="field__error" role="alert">
          {errorText(request.state.error, t)}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="net-connect-code">{t("net.connect.code")}</label>
        <input
          id="net-connect-code"
          className="input"
          value={code}
          maxLength={100}
          autoComplete="off"
          spellCheck={false}
          disabled={pending}
          aria-invalid={problem}
          aria-describedby="net-connect-code-error"
          onChange={(event) => {
            setCode(event.target.value);
            setProblem(false);
          }}
        />
        {problem ? (
          <p className="field__error" id="net-connect-code-error" role="alert">
            {t("net.connect.code.invalid")}
          </p>
        ) : null}
      </div>
      <p className="hint">{t("net.connect.role.hint")}</p>
      <RoleChoice value={role} disabled={pending} onChange={setRole} />
      <p className="actions">
        <button type="button" className="button button--primary" onClick={send} disabled={pending}>
          {pending ? t("state.saving") : t("net.connect.submit")}
        </button>
        <button
          type="button"
          className="button"
          disabled={pending}
          onClick={() => {
            setOpen(false);
            request.reset();
          }}
        >
          {t("action.cancel")}
        </button>
      </p>
    </div>
  );
}

type Option = { id: string; name: string };
/** How many suppliers or customers the picker asks for at once: the rest are reached by searching. */
const PICKER_PAGE = 50;

/**
 * The row of this shop's own books a link stands for: one of its suppliers when it is the buyer, one
 * of its customers when it is the supplier. Chosen from the first of them by name; with none chosen,
 * accepting makes a new row.
 */
function CounterpartPicker({
  role,
  value,
  disabled,
  allowNew,
  onChange,
}: {
  role: LinkRole;
  value: string;
  disabled: boolean;
  /** Offer "a new row" as a choice: when accepting, not when one must be named. */
  allowNew: boolean;
  onChange: (id: string) => void;
}) {
  const { t } = useI18n();
  const { api } = useWorkspace();
  const stock = useStock();
  const [words, setWords] = useState("");
  const [query, setQuery] = useState("");
  const options: { state: Loaded<Option[]> } = useLoad(
    (signal) =>
      role === "buyer"
        ? stock.suppliers({ q: query, status: "active", limit: PICKER_PAGE }, signal).then((page) => page.suppliers.map((row) => ({ id: row.id, name: row.name })))
        : api
            .listCustomers({ q: query, status: "active", limit: PICKER_PAGE }, signal)
            .then((page) => page.items.map((row) => ({ id: row.id, name: row.displayName }))),
    [api, stock, role, query],
  );
  const label = t(role === "buyer" ? "net.counterpart.supplier" : "net.counterpart.customer");
  return (
    <>
      <div className="search">
        <input
          type="search"
          className="input"
          value={words}
          maxLength={80}
          disabled={disabled}
          aria-label={t("net.counterpart.search")}
          placeholder={t("net.counterpart.search")}
          onChange={(event) => setWords(event.target.value)}
        />
        <button type="button" className="button" disabled={disabled} onClick={() => setQuery(words.trim())}>
          {t("action.search")}
        </button>
      </div>
      <div className="field">
        <label htmlFor="net-counterpart">{label}</label>
        <select id="net-counterpart" className="input" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
          <option value="">{t(allowNew ? "net.counterpart.new" : "net.counterpart.choose")}</option>
          {options.state.status === "ready"
            ? options.state.data.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name}
                </option>
              ))
            : null}
        </select>
        {options.state.status === "ready" && options.state.data.length >= PICKER_PAGE ? (
          <p className="field__hint">{t("net.counterpart.more", { count: PICKER_PAGE })}</p>
        ) : null}
        {options.state.status === "error" ? (
          <p className="field__error" role="alert">
            {errorText(options.state.error, t)}
          </p>
        ) : null}
      </div>
    </>
  );
}

type LinkMode = "view" | "accept" | "decline" | "end" | "counterpart" | "payment";

/**
 * What may be done with a link, for a member who manages them: answer a request this shop was sent,
 * end an active link, name the row of the books it stands for.
 */
function LinkActions({ link, full, onChanged }: { link: NetLink; full: boolean; onChanged: () => void }) {
  const { t } = useI18n();
  const can = useMay();
  const network = useNetwork();
  const [mode, setMode] = useState<LinkMode>("view");
  const [counterpart, setCounterpart] = useState("");
  const done = () => {
    setMode("view");
    onChanged();
  };
  const accept = useSubmit((counterpartId: string | null, key) => network.acceptLink(link.id, counterpartId, key).then(done));
  const decline = useSubmit((_: null, key) => network.declineLink(link.id, key).then(done));
  const end = useSubmit((_: null, key) => network.endLink(link.id, key).then(done));
  const attach = useSubmit((counterpartId: string, key) => network.setCounterpart(link.id, counterpartId, key).then(done));
  const manage = can("network.manage");
  const answers = manage && link.state === "requested" && link.invited;
  const ends = manage && full && link.state === "active";
  const attaches = manage && full && link.state === "active" && link.counterpart === null;
  // Money between the two shops is the buyer's payment to a supplier and the supplier's from a customer.
  const pays = full && link.state === "active" && can("network.confirm") && can(link.role === "buyer" ? "suppliers.pay" : "payments.record");
  const close = (reset: () => void) => () => {
    setMode("view");
    reset();
  };

  if (mode === "accept") {
    const pending = accept.state.status === "pending";
    return (
      <div className="form notice" role="group" aria-label={t("net.link.accept")}>
        {accept.state.status === "error" ? (
          <p className="field__error" role="alert">
            {errorText(accept.state.error, t)}
          </p>
        ) : null}
        <p className="hint">{t(link.role === "buyer" ? "net.link.accept.hint.buyer" : "net.link.accept.hint.supplier")}</p>
        <CounterpartPicker role={link.role} value={counterpart} disabled={pending} allowNew onChange={setCounterpart} />
        <p className="actions">
          <button type="button" className="button button--primary" disabled={pending} onClick={() => accept.submit(counterpart === "" ? null : counterpart)}>
            {pending ? t("state.saving") : t("net.link.accept")}
          </button>
          <button type="button" className="button" disabled={pending} onClick={close(accept.reset)}>
            {t("action.cancel")}
          </button>
        </p>
      </div>
    );
  }
  if (mode === "counterpart") {
    const pending = attach.state.status === "pending";
    return (
      <div className="form notice" role="group" aria-label={t("net.counterpart.set")}>
        {attach.state.status === "error" ? (
          <p className="field__error" role="alert">
            {errorText(attach.state.error, t)}
          </p>
        ) : null}
        <CounterpartPicker role={link.role} value={counterpart} disabled={pending} allowNew={false} onChange={setCounterpart} />
        <p className="actions">
          <button type="button" className="button button--primary" disabled={pending || counterpart === ""} onClick={() => attach.submit(counterpart)}>
            {pending ? t("state.saving") : t("action.save")}
          </button>
          <button type="button" className="button" disabled={pending} onClick={close(attach.reset)}>
            {t("action.cancel")}
          </button>
        </p>
      </div>
    );
  }
  if (mode === "decline" || mode === "end") {
    const job = mode === "decline" ? decline : end;
    return (
      <Confirm
        question={t(mode === "decline" ? "net.link.decline.question" : "net.link.end.question", { name: partnerText(link.partner, t) })}
        yes={t(mode === "decline" ? "net.link.decline" : "net.link.end")}
        no={t("action.cancel")}
        pending={job.state.status === "pending"}
        error={job.state.status === "error" ? job.state.error : null}
        onYes={() => job.submit(null)}
        onNo={close(job.reset)}
      />
    );
  }
  if (mode === "payment") {
    return <PaymentForm links={[link]} onDone={done} onClose={() => setMode("view")} />;
  }
  if (!answers && !ends && !attaches && !pays) {
    return null;
  }
  return (
    <p className="actions">
      {answers ? (
        <>
          <button type="button" className="button button--primary" onClick={() => setMode("accept")}>
            {t("net.link.accept")}
          </button>
          <button type="button" className="button" onClick={() => setMode("decline")}>
            {t("net.link.decline")}
          </button>
        </>
      ) : null}
      {pays ? (
        <button type="button" className="button button--primary" onClick={() => setMode("payment")}>
          {t("net.payment.record")}
        </button>
      ) : null}
      {attaches ? (
        <button type="button" className="button" onClick={() => setMode("counterpart")}>
          {t("net.counterpart.set")}
        </button>
      ) : null}
      {ends ? (
        <button type="button" className="button" onClick={() => setMode("end")}>
          {t("net.link.end")}
        </button>
      ) : null}
    </p>
  );
}

/** The shop's links: who with, what this shop is in each, and how each stands. */
export function LinksList({ links, onChanged }: { links: readonly NetLink[]; onChanged: () => void }) {
  const { t } = useI18n();
  const columns: Column<NetLink>[] = [
    {
      id: "partner",
      header: t("net.col.partner"),
      rowHeader: true,
      cell: (link) => <Link to={`/network/links/${link.id}`}>{partnerText(link.partner, t)}</Link>,
    },
    { id: "role", header: t("net.col.role"), cell: (link) => roleText(link.role, t) },
    { id: "state", header: t("net.col.state"), cell: (link) => <Status status={link.state} label={LINK_STATE_LABELS[link.state]} /> },
    {
      id: "awaits",
      header: t("net.col.awaits"),
      cell: (link) => (link.state === "requested" ? t(link.invited ? "net.link.awaits.own" : "net.link.awaits.partner") : null),
    },
  ];
  if (links.length === 0) {
    return <Empty icon={<UsersIcon />}>{t("net.links.none")}</Empty>;
  }
  return (
    <Listing
      caption={t("net.links")}
      columns={columns}
      items={links}
      rowKey={(link) => link.id}
      expanded={(link) => (link.state === "requested" && link.invited ? <LinkActions link={link} full={false} onChanged={onChanged} /> : null)}
    />
  );
}

/** The section's first screen in the web panel: what awaits this shop, its links, and its invitations. */
export function HomeScreen() {
  const { t } = useI18n();
  const can = useMay();
  const network = useNetwork();
  const { state, reload } = useLoad((signal) => network.overview(signal), [network]);
  // What was read stays on the screen while it is read again after a change.
  const [shown, setShown] = useState<Overview | null>(null);
  if (state.status === "ready" && state.data !== shown) {
    setShown(state.data);
  }
  if (state.status === "error") {
    return <Failure error={state.error} onRetry={reload} />;
  }
  if (shown === null) {
    return <Loading />;
  }
  return (
    <>
      <Tabs current={TAB.home} />
      <WaitingFigures waiting={shown.waiting} />
      <h3 className="section-label">{t("net.links")}</h3>
      <LinksList links={shown.links} onChanged={reload} />
      {can("network.manage") ? (
        <>
          <ConnectForm onDone={reload} />
          <Invites invites={shown.invites ?? []} onChanged={reload} />
        </>
      ) : null}
    </>
  );
}

/** What the agreed balance means in words: who owes whom, or that nobody owes anything. */
function balanceText(amount: number, role: LinkRole, formatted: (amount: number) => string, t: Translate): string {
  if (amount === 0) {
    return t("net.recon.settled");
  }
  // Above zero the buyer owes the supplier; below zero the buyer paid ahead.
  const weOwe = (amount > 0) === (role === "buyer");
  return t(weOwe ? "net.recon.weOwe" : "net.recon.theyOwe", { amount: formatted(Math.abs(amount)) });
}

/**
 * The two books of a link in one currency, side by side: what both shops confirmed through the
 * network, and what this shop's own account of the partner says. A difference is said plainly, with
 * what explains it; the two are called equal only when they are, and never when this member may not
 * see the shop's own account.
 */
function ReconciliationBlock({ row, link }: { row: Reconciliation; link: NetLink }) {
  const { t, language } = useI18n();
  const amount = (value: number) => money(value, row.currency, language);
  const account =
    link.counterpart === null ? null : link.counterpart.kind === "supplier" ? `/suppliers/${link.counterpart.id}` : `/customers/${link.counterpart.id}`;
  const reasons: { key: string; text: string }[] = [];
  if (row.awaiting.notes !== 0) {
    reasons.push({ key: "notes", text: t("net.recon.awaiting.notes", { amount: amount(row.awaiting.notes) }) });
  }
  if (row.awaiting.paymentsOwn !== 0) {
    reasons.push({ key: "own", text: t("net.recon.awaiting.paymentsOwn", { amount: amount(row.awaiting.paymentsOwn) }) });
  }
  if (row.awaiting.paymentsPartner !== 0) {
    reasons.push({ key: "partner", text: t("net.recon.awaiting.paymentsPartner", { amount: amount(row.awaiting.paymentsPartner) }) });
  }
  if (row.awaiting.paymentsDeclined !== 0) {
    reasons.push({
      key: "declined",
      text: t(link.role === "buyer" ? "net.recon.awaiting.declined.buyer" : "net.recon.awaiting.declined.supplier", {
        amount: amount(row.awaiting.paymentsDeclined),
      }),
    });
  }
  const list =
    reasons.length === 0 ? null : (
      <ul className="net-reasons">
        {reasons.map((reason) => (
          <li key={reason.key}>{reason.text}</li>
        ))}
      </ul>
    );
  let verdict;
  if (row.difference === undefined || row.ownBalance === undefined) {
    verdict = (
      <div className="notice">
        <p>{t(link.counterpart === null ? "net.recon.unlinked" : "net.recon.hidden")}</p>
        {list ? <p>{t("net.recon.awaiting")}</p> : null}
        {list}
      </div>
    );
  } else if (row.difference !== 0) {
    verdict = (
      <div className="notice notice--warning" role="alert">
        <p className="notice__title">{t("net.recon.differ")}</p>
        <p>{t("net.recon.difference", { amount: amount(Math.abs(row.difference)) })}</p>
        <p>{t(list ? "net.recon.why" : "net.recon.why.unknown")}</p>
        {list}
        {account !== null ? (
          <p>
            <Link to={account}>{t(link.role === "buyer" ? "net.recon.account.supplier" : "net.recon.account.customer")}</Link>
          </p>
        ) : null}
      </div>
    );
  } else if (list) {
    // The books agree today, and something is still on its way: that is not yet "settled between us".
    verdict = (
      <div className="notice">
        <p>{t("net.recon.same.pending")}</p>
        {list}
      </div>
    );
  } else {
    verdict = (
      <p className="notice notice--done" role="status">
        {t("net.recon.same")}
      </p>
    );
  }
  return (
    <section className="net-recon" aria-label={t("net.recon.currency", { currency: t(row.currency === "USD" ? "currency.usd.title" : "currency.uzs") })}>
      <dl className="facts">
        <Fact name={t("net.recon.delivered")}>
          <span className="money">{amount(row.agreed.delivered)}</span>
        </Fact>
        <Fact name={t("net.recon.paid")}>
          <span className="money">{amount(row.agreed.paid)}</span>
        </Fact>
        <Fact name={t("net.recon.agreed")}>
          <span className="money">{balanceText(row.agreed.balance, link.role, amount, t)}</span>
        </Fact>
        {row.ownBalance !== undefined ? (
          <Fact name={t(link.role === "buyer" ? "net.recon.own.buyer" : "net.recon.own.supplier")}>
            <span className="money">{balanceText(row.ownBalance, link.role, amount, t)}</span>
          </Fact>
        ) : null}
      </dl>
      {verdict}
    </section>
  );
}

/** One partner: the link, the two books side by side, what may be done, and everything that happened. */
export function LinkScreen({ linkId }: { linkId: string }) {
  const { t, language } = useI18n();
  const network = useNetwork();
  const { state, reload } = useLoad((signal) => network.link(linkId, signal), [network, linkId]);
  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return state.error.status === 404 ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  const { link, reconciliation, events } = state.data;
  const account =
    link.counterpart === null ? null : link.counterpart.kind === "supplier" ? `/suppliers/${link.counterpart.id}` : `/customers/${link.counterpart.id}`;
  return (
    <>
      <Tabs current={null} />
      <h2 className="subject">{partnerText(link.partner, t)}</h2>
      <dl className="facts">
        <Fact name={t("net.col.role")}>{roleText(link.role, t)}</Fact>
        <Fact name={t("net.col.state")}>
          <Status status={link.state} label={LINK_STATE_LABELS[link.state]} />
        </Fact>
        <Fact name={t("net.partner.phone")}>{link.partner.phone ?? NONE}</Fact>
        <Fact name={t(link.role === "buyer" ? "net.counterpart.supplier" : "net.counterpart.customer")}>
          {account === null ? t("net.counterpart.none") : <Link to={account}>{t("net.counterpart.open")}</Link>}
        </Fact>
        <Fact name={t("net.link.requestedAt")}>{formatInstant(link.requestedAt, language)}</Fact>
        {link.decidedAt !== null ? <Fact name={t("net.link.decidedAt")}>{formatInstant(link.decidedAt, language)}</Fact> : null}
        {link.endedAt !== null ? (
          <Fact name={t("net.link.endedAt")}>
            {formatInstant(link.endedAt, language)}
            {link.endedBy !== null ? ` · ${t(link.endedBy === "own" ? "net.by.own" : "net.by.partner")}` : ""}
          </Fact>
        ) : null}
      </dl>
      {link.state === "requested" && !link.invited ? <p className="hint">{t("net.link.awaits.partner")}</p> : null}
      <LinkActions link={link} full onChanged={reload} />

      <h3 className="section-label">{t("net.recon")}</h3>
      <p className="hint">{t("net.recon.hint")}</p>
      {reconciliation.length === 0 ? (
        <p className="state">{t("net.recon.none")}</p>
      ) : (
        reconciliation.map((row) => <ReconciliationBlock key={row.currency} row={row} link={link} />)
      )}

      <History events={events} />
      <p className="actions">
        <Link to="/network" className="button">
          {t("nav.network")}
        </Link>
      </p>
    </>
  );
}
