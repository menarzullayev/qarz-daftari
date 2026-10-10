import type { MessageKey } from "../i18n/types";

/** The catalogue's categories, in the order they are offered (backend/src/qarz/domain/shared_catalog.py). */
export const SHARED_CATEGORIES = [
  "drinks",
  "sweets",
  "snacks",
  "dairy",
  "cheese",
  "grocery",
  "oils",
  "canned",
  "tea",
  "meat",
  "sausage",
  "frozen",
  "bread",
  "produce",
  "ready",
  "kids",
  "beauty",
  "cleaning",
  "home",
  "pets",
  "other",
] as const;
type Category = (typeof SHARED_CATEGORIES)[number];

/** The name of a category; one the client does not know reads as "other". */
export function categoryKey(category: string): MessageKey {
  const known = (SHARED_CATEGORIES as readonly string[]).includes(category) ? (category as Category) : "other";
  return `catalog.category.${known}`;
}
