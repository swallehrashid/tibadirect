import { useTranslation } from "react-i18next";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { usePageTitle } from "@/lib/usePageTitle";

const POINTS = ["p1", "p2", "p3", "p4", "p5", "p6"] as const;

function LegalPage({ doc }: { doc: "privacy" | "terms" }) {
  const { t } = useTranslation();
  usePageTitle(t(`legal.${doc}.title`));
  return (
    <div className="container max-w-3xl space-y-6 py-8 md:py-12">
      <h1 className="font-heading text-3xl font-extrabold">{t(`legal.${doc}.title`)}</h1>
      <Alert tone="warning">
        <AlertDescription>{t("legal.draft")}</AlertDescription>
      </Alert>
      <p className="text-lg text-muted-foreground">{t(`legal.${doc}.intro`)}</p>
      <dl className="space-y-5">
        {POINTS.map((key) => (
          <div key={key}>
            <dt className="font-heading text-lg font-bold">{t(`legal.${doc}.${key}.title`)}</dt>
            <dd className="text-muted-foreground">{t(`legal.${doc}.${key}.body`)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function Privacy() {
  return <LegalPage doc="privacy" />;
}

export function Terms() {
  return <LegalPage doc="terms" />;
}
