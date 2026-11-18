import {
  initialMockJobs,
  mockAdminAudit,
  mockAdminBlockers,
  mockAdminPrompts,
  mockAdminScoring,
  mockAdminSettings,
  mockBrandKits,
  mockClipsById,
  mockDriveFiles,
  mockDriveStatus,
  mockDriveTree,
  mockFonts,
  mockHealth,
  mockPresets,
  mockPublishPacksByClipId,
  mockTemplates,
  mockTranscriptsByJobId,
} from "../mocks/mockData";
import type {
  AdminActivatePromptPayload,
  AdminAuditResponse,
  AdminBlockersResponse,
  AdminBrandKitPayload,
  AdminBrandKitRecord,
  AdminBrandKitsResponse,
  AdminDryRunScoringPayload,
  AdminDryRunScoringResponse,
  AdminFontRecord,
  AdminFontsResponse,
  AdminPresetsResponse,
  AdminPromptDetail,
  AdminPromptsResponse,
  AdminResolveBlockerPayload,
  AdminScoringResponse,
  AdminSettingsResponse,
  AdminTemplatesResponse,
  AdminTestPromptResponse,
  AdminUpdatePromptPayload,
  AdminUpdateScoringPayload,
  AdminUpdateSettingsPayload,
  Blocker,
  ClipDetail,
  ClipFeedbackPayload,
  ClipRendersResponse,
  ClipSummary,
  CreateJobRequest,
  DriveFilesResponse,
  DrivePickPayload,
  DrivePickResponse,
  DriveStatus,
  DriveSyncPayload,
  DriveTreeResponse,
  ErrorEnvelope,
  HealthResponse,
  InitUploadPayload,
  JobDetail,
  ListDriveFilesQuery,
  ListJobsQuery,
  ListTemplatesQuery,
  PaginatedJobs,
  PatchClipPayload,
  PatchTranscriptPayload,
  Preset,
  PresetListResponse,
  PreviewTemplatePayload,
  PublishPack,
  RegeneratePublishPackPayload,
  RenderAllJobPayload,
  RenderClipPayload,
  RenderRecord,
  ReselectJobPayload,
  RetryJobPayload,
  TemplateListResponse,
  TemplateSummary,
  TemplateVersionsResponse,
  Transcript,
  UpdatePublishPackPayload,
  UploadSession,
} from "./types";

export const API_BASE = "/api/v1";

export class ApiError extends Error {
  public readonly status: number;
  public readonly code: string;
  public readonly retryable: boolean;

  constructor(status: number, envelope?: ErrorEnvelope["error"]) {
    super(
      envelope?.message ??
        "Something went wrong talking to CLIPPER. Check your connection and try again."
    );
    this.name = "ApiError";
    this.status = status;
    this.code = envelope?.code ?? "VALIDATION_ERROR";
    this.retryable = envelope?.retryable ?? status >= 500;
  }
}

export type MockSimulationHeader = "normal" | "empty" | "error";

let currentSimulationMode: MockSimulationHeader = "normal";

export function setApiSimulationMode(mode: MockSimulationHeader): void {
  currentSimulationMode = mode;
}

export function getApiSimulationMode(): MockSimulationHeader {
  return currentSimulationMode;
}

export type ApiMode = "real" | "mock" | "auto";

function resolveInitialApiMode(): ApiMode {
  const envMode = import.meta.env?.VITE_API_MODE as string | undefined;
  if (envMode === "real" || envMode === "mock" || envMode === "auto") {
    return envMode;
  }
  return "auto";
}

let currentApiMode: ApiMode = resolveInitialApiMode();

export function setApiMode(mode: ApiMode): void {
  currentApiMode = mode;
}

export function getApiMode(): ApiMode {
  return currentApiMode;
}

function buildQueryString(
  params?: Record<string, string | number | undefined>
): string {
  if (!params) {
    return "";
  }
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

async function rawFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (currentSimulationMode !== "normal") {
    headers.set("X-Clipper-Mock-Mode", currentSimulationMode);
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });

  if (!response.ok) {
    let envelope: ErrorEnvelope | undefined;
    try {
      envelope = (await response.json()) as ErrorEnvelope;
    } catch {
      envelope = undefined;
    }
    throw new ApiError(response.status, envelope?.error);
  }

  return response;
}

