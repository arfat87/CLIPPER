import { QueryClient } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../../App";
import { subscribeJobEvents } from "../../api/sse";
import { setMockJobFailure, setMockSseStepDelayMs } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { useStudioStore } from "../../store/studioStore";
import { detectVideoPlatform } from "./urlDetect";

function renderStudio(initialPath = "/") {
  window.history.replaceState(null, "", initialPath);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<App client={qc} />);
}

describe("T-10.2 Home, Job Creation & Live SSE Progress Timeline", () => {
  beforeEach(() => {
    setMockSseStepDelayMs(15);
    useStudioStore.setState({
      area: "studio",
      activeTab: "home",
      adminSection: "templates",
      selectedJobId: "job_hinglish_01",
      selectedClipId: "clip_01",
      selectedPresetId: "default",
      selectedTemplateId: "bold_pop_yellow",
    });
    useStudioStore.getState().setSimulation("normal");
  });

  it("detects YouTube, Instagram, Facebook, Direct, and invalid/unsupported URLs", () => {
    expect(detectVideoPlatform("not-a-url").error).toMatch(/valid URL/i);
    expect(detectVideoPlatform("https://vimeo.com/12345").error).toMatch(
      /Unsupported platform/i
    );

    const yt = detectVideoPlatform("https://youtu.be/abc123");
    expect(yt.valid).toBe(true);
    expect(yt.badgeLabel).toBe("YouTube");

    const ig = detectVideoPlatform("https://www.instagram.com/reel/xyz/");
    expect(ig.valid).toBe(true);
    expect(ig.badgeLabel).toBe("Instagram");
    expect(ig.hint).toMatch(/cookies/i);

    const fb = detectVideoPlatform("https://fb.watch/demo/");
    expect(fb.valid).toBe(true);
    expect(fb.badgeLabel).toBe("Facebook");
    expect(fb.hint).toMatch(/public or authorized/i);

    const direct = detectVideoPlatform("https://cdn.example.com/clip.mp4");
    expect(direct.valid).toBe(true);
    expect(direct.badgeLabel).toBe("Direct");
  });

  it("validates URL inline on HomeView and submits a job through live SSE stages to COMPLETED", async () => {
    renderStudio("/");

    const urlInput = await screen.findByLabelText(/Video URL/i);
    fireEvent.change(urlInput, { target: { value: "https://vimeo.com/999" } });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Unsupported platform/i
    );

    // Paste Instagram URL -> shows Instagram badge + cookies hint
    fireEvent.change(urlInput, {
      target: { value: "https://www.instagram.com/reel/founder_hook/" },
    });
    expect(screen.getByText("Instagram")).toBeInTheDocument();
    expect(screen.getByText(/Instagram Reels may require owner cookies/i)).toBeInTheDocument();

    // Click an example brief chip
    fireEvent.click(
      screen.getByRole("button", { name: /Founder mistakes & unit economics/i })
    );

    // Submit form -> transitions QUEUED -> ... -> COMPLETED via SSE
    const startBtn = screen.getByRole("button", { name: /Find Viral Clips/i });
    expect(startBtn.className).toContain("min-h-[44px]");
    fireEvent.click(startBtn);

    expect(await screen.findByText(/Clipping job created/i)).toBeInTheDocument();

    // Live SSE timeline appears and transitions through stages to COMPLETED
    await waitFor(() => {
      expect(screen.getAllByText(/Fetch source/i).length).toBeGreaterThan(0);
    });
    await waitFor(
      () => {
        expect(screen.getAllByText("COMPLETED").length).toBeGreaterThan(0);
      },
      { timeout: 3000 }
    );

    // Ranked clips from completed job load
    await waitFor(() => {
      expect(
        screen.getAllByText(/Founder mistakes & unit economics/i).length
      ).toBeGreaterThanOrEqual(2);
    });
  });

  it("supports Upload and Drive source tabs", async () => {
    renderStudio("/");
    await screen.findByLabelText(/Video URL/i);

    // Switch to Upload tab & upload a mock file
    fireEvent.click(screen.getByRole("tab", { name: /Upload/i }));
    const fileInput = document.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement;
    const mockFile = new File(["video-bytes"], "my_podcast.mp4", {
      type: "video/mp4",
    });
    fireEvent.change(fileInput, { target: { files: [mockFile] } });

    expect(await screen.findByText(/Upload ready/i)).toBeInTheDocument();

    // Switch to Drive tab & pick a mirrored source
    fireEvent.click(screen.getByRole("tab", { name: /Drive/i }));
    const driveBtn = await screen.findByRole("button", {
      name: /founder_podcast_ep42_hinglish\.mp4/i,
    });
    fireEvent.click(driveBtn);
    await waitFor(() => {
      expect(
        screen.getAllByText(/founder_podcast_ep42_hinglish\.mp4/i).length
      ).toBeGreaterThan(1);
    });
  });

  it("supports Cancel on an active job", async () => {
    setMockSseStepDelayMs(80);
    renderStudio("/");

    const urlInput = await screen.findByLabelText(/Video URL/i);
    fireEvent.change(urlInput, {
      target: { value: "https://www.youtube.com/watch?v=cancel_demo" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Find Viral Clips/i }));

    const cancelBtn = await screen.findByRole("button", { name: /Cancel Job/i });
    expect(cancelBtn.className).toContain("min-h-[44px]");
    fireEvent.click(cancelBtn);

    expect(
      await screen.findByText(/Job cancellation requested/i)
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByText("CANCELLED").length).toBeGreaterThan(0);
    });
  });

  it("renders plain-language error card and retries from selected stage on failed job", async () => {
    setMockSseStepDelayMs(10);
    setMockJobFailure(
      "job_hinglish_01",
      "LOGIN_REQUIRED",
      "Instagram needs you to log in.",
      "fetch"
    );
    renderStudio("/");

    expect(
      await screen.findByText(/requires you to log in\. Add cookies in Settings/i)
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Open Settings/i })
    ).toBeInTheDocument();

    const stageSelect = screen.getByLabelText(/Retry from stage/i);
    fireEvent.change(stageSelect, { target: { value: "transcribe" } });
    fireEvent.click(screen.getByRole("button", { name: /^Retry$/i }));

    expect(await screen.findByText(/Job re-queued/i)).toBeInTheDocument();
  });

  it("reconnects SSE stream with Last-Event-ID when interrupted before done", async () => {
    let callCount = 0;
    const seenLastEventIds: Array<string | null> = [];

    server.use(
      http.get("/api/v1/jobs/job_reconnect/events", ({ request }) => {
        callCount += 1;
        seenLastEventIds.push(request.headers.get("Last-Event-ID"));
        if (callCount === 1) {
          return new HttpResponse(
            `id: 1\nevent: progress\ndata: ${JSON.stringify({
              job_id: "job_reconnect",
              stage: "fetch",
              status: "RUNNING",
              pct: 50,
            })}\n\n`,
            { status: 200, headers: { "Content-Type": "text/event-stream" } }
          );
        }
        return new HttpResponse(
          `id: 2\nevent: done\ndata: ${JSON.stringify({
            job_id: "job_reconnect",
            status: "COMPLETED",
          })}\n\n`,
          { status: 200, headers: { "Content-Type": "text/event-stream" } }
        );
      })
    );

    const onDone = vi.fn();
    const unsub = subscribeJobEvents(
      "job_reconnect",
      { onDone },
      { retryDelayMs: 10, maxRetries: 3 }
    );

    await waitFor(() => {
      expect(onDone).toHaveBeenCalledWith(
        {
          job_id: "job_reconnect",
          status: "COMPLETED",
        },
        "2"
      );
    });
    expect(seenLastEventIds).toEqual([null, "1"]);
    unsub();
  });
});
