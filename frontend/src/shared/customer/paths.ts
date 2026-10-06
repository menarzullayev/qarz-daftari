/**
 * Where the customer's own pages live in the address. Everything else belongs to the staff workspace.
 * Kept apart from the screens so that deciding which area a path belongs to does not load them.
 */
export const MY_PATH = "/my";

export function isCustomerPath(path: string): boolean {
  return path === MY_PATH || path.startsWith(`${MY_PATH}/`);
}
