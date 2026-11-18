import { create } from "zustand";
import { setApiSimulationMode } from "../api/client";

export type NavTab = "home" | "library" | "jobs" | "templates" | "settings";
export type RouteArea = "studio" | "admin";
export type AdminSection =
  | "templates"
  | "presets"
  | "fonts"
  | "brandkits"
  | "prompts"
  | "scoring"
  | "settings"
  | "drive"
  | "blockers"
  | "jobs"
  | "health"
  | "audit";

export type UiStateSimulation = "normal" | "loading" | "empty" | "error" | "offline";

export interface ClipWorkspaceState {
  clipId: string;
  tab: "editor" | "publish";
}

interface StudioState {
  area: RouteArea;
  activeTab: NavTab;
  adminSection: AdminSection;
  selectedJobId: string | null;
  selectedClipId: string | null;
  selectedPresetId: string;
  selectedTemplateId: string;
  clipWorkspace: ClipWorkspaceState | null;
  simulation: UiStateSimulation;
  setArea: (area: RouteArea) => void;
  setActiveTab: (tab: NavTab) => void;
  setAdminSection: (section: AdminSection) => void;
  setSelectedJobId: (id: string | null) => void;
  setSelectedClipId: (id: string | null) => void;
  setSelectedPresetId: (id: string) => void;
  setSelectedTemplateId: (id: string) => void;
  openClipWorkspace: (clipId: string, tab: "editor" | "publish") => void;
  closeClipWorkspace: () => void;
  setSimulation: (mode: UiStateSimulation) => void;
}

export const useStudioStore = create<StudioState>((set) => ({
  area: "studio",
  activeTab: "home",
  adminSection: "templates",
  selectedJobId: "job_hinglish_01",
  selectedClipId: "clip_01",
  selectedPresetId: "default",
  selectedTemplateId: "bold_pop_yellow",
  clipWorkspace: null,
  simulation: "normal",
  setArea: (area) => set({ area }),
  setActiveTab: (activeTab) =>
    set({ area: "studio", activeTab, clipWorkspace: null }),
  setAdminSection: (adminSection) =>
    set({ area: "admin", adminSection, clipWorkspace: null }),
  setSelectedJobId: (selectedJobId) => set({ selectedJobId }),
  setSelectedClipId: (selectedClipId) => set({ selectedClipId }),
  setSelectedPresetId: (selectedPresetId) => set({ selectedPresetId }),
  setSelectedTemplateId: (selectedTemplateId) => set({ selectedTemplateId }),
  openClipWorkspace: (clipId, tab) =>
    set({ selectedClipId: clipId, clipWorkspace: { clipId, tab } }),
  closeClipWorkspace: () => set({ clipWorkspace: null }),
  setSimulation: (simulation) => {
    if (simulation === "empty" || simulation === "error") {
      setApiSimulationMode(simulation);
    } else {
      setApiSimulationMode("normal");
    }
    set({ simulation });
  },
}));
