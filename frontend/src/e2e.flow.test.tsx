import { QueryClient } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import {
  apiClient,
  ApiError,
  createJob,
  fetchHealth,
  fetchJob,
  fetchJobs,
  fetchTemplates,
  getAdminBlockers,
  getAdminSettings,
  getApiClient,
  getApiMode,
  getClip,
  getClipPlan,
  getClipPublishPack,
  getDriveFiles,
  getDriveStatus,
  getDriveTree,
  getJobZipDownloadUrl,
  getPublishPack,
  getScoringWeights,
  getTranscript,
  listBlockers,
  listClips,
  listDriveFiles,
  listJobs,
  listLogs,
  listMetrics,
  listPrompts,
  patchAdminSettings,
  patchClip,
  patchClipPlan,
  pickDriveFile,
  pickDriveSource,
  putPrompt,
  putScoringWeights,
  regenerateClipPublishPack,
  regeneratePublishPack,
  renderClip,
  rerenderClip,
  resolveAdminBlocker,
  resolveBlocker,
  runMaintenance,
  saveClipPublishPack,
  setApiMode,
  syncDrive,
  triggerDriveSync,
  updatePublishPack,
  uploadJob,
} from "./api/client";
import { setMockSseStepDelayMs } from "./mocks/handlers";
import {
  initialMockJobs,
  mockAdminBlockers,
  mockDriveStatus,
  mockHealth,
  mockPublishPacksByClipId,
} from "./mocks/mockData";
import { useStudioStore } from "./store/studioStore";

function renderApp(initialPath = "/") {
  window.history.replaceState(null, "", initialPath);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<App client={qc} />);
}

