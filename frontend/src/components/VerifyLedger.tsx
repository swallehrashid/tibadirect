import { ShieldCheck, ShieldX } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLedgerVerification } from "@/api";
import { ErrorState } from "@/components/ErrorState";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

/** Asks the server to recompute the case's fingerprint chain and says plainly what came back. */
export function VerifyLedger({ slug }: { slug: string }) {
  const { t } = useTranslation();
  const check = useLedgerVerification(slug);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" disabled={check.isFetching} onClick={() => void check.refetch()}>
          <ShieldCheck /> {check.isFetching ? t("ledger.verifying") : t("ledger.verify")}
        </Button>
        <p className="text-sm text-muted-foreground">{t("ledger.verifyHelp")}</p>
      </div>
      <div aria-live="polite">
        {check.isError ? (
          <ErrorState error={check.error} onRetry={() => void check.refetch()} />
        ) : check.data ? (
          check.data.valid ? (
            <Alert tone="success">
              <ShieldCheck />
              <div>
                <AlertTitle>{t("ledger.valid.title")}</AlertTitle>
                <AlertDescription>{t("ledger.valid.body", { count: check.data.entries_checked, hash: check.data.last_hash.slice(0, 12) })}</AlertDescription>
              </div>
            </Alert>
          ) : (
            <Alert tone="danger">
              <ShieldX />
              <div>
                <AlertTitle>{t("ledger.invalid.title")}</AlertTitle>
                <AlertDescription>{t("ledger.invalid.body")}</AlertDescription>
              </div>
            </Alert>
          )
        ) : null}
      </div>
    </div>
  );
}
