import { lazy, Suspense, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle2,
  Home,
  Layers,
  ListChecks,
  Palette,
  Settings,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { ApiError } from "../api/client";
import {
  useClipQuery,
  useDeleteJobMutation,
  useDriveStatusQuery,
  useHealthQuery,
  useJobQuery,
  useJobsQuery,
  usePatchClipMutation,
  usePresetsQuery,
  useTemplatesQuery,
} from "../api/hooks";
import type {
  ClipDetail,
  ClipSummary,
  JobDetail,
  LanguageCode,
  TemplateKind,
} from "../api/types";
import { en } from "../i18n/en";
import { navigateToAdmin, navigateToTab } from "../router";
import {
  type NavTab,
  type UiStateSimulation,
  useStudioStore,
} from "../store/studioStore";
import { EmptyState, ErrorState, LoadingState, OfflineBanner } from "./StateViews";
import { ClipGridView } from "./studio/ClipGridView";
import { HomeView } from "./studio/HomeView";
import { JobTimelineView } from "./studio/JobTimelineView";

const ClipEditorView = lazy(() => import("./studio/ClipEditorView"));
const PublishPackView = lazy(() => import("./studio/PublishPackView"));
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ConfirmModal,
  StatusBadge,
  useToast,
} from "./ui";

const NAV_ITEMS: Array<{ id: NavTab; label: string; icon: typeof Home }> = [
  { id: "home", label: en.nav.home, icon: Home },
  { id: "library", label: en.nav.library, icon: Layers },
  { id: "jobs", label: en.nav.jobs, icon: ListChecks },
  { id: "templates", label: en.nav.templates, icon: Palette },
  { id: "settings", label: en.nav.settings, icon: Settings },
];

const SIM_MODES: UiStateSimulation[] = [
  "normal",
  "loading",
  "empty",
  "error",
  "offline",
];

