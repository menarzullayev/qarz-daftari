import { Fragment, type ReactNode } from "react";

export type Column<T> = {
  id: string;
  header: string;
  cell: (item: T) => ReactNode;
  /** Amounts and counts: right-aligned, in tabular figures. */
  numeric?: boolean;
  /** The cell that names the row; it is rendered as the row's header. */
  rowHeader?: boolean;
};

/**
 * A real table: a caption, column headers, and a header cell in each row, so a screen reader can say
 * where it is. The roles are spelled out because the panel's style sheet stacks the rows into cards on
 * a narrow screen, and a browser drops a table's meaning when its display changes unless the roles say it.
 */
export function DataTable<T>({
  caption,
  columns,
  items,
  rowKey,
  expanded,
  foot,
}: {
  /** Names the table for a screen reader; not drawn. */
  caption: string;
  columns: readonly Column<T>[];
  items: readonly T[];
  rowKey: (item: T) => string;
  /** Content that takes a full row under an item, such as an open form; null for none. */
  expanded?: (item: T) => ReactNode;
  /** One summary row: a cell per column. */
  foot?: readonly ReactNode[];
}) {
  return (
    <div className="table-wrap">
      <table className="table" role="table">
        <caption className="visually-hidden">{caption}</caption>
        <thead role="rowgroup">
          <tr role="row">
            {columns.map((column) => (
              <th key={column.id} scope="col" role="columnheader" className={column.numeric ? "table__num" : undefined}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody role="rowgroup">
          {items.map((item) => {
            const under = expanded?.(item) ?? null;
            return (
              <Fragment key={rowKey(item)}>
                <tr role="row">
                  {columns.map((column) =>
                    column.rowHeader ? (
                      <th key={column.id} scope="row" role="rowheader" data-label={column.header}>
                        {column.cell(item)}
                      </th>
                    ) : (
                      <td
                        key={column.id}
                        role="cell"
                        data-label={column.header}
                        className={column.numeric ? "table__num" : undefined}
                      >
                        {column.cell(item)}
                      </td>
                    ),
                  )}
                </tr>
                {under === null ? null : (
                  <tr role="row" className="table__under">
                    <td role="cell" colSpan={columns.length}>
                      {under}
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
        {foot ? (
          <tfoot role="rowgroup">
            <tr role="row">
              {columns.map((column, index) =>
                index === 0 ? (
                  <th key={column.id} scope="row" role="rowheader" data-label={column.header}>
                    {foot[index]}
                  </th>
                ) : (
                  <td
                    key={column.id}
                    role="cell"
                    data-label={column.header}
                    className={column.numeric ? "table__num" : undefined}
                  >
                    {foot[index]}
                  </td>
                ),
              )}
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
}
