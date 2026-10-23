import { Eye } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { familyApi, useCaseMutation } from "@/api/family";
import { flaggedTerms, serverFieldMessage } from "@/lib/fieldErrors";
import { CaseCard } from "@/components/CaseCard";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/input";
import type { StepProps } from "./types";

const STORY_MAX = 600;
const SAVE_AFTER_MS = 700;

type StoryState = { kind: "idle" } | { kind: "saving" } | { kind: "saved" } | { kind: "problem"; text: string; message: string; terms: string[] };

export function PageStep({ draft, onNext, onBack }: StepProps) {
  const { t } = useTranslation();
  const [story, setStory] = useState(draft?.story ?? "");
  const [state, setState] = useState<StoryState>({ kind: "idle" });
  const saved = useRef(draft?.story ?? "");
  const timer = useRef<number | undefined>(undefined);
  const save = useCaseMutation(async ({ caseId, story: text }: { caseId: string; story: string }) => familyApi.update(caseId, { story: text.trim() ? text.trim() : null }));

  async function persist(text: string): Promise<boolean> {
    if (!draft) return false;
    setState({ kind: "saving" });
    try {
      await save.mutateAsync({ caseId: draft.id, story: text });
      saved.current = text;
      setState({ kind: "saved" });
      return true;
    } catch (error) {
      setState({ kind: "problem", text, message: serverFieldMessage(error, t) ?? t("wizard.errors.storyGeneric"), terms: flaggedTerms(error) });
      return false;
    }
  }

  // The server is the judge of what counts as a medical word, so the check is a quiet autosave a moment after typing stops.
  useEffect(() => {
    if (story === saved.current) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void persist(story), SAVE_AFTER_MS);
    return () => window.clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- persist only closes over values that change with the draft
  }, [story]);

  if (!draft) return null;
  // A message about words that are no longer in the box would only confuse; it shows while it still applies.
  const problem = state.kind === "problem" && state.text === story ? state : null;

  async function next() {
    window.clearTimeout(timer.current);
    if (story !== saved.current && !(await persist(story))) return;
    onNext();
  }

  const preview = {
    slug: "preview",
    alias: draft.alias,
    region: draft.region,
    hospital_name: draft.hospital.display_name,
    trust_level: "pending_confirmation" as const,
    status: "live" as const,
    story: story.trim() || null,
    photo_url: null,
    is_estimate: true,
    target_kes_minor: null,
    target_kes: null,
    pledged_kes_minor: 0,
    pledged_kes: "0.00",
    settled_kes_minor: 0,
    settled_kes: "0.00",
    usd_goal_minor: null,
    usd_goal: null,
    donor_count: 0,
    created_at: draft.created_at,
    live_at: null,
  };

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h2 className="font-heading text-xl font-bold">{t("wizard.page.title")}</h2>
        <p className="text-muted-foreground">{t("wizard.page.intro")}</p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="story">{t("wizard.page.story")}</Label>
        <Textarea id="story" value={story} maxLength={STORY_MAX} rows={6} aria-invalid={problem ? true : undefined} aria-describedby="story-help story-status" onChange={(event) => setStory(event.target.value)} />
        <div className="flex flex-wrap justify-between gap-2 text-sm text-muted-foreground">
          <p id="story-help">{t("wizard.page.storyHelp")}</p>
          <p aria-label={t("wizard.page.counter", { count: story.length, max: STORY_MAX })}>
            {story.length}/{STORY_MAX}
          </p>
        </div>
        <div id="story-status" aria-live="polite">
          {problem ? (
            <Alert tone="danger">
              <div className="space-y-1">
                <AlertDescription>{problem.message}</AlertDescription>
                {problem.terms.length > 0 ? (
                  <p className="text-sm">
                    {t("wizard.page.flagged")} <strong>{problem.terms.join(", ")}</strong>
                  </p>
                ) : null}
              </div>
            </Alert>
          ) : state.kind === "saving" ? (
            <p className="text-sm text-muted-foreground">{t("wizard.page.checking")}</p>
          ) : state.kind === "saved" ? (
            <p className="text-sm font-semibold text-uzima-700">{t("wizard.page.saved")}</p>
          ) : null}
        </div>
      </div>

      <div className="flex items-start gap-3 rounded-lg border border-border bg-card p-4">
        <Switch id="photo" checked={false} disabled aria-describedby="photo-note" />
        <Label htmlFor="photo" className="space-y-0.5 font-normal">
          <span className="block font-semibold">{t("wizard.page.photo")}</span>
          <span id="photo-note" className="block text-sm text-muted-foreground">
            {t("wizard.page.photoOff")}
          </span>
        </Label>
      </div>

      <div className="space-y-2">
        <h3 className="flex items-center gap-2 font-heading text-base font-bold">
          <Eye className="size-4" aria-hidden="true" /> {t("wizard.page.preview")}
        </h3>
        <div className="pointer-events-none max-w-sm" aria-hidden="true" {...({ inert: "" } as object)}>
          <CaseCard item={preview} />
        </div>
        <p className="text-sm text-muted-foreground">{t("wizard.page.previewNote")}</p>
      </div>

      <div className="flex justify-between gap-3">
        <Button variant="outline" size="lg" onClick={onBack}>
          {t("common.back")}
        </Button>
        <Button size="lg" disabled={state.kind === "saving" || (problem !== null && story.trim() !== "")} onClick={() => void next()}>
          {t("common.next")}
        </Button>
      </div>
    </div>
  );
}
