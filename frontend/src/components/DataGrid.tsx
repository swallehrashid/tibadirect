import type { ColDef, GridOptions, RowClickedEvent } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { tibaGridTheme } from "@/lib/aggrid";

interface Props<T> {
  rows: T[];
  columns: ColDef<T>[];
  getRowId: (row: T) => string;
  /** Accessible name for the table. */
  label: string;
  onRowClicked?: (row: T) => void;
  noRows?: string;
  options?: GridOptions<T>;
}

/** AG Grid in the TibaDirect theme: sorted by clicking a header, as tall as its rows, scrolling sideways inside its own box on a phone. */
export function DataGrid<T>({ rows, columns, getRowId, label, onRowClicked, noRows, options }: Props<T>) {
  return (
    <div role="region" aria-label={label} data-testid="data-grid">
      <AgGridReact<T>
        theme={tibaGridTheme}
        rowData={rows}
        columnDefs={columns}
        getRowId={(params) => getRowId(params.data)}
        domLayout="autoHeight"
        defaultColDef={{ sortable: true, resizable: true }}
        rowClass={onRowClicked ? "cursor-pointer" : undefined}
        onRowClicked={onRowClicked ? (event: RowClickedEvent<T>) => event.data && onRowClicked(event.data) : undefined}
        overlayNoRowsTemplate={noRows ? `<span>${noRows}</span>` : undefined}
        {...options}
      />
    </div>
  );
}