async function request<T>(
  path: string,
  init?: RequestInit,
  fallback?: () => T | Promise<T>
): Promise<T> {
  if (currentApiMode === "mock" && fallback) {
    if (currentSimulationMode === "error") {
      throw new ApiError(503, {
        code: "LLM_UNAVAILABLE",
        message: "Simulated offline error in mock API mode.",
        retryable: true,
      });
    }
    return await fallback();
  }

  const headers = new Headers(init?.headers);
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }

  try {
    const response = await rawFetch(path, { ...init, headers });

    if (response.status === 204) {
      return undefined as T;
    }

    return (await response.json()) as T;
  } catch (err) {
    if (err instanceof ApiError) {
      throw err;
    }
    if (currentApiMode === "auto" && fallback) {
      return await fallback();
    }
    throw err;
  }
}

async function blobToPreviewUrl(blob: Blob): Promise<string> {
  if (typeof URL !== "undefined" && typeof URL.createObjectURL === "function") {
    return URL.createObjectURL(blob);
  }
  if (blob.type.includes("svg") && typeof blob.text === "function") {
    const svgText = await blob.text();
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svgText)}`;
  }
  if (typeof FileReader !== "undefined") {
    return await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(String(reader.result ?? ""));
      reader.onerror = () => resolve("data:image/png;base64,");
      reader.readAsDataURL(blob);
    });
  }
  return "data:image/png;base64,";
}

export const apiClient = {
  getHealth: (): Promise<HealthResponse> =>
    request<HealthResponse>("/health", undefined, () =>
      structuredClone(mockHealth)
    ),

  listJobs: (params?: ListJobsQuery | number): Promise<PaginatedJobs> => {
    const query: ListJobsQuery =
      typeof params === "number" ? { limit: params } : (params ?? { limit: 20 });
    return request<PaginatedJobs>(
      `/jobs${buildQueryString({
        limit: query?.limit ?? 20,
        cursor: query?.cursor,
        status: query?.status,
      })}`,
      undefined,
      () => ({
        items:
          currentSimulationMode === "empty"
            ? []
            : structuredClone(initialMockJobs).slice(0, query?.limit ?? 20),
        next_cursor: null,
      })
    );
  },

  createJob: (
    payload: CreateJobRequest,
    idempotencyKey?: string
  ): Promise<JobDetail> =>
    request<JobDetail>(
      "/jobs",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
        },
        body: JSON.stringify(payload),
      },
      () => {
        const now = new Date().toISOString();
        return {
          ...structuredClone(initialMockJobs[0]!),
          id: `job_fallback_${Date.now().toString(36)}`,
          source_kind: payload.source.kind,
          source_url: payload.source.url ?? null,
          source_ref:
            payload.source.drive_file_id ?? payload.source.upload_id ?? null,
          title: payload.clip_brief?.trim() || "Created Clipping Job",
          status: "QUEUED",
          created_at: now,
          updated_at: now,
        };
      }
    ),

  getJob: (id: string): Promise<JobDetail> =>
    request<JobDetail>(`/jobs/${encodeURIComponent(id)}`, undefined, () => {
      const found =
        initialMockJobs.find((j) => j.id === id) ?? initialMockJobs[0]!;
      return structuredClone(found);
    }),

  deleteJob: (id: string): Promise<void> =>
    request<void>(
      `/jobs/${encodeURIComponent(id)}`,
      {
        method: "DELETE",
      },
      () => undefined
    ),

  retryJob: (id: string, payload?: RetryJobPayload): Promise<JobDetail> =>
    request<JobDetail>(
      `/jobs/${encodeURIComponent(id)}/retry`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload ?? {}),
      },
      () => ({
        ...structuredClone(
          initialMockJobs.find((j) => j.id === id) ?? initialMockJobs[0]!
        ),
        status: "FETCHING",
        stage: payload?.from_stage ?? "fetch",
      })
    ),

  cancelJob: (id: string): Promise<JobDetail> =>
    request<JobDetail>(
      `/jobs/${encodeURIComponent(id)}/cancel`,
      {
        method: "POST",
      },
      () => ({
        ...structuredClone(
          initialMockJobs.find((j) => j.id === id) ?? initialMockJobs[0]!
        ),
        status: "CANCELLED",
      })
    ),

  reselectJobClips: (
    id: string,
    payload?: ReselectJobPayload
  ): Promise<JobDetail> =>
    request<JobDetail>(
      `/jobs/${encodeURIComponent(id)}/reselect`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload ?? {}),
      },
      () =>
        structuredClone(
          initialMockJobs.find((j) => j.id === id) ?? initialMockJobs[0]!
        )
    ),

  renderAllJobClips: (
    id: string,
    payload?: RenderAllJobPayload
  ): Promise<JobDetail> =>
    request<JobDetail>(
      `/jobs/${encodeURIComponent(id)}/render-all`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload ?? {}),
      },
      () =>
        structuredClone(
          initialMockJobs.find((j) => j.id === id) ?? initialMockJobs[0]!
        )
    ),

  downloadJobZipUrl: (id: string): string =>
    `${API_BASE}/jobs/${encodeURIComponent(id)}/download.zip`,

  getJobZipDownloadUrl: (id: string): string =>
    `${API_BASE}/jobs/${encodeURIComponent(id)}/download.zip`,

  getJobTranscript: (id: string): Promise<Transcript> =>
    request<Transcript>(
      `/jobs/${encodeURIComponent(id)}/transcript`,
      undefined,
      () =>
        structuredClone(
          mockTranscriptsByJobId[id] ?? mockTranscriptsByJobId.job_hinglish_01!
        )
    ),

  getTranscript: (id: string): Promise<Transcript> =>
    apiClient.getJobTranscript(id),

  patchJobTranscript: (
    id: string,
    payload: PatchTranscriptPayload
  ): Promise<Transcript> =>
    request<Transcript>(
      `/jobs/${encodeURIComponent(id)}/transcript`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      () =>
        structuredClone(
          mockTranscriptsByJobId[id] ?? mockTranscriptsByJobId.job_hinglish_01!
        )
    ),

  initUpload: (payload: InitUploadPayload): Promise<UploadSession> =>
    request<UploadSession>(
      "/uploads",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      () => ({
        id: `upl_mock_${Date.now().toString(36)}`,
        filename: payload.filename,
        size_bytes: payload.size_bytes,
        received_chunks: 0,
        status: "INIT",
        upload_id: null,
      })
    ),

  uploadChunk: (
    id: string,
    chunkIndex: number,
    chunk: Blob | ArrayBuffer
  ): Promise<UploadSession> =>
    request<UploadSession>(
      `/uploads/${encodeURIComponent(id)}/chunk${buildQueryString({ n: chunkIndex })}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/octet-stream" },
        body: chunk,
      },
      () => ({
        id,
        filename: "upload.mp4",
        size_bytes: 1024,
        received_chunks: chunkIndex + 1,
        status: "UPLOADING",
        upload_id: null,
      })
    ),

  completeUpload: (id: string): Promise<UploadSession> =>
    request<UploadSession>(
      `/uploads/${encodeURIComponent(id)}/complete`,
      {
        method: "POST",
      },
      () => ({
        id,
        filename: "upload.mp4",
        size_bytes: 1024,
        received_chunks: 1,
        status: "COMPLETED",
        upload_id: `upload_${id}`,
      })
    ),

  listClips: async (jobId: string): Promise<ClipSummary[]> => {
    const job = await apiClient.getJob(jobId);
    return job.clips ?? [];
  },

  getClip: (id: string): Promise<ClipDetail> =>
    request<ClipDetail>(`/clips/${encodeURIComponent(id)}`, undefined, () =>
      structuredClone(mockClipsById[id] ?? mockClipsById.clip_01!)
    ),

  getClipPlan: async (id: string): Promise<Record<string, unknown>> => {
    const clip = await apiClient.getClip(id);
    return clip.clip_plan;
  },

  patchClip: (id: string, patch: PatchClipPayload): Promise<ClipDetail> =>
    request<ClipDetail>(
      `/clips/${encodeURIComponent(id)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/merge-patch+json" },
        body: JSON.stringify(patch),
      },
      () => {
        const base = structuredClone(
          mockClipsById[id] ?? mockClipsById.clip_01!
        );
        return {
          ...base,
          ...patch,
          clip_plan: {
            ...base.clip_plan,
            ...(typeof patch.clip_plan === "object" && patch.clip_plan !== null
              ? (patch.clip_plan as Record<string, unknown>)
              : {}),
          },
        };
      }
    ),

  patchClipPlan: (id: string, patch: PatchClipPayload): Promise<ClipDetail> =>
    apiClient.patchClip(id, patch),

  renderClip: (
    id: string,
    payload?: RenderClipPayload
  ): Promise<RenderRecord> =>
    request<RenderRecord>(
      `/clips/${encodeURIComponent(id)}/render`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload ?? {}),
      },
      () => ({
        id: `rnd_${Date.now().toString(36)}`,
        clip_id: id,
        plan_hash: "ph_fallback",
        aspect: payload?.aspect ?? "9:16",
        profile: payload?.profile ?? "1080p_h264_fast",
        status: "DONE",
        progress_pct: 100,
        duration_s: 41.5,
        file_url: `/api/v1/renders/rnd_${id}/file`,
        thumb_url: null,
        created_at: new Date().toISOString(),
      })
    ),

  listClipRenders: (id: string): Promise<ClipRendersResponse> =>
    request<ClipRendersResponse>(
      `/clips/${encodeURIComponent(id)}/renders`,
      undefined,
      () => ({
        items: [
          {
            id: "rnd_01",
            clip_id: id,
            plan_hash: "ph_81f2a9",
            aspect: "9:16",
            profile: "1080p_h264_fast",
            status: "DONE",
            progress_pct: 100,
            duration_s: 41.5,
            file_url: "/api/v1/renders/rnd_01/file",
            thumb_url: null,
            created_at: "2026-11-18T12:14:00Z",
          },
        ],
      })
    ),

  submitClipFeedback: (
    id: string,
    payload: ClipFeedbackPayload
  ): Promise<ClipDetail> =>
    request<ClipDetail>(
      `/clips/${encodeURIComponent(id)}/feedback`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      () => ({
        ...structuredClone(mockClipsById[id] ?? mockClipsById.clip_01!),
        feedback: payload.value,
        actual_views: payload.actual_views ?? null,
      })
    ),

  getClipPublishPack: (id: string): Promise<PublishPack> =>
    request<PublishPack>(
      `/clips/${encodeURIComponent(id)}/publish-pack`,
      undefined,
      () =>
        structuredClone(
          mockPublishPacksByClipId[id] ?? mockPublishPacksByClipId.clip_01!
        )
    ),

  getPublishPack: (id: string): Promise<PublishPack> =>
    apiClient.getClipPublishPack(id),

  updateClipPublishPack: (
    id: string,
    payload: UpdatePublishPackPayload
  ): Promise<PublishPack> =>
    request<PublishPack>(
      `/clips/${encodeURIComponent(id)}/publish-pack`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      () => ({
        ...structuredClone(
          mockPublishPacksByClipId[id] ?? mockPublishPacksByClipId.clip_01!
        ),
        ...payload,
      })
    ),

  saveClipPublishPack: (
    id: string,
    payload: UpdatePublishPackPayload
  ): Promise<PublishPack> => apiClient.updateClipPublishPack(id, payload),

  updatePublishPack: (
    id: string,
    payload: UpdatePublishPackPayload
  ): Promise<PublishPack> => apiClient.updateClipPublishPack(id, payload),

  regenerateClipPublishPack: (
    id: string,
    payload?: RegeneratePublishPackPayload
  ): Promise<PublishPack> =>
    request<PublishPack>(
      `/clips/${encodeURIComponent(id)}/publish-pack/regenerate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload ?? {}),
      },
      () => {
        const base = structuredClone(
          mockPublishPacksByClipId[id] ?? mockPublishPacksByClipId.clip_01!
        );
        return {
          ...base,
          platform: payload?.platform ?? base.platform,
          title: `[Refreshed] ${base.title}`.slice(0, 95),
        };
      }
    ),

  regeneratePublishPack: (
    id: string,
    payload?: RegeneratePublishPackPayload
  ): Promise<PublishPack> => apiClient.regenerateClipPublishPack(id, payload),

  listTemplates: (params?: ListTemplatesQuery): Promise<TemplateListResponse> =>
    request<TemplateListResponse>(
      `/templates${buildQueryString({
        kind: params?.kind,
        status: params?.status,
      })}`,
      undefined,
      () => ({
        items:
          currentSimulationMode === "empty"
            ? []
            : structuredClone(mockTemplates),
      })
    ),

  getTemplate: (id: string): Promise<TemplateSummary> =>
    request<TemplateSummary>(
      `/templates/${encodeURIComponent(id)}`,
      undefined,
      () =>
        structuredClone(
          mockTemplates.find((t) => t.id === id) ?? mockTemplates[0]!
        )
    ),

  listTemplateVersions: (id: string): Promise<TemplateVersionsResponse> =>
    request<TemplateVersionsResponse>(
      `/templates/${encodeURIComponent(id)}/versions`,
      undefined,
      () => ({
        items: [{ version: 1, id }],
      })
    ),

  previewTemplate: async (
    id: string,
    payload?: PreviewTemplatePayload
  ): Promise<{ previewUrl: string; contentType: string }> => {
    if (currentApiMode === "mock") {
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="640"><text x="20" y="40">${id}</text></svg>`;
      return {
        previewUrl: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`,
        contentType: "image/svg+xml",
      };
    }
    const response = await rawFetch(
      `/templates/${encodeURIComponent(id)}/preview`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "image/png, image/svg+xml, application/json",
        },
        body: JSON.stringify(payload ?? {}),
      }
    );
    const contentType = response.headers.get("Content-Type") ?? "image/png";
    const blob = await response.blob();
    const previewUrl = await blobToPreviewUrl(blob);
    return { previewUrl, contentType };
  },

  listPresets: (): Promise<PresetListResponse> =>
    request<PresetListResponse>("/presets", undefined, () => ({
      items: structuredClone(mockPresets),
    })),

  getDriveStatus: (): Promise<DriveStatus> =>
    request<DriveStatus>("/drive/status", undefined, () =>
      structuredClone(mockDriveStatus)
    ),

  listDriveFiles: (params?: ListDriveFilesQuery): Promise<DriveFilesResponse> =>
    request<DriveFilesResponse>(
      `/drive/files${buildQueryString({ route: params?.route })}`,
      undefined,
      () => ({
        items: structuredClone(mockDriveFiles),
      })
    ),

  getDriveFiles: (params?: ListDriveFilesQuery): Promise<DriveFilesResponse> =>
    apiClient.listDriveFiles(params),

  pickDriveFile: (payload: DrivePickPayload): Promise<DrivePickResponse> =>
    request<DrivePickResponse>(
      "/drive/pick",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      () => ({
        drive_file_id: payload.file_id,
        source_ref: `drive://sources/${payload.file_id}`,
      })
    ),

  pickDriveSource: (
    payload: DrivePickPayload | string
  ): Promise<DrivePickResponse> =>
    apiClient.pickDriveFile(
      typeof payload === "string" ? { file_id: payload } : payload
    ),

  adminListTemplates: (): Promise<AdminTemplatesResponse> =>
    request<AdminTemplatesResponse>("/admin/templates", undefined, () => ({
      items: structuredClone(mockTemplates),
    })),

  adminImportTemplate: (file: File): Promise<TemplateSummary> => {
    const formData = new FormData();
    formData.append("file", file);
    return request<TemplateSummary>(
      "/admin/templates",
      {
        method: "POST",
        body: formData,
      },
      () => ({
        id: `imported_${Date.now().toString(36)}`,
        kind: "caption",
        name: file.name || "Imported Template Draft",
        status: "draft",
        latest_version: 1,
        source: "upload",
        valid: true,
        validation_error: null,
      })
    );
  },

  adminPublishTemplate: (id: string): Promise<TemplateSummary> =>
    request<TemplateSummary>(
      `/admin/templates/${encodeURIComponent(id)}/publish`,
      { method: "POST" },
      () => ({
        ...structuredClone(
          mockTemplates.find((t) => t.id === id) ?? mockTemplates[0]!
        ),
        status: "published",
      })
    ),

  adminArchiveTemplate: (id: string): Promise<TemplateSummary> =>
    request<TemplateSummary>(
      `/admin/templates/${encodeURIComponent(id)}/archive`,
      { method: "POST" },
      () => ({
        ...structuredClone(
          mockTemplates.find((t) => t.id === id) ?? mockTemplates[0]!
        ),
        status: "archived",
      })
    ),

  adminDuplicateTemplate: (id: string): Promise<TemplateSummary> =>
    request<TemplateSummary>(
      `/admin/templates/${encodeURIComponent(id)}/duplicate`,
      { method: "POST" },
      () => ({
        ...structuredClone(
          mockTemplates.find((t) => t.id === id) ?? mockTemplates[0]!
        ),
        id: `${id}_copy`,
        status: "draft",
      })
    ),

  adminExportTemplate: async (id: string): Promise<Blob> => {
    const response = await rawFetch(
      `/admin/templates/${encodeURIComponent(id)}/export`,
      { method: "GET" }
    );
    return response.blob();
  },

  adminListPresets: (): Promise<AdminPresetsResponse> =>
    request<AdminPresetsResponse>("/admin/presets", undefined, () => ({
      items: structuredClone(mockPresets),
    })),

  adminSavePreset: (preset: Preset): Promise<Preset> =>
    request<Preset>(
      "/admin/presets",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(preset),
      },
      () => structuredClone(preset)
    ),

  adminListFonts: (): Promise<AdminFontsResponse> =>
    request<AdminFontsResponse>("/admin/fonts", undefined, () => ({
      items: structuredClone(mockFonts),
    })),

  adminUploadFont: (file: File, license: string): Promise<AdminFontRecord> => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("license", license);
    return request<AdminFontRecord>(
      "/admin/fonts",
      {
        method: "POST",
        body: formData,
      },
      () => ({
        id: `font_${Date.now().toString(36)}`,
        family: file.name.replace(/\..*$/, "") || "CustomFont",
        filename: file.name,
        license,
        license_ok: true,
      })
    );
  },

  adminListBrandKits: (): Promise<AdminBrandKitsResponse> =>
    request<AdminBrandKitsResponse>("/admin/brandkits", undefined, () => ({
      items: structuredClone(mockBrandKits),
    })),

  adminSaveBrandKit: (
    payload: AdminBrandKitPayload
  ): Promise<AdminBrandKitRecord> =>
    request<AdminBrandKitRecord>(
      "/admin/brandkits",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      () => ({ ...payload, id: String(payload.id ?? "kit_new") })
    ),

  adminListPrompts: (): Promise<AdminPromptsResponse> =>
    request<AdminPromptsResponse>("/admin/prompts", undefined, () => ({
      items: mockAdminPrompts.map((p) => ({
        name: p.name,
        path: p.path,
        output: p.output,
        active_version: p.active_version,
        updated_at: p.updated_at,
        version_count: p.versions.length,
      })),
    })),

  listPrompts: (): Promise<AdminPromptsResponse> =>
    apiClient.adminListPrompts(),

  adminGetPrompt: (name: string): Promise<AdminPromptDetail> =>
    request<AdminPromptDetail>(
      `/admin/prompts/${encodeURIComponent(name)}`,
      undefined,
      () =>
        structuredClone(
          mockAdminPrompts.find((p) => p.name === name) ?? mockAdminPrompts[0]!
        ) as unknown as AdminPromptDetail
    ),

  adminUpdatePrompt: (
    name: string,
    payload: AdminUpdatePromptPayload
  ): Promise<AdminPromptDetail> =>
    request<AdminPromptDetail>(
      `/admin/prompts/${encodeURIComponent(name)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      () =>
        structuredClone(
          mockAdminPrompts.find((p) => p.name === name) ?? mockAdminPrompts[0]!
        ) as unknown as AdminPromptDetail
    ),

  putPrompt: (
    name: string,
    payload: AdminUpdatePromptPayload | string
  ): Promise<AdminPromptDetail> =>
    apiClient.adminUpdatePrompt(
      name,
      typeof payload === "string" ? { content: payload } : payload
    ),

  adminActivatePrompt: (
    name: string,
    payload: AdminActivatePromptPayload
  ): Promise<AdminPromptDetail> =>
    request<AdminPromptDetail>(
      `/admin/prompts/${encodeURIComponent(name)}/activate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      () =>
        structuredClone(
          mockAdminPrompts.find((p) => p.name === name) ?? mockAdminPrompts[0]!
        ) as unknown as AdminPromptDetail
    ),

  adminTestPrompt: (name: string): Promise<AdminTestPromptResponse> =>
    request<AdminTestPromptResponse>(
      `/admin/prompts/${encodeURIComponent(name)}/test`,
      { method: "POST" },
      () => ({
        prompt: name,
        schema_valid: true,
        latency_ms: 412,
      })
    ),

  adminGetScoring: (): Promise<AdminScoringResponse> =>
    request<AdminScoringResponse>("/admin/scoring", undefined, () =>
      structuredClone(mockAdminScoring)
    ),

  getScoringWeights: (): Promise<AdminScoringResponse> =>
    apiClient.adminGetScoring(),

  adminUpdateScoring: (
    payload: AdminUpdateScoringPayload
  ): Promise<AdminScoringResponse> =>
    request<AdminScoringResponse>(
      "/admin/scoring",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      () => ({
        ...structuredClone(mockAdminScoring),
        ...payload,
      })
    ),

  putScoringWeights: (
    payload: AdminUpdateScoringPayload
  ): Promise<AdminScoringResponse> => apiClient.adminUpdateScoring(payload),

  adminDryRunScoring: (
    payload: AdminDryRunScoringPayload
  ): Promise<AdminDryRunScoringResponse> =>
    request<AdminDryRunScoringResponse>(
      "/admin/scoring/dry-run",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      () => ({
        job_id: payload.job_id,
        old_picks: [],
        new_picks: [],
      })
    ),

  adminGetDrive: (): Promise<DriveStatus> =>
    request<DriveStatus>("/admin/drive", undefined, () =>
      structuredClone(mockDriveStatus)
    ),

  triggerDriveSync: (payload?: DriveSyncPayload): Promise<DriveStatus> =>
    request<DriveStatus>(
      "/drive/sync",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload ?? { full: false }),
      },
      () => ({
        ...structuredClone(mockDriveStatus),
        last_sync_at: new Date().toISOString(),
      })
    ),

  syncDrive: (payload?: DriveSyncPayload): Promise<DriveStatus> =>
    apiClient.triggerDriveSync(payload),

  getDriveTree: (): Promise<DriveTreeResponse> =>
    request<DriveTreeResponse>("/drive/tree", undefined, () =>
      structuredClone(mockDriveTree)
    ),

  adminListBlockers: (): Promise<AdminBlockersResponse> =>
    request<AdminBlockersResponse>("/admin/blockers", undefined, () => ({
      items: structuredClone(mockAdminBlockers),
    })),

  getAdminBlockers: (): Promise<AdminBlockersResponse> =>
    apiClient.adminListBlockers(),

  listBlockers: (): Promise<AdminBlockersResponse> =>
    apiClient.adminListBlockers(),

  adminResolveBlocker: (
    id: string,
    payload?: AdminResolveBlockerPayload
  ): Promise<Blocker> =>
    request<Blocker>(
      `/admin/blockers/${encodeURIComponent(id)}/resolve`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload ?? {}),
      },
      () => ({
        ...structuredClone(
          mockAdminBlockers.find((b) => b.id === id) ?? mockAdminBlockers[0]!
        ),
        id,
        status: "RESOLVED",
        resolved_at: new Date().toISOString(),
      })
    ),

  resolveAdminBlocker: (
    id: string,
    payload?: AdminResolveBlockerPayload | string
  ): Promise<Blocker> =>
    apiClient.adminResolveBlocker(
      id,
      typeof payload === "string" ? { note: payload } : payload
    ),

  resolveBlocker: (
    id: string,
    payload?: AdminResolveBlockerPayload | string
  ): Promise<Blocker> =>
    apiClient.adminResolveBlocker(
      id,
      typeof payload === "string" ? { note: payload } : payload
    ),

  adminListJobs: (): Promise<PaginatedJobs> =>
    request<PaginatedJobs>("/admin/jobs", undefined, () => ({
      items: structuredClone(initialMockJobs),
      next_cursor: null,
    })),

  adminGetSettings: (): Promise<AdminSettingsResponse> =>
    request<AdminSettingsResponse>("/admin/settings", undefined, () =>
      structuredClone(mockAdminSettings)
    ),

  getAdminSettings: (): Promise<AdminSettingsResponse> =>
    apiClient.adminGetSettings(),

  adminUpdateSettings: (
    payload: AdminUpdateSettingsPayload
  ): Promise<AdminSettingsResponse> =>
    request<AdminUpdateSettingsPayload>(
      "/admin/settings",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      () => ({
        ...structuredClone(mockAdminSettings),
        ...payload,
      })
    ),

  patchAdminSettings: (
    payload: AdminUpdateSettingsPayload
  ): Promise<AdminSettingsResponse> => apiClient.adminUpdateSettings(payload),

  adminAuditLog: (limit = 50): Promise<AdminAuditResponse> =>
    request<AdminAuditResponse>(
      `/admin/audit${buildQueryString({ limit })}`,
      undefined,
      () => ({
        items: structuredClone(mockAdminAudit).slice(0, limit),
        next_cursor: null,
      })
    ),

  listLogs: (limit = 50): Promise<AdminAuditResponse> =>
    apiClient.adminAuditLog(limit),

  listMetrics: async (): Promise<{
    health: HealthResponse;
    scoring: AdminScoringResponse;
  }> => {
    const [health, scoring] = await Promise.all([
      apiClient.getHealth(),
      apiClient.adminGetScoring(),
    ]);
    return { health, scoring };
  },

  runMaintenance: async (action = "sync"): Promise<{
    ok: boolean;
    action: string;
    drive: DriveStatus;
  }> => {
    const drive = await apiClient.triggerDriveSync({ full: action === "full" });
    return { ok: true, action, drive };
  },
};

