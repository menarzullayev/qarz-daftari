import { type FormEvent, type ReactNode, useState } from "react";

import { useI18n } from "../i18n/I18nProvider";
import { useLoad, useSubmit } from "../shared/hooks";
import { isRole } from "../shared/navigation";
import { NotFoundScreen } from "../shared/screens";
import { useMay, useWorkspace } from "../shared/workspace/context";
import { Confirm, Empty, errorText, Failure, formatInstant, Loading } from "../shared/workspace/parts";
import { StartCode } from "../shared/workspace/StartCode";
import type { CatalogueGroup, Invitation, Member } from "./backoffice";
import { type Column, DataTable } from "./DataTable";
import "./messages";
import { memberLabel, useOffice } from "./office";
import { PermissionMatrix } from "./PermissionMatrix";

/** What follows "/start " in the bot's deep link for a staff invitation (application/chat.py). */
export const STAFF_START_PREFIX = "s_";

type InviteRole = "manager" | "seller";
const INVITE_ROLES: readonly InviteRole[] = ["seller", "manager"];
/** What someone who manages staff without being the owner may invite. */
const SELLER_ONLY: readonly InviteRole[] = ["seller"];

type MemberAction = "manager" | "seller" | "suspend" | "restore" | "remove" | "offer";
type Asking = { id: string; action: MemberAction };

/** The other of the two roles a member can be given; the owner's role is not one of them. */
function otherRole(member: Member): InviteRole {
  return member.role === "manager" ? "seller" : "manager";
}

function Members({
  members,
  onChanged,
  onPermissions,
}: {
  members: readonly Member[];
  onChanged: () => void;
  /** Opens a member's permissions; absent when the server keeps to roles or the viewer is not the owner. */
  onPermissions?: ((member: Member) => void) | undefined;
}) {
  const { membershipId, role } = useWorkspace();
  // Someone the owner let manage staff acts on sellers only, and never on a role (the server's rule).
  const isOwner = role === "owner";
  const { office, transfer, reloadTransfer } = useOffice();
  const { t } = useI18n();
  // One question at a time, and nothing is sent before its "yes".
  const [asking, setAsking] = useState<Asking | null>(null);
  const act = useSubmit(({ id, action }: Asking, key) => {
    const sent =
      action === "remove"
        ? office.removeMember(id, key)
        : action === "offer"
          ? office.startTransfer(id, key)
          : action === "suspend" || action === "restore"
            ? office.updateMember(id, { status: action === "suspend" ? "suspended" : "active" }, key)
            : office.updateMember(id, { role: action }, key);
    return sent.then(() => {
      setAsking(null);
      onChanged();
      reloadTransfer();
    });
  });
  const pending = act.state.status === "pending";
  const noOffer = transfer.status === "ready" && transfer.data === null;

  const ask = (id: string, action: MemberAction) => {
    act.reset();
    setAsking({ id, action });
  };

  const question = (member: Member, action: MemberAction): string => {
    const label = memberLabel(member, membershipId, t);
    switch (action) {
      case "manager":
      case "seller":
        return t("staff.confirm.role", { member: label, role: t(`role.${action}`) });
      case "suspend":
        return t("staff.confirm.suspend", { member: label });
      case "restore":
        return t("staff.confirm.restore", { member: label });
      case "remove":
        return t("staff.confirm.remove", { member: label });
      case "offer":
        return t("ownership.offer.confirm", { member: label });
    }
  };

  const controls = (member: Member): ReactNode => {
    // The owner's own membership changes only through an ownership transfer; the server refuses the rest.
    if (member.role === "owner") {
      return <span className="hint">{t("staff.owner.fixed")}</span>;
    }
    if (asking?.id === member.id) {
      return (
        <Confirm
          question={question(member, asking.action)}
          yes={t("confirm.yes")}
          no={t("confirm.no")}
          pending={pending}
          error={act.state.status === "error" ? act.state.error : null}
          onYes={() => act.submit(asking)}
          onNo={() => {
            setAsking(null);
            act.reset();
          }}
        />
      );
    }
    const button = (action: MemberAction, label: string) => (
      <button type="button" className="button button--small" onClick={() => ask(member.id, action)} disabled={pending}>
        {label}
      </button>
    );
    if (!isOwner && (member.role !== "seller" || member.id === membershipId)) {
      return <span className="hint">{t("staff.ownerOnly")}</span>;
    }
    const next = otherRole(member);
    return (
      <span className="table__actions">
        {isOwner ? button(next, next === "manager" ? t("staff.makeManager") : t("staff.makeSeller")) : null}
        {member.status === "suspended" ? button("restore", t("staff.restore")) : button("suspend", t("staff.suspend"))}
        {button("remove", t("staff.remove"))}
        {onPermissions ? (
          <button type="button" className="button button--small" onClick={() => onPermissions(member)} disabled={pending}>
            {t("staff.permissions")}
          </button>
        ) : null}
        {/* Ownership can be offered only to an active manager, and to one person at a time. */}
        {isOwner && member.role === "manager" && member.status === "active" && noOffer
          ? button("offer", t("ownership.offer"))
          : null}
      </span>
    );
  };

  const columns: Column<Member>[] = [
    { id: "member", header: t("staff.member"), rowHeader: true, cell: (member) => memberLabel(member, membershipId, t) },
    {
      id: "status",
      header: t("staff.status"),
      cell: (member) =>
        member.status === "active" || member.status === "suspended" ? t(`staff.status.${member.status}`) : member.status,
    },
    { id: "actions", header: t("table.actions"), cell: controls },
  ];
  return <DataTable caption={t("staff.members")} columns={columns} items={members} rowKey={(member) => member.id} />;
}

