/** Client-side checks for a bill upload. The server repeats them (and more); these only save a round trip. */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const ACCEPTED_TYPES = ["application/pdf", "image/png", "image/jpeg"] as const;
export const ACCEPT_ATTRIBUTE = ".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg";
/** For screenshots: pictures only. */
export const ACCEPT_IMAGES_ATTRIBUTE = ".png,.jpg,.jpeg,image/png,image/jpeg";

export type UploadProblem = "type" | "size" | "empty";

const EXTENSION_TYPE: Record<string, string> = { pdf: "application/pdf", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg" };

export function validateUpload(file: Pick<File, "name" | "type" | "size">, options: { imagesOnly?: boolean } = {}): UploadProblem | null {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  const byName = EXTENSION_TYPE[extension];
  const byType = (ACCEPTED_TYPES as readonly string[]).includes(file.type) ? file.type : undefined;
  // A named type must agree with the extension; a missing type (some phones) falls back to the extension.
  if (!byName || (file.type && !byType) || (byType && byType !== byName)) return "type";
  if (options.imagesOnly && (byName === "application/pdf" || byType === "application/pdf")) return "type";
  if (file.size === 0) return "empty";
  if (file.size > MAX_UPLOAD_BYTES) return "size";
  return null;
}
