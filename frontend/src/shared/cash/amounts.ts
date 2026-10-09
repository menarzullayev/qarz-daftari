import type { Language } from "../../i18n/types";
import { formatMoney } from "../format";
import { type AmountResult, parseMoney } from "../money";
import type { CashCurrency } from "./cashApi";

/**
 * An amount of the cash book as a person reads it and as a person types it: the currencies' own rules
 * (`shared/money.ts`), with the currency always passed along with the amount.
 */

/** "45 000 so'm", "1 250.50 $". An amount of one currency is never shown without saying which. */
export function money(amount: number, currency: CashCurrency, language: Language): string {
  return formatMoney(amount, language, currency);
}

/** What was typed into an amount field, in the currency's minor unit, or why it is not an amount. */
export function readAmount(text: string, currency: CashCurrency): AmountResult {
  return parseMoney(text, currency);
}
