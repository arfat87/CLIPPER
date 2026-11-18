import { QueryClient } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "./App";
import { useStudioStore } from "./store/studioStore";

function renderApp() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<App client={qc} />);
}

describe("CLIPPER Studio & Admin Shell", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
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

  it("navigates across Home, Library, Jobs, Templates, Settings, and /admin routes with >=44px nav targets", async () => {
    renderApp();

    // Home route renders mocked health, jobs, clips, and score breakdown
    expect(
      await screen.findByText(/FFmpeg \+ libass ready/i)
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/3 Mistakes New Founders Make in India/i)
    ).toBeInTheDocument();
    expect(await screen.findByText(/Score breakdown/i)).toBeInTheDocument();

    // Check 44px touch targets on primary navigation buttons
    const homeBtns = screen.getAllByRole("button", { name: /^Home$/i });
    expect(homeBtns[0]!.className).toContain("min-h-[44px]");
    expect(homeBtns[1]!.className).toContain("min-h-[52px]");

    // Navigate to Library
    fireEvent.click(screen.getAllByRole("button", { name: /^Library$/i })[0]!);
    expect(window.location.pathname).toBe("/library");
    expect(await screen.findByText(/Google Drive Sync/i)).toBeInTheDocument();
    expect(await screen.findByText("CONNECTED")).toBeInTheDocument();

    // Navigate to Jobs
    fireEvent.click(screen.getAllByRole("button", { name: /^Jobs$/i })[0]!);
    expect(window.location.pathname).toBe("/jobs");
    expect(await screen.findByText(/Pipeline Jobs/i)).toBeInTheDocument();

    // Navigate to Templates
    fireEvent.click(screen.getAllByRole("button", { name: /^Templates$/i })[0]!);
    expect(window.location.pathname).toBe("/templates");
    expect(await screen.findByText(/Devanagari Gold/i)).toBeInTheDocument();

    // Navigate to Settings
    fireEvent.click(screen.getAllByRole("button", { name: /^Settings$/i })[0]!);
    expect(window.location.pathname).toBe("/settings");
    expect(await screen.findByText(/Local Toolchain Health/i)).toBeInTheDocument();

    // Navigate to /admin shell from Settings
    fireEvent.click(screen.getByRole("button", { name: /Open Admin Panel/i }));
    expect(window.location.pathname).toBe("/admin");
    expect(await screen.findByText(/Owner-only/i)).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: /Templates & Editor/i })
    ).toBeInTheDocument();

    // Navigate across admin sections
    fireEvent.click(
      screen.getAllByRole("button", { name: /Blockers inbox/i })[0]!
    );
    expect(window.location.pathname).toBe("/admin/blockers");
    expect(
      await screen.findByText(/Gemini API Key \(optional cloud LLM fallback\)/i)
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getAllByRole("button", { name: /Models & providers/i })[0]!
    );
    expect(window.location.pathname).toBe("/admin/settings");
    expect(await screen.findByText(/GEMINI_API_KEY/i)).toBeInTheDocument();
    expect(screen.getAllByText("NOT SET").length).toBeGreaterThan(0);

    // Return back to Studio
    fireEvent.click(
      screen.getAllByRole("button", { name: /Back to Studio/i })[0]!
    );
    expect(window.location.pathname).toBe("/");
    expect(await screen.findByText(/Create Viral Clips/i)).toBeInTheDocument();
  });

  it("wires useCreateJobMutation and usePatchClipMutation with MSW handlers", async () => {
    renderApp();

    expect(
      await screen.findByText(/3 Mistakes New Founders Make in India/i)
    ).toBeInTheDocument();

    // Patch clip template via quick template selector
    const devanagariBtn = await screen.findByRole("button", {
      name: /Devanagari Gold/i,
    });
    expect(devanagariBtn.className).toContain("min-h-[44px]");
    fireEvent.click(devanagariBtn);

    expect(
      await screen.findByText(/Clip template updated/i)
    ).toBeInTheDocument();

    // Create a new job via form mutation
    const briefInput = screen.getByPlaceholderText(
      /Focus on founder mistakes/i
    );
    fireEvent.change(briefInput, {
      target: { value: "Unit Economics Breakdown" },
    });
    const submitBtn = screen.getByRole("button", { name: /Find Viral Clips/i });
    expect(submitBtn.className).toContain("min-h-[44px]");
    fireEvent.click(submitBtn);

    expect(
      await screen.findByText(/Unit Economics Breakdown/i)
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/Clipping job created/i)
    ).toBeInTheDocument();
  });

  it("opens ConfirmModal when deleting a job and removes it on confirm", async () => {
    window.history.replaceState(null, "", "/jobs");
    renderApp();

    expect(
      await screen.findByText(/Founder Podcast #42/i)
    ).toBeInTheDocument();

    const deleteBtns = screen.getAllByRole("button", { name: /Delete Job/i });
    fireEvent.click(deleteBtns[0]!);

    expect(screen.getByRole("dialog")).toHaveTextContent(/Delete clipping job\?/i);
    fireEvent.click(
      screen.getByRole("button", { name: /Delete Permanently/i })
    );

    await waitFor(() => {
      expect(screen.queryByText(/Founder Podcast #42/i)).not.toBeInTheDocument();
    });
  });

  it("renders empty and error states cleanly via simulation selector", async () => {
    renderApp();

    expect(
      await screen.findByText(/3 Mistakes New Founders Make in India/i)
    ).toBeInTheDocument();

    const simSelect = screen.getByLabelText(/Preview state/i);

    fireEvent.change(simSelect, { target: { value: "empty" } });
    await waitFor(() => {
      expect(screen.getByText(/No clipping jobs yet/i)).toBeInTheDocument();
    });

    fireEvent.change(simSelect, { target: { value: "error" } });
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        /Could not load jobs right now/i
      );
    });
  });
});