/** The offer that waits for an answer, with its expiry, and the way to take it back (REQ-036). */
function Ownership({ members }: { members: readonly Member[] }) {
  const { membershipId } = useWorkspace();
  const { office, transfer, reloadTransfer } = useOffice();
  const { t, language } = useI18n();
  const [asking, setAsking] = useState(false);
  const cancel = useSubmit((_: null, key) =>
    office.cancelTransfer(key).then(() => {
      setAsking(false);
      reloadTransfer();
    }),
  );

  if (transfer.status === "loading") {
    return <Loading />;
  }
  if (transfer.status === "error") {
    return <Failure error={transfer.error} onRetry={reloadTransfer} />;
  }
  const offer = transfer.data;
  if (offer === null) {
    return <Empty>{t("ownership.none")}</Empty>;
  }
  const target = members.find((member) => member.id === offer.toMembership) ?? { id: offer.toMembership, role: "manager" as const };
  return (
    <div className="notice">
      <p>
        {t("ownership.pending", {
          member: memberLabel(target, membershipId, t),
          date: formatInstant(offer.expiresAt, language),
        })}
      </p>
      {asking ? (
        <Confirm
          question={t("ownership.cancel.confirm")}
          yes={t("confirm.yes")}
          no={t("confirm.no")}
          pending={cancel.state.status === "pending"}
          error={cancel.state.status === "error" ? cancel.state.error : null}
          onYes={() => cancel.submit(null)}
          onNo={() => {
            setAsking(false);
            cancel.reset();
          }}
        />
      ) : (
        <p className="actions">
          <button type="button" className="button" onClick={() => setAsking(true)}>
            {t("ownership.cancel")}
          </button>
        </p>
      )}
    </div>
  );
}

/**
 * Creates an invitation and shows its link. The link carries the invitation's token, a credential the
 * server returns once: it stays in this component's state and is gone when the screen is left.
 */
function Invite({ onCreated, roles }: { onCreated: () => void; roles: readonly InviteRole[] }) {
  const { office } = useOffice();
  const { t, language } = useI18n();
  const [role, setRole] = useState<InviteRole>("seller");
  const { state, submit, reset } = useSubmit((chosen: InviteRole, key) =>
    office.invite(chosen, key).then((issued) => {
      onCreated();
      return issued;
    }),
  );
  const pending = state.status === "pending";

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    submit(role);
  };

  if (state.status === "done") {
    const issued = state.result;
    const roleName = isRole(issued.role) ? t(`role.${issued.role}`) : issued.role;
    return (
      <>
        {issued.token === null ? (
          <p className="notice notice--error" role="alert">
            {t("staff.invite.lost")}
          </p>
        ) : (
          <>
            <StartCode
              start={`${STAFF_START_PREFIX}${issued.token}`}
              caption={t("staff.invite.caption", { role: roleName })}
              noBotHint={t("staff.invite.noBot")}
            />
            <p className="hint">{t("staff.invite.expires", { date: formatInstant(issued.expiresAt, language) })}</p>
          </>
        )}
        <p className="actions">
          <button type="button" className="button" onClick={reset}>
            {t("link.hide")}
          </button>
        </p>
      </>
    );
  }

  return (
    <form className="form" onSubmit={onSubmit} noValidate>
      {state.status === "error" ? (
        <p className="notice notice--error" role="alert">
          {errorText(state.error, t)}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="invite-role">{t("staff.invite.role")}</label>
        <select
          id="invite-role"
          className="input"
          value={role}
          onChange={(event) => setRole(event.target.value === "manager" ? "manager" : "seller")}
        >
          {roles.map((option) => (
            <option key={option} value={option}>
              {t(`role.${option}`)}
            </option>
          ))}
        </select>
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("staff.invite.submit")}
        </button>
      </p>
    </form>
  );
}

