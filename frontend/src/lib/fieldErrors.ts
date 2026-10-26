import { isApiError } from "@/api";

type Translate = (key: string, options?: Record<string, unknown>) => string;

const BY_CODE: Record<string, string> = {
  alias_length: "wizard.errors.aliasLength",
  alias_personal_data: "wizard.errors.aliasPersonal",
  alias_medical_terms: "wizard.errors.aliasMedical",
  region_invalid: "wizard.errors.region",
  patient_reference_invalid: "wizard.errors.patientReference",
  hospital_not_found: "wizard.errors.hospital",
  story_length: "wizard.errors.storyLength",
  story_personal_data: "wizard.errors.storyPersonal",
  story_medical_terms: "wizard.errors.storyMedical",
};

/** Plain-language text for a field the server refused, or null when it has no specific wording. */
export function serverFieldMessage(error: unknown, t: Translate): string | null {
  if (isApiError(error) && error.code in BY_CODE) return t(BY_CODE[error.code]!);
  return null;
}

/** The first field a 422 names, with the server's own (English) message as a fallback. */
export function firstFieldError(error: unknown): { field: string; message: string } | null {
  if (!isApiError(error)) return null;
  const [field, message] = Object.entries(error.fieldErrors)[0] ?? [];
  return field && message ? { field, message } : null;
}

/** Terms the server flagged as medical in a field (for example the story), if any. */
export function flaggedTerms(error: unknown): string[] {
  if (!isApiError(error)) return [];
  const terms = error.details.terms;
  return Array.isArray(terms) ? terms.filter((term): term is string => typeof term === "string") : [];
}
