import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type UiStateSimulation, useStudioStore } from "../store/studioStore";
import { apiClient, type ApiError } from "./client";
import type {
  AdminAuditResponse,
  AdminBlockersResponse,
  AdminSettingsResponse,
  AdminTemplatesResponse,
  ClipDetail,
  CreateJobRequest,
  DriveStatus,
  HealthResponse,
  JobDetail,
  ListJobsQuery,
  ListTemplatesQuery,
  PaginatedJobs,
  PatchClipPayload,
  PresetListResponse,
  TemplateListResponse,
} from "./types";

export const queryKeys = {
  health: (sim: UiStateSimulation) => ["health", sim] as const,
  jobs: (sim: UiStateSimulation, params?: ListJobsQuery) =>
    ["jobs", sim, params?.limit ?? 20, params?.status ?? null, params?.cursor ?? null] as const,
  job: (id: string | null | undefined, sim: UiStateSimulation) =>
    ["job", id ?? null, sim] as const,
  clip: (id: string | null | undefined, sim: UiStateSimulation) =>
    ["clip", id ?? null, sim] as const,
  templates: (sim: UiStateSimulation, params?: ListTemplatesQuery) =>
    ["templates", sim, params?.kind ?? null, params?.status ?? null] as const,
  presets: (sim: UiStateSimulation) => ["presets", sim] as const,
  driveStatus: (sim: UiStateSimulation) => ["drive-status", sim] as const,
  adminTemplates: (sim: UiStateSimulation) => ["admin", "templates", sim] as const,
  adminBlockers: (sim: UiStateSimulation) => ["admin", "blockers", sim] as const,
  adminSettings: (sim: UiStateSimulation) => ["admin", "settings", sim] as const,
  adminAudit: (sim: UiStateSimulation, limit = 20) =>
    ["admin", "audit", sim, limit] as const,
};

export function useHealthQuery() {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<HealthResponse, ApiError>({
    queryKey: queryKeys.health(simulation),
    queryFn: () => apiClient.getHealth(),
    retry: false,
  });
}

export function useJobsQuery(params?: ListJobsQuery) {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<PaginatedJobs, ApiError>({
    queryKey: queryKeys.jobs(simulation, params),
    queryFn: () => apiClient.listJobs(params),
    retry: false,
  });
}

export function useJobQuery(
  id: string | null | undefined,
  options?: { enabled?: boolean }
) {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<JobDetail, ApiError>({
    queryKey: queryKeys.job(id, simulation),
    queryFn: () => apiClient.getJob(id!),
    enabled:
      Boolean(id) &&
      (options?.enabled ?? true) &&
      simulation !== "loading" &&
      simulation !== "empty",
    retry: false,
  });
}

export function useClipQuery(
  id: string | null | undefined,
  options?: { enabled?: boolean }
) {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<ClipDetail, ApiError>({
    queryKey: queryKeys.clip(id, simulation),
    queryFn: () => apiClient.getClip(id!),
    enabled:
      Boolean(id) &&
      (options?.enabled ?? true) &&
      simulation !== "loading" &&
      simulation !== "empty",
    retry: false,
  });
}

export function useTemplatesQuery(params?: ListTemplatesQuery) {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<TemplateListResponse, ApiError>({
    queryKey: queryKeys.templates(simulation, params),
    queryFn: () => apiClient.listTemplates(params),
    retry: false,
  });
}

export function usePresetsQuery() {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<PresetListResponse, ApiError>({
    queryKey: queryKeys.presets(simulation),
    queryFn: () => apiClient.listPresets(),
    retry: false,
  });
}

export function useDriveStatusQuery() {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<DriveStatus, ApiError>({
    queryKey: queryKeys.driveStatus(simulation),
    queryFn: () => apiClient.getDriveStatus(),
    retry: false,
  });
}

export function useCreateJobMutation() {
  const queryClient = useQueryClient();
  return useMutation<JobDetail, ApiError, CreateJobRequest>({
    mutationFn: (payload) => apiClient.createJob(payload),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["job", created.id] });
    },
  });
}

export function usePatchClipMutation() {
  const queryClient = useQueryClient();
  return useMutation<
    ClipDetail,
    ApiError,
    { id: string; patch: PatchClipPayload }
  >({
    mutationFn: ({ id, patch }) => apiClient.patchClip(id, patch),
    onSuccess: (updated) => {
      queryClient.setQueriesData<ClipDetail>(
        { queryKey: ["clip", updated.id] },
        updated
      );
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["job", updated.job_id] });
    },
  });
}

export function useDeleteJobMutation() {
  const queryClient = useQueryClient();
  return useMutation<void, ApiError, string>({
    mutationFn: (id) => apiClient.deleteJob(id),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      void queryClient.removeQueries({ queryKey: ["job", id] });
    },
  });
}

export function useAdminTemplatesQuery() {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<AdminTemplatesResponse, ApiError>({
    queryKey: queryKeys.adminTemplates(simulation),
    queryFn: () => apiClient.adminListTemplates(),
    retry: false,
  });
}

export function useAdminBlockersQuery() {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<AdminBlockersResponse, ApiError>({
    queryKey: queryKeys.adminBlockers(simulation),
    queryFn: () => apiClient.adminListBlockers(),
    retry: false,
  });
}

export function useAdminSettingsQuery() {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<AdminSettingsResponse, ApiError>({
    queryKey: queryKeys.adminSettings(simulation),
    queryFn: () => apiClient.adminGetSettings(),
    retry: false,
  });
}

export function useAdminAuditQuery(limit = 20) {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<AdminAuditResponse, ApiError>({
    queryKey: queryKeys.adminAudit(simulation, limit),
    queryFn: () => apiClient.adminAuditLog(limit),
    retry: false,
  });
}
