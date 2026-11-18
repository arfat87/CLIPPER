import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Clapperboard,
  FolderSync,
  Home,
  Layers,
  Link2,
  ListChecks,
  Palette,
  Settings,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import { ApiError } from "../api/client";
import {
  useClipQuery,
  useCreateJobMutation,
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
  AspectRatio,
  ClipDetail,
  ClipSummary,
  CreateJobRequest,
  JobDetail,
  LanguageCode,
  ScriptPref,
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
    simulation,
    setSelectedJobId,
    setSelectedClipId,
    setSelectedPresetId,
    setSelectedTemplateId,
    setSimulation,
  } = useStudioStore();

  const [browserOffline, setBrowserOffline] = useState(
    typeof navigator !== "undefined" ? !navigator.onLine : false
  );
  const [sourceKind, setSourceKind] = useState<"url" | "upload" | "drive">("url");
  const [urlInput, setUrlInput] = useState(
    "https://www.youtube.com/watch?v=sample_hinglish_podcast"
  );
  const [language, setLanguage] = useState<LanguageCode>("hinglish");
  const [scriptPref, setScriptPref] = useState<ScriptPref>("auto");
  const [aspect, setAspect] = useState<AspectRatio>("9:16");
  const [clipBrief, setClipBrief] = useState("");
  const [rightsConfirmed, setRightsConfirmed] = useState(true);
  const [formNotice, setFormNotice] = useState<string | null>(null);
  const [templateKindFilter, setTemplateKindFilter] = useState<TemplateKind | "all">("all");
  const [jobPendingDelete, setJobPendingDelete] = useState<JobDetail | null>(null);

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

  const createJobMutation = useCreateJobMutation();
  const patchClipMutation = usePatchClipMutation();
  const deleteJobMutation = useDeleteJobMutation();

  const handleCreateJob = (e: React.FormEvent) => {
    e.preventDefault();
    setFormNotice(null);
    if (sourceKind === "url" && !urlInput.trim()) {
      setFormNotice(en.home.urlValidationError);
      return;
    }

    if (!rightsConfirmed) {
      setFormNotice(en.home.rightsValidationError);
      return;
    }

    const payload: CreateJobRequest = {
      source:
        sourceKind === "url"
          ? { kind: "url", url: urlInput.trim() }
          : sourceKind === "upload"
            ? { kind: "upload", upload_id: "upload_local_demo" }
            : { kind: "drive", drive_file_id: "drive_source_demo" },
      language,
      script_pref: scriptPref,
      aspect,
      preset_id: selectedPresetId,
      auto_mode: true,
      max_clips: 8,
      clip_brief: clipBrief.trim() || null,
      rights_confirmed: true,
    };

    createJobMutation.mutate(payload, {
      onSuccess: (created) => {
        setFormNotice(null);
        setSelectedJobId(created.id);
        setSelectedClipId(created.clips?.[0]?.id ?? null);
        toast({
          title: en.home.jobCreatedToast,
          description: en.home.jobCreatedToastDesc,
          tone: "ok",
        });
      },
      onError: (err) => {
        setFormNotice(err instanceof Error ? err.message : en.states.errorBody);
      },
    });
  };

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

  const previewLines = Array.isArray(activeClip?.clip_plan?.caption_preview_lines)
    ? (activeClip.clip_plan.caption_preview_lines as string[])
    : [activeClipSummary?.hook_line ?? "90% startups yahi galti karte hain!"];

  const activeTemplateId =
    typeof activeClip?.clip_plan?.template_id === "string"
      ? activeClip.clip_plan.template_id
      : selectedTemplateId;

  const selectedTemplate =
    templates.find((t) => t.id === activeTemplateId) ?? templates[0];

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
          ) : (
            <>
              {activeTab === "home" ? (
                <div className="space-y-6">
                  {/* New Job Form Card */}
                  <Card aria-labelledby="new-job-heading" className="p-4 sm:p-5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <h2
                          id="new-job-heading"
                          className="text-lg font-bold text-text"
                        >
                          {en.home.title}
                        </h2>
                        <p className="text-xs sm:text-sm text-text-dim">
                          {en.home.subtitle}
                        </p>
                      </div>
                      <div
                        role="tablist"
                        aria-label="Video source type"
                        className="inline-flex rounded-sm border border-border bg-surface-2 p-1"
                      >
                        {(
                          [
                            {
                              id: "url",
                              label: en.home.sourceKinds.url,
                              icon: Link2,
                            },
                            {
                              id: "upload",
                              label: en.home.sourceKinds.upload,
                              icon: Upload,
                            },
                            {
                              id: "drive",
                              label: en.home.sourceKinds.drive,
                              icon: FolderSync,
                            },
                          ] as const
                        ).map(({ id, label, icon: Icon }) => (
                          <button
                            key={id}
                            type="button"
                            role="tab"
                            aria-selected={sourceKind === id}
                            onClick={() => setSourceKind(id)}
                            className={`inline-flex min-h-[44px] items-center gap-1.5 rounded-sm px-3 text-xs font-semibold transition ${
                              sourceKind === id
                                ? "bg-accent text-accent-fg"
                                : "text-text-dim hover:text-text"
                            }`}
                          >
                            <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                            <span>{label}</span>
                          </button>
                        ))}
                      </div>
                    </div>

                    <form onSubmit={handleCreateJob} className="mt-4 space-y-4">
                      {sourceKind === "url" ? (
                        <div>
                          <label htmlFor="job-url" className="sr-only">
                            Video URL
                          </label>
                          <input
                            id="job-url"
                            type="url"
                            value={urlInput}
                            onChange={(e) => setUrlInput(e.target.value)}
                            placeholder={en.home.urlPlaceholder}
                            className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 py-2 text-sm text-text placeholder:text-text-dim"
                          />
                        </div>
                      ) : (
                        <div className="rounded-sm border border-dashed border-border bg-surface-2 px-3 py-3 text-xs text-text-dim">
                          {sourceKind === "upload"
                            ? en.home.uploadHint
                            : en.home.driveHint}
                        </div>
                      )}

                      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                        <label className="block space-y-1 text-xs">
                          <span className="text-text-dim">
                            {en.home.languageLabel}
                          </span>
                          <select
                            value={language}
                            onChange={(e) =>
                              setLanguage(e.target.value as LanguageCode)
                            }
                            className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2.5 text-sm text-text"
                          >
                            <option value="auto">Auto</option>
                            <option value="hinglish">Hinglish</option>
                            <option value="en">English</option>
                            <option value="hi">Hindi (हिन्दी)</option>
                            <option value="bn">Bengali (বাংলা)</option>
                          </select>
                        </label>

                        <label className="block space-y-1 text-xs">
                          <span className="text-text-dim">
                            {en.home.scriptLabel}
                          </span>
                          <select
                            value={scriptPref}
                            onChange={(e) =>
                              setScriptPref(e.target.value as ScriptPref)
                            }
                            className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2.5 text-sm text-text"
                          >
                            <option value="auto">Auto</option>
                            <option value="latn">Roman / Latin</option>
                            <option value="native">Native Script</option>
                          </select>
                        </label>

                        <label className="block space-y-1 text-xs">
                          <span className="text-text-dim">
                            {en.home.aspectLabel}
                          </span>
                          <select
                            value={aspect}
                            onChange={(e) =>
                              setAspect(e.target.value as AspectRatio)
                            }
                            className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2.5 text-sm text-text"
                          >
                            <option value="9:16">9:16 Vertical</option>
                            <option value="1:1">1:1 Square</option>
                            <option value="4:5">4:5 Portrait</option>
                          </select>
                        </label>

                        <label className="block space-y-1 text-xs">
                          <span className="text-text-dim">
                            {en.home.presetLabel}
                          </span>
                          <select
                            value={selectedPresetId}
                            onChange={(e) => setSelectedPresetId(e.target.value)}
                            className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2.5 text-sm text-text"
                          >
                            {(presets.length > 0
                              ? presets
                              : [{ id: "default", name: "Viral Hinglish 9:16" }]
                            ).map((preset) => (
                              <option key={preset.id} value={preset.id}>
                                {preset.name}
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>

                      <label className="block space-y-1 text-xs">
                        <span className="text-text-dim">{en.home.briefLabel}</span>
                        <input
                          type="text"
                          value={clipBrief}
                          onChange={(e) => setClipBrief(e.target.value)}
                          placeholder={en.home.briefPlaceholder}
                          className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text placeholder:text-text-dim"
                        />
                      </label>

                      {formNotice ? (
                        <p role="alert" className="text-xs font-medium text-err">
                          {formNotice}
                        </p>
                      ) : null}

                      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <label className="flex min-h-[44px] items-center gap-2 text-xs text-text-dim">
                          <input
                            type="checkbox"
                            checked={rightsConfirmed}
                            onChange={(e) => setRightsConfirmed(e.target.checked)}
                            className="h-4 w-4 accent-[#FFD400]"
                          />
                          <span>{en.home.rightsLabel}</span>
                        </label>

                        <Button
                          type="submit"
                          loading={createJobMutation.isPending}
                          leftIcon={
                            <Sparkles className="h-4 w-4" aria-hidden="true" />
                          }
                        >
                          {createJobMutation.isPending
                            ? en.home.startingButton
                            : en.home.startButton}
                        </Button>
                      </div>
                    </form>
                  </Card>

                  {/* Clips + 9:16 Preview Split */}
                  {jobs.length === 0 ? (
                    <EmptyState
                      title={en.jobs.emptyTitle}
                      body={en.jobs.emptyBody}
                      actionLabel={en.jobs.emptyCta}
                      onAction={() => {
                        setSimulation("normal");
                        document.getElementById("job-url")?.focus();
                      }}
                    />
                  ) : (
                    <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
                      {/* Ranked Clips */}
                      <section
                        aria-labelledby="clips-heading"
                        className="space-y-3 lg:col-span-7"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div>
                            <h2 id="clips-heading" className="text-base font-bold">
                              {en.home.clipsTitle} ·{" "}
                              {activeJob?.title ?? "Untitled"}
                            </h2>
                            <p className="text-xs text-text-dim">
                              {activeJob?.platform?.toUpperCase() ?? "SOURCE"} ·{" "}
                              {activeJob?.language.toUpperCase()} ·{" "}
                              {activeJob?.clips?.length ?? 0} clips
                            </p>
                          </div>
                          {activeJob ? (
                            <StatusBadge status={activeJob.status} />
                          ) : null}
                        </div>

                        <div className="space-y-3">
                          {(activeJob?.clips ?? []).map((clip) => {
                            const selected = clip.id === resolvedClipId;
                            const scorePct = Math.round(clip.score * 100);
                            return (
                              <button
                                key={clip.id}
                                type="button"
                                onClick={() => setSelectedClipId(clip.id)}
                                className={`w-full rounded-lg border p-4 text-left transition ${
                                  selected
                                    ? "border-accent bg-surface"
                                    : "border-border bg-surface/60 hover:border-text-dim"
                                }`}
                              >
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                  <div className="flex items-center gap-2">
                                    <Badge tone="accent" size="sm">
                                      #{clip.rank} · {scorePct}/100
                                    </Badge>
                                    <span className="text-xs text-text-dim">
                                      {clip.start_s}s – {clip.end_s}s
                                    </span>
                                  </div>
                                  <StatusBadge status={clip.status} />
                                </div>
                                <p className="mt-2 text-sm font-semibold text-text">
                                  {clip.title ?? "Untitled Clip"}
                                </p>
                                {clip.reason ? (
                                  <p className="mt-1 text-xs text-text-dim">
                                    <span className="font-semibold text-text">
                                      {en.home.scoreWhy}:
                                    </span>{" "}
                                    {clip.reason}
                                  </p>
                                ) : null}
                              </button>
                            );
                          })}
                        </div>
                      </section>

                      {/* 9:16 Live Preview */}
                      <Card
                        aria-labelledby="preview-heading"
                        className="space-y-4 lg:col-span-5"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <h2
                            id="preview-heading"
                            className="text-sm font-bold uppercase tracking-wide text-text-dim"
                          >
                            {en.home.previewTitle}
                          </h2>
                          {selectedTemplate ? (
                            <span className="text-xs font-semibold text-accent">
                              {selectedTemplate.name}
                            </span>
                          ) : null}
                        </div>

                        {clipDetailQuery.isLoading ? (
                          <LoadingState
                            title="Loading clip preview..."
                            body=""
                            showSkeletons={false}
                          />
                        ) : clipDetailQuery.isError ? (
                          <ErrorState
                            message={
                              clipDetailQuery.error instanceof ApiError
                                ? clipDetailQuery.error.message
                                : en.states.errorBody
                            }
                            onRetry={() => void clipDetailQuery.refetch()}
                          />
                        ) : (
                          <>
                            <div className="mx-auto flex aspect-[9/16] max-h-[340px] w-full max-w-[200px] flex-col justify-between overflow-hidden rounded-md border border-border bg-bg p-3">
                              <div className="flex items-center justify-between text-[10px] text-text-dim">
                                <span>9:16 SAFE AREA</span>
                                <Clapperboard
                                  className="h-3.5 w-3.5"
                                  aria-hidden="true"
                                />
                              </div>
                              <div className="my-auto text-center">
                                {previewLines.map((line, idx) => (
                                  <p
                                    key={line}
                                    className={`text-sm font-extrabold leading-snug ${
                                      idx === 1
                                        ? "text-accent scale-105"
                                        : "text-text"
                                    }`}
                                  >
                                    {line}
                                  </p>
                                ))}
                              </div>
                              <div className="h-1 w-full overflow-hidden rounded-full bg-surface-2">
                                <div className="h-full w-2/3 bg-accent" />
                              </div>
                            </div>

                            {/* Score Breakdown */}
                            {activeClip?.score_breakdown ? (
                              <div className="space-y-2">
                                <p className="text-xs font-semibold text-text-dim">
                                  {en.home.scoreBreakdown}
                                </p>
                                <div className="space-y-1.5">
                                  {Object.entries(activeClip.score_breakdown).map(
                                    ([metric, val]) => {
                                      const num =
                                        typeof val === "number" ? val : 0;
                                      const pct = Math.round(num * 100);
                                      return (
                                        <div
                                          key={metric}
                                          className="flex items-center gap-2 text-xs"
                                        >
                                          <span className="w-16 capitalize text-text-dim">
                                            {metric}
                                          </span>
                                          <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface-2">
                                            <div
                                              className="h-full bg-accent"
                                              style={{ width: `${pct}%` }}
                                            />
                                          </div>
                                          <span className="w-8 text-right font-mono text-text">
                                            {pct}
                                          </span>
                                        </div>
                                      );
                                    }
                                  )}
                                </div>
                              </div>
                            ) : null}

                            {/* Quick Template Selector */}
                            <div className="grid grid-cols-2 gap-2">
                              {templates
                                .filter((t) => t.kind === "caption")
                                .slice(0, 4)
                                .map((tpl) => {
                                  const isSelected = activeTemplateId === tpl.id;
                                  return (
                                    <button
                                      key={tpl.id}
                                      type="button"
                                      onClick={() =>
                                        handleSelectCaptionTemplate(tpl.id)
                                      }
                                      className={`min-h-[44px] rounded-sm border px-2.5 py-2 text-left text-xs font-medium transition ${
                                        isSelected
                                          ? "border-accent bg-accent/10 text-accent"
                                          : "border-border bg-surface-2 text-text hover:border-text-dim"
                                      }`}
                                    >
                                      {tpl.name}
                                    </button>
                                  );
                                })}
                            </div>

                            <p className="text-[11px] text-text-dim">
                              {en.home.previewDisclaimer}
                            </p>
                          </>
                        )}
                      </Card>
                    </div>
                  )}
                </div>
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

                  {jobs.length === 0 ? (
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
                            <div className="flex items-center gap-2">
                              <StatusBadge status={job.status} />
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
                          <Card key={job.id} variant="interactive">
                            <CardHeader>
                              <CardTitle>{job.title ?? job.id}</CardTitle>
                              <Badge tone="route" size="sm">
                                {job.source_kind}
                              </Badge>
                            </CardHeader>
                            <CardContent className="text-xs text-text-dim">
                              {job.language} · {job.clips?.length ?? 0} clips
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

                  <Card variant="raised" className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
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
