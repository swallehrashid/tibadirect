import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { FamilyCase } from "@/api";
import { CONSENT_SCOPES, type ConsentScope } from "@/api/enums";
import { familyApi, useCaseMutation, useConsentTexts } from "@/api/family";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/** The consents the family has given, each with a way to take it back and what that would change. */
export function ConsentsCard({ item }: { item: FamilyCase }) {
  const { t, i18n } = useTranslation();
  const texts = useConsentTexts(i18n.language.startsWith("sw") ? "sw" : "en");
  const [target, setTarget] = useState<ConsentScope | null>(null);
  const withdraw = useCaseMutation(async ({ caseId, scope }: { caseId: string; scope: ConsentScope }) => familyApi.withdrawConsent(caseId, scope));
  const active = CONSENT_SCOPES.filter((scope) => item.consents.includes(scope));
  const open = item.status !== "closed" && item.status !== "cancelled";
  const confirmed = item.trust_level === "hospital_confirmed";
  const consequence = (scope: ConsentScope) => (scope === "hospital_verification" && !confirmed ? t("family.consents.consequence.hospital_verification_unconfirmed") : t(`family.consents.consequence.${scope}`));
  const titleOf = (scope: string) => texts.data?.find((entry) => entry.scope === scope)?.title ?? scope;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-lg">{t("family.consents.title")}</CardTitle>
        <CardDescription>{t("family.consents.intro")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {active.length === 0 ? <p className="text-sm text-muted-foreground">{t("family.consents.none")}</p> : null}
        <ul className="space-y-2">
          {active.map((scope) => (
            <li key={scope} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border p-3">
              <span className="flex items-center gap-2 font-semibold">
                {titleOf(scope)} <Badge tone="confirmed">{t("wizard.review.given")}</Badge>
              </span>
              {open ? (
                <Button variant="outline" size="sm" onClick={() => setTarget(scope)}>
                  {t("consent.withdraw")}
                  <span className="sr-only">: {titleOf(scope)}</span>
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
        {withdraw.isError ? (
          <Alert tone="danger">
            <AlertDescription>{t("error.body")}</AlertDescription>
          </Alert>
        ) : null}
      </CardContent>
      <AlertDialog open={target !== null} onOpenChange={(next) => !next && setTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("family.consents.withdrawTitle", { name: target ? titleOf(target) : "" })}</AlertDialogTitle>
            <AlertDialogDescription>{target ? consequence(target) : ""}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              destructive
              onClick={() => {
                if (target) withdraw.mutate({ caseId: item.id, scope: target });
                setTarget(null);
              }}
            >
              {t("consent.withdraw")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
