import { useI18n } from "../../i18n/I18nProvider";
import type { ApiError, StockWarning } from "../api";

/** A quantity of the stock as people here write it: the API's decimal string with a decimal comma. */
export function stockQtyText(qty: string): string {
  return qty.replace(".", ",");
}

/**
 * What a saved sale did to the stock that its seller should know: it never stops the sale, which is
 * already saved. Nothing is drawn without a warning, and the server sends none while the stock is off.
 */
export function StockWarnings({ warnings }: { warnings: readonly StockWarning[] | undefined }) {
  const { t } = useI18n();
  if (!warnings || warnings.length === 0) {
    return null;
  }
  return (
    <>
      {warnings.map((warning) => (
        <p key={`${warning.kind}/${warning.itemId}`} className="row__warning">
          {warning.kind === "negative"
            ? t("stock.warn.negative", { name: warning.name, onHand: stockQtyText(warning.onHand) })
            : t("stock.warn.unit", { name: warning.name, unit: warning.unit })}
        </p>
      ))}
    </>
  );
}

const QTY = /^-?\d{1,12}(?:\.\d{1,3})?$/;

/**
 * The figures of a sale the shop refused because it would take an item beyond what is on hand. The
 * server's own sentence is shown by the caller; this adds which item, how much there is and how much
 * the sale needs, when the refusal carries them.
 */
export function StockRefusal({ error }: { error: ApiError | null }) {
  const { t } = useI18n();
  if (error?.code !== "STOCK_INSUFFICIENT") {
    return null;
  }
  const { name, on_hand: onHand, wanted } = error.fields;
  if (name === undefined || onHand === undefined || wanted === undefined || !QTY.test(onHand) || !QTY.test(wanted)) {
    return null;
  }
  return <p>{t("stock.refused.figures", { name, onHand: stockQtyText(onHand), wanted: stockQtyText(wanted) })}</p>;
}
