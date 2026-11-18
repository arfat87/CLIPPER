import {
  Activity,
  ArrowLeft,
  Bell,
  Boxes,
  FileCode2,
  FolderSync,
  History,
  ListChecks,
  Palette,
  Settings,
  ShieldCheck,
  Sliders,
  Sparkles,
  Type,
} from "lucide-react";
import { en } from "../i18n/en";
import { ADMIN_SECTIONS, navigateToAdmin, navigateToTab } from "../router";
import { type AdminSection, useStudioStore } from "../store/studioStore";
import {
  AdminBrandKitsView,
  AdminFontsView,
  AdminPresetsView,
} from "./admin/AdminAssetsViews";
import { AdminBlockersView } from "./admin/AdminBlockersView";
import { AdminDriveView } from "./admin/AdminDriveView";
import {
  AdminAuditView,
  AdminHealthView,
  AdminJobsView,
} from "./admin/AdminOpsViews";
import { AdminPromptsView } from "./admin/AdminPromptsView";
import { AdminScoringView } from "./admin/AdminScoringView";
import { AdminSettingsView } from "./admin/AdminSettingsView";
import { AdminTemplatesView } from "./admin/AdminTemplatesView";
import { LoadingState, OfflineBanner } from "./StateViews";
import { Badge, Button } from "./ui";

const ADMIN_ICONS: Record<AdminSection, typeof Palette> = {
  templates: Palette,
  presets: Boxes,
  fonts: Type,
  brandkits: Sparkles,
  prompts: FileCode2,
  scoring: Sliders,
  settings: Settings,
  drive: FolderSync,
  blockers: Bell,
  jobs: ListChecks,
  health: Activity,
  audit: History,
};

export function AdminShell() {
  const { adminSection, simulation } = useStudioStore();

  const sectionMeta = en.admin.sections[adminSection];
  const isOffline =
    (typeof navigator !== "undefined" && !navigator.onLine) ||
    simulation === "offline";
  const isForcedLoading = simulation === "loading";

  const renderSectionBody = () => {
    if (isForcedLoading) {
      return <LoadingState />;
    }

    switch (adminSection) {
      case "templates":
        return <AdminTemplatesView />;
      case "presets":
        return <AdminPresetsView />;
      case "fonts":
        return <AdminFontsView />;
      case "brandkits":
        return <AdminBrandKitsView />;
      case "prompts":
        return <AdminPromptsView />;
      case "scoring":
        return <AdminScoringView />;
      case "settings":
        return <AdminSettingsView />;
      case "drive":
        return <AdminDriveView />;
      case "blockers":
        return <AdminBlockersView />;
      case "jobs":
        return <AdminJobsView />;
      case "health":
        return <AdminHealthView />;
      case "audit":
        return <AdminAuditView />;
    }
  };

  return (
    <div className="min-h-screen bg-bg text-text flex flex-col md:flex-row">
      {/* Admin Desktop Sidebar */}
      <aside className="hidden md:flex md:w-64 md:shrink-0 md:flex-col border-r border-border bg-surface">
        <div className="flex items-center justify-between border-b border-border px-4 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-sm bg-accent text-accent-fg">
              <ShieldCheck className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <p className="text-sm font-bold leading-tight text-text">
                {en.admin.shellTitle}
              </p>
              <p className="text-xs text-text-dim">{en.admin.shellSubtitle}</p>
            </div>
          </div>
        </div>

        <nav
          aria-label={en.nav.adminNavLabel}
          className="flex-1 space-y-1 overflow-y-auto p-3"
        >
          {ADMIN_SECTIONS.map((sec) => {
            const Icon = ADMIN_ICONS[sec];
            const active = adminSection === sec;
            return (
              <button
                key={sec}
                type="button"
                aria-current={active ? "page" : undefined}
                onClick={() => navigateToAdmin(sec)}
                className={`flex min-h-[44px] w-full items-center gap-3 rounded-sm px-3 py-2 text-sm font-medium transition ${
                  active
                    ? "bg-accent text-accent-fg font-semibold"
                    : "text-text-dim hover:bg-surface-2 hover:text-text"
                }`}
              >
                <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
                <span>{en.admin.sections[sec].label}</span>
              </button>
            );
          })}
        </nav>

        <div className="border-t border-border p-3">
          <Button
            variant="secondary"
            fullWidth
            onClick={() => navigateToTab("home")}
            leftIcon={<ArrowLeft className="h-4 w-4" aria-hidden="true" />}
          >
            {en.nav.backToStudio}
          </Button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {isOffline ? <OfflineBanner /> : null}

        <header className="sticky top-0 z-20 border-b border-border bg-bg/95 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center justify-between gap-2 px-4 py-3">
            <div className="flex items-center gap-2">
              <Badge tone="accent">{en.admin.ownerOnlyBadge}</Badge>
              <h1 className="text-base font-bold text-text">
                {sectionMeta.title}
              </h1>
            </div>
            <div className="md:hidden">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => navigateToTab("home")}
                leftIcon={<ArrowLeft className="h-4 w-4" aria-hidden="true" />}
              >
                {en.nav.backToStudio}
              </Button>
            </div>
          </div>

          {/* Mobile 360px horizontal admin section selector */}
          <nav
            aria-label={en.nav.adminNavLabel}
            className="flex gap-1.5 overflow-x-auto border-t border-border px-4 py-2 md:hidden"
          >
            {ADMIN_SECTIONS.map((sec) => {
              const active = adminSection === sec;
              return (
                <button
                  key={sec}
                  type="button"
                  aria-current={active ? "page" : undefined}
                  onClick={() => navigateToAdmin(sec)}
                  className={`min-h-[44px] shrink-0 rounded-sm border px-3 py-2 text-xs font-semibold transition ${
                    active
                      ? "border-accent bg-accent text-accent-fg"
                      : "border-border bg-surface text-text-dim hover:text-text"
                  }`}
                >
                  {en.admin.sections[sec].label}
                </button>
              );
            })}
          </nav>
        </header>

        <main className="mx-auto w-full max-w-6xl flex-1 space-y-5 px-4 py-4 sm:py-6">
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-surface px-4 py-3 text-xs text-text-dim">
            <span>{sectionMeta.description}</span>
            <span className="text-text">{en.admin.authNotice}</span>
          </div>

          {renderSectionBody()}
        </main>
      </div>
    </div>
  );
}

export default AdminShell;
