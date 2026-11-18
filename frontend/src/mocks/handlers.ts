import { delay, http, HttpResponse } from "msw";
import type {
  AdminAuditResponse,
  AdminBlockersResponse,
  AdminBrandKitsResponse,
  AdminFontsResponse,
  AdminPresetsResponse,
  AdminTemplatesResponse,
  Blocker,
  ClipDetail,
  CreateJobRequest,
  DriveFilesResponse,
  DriveStatus,
  ErrorEnvelope,
  JobDetail,
  JobStatus,
  PaginatedJobs,
  Preset,
  PresetListResponse,
  PublishPack,
  RenderRecord,
  StageName,
  TemplateListResponse,
  TemplateSummary,
  Transcript,
  UploadSession,
} from "../api/types";
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
  type MockPromptRecord,
} from "./mockData";

const PIPELINE_STAGES: Array<{ stage: StageName; status: JobStatus; label: string }> = [
  { stage: "fetch", status: "FETCHING", label: "Downloading video & metadata" },
  { stage: "prepare", status: "PREPARING", label: "Normalizing 16kHz WAV & probing streams" },
  { stage: "transcribe", status: "TRANSCRIBING", label: "Transcribing speech with word timestamps" },
  { stage: "analyze", status: "ANALYZING", label: "Scoring hooks, audio peaks & highlights" },
  { stage: "select", status: "SELECTING", label: "Selecting top non-overlapping clips" },
  { stage: "render", status: "RENDERING", label: "Burning Indic captions via FFmpeg + libass" },
  { stage: "package", status: "PACKAGING", label: "Generating Publish Pack & sidecars" },
];

