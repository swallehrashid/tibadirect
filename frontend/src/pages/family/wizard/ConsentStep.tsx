import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { isApiError } from "@/api";
import { CONSENT_SCOPES, type ConsentScope } from "@/api/enums";
import { familyApi, useCaseConsents, useConsentTexts } from "@/api/family";
import { ConsentCard } from "@/components/ConsentCard";
import { ErrorState } from "@/components/ErrorState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useQueryClient } from "@tanstack/react-query";
import { REQUIRED_SCOPES } from "@/lib/consents";
import type { StepProps } from "./types";

export function ConsentStep({ draft, onNext, onBack }: StepProps) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const language = i18n.language.startsWith("sw") ? "sw" : "en";
  const texts = useConsentTexts(language);
  const given = useCaseConsents(draft?.id ?? null);
  const [wanted, setWanted] = useState<Partial<Record<ConsentScope, boolean>>>({});
  const [guardian, setGuardian] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const active = new Set((given.data ?? []).filter((consent) => consent.withdrawn_at === null).map((consent) => consent.scope));
  const isOn = (scope: ConsentScope) => wanted[scope] ?? active.has(scope);
  const minor = draft?.is_minor ?? false;
  const requiredMet = REQUIRED_SCOPES.every(isOn);
  const guardianOk = !minor || guardian || [...active].length > 0;

  const save = useMutation({
    mutationFn: async () => {
      if (!draft || !texts.data) return;
      for (const scope of CONSENT_SCOPES) {
        const text = texts.data.find((item) => item.scope === scope);
        if (isOn(scope) && !active.has(scope) && text) {
          await familyApi.grantConsent(draft.id, { scope, actor: minor ? "guardian" : "self", wording_version: text.version, language: text.language });
        } else if (!isOn(scope) && active.has(scope) && !REQUIRED_SCOPES.includes(scope)) {
          await familyApi.withdrawConsent(draft.id, scope);
        }
      }
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["family", "cases"] });
      onNext();
    },
    onError: (error) => {
      if (isApiError(error, "consent_wording_outdated")) {
        void texts.refetch();
        setProblem(t("wizard.consent.outdated"));
      } else if (isApiError(error, "guardian_consent_required")) {
        setProblem(t("wizard.consent.guardianNeeded"));
      } else {
        setProblem(t("error.body"));
      }
    },
  });

  if (texts.isPending || (draft && given.isPending)) return <Skeleton className="h-64" />;
  if (texts.isError) return <ErrorState error={texts.error} onRetry={() => void texts.refetch()} />;

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h2 className="font-heading text-xl font-bold">{t("wizard.consent.title")}</h2>
        <p className="text-muted-foreground">{t("wizard.consent.intro")}</p>
      </div>

      {minor ? (
        <div className="flex items-start gap-3 rounded-lg border border-jua-400/60 bg-jua-50 p-4">
          <Checkbox id="guardian" checked={guardian || active.size > 0} disabled={active.size > 0} onCheckedChange={(value) => setGuardian(value === true)} />
          <Label htmlFor="guardian" className="pt-0.5 font-semibold leading-snug">
            {t("wizard.consent.guardianConfirm")}
          </Label>
        </div>
      ) : null}

      <div className="space-y-4">
        {CONSENT_SCOPES.map((scope) => {
          const text = texts.data.find((item) => item.scope === scope);
          if (!text) return null;
          const locked = active.has(scope) && REQUIRED_SCOPES.includes(scope);
          return (
            <ConsentCard
              key={scope}
              title={text.title}
              text={text.body}
              required={REQUIRED_SCOPES.includes(scope)}
              checked={isOn(scope)}
              onCheckedChange={(checked) => setWanted((current) => ({ ...current, [scope]: checked }))}
              guardian={minor}
              disabled={locked || save.isPending || (minor && !guardianOk)}
              footer={locked ? <p className="text-sm text-muted-foreground">{t("wizard.consent.locked")}</p> : null}
            />
          );
        })}
      </div>

      {problem ? (
        <Alert tone="danger">
          <AlertDescription>{problem}</AlertDescription>
        </Alert>
      ) : null}
      {!requiredMet ? <p className="text-sm text-muted-foreground">{t("wizard.consent.requiredNote")}</p> : null}

      <div className="flex justify-between gap-3">
        <Button variant="outline" size="lg" onClick={onBack} disabled={save.isPending}>
          {t("common.back")}
        </Button>
        <Button
          size="lg"
          disabled={!requiredMet || !guardianOk || save.isPending}
          onClick={() => {
            setProblem(null);
            save.mutate();
          }}
        >
          {save.isPending ? t("common.loading") : t("common.next")}
        </Button>
      </div>
    </div>
  );
}
