import { FileText, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { CaseDocument } from "@/api";
import { familyApi, useCaseMutation, useDocuments } from "@/api/family";
import { ErrorState } from "@/components/ErrorState";
import { FileDropzone } from "@/components/FileDropzone";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Skeleton } from "@/components/ui/skeleton";
import { uploadErrorMessage } from "@/lib/uploadErrors";
import type { StepProps } from "./types";

type BillKind = "invoice" | "estimate";

export function BillStep({ draft, onNext, onBack }: StepProps) {
  const { t } = useTranslation();
  const caseId = draft?.id ?? null;
  const documents = useDocuments(caseId);
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState<BillKind>("invoice");
  const [problem, setProblem] = useState<string | null>(null);

  const upload = useCaseMutation(async ({ caseId: id, file: chosen, kind: chosenKind }: { caseId: string; file: File; kind: BillKind }) => familyApi.uploadDocument(id, chosen, chosenKind));
  const remove = useCaseMutation(async ({ caseId: id, documentId }: { caseId: string; documentId: string }) => familyApi.deleteDocument(id, documentId));

  if (!draft) return null;

  const bills = (documents.data ?? []).filter((doc) => doc.kind === "invoice" || doc.kind === "estimate");

  async function send() {
    if (!file) return;
    setProblem(null);
    try {
      await upload.mutateAsync({ caseId: draft!.id, file, kind });
      setFile(null);
    } catch (error) {
      setProblem(uploadErrorMessage(error, t));
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h2 className="font-heading text-xl font-bold">{t("wizard.bill.title")}</h2>
        <p className="text-muted-foreground">{t("wizard.bill.intro")}</p>
        <Alert tone="info">
          <AlertDescription>{t("wizard.bill.synthetic")}</AlertDescription>
        </Alert>
      </div>

      {documents.isPending ? (
        <Skeleton className="h-16" />
      ) : documents.isError ? (
        <ErrorState error={documents.error} onRetry={() => void documents.refetch()} />
      ) : bills.length > 0 ? (
        <ul className="space-y-2" aria-label={t("wizard.bill.uploaded")}>
          {bills.map((doc) => (
            <BillRow key={doc.id} doc={doc} busy={remove.isPending} onRemove={() => remove.mutate({ caseId: draft.id, documentId: doc.id })} />
          ))}
        </ul>
      ) : null}

      <div className="space-y-4 rounded-lg border border-border bg-card p-4">
        <fieldset className="space-y-2">
          <legend className="text-sm font-semibold">{t("wizard.bill.kind")}</legend>
          <RadioGroup value={kind} onValueChange={(value) => setKind(value as BillKind)} className="grid-cols-1 sm:grid-cols-2">
            {(["invoice", "estimate"] as const).map((option) => (
              <div key={option} className="flex items-start gap-3 rounded-md border border-border p-3">
                <RadioGroupItem value={option} id={`kind-${option}`} className="mt-0.5" />
                <Label htmlFor={`kind-${option}`} className="space-y-0.5 font-normal">
                  <span className="block font-semibold">{t(`wizard.bill.kinds.${option}.label`)}</span>
                  <span className="block text-sm text-muted-foreground">{t(`wizard.bill.kinds.${option}.body`)}</span>
                </Label>
              </div>
            ))}
          </RadioGroup>
        </fieldset>
        <FileDropzone file={file} onChange={setFile} error={problem} disabled={upload.isPending} label={t("wizard.bill.file")} />
        <Button onClick={() => void send()} disabled={!file || upload.isPending}>
          {upload.isPending ? t("wizard.bill.uploading") : t("wizard.bill.upload")}
        </Button>
      </div>

      {bills.length === 0 ? <p className="text-sm text-muted-foreground">{t("wizard.bill.needOne")}</p> : null}

      <div className="flex justify-between gap-3">
        <Button variant="outline" size="lg" onClick={onBack}>
          {t("common.back")}
        </Button>
        <Button size="lg" disabled={bills.length === 0 || upload.isPending} onClick={() => onNext()}>
          {t("common.next")}
        </Button>
      </div>
    </div>
  );
}

function BillRow({ doc, onRemove, busy }: { doc: CaseDocument; onRemove: () => void; busy: boolean }) {
  const { t } = useTranslation();
  return (
    <li className="flex items-center gap-3 rounded-lg border border-border bg-card p-3">
      <FileText className="size-5 shrink-0 text-primary" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">{t(`wizard.bill.kinds.${doc.kind === "estimate" ? "estimate" : "invoice"}.label`)}</p>
        <p className="text-sm text-muted-foreground">
          {(doc.size_bytes / 1024 / 1024).toFixed(1)} MB · {doc.mime_type.replace("application/", "").replace("image/", "").toUpperCase()}
        </p>
      </div>
      <Badge tone="confirmed">{t("wizard.bill.received")}</Badge>
      <Button variant="ghost" size="icon" aria-label={t("wizard.bill.removeNamed", { kind: t(`wizard.bill.kinds.${doc.kind === "estimate" ? "estimate" : "invoice"}.label`) })} disabled={busy} onClick={onRemove}>
        <Trash2 />
      </Button>
    </li>
  );
}
