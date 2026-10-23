import { Camera, FileText, UploadCloud, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type DragEvent } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { ACCEPT_ATTRIBUTE, validateUpload } from "@/lib/uploads";
import { cn } from "@/lib/utils";

interface Props {
  file: File | null;
  onChange: (file: File | null) => void;
  /** Offer the phone camera as a second way to add a photo of the bill. */
  allowCamera?: boolean;
  disabled?: boolean;
  /** An error from outside (for example the server refusing the file). */
  error?: string | null;
  label?: string;
}

/** Drag and drop, file picker and camera, with a preview and the same checks the server makes. */
export function FileDropzone({ file, onChange, allowCamera = true, disabled = false, error = null, label }: Props) {
  const { t } = useTranslation();
  const inputId = useId();
  const hintId = useId();
  const errorId = useId();
  const pickRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ file: File; url: string } | null>(null);

  // Read an image into a data URL; the result is only shown while it belongs to the current file.
  useEffect(() => {
    if (!file || !file.type.startsWith("image/")) return;
    const reader = new FileReader();
    reader.onload = () => setPreview({ file, url: String(reader.result) });
    reader.readAsDataURL(file);
    return () => reader.abort();
  }, [file]);
  const previewUrl = preview && preview.file === file ? preview.url : null;

  function accept(candidate: File | undefined) {
    if (!candidate) return;
    const issue = validateUpload(candidate);
    if (issue) {
      setProblem(t(`upload.errors.${issue}`));
      return;
    }
    setProblem(null);
    onChange(candidate);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (!disabled) accept(event.dataTransfer.files[0]);
  }

  const shownError = problem ?? error;
  const describedBy = [hintId, shownError ? errorId : null].filter(Boolean).join(" ");

  return (
    <div className="space-y-3">
      {label ? (
        <label htmlFor={inputId} className="text-sm font-semibold">
          {label}
        </label>
      ) : null}
      <div
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        data-dragging={dragging}
        className={cn(
          "flex flex-col items-center gap-3 rounded-lg border-2 border-dashed bg-card p-6 text-center",
          dragging ? "border-primary bg-tiba-50" : "border-input",
          disabled && "opacity-60",
        )}
      >
        {file ? (
          <div className="flex w-full flex-col items-center gap-3">
            {previewUrl ? (
              <img src={previewUrl} alt={t("upload.preview")} className="max-h-56 rounded-md border border-border object-contain" />
            ) : (
              <FileText className="size-12 text-primary" aria-hidden="true" />
            )}
            <p className="break-all text-sm font-semibold">
              {file.name} <span className="font-normal text-muted-foreground">({(file.size / 1024 / 1024).toFixed(1)} MB)</span>
            </p>
            <Button variant="outline" size="sm" disabled={disabled} onClick={() => onChange(null)}>
              <X /> {t("common.remove")}
            </Button>
          </div>
        ) : (
          <>
            <UploadCloud className="size-10 text-primary" aria-hidden="true" />
            <p className="font-semibold">{t("upload.drop")}</p>
            <div className="flex flex-wrap justify-center gap-2">
              <Button variant="default" disabled={disabled} onClick={() => pickRef.current?.click()}>
                {t("upload.choose")}
              </Button>
              {allowCamera ? (
                <Button variant="outline" disabled={disabled} onClick={() => cameraRef.current?.click()}>
                  <Camera /> {t("upload.camera")}
                </Button>
              ) : null}
            </div>
          </>
        )}
        <p id={hintId} className="text-sm text-muted-foreground">
          {t("upload.hint")}
        </p>
        <input
          id={inputId}
          ref={pickRef}
          type="file"
          accept={ACCEPT_ATTRIBUTE}
          className="sr-only"
          disabled={disabled}
          aria-describedby={describedBy}
          aria-invalid={shownError ? true : undefined}
          onChange={(event) => {
            accept(event.target.files?.[0]);
            event.target.value = "";
          }}
        />
        <input
          ref={cameraRef}
          type="file"
          accept="image/*"
          capture="environment"
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          disabled={disabled}
          onChange={(event) => {
            accept(event.target.files?.[0]);
            event.target.value = "";
          }}
        />
      </div>
      {shownError ? (
        <p id={errorId} role="alert" className="text-sm font-semibold text-danger-700">
          {shownError}
        </p>
      ) : null}
    </div>
  );
}