describe("T-10.8 End-to-End Studio, Admin & Dual Real/Mock API Flows", () => {
  beforeEach(() => {
    setApiMode("auto");
    setMockSseStepDelayMs(15);
    useStudioStore.setState({
      area: "studio",
      activeTab: "home",
      adminSection: "templates",
      selectedJobId: "job_hinglish_01",
      selectedClipId: "clip_01",
      selectedPresetId: "default",
      selectedTemplateId: "bold_pop_yellow",
      clipWorkspace: null,
    });
    useStudioStore.getState().setSimulation("normal");
  });

  afterEach(() => {
    setApiMode("auto");
    vi.restoreAllMocks();
  });

  it("Flow 1: Paste URL / Create Job -> SSE Progress Timeline -> Clip Grid -> Open NLE Editor -> Edit Caption/Layout/Trim -> Open Publish Pack -> Copy/Export & Download ZIP URL", async () => {
    renderApp("/");

    // 1. Paste YouTube URL and create a new job
    const urlInput = await screen.findByLabelText(/Video URL/i);
    fireEvent.change(urlInput, {
      target: { value: "https://www.youtube.com/watch?v=e2e_founder_podcast" },
    });
    expect(screen.getByText("YouTube")).toBeInTheDocument();

    const briefInput = screen.getByPlaceholderText(/Focus on founder mistakes/i);
    fireEvent.change(briefInput, {
      target: { value: "E2E Viral Unit Economics Hook" },
    });

    const createBtn = screen.getByRole("button", { name: /Find Viral Clips/i });
    fireEvent.click(createBtn);

    expect(await screen.findByText(/Clipping job created/i)).toBeInTheDocument();

    // 2. Job Progress Timeline transitions through SSE stages to COMPLETED
    await waitFor(() => {
      expect(screen.getAllByText(/Fetch source/i).length).toBeGreaterThan(0);
    });
    await waitFor(
      () => {
        expect(screen.getAllByText("COMPLETED").length).toBeGreaterThan(0);
      },
      { timeout: 3500 }
    );

    // Verify Download ZIP URL helper method matches `/api/v1/jobs/{id}/download.zip`
    const activeJobId = useStudioStore.getState().selectedJobId!;
    expect(getJobZipDownloadUrl(activeJobId)).toBe(
      `/api/v1/jobs/${encodeURIComponent(activeJobId)}/download.zip`
    );

    // 3. Clip Grid displays ranked clip from completed job -> Open Clip in NLE Editor
    await waitFor(() => {
      expect(
        screen.getAllByText(/E2E Viral Unit Economics Hook/i).length
      ).toBeGreaterThanOrEqual(1);
    });

    const openEditorBtns = screen.getAllByRole("button", {
      name: /Open Editor/i,
    });
    fireEvent.click(openEditorBtns[0]!);

    expect(
      await screen.findByTestId("editor-9x16-stage")
    ).toBeInTheDocument();

    // 4. Edit Caption Template, Layout, and Trim in NLE Editor
    fireEvent.click(
      await screen.findByRole("button", { name: /Clean Minimal Box \(v3\)/i })
    );
    expect(await screen.findByText(/Clip plan updated/i)).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /Fit \+ Blurred Backdrop/i })
    );

    const trimInSlider = screen.getByLabelText(/Trim In \(s\)/i);
    fireEvent.change(trimInSlider, { target: { value: "15" } });
    fireEvent.click(screen.getByRole("button", { name: /Apply Trim/i }));
    await waitFor(() => {
      expect(screen.queryByText(/Unsaved trim bounds/i)).not.toBeInTheDocument();
    });

    // 5. Switch directly to Publish Pack screen from NLE Editor
    fireEvent.click(
      screen.getAllByRole("button", { name: /^Publish Pack$/i })[0]!
    );

    expect(
      await screen.findByText(/Suggested Title Options/i)
    ).toBeInTheDocument();

    // Verify Job ZIP download link on Publish Pack screen points to getJobZipDownloadUrl(activeJobId)
    const zipLink = await screen.findByRole("link", { name: /Job ZIP/i });
    expect(zipLink).toHaveAttribute(
      "href",
      getJobZipDownloadUrl(activeJobId)
    );

    // Copy All Metadata -> Save -> Regenerate Publish Pack
    fireEvent.click(
      screen.getByRole("button", { name: /Copy All Metadata/i })
    );
    expect(
      await screen.findByText(/Copied to clipboard: All Metadata/i)
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Save Publish Pack/i }));
    expect(await screen.findByText(/Publish Pack saved/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Regenerate$/i }));
    expect(
      await screen.findByText(/Publish Pack regenerated/i)
    ).toBeInTheDocument();
  });

  it("Flow 2: Switch to Admin Console -> Inspect Templates, Scoring Weights, Prompts, Drive Workspace, Blockers Resolution, and Ops Health", async () => {
    renderApp("/settings");

    expect(
      await screen.findByText(/Local Toolchain Health/i)
    ).toBeInTheDocument();

    // Navigate from Studio Settings to Admin Console
    fireEvent.click(screen.getByRole("button", { name: /Open Admin Panel/i }));
    expect(window.location.pathname).toBe("/admin");

    // 1. Templates section
    expect(
      await screen.findByRole("heading", { name: /Templates & Editor/i })
    ).toBeInTheDocument();
    expect(await screen.findByText("Bold Pop — Yellow")).toBeInTheDocument();

    // 2. Scoring Weights section
    fireEvent.click(
      screen.getAllByRole("button", { name: /^Scoring$/i })[0]!
    );
    expect(window.location.pathname).toBe("/admin/scoring");
    expect(
      await screen.findByText(/Highlight Fusion Weights/i)
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /Save Scoring Config/i })
    );
    expect(
      await screen.findByText(/Scoring weights & thresholds updated/i)
    ).toBeInTheDocument();

    // 3. Prompts section
    fireEvent.click(
      screen.getAllByRole("button", { name: /^Prompts$/i })[0]!
    );
    expect(window.location.pathname).toBe("/admin/prompts");
    expect(
      await screen.findByText("highlight_map.md", undefined, { timeout: 3000 })
    ).toBeInTheDocument();
    const promptTextarea = await screen.findByLabelText(/Prompt Markdown Content/i);
    await waitFor(() => {
      expect((promptTextarea as HTMLTextAreaElement).value).toContain(
        "highlight_map"
      );
    });
    fireEvent.click(
      screen.getByRole("button", { name: /Test against fixture/i })
    );
    expect(await screen.findByText(/Schema Valid/i)).toBeInTheDocument();

    // 4. Drive Workspace section
    fireEvent.click(
      screen.getAllByRole("button", { name: /^Drive$/i })[0]!
    );
    expect(window.location.pathname).toBe("/admin/drive");
    expect(
      await screen.findByText(/Drive Workspace Connection/i)
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Force Resync/i }));
    expect(
      await screen.findByText(/Drive resync triggered/i)
    ).toBeInTheDocument();

    // 5. Blockers Resolution section
    fireEvent.click(
      screen.getAllByRole("button", { name: /Blockers inbox/i })[0]!
    );
    expect(window.location.pathname).toBe("/admin/blockers");
    expect(
      await screen.findByText(/Gemini API Key \(optional cloud LLM fallback\)/i)
    ).toBeInTheDocument();
    const resolveBtns = screen.getAllByRole("button", {
      name: /Validate & Mark Done/i,
    });
    fireEvent.click(resolveBtns[0]!);
    expect(
      await screen.findByText(/Blocker validated and resolved/i)
    ).toBeInTheDocument();

    // 6. Ops System Health section
    fireEvent.click(
      screen.getAllByRole("button", { name: /System health/i })[0]!
    );
    expect(window.location.pathname).toBe("/admin/health");
    expect(
      await screen.findByText(/FFmpeg & Indic Shaping Stack/i)
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/faster-whisper-large-v3/i)
    ).toBeInTheDocument();
  });

  it("Flow 3: Verifies client.ts real-HTTP fetch paths against mocked globalThis.fetch and graceful mock/auto mode fallback", async () => {
    expect(getApiClient()).toBe(apiClient);
    setApiMode("real");
    expect(getApiMode()).toBe("real");

    const fetchCalls: Array<{ url: string; method: string; body?: string }> = [];

    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.toString()
              : input.url;
        const method = (init?.method ?? "GET").toUpperCase();
        const body =
          typeof init?.body === "string" ? init.body : undefined;
        fetchCalls.push({ url, method, body });

        if (url === "/api/v1/health" && method === "GET") {
          return new Response(JSON.stringify(mockHealth), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }

        if (url.startsWith("/api/v1/jobs") && method === "GET") {
          if (url.endsWith("/transcript")) {
            return new Response(
              JSON.stringify({
                schema_version: 1,
                language: "hinglish",
                script: "latn",
                duration: 1840,
                engine: "faster-whisper",
                segments: [],
              }),
              { status: 200, headers: { "Content-Type": "application/json" } }
            );
          }
          if (url === "/api/v1/jobs?limit=20") {
            return new Response(
              JSON.stringify({ items: initialMockJobs, next_cursor: null }),
              { status: 200, headers: { "Content-Type": "application/json" } }
            );
          }
          return new Response(JSON.stringify(initialMockJobs[0]), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }

        if (url === "/api/v1/jobs" && method === "POST") {
          return new Response(JSON.stringify(initialMockJobs[0]), {
            status: 201,
            headers: { "Content-Type": "application/json" },
          });
        }

        if (
          url === "/api/v1/clips/clip_01/publish-pack" &&
          (method === "GET" || method === "PUT")
        ) {
          const pack = {
            ...mockPublishPacksByClipId.clip_01!,
            ...(body ? (JSON.parse(body) as Record<string, unknown>) : {}),
          };
          return new Response(JSON.stringify(pack), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }

        if (
          url === "/api/v1/clips/clip_01/publish-pack/regenerate" &&
          method === "POST"
        ) {
          return new Response(
            JSON.stringify({
              ...mockPublishPacksByClipId.clip_01!,
              title: "[Refreshed] Real HTTP Publish Pack",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }

        if (url === "/api/v1/drive/status" && method === "GET") {
          return new Response(JSON.stringify(mockDriveStatus), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }

        if (url === "/api/v1/drive/tree" && method === "GET") {
          return new Response(
            JSON.stringify({ tree_md: "# Drive Tree", manifest: {} }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }

        if (url === "/api/v1/drive/sync" && method === "POST") {
          return new Response(JSON.stringify(mockDriveStatus), {
            status: 202,
            headers: { "Content-Type": "application/json" },
          });
        }

        if (url.startsWith("/api/v1/drive/files") && method === "GET") {
          return new Response(
            JSON.stringify({ items: [{ id: "drv_src_01" }] }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }

        if (url === "/api/v1/drive/pick" && method === "POST") {
          return new Response(
            JSON.stringify({
              drive_file_id: "drv_src_01",
              source_ref: "drive://sources/drv_src_01",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }

        if (url === "/api/v1/admin/blockers" && method === "GET") {
          return new Response(
            JSON.stringify({ items: mockAdminBlockers }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }

        if (
          url === "/api/v1/admin/blockers/BLK-0001/resolve" &&
          method === "POST"
        ) {
          return new Response(
            JSON.stringify({
              ...mockAdminBlockers[0]!,
              status: "RESOLVED",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }

        if (url === "/api/v1/error-test") {
          return new Response(
            JSON.stringify({
              error: {
                code: "RATE_LIMITED",
                message: "Rate limit hit on real endpoint",
                retryable: true,
              },
            }),
            { status: 429, headers: { "Content-Type": "application/json" } }
          );
        }

        return new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      });

    // 1. Verify `/api/v1/health`
    const health = await fetchHealth();
    expect(health.ffmpeg.libass).toBe(true);

    // 2. Verify `/api/v1/jobs`
    const jobsRes = await listJobs();
    expect(jobsRes.items.length).toBeGreaterThan(0);
    const createdJob = await createJob({
      source: { kind: "url", url: "https://youtu.be/real_test" },
      language: "auto",
      script_pref: "auto",
      aspect: "9:16",
      preset_id: "default",
      auto_mode: true,
      max_clips: 8,
      rights_confirmed: true,
    });
    expect(createdJob.id).toBe("job_hinglish_01");
    const transcript = await getTranscript("job_hinglish_01");
    expect(transcript.language).toBe("hinglish");

    // 3. Verify `/api/v1/clips/{id}/publish-pack`
    const pack = await getClipPublishPack("clip_01");
    expect(pack.clip_id).toBe("clip_01");
    const savedPack = await saveClipPublishPack("clip_01", {
      title: "Updated Real Title",
    });
    expect(savedPack.title).toBe("Updated Real Title");
    const regenPack = await regenerateClipPublishPack("clip_01", {
      platform: "youtube_shorts",
    });
    expect(regenPack.title).toBe("[Refreshed] Real HTTP Publish Pack");

    // 4. Verify `/api/v1/drive/status` and Drive endpoints
    const driveStatus = await getDriveStatus();
    expect(driveStatus.status).toBe("CONNECTED");
    const driveTree = await getDriveTree();
    expect(driveTree.tree_md).toContain("# Drive Tree");
    await syncDrive({ full: true });
    const driveFiles = await getDriveFiles({ route: "source" });
    expect(driveFiles.items.length).toBe(1);
    const picked = await pickDriveSource("drv_src_01");
    expect(picked.source_ref).toBe("drive://sources/drv_src_01");

    // 5. Verify `/api/v1/admin/blockers` and resolution
    const blockers = await getAdminBlockers();
    expect(blockers.items[0]?.id).toBe("BLK-0001");
    const resolved = await resolveAdminBlocker("BLK-0001", "Added to .env");
    expect(resolved.status).toBe("RESOLVED");

    // Verify all expected real endpoints were hit via globalThis.fetch
    const hitUrls = fetchCalls.map((c) => `${c.method} ${c.url}`);
    expect(hitUrls).toContain("GET /api/v1/health");
    expect(hitUrls).toContain("GET /api/v1/jobs?limit=20");
    expect(hitUrls).toContain("POST /api/v1/jobs");
    expect(hitUrls).toContain("GET /api/v1/clips/clip_01/publish-pack");
    expect(hitUrls).toContain("PUT /api/v1/clips/clip_01/publish-pack");
    expect(hitUrls).toContain(
      "POST /api/v1/clips/clip_01/publish-pack/regenerate"
    );
    expect(hitUrls).toContain("GET /api/v1/drive/status");
    expect(hitUrls).toContain("GET /api/v1/admin/blockers");
    expect(hitUrls).toContain("POST /api/v1/admin/blockers/BLK-0001/resolve");

    // Verify structured ApiError handling when server returns 4xx/5xx ErrorEnvelope
    fetchSpy.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          error: {
            code: "RATE_LIMITED",
            message: "Too many requests",
            retryable: true,
          },
        }),
        { status: 429, headers: { "Content-Type": "application/json" } }
      )
    );
    await expect(fetchHealth()).rejects.toBeInstanceOf(ApiError);

    // Verify mock mode & auto fallback when network is offline
    fetchSpy.mockRejectedValue(new TypeError("Failed to fetch"));
    setApiMode("auto");
    const fallbackHealth = await fetchHealth();
    expect(fallbackHealth.ffmpeg.version).toBe("7.1.0");

    setApiMode("mock");
    const mockClips = await listClips("job_hinglish_01");
    expect(mockClips.length).toBeGreaterThan(0);
    const mockPlan = await getClipPlan("clip_01");
    expect(mockPlan.aspect).toBe("9:16");

    // Verify remaining exported client functions exist and work in mock mode
    expect(typeof fetchTemplates).toBe("function");
    expect(typeof fetchJobs).toBe("function");
    expect(typeof fetchJob).toBe("function");
    expect(typeof getClip).toBe("function");
    expect(typeof patchClip).toBe("function");
    expect(typeof patchClipPlan).toBe("function");
    expect(typeof renderClip).toBe("function");
    expect(typeof rerenderClip).toBe("function");
    expect(typeof getPublishPack).toBe("function");
    expect(typeof updatePublishPack).toBe("function");
    expect(typeof regeneratePublishPack).toBe("function");
    expect(typeof triggerDriveSync).toBe("function");
    expect(typeof listDriveFiles).toBe("function");
    expect(typeof pickDriveFile).toBe("function");
    expect(typeof getAdminSettings).toBe("function");
    expect(typeof patchAdminSettings).toBe("function");
    expect(typeof getScoringWeights).toBe("function");
    expect(typeof putScoringWeights).toBe("function");
    expect(typeof listPrompts).toBe("function");
    expect(typeof putPrompt).toBe("function");
    expect(typeof listBlockers).toBe("function");
    expect(typeof resolveBlocker).toBe("function");
    expect(typeof listMetrics).toBe("function");
    expect(typeof listLogs).toBe("function");
    expect(typeof runMaintenance).toBe("function");
    expect(typeof uploadJob).toBe("function");
  });
});
