import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";
import { isApiError } from "./errors";
import type { HospitalCaseDetail, HospitalConfirmation, HospitalQueue, OtpSent, Page, SettlementItem } from "./models";

const retryUnlessClientError = (count: number, error: unknown) => !(isApiError(error) && error.status < 500) && count < 2;

export function useHospitalQueue() {
  return useQuery({
    queryKey: ["hospital", "queue"],
    queryFn: async () => unwrap(await api.GET("/api/hospital/queue")) as unknown as HospitalQueue,
    // New bills arrive while the page is open.
    refetchInterval: 30_000,
    retry: retryUnlessClientError,
  });
}

export function useHospitalCase(caseId: string) {
  return useQuery({
    queryKey: ["hospital", "case", caseId],
    queryFn: async () => unwrap(await api.GET("/api/hospital/cases/{case_id}", { params: { path: { case_id: caseId } } })) as unknown as HospitalCaseDetail,
    // The links to the bill files last five minutes; a fresh set is fetched before they run out.
    refetchInterval: 4 * 60_000,
    staleTime: 0,
    retry: retryUnlessClientError,
  });
}

export function useSettlements(page = 1) {
  return useQuery({
    queryKey: ["hospital", "settlements", page],
    queryFn: async () => unwrap(await api.GET("/api/hospital/settlements", { params: { query: { page, page_size: 50 } } })) as unknown as Page<SettlementItem>,
    retry: retryUnlessClientError,
  });
}

export type OtpPurpose = "decision" | "second_approval";
export type Decision = "confirmed" | "queried" | "rejected";

export interface DecisionInput {
  requestId: string;
  otp: string;
  decision: Decision;
  /** Whole shillings and cents as a decimal string, "245000.00". */
  amountKes: string | null;
  invoiceNumber: string | null;
  isFinalBill: boolean;
  depositKes: string | null;
  note: string | null;
}

export const hospitalApi = {
  async sendOtp(caseId: string, purpose: OtpPurpose) {
    return unwrap(await api.POST("/api/hospital/cases/{case_id}/otp", { params: { path: { case_id: caseId } }, body: { purpose } })) as OtpSent;
  },
  async decide(caseId: string, input: DecisionInput) {
    const result = await api.POST("/api/hospital/cases/{case_id}/decision", {
      params: { path: { case_id: caseId } },
      body: {
        request_id: input.requestId,
        otp: input.otp,
        decision: input.decision,
        // The API reads these as exact decimals; the generated type says number.
        amount_kes: input.amountKes as unknown as number | null,
        invoice_number: input.invoiceNumber,
        is_final_bill: input.isFinalBill,
        deposit_kes: input.depositKes as unknown as number | null,
        note: input.note,
      },
    });
    return unwrap(result) as unknown as { confirmation: HospitalConfirmation; case_status: string };
  },
  async approve(confirmationId: string, input: { requestId: string; otp: string }) {
    const result = await api.POST("/api/hospital/confirmations/{confirmation_id}/approve", {
      params: { path: { confirmation_id: confirmationId } },
      body: { request_id: input.requestId, otp: input.otp },
    });
    return unwrap(result) as unknown as { confirmation: HospitalConfirmation; case_status: string };
  },
};
