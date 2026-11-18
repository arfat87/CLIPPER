import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type UiStateSimulation, useStudioStore } from "../store/studioStore";
import { apiClient, type ApiError } from "./client";
import {
  subscribeJobEvents,
  type SseProgressEvent,
  type SseStateEvent,
} from "./sse";
import type {
  AdminAuditResponse,
  AdminBlockersResponse,
  AdminBrandKitPayload,
  AdminBrandKitRecord,
  AdminBrandKitsResponse,
  AdminFontRecord,
  AdminFontsResponse,
  AdminPresetsResponse,
  AdminSettingsResponse,
  AdminTemplatesResponse,
  ClipDetail,
  CreateJobRequest,
  DriveFilesResponse,
  DrivePickPayload,
  DrivePickResponse,
  DriveStatus,
  HealthResponse,
  JobDetail,
  JobStatus,
  ListDriveFilesQuery,
  ListJobsQuery,
  ListTemplatesQuery,
  PaginatedJobs,
  PatchClipPayload,
  Preset,
  PresetListResponse,
  PreviewTemplatePayload,
  RetryJobPayload,
  StageCheckpoint,
  StageName,
  TemplateListResponse,
  TemplateSummary,
} from "./types";

export const queryKeys = {
  health: (sim: UiStateSimulation) => ["health", sim] as const,
  jobs: (sim: UiStateSimulation, params?: ListJobsQuery) =>
    [
      "jobs",
      sim,
      params?.limit ?? 20,
      params?.status ?? null,
      params?.cursor ?? null,
    ] as const,
  job: (id: string | null | undefined, sim: UiStateSimulation) =>
    ["job", id ?? null, sim] as const,
  clip: (id: string | null | undefined, sim: UiStateSimulation) =>
    ["clip", id ?? null, sim] as const,
  templates: (sim: UiStateSimulation, params?: ListTemplatesQuery) =>
    ["templates", sim, params?.kind ?? null, params?.status ?? null] as const,
  presets: (sim: UiStateSimulation) => ["presets", sim] as const,
  driveStatus: (sim: UiStateSimulation) => ["drive-status", sim] as const,
  driveFiles: (
    sim: UiStateSimulation,
    route?: NonNullable<ListDriveFilesQuery>["route"]
  ) => ["drive-files", sim, route ?? "all"] as const,
  adminTemplates: (sim: UiStateSimulation) =>
    ["admin", "templates", sim] as const,
  adminPresets: (sim: UiStateSimulation) => ["admin", "presets", sim] as const,
  adminFonts: (sim: UiStateSimulation) => ["admin", "fonts", sim] as const,
  adminBrandKits: (sim: UiStateSimulation) =>
    ["admin", "brandkits", sim] as const,
  adminBlockers: (sim: UiStateSimulation) =>
    ["admin", "blockers", sim] as const,
  adminSettings: (sim: UiStateSimulation) =>
    ["admin", "settings", sim] as const,
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

export function useDriveFilesQuery(
  route?: NonNullable<ListDriveFilesQuery>["route"],
  options?: { enabled?: boolean }
) {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<DriveFilesResponse, ApiError>({
    queryKey: queryKeys.driveFiles(simulation, route),
    queryFn: () => apiClient.listDriveFiles(route ? { route } : undefined),
    enabled:
      (options?.enabled ?? true) &&
      simulation !== "loading" &&
      simulation !== "empty",
    retry: false,
  });
}

export function usePickDriveFileMutation() {
  return useMutation<DrivePickResponse, ApiError, DrivePickPayload>({
    mutationFn: (payload) => apiClient.pickDriveFile(payload),
  });
}

export function useCreateJobMutation() {
  const queryClient = useQueryClient();
  return useMutation<JobDetail, ApiError, CreateJobRequest>({
    mutationFn: (payload) => apiClient.createJob(payload),
    onSuccess: (created) => {
      queryClient.setQueriesData<PaginatedJobs>(
        { queryKey: ["jobs"] },
        (prev) => {
          if (!prev) {
            return { items: [created], next_cursor: null };
          }
          const without = prev.items.filter((j) => j.id !== created.id);
          return { ...prev, items: [created, ...without] };
        }
      );
      queryClient.setQueriesData<JobDetail>(
        { queryKey: ["job", created.id] },
        created
      );
    },
  });
}

export function useRetryJobMutation() {
  const queryClient = useQueryClient();
  return useMutation<
    JobDetail,
    ApiError,
    { id: string; payload?: RetryJobPayload }
  >({
    mutationFn: ({ id, payload }) => apiClient.retryJob(id, payload),
    onSuccess: (updated) => {
      queryClient.setQueriesData<JobDetail>(
        { queryKey: ["job", updated.id] },
        updated
      );
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useCancelJobMutation() {
  const queryClient = useQueryClient();
  return useMutation<JobDetail, ApiError, string>({
    mutationFn: (id) => apiClient.cancelJob(id),
    onSuccess: (updated) => {
      queryClient.setQueriesData<JobDetail>(
        { queryKey: ["job", updated.id] },
        updated
      );
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
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

export interface StageProgressInfo {
  pct: number;
  status: StageCheckpoint["status"];
  message?: string;
  eta_s?: number | null;
}

export interface JobEventsStreamState {
  connected: boolean;
  reconnecting: boolean;
  lastEventId: string | null;
  activeStage: StageName | null;
  etaSeconds: number | null;
  liveMessage: string | null;
  liveWarnings: string[];
  stageProgress: Partial<Record<StageName, StageProgressInfo>>;
  streamError: string | null;
}

const ACTIVE_JOB_STATUSES = new Set<JobStatus>([
  "QUEUED",
  "FETCHING",
  "PREPARING",
  "TRANSCRIBING",
  "ANALYZING",
  "SELECTING",
  "RENDERING",
  "PACKAGING",
]);

export function isActiveJobStatus(status?: JobStatus | null): boolean {
  return Boolean(status && ACTIVE_JOB_STATUSES.has(status));
}

function updateJobStagesWithProgress(
  stages: StageCheckpoint[] | undefined,
  evt: SseProgressEvent
): StageCheckpoint[] {
  const base: StageCheckpoint[] = stages ? [...stages] : [];
  const idx = base.findIndex((s) => s.stage === evt.stage);
  const nextCheckpoint: StageCheckpoint = {
    ...(idx >= 0 ? base[idx] : { stage: evt.stage, attempt: 1 }),
    stage: evt.stage,
    status: evt.status,
    metrics: {
      ...(idx >= 0 ? base[idx]?.metrics : {}),
      pct: evt.pct,
    },
  };
  if (idx >= 0) {
    base[idx] = nextCheckpoint;
  } else {
    base.push(nextCheckpoint);
  }
  return base;
}

export function useJobEventsStream(
  jobId: string | null | undefined,
  options?: {
    enabled?: boolean;
    onWarning?: (message: string) => void;
    onDone?: (status: JobStatus) => void;
  }
): JobEventsStreamState {
  const queryClient = useQueryClient();
  const simulation = useStudioStore((s) => s.simulation);
  const enabled =
    Boolean(jobId) &&
    (options?.enabled ?? true) &&
    simulation === "normal";

  const [state, setState] = useState<JobEventsStreamState>({
    connected: false,
    reconnecting: false,
    lastEventId: null,
    activeStage: null,
    etaSeconds: null,
    liveMessage: null,
    liveWarnings: [],
    stageProgress: {},
    streamError: null,
  });

  useEffect(() => {
    setState({
      connected: false,
      reconnecting: false,
      lastEventId: null,
      activeStage: null,
      etaSeconds: null,
      liveMessage: null,
      liveWarnings: [],
      stageProgress: {},
      streamError: null,
    });

    if (!enabled || !jobId) {
      return;
    }

    const unsubscribe = subscribeJobEvents(jobId, {
      onOpen: () => {
        setState((prev) => ({
          ...prev,
          connected: true,
          reconnecting: false,
          streamError: null,
        }));
      },
      onReconnecting: (_attempt, lastId) => {
        setState((prev) => ({
          ...prev,
          connected: false,
          reconnecting: true,
          lastEventId: lastId,
        }));
      },
      onProgress: (evt, eventId) => {
        setState((prev) => ({
          ...prev,
          lastEventId: eventId ?? prev.lastEventId,
          activeStage: evt.stage,
          etaSeconds: evt.eta_s ?? prev.etaSeconds,
          liveMessage: evt.message ?? prev.liveMessage,
          stageProgress: {
            ...prev.stageProgress,
            [evt.stage]: {
              pct: evt.pct,
              status: evt.status,
              message: evt.message,
              eta_s: evt.eta_s,
            },
          },
        }));

        queryClient.setQueriesData<JobDetail>(
          { queryKey: ["job", jobId] },
          (prev) =>
            prev
              ? {
                  ...prev,
                  stage: evt.stage,
                  stages: updateJobStagesWithProgress(prev.stages, evt),
                }
              : prev
        );
      },
      onState: (evt: SseStateEvent, eventId) => {
        setState((prev) => ({
          ...prev,
          lastEventId: eventId ?? prev.lastEventId,
          activeStage: evt.stage ?? prev.activeStage,
        }));

        queryClient.setQueriesData<JobDetail>(
          { queryKey: ["job", jobId] },
          (prev) =>
            prev
              ? {
                  ...prev,
                  status: evt.status,
                  stage: evt.stage ?? prev.stage,
                  error_code:
                    evt.error_code !== undefined
                      ? evt.error_code
                      : prev.error_code,
                  error_message:
                    evt.error_message !== undefined
                      ? evt.error_message
                      : prev.error_message,
                }
              : prev
        );
        queryClient.setQueriesData<PaginatedJobs>(
          { queryKey: ["jobs"] },
          (prev) =>
            prev
              ? {
                  ...prev,
                  items: prev.items.map((j) =>
                    j.id === jobId
                      ? {
                          ...j,
                          status: evt.status,
                          stage: evt.stage ?? j.stage,
                        }
                      : j
                  ),
                }
              : prev
        );
      },
      onWarning: (evt, eventId) => {
        setState((prev) => ({
          ...prev,
          lastEventId: eventId ?? prev.lastEventId,
          liveWarnings: prev.liveWarnings.includes(evt.message)
            ? prev.liveWarnings
            : [...prev.liveWarnings, evt.message],
        }));
        options?.onWarning?.(evt.message);
      },
      onDone: (evt, eventId) => {
        setState((prev) => ({
          ...prev,
          connected: false,
          reconnecting: false,
          lastEventId: eventId ?? prev.lastEventId,
        }));
        void queryClient.invalidateQueries({ queryKey: ["job", jobId] });
        void queryClient.invalidateQueries({ queryKey: ["jobs"] });
        options?.onDone?.(evt.status);
      },
      onError: (message) => {
        setState((prev) => ({
          ...prev,
          connected: false,
          reconnecting: false,
          streamError: message,
        }));
      },
    });

    return () => {
      unsubscribe();
    };
  }, [enabled, jobId, queryClient]);

  return state;
}

export function useAdminTemplatesQuery() {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<AdminTemplatesResponse, ApiError>({
    queryKey: queryKeys.adminTemplates(simulation),
    queryFn: () => apiClient.adminListTemplates(),
    retry: false,
  });
}

export function useAdminImportTemplateMutation() {
  const queryClient = useQueryClient();
  return useMutation<TemplateSummary, ApiError, File>({
    mutationFn: (file) => apiClient.adminImportTemplate(file),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "templates"] });
      void queryClient.invalidateQueries({ queryKey: ["templates"] });
    },
  });
}

export function useAdminPublishTemplateMutation() {
  const queryClient = useQueryClient();
  return useMutation<TemplateSummary, ApiError, string>({
    mutationFn: (id) => apiClient.adminPublishTemplate(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "templates"] });
      void queryClient.invalidateQueries({ queryKey: ["templates"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "audit"] });
    },
  });
}

export function useAdminArchiveTemplateMutation() {
  const queryClient = useQueryClient();
  return useMutation<TemplateSummary, ApiError, string>({
    mutationFn: (id) => apiClient.adminArchiveTemplate(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "templates"] });
      void queryClient.invalidateQueries({ queryKey: ["templates"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "audit"] });
    },
  });
}

