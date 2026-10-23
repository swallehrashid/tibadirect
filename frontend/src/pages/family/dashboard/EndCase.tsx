import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { FamilyCase } from "@/api";
import { isApiError } from "@/api";
import { familyApi, useCaseMutation } from "@/api/family";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { toast } from "@/components/ui/toaster";

type Reason = "treatment_cancelled" | "patient_deceased" | "other";
const REASONS: Reason[] = ["treatment_cancelled", "patient_deceased", "other"];
const ENDABLE = new Set(["pending_triage", "in_review", "pending_confirmation", "live", "funded"]);

/** Delete a draft, or end a campaign that is under way. Donations stop at once; leftovers follow each donor's choice. */
export function EndCase({ item }: { item: FamilyCase }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [reason, setReason] = useState<Reason>("treatment_cancelled");
  const remove = useCaseMutation(async ({ caseId }: { caseId: string }) => familyApi.deleteDraft(caseId));
  const cancel = useCaseMutation(async ({ caseId, reason: why }: { caseId: string; reason: Reason }) => familyApi.cancel(caseId, why));
  const draft = item.status === "draft";
  if (!draft && !ENDABLE.has(item.status)) return null;
  const error = remove.error ?? cancel.error;

  return (
    <Card className="border-danger-600/30">
      <CardHeader>
        <CardTitle className="font-heading text-lg">{draft ? t("family.end.deleteTitle") : t("family.end.title")}</CardTitle>
        <CardDescription>{draft ? t("family.end.deleteIntro") : t("family.end.intro")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {error ? (
          <Alert tone="danger">
            <AlertDescription>{isApiError(error) && error.status === 409 ? t("family.end.conflict") : t("error.body")}</AlertDescription>
          </Alert>
        ) : null}
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button variant="destructive">{draft ? t("family.end.deleteButton") : t("family.end.button")}</Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{draft ? t("family.end.deleteConfirm") : t("family.end.confirm")}</AlertDialogTitle>
              <AlertDialogDescription>{draft ? t("family.end.deleteWarning") : t("family.end.warning")}</AlertDialogDescription>
            </AlertDialogHeader>
            {draft ? null : (
              <fieldset className="space-y-2">
                <legend className="text-sm font-semibold">{t("family.end.why")}</legend>
                <RadioGroup value={reason} onValueChange={(value) => setReason(value as Reason)}>
                  {REASONS.map((option) => (
                    <div key={option} className="flex items-center gap-3">
                      <RadioGroupItem value={option} id={`reason-${option}`} />
                      <Label htmlFor={`reason-${option}`} className="font-normal">
                        {t(`family.end.reasons.${option}`)}
                      </Label>
                    </div>
                  ))}
                </RadioGroup>
              </fieldset>
            )}
            <AlertDialogFooter>
              <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
              <AlertDialogAction
                destructive
                onClick={async () => {
                  try {
                    if (draft) {
                      await remove.mutateAsync({ caseId: item.id });
                      toast.success(t("family.end.deleted"));
                      navigate("/app");
                    } else {
                      await cancel.mutateAsync({ caseId: item.id, reason });
                      toast.success(t("family.end.ended"));
                    }
                  } catch {
                    /* shown above */
                  }
                }}
              >
                {draft ? t("family.end.deleteButton") : t("family.end.button")}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </CardContent>
    </Card>
  );
}
