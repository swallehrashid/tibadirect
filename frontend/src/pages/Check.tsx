import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, EyeOff, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { isApiError, usePublicConfig, type AppealResult } from "@/api";
import { APPEAL_MAX, APPEAL_MIN, checkAppealImage, checkAppealText } from "@/api/checker";
import { EmptyState } from "@/components/EmptyState";
import { FileDropzone } from "@/components/FileDropzone";
import { SandboxTag } from "@/components/SandboxTag";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { usePageTitle } from "@/lib/usePageTitle";

const BAND_STYLE = {
  low: { icon: CheckCircle2, box: "border-sky-500/40 bg-sky-100 text-sky-800", bar: "bg-sky-500" },
  medium: { icon: AlertTriangle, box: "border-jua-400/60 bg-jua-50 text-jua-700", bar: "bg-jua-400" },
  high: { icon: ShieldAlert, box: "border-danger-600/30 bg-danger-50 text-danger-700", bar: "bg-danger-600" },
} as const;

function errorMessage(error: unknown, t: (key: string, options?: Record<string, unknown>) => string): string {
  if (isApiError(error)) {
    if (error.status === 429) return t("error.rateLimited", { seconds: error.retryAfter ?? 60 });
    if (error.status === 404) return t("checker.errors.off");
    if (error.code === "image_unreadable") return t("checker.errors.unreadable");
    if (error.code === "image_required" || error.status === 415) return t("upload.errors.imageType");
    if (error.status === 413) return t("upload.errors.size");
    if (error.status === 422) return t("checker.errors.invalid");
  }
  if (error instanceof TypeError) return t("error.offline");
  return t("error.body");
}

function Result({ result, onAgain }: { result: AppealResult; onAgain: () => void }) {
  const { t, i18n } = useTranslation();
  const sw = i18n.language.startsWith("sw");
  const style = BAND_STYLE[result.band];
  const Icon = style.icon;
  const reasons = sw ? result.reasons_sw : result.reasons_en;
  return (
    <section aria-labelledby="result-title" className="space-y-4" data-testid="checker-result">
      <Card className={cn("border", style.box, "shadow-none")}>
        <CardHeader>
          <CardTitle id="result-title" className="flex items-center gap-2 font-heading text-xl">
            <Icon className="size-6 shrink-0" aria-hidden="true" />
            {t(`checker.band.${result.band}.title`)}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p>{t(`checker.band.${result.band}.body`)}</p>
          <div
            role="meter"
            aria-label={t("checker.score")}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={result.score}
            aria-valuetext={t("checker.scoreText", { score: result.score })}
            className="h-3 w-full overflow-hidden rounded-full bg-card"
          >
            <div className={cn("h-full", style.bar)} style={{ width: `${Math.max(2, result.score)}%` }} />
          </div>
          <p className="text-sm font-semibold">{t("checker.scoreText", { score: result.score })}</p>
        </CardContent>
      </Card>

      {result.matched_case ? (
        <Alert tone="success">
          <CheckCircle2 />
          <div className="space-y-1">
            <p className="font-semibold">{t("checker.match.title")}</p>
            <AlertDescription>{t("checker.match.body", { alias: result.matched_case.alias, hospital: result.matched_case.hospital_name })}</AlertDescription>
            <Link to={`/c/${result.matched_case.slug}`} className="inline-block font-semibold underline underline-offset-4">
              {t("checker.match.link")}
            </Link>
          </div>
        </Alert>
      ) : null}

      {reasons.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="font-heading text-lg">{t("checker.reasons")}</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="list-disc space-y-1 pl-5">
              {reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      {result.explanation ? (
        <Card>
          <CardHeader>
            <CardTitle className="font-heading text-lg">{t("checker.explanation")}</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="whitespace-pre-line">{result.explanation}</p>
          </CardContent>
        </Card>
      ) : null}

      <Alert tone="info">
        <ShieldAlert />
        <div>
          <p className="font-semibold">{t("checker.tip")}</p>
          <AlertDescription>{sw ? result.safety_tip_sw : result.safety_tip_en}</AlertDescription>
        </div>
      </Alert>
      <p className="text-sm text-muted-foreground">{t("checker.notProof")}</p>
      <Button variant="outline" onClick={onAgain}>
        {t("checker.again")}
      </Button>
    </section>
  );
}

export function Check() {
  const { t } = useTranslation();
  usePageTitle(t("checker.title"));
  const { data: config, isPending: configPending } = usePublicConfig();
  const [mode, setMode] = useState<"text" | "image">("text");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<AppealResult | null>(null);
  const check = useMutation({
    mutationFn: async () => (mode === "text" ? checkAppealText(text.trim()) : checkAppealImage(file!)),
    onSuccess: (data) => setResult(data),
  });

  if (configPending) return <Skeleton className="container my-10 h-64" />;
  if (config?.features.appeal_checker === false) {
    return (
      <div className="container py-10">
        <EmptyState headingLevel={1} title={t("checker.offTitle")} description={t("checker.offBody")} />
      </div>
    );
  }

  const length = text.trim().length;
  const ready = mode === "text" ? length >= APPEAL_MIN && length <= APPEAL_MAX : file !== null;
  const reset = () => {
    setResult(null);
    setText("");
    setFile(null);
    check.reset();
  };

  return (
    <div className="container max-w-3xl space-y-6 py-8 md:py-12">
      <header className="space-y-2">
        <h1 className="font-heading text-3xl font-extrabold">{t("checker.title")}</h1>
        <p className="text-lg text-muted-foreground">{t("checker.intro")}</p>
        <p className="flex items-start gap-2 text-sm text-muted-foreground">
          <EyeOff className="mt-0.5 size-4 shrink-0" aria-hidden="true" /> {t("checker.privacy")}
        </p>
      </header>

      {result ? (
        <Result result={result} onAgain={reset} />
      ) : (
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (ready) check.mutate();
          }}
        >
          <Tabs value={mode} onValueChange={(value) => { setMode(value as "text" | "image"); check.reset(); }}>
            <TabsList aria-label={t("checker.how")}>
              <TabsTrigger value="text">{t("checker.tabs.text")}</TabsTrigger>
              <TabsTrigger value="image">{t("checker.tabs.image")}</TabsTrigger>
            </TabsList>
            <TabsContent value="text" className="space-y-2">
              <Label htmlFor="appeal-text">{t("checker.textLabel")}</Label>
              <Textarea id="appeal-text" value={text} rows={8} maxLength={APPEAL_MAX} aria-describedby="appeal-help" onChange={(event) => setText(event.target.value)} />
              <p id="appeal-help" className="text-sm text-muted-foreground">
                {length < APPEAL_MIN ? t("checker.textHelp", { count: APPEAL_MIN }) : t("checker.textCount", { count: length, max: APPEAL_MAX })}
              </p>
            </TabsContent>
            <TabsContent value="image" className="space-y-2">
              <FileDropzone file={file} onChange={setFile} imagesOnly allowCamera={false} label={t("checker.imageLabel")} disabled={check.isPending} />
              <p className="text-sm text-muted-foreground">{t("checker.imageHelp")}</p>
            </TabsContent>
          </Tabs>

          {check.isError ? (
            <Alert tone="danger">
              <AlertDescription>{errorMessage(check.error, t)}</AlertDescription>
            </Alert>
          ) : null}
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" size="lg" disabled={!ready || check.isPending}>
              {check.isPending ? t("checker.checking") : t("checker.check")}
            </Button>
            {config?.demo_banner ? <SandboxTag /> : null}
          </div>
        </form>
      )}
    </div>
  );
}

export const Component = Check;
