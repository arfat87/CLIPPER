import { QueryClient } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../../App";
import { server } from "../../mocks/server";
import { useStudioStore } from "../../store/studioStore";

function renderStudio(initialPath = "/") {
  window.history.replaceState(null, "", initialPath);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<App client={qc} />);
}

describe("T-10.3 Clip Grid, Score Breakdown, Feedback, Batch Render & Reselect", () => {
  beforeEach(() => {
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

  it("renders score-sorted cards, mm:ss range, score breakdown bars, and collapsible Why-this-clip reason", async () => {
    renderStudio("/");

    expect(
      await screen.findByText(/3 Mistakes New Founders Make in India/i)
    ).toBeInTheDocument();

    const clipCards = document.querySelectorAll("[data-clip-id]");
    expect(clipCards.length).toBe(2);
    expect(clipCards[0]?.getAttribute("data-clip-id")).toBe("clip_01");
    expect(clipCards[1]?.getAttribute("data-clip-id")).toBe("clip_02");

    // Time range formatted as mm:ss - mm:ss
    expect(screen.getByText("00:42 - 01:24")).toBeInTheDocument();
    expect(screen.getByText("#1 · 94/100")).toBeInTheDocument();

    // Score breakdown progress bars appear
    await waitFor(() => {
      expect(screen.getAllByRole("progressbar").length).toBeGreaterThanOrEqual(
        6
      );
    });

    // Toggle Why this clip explanation
    const hideButtons = screen.getAllByRole("button", { name: /Hide why/i });
    fireEvent.click(hideButtons[0]!);
    expect(
      screen.queryByText(/Strong 2\.1s contrarian hook/i)
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Show why/i }));
    expect(
      screen.getByText(/Strong 2\.1s contrarian hook/i)
    ).toBeInTheDocument();
  });

  it("supports optimistic thumbs feedback, optional actual_views input, and rolls back on error", async () => {
    renderStudio("/");

    expect(
      await screen.findByText(/Why Tier-2 Creators Outgrow Metro Channels/i)
    ).toBeInTheDocument();

    // Optimistic thumbs down on clip_02
    const downBtn = screen.getByRole("button", {
      name: /Thumbs down \(weak clip\) Why Tier-2 Creators Outgrow Metro Channels/i,
    });
    fireEvent.click(downBtn);
    await waitFor(() => {
      expect(downBtn).toHaveAttribute("aria-pressed", "true");
    });
    expect(await screen.findByText(/Clip feedback saved/i)).toBeInTheDocument();

    // Log actual_views via popover on clip_02
    const logViewsBtn = screen.getByRole("button", { name: /Log views/i });
    fireEvent.click(logViewsBtn);
    const viewsInput = screen.getByPlaceholderText(/e\.g\. 18400/i);
    fireEvent.change(viewsInput, { target: { value: "24500" } });
    fireEvent.click(screen.getByRole("button", { name: /Save feedback/i }));

    expect(await screen.findByText(/24,500 views/i)).toBeInTheDocument();

    // Rollback when API returns 500
    server.use(
      http.post("/api/v1/clips/clip_02/feedback", () => {
        return HttpResponse.json(
          {
            error: {
              code: "VALIDATION_ERROR",
              message: "Simulated feedback failure",
              retryable: true,
            },
          },
          { status: 500 }
        );
      })
    );

    const upBtn = screen.getByRole("button", {
      name: /Thumbs up \(good clip\) Why Tier-2 Creators Outgrow Metro Channels/i,
    });
    fireEvent.click(upBtn);

    expect(
      await screen.findByText(/Could not save feedback — rolled back/i)
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(upBtn).toHaveAttribute("aria-pressed", "false");
    });
  });

  it("supports batch selection render, Render all, Open Editor stub, and Reselect when fewer clips than requested", async () => {
    renderStudio("/");

    expect(
      await screen.findByText(/Fewer clips than requested/i)
    ).toBeInTheDocument();

    // Lower threshold and reselect -> adds a 3rd clip
    fireEvent.click(
      screen.getByRole("button", { name: /Lower threshold \/ Reselect/i })
    );
    fireEvent.change(screen.getByLabelText(/Updated Clip Brief/i), {
      target: { value: "First 100 customers" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Reselect Clips/i }));

    expect(
      await screen.findByText(/Reselected: First 100 customers/i)
    ).toBeInTheDocument();

    // Select a clip and batch render selected
    const checkbox = screen.getByLabelText(
      /Select Why Tier-2 Creators Outgrow Metro Channels/i
    );
    fireEvent.click(checkbox);
    const renderSelBtn = screen.getByRole("button", {
      name: /Render selected \(1\)/i,
    });
    fireEvent.click(renderSelBtn);
    expect(await screen.findByText(/Render queued \(1\)/i)).toBeInTheDocument();

    // Open Editor opens ClipEditorView and Back returns to Ranked Clips
    const editorBtns = screen.getAllByRole("button", { name: /Open Editor/i });
    fireEvent.click(editorBtns[0]!);
    expect(
      await screen.findByText(/Waveform & Trim Handles/i)
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /Back to Ranked Clips/i })
    );

    // Render all
    fireEvent.click(await screen.findByRole("button", { name: /Render all/i }));
    expect(
      await screen.findByText(/Batch render queued for all clips/i)
    ).toBeInTheDocument();
  });
});
