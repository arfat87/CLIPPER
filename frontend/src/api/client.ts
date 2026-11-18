import type {
  AdminAuditResponse,
  AdminBlockersResponse,
  AdminSettingsResponse,
  AdminTemplatesResponse,
  ClipDetail,
  CreateJobRequest,
  DriveStatus,
  ErrorEnvelope,
  HealthResponse,
  JobDetail,
  ListJobsQuery,
  ListTemplatesQuery,
  PaginatedJobs,
  PatchClipPayload,
  PresetListResponse,
  TemplateListResponse,
} from "./types";

const API_BASE = "/api/v1";

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }
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

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
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

  createJob: (payload: CreateJobRequest): Promise<JobDetail> =>
    request<JobDetail>("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  getJob: (id: string): Promise<JobDetail> =>
    request<JobDetail>(`/jobs/${encodeURIComponent(id)}`),

  deleteJob: (id: string): Promise<void> =>
    request<void>(`/jobs/${encodeURIComponent(id)}`, {
      method: "DELETE",
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

  listPresets: (): Promise<PresetListResponse> =>
    request<PresetListResponse>("/presets"),

  getDriveStatus: (): Promise<DriveStatus> =>
    request<DriveStatus>("/drive/status"),

  adminListTemplates: (): Promise<AdminTemplatesResponse> =>
    request<AdminTemplatesResponse>("/admin/templates"),

  adminListBlockers: (): Promise<AdminBlockersResponse> =>
    request<AdminBlockersResponse>("/admin/blockers"),

  adminListJobs: (): Promise<PaginatedJobs> =>
    request<PaginatedJobs>("/admin/jobs"),

  adminGetSettings: (): Promise<AdminSettingsResponse> =>
    request<AdminSettingsResponse>("/admin/settings"),

  adminAuditLog: (limit = 20): Promise<AdminAuditResponse> =>
    request<AdminAuditResponse>(`/admin/audit${buildQueryString({ limit })}`),
};
