import { type Customer, type CustomerDetail, type Page, reading } from "../../shared/api";
import type { AdminApi } from "../adminApi";

/**
 * The one place the administrator's side reads a shop's customers (REQ-059): the list and one
 * customer's page, both read-only, both answered only while the caller's own support access to that
 * shop is open. Without one the server answers SUPPORT_ACCESS_REQUIRED, and records the attempt. Every
 * successful call is recorded too, and shown to the shop's owner. The shapes are the shop API's own
 * (backend/src/qarz/application/support_access.py: `list_customers`, `read_customer`).
 *
 * Only the two screens beside this file may use it; a test holds that line.
 */
export function supportCustomers(api: AdminApi) {
  const shop = (shopId: string) => `${api.base}/shops/${encodeURIComponent(shopId)}/customers`;
  return {
    list(
      shopId: string,
      params: { q?: string | null; status?: "active" | "archived"; cursor?: string | null },
      signal?: AbortSignal,
    ): Promise<Page<Customer>> {
      return api.send({
        method: "GET",
        path: shop(shopId),
        query: { q: params.q, status: params.status, cursor: params.cursor },
        signal,
        read: reading.page(reading.customer),
      });
    },

    read(shopId: string, customerId: string, signal?: AbortSignal): Promise<CustomerDetail> {
      return api.send({
        method: "GET",
        path: `${shop(shopId)}/${encodeURIComponent(customerId)}`,
        signal,
        read: reading.customerDetail,
      });
    },
  };
}
