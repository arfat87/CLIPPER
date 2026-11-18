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
  ErrorEnvelope,
  HealthResponse,
  InitUploadPayload,
  JobDetail,
  ListDriveFilesQuery,
  ListJobsQuery,
  ListTemplatesQuery,
  PaginatedJobs,
  PatchClipPayload,
  Preset,
  PresetListResponse,
  PreviewTemplatePayload,
  RetryJobPayload,
  TemplateListResponse,
  TemplateSummary,
  TemplateVersionsResponse,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }
  const response = await rawFetch(path, { ...init, headers });

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
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
  getHealth: (): Promise<HealthResponse> => request<HealthResponse>("/health"),

  listJobs: (params?: ListJobsQuery | number): Promise<PaginatedJobs> => {
    const query: ListJobsQuery =
      typeof params === "number" ? { limit: params } : (params ?? { limit: 20 });
    return request<PaginatedJobs>(
      `/jobs${buildQueryString({
        limit: query?.limit ?? 20,
        cursor: query?.cursor,
        status: query?.status,
      })}`
    );
  },

  createJob: (payload: CreateJobRequest, idempotencyKey?: string): Promise<JobDetail> =>
    request<JobDetail>("/jobs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
      },
      body: JSON.stringify(payload),
    }),

  getJob: (id: string): Promise<JobDetail> =>
    request<JobDetail>(`/jobs/${encodeURIComponent(id)}`),

  deleteJob: (id: string): Promise<void> =>
    request<void>(`/jobs/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),

  retryJob: (id: string, payload?: RetryJobPayload): Promise<JobDetail> =>
    request<JobDetail>(`/jobs/${encodeURIComponent(id)}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload ?? {}),
    }),

  cancelJob: (id: string): Promise<JobDetail> =>
    request<JobDetail>(`/jobs/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
    }),

  downloadJobZipUrl: (id: string): string =>
    `${API_BASE}/jobs/${encodeURIComponent(id)}/download.zip`,

  initUpload: (payload: InitUploadPayload): Promise<UploadSession> =>
    request<UploadSession>("/uploads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

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
      }
    ),

  completeUpload: (id: string): Promise<UploadSession> =>
    request<UploadSession>(`/uploads/${encodeURIComponent(id)}/complete`, {
      method: "POST",
    }),

  getClip: (id: string): Promise<ClipDetail> =>
    request<ClipDetail>(`/clips/${encodeURIComponent(id)}`),

  patchClip: (id: string, patch: PatchClipPayload): Promise<ClipDetail> =>
    request<ClipDetail>(`/clips/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/merge-patch+json" },
      body: JSON.stringify(patch),
    }),

  listTemplates: (params?: ListTemplatesQuery): Promise<TemplateListResponse> =>
    request<TemplateListResponse>(
      `/templates${buildQueryString({
        kind: params?.kind,
        status: params?.status,
      })}`
    ),

  getTemplate: (id: string): Promise<TemplateSummary> =>
    request<TemplateSummary>(`/templates/${encodeURIComponent(id)}`),

  listTemplateVersions: (id: string): Promise<TemplateVersionsResponse> =>
    request<TemplateVersionsResponse>(
      `/templates/${encodeURIComponent(id)}/versions`
    ),

  previewTemplate: async (
    id: string,
    payload?: PreviewTemplatePayload
  ): Promise<{ previewUrl: string; contentType: string }> => {
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
    request<PresetListResponse>("/presets"),

  getDriveStatus: (): Promise<DriveStatus> =>
    request<DriveStatus>("/drive/status"),

  listDriveFiles: (params?: ListDriveFilesQuery): Promise<DriveFilesResponse> =>
    request<DriveFilesResponse>(
      `/drive/files${buildQueryString({ route: params?.route })}`
    ),

  pickDriveFile: (payload: DrivePickPayload): Promise<DrivePickResponse> =>
    request<DrivePickResponse>("/drive/pick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  adminListTemplates: (): Promise<AdminTemplatesResponse> =>
    request<AdminTemplatesResponse>("/admin/templates"),

  adminImportTemplate: (file: File): Promise<TemplateSummary> => {
    const formData = new FormData();
    formData.append("file", file);
    return request<TemplateSummary>("/admin/templates", {
      method: "POST",
      body: formData,
    });
  },

  adminPublishTemplate: (id: string): Promise<TemplateSummary> =>
    request<TemplateSummary>(
      `/admin/templates/${encodeURIComponent(id)}/publish`,
      { method: "POST" }
    ),

  adminArchiveTemplate: (id: string): Promise<TemplateSummary> =>
    request<TemplateSummary>(
      `/admin/templates/${encodeURIComponent(id)}/archive`,
      { method: "POST" }
    ),

  adminDuplicateTemplate: (id: string): Promise<TemplateSummary> =>
    request<TemplateSummary>(
      `/admin/templates/${encodeURIComponent(id)}/duplicate`,
      { method: "POST" }
    ),

  adminExportTemplate: async (id: string): Promise<Blob> => {
    const response = await rawFetch(
      `/admin/templates/${encodeURIComponent(id)}/export`,
      { method: "GET" }
    );
    return response.blob();
  },

  adminListPresets: (): Promise<AdminPresetsResponse> =>
    request<AdminPresetsResponse>("/admin/presets"),

  adminSavePreset: (preset: Preset): Promise<Preset> =>
    request<Preset>("/admin/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(preset),
    }),

  adminListFonts: (): Promise<AdminFontsResponse> =>
    request<AdminFontsResponse>("/admin/fonts"),

  adminUploadFont: (file: File, license: string): Promise<AdminFontRecord> => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("license", license);
    return request<AdminFontRecord>("/admin/fonts", {
      method: "POST",
      body: formData,
    });
  },

  adminListBrandKits: (): Promise<AdminBrandKitsResponse> =>
    request<AdminBrandKitsResponse>("/admin/brandkits"),

  adminSaveBrandKit: (
    payload: AdminBrandKitPayload
  ): Promise<AdminBrandKitRecord> =>
    request<AdminBrandKitRecord>("/admin/brandkits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  adminListBlockers: (): Promise<AdminBlockersResponse> =>
    request<AdminBlockersResponse>("/admin/blockers"),

  adminListJobs: (): Promise<PaginatedJobs> =>
    request<PaginatedJobs>("/admin/jobs"),

  adminGetSettings: (): Promise<AdminSettingsResponse> =>
    request<AdminSettingsResponse>("/admin/settings"),

  adminAuditLog: (limit = 20): Promise<AdminAuditResponse> =>
    request<AdminAuditResponse>(`/admin/audit${buildQueryString({ limit })}`),
};
