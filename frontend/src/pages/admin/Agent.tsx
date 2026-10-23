import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, CircleAlert, Wrench, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { isApiError, usePublicConfig, type AgentAction, type AgentAnswer } from "@/api";
import { adminApi, useAdminCases, useAgentActions } from "@/api/admin";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/input";
import { toast } from "@/components/ui/toaster";
import { usePageTitle } from "@/lib/usePageTitle";

const NONE = "none";
const TONE = { proposed: "pending", approved: "info", executed: "confirmed", rejected: "neutral", failed: "danger" } as const;

function askErrorMessage(error: unknown, t: (key: string) => string): string {
  if (isApiError(error)) {
    if (error.status === 503) return t("admin.agent.errors.noModel");
    if (error.status === 502) return t("admin.agent.errors.unreachable");
    if (error.status === 429) return t("admin.agent.errors.tooMany");
    if (error.status === 404) return t("admin.agent.errors.off");
  }
  return t("error.body");
}

function ActionCard({ action }: { action: AgentAction }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [approving, setApproving] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["admin"] });
  const approve = useMutation({
    mutationFn: () => adminApi.approveAction(action.id),
    onSuccess: async (done) => {
      toast[done.status === "failed" ? "error" : "success"](done.status === "failed" ? t("admin.agent.action.failedToast") : t("admin.agent.action.doneToast"));
      await refresh();
    },
  });
  const reject = useMutation({
    mutationFn: () => adminApi.rejectAction(action.id, note.trim()),
    onSuccess: async () => {
      toast.success(t("admin.agent.action.rejectedToast"));
      setRejecting(false);
      setNote("");
      await refresh();
    },
  });
  const open = action.status === "proposed";
  const conflict = [approve.error, reject.error].some((error) => isApiError(error) && error.status === 409);

  return (
    <Card className="space-y-3 p-4" data-testid="agent-action">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="brand">{t(`admin.agent.action.types.${action.action_type}`)}</Badge>
        <Badge tone={TONE[action.status]}>{t(`admin.agent.action.statuses.${action.status}`)}</Badge>
        {action.case_alias ? <span className="font-semibold">{action.case_alias}</span> : null}
      </div>
      <p>{action.rationale}</p>
      <pre className="max-h-40 overflow-auto rounded-md bg-muted p-2 text-xs">{JSON.stringify(action.payload, null, 2)}</pre>
      {action.error ? (
        <Alert tone="danger">
          <CircleAlert />
          <AlertDescription>{action.error}</AlertDescription>
        </Alert>
      ) : null}
      {action.decided_by ? <p className="text-sm text-muted-foreground">{t("admin.agent.action.decidedBy", { name: action.decided_by })}</p> : null}
      {conflict ? (
        <Alert tone="warning">
          <AlertDescription>{t("admin.agent.action.conflict")}</AlertDescription>
        </Alert>
      ) : null}
      {open ? (
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => setApproving(true)} disabled={approve.isPending}>
            <Check /> {t("admin.agent.action.approve")}
            <span className="sr-only">: {t(`admin.agent.action.types.${action.action_type}`)} {action.case_alias}</span>
          </Button>
          <Button variant="outline" onClick={() => setRejecting(true)}>
            <X /> {t("admin.agent.action.reject")}
            <span className="sr-only">: {t(`admin.agent.action.types.${action.action_type}`)} {action.case_alias}</span>
          </Button>
        </div>
      ) : null}

      <AlertDialog open={approving} onOpenChange={setApproving}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("admin.agent.action.approveTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{t(`admin.agent.action.approveBody.${action.action_type}`)}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction destructive={action.action_type !== "payout"} onClick={() => approve.mutate()}>
              {t("admin.agent.action.approve")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={rejecting} onOpenChange={setRejecting}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("admin.agent.action.rejectTitle")}</DialogTitle>
            <DialogDescription>{t("admin.agent.action.rejectBody")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor={`reject-${action.id}`}>{t("admin.review.note")}</Label>
            <Textarea id={`reject-${action.id}`} value={note} maxLength={500} onChange={(event) => setNote(event.target.value)} />
            <p className="text-sm text-muted-foreground">{t("admin.review.noteHelp", { count: note.trim().length })}</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRejecting(false)}>
              {t("common.cancel")}
            </Button>
            <Button disabled={note.trim().length < 10 || reject.isPending} onClick={() => reject.mutate()}>
              {t("admin.agent.action.reject")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function Answer({ answer }: { answer: AgentAnswer }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 font-heading text-lg">
          <Bot className="size-5 text-primary" aria-hidden="true" /> {t("admin.agent.answer")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4" aria-live="polite">
        <p className="whitespace-pre-wrap">{answer.answer}</p>
        {answer.stopped_early ? (
          <Alert tone="warning">
            <AlertDescription>{t("admin.agent.stoppedEarly")}</AlertDescription>
          </Alert>
        ) : null}
        <details className="rounded-lg border border-border p-3">
          <summary className="cursor-pointer font-semibold">{t("admin.agent.trace", { count: answer.tool_calls.length })}</summary>
          <ol className="mt-3 space-y-2">
            {answer.tool_calls.map((call, index) => (
              <li key={index} className="rounded-md bg-muted p-2 text-sm">
                <p className="flex items-center gap-2 font-mono font-semibold">
                  <Wrench className="size-3.5" aria-hidden="true" /> {call.name}
                  <Badge tone={call.ok ? "confirmed" : "danger"}>{call.ok ? t("admin.agent.ok") : t("admin.agent.failed")}</Badge>
                </p>
                <p className="break-all font-mono text-xs text-muted-foreground">{JSON.stringify(call.input)}</p>
                <p>{call.summary}</p>
              </li>
            ))}
          </ol>
        </details>
        {answer.proposed_actions.length > 0 ? (
          <div className="space-y-3">
            <h3 className="font-heading text-base font-bold">{t("admin.agent.proposed")}</h3>
            {answer.proposed_actions.map((action) => (
              <ActionCard key={action.id} action={action} />
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function AgentPage() {
  const { t } = useTranslation();
  usePageTitle(t("nav.agent"));
  const { data: config, isPending: configPending } = usePublicConfig();
  const [question, setQuestion] = useState("");
  const [caseId, setCaseId] = useState<string>(NONE);
  const [filter, setFilter] = useState<"proposed" | "all">("proposed");
  const on = config?.features.agent === true;
  // Nothing is asked of the agent's routes while it is switched off (they would only answer 404).
  const cases = useAdminCases({ status: null, q: "" }, 1, on);
  const actions = useAgentActions(filter === "proposed" ? "proposed" : null, on);
  const ask = useMutation({ mutationFn: () => adminApi.ask(question.trim(), caseId === NONE ? null : caseId) });

  if (configPending) return <Skeleton className="h-48" />;
  if (!config?.features.agent) return <EmptyState title={t("admin.agent.offTitle")} description={t("admin.agent.offBody")} />;

  return (
    <div className="space-y-8">
      <div className="space-y-1">
        <h1 className="font-heading text-2xl font-bold">{t("admin.agent.title")}</h1>
        <p className="text-muted-foreground">{t("admin.agent.intro")}</p>
      </div>

      <form
        className="space-y-4 rounded-lg border border-border bg-card p-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (question.trim().length >= 3) ask.mutate();
        }}
      >
        <div className="space-y-2">
          <Label htmlFor="agent-question">{t("admin.agent.question")}</Label>
          <Textarea id="agent-question" value={question} maxLength={1000} onChange={(event) => setQuestion(event.target.value)} placeholder={t("admin.agent.placeholder")} />
        </div>
        <div className="max-w-sm space-y-2">
          <Label htmlFor="agent-case">{t("admin.agent.aboutCase")}</Label>
          <Select value={caseId} onValueChange={setCaseId}>
            <SelectTrigger id="agent-case">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>{t("admin.agent.noCase")}</SelectItem>
              {(cases.data?.items ?? []).map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.alias} · {item.hospital_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <p className="text-sm text-muted-foreground">{t("admin.agent.safety")}</p>
        {ask.isError ? (
          <Alert tone="danger">
            <AlertDescription>{askErrorMessage(ask.error, t)}</AlertDescription>
          </Alert>
        ) : null}
        <Button type="submit" disabled={question.trim().length < 3 || ask.isPending}>
          {ask.isPending ? t("admin.agent.thinking") : t("admin.agent.ask")}
        </Button>
      </form>

      {ask.data ? <Answer answer={ask.data} /> : null}

      <section aria-labelledby="actions" className="space-y-3">
        <h2 id="actions" className="font-heading text-xl font-bold">
          {t("admin.agent.actions")}
        </h2>
        <Tabs value={filter} onValueChange={(value) => setFilter(value as "proposed" | "all")}>
          <TabsList aria-label={t("admin.review.filter")}>
            <TabsTrigger value="proposed">{t("admin.agent.waiting")}</TabsTrigger>
            <TabsTrigger value="all">{t("admin.review.tabs.all")}</TabsTrigger>
          </TabsList>
        </Tabs>
        {actions.isPending ? (
          <Skeleton className="h-24" />
        ) : actions.isError ? (
          <ErrorState error={actions.error} onRetry={() => void actions.refetch()} />
        ) : actions.data.items.length === 0 ? (
          <EmptyState title={t("admin.agent.noActions")} description={t("admin.agent.noActionsBody")} />
        ) : (
          <ul className="space-y-3">
            {actions.data.items.map((action) => (
              <li key={action.id}>
                <ActionCard action={action} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export const Component = AgentPage;
