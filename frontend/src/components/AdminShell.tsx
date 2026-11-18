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
import {
  useAdminAuditQuery,
  useAdminBlockersQuery,
  useAdminSettingsQuery,
  useAdminTemplatesQuery,
  useDriveStatusQuery,
  useHealthQuery,
  useJobsQuery,
  usePresetsQuery,
} from "../api/hooks";
import { ApiError } from "../api/client";
import { en } from "../i18n/en";
import { ADMIN_SECTIONS, navigateToAdmin, navigateToTab } from "../router";
import { type AdminSection, useStudioStore } from "../store/studioStore";
import { EmptyState, ErrorState, LoadingState, OfflineBanner } from "./StateViews";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  StatusBadge,
} from "./ui";

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

  const templatesQuery = useAdminTemplatesQuery();
  const presetsQuery = usePresetsQuery();
  const blockersQuery = useAdminBlockersQuery();
  const settingsQuery = useAdminSettingsQuery();
  const driveQuery = useDriveStatusQuery();
  const jobsQuery = useJobsQuery();
  const healthQuery = useHealthQuery();
  const auditQuery = useAdminAuditQuery();

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
      case "templates": {
        if (templatesQuery.isLoading) return <LoadingState />;
        if (templatesQuery.isError) {
          return (
            <ErrorState
              title={en.admin.errorTitle}
              message={
                templatesQuery.error instanceof ApiError
                  ? templatesQuery.error.message
                  : en.states.errorBody
              }
              onRetry={() => void templatesQuery.refetch()}
            />
          );
        }
        const items = templatesQuery.data?.items ?? [];
        if (items.length === 0) {
          return (
            <EmptyState
              title={en.admin.emptyTitle}
              body={en.admin.emptyBody}
            />
          );
        }
        return (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((tpl) => (
              <Card key={tpl.id}>
                <CardHeader>
                  <CardTitle>{tpl.name}</CardTitle>
                  <StatusBadge status={tpl.status} />
                </CardHeader>
                <CardContent className="space-y-1 text-xs text-text-dim">
                  <p>
                    Kind: <span className="text-text">{tpl.kind}</span> · v
                    {tpl.latest_version}
                  </p>
                  <p>
                    Source: <span className="text-text">{tpl.source ?? "builtin"}</span>
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>
        );
      }

      case "presets": {
        if (presetsQuery.isLoading) return <LoadingState />;
        const items = presetsQuery.data?.items ?? [];
        if (items.length === 0) {
          return (
            <EmptyState
              title={en.admin.emptyTitle}
              body={en.admin.emptyBody}
            />
          );
        }
        return (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {items.map((preset) => (
              <Card key={preset.id}>
                <CardHeader>
                  <CardTitle>{preset.name}</CardTitle>
                  {preset.is_default ? (
                    <Badge tone="accent">{en.templates.activePreset}</Badge>
                  ) : null}
                </CardHeader>
                <CardContent className="space-y-1 text-xs text-text-dim">
                  <p>Caption: {preset.caption}</p>
                  <p>Layout: {preset.layout}</p>
                  <p>Render profile: {preset.render_profile}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        );
      }

      case "blockers": {
        if (blockersQuery.isLoading) return <LoadingState />;
        const items = blockersQuery.data?.items ?? [];
        if (items.length === 0) {
          return (
            <EmptyState
              title={en.admin.emptyTitle}
              body={en.admin.emptyBody}
            />
          );
        }
        return (
          <div className="space-y-3">
            {items.map((blk) => (
              <Card key={blk.id}>
                <CardHeader>
                  <div>
                    <div className="flex items-center gap-2">
                      <Badge tone="route" size="sm">
                        {blk.id}
                      </Badge>
                      <Badge tone="neutral" size="sm">
                        {blk.type}
                      </Badge>
                    </div>
                    <CardTitle className="mt-1.5">{blk.title}</CardTitle>
                  </div>
                  <StatusBadge status={blk.status} />
                </CardHeader>
                <CardDescription className="mt-2">
                  Task: {blk.task_id}
                  {blk.step ? ` · Step: ${blk.step}` : ""}
                </CardDescription>
              </Card>
            ))}
          </div>
        );
      }

      case "settings": {
        if (settingsQuery.isLoading) return <LoadingState />;
        const data = settingsQuery.data ?? {};
        const secrets =
          typeof data.secrets === "object" && data.secrets !== null
            ? (data.secrets as Record<string, { set?: boolean }>)
            : {};
        return (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Runtime Models &amp; Device</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1.5 text-xs">
                <div className="flex justify-between">
                  <span className="text-text-dim">ASR Engine</span>
                  <span>{String(data.asr_engine ?? "faster-whisper")}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-dim">ASR Model</span>
                  <span>{String(data.asr_model ?? "large-v3")}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-dim">Device</span>
                  <span>{String(data.device ?? "cpu")}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-dim">LLM Provider</span>
                  <span>{String(data.llm_provider ?? "ollama")}</span>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Secrets Status (never values)</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-xs">
                {Object.entries(secrets).map(([key, meta]) => (
                  <div key={key} className="flex items-center justify-between">
                    <span className="font-mono text-text-dim">{key}</span>
                    <Badge tone={meta?.set ? "ok" : "warn"}>
                      {meta?.set ? "SET" : "NOT SET"}
                    </Badge>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>
        );
      }

      case "drive": {
        if (driveQuery.isLoading) return <LoadingState />;
        if (driveQuery.isError) {
          return (
            <ErrorState
              title={en.library.errorTitle}
              message={
                driveQuery.error instanceof ApiError
                  ? driveQuery.error.message
                  : en.states.errorBody
              }
              onRetry={() => void driveQuery.refetch()}
            />
          );
        }
        return (
          <Card>
            <CardHeader>
              <CardTitle>{en.library.driveCardTitle}</CardTitle>
              <StatusBadge status={driveQuery.data?.status ?? "DISABLED"} />
            </CardHeader>
            <CardContent className="space-y-3 text-xs">
              <p className="text-text-dim">
                {en.library.driveRootLabel}:{" "}
                <span className="font-mono text-text">
                  {driveQuery.data?.root_folder_id ?? "—"}
                </span>
              </p>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
                {Object.entries(driveQuery.data?.counts_by_route ?? {}).map(
                  ([route, count]) => (
                    <div
                      key={route}
                      className="rounded-sm border border-border bg-surface-2 p-3"
                    >
                      <p className="text-[11px] uppercase text-text-dim">{route}</p>
                      <p className="text-lg font-bold text-text">{count}</p>
                    </div>
                  )
                )}
              </div>
            </CardContent>
          </Card>
        );
      }

      case "jobs": {
        if (jobsQuery.isLoading) return <LoadingState />;
        const items = jobsQuery.data?.items ?? [];
        if (items.length === 0) {
          return (
            <EmptyState
              title={en.jobs.emptyTitle}
              body={en.jobs.emptyBody}
            />
          );
        }
        return (
          <div className="space-y-3">
            {items.map((job) => (
              <Card key={job.id}>
                <CardHeader>
                  <div>
                    <CardTitle>{job.title ?? job.id}</CardTitle>
                    <CardDescription>
                      {job.id} · {job.source_kind} · {job.language}
                    </CardDescription>
                  </div>
                  <StatusBadge status={job.status} />
                </CardHeader>
              </Card>
            ))}
          </div>
        );
      }

      case "health": {
        if (healthQuery.isLoading) return <LoadingState />;
        const h = healthQuery.data;
        return (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>FFmpeg &amp; Subtitle Stack</CardTitle>
                <Badge tone={h?.ffmpeg?.libass ? "ok" : "err"}>
                  {h?.ffmpeg?.libass ? "READY" : "CHECK"}
                </Badge>
              </CardHeader>
              <CardContent className="space-y-1 text-xs">
                <p>FFmpeg: {h?.ffmpeg?.version ?? "—"}</p>
                <p>libass: {h?.ffmpeg?.libass ? "Enabled" : "Missing"}</p>
                <p>HarfBuzz: {h?.ffmpeg?.harfbuzz ? "Enabled" : "Missing"}</p>
                <p>FriBidi: {h?.ffmpeg?.fribidi ? "Enabled" : "Missing"}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Hardware, Queue &amp; Storage</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1 text-xs">
                <p>GPU: {h?.gpu?.name ?? "CPU mode"}</p>
                <p>Disk free: {h?.disk?.free_gb ?? "—"} GB</p>
                <p>yt-dlp: {h?.ytdlp?.version ?? "—"}</p>
                <p>Queue depth: {h?.queue?.depth ?? 0}</p>
              </CardContent>
            </Card>
          </div>
        );
      }

      case "audit": {
        if (auditQuery.isLoading) return <LoadingState />;
        const items = auditQuery.data?.items ?? [];
        if (items.length === 0) {
          return (
            <EmptyState
              title={en.admin.emptyTitle}
              body={en.admin.emptyBody}
            />
          );
        }
        return (
          <div className="space-y-2">
            {items.map((entry, idx) => (
              <Card key={String(entry.id ?? idx)} className="p-3">
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                  <div className="flex items-center gap-2">
                    <Badge tone="route" size="sm">
                      {String(entry.action ?? "write")}
                    </Badge>
                    <span className="font-medium text-text">
                      {String(entry.target ?? "—")}
                    </span>
                  </div>
                  <span className="text-text-dim">
                    {String(entry.actor ?? "owner")} ·{" "}
                    {String(entry.timestamp ?? "")}
                  </span>
                </div>
              </Card>
            ))}
          </div>
        );
      }

      default:
        return (
          <Card variant="dashed">
            <CardHeader>
              <CardTitle>{sectionMeta.title}</CardTitle>
              <Badge tone="accent">Shell Ready</Badge>
            </CardHeader>
            <CardDescription className="mt-2">
              {sectionMeta.description}
            </CardDescription>
          </Card>
        );
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
