import { useState } from "react";
import {
  CheckCircle2,
  Clapperboard,
  FolderSync,
  Link2,
  Sparkles,
  Upload,
} from "lucide-react";
import { apiClient } from "../../api/client";
import {
  isActiveJobStatus,
  useCreateJobMutation,
  useDriveFilesQuery,
  usePickDriveFileMutation,
} from "../../api/hooks";
import type {
  AspectRatio,
  ClipDetail,
  ClipSummary,
  CreateJobRequest,
  JobDetail,
  LanguageCode,
  Preset,
  ScriptPref,
  TemplateSummary,
} from "../../api/types";
import { en } from "../../i18n/en";
import { EmptyState, ErrorState, LoadingState } from "../StateViews";
import {
  Badge,
  Button,
  Card,
  StatusBadge,
  useToast,
} from "../ui";
import { JobTimelineView } from "./JobTimelineView";
import { detectVideoPlatform } from "./urlDetect";

export interface HomeViewProps {
  jobs: JobDetail[];
  activeJob: JobDetail | undefined;
  activeClip: ClipDetail | undefined;
  activeClipSummary: ClipSummary | undefined;
  resolvedClipId: string | null;
  clipLoading: boolean;
  clipError: string | null;
  onRetryClip: () => void;
  presets: Preset[];
  templates: TemplateSummary[];
  selectedPresetId: string;
  selectedTemplateId: string;
  onSelectPresetId: (id: string) => void;
  onSelectJobId: (id: string) => void;
  onSelectClipId: (id: string | null) => void;
  onSelectCaptionTemplate: (templateId: string) => void;
  onRequestDeleteJob: (job: JobDetail) => void;
  onResetEmptySimulation: () => void;
}

