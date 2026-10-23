import { HandHeart, Lock } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useFx, usePublicConfig, type PublicCase } from "@/api";
import { captureOrder, createOrder, type NewDonation } from "@/api/donations";
import type { ExcessChoice as ExcessChoiceValue } from "@/api/enums";
import { ExcessChoice } from "@/components/ExcessChoice";
import { MoneyInput } from "@/components/MoneyInput";
import { SandboxTag } from "@/components/SandboxTag";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { kesEstimateMinor, PRESETS_USD_MINOR, validAmount } from "@/lib/donation";
import { donationErrorMessage } from "@/lib/donationErrors";
import { formatKes, formatUsd } from "@/lib/money";
import { cn } from "@/lib/utils";

// The PayPal button code is only fetched when the app runs against the PayPal sandbox.
const PayPalCheckout = lazy(() => import("./PayPalCheckout"));

export function DonatePanel({ item }: { item: PublicCase }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { data: config, isPending: configPending } = usePublicConfig();
  const { data: fx } = useFx();
  const [amount, setAmount] = useState<number | null>(2500);
  const [name, setName] = useState("");
  const [anonymous, setAnonymous] = useState(true);
  const [excess, setExcess] = useState<ExcessChoiceValue>("refund");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const donationRef = useRef<() => NewDonation | null>(() => null);

  const accepting = item.status === "live";
  const canPay = accepting && validAmount(amount) && !busy;
  const estimate = validAmount(amount) ? kesEstimateMinor(amount, fx?.rate) : null;

  // PayPal's button may keep the callbacks it was first given, so it reads the form through a ref
  // that always holds the latest values.
  useEffect(() => {
    donationRef.current = () =>
      validAmount(amount)
        ? { caseSlug: item.slug, amountUsdMinor: amount, excessChoice: excess, donorDisplayName: anonymous || !name.trim() ? null : name.trim(), isAnonymous: anonymous || !name.trim() }
        : null;
  });

  function finished(receiptToken: string) {
    navigate(`/receipt/${receiptToken}`);
  }

  function failed(error: unknown) {
    setBusy(false);
    setProblem(donationErrorMessage(error, t));
  }

  /** Mock mode: no PayPal window, so the server approves and captures the simulated order in one go. */
  async function payWithSimulatedPayPal() {
    const donation = donationRef.current();
    if (!donation) return;
    setBusy(true);
    setProblem(null);
    try {
      const order = await createOrder(donation);
      const result = await captureOrder(order.order_id);
      finished(result.receipt_token);
    } catch (error) {
      failed(error);
    }
  }

  if (!accepting) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-xl">{item.status === "funded" ? t("donate.funded.title") : t("donate.closed.title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">{item.status === "funded" ? t("donate.funded.body") : t("donate.closed.body")}</p>
        </CardContent>
      </Card>
    );
  }

  const sandbox = config?.paypal_mode === "sandbox" && Boolean(config.paypal_client_id);

  return (
    <Card id="donate">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 font-heading text-xl">
          <HandHeart className="size-5 text-primary" aria-hidden="true" /> {t("donate.title")}
        </CardTitle>
        <CardDescription>{t("donate.intro")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-3">
          <Label htmlFor="donate-amount">{t("donate.amount")}</Label>
          <div role="group" aria-label={t("donate.presets")} className="grid grid-cols-4 gap-2">
            {PRESETS_USD_MINOR.map((preset) => (
              <Button key={preset} variant={amount === preset ? "default" : "outline"} aria-pressed={amount === preset} onClick={() => setAmount(preset)} className="px-0">
                {formatUsd(preset)}
              </Button>
            ))}
          </div>
          <MoneyInput id="donate-amount" value={amount} onChange={setAmount} currency="USD" invalid={amount !== null && !validAmount(amount)} describedBy="donate-amount-note" />
          <p id="donate-amount-note" className={cn("text-sm", amount !== null && !validAmount(amount) ? "font-semibold text-danger-700" : "text-muted-foreground")} aria-live="polite">
            {estimate !== null && validAmount(amount)
              ? t("donate.estimate", { kes: formatKes(estimate, i18n.language) })
              : amount === null || !validAmount(amount)
                ? t("donate.range")
                : t("donate.estimateWait")}
          </p>
        </div>

        <div className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="donate-name">{t("donate.name")}</Label>
            <Input id="donate-name" value={name} maxLength={60} disabled={anonymous} autoComplete="given-name" onChange={(event) => setName(event.target.value)} />
          </div>
          <div className="flex items-start gap-3">
            <Checkbox id="donate-anon" checked={anonymous} onCheckedChange={(value) => setAnonymous(value === true)} />
            <Label htmlFor="donate-anon" className="pt-0.5 font-normal leading-snug">
              {t("donate.anonymous")}
            </Label>
          </div>
        </div>

        <ExcessChoice value={excess} onChange={setExcess} disabled={busy} />

        {problem ? (
          <Alert tone="danger" role="alert">
            <AlertDescription>{problem}</AlertDescription>
          </Alert>
        ) : null}

        <div className="space-y-3">
          {configPending ? (
            <Skeleton className="h-12" />
          ) : sandbox ? (
            <Suspense fallback={<Skeleton className="h-12" />}>
              <PayPalCheckout
                clientId={config!.paypal_client_id}
                environment="sandbox"
                disabled={!canPay}
                getDonation={() => donationRef.current()}
                onCaptured={finished}
                onProblem={failed}
                onCancel={() => setProblem(null)}
              />
            </Suspense>
          ) : (
            <Button size="lg" className="w-full" disabled={!canPay} onClick={() => void payWithSimulatedPayPal()}>
              {busy ? t("donate.working") : t("donate.payMock", { amount: validAmount(amount) ? formatUsd(amount) : "" })}
            </Button>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {config?.paypal_mode !== "live" ? <SandboxTag /> : null}
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Lock className="size-3.5 shrink-0" aria-hidden="true" /> {t("donate.secure")}
            </p>
          </div>
          <p className="text-xs text-muted-foreground">{t("donate.feeNote")}</p>
          <p className="sr-only" aria-live="polite">
            {busy ? t("donate.working") : ""}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
