import { createContext, type ReactNode, useContext, useMemo } from "react";

import type { Translate } from "../i18n/I18nProvider";
import { type Loaded, useLoad } from "../shared/hooks";
import { canManage, type Role } from "../shared/navigation";
import { useWorkspace } from "../shared/workspace/context";
import { type Backoffice, backoffice, type Deletion, type Transfer } from "./backoffice";
import "./messages";

type Office = {
  office: Backoffice;
  /** Whether the shop is waiting to be deleted. Read for the owner only; null for everyone else. */
  deletion: Loaded<Deletion | null>;
  reloadDeletion: () => void;
  /** The ownership offer that waits for an answer. Read for the owner and managers; null for a seller. */
  transfer: Loaded<Transfer | null>;
  reloadTransfer: () => void;
};

const OfficeContext = createContext<Office | null>(null);

/**
 * State the panel's parts share: the banner, the staff screen and the settings section all show the
 * same deletion request and the same ownership offer, and each must see what another just changed.
 * A role that may not read one of them is not asked for it: nothing is requested for a seller.
 */
export function OfficeProvider({ children }: { children: ReactNode }) {
  const { api, role } = useWorkspace();
  const office = useMemo(() => backoffice(api), [api]);
  const deletion = useLoad(
    (signal) => (role === "owner" ? office.readDeletion(signal) : Promise.resolve(null)),
    [office, role],
  );
  const transfer = useLoad(
    (signal) => (canManage(role) ? office.readTransfer(signal) : Promise.resolve(null)),
    [office, role],
  );
  const value = useMemo(
    () => ({
      office,
      deletion: deletion.state,
      reloadDeletion: deletion.reload,
      transfer: transfer.state,
      reloadTransfer: transfer.reload,
    }),
    [office, deletion.state, deletion.reload, transfer.state, transfer.reload],
  );
  return <OfficeContext.Provider value={value}>{children}</OfficeContext.Provider>;
}

export function useOffice(): Office {
  const value = useContext(OfficeContext);
  if (!value) {
    throw new Error("useOffice must be used inside OfficeProvider");
  }
  return value;
}

/**
 * A short code for a membership: the end of its identifier. The API gives no name for a member of
 * staff, so the role and this code are what tells two sellers apart, here and in the activity log.
 */
export function memberCode(membershipId: string): string {
  return membershipId.slice(-6);
}

/** "Sotuvchi · 3f9a1c", with "(siz)" for the signed-in person's own membership. */
export function memberLabel(member: { id: string; role: Role }, selfId: string | null, t: Translate): string {
  const label = t("staff.member.label", { role: t(`role.${member.role}`), code: memberCode(member.id) });
  return member.id === selfId ? t("staff.member.you", { member: label }) : label;
}
