import { createContext, useContext, type ReactNode } from "react";

import type { ShopApi } from "../api";
import type { Role } from "../navigation";

/** What every data screen of the staff workspace needs: the active shop's API, the role, and a clock. */
export type Workspace = {
  api: ShopApi;
  role: Role;
  /** The current instant; tests pass a fixed one. Calendar dates are always derived in Tashkent time. */
  now: () => Date;
};

const WorkspaceContext = createContext<Workspace | null>(null);

export function WorkspaceProvider({ value, children }: { value: Workspace; children: ReactNode }) {
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): Workspace {
  const value = useContext(WorkspaceContext);
  if (!value) {
    throw new Error("useWorkspace must be used inside WorkspaceProvider");
  }
  return value;
}