let jobsStore: JobDetail[] = structuredClone(initialMockJobs);
let clipsStore: Record<string, ClipDetail> = structuredClone(mockClipsById);
let templatesStore: TemplateSummary[] = structuredClone(mockTemplates);
let presetsStore: Preset[] = structuredClone(mockPresets);
let fontsStore: Array<Record<string, unknown>> = structuredClone(mockFonts);
let brandKitsStore: Array<Record<string, unknown>> = structuredClone(mockBrandKits);
let promptsStore: MockPromptRecord[] = structuredClone(mockAdminPrompts);
let scoringStore: Record<string, unknown> = structuredClone(mockAdminScoring);
let settingsStore: Record<string, unknown> = structuredClone(mockAdminSettings);
let driveStatusStore: DriveStatus = structuredClone(mockDriveStatus);
let driveTreeStore = structuredClone(mockDriveTree);
let blockersStore: Blocker[] = structuredClone(mockAdminBlockers);
let auditStore: Array<Record<string, unknown>> = structuredClone(mockAdminAudit);
let transcriptsStore: Record<string, Transcript> = structuredClone(
  mockTranscriptsByJobId
);
let publishPacksStore: Record<string, PublishPack> = structuredClone(
  mockPublishPacksByClipId
);
let rendersByClipStore: Record<string, RenderRecord[]> = {
  clip_01: [
    {
      id: "rnd_01",
      clip_id: "clip_01",
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
};
let uploadSessionsStore: Record<string, UploadSession> = {};
let jobPendingClipsStore: Record<string, ClipDetail[]> = {};
let sseStepDelayMs = import.meta.env?.MODE === "test" ? 20 : 450;

export function setMockSseStepDelayMs(ms: number): void {
  sseStepDelayMs = ms;
}

export function resetMockStore(): void {
  jobsStore = structuredClone(initialMockJobs);
  clipsStore = structuredClone(mockClipsById);
  templatesStore = structuredClone(mockTemplates);
  presetsStore = structuredClone(mockPresets);
  fontsStore = structuredClone(mockFonts);
  brandKitsStore = structuredClone(mockBrandKits);
  promptsStore = structuredClone(mockAdminPrompts);
  scoringStore = structuredClone(mockAdminScoring);
  settingsStore = structuredClone(mockAdminSettings);
  driveStatusStore = structuredClone(mockDriveStatus);
  driveTreeStore = structuredClone(mockDriveTree);
  blockersStore = structuredClone(mockAdminBlockers);
  auditStore = structuredClone(mockAdminAudit);
  transcriptsStore = structuredClone(mockTranscriptsByJobId);
  publishPacksStore = structuredClone(mockPublishPacksByClipId);
  rendersByClipStore = {
    clip_01: [
      {
        id: "rnd_01",
        clip_id: "clip_01",
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
  };
  uploadSessionsStore = {};
  jobPendingClipsStore = {};
}

export function setMockJobFailure(
  jobId: string,
  errorCode: string,
  errorMessage: string,
  failedStage: StageName = "fetch"
): void {
  jobsStore = jobsStore.map((job) => {
    if (job.id !== jobId) return job;
    let passedFailed = false;
    return {
      ...job,
      status: "FAILED",
      stage: failedStage,
      error_code: errorCode,
      error_message: errorMessage,
      stages: (job.stages ?? []).map((st) => {
        if (st.stage === failedStage) {
          passedFailed = true;
          return { ...st, status: "FAILED" };
        }
        return { ...st, status: passedFailed ? "PENDING" : "DONE" };
      }),
    };
  });
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

function appendAudit(action: string, target: string, diff?: string): void {
  auditStore = [
    {
      id: `aud_${Date.now().toString(36)}`,
      actor: "owner",
      action,
      target,
      diff: diff ?? `${action} applied on ${target}`,
      timestamp: new Date().toISOString(),
    },
    ...auditStore,
  ];
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
    await delay(40);

    if (!body.rights_confirmed) {
      return makeError(
        "VALIDATION_ERROR",
        "Please confirm you have rights to clip this video before starting.",
        422,
        false
      );
    }

    const now = new Date().toISOString();
    const id = `job_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 5)}`;
    const clipId = `clip_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 5)}`;

    const platform =
      body.source.kind === "url"
        ? body.source.url?.includes("instagram")
          ? "instagram"
          : body.source.url?.includes("facebook") || body.source.url?.includes("fb.watch")
            ? "facebook"
            : "youtube"
        : body.source.kind;

    const clipTitle = body.clip_brief?.trim() || "Top Viral Hook Extracted";
    const newClip: ClipDetail = {
      id: clipId,
      job_id: id,
      rank: 1,
      start_s: 12.0,
      end_s: 48.0,
      score: 0.89,
      score_mode: "hybrid",
      title: clipTitle,
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
        body.clip_brief?.trim() ||
        body.source.url?.replace(/^https?:\/\//, "").slice(0, 48) ||
        "Uploaded Creator Video",
      duration_s: 620,
      language: body.language ?? "auto",
      script: body.script_pref ?? "auto",
      genre: "creator",
      status: "QUEUED",
      stage: "fetch",
      error_code: null,
      error_message: null,
      auto_mode: body.auto_mode ?? true,
      preset_id: body.preset_id ?? "default",
      warnings: [],
      stages: PIPELINE_STAGES.map((s) => ({
        stage: s.stage,
        status: "PENDING",
        attempt: 1,
      })),
      clips: [],
      created_at: now,
      updated_at: now,
    };

    clipsStore[clipId] = newClip;
    jobPendingClipsStore[id] = [newClip];
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

  http.delete("/api/v1/jobs/:id", ({ params }) => {
    const id = String(params.id);
    const exists = jobsStore.some((j) => j.id === id);
    if (!exists) {
      return makeError("VALIDATION_ERROR", `Job '${id}' was not found.`, 404, false);
    }
    jobsStore = jobsStore.filter((j) => j.id !== id);
    return new HttpResponse(null, { status: 204 });
  }),

  http.post("/api/v1/jobs/:id/cancel", ({ params }) => {
    const id = String(params.id);
    const existing = jobsStore.find((j) => j.id === id);
    if (!existing) {
      return makeError("VALIDATION_ERROR", `Job '${id}' was not found.`, 404, false);
    }
    const updated: JobDetail = {
      ...existing,
      status: "CANCELLED",
      updated_at: new Date().toISOString(),
    };
    jobsStore = jobsStore.map((j) => (j.id === id ? updated : j));
    return HttpResponse.json(updated);
  }),

  http.post("/api/v1/jobs/:id/retry", async ({ params, request }) => {
    const id = String(params.id);
    const existing = jobsStore.find((j) => j.id === id);
    if (!existing) {
      return makeError("VALIDATION_ERROR", `Job '${id}' was not found.`, 404, false);
    }
    const body = (await request.json().catch(() => ({}))) as {
      from_stage?: StageName;
    };
    const fromStage = body.from_stage ?? "fetch";
    const startIdx = Math.max(
      0,
      PIPELINE_STAGES.findIndex((s) => s.stage === fromStage)
    );
    const targetStage = PIPELINE_STAGES[startIdx]!;

    const updated: JobDetail = {
      ...existing,
      status: targetStage.status,
      stage: targetStage.stage,
      error_code: null,
      error_message: null,
      stages: PIPELINE_STAGES.map((s, idx) => ({
        stage: s.stage,
        status: idx < startIdx ? "DONE" : idx === startIdx ? "RUNNING" : "PENDING",
        attempt: (existing.stages?.[idx]?.attempt ?? 1) + (idx >= startIdx ? 1 : 0),
      })),
      updated_at: new Date().toISOString(),
    };
    jobsStore = jobsStore.map((j) => (j.id === id ? updated : j));
    return HttpResponse.json(updated, { status: 202 });
  }),

  http.post("/api/v1/jobs/:id/reselect", async ({ params, request }) => {
    const id = String(params.id);
    const existing = jobsStore.find((j) => j.id === id);
    if (!existing) {
      return makeError("VALIDATION_ERROR", `Job '${id}' was not found.`, 404, false);
    }
    const body = (await request.json().catch(() => ({}))) as {
      clip_brief?: string;
      threshold?: number;
      max_clips?: number;
    };

    const threshold = typeof body.threshold === "number" ? body.threshold : 0.4;
    const maxClips = typeof body.max_clips === "number" ? body.max_clips : 8;
    const baseClips = existing.clips ?? [];

    let updatedClips = [...baseClips];
    if (threshold <= 0.5 && updatedClips.length < maxClips) {
      const extraClipId = `clip_reselect_${Date.now().toString(36)}`;
      const extraClip: ClipDetail = {
        id: extraClipId,
        job_id: id,
        rank: updatedClips.length + 1,
        start_s: 340.0,
        end_s: 382.0,
        score: Number(Math.max(threshold + 0.08, 0.62).toFixed(2)),
        score_mode: "hybrid",
        title: body.clip_brief?.trim()
          ? `Reselected: ${body.clip_brief.trim()}`
          : "Customer Retention Truth Bomb (Reselected)",
        reason: `Included after lowering quality threshold to ${threshold.toFixed(2)}.`,
        hook_line: "Pehle 100 customers kaise milte hain?",
        layout: "fill",
        preset_id: existing.preset_id ?? "default",
        status: "READY",
        feedback: null,
        actual_views: null,
        score_breakdown: {
          hook: 0.74,
          pacing: 0.71,
          clarity: 0.79,
          payoff: 0.72,
          novelty: 0.68,
          engagement: 0.73,
        },
        clip_plan: {
          schema_version: 1,
          trim: { start_s: 340.0, end_s: 382.0 },
          layout: "fill",
          aspect: "9:16",
          template_id: "bold_pop_yellow",
          preset_id: existing.preset_id ?? "default",
        },
      };
      clipsStore[extraClipId] = extraClip;
      updatedClips = [...updatedClips, extraClip].slice(0, maxClips);
    }

    const updated: JobDetail = {
      ...existing,
      clips: updatedClips,
      updated_at: new Date().toISOString(),
    };
    jobsStore = jobsStore.map((j) => (j.id === id ? updated : j));
    return HttpResponse.json(updated, { status: 202 });
  }),

  http.post("/api/v1/jobs/:id/render-all", async ({ params, request }) => {
    const id = String(params.id);
    const existing = jobsStore.find((j) => j.id === id);
    if (!existing) {
      return makeError("VALIDATION_ERROR", `Job '${id}' was not found.`, 404, false);
    }
    const body = (await request.json().catch(() => ({}))) as {
      preset_id?: string;
    };
    const nextClips = (existing.clips ?? []).map((c) => {
      const next = {
        ...c,
        preset_id: body.preset_id ?? c.preset_id,
        status: "RENDERED",
      };
      if (clipsStore[c.id]) {
        clipsStore[c.id] = {
          ...clipsStore[c.id]!,
          preset_id: next.preset_id,
          status: "RENDERED",
        };
      }
      return next;
    });
    const updated: JobDetail = {
      ...existing,
      clips: nextClips,
      updated_at: new Date().toISOString(),
    };
    jobsStore = jobsStore.map((j) => (j.id === id ? updated : j));
    return HttpResponse.json(updated, { status: 202 });
  }),

  http.get("/api/v1/jobs/:id/events", ({ params, request }) => {
    const id = String(params.id);
    const url = new URL(request.url);
    const lastEventIdRaw =
      request.headers.get("Last-Event-ID") ??
      url.searchParams.get("last_event_id");
    const lastEventIdx = lastEventIdRaw ? Number(lastEventIdRaw) : 0;

    const job = jobsStore.find((j) => j.id === id);
    if (!job) {
      return makeError("VALIDATION_ERROR", `Job '${id}' was not found.`, 404, false);
    }

    const encoder = new TextEncoder();

    const stream = new ReadableStream({
      async start(controller) {
        const pushFrame = (
          frameId: number,
          event: string,
          data: Record<string, unknown>
        ) => {
          if (frameId <= lastEventIdx) {
            return;
          }
          const payload = `id: ${frameId}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
          controller.enqueue(encoder.encode(payload));
        };

        const currentJob = jobsStore.find((j) => j.id === id);
        if (
          !currentJob ||
          currentJob.status === "COMPLETED" ||
          currentJob.status === "FAILED" ||
          currentJob.status === "CANCELLED"
        ) {
          pushFrame(999, "done", {
            job_id: id,
            status: currentJob?.status ?? "COMPLETED",
          });
          controller.close();
          return;
        }

        const startStageIdx = Math.max(
          0,
          (currentJob.stages ?? []).findIndex((st) => st.status !== "DONE")
        );

        let seq = 1;
        for (let i = startStageIdx; i < PIPELINE_STAGES.length; i++) {
          const latest = jobsStore.find((j) => j.id === id);
          if (!latest || latest.status === "CANCELLED" || latest.status === "FAILED") {
            pushFrame(seq++, "state", {
              job_id: id,
              status: latest?.status ?? "CANCELLED",
              stage: latest?.stage ?? null,
            });
            pushFrame(seq++, "done", {
              job_id: id,
              status: latest?.status ?? "CANCELLED",
            });
            controller.close();
            return;
          }

          const step = PIPELINE_STAGES[i]!;
          const etaRemaining = (PIPELINE_STAGES.length - 1 - i) * 4;

          jobsStore = jobsStore.map((j) =>
            j.id === id
              ? {
                  ...j,
                  status: step.status,
                  stage: step.stage,
                  stages: PIPELINE_STAGES.map((s, idx) => ({
                    stage: s.stage,
                    status: idx < i ? "DONE" : idx === i ? "RUNNING" : "PENDING",
                    attempt: 1,
                  })),
                }
              : j
          );

          pushFrame(seq++, "state", {
            job_id: id,
            status: step.status,
            stage: step.stage,
          });
          pushFrame(seq++, "progress", {
            job_id: id,
            stage: step.stage,
            status: "RUNNING",
            pct: 55,
            message: step.label,
            eta_s: etaRemaining,
          });

          await delay(sseStepDelayMs);

          const postStep = jobsStore.find((j) => j.id === id);
          if (!postStep || postStep.status === "CANCELLED") {
            pushFrame(seq++, "state", {
              job_id: id,
              status: "CANCELLED",
              stage: step.stage,
            });
            pushFrame(seq++, "done", { job_id: id, status: "CANCELLED" });
            controller.close();
            return;
          }

          if (step.stage === "analyze") {
            pushFrame(seq++, "warning", {
              job_id: id,
              message: "Background music detected (-18 LUFS); vocal ducking applied.",
            });
          }

          pushFrame(seq++, "progress", {
            job_id: id,
            stage: step.stage,
            status: "DONE",
            pct: 100,
            message: `${step.stage} complete`,
            eta_s: Math.max(0, etaRemaining - 3),
          });
        }

        const pendingClips = jobPendingClipsStore[id] ?? [];
        jobsStore = jobsStore.map((j) =>
          j.id === id
            ? {
                ...j,
                status: "COMPLETED",
                stage: "package",
                clips: j.clips && j.clips.length > 0 ? j.clips : pendingClips,
                stages: PIPELINE_STAGES.map((s) => ({
                  stage: s.stage,
                  status: "DONE",
                  attempt: 1,
                })),
                updated_at: new Date().toISOString(),
              }
            : j
        );

        pushFrame(seq++, "state", {
          job_id: id,
          status: "COMPLETED",
          stage: "package",
        });
        pushFrame(seq++, "done", {
          job_id: id,
          status: "COMPLETED",
        });
        controller.close();
      },
    });

    return new HttpResponse(stream, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
      },
    });
  }),

  http.post("/api/v1/uploads", async ({ request }) => {
    const body = (await request.json()) as {
      filename: string;
      size_bytes: number;
    };
    const id = `upl_${Date.now().toString(36)}`;
    const session: UploadSession = {
      id,
      filename: body.filename,
      size_bytes: body.size_bytes,
      received_chunks: 0,
      status: "INIT",
      upload_id: null,
    };
    uploadSessionsStore[id] = session;
    return HttpResponse.json(session, { status: 201 });
  }),

  http.put("/api/v1/uploads/:id/chunk", ({ params, request }) => {
    const id = String(params.id);
    const session = uploadSessionsStore[id];
    if (!session) {
      return makeError("VALIDATION_ERROR", "Upload session not found.", 404, false);
    }
    const n = Number(new URL(request.url).searchParams.get("n") ?? "0");
    const updated: UploadSession = {
      ...session,
      received_chunks: Math.max(session.received_chunks ?? 0, n + 1),
      status: "UPLOADING",
    };
    uploadSessionsStore[id] = updated;
    return HttpResponse.json(updated);
  }),

  http.post("/api/v1/uploads/:id/complete", ({ params }) => {
    const id = String(params.id);
    const session = uploadSessionsStore[id];
    if (!session) {
      return makeError("VALIDATION_ERROR", "Upload session not found.", 404, false);
    }
    const updated: UploadSession = {
      ...session,
      status: "COMPLETED",
      upload_id: `upload_${id}`,
    };
    uploadSessionsStore[id] = updated;
    return HttpResponse.json(updated);
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
    jobsStore = jobsStore.map((job) =>
      job.id === updated.job_id
        ? {
            ...job,
            clips: (job.clips ?? []).map((c) =>
              c.id === updated.id ? { ...c, ...updated } : c
            ),
          }
        : job
    );
    return HttpResponse.json(updated);
  }),

  http.post("/api/v1/clips/:id/feedback", async ({ params, request }) => {
    const id = String(params.id);
    const existing = clipsStore[id];
    if (!existing) {
      return makeError("VALIDATION_ERROR", `Clip '${id}' was not found.`, 404, false);
    }
    const body = (await request.json()) as {
      value: "up" | "down";
      actual_views?: number;
    };
    const updated: ClipDetail = {
      ...existing,
      feedback: body.value,
      actual_views:
        body.actual_views !== undefined
          ? body.actual_views
          : existing.actual_views ?? null,
    };
    clipsStore[id] = updated;
    jobsStore = jobsStore.map((job) =>
      job.id === updated.job_id
        ? {
            ...job,
            clips: (job.clips ?? []).map((c) =>
              c.id === updated.id
                ? {
                    ...c,
                    feedback: updated.feedback,
                    actual_views: updated.actual_views,
                  }
                : c
            ),
          }
        : job
    );
    const stats = (scoringStore.feedback_stats ?? {
      up: 14,
      down: 3,
      total: 17,
    }) as { up: number; down: number; total: number };
    scoringStore = {
      ...scoringStore,
      feedback_stats: {
        up: stats.up + (body.value === "up" ? 1 : 0),
        down: stats.down + (body.value === "down" ? 1 : 0),
        total: stats.total + 1,
      },
    };
    return HttpResponse.json(updated);
  }),

  http.post("/api/v1/clips/:id/render", async ({ params, request }) => {
    const id = String(params.id);
    const existing = clipsStore[id];
    if (!existing) {
      return makeError("VALIDATION_ERROR", `Clip '${id}' was not found.`, 404, false);
    }
    const body = (await request.json().catch(() => ({}))) as {
      preset_id?: string;
      aspect?: "9:16" | "1:1" | "4:5";
      profile?: string;
    };
    const renderRec: RenderRecord = {
      id: `rnd_${Date.now().toString(36)}`,
      clip_id: id,
      plan_hash: "ph_live",
      aspect: body.aspect ?? "9:16",
      profile: body.profile ?? "1080p_h264_fast",
      status: "DONE",
      progress_pct: 100,
      duration_s: Math.max(1, existing.end_s - existing.start_s),
      file_url: `/api/v1/renders/rnd_${id}/file`,
      thumb_url: null,
      created_at: new Date().toISOString(),
    };
    rendersByClipStore[id] = [renderRec, ...(rendersByClipStore[id] ?? [])];
    clipsStore[id] = { ...existing, status: "RENDERED" };
    jobsStore = jobsStore.map((job) =>
      job.id === existing.job_id
        ? {
            ...job,
            clips: (job.clips ?? []).map((c) =>
              c.id === id ? { ...c, status: "RENDERED" } : c
            ),
          }
        : job
    );
    return HttpResponse.json(renderRec, { status: 202 });
  }),

  http.get("/api/v1/clips/:id/renders", ({ params }) => {
    const id = String(params.id);
    return HttpResponse.json({
      items: rendersByClipStore[id] ?? [],
    });
  }),

  http.get("/api/v1/jobs/:id/transcript", ({ params }) => {
    const id = String(params.id);
    const transcript =
      transcriptsStore[id] ?? mockTranscriptsByJobId.job_hinglish_01!;
    return HttpResponse.json(transcript);
  }),

  http.patch("/api/v1/jobs/:id/transcript", async ({ params, request }) => {
    const id = String(params.id);
    const current =
      transcriptsStore[id] ??
      structuredClone(mockTranscriptsByJobId.job_hinglish_01!);
    const body = (await request.json()) as {
      edits: Array<{ idx: number; text: string }>;
    };
    const editMap = new Map(body.edits.map((e) => [e.idx, e.text]));
    let flatIdx = 0;
    const nextSegments = current.segments.map((seg) => {
      const words = Array.isArray(seg.words)
        ? (seg.words as Array<{
            w: string;
            s: number;
            e: number;
            p?: number;
            flags?: string[];
          }>)
        : [];
      const updatedWords = words.map((word) => {
        const myIdx = flatIdx++;
        const nextText = editMap.get(myIdx);
        if (nextText !== undefined) {
          const prevFlags = Array.isArray(word.flags)
            ? word.flags.filter((f) => f !== "low_conf")
            : [];
          return {
            ...word,
            w: nextText,
            flags: Array.from(new Set([...prevFlags, "edited"])),
          };
        }
        return word;
      });
      return {
        ...seg,
        words: updatedWords,
        text: updatedWords.map((w) => w.w).join(" "),
      };
    });
    const updated: Transcript = {
      ...current,
      segments: nextSegments,
    };
    transcriptsStore[id] = updated;
    return HttpResponse.json(updated);
  }),

  http.get("/api/v1/clips/:id/publish-pack", ({ params }) => {
    const id = String(params.id);
    const existing = publishPacksStore[id];
    if (existing) {
      return HttpResponse.json(existing);
    }
    const clip = clipsStore[id];
    const fallback: PublishPack = {
      schema_version: 1,
      clip_id: id,
      platform: "youtube_shorts",
      language: "hinglish",
      title: clip?.title ?? `Highlight Clip ${id}`,
      title_options: [
        clip?.title ?? `Highlight Clip ${id}`,
        "Wait till the end for this key insight!",
      ],
      description:
        clip?.reason ?? "Top selected clip with burned-in captions.",
      hashtags: ["#Shorts", "#Creators", "#CLIPPER"],
      tags: ["shorts", "ai clips"],
      pinned_comment: "What did you think of this takeaway?",
      hook_line: clip?.hook_line ?? null,
      valid: true,
      warnings: [],
    };
    publishPacksStore[id] = fallback;
    return HttpResponse.json(fallback);
  }),

  http.put("/api/v1/clips/:id/publish-pack", async ({ params, request }) => {
    const id = String(params.id);
    const current =
      publishPacksStore[id] ?? mockPublishPacksByClipId.clip_01!;
    const body = (await request.json()) as {
      title?: string;
      description?: string;
      hashtags?: string[];
      tags?: string[];
      pinned_comment?: string | null;
    };
    const nextTitle = body.title ?? current.title;
    const nextHashtags = body.hashtags ?? current.hashtags;
    const warnings: string[] = [];
    if (nextTitle.length > 70) {
      warnings.push("Title exceeds 70 characters (may truncate on Shorts).");
    }
    if (nextHashtags.length > 15) {
      warnings.push("More than 15 hashtags is rejected by YouTube/Instagram.");
    }
    const updated: PublishPack = {
      ...current,
      ...body,
      title: nextTitle,
      hashtags: nextHashtags,
      valid: nextTitle.length <= 100 && nextHashtags.length <= 15,
      warnings,
    };
    publishPacksStore[id] = updated;
    return HttpResponse.json(updated);
  }),

  http.post(
    "/api/v1/clips/:id/publish-pack/regenerate",
    async ({ params, request }) => {
      const id = String(params.id);
      const current =
        publishPacksStore[id] ?? mockPublishPacksByClipId.clip_01!;
      const body = (await request.json().catch(() => ({}))) as {
        template_id?: string;
        platform?: PublishPack["platform"];
      };
      const platform = body.platform ?? current.platform;
      const updated: PublishPack = {
        ...current,
        platform,
        title: `[Refreshed] ${current.title}`.slice(0, 95),
        title_options: [
          `[Refreshed] ${current.title}`.slice(0, 95),
          "Do NOT skip this founder lesson before scaling!",
          "Why most creators miss this 3-second hook",
        ],
        valid: true,
        warnings: [],
      };
      publishPacksStore[id] = updated;
      return HttpResponse.json(updated);
    }
  ),

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

    const url = new URL(request.url);
    const kindFilter = url.searchParams.get("kind");
    const statusFilter = url.searchParams.get("status");
    const items = templatesStore.filter((tpl) => {
      if (kindFilter && tpl.kind !== kindFilter) {
        return false;
      }
      if (statusFilter && tpl.status !== statusFilter) {
        return false;
      }
      return true;
    });

    const payload: TemplateListResponse = { items };
    return HttpResponse.json(payload);
  }),

  http.post("/api/v1/templates/:id/preview", async ({ params, request }) => {
    const id = String(params.id);
    const body = (await request.json().catch(() => ({}))) as {
      language?: string;
      sample_text?: string;
    };
    const lang = body.language ?? "hinglish";
    const sample =
      body.sample_text ??
      (lang === "hi"
        ? "पहले ३ सेकंड में ध्यान खींचें"
        : lang === "bn"
          ? "এক ক্লিকেই ভিডিও ভাইরাল!"
          : "90% startups yahi GALTI karte hain!");

    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="640" viewBox="0 0 360 640">
      <rect width="360" height="640" fill="#141820"/>
      <rect x="24" y="90" width="312" height="390" fill="none" stroke="#3DDC84" stroke-dasharray="6 4"/>
      <text x="180" y="60" fill="#9AA6B8" font-family="sans-serif" font-size="14" text-anchor="middle">libass test frame · ${id} (${lang})</text>
      <text x="180" y="420" fill="#FFD400" stroke="#000000" stroke-width="2" font-family="sans-serif" font-weight="800" font-size="22" text-anchor="middle">${sample}</text>
    </svg>`;

    return new HttpResponse(svg, {
      status: 200,
      headers: { "Content-Type": "image/svg+xml" },
    });
  }),

  http.get("/api/v1/presets", ({ request }) => {
    const mode = getMockMode(request);
    if (mode === "empty") {
      const empty: PresetListResponse = { items: [] };
      return HttpResponse.json(empty);
    }
    const payload: PresetListResponse = { items: presetsStore };
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
    return HttpResponse.json(driveStatusStore);
  }),

  http.post("/api/v1/drive/sync", async () => {
    driveStatusStore = {
      ...driveStatusStore,
      status: "CONNECTED",
      last_sync_at: new Date().toISOString(),
      last_error: null,
    };
    appendAudit("drive.sync", "drive_mirror", "synced folders from Drive root");
    return HttpResponse.json(driveStatusStore, { status: 202 });
  }),

  http.get("/api/v1/drive/tree", () => {
    return HttpResponse.json(driveTreeStore);
  }),

  http.get("/api/v1/drive/files", ({ request }) => {
    const route = new URL(request.url).searchParams.get("route");
    const items = route
      ? mockDriveFiles.filter((f) => f.route === route)
      : mockDriveFiles;
    const payload: DriveFilesResponse = { items };
    return HttpResponse.json(payload);
  }),

  http.post("/api/v1/drive/pick", async ({ request }) => {
    const body = (await request.json()) as { file_id: string };
    return HttpResponse.json({
      drive_file_id: body.file_id,
      source_ref: `drive://sources/${body.file_id}`,
    });
  }),

  http.get("/api/v1/admin/templates", ({ request }) => {
    const mode = getMockMode(request);
    if (mode === "empty") {
      const empty: AdminTemplatesResponse = { items: [] };
      return HttpResponse.json(empty);
    }
    const payload: AdminTemplatesResponse = { items: templatesStore };
    return HttpResponse.json(payload);
  }),

  http.post("/api/v1/admin/templates", async ({ request }) => {
    let rawId = `imported_${Date.now().toString(36)}`;
    let rawName = "Imported Template Draft";

    try {
      const formData = await request.formData();
      const file = formData.get("file");
      if (file && typeof (file as File).text === "function") {
        const text = await (file as File).text().catch(() => "");
        try {
          const parsed = JSON.parse(text) as Record<string, unknown>;
          const meta = (parsed.meta ?? {}) as Record<string, unknown>;
          if (typeof meta.id === "string" && meta.id) rawId = meta.id;
          else if (typeof parsed.id === "string" && parsed.id) rawId = parsed.id;
          if (typeof meta.name === "string" && meta.name) rawName = meta.name;
          else if (typeof parsed.name === "string" && parsed.name) rawName = parsed.name;
        } catch {
          const fname = (file as File).name;
          if (fname) {
            rawName = fname.replace(/\.(cliptpl\.zip|json)$/i, "") || rawName;
          }
        }
      }
    } catch {
      // jsdom FormData fallback when running in Vitest
    }

    const imported: TemplateSummary = {
      id: rawId,
      kind: "caption",
      name: rawName,
      status: "draft",
      latest_version: 1,
      source: "upload",
      valid: true,
      validation_error: null,
    };

    templatesStore = [
      imported,
      ...templatesStore.filter((t) => t.id !== imported.id),
    ];
    appendAudit("template.import", `${imported.id}@v1`);
    return HttpResponse.json(imported, { status: 201 });
  }),

  http.post("/api/v1/admin/templates/:id/publish", ({ params }) => {
    const id = String(params.id);
    const existing = templatesStore.find((t) => t.id === id);
    if (!existing) {
      return makeError("TEMPLATE_INVALID", `Template '${id}' not found.`, 404, false);
    }
    const updated: TemplateSummary = {
      ...existing,
      status: "published",
      valid: true,
      latest_version:
        existing.status === "published"
          ? existing.latest_version + 1
          : existing.latest_version,
    };
    templatesStore = templatesStore.map((t) => (t.id === id ? updated : t));
    appendAudit("template.publish", `${updated.id}@v${updated.latest_version}`);
    return HttpResponse.json(updated);
  }),

  http.post("/api/v1/admin/templates/:id/archive", ({ params }) => {
    const id = String(params.id);
    const existing = templatesStore.find((t) => t.id === id);
    if (!existing) {
      return makeError("TEMPLATE_INVALID", `Template '${id}' not found.`, 404, false);
    }
    const updated: TemplateSummary = { ...existing, status: "archived" };
    templatesStore = templatesStore.map((t) => (t.id === id ? updated : t));
    appendAudit("template.archive", updated.id);
    return HttpResponse.json(updated);
  }),

  http.post("/api/v1/admin/templates/:id/duplicate", ({ params }) => {
    const id = String(params.id);
    const existing = templatesStore.find((t) => t.id === id);
    if (!existing) {
      return makeError("TEMPLATE_INVALID", `Template '${id}' not found.`, 404, false);
    }
    const copy: TemplateSummary = {
      ...existing,
      id: `${existing.id}_copy_${Date.now().toString(36).slice(-4)}`,
      name: `${existing.name} (Copy)`,
      status: "draft",
      latest_version: 1,
      source: "upload",
    };
    templatesStore = [copy, ...templatesStore];
    appendAudit("template.duplicate", copy.id);
    return HttpResponse.json(copy, { status: 201 });
  }),

  http.get("/api/v1/admin/templates/:id/export", ({ params }) => {
    const id = String(params.id);
    const existing = templatesStore.find((t) => t.id === id);
    return new HttpResponse(
      JSON.stringify(existing ?? { id }, null, 2),
      {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "Content-Disposition": `attachment; filename="${id}.json"`,
        },
      }
    );
  }),

  http.get("/api/v1/admin/presets", ({ request }) => {
    if (getMockMode(request) === "empty") {
      const empty: AdminPresetsResponse = { items: [] };
      return HttpResponse.json(empty);
    }
    const payload: AdminPresetsResponse = { items: presetsStore };
    return HttpResponse.json(payload);
  }),

  http.post("/api/v1/admin/presets", async ({ request }) => {
    const body = (await request.json()) as Preset;
    if (body.is_default) {
      presetsStore = presetsStore.map((p) => ({ ...p, is_default: false }));
    }
    const idx = presetsStore.findIndex((p) => p.id === body.id);
    const saved: Preset = {
      ...body,
      version: idx >= 0 ? presetsStore[idx]!.version + 1 : body.version || 1,
    };
    if (idx >= 0) {
      presetsStore = presetsStore.map((p) => (p.id === body.id ? saved : p));
    } else {
      presetsStore = [saved, ...presetsStore];
    }
    appendAudit("preset.save", saved.id);
    return HttpResponse.json(saved);
  }),

  http.get("/api/v1/admin/fonts", ({ request }) => {
    if (getMockMode(request) === "empty") {
      const empty: AdminFontsResponse = { items: [] };
      return HttpResponse.json(empty);
    }
    const payload: AdminFontsResponse = { items: fontsStore };
    return HttpResponse.json(payload);
  }),

  http.post("/api/v1/admin/fonts", async ({ request }) => {
    let filename = `CustomFont_${Date.now().toString(36)}.ttf`;
    let license = "OFL-1.1";

    try {
      const formData = await request.formData();
      const file = formData.get("file");
      const rawLic = formData.get("license");
      if (typeof rawLic === "string" && rawLic.trim()) {
        license = rawLic;
      }
      if (file && typeof (file as File).name === "string" && (file as File).name) {
        filename = (file as File).name;
      }
    } catch {
      // jsdom FormData fallback when running in Vitest
    }

    const family = filename.replace(/\.(ttf|otf|woff2)$/i, "").replace(/[-_].*$/, "");
    const record: Record<string, unknown> = {
      id: `font_${Date.now().toString(36)}`,
      family: family || "CustomFont",
      filename,
      weight: 700,
      license,
      license_ok: license.toUpperCase() !== "UNKNOWN" && license.trim().length > 0,
      coverage: { latn: true, deva: true, beng: false },
      is_default_fallback: false,
      fallback_scripts: [],
    };
    fontsStore = [record, ...fontsStore];
    appendAudit("font.upload", filename);
    return HttpResponse.json(record, { status: 201 });
  }),

  http.get("/api/v1/admin/brandkits", ({ request }) => {
    if (getMockMode(request) === "empty") {
      const empty: AdminBrandKitsResponse = { items: [] };
      return HttpResponse.json(empty);
    }
    const payload: AdminBrandKitsResponse = { items: brandKitsStore };
    return HttpResponse.json(payload);
  }),

  http.post("/api/v1/admin/brandkits", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const id =
      typeof body.id === "string" && body.id.trim()
        ? body.id
        : `kit_${Date.now().toString(36)}`;
    const saved = { ...body, id };
    const exists = brandKitsStore.some((k) => k.id === id);
    brandKitsStore = exists
      ? brandKitsStore.map((k) => (k.id === id ? saved : k))
      : [saved, ...brandKitsStore];
    appendAudit("brandkit.save", id);
    return HttpResponse.json(saved, { status: 201 });
  }),

  http.get("/api/v1/admin/prompts", ({ request }) => {
    if (getMockMode(request) === "empty") {
      return HttpResponse.json({ items: [] });
    }
    return HttpResponse.json({
      items: promptsStore.map((p) => ({
        name: p.name,
        path: p.path,
        output: p.output,
        active_version: p.active_version,
        updated_at: p.updated_at,
        version_count: p.versions.length,
      })),
    });
  }),

  http.get("/api/v1/admin/prompts/:name", ({ params }) => {
    const name = String(params.name);
    const found = promptsStore.find((p) => p.name === name);
    if (!found) {
      return makeError("VALIDATION_ERROR", `Prompt '${name}' not found.`, 404, false);
    }
    return HttpResponse.json(found);
  }),

  http.put("/api/v1/admin/prompts/:name", async ({ params, request }) => {
    const name = String(params.name);
    const found = promptsStore.find((p) => p.name === name);
    if (!found) {
      return makeError("VALIDATION_ERROR", `Prompt '${name}' not found.`, 404, false);
    }
    const body = (await request.json()) as { content: string };
    const nextVersion =
      Math.max(...found.versions.map((v) => v.version), 0) + 1;
    const now = new Date().toISOString();
    const updated: MockPromptRecord = {
      ...found,
      active_version: nextVersion,
      updated_at: now,
      versions: [
        ...found.versions,
        {
          version: nextVersion,
          created_at: now,
          author: "owner",
          content: body.content,
        },
      ],
    };
    promptsStore = promptsStore.map((p) => (p.name === name ? updated : p));
    appendAudit(
      "prompt.update",
      `${name}@v${nextVersion}`,
      `saved version v${nextVersion}`
    );
    return HttpResponse.json(updated);
  }),

  http.post("/api/v1/admin/prompts/:name/activate", async ({ params, request }) => {
    const name = String(params.name);
    const found = promptsStore.find((p) => p.name === name);
    if (!found) {
      return makeError("VALIDATION_ERROR", `Prompt '${name}' not found.`, 404, false);
    }
    const body = (await request.json()) as { version: number };
    const prev = found.active_version;
    const updated: MockPromptRecord = {
      ...found,
      active_version: body.version,
      updated_at: new Date().toISOString(),
    };
    promptsStore = promptsStore.map((p) => (p.name === name ? updated : p));
    appendAudit(
      "prompt.activate",
      `${name}@v${body.version}`,
      `active_version: v${prev} -> v${body.version}`
    );
    return HttpResponse.json(updated);
  }),

  http.post("/api/v1/admin/prompts/:name/test", ({ params }) => {
    const name = String(params.name);
    const found = promptsStore.find((p) => p.name === name);
    if (!found) {
      return makeError("VALIDATION_ERROR", `Prompt '${name}' not found.`, 404, false);
    }
    return HttpResponse.json({
      prompt: name,
      version: found.active_version,
      schema_valid: true,
      latency_ms: 412,
      fixture: "hinglish_podcast_sample_01",
      output: {
        moments: [
          {
            start_word_idx: 18,
            end_word_idx: 112,
            hook: 94,
            payoff: 90,
            title: "3 Mistakes New Founders Make in India",
            reason: "Strong contrarian hook with clean financial payoff.",
          },
        ],
      },
    });
  }),

  http.get("/api/v1/admin/scoring", () => {
    return HttpResponse.json(scoringStore);
  }),

  http.put("/api/v1/admin/scoring", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    scoringStore = {
      ...scoringStore,
      ...body,
    };
    appendAudit(
      "scoring.update",
      String(body.active_genre ?? scoringStore.active_genre ?? "podcast"),
      "updated fusion weights and quality threshold"
    );
    return HttpResponse.json(scoringStore);
  }),

  http.post("/api/v1/admin/scoring/dry-run", async ({ request }) => {
    const body = (await request.json()) as {
      job_id: string;
      weights?: Record<string, number>;
    };
    const job = jobsStore.find((j) => j.id === body.job_id) ?? jobsStore[0];
    const clips = job?.clips ?? [];
    const oldPicks = clips.map((c, idx) => ({
      clip_id: c.id,
      rank: idx + 1,
      title: c.title ?? c.id,
      score: c.score,
    }));
    const weightBoost =
      typeof body.weights?.hook === "number" ? (body.weights.hook - 0.28) * 0.2 : 0.03;
    const newPicks = [...clips]
      .map((c) => {
        const nextScore = Math.min(
          0.99,
          Math.max(0.2, Number((c.score + weightBoost + (c.rank === 2 ? 0.05 : -0.01)).toFixed(2)))
        );
        return {
          clip_id: c.id,
          title: c.title ?? c.id,
          old_score: c.score,
          score: nextScore,
          delta: Number((nextScore - c.score).toFixed(2)),
        };
      })
      .sort((a, b) => b.score - a.score)
      .map((item, idx) => ({ ...item, rank: idx + 1 }));

    return HttpResponse.json({
      job_id: body.job_id,
      old_picks: oldPicks,
      new_picks: newPicks,
    });
  }),

  http.get("/api/v1/admin/drive", () => {
    return HttpResponse.json(driveStatusStore);
  }),

  http.get("/api/v1/admin/blockers", ({ request }) => {
    const mode = getMockMode(request);
    if (mode === "empty") {
      const empty: AdminBlockersResponse = { items: [] };
      return HttpResponse.json(empty);
    }
    const payload: AdminBlockersResponse = { items: blockersStore };
    return HttpResponse.json(payload);
  }),

  http.post("/api/v1/admin/blockers/:id/resolve", async ({ params, request }) => {
    const id = String(params.id);
    const existing = blockersStore.find((b) => b.id === id);
    if (!existing) {
      return makeError("VALIDATION_ERROR", `Blocker '${id}' not found.`, 404, false);
    }
    const body = (await request.json().catch(() => ({}))) as { note?: string };
    const resolved: Blocker = {
      ...existing,
      status: "RESOLVED",
      resolved_at: new Date().toISOString(),
      payload: {
        ...(existing.payload ?? {}),
        resolution_note: body.note ?? "Validated via Admin Blockers Inbox",
      },
    };
    blockersStore = blockersStore.map((b) => (b.id === id ? resolved : b));
    appendAudit("blocker.resolve", id, body.note ?? "resolved blocker");
    return HttpResponse.json(resolved);
  }),

  http.get("/api/v1/admin/jobs", ({ request }) => {
    const mode = getMockMode(request);
    if (mode === "empty") {
      const empty: PaginatedJobs = { items: [], next_cursor: null };
      return HttpResponse.json(empty);
    }
    const payload: PaginatedJobs = { items: jobsStore, next_cursor: null };
    return HttpResponse.json(payload);
  }),

  http.get("/api/v1/admin/settings", () => {
    return HttpResponse.json(settingsStore);
  }),

  http.put("/api/v1/admin/settings", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const { secrets: _ignoredSecrets, ...safeFields } = body;
    settingsStore = {
      ...settingsStore,
      ...safeFields,
    };
    appendAudit(
      "settings.update",
      "runtime",
      `llm_provider=${String(settingsStore.llm_provider)}, device=${String(settingsStore.device)}`
    );
    return HttpResponse.json(settingsStore);
  }),

  http.get("/api/v1/admin/audit", ({ request }) => {
    const mode = getMockMode(request);
    if (mode === "empty") {
      const empty: AdminAuditResponse = { items: [], next_cursor: null };
      return HttpResponse.json(empty);
    }
    const payload: AdminAuditResponse = {
      items: auditStore,
      next_cursor: null,
    };
    return HttpResponse.json(payload);
  }),
];
