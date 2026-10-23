import { Check, Copy, MessageCircle, Share2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toaster";

/** Copy the link, send it on WhatsApp, or use the phone's own share sheet when there is one. */
export function ShareButtons({ title }: { title: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const url = window.location.href.split("#")[0] ?? window.location.href;
  const text = `${title} ${url}`;
  const canShare = typeof navigator.share === "function";

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      toast.success(t("share.copied"));
      window.setTimeout(() => setCopied(false), 2500);
    } catch {
      toast.error(t("share.copyFailed"));
    }
  }

  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label={t("share.title")}>
      <Button variant="outline" size="sm" onClick={() => void copy()}>
        {copied ? <Check /> : <Copy />} {t("share.copy")}
      </Button>
      <Button asChild variant="outline" size="sm">
        <a href={`https://wa.me/?text=${encodeURIComponent(text)}`} target="_blank" rel="noopener noreferrer">
          <MessageCircle /> WhatsApp
          <span className="sr-only"> ({t("share.opensNewTab")})</span>
        </a>
      </Button>
      {canShare ? (
        <Button variant="outline" size="sm" onClick={() => void navigator.share({ title, url }).catch(() => undefined)}>
          <Share2 /> {t("share.more")}
        </Button>
      ) : null}
    </div>
  );
}
