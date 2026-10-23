import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";
import { isApiError } from "./errors";
import type { AdminCase, AdminCaseListItem, AdminStats, AgentAction, AgentActionStatus, AgentAnswer, AuditEntry, ChainVerification, JobsOverview, Page, ReviewItem, ReviewResolution, SettlementRun } from "./models";
import type { CaseStatus } from "./enums";

const retryUnlessClientError = (count: number, error: unknown) => !(isApiError(error) && error.status < 500) && count < 2;

export const ADMIN_PAGE_SIZE = 50;

export function useAdminStats() {
  return useQuery({
    queryKey: ["admin", "stats"],
    queryFn: async () => unwrap(await api.GET("/api/admin/stats")) as unknown as AdminStats,
    refetchInterval: 30_000,
    retry: retryUnlessClientError,
  });
}

export function useAdminJobs() {
  return useQuery({
    queryKey: ["admin", "jobs"],
    queryFn: async () => unwrap(await api.GET("/api/admin/jobs")) as unknown as JobsOverview,
    refetchInterval: 15_000,
    retry: retryUnlessClientError,
  });
}

export function useAdminCases(filters: { status: CaseStatus | null; q: string }, page: number, enabled = true) {
  return useQuery({
    queryKey: ["admin", "cases", filters, page],
    enabled,
    queryFn: async () =>
      unwrap(await api.GET("/api/admin/cases", { params: { query: { status: filters.status ?? undefined, q: filters.q || undefined, page, page_size: ADMIN_PAGE_SIZE } } })) as unknown as Page<AdminCaseListItem>,
    placeholderData: (previous) => previous,
    retry: retryUnlessClientError,
  });
}

export function useAdminCase(caseId: string) {
  return useQuery({
    queryKey: ["admin", "case", caseId],
    // Opening a case is written to the audit log, so it is read once and then only when asked for again.
    queryFn: async () => unwrap(await api.GET("/api/admin/cases/{case_id}", { params: { path: { case_id: caseId } } })) as unknown as AdminCase,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: retryUnlessClientError,
  });
}

export function useReviewItems(status: "open" | "resolved" | null, page: number) {
  return useQuery({
    queryKey: ["admin", "review", status, page],
    queryFn: async () =>
      unwrap(await api.GET("/api/admin/review-items", { params: { query: { status: status ?? undefined, page, page_size: ADMIN_PAGE_SIZE } } })) as unknown as Page<ReviewItem>,
    refetchInterval: 30_000,
    retry: retryUnlessClientError,
  });
}

export function useAuditLog(filters: { targetType: string; targetId: string }, page: number) {
  return useQuery({
    queryKey: ["admin", "audit", filters, page],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/admin/audit-log", {
          params: { query: { target_type: filters.targetType || undefined, target_id: filters.targetId || undefined, page, page_size: ADMIN_PAGE_SIZE } },
        }),
      ) as unknown as Page<AuditEntry>,
    placeholderData: (previous) => previous,
    retry: retryUnlessClientError,
  });
}

export function useAgentActions(status: AgentActionStatus | null, enabled = true) {
  return useQuery({
    queryKey: ["admin", "agent", "actions", status],
    enabled,
    queryFn: async () => unwrap(await api.GET("/api/admin/agent/actions", { params: { query: { status: status ?? undefined, page_size: ADMIN_PAGE_SIZE } } })) as unknown as Page<AgentAction>,
    retry: retryUnlessClientError,
  });
}

export const adminApi = {
  async verifyLedger() {
    return unwrap(await api.GET("/api/admin/ledger/verify")) as unknown as ChainVerification;
  },
  async retryJob(jobId: string) {
    return unwrap(await api.POST("/api/admin/jobs/{job_id}/retry", { params: { path: { job_id: jobId } } }));
  },
  async resolveReview(itemId: string, resolution: ReviewResolution, note: string) {
    return unwrap(await api.POST("/api/admin/review-items/{item_id}/resolve", { params: { path: { item_id: itemId } }, body: { resolution, note } })) as unknown as ReviewItem;
  },
  async freeze(caseId: string, reason: string) {
    return unwrap(await api.POST("/api/admin/cases/{case_id}/freeze", { params: { path: { case_id: caseId } }, body: { reason } })) as unknown as AdminCase;
  },
  async runSettlement(caseId: string) {
    return unwrap(await api.POST("/api/admin/cases/{case_id}/settlements/run", { params: { path: { case_id: caseId } } })) as unknown as SettlementRun;
  },
  async ask(question: string, caseId: string | null) {
    return unwrap(await api.POST("/api/admin/agent/ask", { body: { question, case_id: caseId } })) as unknown as AgentAnswer;
  },
  async approveAction(actionId: string) {
    return unwrap(await api.POST("/api/admin/agent/actions/{action_id}/approve", { params: { path: { action_id: actionId } } })) as unknown as AgentAction;
  },
  async rejectAction(actionId: string, note: string) {
    return unwrap(await api.POST("/api/admin/agent/actions/{action_id}/reject", { params: { path: { action_id: actionId } }, body: { note } })) as unknown as AgentAction;
  },
};