export type ApiClient = typeof apiClient;

export function getApiClient(): ApiClient {
  return apiClient;
}

export const fetchHealth = apiClient.getHealth;
export const fetchTemplates = apiClient.listTemplates;
export const fetchJobs = apiClient.listJobs;
export const fetchJob = apiClient.getJob;
export const createJob = apiClient.createJob;
export const deleteJob = apiClient.deleteJob;
export const cancelJob = apiClient.cancelJob;
export const getTranscript = apiClient.getTranscript;
export const listJobs = apiClient.listJobs;
export const getJob = apiClient.getJob;
export const listClips = apiClient.listClips;
export const getClip = apiClient.getClip;
export const getClipPlan = apiClient.getClipPlan;
export const patchClip = apiClient.patchClip;
export const patchClipPlan = apiClient.patchClipPlan;
export const renderClip = apiClient.renderClip;
export const rerenderClip = apiClient.renderClip;
export const getJobZipDownloadUrl = apiClient.getJobZipDownloadUrl;
export const getClipPublishPack = apiClient.getClipPublishPack;
export const getPublishPack = apiClient.getPublishPack;
export const saveClipPublishPack = apiClient.saveClipPublishPack;
export const updatePublishPack = apiClient.updatePublishPack;
export const regenerateClipPublishPack = apiClient.regenerateClipPublishPack;
export const regeneratePublishPack = apiClient.regeneratePublishPack;
export const getDriveStatus = apiClient.getDriveStatus;
export const getDriveTree = apiClient.getDriveTree;
export const syncDrive = apiClient.syncDrive;
export const triggerDriveSync = apiClient.triggerDriveSync;
export const getDriveFiles = apiClient.getDriveFiles;
export const listDriveFiles = apiClient.listDriveFiles;
export const pickDriveSource = apiClient.pickDriveSource;
export const pickDriveFile = apiClient.pickDriveFile;
export const getAdminBlockers = apiClient.getAdminBlockers;
export const listBlockers = apiClient.listBlockers;
export const resolveAdminBlocker = apiClient.resolveAdminBlocker;
export const resolveBlocker = apiClient.resolveBlocker;
export const getAdminSettings = apiClient.getAdminSettings;
export const patchAdminSettings = apiClient.patchAdminSettings;
export const getScoringWeights = apiClient.getScoringWeights;
export const putScoringWeights = apiClient.putScoringWeights;
export const listPrompts = apiClient.listPrompts;
export const putPrompt = apiClient.putPrompt;
export const listMetrics = apiClient.listMetrics;
export const listLogs = apiClient.listLogs;
export const runMaintenance = apiClient.runMaintenance;

export async function uploadJob(
  file: File,
  options?: Partial<Omit<CreateJobRequest, "source" | "rights_confirmed">>
): Promise<JobDetail> {
  const session = await apiClient.initUpload({
    filename: file.name || "upload.mp4",
    size_bytes: Math.max(1, file.size || 1024),
    mime_type: file.type || "video/mp4",
  });
  await apiClient.uploadChunk(session.id, 0, file);
  const completed = await apiClient.completeUpload(session.id);
  return apiClient.createJob({
    source: {
      kind: "upload",
      upload_id: completed.upload_id ?? completed.id,
    },
    language: options?.language ?? "auto",
    script_pref: options?.script_pref ?? "auto",
    aspect: options?.aspect ?? "9:16",
    preset_id: options?.preset_id ?? "default",
    auto_mode: options?.auto_mode ?? true,
    max_clips: options?.max_clips ?? 8,
    clip_brief: options?.clip_brief ?? file.name,
    rights_confirmed: true,
  });
}
