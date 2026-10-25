import type { ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { PublicLedgerRow } from "@/api";
import { tibaGridTheme } from "@/lib/aggrid";
import { ledgerLine, type LedgerLine } from "@/lib/ledger";

function formatWhen(value: string, language: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(language.startsWith("sw") ? "sw-KE" : "en-KE", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

/**
 * The case's public ledger as a table. Every row is one side of a money movement; the fingerprint
 * links it to the one before, which is what the "Verify ledger" check recomputes.
 */
export default function LedgerGrid({ rows }: { rows: PublicLedgerRow[] }) {
  const { t, i18n } = useTranslation();
  const language = i18n.language;
  const data = useMemo(() => rows.map((row) => ledgerLine(row, language, t)), [rows, language, t]);
  const columns = useMemo<ColDef<LedgerLine>[]>(
    () => [
      { field: "createdAt", headerName: t("ledger.columns.when"), valueFormatter: (p) => formatWhen(p.value as string, language), minWidth: 175, flex: 1 },
      { field: "what", headerName: t("ledger.columns.what"), minWidth: 290, flex: 2 },
      {
        field: "amount",
        headerName: t("ledger.columns.amount"),
        minWidth: 170,
        flex: 1,
        type: "rightAligned",
        cellClass: (p) => ((p.data?.amountMinor ?? 0) < 0 ? "text-danger-700 font-semibold" : "font-semibold"),
        comparator: (_a, _b, nodeA, nodeB) => (nodeA.data?.amountMinor ?? 0) - (nodeB.data?.amountMinor ?? 0),
      },
      { field: "account", headerName: t("ledger.columns.account"), minWidth: 170, flex: 1.2 },
      { field: "reference", headerName: t("ledger.columns.reference"), minWidth: 140, flex: 1, cellClass: "font-mono text-xs" },
      { field: "fingerprint", headerName: t("ledger.columns.fingerprint"), minWidth: 150, flex: 1, cellClass: "font-mono text-xs" },
    ],
    [t, language],
  );
  return (
    <div data-testid="ledger-grid" role="region" aria-label={t("common.tableOf", { name: t("ledger.title") })}>
      <AgGridReact<LedgerLine>
        theme={tibaGridTheme}
        rowData={data}
        columnDefs={columns}
        getRowId={(params) => params.data.id}
        domLayout="autoHeight"
        suppressCellFocus={false}
        defaultColDef={{ sortable: true, resizable: true }}
        overlayNoRowsTemplate={`<span>${t("ledger.empty")}</span>`}
      />
    </div>
  );
}
