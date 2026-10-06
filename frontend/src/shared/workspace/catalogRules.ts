import type { Translate } from "../../i18n/I18nProvider";
import { formatUzs, type AmountProblem } from "../money";
import { MAX_PRICE, MIN_PRICE, type QtyProblem } from "../goods";

/**
 * What a good's name, unit and price must look like before they are sent. A port of the length rules of
 * backend/src/qarz/domain/catalog.py, which is the authority and also folds unit spellings ("кг", "kilo").
 */

export const MAX_ITEM_NAME = 80;
/** The longest unit the API reads; the server keeps a known spelling or a short unit of the shop's own. */
export const MAX_UNIT_INPUT = 40;

/** The name as it is stored: trimmed, with single spaces. */
export function cleanItemName(raw: string): string {
  return raw.split(/\s+/u).filter(Boolean).join(" ");
}

export function itemNameProblem(name: string, t: Translate): string | null {
  if (name === "") {
    return t("catalog.name.required");
  }
  return [...name].length > MAX_ITEM_NAME ? t("catalog.name.tooLong", { max: MAX_ITEM_NAME }) : null;
}

export function priceMessage(problem: AmountProblem, t: Translate): string {
  switch (problem) {
    case "empty":
      return t("catalog.price.required");
    case "too_small":
    case "too_large":
      return t("catalog.price.range", { min: formatUzs(MIN_PRICE), max: formatUzs(MAX_PRICE) });
    case "not_whole":
    case "invalid":
      return t("catalog.price.invalid");
  }
}

export function qtyMessage(problem: QtyProblem, t: Translate): string {
  switch (problem) {
    case "empty":
      return t("goods.qty.required");
    case "invalid":
      return t("goods.qty.invalid");
    case "too_precise":
      return t("goods.qty.tooPrecise");
    case "not_positive":
      return t("goods.qty.notPositive");
    case "too_large":
      return t("goods.qty.tooLarge");
  }
}
