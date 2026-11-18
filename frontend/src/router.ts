import { useEffect } from "react";
import {
  type AdminSection,
  type NavTab,
  type RouteArea,
  useStudioStore,
} from "./store/studioStore";

export const TAB_PATHS: Record<NavTab, string> = {
  home: "/",
  library: "/library",
  jobs: "/jobs",
  templates: "/templates",
  settings: "/settings",
};

export const ADMIN_SECTIONS: readonly AdminSection[] = [
  "templates",
  "presets",
  "fonts",
  "brandkits",
  "prompts",
  "scoring",
  "settings",
  "drive",
  "blockers",
  "jobs",
  "health",
  "audit",
] as const;

export interface ParsedRoute {
  area: RouteArea;
  tab: NavTab;
  adminSection: AdminSection;
}

function isAdminSection(value: string): value is AdminSection {
  return (ADMIN_SECTIONS as readonly string[]).includes(value);
}

export function parseRoute(pathname: string): ParsedRoute {
  const clean = pathname.replace(/\/+$/, "") || "/";

  if (clean === "/admin" || clean.startsWith("/admin/")) {
    const segment = clean.slice("/admin".length).replace(/^\//, "");
    const section = segment && isAdminSection(segment) ? segment : "templates";
    return { area: "admin", tab: "home", adminSection: section };
  }

  if (clean === "/library") {
    return { area: "studio", tab: "library", adminSection: "templates" };
  }
  if (clean === "/jobs" || clean.startsWith("/jobs/")) {
    return { area: "studio", tab: "jobs", adminSection: "templates" };
  }
  if (clean === "/templates") {
    return { area: "studio", tab: "templates", adminSection: "templates" };
  }
  if (clean === "/settings") {
    return { area: "studio", tab: "settings", adminSection: "templates" };
  }

  return { area: "studio", tab: "home", adminSection: "templates" };
}

function applyRouteToStore(pathname: string): void {
  const parsed = parseRoute(pathname);
  useStudioStore.setState((prev) => ({
    ...prev,
    area: parsed.area,
    activeTab: parsed.area === "studio" ? parsed.tab : prev.activeTab,
    adminSection:
      parsed.area === "admin" ? parsed.adminSection : prev.adminSection,
  }));
}

export function navigateTo(path: string): void {
  if (typeof window !== "undefined" && window.location.pathname !== path) {
    window.history.pushState(null, "", path);
  }
  applyRouteToStore(path);
}

export function navigateToTab(tab: NavTab): void {
  navigateTo(TAB_PATHS[tab]);
}

export function navigateToAdmin(section: AdminSection = "templates"): void {
  navigateTo(section === "templates" ? "/admin" : `/admin/${section}`);
}

export function useSyncRoute(): void {
  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    applyRouteToStore(window.location.pathname);

    const onPopState = () => {
      applyRouteToStore(window.location.pathname);
    };
    window.addEventListener("popstate", onPopState);
    return () => {
      window.removeEventListener("popstate", onPopState);
    };
  }, []);
}
