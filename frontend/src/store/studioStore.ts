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

interface StudioState {
  area: RouteArea;
  activeTab: NavTab;
  adminSection: AdminSection;
  selectedJobId: string | null;
  selectedClipId: string | null;
  selectedPresetId: string;
  selectedTemplateId: string;
  simulation: UiStateSimulation;
  setArea: (area: RouteArea) => void;
  setActiveTab: (tab: NavTab) => void;
  setAdminSection: (section: AdminSection) => void;
  setSelectedJobId: (id: string | null) => void;
  setSelectedClipId: (id: string | null) => void;
  setSelectedPresetId: (id: string) => void;
  setSelectedTemplateId: (id: string) => void;
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
  simulation: "normal",
  setArea: (area) => set({ area }),
  setActiveTab: (activeTab) => set({ area: "studio", activeTab }),
  setAdminSection: (adminSection) => set({ area: "admin", adminSection }),
  setSelectedJobId: (selectedJobId) => set({ selectedJobId }),
  setSelectedClipId: (selectedClipId) => set({ selectedClipId }),
  setSelectedPresetId: (selectedPresetId) => set({ selectedPresetId }),
  setSelectedTemplateId: (selectedTemplateId) => set({ selectedTemplateId }),
  setSimulation: (simulation) => {
    if (simulation === "empty" || simulation === "error") {
      setApiSimulationMode(simulation);
    } else {
      setApiSimulationMode("normal");
    }
    set({ simulation });
  },
}));
