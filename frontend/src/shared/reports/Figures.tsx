import type { ReactNode } from "react";

import { type Column, useDesktop } from "../layout";

export type FigureRow = { name: ReactNode; amount?: string; detail?: string };

/**
 * A list of figures: a real table on a wide screen of the web panel, readable rows on a phone. The
 * figures are the same either way; `row` is how one item reads when there are no columns to align it
 * in. Shared by the reports and the cash book.
 */
export function Figures<T>({
  caption,
  columns,
  items,
  rowKey,
  row,
  foot,
  footRow,
}: {
  caption: string;
  columns: readonly Column<T>[];
  items: readonly T[];
  rowKey: (item: T) => string;
  row: (item: T) => FigureRow;
  foot?: readonly ReactNode[];
  footRow?: { name: string; amount?: string; detail?: string };
}) {
  const desktop = useDesktop();
  if (desktop) {
    return <desktop.Table caption={caption} columns={columns} items={items} rowKey={rowKey} {...(foot ? { foot } : {})} />;
  }
  const line = (key: string, { name, amount, detail }: FigureRow, total = false) => (
    <li key={key} className={total ? "row row--total" : "row"}>
      <p className="row__link">
        <span className="row__name">{name}</span>
        {amount === undefined ? null : <span className="row__amount">{amount}</span>}
      </p>
      {detail === undefined ? null : <p className="row__meta">{detail}</p>}
    </li>
  );
  return (
    <ul className="rows" aria-label={caption}>
      {items.map((item) => line(rowKey(item), row(item)))}
      {footRow ? line("total", footRow, true) : null}
    </ul>
  );
}
