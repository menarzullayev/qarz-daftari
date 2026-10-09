/**
 * The product's name: read from the one file where it is written, the same file the server reads
 * (`backend/src/qarz/domain/brand.py`). Nothing else in the front end writes the name out; a text says
 * `{brand}` and the catalog fills it in (`i18n/catalog.ts`), the pages' titles and descriptions and the
 * installed panel's manifest and icons are made from it (`vite.config.ts`, `scripts/pwaIcons.ts`), and
 * `src/brand.test.ts` fails when the name is found anywhere else.
 *
 * Read with an import attribute and by its default export, so that the scripts Node runs without a
 * build (`scripts/*.ts`) read it the same way the build does.
 */
import definition from "../../../backend/src/qarz/domain/brand.json" with { type: "json" };

/** Where the definition is, from the repository's root. */
export const BRAND_SOURCE = "backend/src/qarz/domain/brand.json";

/** The name as people read it. */
export const BRAND_NAME: string = definition.name;
/** The name under the installed panel's icon. */
export const BRAND_SHORT_NAME: string = definition.short_name;
/** One line on what the product is, in Uzbek: the pages are in Uzbek until the reader's language is known. */
export const BRAND_TAGLINE: string = definition.tagline.uz;

/** A block of the mark: a rectangle with round corners, in the units of the mark's canvas. */
export type MarkBlock = { readonly x: number; readonly y: number; readonly w: number; readonly h: number; readonly r: number };
/**
 * The mark: blocks on a square ground with round corners. A prototype, not a registered logo. The
 * icons, the pages' tab icon and the mark beside the name are all drawn from this.
 */
export const BRAND_MARK: { readonly canvas: number; readonly corner: number; readonly blocks: readonly MarkBlock[] } = definition.mark;

/** What a text writes where the name belongs. */
export const BRAND_PLACEHOLDER = "{brand}";
