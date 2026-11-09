import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { LegalBlocks } from "@/components/LegalBlocks";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { parseDocument } from "@/lib/markdown";
import { usePageTitle } from "@/lib/usePageTitle";
import privacyEn from "@/legal/privacy.en.md?raw";
import privacySw from "@/legal/privacy.sw.md?raw";
import termsEn from "@/legal/terms.en.md?raw";
import termsSw from "@/legal/terms.sw.md?raw";

const SOURCES = { privacy: { en: privacyEn, sw: privacySw }, terms: { en: termsEn, sw: termsSw } } as const;

function LegalPage({ doc }: { doc: keyof typeof SOURCES }) {
  const { t, i18n } = useTranslation();
  const language = i18n.language.startsWith("sw") ? "sw" : "en";
  const parsed = useMemo(() => parseDocument(SOURCES[doc][language]), [doc, language]);
  usePageTitle(parsed.title);
  return (
    <div className="container max-w-3xl space-y-5 py-8 md:py-12">
      <h1 className="font-heading text-3xl font-extrabold">{parsed.title}</h1>
      <Alert tone="warning">
        <AlertDescription>{t("legal.draft")}</AlertDescription>
      </Alert>
      <LegalBlocks blocks={parsed.blocks} />
    </div>
  );
}

export function Privacy() {
  return <LegalPage doc="privacy" />;
}

export function Terms() {
  return <LegalPage doc="terms" />;
}
