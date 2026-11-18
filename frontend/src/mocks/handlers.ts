import { http, HttpResponse, delay } from "msw";
import type {
  ClipDetail,
  CreateJobRequest,
  ErrorEnvelope,
  JobDetail,
  PaginatedJobs,
  PresetListResponse,
  TemplateListResponse,
} from "../api/types";
import {
  initialMockJobs,
  mockClipsById,
  mockDriveStatus,
  mockHealth,
  mockPresets,
  mockTemplates,
} from "./mockData";

let jobsStore: JobDetail[] = structuredClone(initialMockJobs);
let clipsStore: Record<string, ClipDetail> = structuredClone(mockClipsById);

export function resetMockStore(): void {
  jobsStore = structuredClone(initialMockJobs);
  clipsStore = structuredClone(mockClipsById);
}

function getMockMode(request: Request): string | null {
  return request.headers.get("X-Clipper-Mock-Mode");
}

function makeError(
  code: ErrorEnvelope["error"]["code"],
  message: string,
  status: number,
  retryable = true
) {
  const payload: ErrorEnvelope = {
    error: {
      code,
      message,
      retryable,
    },
  };
  return HttpResponse.json(payload, { status });
}

export const handlers = [
  http.get("/api/v1/health", async ({ request }) => {
    if (getMockMode(request) === "error") {
      return makeError(
        "LLM_UNAVAILABLE",
        "Backend health check timed out. Ensure local FastAPI server is running or retry.",
        503
      );
    }
    return HttpResponse.json(mockHealth);
  }),

  http.get("/api/v1/jobs", async ({ request }) => {
    const mode = getMockMode(request);
    if (mode === "error") {
      return makeError(
        "ASR_FAILED",
        "Could not load jobs right now. Check the local worker and retry.",
        500
      );
    }
    if (mode === "empty") {
      const empty: PaginatedJobs = { items: [], next_cursor: null };
      return HttpResponse.json(empty);
    }

    const url = new URL(request.url);
    const limit = Number(url.searchParams.get("limit") ?? "20");
    const statusFilter = url.searchParams.get("status");
    const filtered = statusFilter
      ? jobsStore.filter((j) => j.status === statusFilter)
      : jobsStore;

    const payload: PaginatedJobs = {
      items: filtered.slice(0, limit),
      next_cursor: null,
    };
    return HttpResponse.json(payload);
  }),

  http.post("/api/v1/jobs", async ({ request }) => {
    const body = (await request.json()) as CreateJobRequest;
    await delay(150);

    if (!body.rights_confirmed) {
      return makeError(
        "VALIDATION_ERROR",
        "Please confirm you have rights to clip this video before starting.",
        422,
        false
      );
    }

    const now = new Date().toISOString();
    const id = `job_${Date.now().toString(36)}`;
    const clipId = `clip_${Date.now().toString(36)}`;

    const platform =
      body.source.kind === "url"
        ? body.source.url?.includes("instagram")
          ? "instagram"
          : body.source.url?.includes("facebook")
            ? "facebook"
            : "youtube"
        : body.source.kind;

    const newClip: ClipDetail = {
      id: clipId,
      job_id: id,
      rank: 1,
      start_s: 12.0,
      end_s: 48.0,
      score: 0.89,
      score_mode: "hybrid",
      title: body.clip_brief?.trim() || "Top Viral Hook Extracted",
      reason: "Matched strong opening hook and clear vocal cadence.",
      hook_line: "Yeh 30 second miss mat karna!",
      layout: "fill",
      preset_id: body.preset_id ?? "default",
      status: "READY",
      feedback: null,
      actual_views: null,
      score_breakdown: {
        hook: 0.91,
        payoff: 0.87,
        clarity: 0.9,
        energy: 0.88,
        trend: 0.85,
      },
      clip_plan: {
        schema_version: 1,
        trim: { start_s: 12.0, end_s: 48.0 },
        layout: "fill",
        aspect: body.aspect ?? "9:16",
        template_id: "bold_pop_yellow",
        preset_id: body.preset_id ?? "default",
        caption_preview_lines: ["Yeh 30 second", "MISS mat karna!"],
      },
    };

    const newJob: JobDetail = {
      id,
      source_kind: body.source.kind,
      source_url: body.source.url ?? null,
      source_ref: body.source.drive_file_id ?? body.source.upload_id ?? null,
      platform,
      title:
        body.source.url?.replace(/^https?:\/\//, "").slice(0, 48) ??
        "Uploaded Creator Video",
      duration_s: 620,
      language: body.language ?? "auto",
      script: body.script_pref ?? "auto",
      genre: "creator",
      status: "COMPLETED",
      stage: "package",
      error_code: null,
      error_message: null,
      auto_mode: body.auto_mode ?? true,
      preset_id: body.preset_id ?? "default",
      warnings: [],
      stages: [
        { stage: "fetch", status: "DONE", attempt: 1 },
        { stage: "prepare", status: "DONE", attempt: 1 },
        { stage: "transcribe", status: "DONE", attempt: 1 },
        { stage: "analyze", status: "DONE", attempt: 1 },
        { stage: "select", status: "DONE", attempt: 1 },
        { stage: "render", status: "DONE", attempt: 1 },
        { stage: "package", status: "DONE", attempt: 1 },
      ],
      clips: [newClip],
      created_at: now,
      updated_at: now,
    };

    clipsStore[clipId] = newClip;
    jobsStore = [newJob, ...jobsStore];
    return HttpResponse.json(newJob, { status: 201 });
  }),

  http.get("/api/v1/jobs/:id", ({ params }) => {
    const id = String(params.id);
    const job = jobsStore.find((j) => j.id === id);
    if (!job) {
      return makeError("VALIDATION_ERROR", `Job '${id}' was not found.`, 404, false);
    }
    return HttpResponse.json(job);
  }),

  http.get("/api/v1/clips/:id", ({ params, request }) => {
    if (getMockMode(request) === "error") {
      return makeError(
        "RENDER_FAILED",
        "Clip preview could not be loaded. Retry or reselect a clip.",
        500
      );
    }
    const id = String(params.id);
    const clip = clipsStore[id];
    if (!clip) {
      return makeError("VALIDATION_ERROR", `Clip '${id}' was not found.`, 404, false);
    }
    return HttpResponse.json(clip);
  }),

  http.patch("/api/v1/clips/:id", async ({ params, request }) => {
    const id = String(params.id);
    const existing = clipsStore[id];
    if (!existing) {
      return makeError("VALIDATION_ERROR", `Clip '${id}' was not found.`, 404, false);
    }
    const patch = (await request.json()) as Record<string, unknown>;
    const updated: ClipDetail = {
      ...existing,
      ...patch,
      clip_plan: {
        ...existing.clip_plan,
        ...(typeof patch.clip_plan === "object" && patch.clip_plan !== null
          ? (patch.clip_plan as Record<string, unknown>)
          : {}),
      },
    };
    clipsStore[id] = updated;
    return HttpResponse.json(updated);
  }),

  http.get("/api/v1/templates", ({ request }) => {
    const mode = getMockMode(request);
    if (mode === "error") {
      return makeError(
        "TEMPLATE_INVALID",
        "Failed to load caption templates. Verify assets/templates/ and retry.",
        500
      );
    }
    if (mode === "empty") {
      const empty: TemplateListResponse = { items: [] };
      return HttpResponse.json(empty);
    }
    const payload: TemplateListResponse = { items: mockTemplates };
    return HttpResponse.json(payload);
  }),

  http.get("/api/v1/presets", ({ request }) => {
    const mode = getMockMode(request);
    if (mode === "empty") {
      const empty: PresetListResponse = { items: [] };
      return HttpResponse.json(empty);
    }
    const payload: PresetListResponse = { items: mockPresets };
    return HttpResponse.json(payload);
  }),

  http.get("/api/v1/drive/status", ({ request }) => {
    if (getMockMode(request) === "error") {
      return makeError(
        "DRIVE_AUTH",
        "Google Drive token expired. Reconnect credentials in Settings and retry.",
        401,
        false
      );
    }
    return HttpResponse.json(mockDriveStatus);
  }),
];