export function HomeView({
  jobs,
  activeJob,
  activeClip,
  activeClipSummary,
  resolvedClipId,
  clipLoading,
  clipError,
  onRetryClip,
  presets,
  templates,
  selectedPresetId,
  selectedTemplateId,
  onSelectPresetId,
  onSelectJobId,
  onSelectClipId,
  onSelectCaptionTemplate,
  onRequestDeleteJob,
  onResetEmptySimulation,
}: HomeViewProps) {
  const { toast } = useToast();

  const [sourceKind, setSourceKind] = useState<"url" | "upload" | "drive">("url");
  const [urlInput, setUrlInput] = useState(
    "https://www.youtube.com/watch?v=sample_hinglish_podcast"
  );
  const [uploadFilename, setUploadFilename] = useState<string | null>(null);
  const [uploadProgressPct, setUploadProgressPct] = useState<number>(0);
  const [uploadedId, setUploadedId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const [selectedDriveFileId, setSelectedDriveFileId] = useState<string | null>(null);
  const [selectedDriveLabel, setSelectedDriveLabel] = useState<string | null>(null);

  const [language, setLanguage] = useState<LanguageCode>("hinglish");
  const [scriptPref, setScriptPref] = useState<ScriptPref>("auto");
  const [aspect, setAspect] = useState<AspectRatio>("9:16");
  const [autoMode, setAutoMode] = useState(true);
  const [maxClips, setMaxClips] = useState(8);
  const [clipBrief, setClipBrief] = useState("");
  const [rightsConfirmed, setRightsConfirmed] = useState(true);
  const [formNotice, setFormNotice] = useState<string | null>(null);

  const createJobMutation = useCreateJobMutation();
  const driveFilesQuery = useDriveFilesQuery("source", {
    enabled: sourceKind === "drive",
  });
  const pickDriveMutation = usePickDriveFileMutation();

  const urlDetection = detectVideoPlatform(urlInput);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setFormNotice(null);
    setUploading(true);
    setUploadFilename(file.name);
    setUploadProgressPct(10);
    setUploadedId(null);

    try {
      const session = await apiClient.initUpload({
        filename: file.name,
        size_bytes: Math.max(file.size, 1),
        mime_type: file.type || "video/mp4",
      });
      const chunkSize = Math.max(1, Math.ceil(Math.max(file.size, 1) / 2));
      const totalChunks = 2;
      for (let i = 0; i < totalChunks; i++) {
        const slice = file.slice(i * chunkSize, (i + 1) * chunkSize);
        await apiClient.uploadChunk(session.id, i, slice);
        setUploadProgressPct(i === 0 ? 55 : 90);
      }
      const completed = await apiClient.completeUpload(session.id);
      setUploadedId(completed.upload_id ?? session.id);
      setUploadProgressPct(100);
    } catch (err) {
      setFormNotice(
        err instanceof Error ? err.message : en.home.uploadFailedLabel
      );
      setUploadProgressPct(0);
    } finally {
      setUploading(false);
    }
  };

  const handlePickDriveFile = (fileId: string, name: string) => {
    setFormNotice(null);
    pickDriveMutation.mutate(
      { file_id: fileId },
      {
        onSuccess: (res) => {
          setSelectedDriveFileId(res.drive_file_id);
          setSelectedDriveLabel(name);
        },
        onError: (err) => {
          setFormNotice(err.message);
        },
      }
    );
  };

  const handleCreateJob = (e: React.FormEvent) => {
    e.preventDefault();
    setFormNotice(null);

    if (sourceKind === "url") {
      if (!urlInput.trim()) {
        setFormNotice(en.home.urlValidationError);
        return;
      }
      if (!urlDetection.valid) {
        setFormNotice(urlDetection.error ?? en.home.urlInvalid);
        return;
      }
    } else if (sourceKind === "upload" && !uploadedId) {
      setFormNotice(en.home.uploadValidationError);
      return;
    } else if (sourceKind === "drive" && !selectedDriveFileId) {
      setFormNotice(en.home.driveValidationError);
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
            ? { kind: "upload", upload_id: uploadedId! }
            : { kind: "drive", drive_file_id: selectedDriveFileId! },
      language,
      script_pref: scriptPref,
      aspect,
      preset_id: selectedPresetId,
      auto_mode: autoMode,
      max_clips: Math.min(20, Math.max(1, maxClips)),
      clip_brief: clipBrief.trim() || null,
      rights_confirmed: true,
    };

    createJobMutation.mutate(payload, {
      onSuccess: (created) => {
        setFormNotice(null);
        onSelectJobId(created.id);
        onSelectClipId(created.clips?.[0]?.id ?? null);
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
    <div className="space-y-6">
      {/* New Job Form Card */}
      <Card aria-labelledby="new-job-heading" className="p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 id="new-job-heading" className="text-lg font-bold text-text">
              {en.home.title}
            </h2>
            <p className="text-xs sm:text-sm text-text-dim">{en.home.subtitle}</p>
          </div>
          <div
            role="tablist"
            aria-label="Video source type"
            className="inline-flex rounded-sm border border-border bg-surface-2 p-1"
          >
            {(
              [
                { id: "url", label: en.home.sourceKinds.url, icon: Link2 },
                { id: "upload", label: en.home.sourceKinds.upload, icon: Upload },
                { id: "drive", label: en.home.sourceKinds.drive, icon: FolderSync },
              ] as const
            ).map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={sourceKind === id}
                onClick={() => {
                  setSourceKind(id);
                  setFormNotice(null);
                }}
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
            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <label htmlFor="job-url" className="text-xs text-text-dim">
                  {en.home.urlLabel}
                </label>
                {urlDetection.badgeLabel ? (
                  <Badge
                    tone={
                      urlDetection.platform === "youtube"
                        ? "accent"
                        : urlDetection.platform === "direct"
                          ? "ok"
                          : "route"
                    }
                    size="sm"
                  >
                    {urlDetection.badgeLabel}
                  </Badge>
                ) : null}
              </div>
              <input
                id="job-url"
                type="text"
                inputMode="url"
                value={urlInput}
                onChange={(e) => {
                  setUrlInput(e.target.value);
                  setFormNotice(null);
                }}
                placeholder={en.home.urlPlaceholder}
                aria-invalid={Boolean(urlDetection.error)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 py-2 text-sm text-text placeholder:text-text-dim"
              />
              {urlDetection.error ? (
                <p role="alert" className="text-xs text-err">
                  {urlDetection.error}
                </p>
              ) : urlDetection.hint ? (
                <p className="text-xs text-warn">{urlDetection.hint}</p>
              ) : null}
            </div>
          ) : sourceKind === "upload" ? (
            <div className="space-y-2 rounded-sm border border-dashed border-border bg-surface-2 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs text-text-dim">{en.home.uploadHint}</span>
                <label className="inline-flex min-h-[44px] cursor-pointer items-center gap-2 rounded-sm border border-border bg-surface px-3 py-2 text-xs font-semibold text-text hover:border-accent">
                  <Upload className="h-4 w-4" aria-hidden="true" />
                  <span>{en.home.uploadPickFile}</span>
                  <input
                    type="file"
                    accept="video/*,.mp4,.mov,.mkv"
                    onChange={handleFileChange}
                    className="sr-only"
                  />
                </label>
              </div>
              {uploadFilename ? (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="truncate font-medium text-text">
                      {uploadFilename}
                    </span>
                    <span className="font-mono text-text-dim">
                      {uploading
                        ? `${en.home.uploadProgressLabel} ${uploadProgressPct}%`
                        : uploadedId
                          ? `${en.home.uploadReadyLabel} (${uploadedId})`
                          : `${uploadProgressPct}%`}
                    </span>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg">
                    <div
                      className={`h-full transition-[width] duration-150 ease-out ${
                        uploadedId ? "bg-ok" : "bg-accent"
                      }`}
                      style={{ width: `${uploadProgressPct}%` }}
                    />
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="space-y-2 rounded-sm border border-border bg-surface-2 p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-text-dim">{en.home.driveHint}</span>
                {selectedDriveLabel ? (
                  <Badge tone="ok" size="sm">
                    <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                    <span>{selectedDriveLabel}</span>
                  </Badge>
                ) : null}
              </div>

              {driveFilesQuery.isLoading ? (
                <p className="text-xs text-text-dim">{en.home.driveLoading}</p>
              ) : (driveFilesQuery.data?.items ?? []).length === 0 ? (
                <p className="text-xs text-text-dim">{en.home.driveEmpty}</p>
              ) : (
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {(driveFilesQuery.data?.items ?? []).map((item) => {
                    const id = String(item.id ?? "");
                    const name = String(item.name ?? id);
                    const isPicked = selectedDriveFileId === id;
                    return (
                      <button
                        key={id}
                        type="button"
                        onClick={() => handlePickDriveFile(id, name)}
                        className={`flex min-h-[44px] items-center justify-between gap-2 rounded-sm border px-3 py-2 text-left text-xs transition ${
                          isPicked
                            ? "border-accent bg-accent/10 text-accent"
                            : "border-border bg-surface text-text hover:border-text-dim"
                        }`}
                      >
                        <span className="truncate font-medium">{name}</span>
                        {item.size_mb ? (
                          <span className="shrink-0 text-[11px] text-text-dim">
                            {String(item.size_mb)} MB
                          </span>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <label className="block space-y-1 text-xs">
              <span className="text-text-dim">{en.home.languageLabel}</span>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value as LanguageCode)}
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
              <span className="text-text-dim">{en.home.scriptLabel}</span>
              <select
                value={scriptPref}
                onChange={(e) => setScriptPref(e.target.value as ScriptPref)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2.5 text-sm text-text"
              >
                <option value="auto">Auto</option>
                <option value="latn">Roman / Latin</option>
                <option value="native">Native (Deva / Beng)</option>
              </select>
            </label>

            <label className="block space-y-1 text-xs">
              <span className="text-text-dim">{en.home.aspectLabel}</span>
              <select
                value={aspect}
                onChange={(e) => setAspect(e.target.value as AspectRatio)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2.5 text-sm text-text"
              >
                <option value="9:16">9:16 Vertical</option>
                <option value="1:1">1:1 Square</option>
                <option value="4:5">4:5 Portrait</option>
              </select>
            </label>

            <label className="block space-y-1 text-xs">
              <span className="text-text-dim">{en.home.presetLabel}</span>
              <select
                value={selectedPresetId}
                onChange={(e) => onSelectPresetId(e.target.value)}
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

            <label className="block space-y-1 text-xs">
              <span className="text-text-dim">{en.home.maxClipsLabel}</span>
              <input
                type="number"
                min={1}
                max={20}
                value={maxClips}
                onChange={(e) => setMaxClips(Number(e.target.value) || 1)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2.5 text-sm text-text"
              />
            </label>
          </div>

          <div className="space-y-2">
            <label className="block space-y-1 text-xs">
              <span className="text-text-dim">{en.home.briefLabel}</span>
              <textarea
                rows={2}
                value={clipBrief}
                onChange={(e) => setClipBrief(e.target.value)}
                placeholder={en.home.briefPlaceholder}
                className="w-full rounded-sm border border-border bg-surface-2 px-3 py-2 text-sm text-text placeholder:text-text-dim"
              />
            </label>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] text-text-dim">
                {en.home.briefExamplesLabel}
              </span>
              {en.home.briefExamples.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => setClipBrief(example)}
                  className="min-h-[36px] rounded-full border border-border bg-surface-2 px-2.5 py-1 text-[11px] text-text-dim transition hover:border-accent hover:text-text"
                >
                  {example}
                </button>
              ))}
            </div>
          </div>

          <label className="flex min-h-[44px] items-center gap-2.5 text-xs text-text">
            <input
              type="checkbox"
              checked={autoMode}
              onChange={(e) => setAutoMode(e.target.checked)}
              className="h-4 w-4 accent-[#FFD400]"
            />
            <span>
              <span className="font-semibold">{en.home.autoModeLabel}</span>{" "}
              <span className="text-text-dim">— {en.home.autoModeHint}</span>
            </span>
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
              leftIcon={<Sparkles className="h-4 w-4" aria-hidden="true" />}
            >
              {createJobMutation.isPending
                ? en.home.startingButton
                : en.home.startButton}
            </Button>
          </div>
        </form>
      </Card>

      {/* Active Job Timeline (live SSE updates while active, or failed/cancelled recovery) */}
      {activeJob &&
      (isActiveJobStatus(activeJob.status) ||
        activeJob.status === "FAILED" ||
        activeJob.status === "CANCELLED") ? (
        <JobTimelineView job={activeJob} onRequestDelete={onRequestDeleteJob} />
      ) : null}

      {/* Clips + 9:16 Preview Split */}
      {jobs.length === 0 ? (
        <EmptyState
          title={en.jobs.emptyTitle}
          body={en.jobs.emptyBody}
          actionLabel={en.jobs.emptyCta}
          onAction={() => {
            onResetEmptySimulation();
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
                  {en.home.clipsTitle} · {activeJob?.title ?? "Untitled"}
                </h2>
                <p className="text-xs text-text-dim">
                  {activeJob?.platform?.toUpperCase() ?? "SOURCE"} ·{" "}
                  {activeJob?.language.toUpperCase()} ·{" "}
                  {activeJob?.clips?.length ?? 0} clips
                </p>
              </div>
              {activeJob ? <StatusBadge status={activeJob.status} /> : null}
            </div>

            <div className="space-y-3">
              {(activeJob?.clips ?? []).map((clip) => {
                const selected = clip.id === resolvedClipId;
                const scorePct = Math.round(clip.score * 100);
                return (
                  <button
                    key={clip.id}
                    type="button"
                    onClick={() => onSelectClipId(clip.id)}
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
          <Card aria-labelledby="preview-heading" className="space-y-4 lg:col-span-5">
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

            {clipLoading ? (
              <LoadingState
                title="Loading clip preview..."
                body=""
                showSkeletons={false}
              />
            ) : clipError ? (
              <ErrorState message={clipError} onRetry={onRetryClip} />
            ) : (
              <>
                <div className="mx-auto flex aspect-[9/16] max-h-[340px] w-full max-w-[200px] flex-col justify-between overflow-hidden rounded-md border border-border bg-bg p-3">
                  <div className="flex items-center justify-between text-[10px] text-text-dim">
                    <span>9:16 SAFE AREA</span>
                    <Clapperboard className="h-3.5 w-3.5" aria-hidden="true" />
                  </div>
                  <div className="my-auto text-center">
                    {previewLines.map((line, idx) => (
                      <p
                        key={line}
                        className={`text-sm font-extrabold leading-snug ${
                          idx === 1 ? "text-accent scale-105" : "text-text"
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
                          const num = typeof val === "number" ? val : 0;
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
                          onClick={() => onSelectCaptionTemplate(tpl.id)}
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
  );
}
