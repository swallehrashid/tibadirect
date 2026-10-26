import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { isApiError, type FamilyCase } from "@/api";
import { familyApi } from "@/api/family";
import { RiskBand } from "@/components/RiskBand";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/input";
import { toast } from "@/components/ui/toaster";

const REVIEWABLE = new Set(["pending_triage", "in_review", "pending_confirmation", "live", "funded"]);

function RequestReview({ caseId }: { caseId: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const send = useMutation({
    mutationFn: () => familyApi.requestReview(caseId, message.trim()),
    onSuccess: async () => {
      toast.success(t("family.review.sent"));
      setOpen(false);
      setMessage("");
      await queryClient.invalidateQueries({ queryKey: ["family", "cases"] });
    },
  });
  const tooShort = message.trim().length < 10;
  const error = send.error;
  const text = isApiError(error, "review_already_requested") ? t("family.review.already") : isApiError(error, "case_not_reviewable") ? t("family.review.notNow") : isApiError(error, "message_length") ? t("family.review.length") : error ? t("error.body") : null;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">{t("family.review.button")}</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("family.review.title")}</DialogTitle>
          <DialogDescription>{t("family.review.intro")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="review-message">{t("family.review.label")}</Label>
          <Textarea id="review-message" value={message} maxLength={500} onChange={(event) => setMessage(event.target.value)} />
          <p className="text-sm text-muted-foreground">{t("family.review.hint", { count: message.trim().length })}</p>
        </div>
        {text ? (
          <Alert tone="danger">
            <AlertDescription>{text}</AlertDescription>
          </Alert>
        ) : null}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            {t("common.cancel")}
          </Button>
          <Button disabled={tooShort || send.isPending} onClick={() => send.mutate()}>
            {send.isPending ? t("common.loading") : t("family.review.send")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** What the automatic checks noticed, in plain words. They explain; a person decides. */
export function TriageCard({ item }: { item: FamilyCase }) {
  const { t, i18n } = useTranslation();
  const triage = item.triage;
  const reasons = triage ? (i18n.language.startsWith("sw") ? triage.reasons_sw : triage.reasons_en) : [];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-lg">{t("family.triage.title")}</CardTitle>
        <CardDescription>{t("family.triage.note")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {triage ? (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <RiskBand band={triage.risk_band} />
              <span className="text-sm text-muted-foreground">{t(`family.triage.route.${triage.route}`)}</span>
            </div>
            {reasons.length > 0 ? (
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">{t("family.triage.noReasons")}</p>
            )}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">{item.status === "draft" ? t("family.triage.notYetDraft") : t("family.triage.notYet")}</p>
        )}
        {REVIEWABLE.has(item.status) ? <RequestReview caseId={item.id} /> : null}
      </CardContent>
    </Card>
  );
}
