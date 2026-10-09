import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import { formatMoney } from "../format";
import type { Column } from "../layout";
import type { Role } from "../navigation";
import { Badge, formatInstant } from "../workspace/parts";
import { stockQtyText } from "../workspace/StockNotes";
import { Fact, Listing, moneyOrNone, qtyWithUnit } from "./parts";
import type { PaymentMethod, Sale, SaleLine, SaleStatus, SaleSummary, SaleWarning } from "./stockApi";

/**
 * What the screens of a cash sale share: the names of its states and of the ways it was paid, who sold
 * it, and a sale drawn in full.
 *
 * What goods cost is drawn only from what the server sent. A member who may not see cost is sent no
 * `cost` at all, on the sale or on its lines, and then there is no row, no column and no figure of it
 * here: nothing about cost is ever worked out on this side.
 */

export const SALE_METHOD_LABELS = {
  cash: "stock.method.cash",
  card: "stock.method.card",
  transfer: "stock.method.transfer",
} as const satisfies Record<PaymentMethod, MessageKey>;

export const SALE_STATUS_LABELS = {
  posted: "stock.sale.status.posted",
  cancelled: "stock.status.cancelled",
} as const satisfies Record<SaleStatus, MessageKey>;

const ROLE_LABELS = { seller: "role.seller", manager: "role.manager", owner: "role.owner" } as const;

/** The name of a seller's role, which is all the server says of who sold; a member who has left the shop has none. */
export function sellerRoleText(held: Role | null, t: Translate): string {
  return held ? t(ROLE_LABELS[held]) : t("stock.sale.seller.gone");
}

/** Who sold: the reader themselves, or the seller's role. */
export function sellerText(sale: Pick<SaleSummary, "mine" | "sellerRole">, t: Translate): string {
  return sale.mine ? t("stock.sale.seller.you") : sellerRoleText(sale.sellerRole, t);
}

export function SaleStatusBadge({ status }: { status: SaleStatus }) {
  const { t } = useI18n();
  return <Badge tone={status === "posted" ? "success" : "danger"}>{t(SALE_STATUS_LABELS[status])}</Badge>;
}

/** "Shakar 2,5 kg, Choy 1 dona": what a sale was of, in one line of a list. */
export function goodsText(lines: readonly SaleLine[]): string {
  return lines.map((line) => `${line.item.name} ${qtyWithUnit(line.qty, line.item.unit)}`).join(", ");
}

/**
 * What a saved sale did that its seller should know. It never stops the sale, which is already saved.
 * "Sold below cost" is said only when the server sent it, which it does for those who may see cost.
 */
export function SaleWarnings({ warnings }: { warnings: readonly SaleWarning[] }) {
  const { t } = useI18n();
  return (
    <>
      {warnings.map((warning) => (
        <p key={`${warning.kind}/${warning.itemId}`} className="row__warning">
          {warning.kind === "negative"
            ? t("stock.warn.negative", { name: warning.name, onHand: stockQtyText(warning.onHand) })
            : t("stock.sale.warn.belowCost", { name: warning.name })}
        </p>
      ))}
    </>
  );
}

/** A sale as it stands: what it came to, how it was paid, who sold it and when, and its lines. */
export function SaleDetails({ sale }: { sale: Sale }) {
  const { t, language } = useI18n();
  const columns: Column<SaleLine>[] = [
    {
      id: "item",
      header: t("stock.col.item"),
      rowHeader: true,
      cell: (line) => (
        <>
          {line.item.name}
          {/* Sold without a movement: the item is not counted, so nothing left the stock. */}
          {line.counted === false ? (
            <>
              {" "}
              <Badge>{t("stock.untracked")}</Badge>
            </>
          ) : null}
        </>
      ),
    },
    { id: "qty", header: t("stock.col.qty"), numeric: true, cell: (line) => qtyWithUnit(line.qty, line.item.unit) },
    { id: "price", header: t("stock.sale.col.price"), numeric: true, cell: (line) => formatMoney(line.price, language) },
    { id: "sum", header: t("stock.sale.col.sum"), numeric: true, cell: (line) => formatMoney(line.lineTotal, language) },
  ];
  // The cost columns exist only when the answer carries cost.
  if (sale.lines.some((line) => line.cost !== undefined)) {
    columns.push(
      {
        id: "cost",
        header: t("stock.col.cost"),
        numeric: true,
        cell: (line) => moneyOrNone(line.cost?.total, language, line.cost?.currency),
      },
      {
        id: "margin",
        header: t("stock.sale.col.margin"),
        numeric: true,
        cell: (line) => moneyOrNone(line.cost?.margin, language, "UZS"),
      },
    );
  }
  const cost = sale.cost;
  return (
    <>
      <h2 className="subject">
        {t("stock.sale.ref", { number: sale.number })} <SaleStatusBadge status={sale.status} />
      </h2>
      <dl className="facts">
        <Fact name={t("stock.doc.total")}>{formatMoney(sale.total, language)}</Fact>
        <Fact name={t("stock.method")}>{t(SALE_METHOD_LABELS[sale.method])}</Fact>
        <Fact name={t("stock.col.when")}>{formatInstant(sale.createdAt, language)}</Fact>
        <Fact name={t("stock.sale.seller")}>{sellerText(sale, t)}</Fact>
        {sale.note !== null ? <Fact name={t("stock.doc.note")}>{sale.note}</Fact> : null}
        {sale.cancelledAt !== null ? (
          <Fact name={t("stock.doc.cancelledAt")}>{formatInstant(sale.cancelledAt, language)}</Fact>
        ) : null}
        {sale.cancelReason !== null ? <Fact name={t("stock.doc.cancelReason")}>{sale.cancelReason}</Fact> : null}
        {cost !== undefined ? (
          <>
            {/* The cost is of the lines whose margin is known; with none known there is no figure to show. */}
            <Fact name={t("stock.col.cost")}>{moneyOrNone(cost.margin === null ? null : cost.total, language, "UZS")}</Fact>
            <Fact name={t("stock.sale.col.margin")}>{moneyOrNone(cost.margin, language, "UZS")}</Fact>
          </>
        ) : null}
      </dl>
      {cost !== undefined && !cost.complete && cost.margin !== null ? <p className="hint">{t("stock.sale.margin.partial")}</p> : null}
      {sale.inCashBook && sale.status === "posted" ? <p className="hint">{t("stock.sale.cashBook.done")}</p> : null}
      <Listing caption={t("stock.sale.lines")} columns={columns} items={sale.lines} rowKey={(line) => String(line.lineNo)} />
    </>
  );
}
