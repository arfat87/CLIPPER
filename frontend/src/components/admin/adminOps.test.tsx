import { QueryClient } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../../App";
import { useStudioStore } from "../../store/studioStore";

function renderAdmin(path: string) {
  window.history.replaceState(null, "", path);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<App client={qc} />);
}

describe("T-10.7 Remaining 8 Admin Sections", () => {
  beforeEach(() => {
    useStudioStore.setState({
      area: "admin",
      activeTab: "home",
      adminSection: "prompts",
      selectedJobId: "job_hinglish_01",
      selectedClipId: "clip_01",
      selectedPresetId: "default",
      selectedTemplateId: "bold_pop_yellow",
    });
    useStudioStore.getState().setSimulation("normal");
  });

  it("manages Prompts: edit with inline diff, fixture test, save new version, and rollback", async () => {
    renderAdmin("/admin/prompts");

    expect(
      await screen.findByText("highlight_map.md", undefined, { timeout: 3000 })
    ).toBeInTheDocument();
    const textarea = await screen.findByLabelText(/Prompt Markdown Content/i);

    await waitFor(() => {
      expect((textarea as HTMLTextAreaElement).value).toContain("highlight_map");
    });

    // Edit content -> inline diff displays + line
    fireEvent.change(textarea, {
      target: { value: `${(textarea as HTMLTextAreaElement).value}\n# Added rule` },
    });
    expect(screen.getByText(/\+ # Added rule/)).toBeInTheDocument();

    // Test against fixture
    fireEvent.click(screen.getByRole("button", { name: /Test against fixture/i }));
    expect(await screen.findByText(/Schema Valid/i)).toBeInTheDocument();
    expect(
      screen.getByText(/3 Mistakes New Founders Make in India/i)
    ).toBeInTheDocument();

    // Save new version (v3)
    fireEvent.click(screen.getByRole("button", { name: /Save New Version/i }));
    expect(
      await screen.findByText(/New prompt version saved/i)
    ).toBeInTheDocument();
    expect((await screen.findAllByText("v3")).length).toBeGreaterThan(0);

    // Rollback / Activate v1
    const rollbackBtns = screen.getAllByRole("button", {
      name: /Rollback \/ Activate/i,
    });
    fireEvent.click(rollbackBtns[rollbackBtns.length - 1]!);
    expect(
      await screen.findByText(/Prompt version activated/i)
    ).toBeInTheDocument();
  });

  it("manages Scoring: genre switch, weight sliders, save, feedback stats, and dry-run comparison", async () => {
    renderAdmin("/admin/scoring");

    expect(await screen.findByText(/Highlight Fusion Weights/i)).toBeInTheDocument();
    expect(screen.getByText(/14 Thumbs Up/i)).toBeInTheDocument();
    expect(screen.getByText(/82%/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Target Genre/i), {
      target: { value: "tutorial" },
    });

    const hookSlider = screen.getByLabelText(/hook weight/i);
    fireEvent.change(hookSlider, { target: { value: "0.35" } });

    fireEvent.click(screen.getByRole("button", { name: /Save Scoring Config/i }));
    expect(
      await screen.findByText(/Scoring weights & thresholds updated/i)
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Dry-run on job/i }));
    expect(await screen.findByText(/Current \(Active\) Picks/i)).toBeInTheDocument();
    expect(await screen.findByText(/Proposed Weights Picks/i)).toBeInTheDocument();
  });

  it("manages Settings: runtime updates, secret presence badges (never values), and connectivity test", async () => {
    renderAdmin("/admin/settings");

    expect(
      await screen.findByText(/ASR & LLM Runtime Configuration/i)
    ).toBeInTheDocument();

    // Secret presence only
    expect(screen.getByText("GEMINI_API_KEY")).toBeInTheDocument();
    expect(screen.getByText("DRIVE_OAUTH_TOKEN")).toBeInTheDocument();
    expect(screen.getAllByText("NOT SET").length).toBeGreaterThan(0);
    expect(screen.getAllByText("SET").length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText(/LLM Provider/i), {
      target: { value: "ollama" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save Settings/i }));
    expect(await screen.findByText(/Runtime settings saved/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Test Connectivity/i }));
    expect(
      await screen.findByText(/Connectivity check completed/i)
    ).toBeInTheDocument();
  });

  it("manages Drive & Blockers: force resync, folder tree overrides, and blocker validation/resolution", async () => {
    renderAdmin("/admin/drive");

    expect(await screen.findByText(/Drive Workspace Connection/i)).toBeInTheDocument();
    expect((await screen.findAllByText(/01_Sources\//i)).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /Force Resync/i }));
    expect(await screen.findByText(/Drive resync triggered/i)).toBeInTheDocument();

    // Route override selector
    fireEvent.change(screen.getByLabelText(/Route override 01_Sources/i), {
      target: { value: "reference" },
    });
    expect(
      await screen.findByText(/Folder routing preference updated/i)
    ).toBeInTheDocument();

    // Navigate to Blockers inbox
    fireEvent.click(screen.getAllByRole("button", { name: /Blockers inbox/i })[0]!);
    expect(
      await screen.findByText(/Gemini API Key \(optional cloud LLM fallback\)/i)
    ).toBeInTheDocument();

    // Filter Open -> add note -> Validate & Mark Done
    fireEvent.click(screen.getByRole("button", { name: /Open \(2\)/i }));
    const noteInputs = screen.getAllByPlaceholderText(/Added GEMINI_API_KEY/i);
    fireEvent.change(noteInputs[0]!, {
      target: { value: "Switched to local Ollama" },
    });
    const resolveBtns = screen.getAllByRole("button", {
      name: /Validate & Mark Done/i,
    });
    fireEvent.click(resolveBtns[0]!);

    expect(
      await screen.findByText(/Blocker validated and resolved/i)
    ).toBeInTheDocument();
  });

  it("manages Admin Jobs, System Health, and Audit Log", async () => {
    renderAdmin("/admin/jobs");

    expect(await screen.findByText(/Total Artifacts Disk Usage/i)).toBeInTheDocument();
    expect(await screen.findByText(/Founder Podcast #42/i)).toBeInTheDocument();

    // Expand artifacts
    const artifactBtns = screen.getAllByRole("button", {
      name: /Inspect Artifacts/i,
    });
    fireEvent.click(artifactBtns[0]!);
    expect(await screen.findByText(/audio_16k\.wav/i)).toBeInTheDocument();

    // Retry job
    const retryBtns = screen.getAllByRole("button", { name: /^Retry$/i });
    fireEvent.click(retryBtns[0]!);
    expect(await screen.findByText(/Job retry queued/i)).toBeInTheDocument();

    // Delete confirmation modal
    const deleteBtns = screen.getAllByRole("button", { name: /Delete Founder Podcast/i });
    fireEvent.click(deleteBtns[0]!);
    expect(screen.getByRole("dialog")).toHaveTextContent(/Delete clipping job\?/i);
    fireEvent.click(screen.getAllByRole("button", { name: /^Cancel$/i })[0]!);

    // System Health
    fireEvent.click(screen.getAllByRole("button", { name: /System health/i })[0]!);
    expect(await screen.findByText(/FFmpeg & Indic Shaping Stack/i)).toBeInTheDocument();
    expect(await screen.findByText(/faster-whisper-large-v3/i)).toBeInTheDocument();
    expect(screen.getByText(/NVIDIA RTX 4060/i)).toBeInTheDocument();

    // Audit Log
    fireEvent.click(screen.getAllByRole("button", { name: /Audit log/i })[0]!);
    expect(await screen.findByText(/bold_pop_yellow@v2/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Filter by action/i), {
      target: { value: "scoring" },
    });
    await waitFor(() => {
      expect(screen.queryByText(/bold_pop_yellow@v2/i)).not.toBeInTheDocument();
    });
    expect(screen.getByText(/hook: 0\.25 -> 0\.30/i)).toBeInTheDocument();
  });
});
