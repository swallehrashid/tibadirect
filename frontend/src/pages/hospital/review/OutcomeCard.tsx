import { CheckCircle2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** What the hospital just did: kept on the page after the data refreshes, so the result is not lost. */
export type Outcome = { kind: "confirmed" | "queried" | "rejected" | "second_pending" | "approved" };

export function OutcomeCard({ outcome }: { outcome: Outcome }) {
  const { t } = useTranslation();
  const approved = outcome.kind === "approved";
  return (
    <Card className="border-uzima-600/40" role="status">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 font-heading text-lg text-uzima-700">
          <CheckCircle2 className="size-5" aria-hidden="true" /> {approved ? t("hospital.second.approved") : t("hospital.decision.recorded")}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p>{approved ? t("hospital.second.approvedBody") : outcome.kind === "second_pending" ? t("hospital.decision.resultSecond") : t(`hospital.decision.result.${outcome.kind}`)}</p>
      </CardContent>
    </Card>
  );
}
