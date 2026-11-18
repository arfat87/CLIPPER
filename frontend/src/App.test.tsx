import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "./App";
import { useStudioStore } from "./store/studioStore";

describe("CLIPPER Studio Shell", () => {
  beforeEach(() => {
    useStudioStore.setState({
      activeTab: "home",
      selectedJobId: "job_hinglish_01",
      selectedClipId: "clip_01",
      selectedPresetId: "default",
      selectedTemplateId: "bold_pop_yellow",
    });
    useStudioStore.getState().setSimulation("normal");
  });

  it("renders mocked jobs, ranked clips, health status, and templates", async () => {
    render(<App />);

    expect(
      await screen.findByText(/FFmpeg \+ libass ready/i)
    ).toBeInTheDocument();

    expect(
      await screen.findByText(/3 Mistakes New Founders Make in India/i)
    ).toBeInTheDocument();

    expect(
      await screen.findByText(/Score breakdown/i)
    ).toBeInTheDocument();

    // Switch to Templates tab (use mobile/desktop nav button)
    const templateButtons = screen.getAllByRole("button", { name: /Templates/i });
    fireEvent.click(templateButtons[0]!);

    expect(
      await screen.findByText(/Devanagari Gold/i)
    ).toBeInTheDocument();
  });

  it("renders empty and error states cleanly via simulation selector", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <App />
      </QueryClientProvider>
    );

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

  it("displays Google Drive route counts in Library tab", async () => {
    render(<App />);
    const libraryButtons = screen.getAllByRole("button", { name: /Library/i });
    fireEvent.click(libraryButtons[0]!);

    expect(
      await screen.findByText(/Google Drive Sync/i)
    ).toBeInTheDocument();
    expect(await screen.findByText("CONNECTED")).toBeInTheDocument();
  });
});
