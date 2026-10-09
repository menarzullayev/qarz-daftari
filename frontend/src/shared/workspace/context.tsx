import { createContext, useContext, type ReactNode } from "react";

import type { ShopApi, ShopMembership } from "../api";
import type { Role } from "../navigation";
import { type Held, may, type PermissionKey } from "../permissions";
import type { ShopMode } from "./shopMode";

/** What every data screen of the staff workspace needs: the active shop's API, the role, and a clock. */
export type Workspace = {
  api: ShopApi;
  role: Role;
  /**
   * What the server said the person may do here, while the permission matrix is on; absent or null when
   * it keeps to roles. Screens ask `useMay`, never this set.
   */
  permissions?: Held | undefined;
  /** The signed-in person's membership in the active shop; null when the server did not say. */
  membershipId: string | null;
  /** The bot whose deep links connect customers; null when the build does not name one. */
  botUsername: string | null;
  /** The current instant; tests pass a fixed one. Calendar dates are always derived in Tashkent time. */
  now: () => Date;
  /** The active shop's name, for a preview of what a customer will read; absent when it is not known. */
  shopName?: string | undefined;
  /** What the server's refusals have said about the shop since it was opened; see `shopMode.ts`. */
  shopMode?: ShopMode | null | undefined;
  /** Every shop the person works in, when the caller knows them (REQ-064). */
  shops?: readonly ShopMembership[] | undefined;
  /** Reads the person's shops and roles again, after something that changes them (an accepted transfer). */
  reloadSession?: (() => void) | undefined;
};

const WorkspaceContext = createContext<Workspace | null>(null);

export function WorkspaceProvider({ value, children }: { value: Workspace; children: ReactNode }) {
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

/**
 * Whether to offer something to the signed-in member: by the permissions the server named, or by the
 * role when it named none. The server refuses the call whatever the interface shows.
 */
export function useMay(): (permission: PermissionKey) => boolean {
  const { role, permissions } = useWorkspace();
  return (permission) => may({ role, permissions }, permission);
}

export function useWorkspace(): Workspace {
  const value = useContext(WorkspaceContext);
  if (!value) {
    throw new Error("useWorkspace must be used inside WorkspaceProvider");
  }
  return value;
}
