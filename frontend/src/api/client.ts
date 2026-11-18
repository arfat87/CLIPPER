import type {
  ClipDetail,
  CreateJobRequest,
  DriveStatus,
  ErrorEnvelope,
  HealthResponse,
  JobDetail,
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

  return (await response.json()) as T;
}

export const apiClient = {
  getHealth: (): Promise<HealthResponse> => request<HealthResponse>("/health"),

  listJobs: (limit = 20): Promise<PaginatedJobs> =>
    request<PaginatedJobs>(`/jobs?limit=${limit}`),

  createJob: (payload: CreateJobRequest): Promise<JobDetail> =>
    request<JobDetail>("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  getJob: (id: string): Promise<JobDetail> =>
    request<JobDetail>(`/jobs/${encodeURIComponent(id)}`),

  getClip: (id: string): Promise<ClipDetail> =>
    request<ClipDetail>(`/clips/${encodeURIComponent(id)}`),

  patchClip: (id: string, patch: PatchClipPayload): Promise<ClipDetail> =>
    request<ClipDetail>(`/clips/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/merge-patch+json" },
      body: JSON.stringify(patch),
    }),

  listTemplates: (): Promise<TemplateListResponse> =>
    request<TemplateListResponse>("/templates"),

  listPresets: (): Promise<PresetListResponse> =>
    request<PresetListResponse>("/presets"),

  getDriveStatus: (): Promise<DriveStatus> =>
    request<DriveStatus>("/drive/status"),
};
