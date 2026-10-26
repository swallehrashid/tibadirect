import type { ColDef } from "ag-grid-community";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { isApiError, type ReviewItem, type ReviewResolution } from "@/api";
import { adminApi, useReviewItems } from "@/api/admin";
import { DataGrid } from "@/components/DataGrid";
import { EmptyState } from "@/components/EmptyState";
import { Pager } from "@/components/Pager";
import { ErrorState } from "@/components/ErrorState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Skeleton } from "@/components/ui/skeleton";
import { SegmentedControl } from "@/components/SegmentedControl";
import { Textarea } from "@/components/ui/input";
import { toast } from "@/components/ui/toaster";
import { usePageTitle } from "@/lib/usePageTitle";

const RESOLUTIONS: ReviewResolution[] = ["approved_for_confirmation", "kept_unverified", "case_cancelled", "no_action"];
type Filter = "open" | "resolved" | "all";

function formatWhen(value: string | null, language: string) {
  if (!value) return "";
  return new Intl.DateTimeFormat(language.startsWith("sw") ? "sw-KE" : "en-KE", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function Payload({ payload }: { payload: Record<string, unknown> }) {
  const entries = Object.entries(payload);
  if (entries.length === 0) return null;
  return (
    <dl className="max-h-48 space-y-1 overflow-y-auto rounded-md bg-muted p-3 text-sm">
      {entries.map(([key, value]) => (
        <div key={key} className="flex flex-col sm:flex-row sm:gap-3">
          <dt className="text-muted-foreground sm:w-40 sm:shrink-0">{key}</dt>
          <dd className="break-words font-mono text-xs">{typeof value === "string" ? value : JSON.stringify(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function ResolveDialog({ item, onClose }: { item: ReviewItem | null; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [resolution, setResolution] = useState<ReviewResolution>("approved_for_confirmation");
  const [note, setNote] = useState("");
  const resolve = useMutation({
    mutationFn: () => adminApi.resolveReview(item!.id, resolution, note.trim()),
    onSuccess: async () => {
      toast.success(t("admin.review.resolved"));
      setNote("");
      onClose();
      await queryClient.invalidateQueries({ queryKey: ["admin"] });
    },
  });
  const noteOk = note.trim().length >= 10 && note.trim().length <= 500;
  const error = resolve.error;
  const problem = isApiError(error) ? (error.status === 409 ? t("admin.review.conflict") : error.status === 422 ? t("admin.review.noteLength") : t("error.body")) : error ? t("error.body") : null;

  return (
    <Dialog open={item !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-xl">
        {item ? (
          <>
            <DialogHeader>
              <DialogTitle>{item.status === "open" ? t("admin.review.resolveTitle") : t("admin.review.detailsTitle")}</DialogTitle>
              <DialogDescription>
                {t(`admin.review.sources.${item.source}`)} · {item.case_alias} ·{" "}
                <Link to={`/admin/cases/${item.case_id}`} className="font-semibold text-primary underline-offset-4 hover:underline">
                  {t("admin.review.openCase")}
                </Link>
              </DialogDescription>
            </DialogHeader>
            <p className="font-semibold">{item.reason}</p>
            <Payload payload={item.payload} />
            {item.status === "resolved" ? (
              <div className="space-y-1 rounded-md border border-border p-3 text-sm">
                <p className="font-semibold">{item.resolution ? t(`admin.review.resolutions.${item.resolution}.label`) : ""}</p>
                {item.resolution_note ? <p>{item.resolution_note}</p> : null}
              </div>
            ) : (
              <form
                className="space-y-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (noteOk) resolve.mutate();
                }}
              >
                <fieldset className="space-y-2">
                  <legend className="text-sm font-semibold">{t("admin.review.decision")}</legend>
                  <RadioGroup value={resolution} onValueChange={(value) => setResolution(value as ReviewResolution)} className="gap-2">
                    {RESOLUTIONS.map((option) => (
                      <div key={option} className="flex items-start gap-3 rounded-lg border border-border p-3">
                        <RadioGroupItem value={option} id={`resolution-${option}`} className="mt-0.5" />
                        <Label htmlFor={`resolution-${option}`} className="space-y-0.5 font-normal">
                          <span className="block font-semibold">{t(`admin.review.resolutions.${option}.label`)}</span>
                          <span className="block text-sm text-muted-foreground">{t(`admin.review.resolutions.${option}.body`)}</span>
                        </Label>
                      </div>
                    ))}
                  </RadioGroup>
                </fieldset>
                <div className="space-y-2">
                  <Label htmlFor="resolution-note">{t("admin.review.note")}</Label>
                  <Textarea id="resolution-note" value={note} maxLength={500} aria-describedby="note-count" onChange={(event) => setNote(event.target.value)} />
                  <p id="note-count" className="text-sm text-muted-foreground">
                    {t("admin.review.noteHelp", { count: note.trim().length })}
                  </p>
                </div>
                {problem ? (
                  <Alert tone="danger">
                    <AlertDescription>{problem}</AlertDescription>
                  </Alert>
                ) : null}
                <DialogFooter>
                  <Button type="button" variant="outline" onClick={onClose}>
                    {t("common.cancel")}
                  </Button>
                  <Button type="submit" disabled={!noteOk || resolve.isPending}>
                    {resolve.isPending ? t("common.loading") : t("admin.review.submit")}
                  </Button>
                </DialogFooter>
              </form>
            )}
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

export function ReviewQueuePage() {
  const { t, i18n } = useTranslation();
  usePageTitle(t("nav.review"));
  const [filter, setFilter] = useState<Filter>("open");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<ReviewItem | null>(null);
  const items = useReviewItems(filter === "all" ? null : filter, page);
  const language = i18n.language;
  const columns = useMemo<ColDef<ReviewItem>[]>(
    () => [
      { field: "created_at", headerName: t("admin.review.columns.created"), valueFormatter: (p) => formatWhen(p.value as string, language), minWidth: 170, flex: 1 },
      { field: "case_alias", headerName: t("admin.review.columns.case"), minWidth: 140, flex: 1, cellClass: "font-semibold" },
      { field: "source", headerName: t("admin.review.columns.source"), valueFormatter: (p) => t(`admin.review.sources.${p.value as string}`), minWidth: 190, flex: 1 },
      { field: "reason", headerName: t("admin.review.columns.reason"), minWidth: 280, flex: 3 },
      { field: "status", headerName: t("admin.review.columns.status"), valueFormatter: (p) => t(`admin.review.status.${p.value as string}`), minWidth: 120, flex: 0.8 },
      {
        colId: "open",
        headerName: t("admin.actions"),
        sortable: false,
        pinned: "right",
        resizable: false,
        minWidth: 120,
        maxWidth: 140,
        cellRenderer: (p: { data?: ReviewItem }) =>
          p.data ? (
            <Button variant="outline" size="sm" onClick={() => setSelected(p.data!)}>
              {p.data.status === "open" ? t("admin.review.resolve") : t("admin.review.view")}
              <span className="sr-only">: {p.data.case_alias}</span>
            </Button>
          ) : null,
      },
    ],
    [t, language],
  );
  const totalPages = items.data ? Math.max(1, Math.ceil(items.data.total / 50)) : 1;

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="font-heading text-2xl font-bold">{t("admin.review.title")}</h1>
        <p className="text-muted-foreground">{t("admin.review.intro")}</p>
      </div>
      <SegmentedControl
        label={t("admin.review.filter")}
        value={filter}
        onChange={(value: Filter) => {
          setFilter(value);
          setPage(1);
        }}
        options={(["open", "resolved", "all"] as const).map((value) => ({ value, label: t(`admin.review.tabs.${value}`) }))}
      />

      {items.isPending ? (
        <Skeleton className="h-48" />
      ) : items.isError ? (
        <ErrorState error={items.error} onRetry={() => void items.refetch()} />
      ) : items.data.items.length === 0 ? (
        <EmptyState title={t("admin.review.emptyTitle")} description={t("admin.review.emptyBody")} />
      ) : (
        <>
          <DataGrid rows={items.data.items} columns={columns} getRowId={(row) => row.id} label={t("admin.review.title")} onRowClicked={setSelected} />
          <p className="text-sm text-muted-foreground">{t("admin.review.hint")}</p>
          <Pager page={page} totalPages={totalPages} onPage={setPage} />
        </>
      )}
      <ResolveDialog key={selected?.id ?? "none"} item={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

export const Component = ReviewQueuePage;