export function useAdminDuplicateTemplateMutation() {
  const queryClient = useQueryClient();
  return useMutation<TemplateSummary, ApiError, string>({
    mutationFn: (id) => apiClient.adminDuplicateTemplate(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "templates"] });
    },
  });
}

export function usePreviewTemplateMutation() {
  return useMutation<
    { previewUrl: string; contentType: string },
    ApiError,
    { id: string; payload?: PreviewTemplatePayload }
  >({
    mutationFn: ({ id, payload }) => apiClient.previewTemplate(id, payload),
  });
}

export function useAdminPresetsQuery() {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<AdminPresetsResponse, ApiError>({
    queryKey: queryKeys.adminPresets(simulation),
    queryFn: () => apiClient.adminListPresets(),
    retry: false,
  });
}

export function useAdminSavePresetMutation() {
  const queryClient = useQueryClient();
  return useMutation<Preset, ApiError, Preset>({
    mutationFn: (preset) => apiClient.adminSavePreset(preset),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "presets"] });
      void queryClient.invalidateQueries({ queryKey: ["presets"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "audit"] });
    },
  });
}

export function useAdminFontsQuery() {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<AdminFontsResponse, ApiError>({
    queryKey: queryKeys.adminFonts(simulation),
    queryFn: () => apiClient.adminListFonts(),
    retry: false,
  });
}

export function useAdminUploadFontMutation() {
  const queryClient = useQueryClient();
  return useMutation<
    AdminFontRecord,
    ApiError,
    { file: File; license: string }
  >({
    mutationFn: ({ file, license }) => apiClient.adminUploadFont(file, license),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "fonts"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "audit"] });
    },
  });
}

export function useAdminBrandKitsQuery() {
  const simulation = useStudioStore((s) => s.simulation);
  return useQuery<AdminBrandKitsResponse, ApiError>({
    queryKey: queryKeys.adminBrandKits(simulation),
    queryFn: () => apiClient.adminListBrandKits(),
    retry: false,
  });
}

export function useAdminSaveBrandKitMutation() {
  const queryClient = useQueryClient();
  return useMutation<AdminBrandKitRecord, ApiError, AdminBrandKitPayload>({
    mutationFn: (payload) => apiClient.adminSaveBrandKit(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "brandkits"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "audit"] });
    },
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
