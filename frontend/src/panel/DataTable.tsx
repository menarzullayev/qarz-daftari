import { Fragment } from "react";

import type { Column, TableProps } from "../shared/layout";

export type { Column };

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
}: TableProps<T>) {
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
