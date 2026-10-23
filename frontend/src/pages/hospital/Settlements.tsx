import type { ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { SettlementItem } from "@/api";
import { useSettlements } from "@/api/hospital";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { SandboxTag } from "@/components/SandboxTag";
import { Skeleton } from "@/components/ui/skeleton";
import { tibaGridTheme } from "@/lib/aggrid";
import { formatKes } from "@/lib/money";
import { usePageTitle } from "@/lib/usePageTitle";

function formatWhen(value: string | null | undefined, language: string): string {
  if (!value) return "";
  return new Intl.DateTimeFormat(language.startsWith("sw") ? "sw-KE" : "en-KE", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export function HospitalSettlementsPage() {
  const { t, i18n } = useTranslation();
  usePageTitle(t("nav.settlements"));
  const settlements = useSettlements();
  const language = i18n.language;
  const columns = useMemo<ColDef<SettlementItem>[]>(
    () => [
      { field: "requested_at", headerName: t("hospital.settlements.requested"), valueFormatter: (p) => formatWhen(p.value as string, language), minWidth: 180, flex: 1 },
      { field: "case_alias", headerName: t("hospital.settlements.case"), minWidth: 140, flex: 1 },
      { field: "tranche_no", headerName: t("hospital.settlements.tranche"), minWidth: 110, maxWidth: 140, type: "rightAligned" },
      { field: "amount_kes_minor", headerName: t("hospital.settlements.amount"), valueFormatter: (p) => formatKes(p.value as number, language), minWidth: 160, flex: 1, type: "rightAligned", cellClass: "font-semibold" },
      { field: "status", headerName: t("hospital.settlements.status"), valueFormatter: (p) => t(`hospital.settlements.statuses.${p.value as string}`), minWidth: 130, flex: 1 },
      { field: "external_reference", headerName: t("hospital.settlements.reference"), minWidth: 170, flex: 1, cellClass: "font-mono text-xs" },
      { field: "completed_at", headerName: t("hospital.settlements.completed"), valueFormatter: (p) => formatWhen(p.value as string | null, language), minWidth: 180, flex: 1 },
    ],
    [t, language],
  );

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="font-heading text-2xl font-bold">{t("hospital.settlements.title")}</h1>
        <p className="text-muted-foreground">{t("hospital.settlements.intro")}</p>
        <div className="flex flex-wrap items-start gap-2 text-sm text-muted-foreground">
          <SandboxTag />
          <p className="min-w-0 flex-1">{t("tranches.simulated")}</p>
        </div>
      </div>
      {settlements.isPending ? (
        <Skeleton className="h-48" />
      ) : settlements.isError ? (
        <ErrorState error={settlements.error} onRetry={() => void settlements.refetch()} />
      ) : settlements.data.items.length === 0 ? (
        <EmptyState title={t("hospital.settlements.emptyTitle")} description={t("hospital.settlements.emptyBody")} />
      ) : (
        <div role="region" aria-label={t("common.tableOf", { name: t("hospital.settlements.title") })} data-testid="settlements-grid">
          <AgGridReact<SettlementItem> theme={tibaGridTheme} rowData={settlements.data.items} columnDefs={columns} getRowId={(params) => params.data.id} domLayout="autoHeight" defaultColDef={{ sortable: true, resizable: true }} />
        </div>
      )}
    </div>
  );
}

export const Component = HospitalSettlementsPage;
