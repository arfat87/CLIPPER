import { QueryClient } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../../App";
import { useStudioStore } from "../../store/studioStore";

function renderStudio(initialPath = "/") {
  window.history.replaceState(null, "", initialPath);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<App client={qc} />);
}

describe("T-10.4 Clip Editor & T-10.5 Publish Pack Screen", () => {
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

  it("T-10.4: switches preset/template, updates outdated template, trims, edits word transcript, and triggers keyboard/button render", async () => {
    renderStudio("/");

    expect(
      await screen.findByText(/3 Mistakes New Founders Make in India/i)
    ).toBeInTheDocument();

    const openEditorBtns = screen.getAllByRole("button", {
      name: /Open Editor/i,
    });
    fireEvent.click(openEditorBtns[0]!);

    // Editor loaded with 9:16 stage, outdated template badge, and waveform
    expect(
      await screen.findByTestId("editor-9x16-stage")
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/Outdated template \(v1 → v2\)/i)
    ).toBeInTheDocument();

    // Update outdated template to latest
    fireEvent.click(
      screen.getByRole("button", { name: /Update to latest/i })
    );
    expect(
      await screen.findByText(/Updated to latest template version/i)
    ).toBeInTheDocument();

    // Instant preset/template switch + layout switch
    fireEvent.click(
      await screen.findByRole("button", { name: /Clean Minimal Box \(v3\)/i })
    );
    expect(await screen.findByText(/Clip plan updated/i)).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /Fit \+ Blurred Backdrop/i })
    );

    // Adjust trim in and apply
    const trimInSlider = screen.getByLabelText(/Trim In \(s\)/i);
    fireEvent.change(trimInSlider, { target: { value: "45" } });
    expect(screen.getByText(/Unsaved trim bounds/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Apply Trim/i }));
    await waitFor(() => {
      expect(screen.queryByText(/Unsaved trim bounds/i)).not.toBeInTheDocument();
    });

    // Ops toggle
    const removeFillersToggle = screen.getByLabelText(/Remove filler words/i);
    fireEvent.click(removeFillersToggle);

    // Word-level transcript editor: tap low_conf word "mein" -> edit -> Save Transcript
    const wordBtn = await screen.findByRole("button", { name: "mein" });
    fireEvent.click(wordBtn);
    const wordInput = screen.getByLabelText(/Edit transcript word/i);
    fireEvent.change(wordInput, { target: { value: "me" } });
    fireEvent.click(screen.getByRole("button", { name: /^Apply$/i }));

    expect(
      await screen.findByText(/Unsaved transcript edits \(1\)/i)
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Save Transcript/i }));
    expect(
      await screen.findByText(/Transcript updated/i)
    ).toBeInTheDocument();

    // Keyboard shortcut: Space toggles Play -> Pause, Ctrl+Enter triggers Render
    fireEvent.keyDown(window, { code: "Space", key: " " });
    expect(
      screen.getByRole("button", { name: /^Pause$/i })
    ).toBeInTheDocument();
    fireEvent.keyDown(window, { code: "Space", key: " " });
    expect(
      screen.getByRole("button", { name: /^Play$/i })
    ).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Enter", ctrlKey: true });
    expect(await screen.findByText(/Clip render queued/i)).toBeInTheDocument();

    // Render Final button
    fireEvent.click(
      screen.getByRole("button", { name: /Render Final \(1080p\)/i })
    );
    expect(await screen.findByText(/1080p_h264_hq/i)).toBeInTheDocument();
  });

  it("T-10.5: validates title/description/hashtag counters, shows 100-char fold, copies with toast, saves, and regenerates Publish Pack", async () => {
    renderStudio("/");

    expect(
      await screen.findByText(/3 Mistakes New Founders Make in India/i)
    ).toBeInTheDocument();

    const publishBtns = screen.getAllByRole("button", {
      name: /Publish Pack/i,
    });
    fireEvent.click(publishBtns[0]!);

    expect(
      await screen.findByText(/Suggested Title Options/i)
    ).toBeInTheDocument();

    // Select an alternate LLM title option
    fireEvent.click(
      screen.getByRole("button", {
        name: /90% Startups Fail Because of THIS Hiring Mistake!/i,
      })
    );
    const titleInput = screen.getByLabelText(/Custom Title/i);
    expect((titleInput as HTMLInputElement).value).toBe(
      "90% Startups Fail Because of THIS Hiring Mistake!"
    );

    // Title warning past 70 chars & error past 100 chars
    fireEvent.change(titleInput, {
      target: {
        value:
          "This title intentionally exceeds seventy characters to verify the Shorts truncation warning!",
      },
    });
    expect(
      screen.getByText(/Exceeds 70 chars — may truncate on YouTube Shorts mobile/i)
    ).toBeInTheDocument();

    fireEvent.change(titleInput, {
      target: { value: "A".repeat(105) },
    });
    expect(
      screen.getByText(/Title must be 1–100 characters/i)
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Save Publish Pack/i })
    ).toBeDisabled();

    // Restore valid title
    fireEvent.change(titleInput, {
      target: { value: "Founder Cash Burn Truth in 40 Seconds" },
    });

    // Description 100-char fold preview
    expect(screen.getByText(/100 \/ 100 above fold/i)).toBeInTheDocument();
    expect(screen.getByText(/Below the fold \(\.\.\.more\)/i)).toBeInTheDocument();

    // Add hashtag chip
    const hashInput = screen.getByLabelText(/#AddHashtag/i);
    fireEvent.change(hashInput, { target: { value: "UnitEconomics" } });
    fireEvent.click(screen.getByRole("button", { name: /^Add$/i }));
    expect(screen.getByText("#UnitEconomics")).toBeInTheDocument();
    expect(screen.getByText("5 / 15")).toBeInTheDocument();

    // Copy All Metadata -> Toast confirmation
    fireEvent.click(
      screen.getByRole("button", { name: /Copy All Metadata/i })
    );
    expect(
      await screen.findByText(/Copied to clipboard: All Metadata/i)
    ).toBeInTheDocument();

    // Checklist interaction
    const checklistItem = screen.getByLabelText(/Verified 9:16 safe areas/i);
    fireEvent.click(checklistItem);
    expect(screen.getByText("1/4")).toBeInTheDocument();

    // Save Publish Pack
    fireEvent.click(screen.getByRole("button", { name: /Save Publish Pack/i }));
    expect(await screen.findByText(/Publish Pack saved/i)).toBeInTheDocument();

    // Regenerate Publish Pack
    fireEvent.click(screen.getByRole("button", { name: /^Regenerate$/i }));
    expect(
      await screen.findByText(/Publish Pack regenerated/i)
    ).toBeInTheDocument();
    await waitFor(() => {
      expect((titleInput as HTMLInputElement).value).toContain("[Refreshed]");
    });
  });
});
