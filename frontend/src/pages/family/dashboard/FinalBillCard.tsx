import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { FamilyCase } from "@/api";
import { familyApi, useCaseMutation } from "@/api/family";
import { FileDropzone } from "@/components/FileDropzone";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "@/components/ui/toaster";
import { uploadErrorMessage } from "@/lib/uploadErrors";

/** Once treatment ends the family uploads the hospital's final bill; the hospital confirms it and the case closes when it is paid. */
export function FinalBillCard({ item }: { item: FamilyCase }) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const upload = useCaseMutation(async ({ caseId, file: chosen }: { caseId: string; file: File }) => familyApi.uploadFinalBill(caseId, chosen));
  if (item.status !== "live") return null;

  async function send() {
    if (!file) return;
    setProblem(null);
    try {
      await upload.mutateAsync({ caseId: item.id, file });
      setFile(null);
      toast.success(t("family.finalBill.sent"));
    } catch (error) {
      setProblem(uploadErrorMessage(error, t));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-lg">{t("family.finalBill.title")}</CardTitle>
        <CardDescription>{t("family.finalBill.intro")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <FileDropzone file={file} onChange={setFile} error={problem} disabled={upload.isPending} label={t("family.finalBill.file")} />
        <Button disabled={!file || upload.isPending} onClick={() => void send()}>
          {upload.isPending ? t("wizard.bill.uploading") : t("family.finalBill.upload")}
        </Button>
        {upload.isSuccess ? (
          <Alert tone="success">
            <AlertDescription>{t("family.finalBill.sent")}</AlertDescription>
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}