function Invitations({ invitations, onChanged }: { invitations: readonly Invitation[]; onChanged: () => void }) {
  const { office } = useOffice();
  const { t, language } = useI18n();
  const [asking, setAsking] = useState<string | null>(null);
  const cancel = useSubmit((id: string, key) =>
    office.cancelInvitation(id, key).then(() => {
      setAsking(null);
      onChanged();
    }),
  );
  const pending = cancel.state.status === "pending";
  const roleName = (invitation: Invitation) => (isRole(invitation.role) ? t(`role.${invitation.role}`) : invitation.role);

  if (invitations.length === 0) {
    return <Empty>{t("staff.invitations.none")}</Empty>;
  }
  const columns: Column<Invitation>[] = [
    { id: "role", header: t("staff.invite.role"), rowHeader: true, cell: roleName },
    { id: "expires", header: t("staff.invitation.expires"), cell: (invitation) => formatInstant(invitation.expiresAt, language) },
    {
      id: "actions",
      header: t("table.actions"),
      cell: (invitation) =>
        asking === invitation.id ? (
          <Confirm
            question={t("staff.invitation.cancel.confirm", { role: roleName(invitation) })}
            yes={t("confirm.yes")}
            no={t("confirm.no")}
            pending={pending}
            error={cancel.state.status === "error" ? cancel.state.error : null}
            onYes={() => cancel.submit(invitation.id)}
            onNo={() => {
              setAsking(null);
              cancel.reset();
            }}
          />
        ) : (
          <button
            type="button"
            className="button button--small"
            disabled={pending}
            onClick={() => {
              cancel.reset();
              setAsking(invitation.id);
            }}
          >
            {t("staff.invitation.cancel")}
          </button>
        ),
    },
  ];
  return (
    <DataTable caption={t("staff.invitations")} columns={columns} items={invitations} rowKey={(invitation) => invitation.id} />
  );
}

function Staff() {
  const { office } = useOffice();
  const { role, permissions, membershipId } = useWorkspace();
  const { t } = useI18n();
  const isOwner = role === "owner";
  const members = useLoad((signal) => office.listStaff(signal), [office]);
  const invitations = useLoad((signal) => office.listInvitations(signal), [office]);
  // The matrix exists only while the server keeps permissions per member, which it said by naming the
  // viewer's own (`permissions`); otherwise nothing is asked and nothing of it is shown.
  const matrixOn = isOwner && Boolean(permissions);
  const catalogue = useLoad<CatalogueGroup[] | null>(
    (signal) => (matrixOn ? office.permissionCatalogue(signal) : Promise.resolve(null)),
    [office, matrixOn],
  );
  const [editing, setEditing] = useState<Member | null>(null);
  const groups = catalogue.state.status === "ready" ? catalogue.state.data : null;
  const edited =
    editing && members.state.status === "ready"
      ? (members.state.data.find((member) => member.id === editing.id) ?? null)
      : null;

  return (
    <>
      <p className="hint">{t("staff.names.hint")}</p>
      {members.state.status === "loading" ? <Loading /> : null}
      {members.state.status === "error" ? <Failure error={members.state.error} onRetry={members.reload} /> : null}
      {members.state.status === "ready" ? (
        <Members
          members={members.state.data}
          onChanged={members.reload}
          onPermissions={groups ? (member) => setEditing(member) : undefined}
        />
      ) : null}

      {groups && edited ? (
        <section aria-labelledby="permissions-title">
          <h2 id="permissions-title">
            {t("permissions.title", { member: memberLabel(edited, membershipId, t) })}
          </h2>
          {/* A change of role starts again from the new role's defaults: the matrix is read anew. */}
          <PermissionMatrix
            key={`${edited.id}:${edited.role}`}
            member={edited}
            memberName={memberLabel(edited, membershipId, t)}
            catalogue={groups}
            onClose={() => setEditing(null)}
          />
        </section>
      ) : null}

      {isOwner ? (
        <section aria-labelledby="ownership-title">
          <h2 id="ownership-title">{t("ownership.title")}</h2>
          <p className="hint">{t("ownership.hint")}</p>
          <Ownership members={members.state.status === "ready" ? members.state.data : []} />
        </section>
      ) : null}

      <section aria-labelledby="invite-title">
        <h2 id="invite-title">{t("staff.invite.title")}</h2>
        <Invite onCreated={invitations.reload} roles={isOwner ? INVITE_ROLES : SELLER_ONLY} />
      </section>

      <section aria-labelledby="invitations-title">
        <h2 id="invitations-title">{t("staff.invitations")}</h2>
        {invitations.state.status === "loading" ? <Loading /> : null}
        {invitations.state.status === "error" ? (
          <Failure error={invitations.state.error} onRetry={invitations.reload} />
        ) : null}
        {invitations.state.status === "ready" ? (
          <Invitations invitations={invitations.state.data} onChanged={invitations.reload} />
        ) : null}
      </section>
    </>
  );
}

/**
 * The shop's staff (REQ-031 to REQ-036): who works here in which role, invitations, and handing the
 * shop over, and, while the server keeps them, each member's permissions. It is the owner's unless the
 * owner gave `staff.manage` to someone: anyone else is shown nothing and asks the server nothing.
 */
export function StaffScreen() {
  const can = useMay();
  return can("staff.manage") ? <Staff /> : <NotFoundScreen />;
}
