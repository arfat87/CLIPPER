import { create } from "zustand";
import { setApiSimulationMode } from "../api/client";

export type NavTab = "home" | "library" | "jobs" | "templates" | "settings";
export type UiStateSimulation = "normal" | "loading" | "empty" | "error" | "offline";

interface StudioState {
  activeTab: NavTab;
  selectedJobId: string | null;
  selectedClipId: string | null;
  selectedPresetId: string;
  selectedTemplateId: string;
  simulation: UiStateSimulation;
  setActiveTab: (tab: NavTab) => void;
  setSelectedJobId: (id: string | null) => void;
  setSelectedClipId: (id: string | null) => void;
  setSelectedPresetId: (id: string) => void;
  setSelectedTemplateId: (id: string) => void;
  setSimulation: (mode: UiStateSimulation) => void;
}

export const useStudioStore = create<StudioState>((set) => ({
  activeTab: "home",
  selectedJobId: "job_hinglish_01",
  selectedClipId: "clip_01",
  selectedPresetId: "default",
  selectedTemplateId: "bold_pop_yellow",
  simulation: "normal",
  setActiveTab: (activeTab) => set({ activeTab }),
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
