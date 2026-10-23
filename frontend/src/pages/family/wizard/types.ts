import type { FamilyCase } from "@/api";

/** What every wizard step receives from the wizard shell. */
export interface StepProps {
  /** The draft, once step 1 has created it. */
  draft: FamilyCase | null;
  /** Move on; step 1 passes the id of the draft it just created. */
  onNext: (caseId?: string) => void;
  onBack: () => void;
}