export function StudioShell() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const {
    activeTab,
    selectedJobId,
    selectedClipId,
    selectedPresetId,
    selectedTemplateId,
    clipWorkspace,
    simulation,
    setSelectedJobId,
    setSelectedClipId,
    setSelectedPresetId,
    setSelectedTemplateId,
    openClipWorkspace,
    closeClipWorkspace,
    setSimulation,
  } = useStudioStore();

  const [browserOffline, setBrowserOffline] = useState(
    typeof navigator !== "undefined" ? !navigator.onLine : false
  );
  const [templateKindFilter, setTemplateKindFilter] = useState<TemplateKind | "all">(
    "all"
  );
  const [jobPendingDelete, setJobPendingDelete] = useState<JobDetail | null>(null);
  const [inspectedJobId, setInspectedJobId] = useState<string | null>(null);

  useEffect(() => {
    const onOffline = () => setBrowserOffline(true);
    const onOnline = () => setBrowserOffline(false);
    window.addEventListener("offline", onOffline);
    window.addEventListener("online", onOnline);
    return () => {
      window.removeEventListener("offline", onOffline);
      window.removeEventListener("online", onOnline);
    };
  }, []);

  const healthQuery = useHealthQuery();
  const jobsQuery = useJobsQuery({ limit: 20 });
  const templatesQuery = useTemplatesQuery(
    templateKindFilter === "all" ? undefined : { kind: templateKindFilter }
  );
  const presetsQuery = usePresetsQuery();
  const driveQuery = useDriveStatusQuery();

  const jobs = jobsQuery.data?.items ?? [];
  const fallbackJob: JobDetail | undefined =
    jobs.find((j) => j.id === selectedJobId) ?? jobs[0];

  const jobDetailQuery = useJobQuery(fallbackJob?.id, {
    enabled: Boolean(fallbackJob?.id) && simulation !== "error",
  });
  const activeJob: JobDetail | undefined = jobDetailQuery.data ?? fallbackJob;

  const activeClipSummary: ClipSummary | undefined =
    activeJob?.clips?.find((c) => c.id === selectedClipId) ?? activeJob?.clips?.[0];
  const resolvedClipId = activeClipSummary?.id ?? null;

  const clipDetailQuery = useClipQuery(resolvedClipId);

  const patchClipMutation = usePatchClipMutation();
  const deleteJobMutation = useDeleteJobMutation();

  const handleSelectCaptionTemplate = (templateId: string) => {
    setSelectedTemplateId(templateId);
    if (!resolvedClipId) {
      return;
    }
    patchClipMutation.mutate(
      {
        id: resolvedClipId,
        patch: {
          clip_plan: {
            template_id: templateId,
          },
        },
      },
      {
        onSuccess: () => {
          toast({
            title: en.home.clipUpdatedToast,
            description: templateId,
            tone: "ok",
          });
        },
        onError: (err) => {
          toast({
            title: en.states.errorTitle,
            description: err.message,
            tone: "err",
          });
        },
      }
    );
  };

  const handleConfirmDeleteJob = () => {
    if (!jobPendingDelete) {
      return;
    }
    const deletingId = jobPendingDelete.id;
    deleteJobMutation.mutate(deletingId, {
      onSuccess: () => {
        if (selectedJobId === deletingId) {
          setSelectedJobId(null);
        }
        if (inspectedJobId === deletingId) {
          setInspectedJobId(null);
        }
        setJobPendingDelete(null);
        toast({
          title: en.jobs.deletedToast,
          tone: "ok",
        });
      },
      onError: (err) => {
        setJobPendingDelete(null);
        toast({
          title: en.states.errorTitle,
          description: err.message,
          tone: "err",
        });
      },
    });
  };

  const refetchAll = () => {
    setSimulation("normal");
    void queryClient.invalidateQueries();
  };

  const isOffline = browserOffline || simulation === "offline";
  const isForcedLoading = simulation === "loading";
  const templates = templatesQuery.data?.items ?? [];
  const presets = presetsQuery.data?.items ?? [];
  const activeClip: ClipDetail | undefined = clipDetailQuery.data;

  const inspectedJob =
    jobs.find((j) => j.id === inspectedJobId) ??
    (activeJob?.id === inspectedJobId ? activeJob : undefined);

  return (
    <div className="min-h-screen bg-bg text-text flex flex-col md:flex-row">
      {/* Desktop Sidebar */}
      <aside className="hidden md:flex md:w-60 md:shrink-0 md:flex-col border-r border-border bg-surface">
        <div className="flex items-center gap-3 border-b border-border px-4 py-4">
          <div className="flex h-9 w-9 items-center justify-center rounded-sm bg-accent font-black text-accent-fg">
            C
          </div>
          <div>
            <p className="text-base font-bold tracking-tight text-text">
              {en.appName}
            </p>
            <p className="text-xs text-text-dim">v1.0 Studio</p>
          </div>
        </div>

        <nav
          aria-label={en.nav.primaryNavLabel}
          className="flex-1 space-y-1 p-3"
        >
          {NAV_ITEMS.map(({ id, label, icon: Icon }) => {
            const active = activeTab === id;
            return (
              <button
                key={id}
                type="button"
                aria-current={active ? "page" : undefined}
                onClick={() => navigateToTab(id)}
                className={`flex min-h-[44px] w-full items-center gap-3 rounded-sm px-3 py-2 text-sm font-medium transition ${
                  active
                    ? "bg-accent text-accent-fg font-semibold"
                    : "text-text-dim hover:bg-surface-2 hover:text-text"
                }`}
              >
                <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
                <span>{label}</span>
              </button>
            );
          })}

          <div className="pt-3 mt-3 border-t border-border">
            <button
              type="button"
              onClick={() => navigateToAdmin("templates")}
              className="flex min-h-[44px] w-full items-center gap-3 rounded-sm px-3 py-2 text-sm font-medium text-text-dim transition hover:bg-surface-2 hover:text-text"
            >
              <ShieldCheck className="h-5 w-5 shrink-0" aria-hidden="true" />
              <span>{en.nav.admin}</span>
            </button>
          </div>
        </nav>

        <div className="border-t border-border p-3 text-xs text-text-dim space-y-1">
          <p className="font-semibold text-text">Toolchain</p>
          <p>
            FFmpeg {healthQuery.data?.ffmpeg?.version ?? "..."} ·{" "}
            {healthQuery.data?.ffmpeg?.libass ? "libass OK" : "checking"}
          </p>
          <p>Drive: {driveQuery.data?.status ?? "..."}</p>
        </div>
      </aside>

      {/* Main Content Column */}
      <div className="flex min-w-0 flex-1 flex-col pb-20 md:pb-0">
        {isOffline ? <OfflineBanner /> : null}

        {/* Top Header */}
        <header className="sticky top-0 z-20 border-b border-border bg-bg/95 backdrop-blur">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-3">
            <div className="flex items-center gap-2.5 md:hidden">
              <div className="flex h-8 w-8 items-center justify-center rounded-sm bg-accent font-black text-accent-fg">
                C
              </div>
              <div>
                <h1 className="text-base font-bold leading-none">{en.appName}</h1>
                <p className="text-[11px] text-text-dim">Indic Clip Studio</p>
              </div>
            </div>

            <div className="hidden md:block">
              <h1 className="text-base font-semibold text-text">{en.tagline}</h1>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Badge
                tone={healthQuery.data?.ffmpeg?.libass ? "ok" : "neutral"}
                className="py-1"
              >
                <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                <span>
                  {healthQuery.data?.ffmpeg?.libass
                    ? en.header.ffmpegReady
                    : en.header.ffmpegDegraded}
                </span>
              </Badge>

              <label className="flex items-center gap-1.5 text-xs text-text-dim">
                <span className="sr-only">{en.header.mockStateLabel}</span>
                <select
                  aria-label={en.header.mockStateLabel}
                  value={simulation}
                  onChange={(e) =>
                    setSimulation(e.target.value as UiStateSimulation)
                  }
                  className="min-h-[44px] rounded-sm border border-border bg-surface-2 px-2.5 py-1 text-xs text-text"
                >
                  {SIM_MODES.map((mode) => (
                    <option key={mode} value={mode}>
                      {en.header.states[mode]}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
        </header>

        {/* Main Workspace */}
        <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 px-4 py-4 sm:py-6">
          {isForcedLoading || jobsQuery.isLoading ? (
            <LoadingState />
          ) : jobsQuery.isError ? (
            <ErrorState
              message={
                jobsQuery.error instanceof ApiError
                  ? jobsQuery.error.message
                  : en.states.errorBody
              }
              onRetry={refetchAll}
            />
          ) : clipWorkspace ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-surface px-3 py-2">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={closeClipWorkspace}
                  leftIcon={<ArrowLeft className="h-4 w-4" aria-hidden="true" />}
                >
                  {en.clipEditor.backToClips}
                </Button>

                <div className="flex items-center gap-2" role="tablist">
                  <Button
                    role="tab"
                    aria-selected={clipWorkspace.tab === "editor"}
                    size="sm"
                    variant={
                      clipWorkspace.tab === "editor" ? "primary" : "secondary"
                    }
                    onClick={() =>
                      openClipWorkspace(clipWorkspace.clipId, "editor")
                    }
                  >
                    {en.clipEditor.tabEditor}
                  </Button>
                  <Button
                    role="tab"
                    aria-selected={clipWorkspace.tab === "publish"}
                    size="sm"
                    variant={
                      clipWorkspace.tab === "publish" ? "primary" : "secondary"
                    }
                    onClick={() =>
                      openClipWorkspace(clipWorkspace.clipId, "publish")
                    }
                  >
                    {en.clipEditor.tabPublish}
                  </Button>
                </div>
              </div>

              <Suspense fallback={<LoadingState />}>
                {clipWorkspace.tab === "editor" ? (
                  <ClipEditorView
                    clipId={clipWorkspace.clipId}
                    jobLanguage={activeJob?.language as LanguageCode | undefined}
                    onOpenPublishPack={() =>
                      openClipWorkspace(clipWorkspace.clipId, "publish")
                    }
                  />
                ) : (
                  <PublishPackView clipId={clipWorkspace.clipId} />
                )}
              </Suspense>
            </div>
          ) : (
            <>
              {activeTab === "home" ? (
                <HomeView
                  jobs={jobs}
                  activeJob={activeJob}
                  activeClip={activeClip}
                  activeClipSummary={activeClipSummary}
                  resolvedClipId={resolvedClipId}
                  clipLoading={clipDetailQuery.isLoading}
                  clipError={
                    clipDetailQuery.isError
                      ? clipDetailQuery.error instanceof ApiError
                        ? clipDetailQuery.error.message
                        : en.states.errorBody
                      : null
                  }
                  onRetryClip={() => void clipDetailQuery.refetch()}
                  presets={presets}
                  templates={templates}
                  selectedPresetId={selectedPresetId}
                  selectedTemplateId={selectedTemplateId}
                  onSelectPresetId={setSelectedPresetId}
                  onSelectJobId={setSelectedJobId}
                  onSelectClipId={setSelectedClipId}
                  onSelectCaptionTemplate={handleSelectCaptionTemplate}
                  onRequestDeleteJob={setJobPendingDelete}
                  onResetEmptySimulation={() => setSimulation("normal")}
                />
              ) : null}

              {activeTab === "jobs" ? (
                <section aria-labelledby="jobs-heading" className="space-y-4">
                  <div>
                    <h2 id="jobs-heading" className="text-lg font-bold">
                      {en.jobs.title}
                    </h2>
                    <p className="text-xs sm:text-sm text-text-dim">
                      {en.jobs.subtitle}
                    </p>
                  </div>

                  {inspectedJob ? (
                    <div className="space-y-4">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setInspectedJobId(null)}
                      >
                        ← Back to all jobs
                      </Button>
                      <JobTimelineView
                        job={inspectedJob}
                        onRequestDelete={setJobPendingDelete}
                      />
                      <ClipGridView
                        job={inspectedJob}
                        selectedClipId={resolvedClipId}
                        activeClipDetail={activeClip}
                        onSelectClipId={(clipId) => {
                          setSelectedJobId(inspectedJob.id);
                          setSelectedClipId(clipId);
                        }}
                      />
                    </div>
                  ) : jobs.length === 0 ? (
                    <EmptyState
                      title={en.jobs.emptyTitle}
                      body={en.jobs.emptyBody}
                      actionLabel={en.jobs.emptyCta}
                      onAction={() => {
                        setSimulation("normal");
                        navigateToTab("home");
                      }}
                    />
                  ) : (
                    <div className="space-y-3">
                      {jobs.map((job) => (
                        <Card key={job.id} className="space-y-3">
                          <CardHeader>
                            <div>
                              <CardTitle>{job.title ?? job.id}</CardTitle>
                              <CardDescription>
                                {job.platform ?? job.source_kind} · {job.language} ·{" "}
                                {job.clips?.length ?? 0} clips
                              </CardDescription>
                            </div>
                            <div className="flex flex-wrap items-center gap-2">
                              <StatusBadge status={job.status} />
                              <Button
                                variant="secondary"
                                size="sm"
                                onClick={() => {
                                  setSelectedJobId(job.id);
                                  setSelectedClipId(job.clips?.[0]?.id ?? null);
                                  setInspectedJobId(job.id);
                                }}
                              >
                                {en.jobs.inspectTimeline}
                              </Button>
                              <Button
                                variant="destructive"
                                size="sm"
                                aria-label={`${en.jobs.deleteJob} ${job.title ?? job.id}`}
                                onClick={() => setJobPendingDelete(job)}
                                leftIcon={
                                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                                }
                              >
                                {en.jobs.deleteJob}
                              </Button>
                            </div>
                          </CardHeader>

                          <div className="flex flex-wrap gap-1.5">
                            {(job.stages ?? []).map((st) => (
                              <span
                                key={st.stage}
                                className={`rounded-sm border px-2 py-1 text-[11px] font-medium ${
                                  st.status === "DONE"
                                    ? "border-ok/30 bg-ok/10 text-ok"
                                    : st.status === "RUNNING"
                                      ? "border-accent/40 bg-accent/10 text-accent"
                                      : st.status === "FAILED"
                                        ? "border-err/40 bg-err/10 text-err"
                                        : "border-border bg-surface-2 text-text-dim"
                                }`}
                              >
                                {st.stage}: {st.status}
                              </span>
                            ))}
                          </div>
                        </Card>
                      ))}
                    </div>
                  )}
                </section>
              ) : null}

              {activeTab === "library" ? (
                <section aria-labelledby="library-heading" className="space-y-4">
                  <div>
                    <h2 id="library-heading" className="text-lg font-bold">
                      {en.library.title}
                    </h2>
                    <p className="text-xs sm:text-sm text-text-dim">
                      {en.library.subtitle}
                    </p>
                  </div>

                  {driveQuery.isError ? (
                    <ErrorState
                      title={en.library.errorTitle}
                      message={
                        driveQuery.error instanceof ApiError
                          ? driveQuery.error.message
                          : en.states.errorBody
                      }
                      onRetry={() => void driveQuery.refetch()}
                    />
                  ) : (
                    <Card className="space-y-3">
                      <CardHeader>
                        <CardTitle>{en.library.driveCardTitle}</CardTitle>
                        <StatusBadge status={driveQuery.data?.status ?? "DISABLED"} />
                      </CardHeader>
                      <CardDescription>{en.library.routesLabel}</CardDescription>
                      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
                        {Object.entries(
                          driveQuery.data?.counts_by_route ?? {}
                        ).map(([route, count]) => (
                          <div
                            key={route}
                            className="rounded-sm border border-border bg-surface-2 p-3"
                          >
                            <p className="text-xs uppercase text-text-dim">
                              {route}
                            </p>
                            <p className="text-lg font-bold text-text">{count}</p>
                          </div>
                        ))}
                      </div>
                    </Card>
                  )}

                  <div className="space-y-2">
                    <h3 className="text-sm font-semibold text-text-dim">
                      {en.library.recentJobsTitle} ({jobs.length})
                    </h3>
                    {jobs.length === 0 ? (
                      <EmptyState
                        title={en.library.emptyTitle}
                        body={en.library.emptyBody}
                        actionLabel={en.library.emptyCta}
                        onAction={() => navigateToTab("home")}
                      />
                    ) : (
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                        {jobs.map((job) => (
                          <Card
                            key={job.id}
                            variant="interactive"
                            className="space-y-2"
                          >
                            <CardHeader>
                              <CardTitle>{job.title ?? job.id}</CardTitle>
                              <Badge tone="route" size="sm">
                                {job.source_kind}
                              </Badge>
                            </CardHeader>
                            <CardContent className="flex items-center justify-between gap-2 text-xs text-text-dim">
                              <span>
                                {job.language} · {job.clips?.length ?? 0} clips
                              </span>
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() => {
                                  setSelectedJobId(job.id);
                                  setSelectedClipId(job.clips?.[0]?.id ?? null);
                                  setInspectedJobId(job.id);
                                  navigateToTab("jobs");
                                }}
                              >
                                {en.jobs.inspectClips}
                              </Button>
                            </CardContent>
                          </Card>
                        ))}
                      </div>
                    )}
                  </div>
                </section>
              ) : null}

              {activeTab === "templates" ? (
                <section aria-labelledby="templates-heading" className="space-y-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <h2 id="templates-heading" className="text-lg font-bold">
                        {en.templates.title}
                      </h2>
                      <p className="text-xs sm:text-sm text-text-dim">
                        {en.templates.subtitle}
                      </p>
                    </div>

                    <div className="flex flex-wrap gap-1.5">
                      {(
                        [
                          { id: "all", label: en.templates.filterAll },
                          { id: "caption", label: en.templates.filterCaption },
                          { id: "layout", label: en.templates.filterLayout },
                          { id: "publish", label: en.templates.filterPublish },
                        ] as const
                      ).map(({ id, label }) => (
                        <Button
                          key={id}
                          size="sm"
                          variant={
                            templateKindFilter === id ? "primary" : "secondary"
                          }
                          onClick={() => setTemplateKindFilter(id)}
                        >
                          {label}
                        </Button>
                      ))}
                    </div>
                  </div>

                  {templatesQuery.isError ? (
                    <ErrorState
                      title={en.templates.errorTitle}
                      message={
                        templatesQuery.error instanceof ApiError
                          ? templatesQuery.error.message
                          : en.states.errorBody
                      }
                      onRetry={() => void templatesQuery.refetch()}
                    />
                  ) : templates.length === 0 ? (
                    <EmptyState
                      title={en.templates.emptyTitle}
                      body={en.templates.emptyBody}
                      actionLabel={en.templates.emptyCta}
                      onAction={() => navigateToAdmin("templates")}
                    />
                  ) : (
                    <div className="space-y-4">
                      <h3 className="text-sm font-semibold text-text-dim">
                        {en.templates.templatesHeading} ({templates.length})
                      </h3>
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                        {templates.map((tpl) => (
                          <Card key={tpl.id} className="space-y-2">
                            <CardHeader>
                              <CardTitle>{tpl.name}</CardTitle>
                              <StatusBadge status={tpl.status} />
                            </CardHeader>
                            <CardDescription>
                              Kind: {tpl.kind} · v{tpl.latest_version} · Source:{" "}
                              {tpl.source ?? "builtin"}
                            </CardDescription>
                          </Card>
                        ))}
                      </div>

                      <h3 className="text-sm font-semibold text-text-dim">
                        {en.templates.presetsHeading} ({presets.length})
                      </h3>
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                        {presets.map((preset) => {
                          const isDefault = selectedPresetId === preset.id;
                          return (
                            <Card key={preset.id} selected={isDefault}>
                              <CardHeader>
                                <CardTitle>{preset.name}</CardTitle>
                                {isDefault ? (
                                  <Badge tone="accent" size="sm">
                                    {en.templates.activePreset}
                                  </Badge>
                                ) : null}
                              </CardHeader>
                              <CardContent className="space-y-3">
                                <p className="text-xs text-text-dim">
                                  Caption: {preset.caption} · Profile:{" "}
                                  {preset.render_profile}
                                </p>
                                {!isDefault ? (
                                  <Button
                                    size="sm"
                                    variant="secondary"
                                    onClick={() => {
                                      setSelectedPresetId(preset.id);
                                      toast({
                                        title: en.templates.presetUpdatedToast,
                                        description: preset.name,
                                        tone: "ok",
                                      });
                                    }}
                                  >
                                    {en.templates.usePreset}
                                  </Button>
                                ) : null}
                              </CardContent>
                            </Card>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </section>
              ) : null}

              {activeTab === "settings" ? (
                <section aria-labelledby="settings-heading" className="space-y-4">
                  <div>
                    <h2 id="settings-heading" className="text-lg font-bold">
                      {en.settings.title}
                    </h2>
                    <p className="text-xs sm:text-sm text-text-dim">
                      {en.settings.subtitle}
                    </p>
                  </div>

                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <Card className="space-y-2">
                      <CardTitle>{en.settings.toolchainHeading}</CardTitle>
                      <dl className="space-y-1 text-xs">
                        <div className="flex justify-between">
                          <dt className="text-text-dim">FFmpeg</dt>
                          <dd>{healthQuery.data?.ffmpeg?.version ?? "—"}</dd>
                        </div>
                        <div className="flex justify-between">
                          <dt className="text-text-dim">libass / harfbuzz</dt>
                          <dd>
                            {healthQuery.data?.ffmpeg?.libass
                              ? "Enabled"
                              : "Missing"}
                          </dd>
                        </div>
                        <div className="flex justify-between">
                          <dt className="text-text-dim">GPU</dt>
                          <dd>{healthQuery.data?.gpu?.name ?? "CPU mode"}</dd>
                        </div>
                        <div className="flex justify-between">
                          <dt className="text-text-dim">LLM Provider</dt>
                          <dd>
                            {healthQuery.data?.models?.llm?.provider ?? "—"}
                          </dd>
                        </div>
                        <div className="flex justify-between">
                          <dt className="text-text-dim">Disk Free</dt>
                          <dd>{healthQuery.data?.disk?.free_gb ?? "—"} GB</dd>
                        </div>
                      </dl>
                    </Card>

                    <Card className="space-y-2">
                      <CardTitle>{en.settings.defaultsHeading}</CardTitle>
                      <p className="text-xs text-text-dim">
                        Default preset:{" "}
                        <span className="text-text">{selectedPresetId}</span>
                      </p>
                      <p className="text-xs text-text-dim">
                        Default template:{" "}
                        <span className="text-text">{selectedTemplateId}</span>
                      </p>
                      <p className="text-xs text-text-dim">
                        Templates valid: {healthQuery.data?.templates?.valid ?? 0}
                      </p>
                    </Card>
                  </div>

                  <Card
                    variant="raised"
                    className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div>
                      <CardTitle>{en.settings.adminLinkTitle}</CardTitle>
                      <CardDescription className="mt-1">
                        {en.settings.adminLinkBody}
                      </CardDescription>
                    </div>
                    <Button
                      variant="secondary"
                      onClick={() => navigateToAdmin("templates")}
                      leftIcon={
                        <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                      }
                      className="shrink-0"
                    >
                      {en.settings.adminLinkCta}
                    </Button>
                  </Card>
                </section>
              ) : null}
            </>
          )}
        </main>
      </div>

      {/* Mobile Bottom Tab Bar (360px one-handed navigation, >= 44px targets) */}
      <nav
        aria-label={en.nav.mobileNavLabel}
        className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-border bg-surface md:hidden"
      >
        {NAV_ITEMS.map(({ id, label, icon: Icon }) => {
          const active = activeTab === id;
          return (
            <button
              key={id}
              type="button"
              aria-current={active ? "page" : undefined}
              onClick={() => navigateToTab(id)}
              className={`flex min-h-[52px] flex-col items-center justify-center gap-0.5 text-[11px] font-medium ${
                active ? "text-accent" : "text-text-dim"
              }`}
            >
              <Icon className="h-5 w-5" aria-hidden="true" />
              <span>{label}</span>
            </button>
          );
        })}
      </nav>

      <ConfirmModal
        open={Boolean(jobPendingDelete)}
        title={en.jobs.deleteConfirmTitle}
        description={en.jobs.deleteConfirmBody}
        confirmLabel={en.jobs.deleteConfirmCta}
        variant="destructive"
        loading={deleteJobMutation.isPending}
        onConfirm={handleConfirmDeleteJob}
        onCancel={() => setJobPendingDelete(null)}
      />
    </div>
  );
}
