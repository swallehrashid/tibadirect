import type { ConsentScope } from "@/api/enums";

/** Needed to submit a case: the hospital check and the public page. The other two are the family's choice. */
export const REQUIRED_SCOPES: ConsentScope[] = ["hospital_verification", "public_page"];
