import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap, unwrapEmpty } from "./client";
import { isApiError } from "./errors";
import type { CaseDocument, Consent, ConsentText, FamilyCase, FamilyCaseListItem, Page, TriageStatus } from "./models";
import type { ConsentScope } from "./enums";

const MINE = ["family", "cases"] as const;

const retryUnlessClientError = (count: number, error: unknown) => !(isApiError(error) && error.status < 500) && count < 2;

export function useMyCases(page = 1) {
  return useQuery({
    queryKey: [...MINE, "list", page],
    queryFn: async () => unwrap(await api.GET("/api/cases", { params: { query: { page, page_size: 50 } } })) as unknown as Page<FamilyCaseListItem>,
    retry: retryUnlessClientError,
  });
}

export function useMyCase(caseId: string | null) {
  return useQuery({
    queryKey: [...MINE, "one", caseId],
    enabled: caseId !== null,
    queryFn: async () => unwrap(await api.GET("/api/cases/{case_id}", { params: { path: { case_id: caseId! } } })) as unknown as FamilyCase,
    retry: retryUnlessClientError,
  });
}

export function useTriage(caseId: string, enabled = true) {
  return useQuery({
    queryKey: [...MINE, "triage", caseId],
    enabled,
    queryFn: async () => unwrap(await api.GET("/api/cases/{case_id}/triage", { params: { path: { case_id: caseId } } })) as TriageStatus,
  });
}

export function useDocuments(caseId: string | null) {
  return useQuery({
    queryKey: [...MINE, "documents", caseId],
    enabled: caseId !== null,
    queryFn: async () => unwrap(await api.GET("/api/cases/{case_id}/documents", { params: { path: { case_id: caseId! } } })).items as unknown as CaseDocument[],
  });
}

export function useConsentTexts(language: "en" | "sw") {
  return useQuery({
    queryKey: ["consents", "texts", language],
    queryFn: async () => unwrap(await api.GET("/api/consents/texts", { params: { query: { lang: language } } })).items as unknown as ConsentText[],
    staleTime: 10 * 60_000,
  });
}

export function useCaseConsents(caseId: string | null) {
  return useQuery({
    queryKey: [...MINE, "consents", caseId],
    enabled: caseId !== null,
    queryFn: async () => unwrap(await api.GET("/api/cases/{case_id}/consents", { params: { path: { case_id: caseId! } } })).items as unknown as Consent[],
  });
}

/** Everything cached about one case, refetched after a change. */
function useRefreshCase() {
  const queryClient = useQueryClient();
  return (caseId: string) => queryClient.invalidateQueries({ queryKey: MINE, predicate: (query) => query.queryKey.includes(caseId) || query.queryKey[2] === "list" });
}

export interface NewCase {
  hospital_id: string;
  alias: string;
  region: string;
  patient_reference: string;
  is_minor: boolean;
}

export interface CasePatch {
  alias?: string;
  region?: string;
  story?: string | null;
  patient_reference?: string;
  is_minor?: boolean;
  show_photo?: boolean;
}

export const familyApi = {
  async create(body: NewCase) {
    return unwrap(await api.POST("/api/cases", { body })) as unknown as FamilyCase;
  },
  async update(caseId: string, body: CasePatch) {
    return unwrap(await api.PATCH("/api/cases/{case_id}", { params: { path: { case_id: caseId } }, body })) as unknown as FamilyCase;
  },
  async deleteDraft(caseId: string) {
    unwrapEmpty(await api.DELETE("/api/cases/{case_id}", { params: { path: { case_id: caseId } } }));
  },
  async cancel(caseId: string, reason: "treatment_cancelled" | "patient_deceased" | "other") {
    return unwrap(await api.POST("/api/cases/{case_id}/cancel", { params: { path: { case_id: caseId } }, body: { reason } })) as unknown as FamilyCase;
  },
  async requestReview(caseId: string, message: string) {
    return unwrap(await api.POST("/api/cases/{case_id}/review-request", { params: { path: { case_id: caseId } }, body: { message } }));
  },
  async submit(caseId: string) {
    return unwrap(await api.POST("/api/cases/{case_id}/submit", { params: { path: { case_id: caseId } } })) as unknown as FamilyCase;
  },
  async grantConsent(caseId: string, body: { scope: ConsentScope; actor: "self" | "guardian"; wording_version: string; language: "en" | "sw" }) {
    return unwrap(await api.POST("/api/cases/{case_id}/consents", { params: { path: { case_id: caseId } }, body })) as unknown as Consent;
  },
  async withdrawConsent(caseId: string, scope: ConsentScope) {
    return unwrap(await api.DELETE("/api/cases/{case_id}/consents/{scope}", { params: { path: { case_id: caseId, scope } } })) as unknown as Consent;
  },
  async uploadDocument(caseId: string, file: File, kind: "invoice" | "estimate") {
    const result = await api.POST("/api/cases/{case_id}/documents", {
      params: { path: { case_id: caseId } },
      // The browser sets the multipart boundary; the generated type says the file is a string.
      body: { kind, file: file as unknown as string },
      bodySerializer: () => {
        const form = new FormData();
        form.append("kind", kind);
        form.append("file", file);
        return form;
      },
    });
    return unwrap(result) as unknown as CaseDocument;
  },
  async deleteDocument(caseId: string, documentId: string) {
    unwrapEmpty(await api.DELETE("/api/cases/{case_id}/documents/{document_id}", { params: { path: { case_id: caseId, document_id: documentId } } }));
  },
  async uploadFinalBill(caseId: string, file: File) {
    const result = await api.POST("/api/cases/{case_id}/final-bill", {
      params: { path: { case_id: caseId } },
      body: { file: file as unknown as string },
      bodySerializer: () => {
        const form = new FormData();
        form.append("file", file);
        return form;
      },
    });
    return unwrap(result) as unknown as CaseDocument;
  },
};

/** A mutation that refreshes what is cached about the case when it finishes. */
export function useCaseMutation<TArgs extends { caseId: string }, TResult>(action: (args: TArgs) => Promise<TResult>) {
  const refresh = useRefreshCase();
  return useMutation({
    mutationFn: action,
    onSuccess: (_result, args) => refresh(args.caseId),
  });
}
