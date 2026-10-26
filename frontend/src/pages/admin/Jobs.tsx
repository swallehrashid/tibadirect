import { useMutation, useQueryClient } from "@tanstack/react-query";
import { HeartPulse, RotateCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { isApiError } from "@/api";
import { adminApi, useAdminJobs } from "@/api/admin";
import { EmptyState } from "@/components/EmptyState";
import { ScrollRegion } from "@/components/ScrollRegion";
import { ErrorState } from "@/components/ErrorState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toaster";
import { usePageTitle } from "@/lib/usePageTitle";

export function JobsPage() {
  const { t, i18n } = useTranslation();
  usePageTitle(t("nav.jobs"));
  const queryClient = useQueryClient();
  const jobs = useAdminJobs();
  const retry = useMutation({
    mutationFn: (jobId: string) => adminApi.retryJob(jobId),
    onSuccess: async () => {
      toast.success(t("admin.jobs.retried"));
      await queryClient.invalidateQueries({ queryKey: ["admin"] });
    },
  });
  const when = (value: string | null) => (value ? new Intl.DateTimeFormat(i18n.language.startsWith("sw") ? "sw-KE" : "en-KE", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value)) : "—");

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="font-heading text-2xl font-bold">{t("admin.jobs.title")}</h1>
        <p className="text-muted-foreground">{t("admin.jobs.intro")}</p>
      </div>

      {jobs.isPending ? (
        <Skeleton className="h-48" />
      ) : jobs.isError ? (
        <ErrorState error={jobs.error} onRetry={() => void jobs.refetch()} />
      ) : (
        <>
          <Alert tone={jobs.data.scheduler.healthy ? "success" : "danger"}>
            <HeartPulse />
            <AlertDescription>
              {jobs.data.scheduler.healthy ? t("admin.jobs.schedulerOk") : t("admin.jobs.schedulerDown")}
              {jobs.data.scheduler.last_heartbeat ? ` · ${t("admin.jobs.heartbeat", { when: when(jobs.data.scheduler.last_heartbeat) })}` : ""}
            </AlertDescription>
          </Alert>

          <Card>
            <CardHeader>
              <CardTitle className="font-heading text-lg">{t("admin.jobs.queues")}</CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollRegion label={t("admin.jobs.queues")}>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th scope="col" className="py-2 pr-3 font-semibold">
                      {t("admin.jobs.queue")}
                    </th>
                    {(["queued", "started", "scheduled", "failed"] as const).map((column) => (
                      <th key={column} scope="col" className="px-3 py-2 text-right font-semibold">
                        {t(`admin.jobs.columns.${column}`)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {jobs.data.queues.map((queue) => (
                    <tr key={queue.name} className="border-b border-border last:border-0">
                      <th scope="row" className="py-2 pr-3 text-left font-semibold">
                        {queue.name}
                      </th>
                      <td className="px-3 py-2 text-right">{queue.queued}</td>
                      <td className="px-3 py-2 text-right">{queue.started}</td>
                      <td className="px-3 py-2 text-right">{queue.scheduled}</td>
                      <td className="px-3 py-2 text-right">{queue.failed > 0 ? <Badge tone="danger">{queue.failed}</Badge> : 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </ScrollRegion>
            </CardContent>
          </Card>

          <section aria-labelledby="failed" className="space-y-3">
            <h2 id="failed" className="font-heading text-xl font-bold">
              {t("admin.jobs.failed")} ({jobs.data.failed_jobs.length})
            </h2>
            {retry.isError ? (
              <Alert tone="danger">
                <AlertDescription>{isApiError(retry.error) && retry.error.status === 409 ? t("admin.jobs.notFailed") : t("error.body")}</AlertDescription>
              </Alert>
            ) : null}
            {jobs.data.failed_jobs.length === 0 ? (
              <EmptyState title={t("admin.jobs.noneTitle")} description={t("admin.jobs.noneBody")} />
            ) : (
              <ul className="space-y-3">
                {jobs.data.failed_jobs.map((job) => (
                  <li key={job.id}>
                    <Card className="flex flex-wrap items-start justify-between gap-3 p-4" data-testid="failed-job">
                      <div className="min-w-0 space-y-1">
                        <p className="font-mono text-sm font-semibold">{job.function}</p>
                        <p className="text-sm text-muted-foreground">
                          {t("admin.jobs.jobMeta", { queue: job.queue, when: when(job.failed_at), retries: job.retries_left })}
                        </p>
                        <p className="break-words text-sm text-danger-700">{job.error}</p>
                        {job.request_id ? <p className="text-xs text-muted-foreground">{t("error.reference", { id: job.request_id })}</p> : null}
                      </div>
                      <Button variant="outline" size="sm" disabled={retry.isPending} onClick={() => retry.mutate(job.id)}>
                        <RotateCw /> {t("admin.jobs.retry")}
                        <span className="sr-only"> {job.function}</span>
                      </Button>
                    </Card>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}

export const Component = JobsPage;
