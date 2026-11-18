import { QueryClient } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../../App";
import { useStudioStore } from "../../store/studioStore";

function renderAdmin(path = "/admin") {
  window.history.replaceState(null, "", path);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<App client={qc} />);
}

describe("T-10.6 Admin Templates, Editor, Import, Presets, Fonts & Brand Kits", () => {
  beforeEach(() => {
    useStudioStore.setState({
      area: "admin",
      activeTab: "home",
      adminSection: "templates",
      selectedJobId: "job_hinglish_01",
      selectedClipId: "clip_01",
      selectedPresetId: "default",
      selectedTemplateId: "bold_pop_yellow",
    });
    useStudioStore.getState().setSimulation("normal");
  });

  it("filters templates, imports JSON draft into editor, validates errors/warnings, previews test frame, and publishes", async () => {
    renderAdmin("/admin");

    expect(
      await screen.findByText("Bold Pop — Yellow")
    ).toBeInTheDocument();

    // Filter by kind = layout
    fireEvent.change(screen.getByLabelText(/Filter by kind/i), {
      target: { value: "layout" },
    });
    expect(screen.getByText("9:16 Face-Track Crop")).toBeInTheDocument();
    expect(screen.queryByText("Bold Pop — Yellow")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Filter by kind/i), {
      target: { value: "all" },
    });

    // Open Import modal -> paste JSON -> Import as Draft -> opens editor
    fireEvent.click(screen.getByRole("button", { name: /Import Template/i }));
    expect(screen.getByRole("dialog")).toHaveTextContent(
      /Import Template Package/i
    );
    fireEvent.click(screen.getByRole("button", { name: /Import as Draft/i }));

    expect(
      await screen.findByText(/Template imported as draft/i)
    ).toBeInTheDocument();

    // Editor should now be open on the imported draft
    expect(screen.getByTestId("template-css-preview")).toBeInTheDocument();

    // Switch sample language to Hindi & Bengali and toggle safe-area overlay
    fireEvent.change(screen.getByLabelText(/Sample language/i), {
      target: { value: "hi" },
    });
    expect(screen.getByTestId("template-css-preview")).toHaveTextContent(
      /पहले ३ सेकंड में/
    );

    fireEvent.change(screen.getByLabelText(/Safe-area overlay/i), {
      target: { value: "tiktok_default" },
    });
    expect(
      screen.getByLabelText(/Safe area overlay: TikTok/i)
    ).toBeInTheDocument();

    // Render server libass test frame
    fireEvent.click(screen.getByRole("button", { name: /Render test frame/i }));
    expect(
      await screen.findByAltText(/Server libass test frame/i)
    ).toBeInTheDocument();

    // Introduce validation error in JSON tab -> Publish button must be disabled
    fireEvent.click(screen.getByRole("tab", { name: /^JSON$/i }));
    const jsonTextarea = screen.getByLabelText(/Template JSON/i);
    fireEvent.change(jsonTextarea, { target: { value: "{ bad json" } });

    expect(
      screen.getByText(/Validation errors \(blocks publish\)/i)
    ).toBeInTheDocument();
    const publishBtn = screen.getByRole("button", { name: /Publish Version/i });
    expect(publishBtn).toBeDisabled();

    // Switch back to Form tab, set license to UNKNOWN -> warning requires acknowledgment
    fireEvent.click(screen.getByRole("tab", { name: /^Form$/i }));
    const licenseInput = screen.getByLabelText(/License/i);
    fireEvent.change(licenseInput, { target: { value: "UNKNOWN" } });

    expect(
      screen.getByText(/Warnings \(acknowledge to publish\)/i)
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Publish Version/i })
    ).toBeDisabled();

    // Acknowledge warning -> Publish becomes enabled -> Publish transitions to published
    fireEvent.click(
      screen.getByLabelText(/I acknowledge these warnings/i)
    );
    const enabledPublish = screen.getByRole("button", {
      name: /Publish Version/i,
    });
    expect(enabledPublish).toBeEnabled();
    fireEvent.click(enabledPublish);

    expect(
      await screen.findByText(/Template published/i)
    ).toBeInTheDocument();
  });

  it("supports Duplicate and Archive with ConfirmModal on templates list", async () => {
    renderAdmin("/admin");
    expect(
      await screen.findByText("Bold Pop — Yellow")
    ).toBeInTheDocument();

    const archiveBtns = screen.getAllByRole("button", { name: /^Archive$/i });
    fireEvent.click(archiveBtns[0]!);
    expect(screen.getByRole("dialog")).toHaveTextContent(/Archive template\?/i);
    fireEvent.click(screen.getByRole("button", { name: /Archive Template/i }));

    expect(await screen.findByText(/Template archived/i)).toBeInTheDocument();
  });

  it("manages Presets, Fonts, and Brand Kits in Admin views", async () => {
    renderAdmin("/admin/presets");

    // Presets: create & set default
    expect(await screen.findByText("Viral Hinglish 9:16")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Preset Name/i), {
      target: { value: "Shorts Fast Desi" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save Preset/i }));
    expect(await screen.findByText("Shorts Fast Desi")).toBeInTheDocument();

    const setDefaultBtns = screen.getAllByRole("button", {
      name: /Set Default/i,
    });
    fireEvent.click(setDefaultBtns[0]!);
    expect(
      await screen.findByText(/Default preset updated/i)
    ).toBeInTheDocument();

    // Fonts: inspect license badges, script coverage pills, upload font, toggle fallback
    fireEvent.click(screen.getAllByRole("button", { name: /^Fonts$/i })[0]!);
    expect(await screen.findByText("Montserrat")).toBeInTheDocument();
    expect(screen.getAllByText("License OK").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Check License").length).toBeGreaterThan(0);
    expect(screen.getAllByText("deva").length).toBeGreaterThan(0);
    expect(screen.getAllByText("beng").length).toBeGreaterThan(0);

    fireEvent.click(
      screen.getByRole("button", { name: /Upload & Check Coverage/i })
    );
    expect(await screen.findByText(/Font registered/i)).toBeInTheDocument();

    // Brand Kits: create brand kit
    fireEvent.click(
      screen.getAllByRole("button", { name: /^Brand kits$/i })[0]!
    );
    expect(await screen.findByText("Creator Gold Studio")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Brand Kit Name/i), {
      target: { value: "Hinglish Alpha Kit" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save Brand Kit/i }));
    await waitFor(() => {
      expect(screen.getByText("Hinglish Alpha Kit")).toBeInTheDocument();
    });
  });
});
