import type { ColDef } from "ag-grid-community";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { AuditEntry } from "@/api";
import { ADMIN_PAGE_SIZE, useAuditLog } from "@/api/admin";
import { DataGrid } from "@/components/DataGrid";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { usePageTitle } from "@/lib/usePageTitle";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function when(value: string, language: string) {
  return new Intl.DateTimeFormat(language.startsWith("sw") ? "sw-KE" : "en-KE", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
}

export function AuditLogPage() {
  const { t, i18n } = useTranslation();
  usePageTitle(t("nav.audit"));
  const [targetType, setTargetType] = useState("");
  const [targetId, setTargetId] = useState("");
  const [applied, setApplied] = useState({ targetType: "", targetId: "" });
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<AuditEntry | null>(null);
  const idInvalid = targetId.trim() !== "" && !UUID.test(targetId.trim());
  const log = useAuditLog(applied, page);
  const language = i18n.language;
  const columns = useMemo<ColDef<AuditEntry>[]>(
    () => [
      { field: "created_at", headerName: t("admin.audit.columns.when"), valueFormatter: (p) => when(p.value as string, language), minWidth: 210, flex: 1.2 },
      { field: "actor_type", headerName: t("admin.audit.columns.actor"), minWidth: 110, flex: 0.7 },
      { field: "action", headerName: t("admin.audit.columns.action"), minWidth: 210, flex: 1.5, cellClass: "font-mono text-xs font-semibold" },
      { field: "target_type", headerName: t("admin.audit.columns.target"), minWidth: 120, flex: 0.8 },
      { field: "target_id", headerName: t("admin.audit.columns.targetId"), minWidth: 130, flex: 1, valueFormatter: (p) => (p.value ? String(p.value).slice(0, 8) + "…" : ""), cellClass: "font-mono text-xs" },
      { field: "details", headerName: t("admin.audit.columns.details"), minWidth: 260, flex: 2, valueFormatter: (p) => JSON.stringify(p.value), cellClass: "font-mono text-xs" },
      {
        colId: "open",
        headerName: "",
        sortable: false,
        pinned: "right",
        resizable: false,
        minWidth: 110,
        maxWidth: 130,
        cellRenderer: (p: { data?: AuditEntry }) =>
          p.data ? (
            <Button variant="outline" size="sm" onClick={() => setSelected(p.data!)}>
              {t("admin.audit.view")}
              <span className="sr-only">: {p.data.action}</span>
            </Button>
          ) : null,
      },
    ],
    [t, language],
  );
  const totalPages = log.data ? Math.max(1, Math.ceil(log.data.total / ADMIN_PAGE_SIZE)) : 1;

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="font-heading text-2xl font-bold">{t("admin.audit.title")}</h1>
        <p className="text-muted-foreground">{t("admin.audit.intro")}</p>
      </div>

      <form
        role="search"
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (idInvalid) return;
          setApplied({ targetType: targetType.trim(), targetId: targetId.trim() });
          setPage(1);
        }}
      >
        <div className="w-48 space-y-2">
          <Label htmlFor="target-type">{t("admin.audit.targetType")}</Label>
          <Input id="target-type" value={targetType} placeholder="case" onChange={(event) => setTargetType(event.target.value)} />
        </div>
        <div className="min-w-64 flex-1 space-y-2">
          <Label htmlFor="target-id">{t("admin.audit.targetIdFull")}</Label>
          <Input id="target-id" value={targetId} aria-invalid={idInvalid || undefined} aria-describedby={idInvalid ? "target-id-error" : undefined} onChange={(event) => setTargetId(event.target.value)} />
          {idInvalid ? (
            <p id="target-id-error" role="alert" className="text-sm font-semibold text-danger-700">
              {t("admin.audit.idInvalid")}
            </p>
          ) : null}
        </div>
        <Button type="submit" variant="outline">
          {t("admin.audit.filter")}
        </Button>
        {applied.targetType || applied.targetId ? (
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setTargetType("");
              setTargetId("");
              setApplied({ targetType: "", targetId: "" });
              setPage(1);
            }}
          >
            {t("admin.audit.clear")}
          </Button>
        ) : null}
      </form>

      {log.isPending ? (
        <Skeleton className="h-48" />
      ) : log.isError ? (
        <ErrorState error={log.error} onRetry={() => void log.refetch()} />
      ) : log.data.items.length === 0 ? (
        <EmptyState title={t("admin.audit.emptyTitle")} description={t("admin.audit.emptyBody")} />
      ) : (
        <>
          <DataGrid rows={log.data.items} columns={columns} getRowId={(row) => row.id} label={t("admin.audit.title")} onRowClicked={setSelected} />
          <p className="text-sm text-muted-foreground">{t("admin.audit.hint")}</p>
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground" aria-live="polite">
              {t("admin.audit.count", { count: log.data.total })}
            </p>
            {totalPages > 1 ? (
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                  {t("cases.previous")}
                </Button>
                <span className="text-sm text-muted-foreground">{t("cases.pageOf", { page, total: totalPages })}</span>
                <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
                  {t("cases.next")}
                </Button>
              </div>
            ) : null}
          </div>
        </>
      )}

      <Dialog open={selected !== null} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className="max-w-xl">
          {selected ? (
            <>
              <DialogHeader>
                <DialogTitle className="font-mono text-base">{selected.action}</DialogTitle>
                <DialogDescription>{when(selected.created_at, language)}</DialogDescription>
              </DialogHeader>
              <dl className="space-y-1 text-sm">
                <div className="flex gap-3">
                  <dt className="w-28 shrink-0 text-muted-foreground">{t("admin.audit.columns.actor")}</dt>
                  <dd>{selected.actor_type}</dd>
                </div>
                <div className="flex gap-3">
                  <dt className="w-28 shrink-0 text-muted-foreground">{t("admin.audit.columns.target")}</dt>
                  <dd className="break-all font-mono text-xs">
                    {selected.target_type} {selected.target_id ?? ""}
                  </dd>
                </div>
                {selected.request_id ? (
                  <div className="flex gap-3">
                    <dt className="w-28 shrink-0 text-muted-foreground">{t("admin.audit.requestId")}</dt>
                    <dd className="break-all font-mono text-xs">{selected.request_id}</dd>
                  </div>
                ) : null}
              </dl>
              <pre className="max-h-72 overflow-auto rounded-md bg-muted p-3 text-xs">{JSON.stringify(selected.details, null, 2)}</pre>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}

export const Component = AuditLogPage;
