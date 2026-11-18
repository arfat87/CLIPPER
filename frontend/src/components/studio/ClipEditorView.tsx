import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  Download,
  Keyboard,
  Pause,
  Play,
  RotateCcw,
  Save,
  Scissors,
  SkipBack,
  SkipForward,
  Sparkles,
} from "lucide-react";
import { ApiError } from "../../api/client";
import {
  useClipQuery,
  useClipRendersQuery,
  useJobTranscriptQuery,
  usePatchClipMutation,
  usePatchJobTranscriptMutation,
  usePresetsQuery,
  useRenderClipMutation,
  useTemplatesQuery,
} from "../../api/hooks";
import type { LanguageCode } from "../../api/types";
import { en } from "../../i18n/en";
import {
  applyTextCase,
  type CaptionTemplateDoc,
  computeCaptionPreviewStyle,
  createDefaultTemplateDoc,
  SAFE_AREAS,
  type SafeAreaProfile,
  SAMPLE_LINES_BY_LANG,
} from "../../lib/captionPreview";
import { ErrorState, LoadingState } from "../StateViews";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  StatusBadge,
  useToast,
} from "../ui";

function formatMmSsDec(totalSeconds: number): string {
  const safe = Math.max(0, totalSeconds);
  const mins = Math.floor(safe / 60);
  const secs = Math.floor(safe % 60);
  const tenths = Math.floor((safe * 10) % 10);
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${tenths}`;
}

interface TranscriptWord {
  w: string;
  s: number;
  e: number;
  p?: number;
  flags?: string[];
  flatIdx: number;
}

function buildTemplateDocForId(templateId: string): CaptionTemplateDoc {
  const doc = createDefaultTemplateDoc(templateId, templateId);
  if (templateId === "devanagari_gold") {
    doc.colors.active = "#FFB020";
    doc.fonts.deva = {
      family: "Mukta",
      weight: 800,
      fallback: ["Noto Sans Devanagari"],
    };
  } else if (templateId === "bengali_bold_wave") {
    doc.colors.active = "#3DDC84";
    doc.fonts.beng = {
      family: "Hind Siliguri",
      weight: 700,
      fallback: ["Noto Sans Bengali"],
    };
  } else if (templateId === "clean_minimal_box") {
    doc.colors.box = "#0B0D10";
    doc.box = { enabled: true, padding_px: 16, radius_px: 12, opacity: 0.85 };
  }
  return doc;
}

export interface ClipEditorViewProps {
  clipId: string;
  jobLanguage?: LanguageCode;
  onOpenPublishPack?: () => void;
}

export function ClipEditorView({
  clipId,
  jobLanguage = "hinglish",
  onOpenPublishPack,
}: ClipEditorViewProps) {
  const { toast } = useToast();

  const clipQuery = useClipQuery(clipId);
  const rendersQuery = useClipRendersQuery(clipId);
  const presetsQuery = usePresetsQuery();
  const templatesQuery = useTemplatesQuery({
    kind: "caption",
    status: "published",
  });

  const clip = clipQuery.data;
  const transcriptQuery = useJobTranscriptQuery(clip?.job_id, {
    enabled: Boolean(clip?.job_id),
  });

  const patchClipMutation = usePatchClipMutation();
  const patchTranscriptMutation = usePatchJobTranscriptMutation();
  const renderClipMutation = useRenderClipMutation();

  const clipPlan = (clip?.clip_plan ?? {}) as Record<string, unknown>;
  const planTrim = (clipPlan.trim ?? {}) as {
    start_s?: number;
    end_s?: number;
  };
  const planOverlays = (clipPlan.overlays ?? {}) as {
    hook_title?: boolean;
    progress_bar?: boolean;
  };

  const baseStart = Number(planTrim.start_s ?? clip?.start_s ?? 0);
  const baseEnd = Number(planTrim.end_s ?? clip?.end_s ?? 30);
  const timelineMin = Math.max(0, Math.floor(baseStart - 10));
  const timelineMax = Math.max(baseEnd + 10, timelineMin + 15);

  const [optimisticPresetId, setOptimisticPresetId] = useState<string>(
    clip?.preset_id ?? "default"
  );
  const [optimisticTemplateId, setOptimisticTemplateId] = useState<string>(
    typeof clipPlan.template_id === "string"
      ? clipPlan.template_id
      : "bold_pop_yellow"
  );
  const [optimisticTemplateVersion, setOptimisticTemplateVersion] = useState<
    number | null
  >(
    typeof clipPlan.template_version === "number"
      ? clipPlan.template_version
      : null
  );
  const [optimisticLayout, setOptimisticLayout] = useState<"fill" | "fit_blur">(
    clipPlan.layout === "fit_blur" || clip?.layout === "fit_blur"
      ? "fit_blur"
      : "fill"
  );
  const [removeFillers, setRemoveFillers] = useState<boolean>(
    Boolean(clipPlan.remove_fillers ?? true)
  );
  const [trimSilence, setTrimSilence] = useState<boolean>(
    Boolean(clipPlan.trim_silence ?? true)
  );
  const [hookOverlay, setHookOverlay] = useState<boolean>(
    Boolean(planOverlays.hook_title ?? true)
  );
  const [progressOverlay, setProgressOverlay] = useState<boolean>(
    Boolean(planOverlays.progress_bar ?? true)
  );
  const [safeAreaProfile, setSafeAreaProfile] =
    useState<SafeAreaProfile>("shorts_default");

  const [trimStart, setTrimStart] = useState<number>(baseStart);
  const [trimEnd, setTrimEnd] = useState<number>(baseEnd);
  const [playhead, setPlayhead] = useState<number>(baseStart);
  const [isPlaying, setIsPlaying] = useState(false);

  // Transcript word edits staged locally before PATCH /jobs/:id/transcript
  const [editingWordIdx, setEditingWordIdx] = useState<number | null>(null);
  const [editingWordValue, setEditingWordValue] = useState<string>("");
  const [stagedEdits, setStagedEdits] = useState<Record<number, string>>({});

  // Sync local editor state when clip changes
  useEffect(() => {
    if (!clip) return;
    const plan = (clip.clip_plan ?? {}) as Record<string, unknown>;
    const trim = (plan.trim ?? {}) as { start_s?: number; end_s?: number };
    const overlays = (plan.overlays ?? {}) as {
      hook_title?: boolean;
      progress_bar?: boolean;
    };
    const nextStart = Number(trim.start_s ?? clip.start_s ?? 0);
    const nextEnd = Number(trim.end_s ?? clip.end_s ?? nextStart + 30);

    setOptimisticPresetId(clip.preset_id ?? "default");
    setOptimisticTemplateId(
      typeof plan.template_id === "string"
        ? plan.template_id
        : "bold_pop_yellow"
    );
    setOptimisticTemplateVersion(
      typeof plan.template_version === "number" ? plan.template_version : null
    );
    setOptimisticLayout(
      plan.layout === "fit_blur" || clip.layout === "fit_blur"
        ? "fit_blur"
        : "fill"
    );
    setRemoveFillers(Boolean(plan.remove_fillers ?? true));
    setTrimSilence(Boolean(plan.trim_silence ?? true));
    setHookOverlay(Boolean(overlays.hook_title ?? true));
    setProgressOverlay(Boolean(overlays.progress_bar ?? true));
    setTrimStart(nextStart);
    setTrimEnd(nextEnd);
    setPlayhead(nextStart);
    setIsPlaying(false);
    setStagedEdits({});
    setEditingWordIdx(null);
  }, [clip]);

  // Playback timer
  useEffect(() => {
    if (!isPlaying) return;
    const timer = window.setInterval(() => {
      setPlayhead((prev) => {
        const next = prev + 0.2;
        if (next >= trimEnd) {
          return trimStart;
        }
        return Number(next.toFixed(1));
      });
    }, 200);
    return () => window.clearInterval(timer);
  }, [isPlaying, trimStart, trimEnd]);

  // Flatten transcript words with global flatIdx
  const allWords = useMemo<TranscriptWord[]>(() => {
    const segs = transcriptQuery.data?.segments ?? [];
    const out: TranscriptWord[] = [];
    let flatIdx = 0;
    for (const seg of segs) {
      const words = Array.isArray(seg.words)
        ? (seg.words as Array<{
            w: string;
            s: number;
            e: number;
            p?: number;
            flags?: string[];
          }>)
        : [];
      for (const word of words) {
        out.push({
          w: stagedEdits[flatIdx] ?? word.w,
          s: word.s,
          e: word.e,
          p: word.p,
          flags: word.flags,
          flatIdx,
        });
        flatIdx++;
      }
    }
    return out;
  }, [transcriptQuery.data?.segments, stagedEdits]);

  const clipWords = useMemo(() => {
    const filtered = allWords.filter(
      (w) => w.e >= timelineMin && w.s <= timelineMax
    );
    return filtered.length > 0 ? filtered : allWords;
  }, [allWords, timelineMin, timelineMax]);

  // Deterministic waveform bars across [timelineMin, timelineMax]
  const waveformBars = useMemo(() => {
    const barCount = 48;
    const span = Math.max(1, timelineMax - timelineMin);
    return Array.from({ length: barCount }, (_, idx) => {
      const t = timelineMin + (idx / barCount) * span;
      const wordNearby = clipWords.some(
        (w) => t >= w.s - 0.3 && t <= w.e + 0.3
      );
      const base = wordNearby ? 52 : 18;
      const wave = Math.round(((Math.sin(idx * 1.7 + 0.4) + 1) / 2) * 40);
      return {
        idx,
        t,
        heightPct: Math.min(96, base + wave),
        inTrim: t >= trimStart && t <= trimEnd,
      };
    });
  }, [timelineMin, timelineMax, clipWords, trimStart, trimEnd]);

  const handlePatchClip = (
    patch: Record<string, unknown>,
    rollback?: () => void
  ) => {
    if (!clip) return;
    patchClipMutation.mutate(
      { id: clip.id, patch },
      {
        onSuccess: () => {
          toast({ title: en.clipEditor.planSavedToast, tone: "ok" });
        },
        onError: (err) => {
          rollback?.();
          toast({
            title: en.clipEditor.planErrorToast,
            description: err.message,
            tone: "err",
          });
        },
      }
    );
  };

  const handleTriggerRender = (profile: string) => {
    if (!clip) return;
    renderClipMutation.mutate(
      {
        id: clip.id,
        payload: {
          preset_id: optimisticPresetId,
          aspect:
            (clipPlan.aspect as "9:16" | "1:1" | "4:5" | undefined) ?? "9:16",
          profile,
        },
      },
      {
        onSuccess: () => {
          toast({
            title: en.clipEditor.renderQueuedToast,
            description: profile,
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

  // Keyboard shortcut refs to avoid stale closures
  const shortcutsRef = useRef({
    togglePlay: () => setIsPlaying((p) => !p),
    pause: () => setIsPlaying(false),
    seekBy: (delta: number) =>
      setPlayhead((p) =>
        Math.min(trimEnd, Math.max(trimStart, Number((p + delta).toFixed(1))))
      ),
    setIn: () => {
      const nextIn = Math.min(playhead, trimEnd - 3);
      setTrimStart(Number(nextIn.toFixed(1)));
      handlePatchClip({
        start_s: Number(nextIn.toFixed(1)),
        clip_plan: { trim: { start_s: Number(nextIn.toFixed(1)), end_s: trimEnd } },
      });
    },
    setOut: () => {
      const nextOut = Math.max(playhead, trimStart + 3);
      setTrimEnd(Number(nextOut.toFixed(1)));
      handlePatchClip({
        end_s: Number(nextOut.toFixed(1)),
        clip_plan: { trim: { start_s: trimStart, end_s: Number(nextOut.toFixed(1)) } },
      });
    },
    renderDraft: () => handleTriggerRender("720p_h264_fast"),
  });

  shortcutsRef.current = {
    togglePlay: () => setIsPlaying((p) => !p),
    pause: () => setIsPlaying(false),
    seekBy: (delta: number) =>
      setPlayhead((p) =>
        Math.min(trimEnd, Math.max(trimStart, Number((p + delta).toFixed(1))))
      ),
    setIn: () => {
      const nextIn = Math.min(playhead, trimEnd - 3);
      setTrimStart(Number(nextIn.toFixed(1)));
      handlePatchClip({
        start_s: Number(nextIn.toFixed(1)),
        clip_plan: { trim: { start_s: Number(nextIn.toFixed(1)), end_s: trimEnd } },
      });
    },
    setOut: () => {
      const nextOut = Math.max(playhead, trimStart + 3);
      setTrimEnd(Number(nextOut.toFixed(1)));
      handlePatchClip({
        end_s: Number(nextOut.toFixed(1)),
        clip_plan: { trim: { start_s: trimStart, end_s: Number(nextOut.toFixed(1)) } },
      });
    },
    renderDraft: () => handleTriggerRender("720p_h264_fast"),
  };

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") {
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        shortcutsRef.current.renderDraft();
        return;
      }
      if (e.code === "Space") {
        e.preventDefault();
        shortcutsRef.current.togglePlay();
      } else if (e.key === "j" || e.key === "J") {
        e.preventDefault();
        shortcutsRef.current.seekBy(-5);
      } else if (e.key === "k" || e.key === "K") {
        e.preventDefault();
        shortcutsRef.current.pause();
      } else if (e.key === "l" || e.key === "L") {
        e.preventDefault();
        shortcutsRef.current.seekBy(5);
      } else if (e.key === "[") {
        e.preventDefault();
        shortcutsRef.current.setIn();
      } else if (e.key === "]") {
        e.preventDefault();
        shortcutsRef.current.setOut();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  if (clipQuery.isLoading) return <LoadingState />;
  if (clipQuery.isError || !clip) {
    return (
      <ErrorState
        title={en.states.errorTitle}
        message={
          clipQuery.error instanceof ApiError
            ? clipQuery.error.message
            : en.states.errorBody
        }
        onRetry={() => void clipQuery.refetch()}
      />
    );
  }

  const presets = presetsQuery.data?.items ?? [];
  const captionTemplates = templatesQuery.data?.items ?? [];
  const activeTemplateMeta = captionTemplates.find(
    (t) => t.id === optimisticTemplateId
  );
  const isTemplateOutdated =
    activeTemplateMeta &&
    optimisticTemplateVersion !== null &&
    optimisticTemplateVersion < activeTemplateMeta.latest_version;

  const previewDoc = buildTemplateDocForId(optimisticTemplateId);
  const effectiveLang: Exclude<LanguageCode, "auto"> =
    jobLanguage === "auto" ? "hinglish" : jobLanguage;
  const previewStyles = computeCaptionPreviewStyle(
    previewDoc,
    240,
    effectiveLang
  );
  const safeArea = SAFE_AREAS[safeAreaProfile];

  // Active live caption words synced to playhead
  const activeWordIndexInClip = clipWords.findIndex(
    (w) => playhead >= w.s && playhead <= w.e
  );
  const captionSliceStart =
    activeWordIndexInClip >= 0 ? Math.max(0, activeWordIndexInClip - 1) : 0;
  const liveCaptionWords =
    clipWords.length > 0
      ? clipWords.slice(captionSliceStart, captionSliceStart + 3)
      : SAMPLE_LINES_BY_LANG[effectiveLang].words.map((w, i) => ({
          w,
          s: i,
          e: i + 1,
          flatIdx: i,
        }));

  const trimDuration = Math.max(1, trimEnd - trimStart);
  const playheadProgressPct = Math.min(
    100,
    Math.max(0, ((playhead - trimStart) / trimDuration) * 100)
  );

  const renders = rendersQuery.data?.items ?? [];
  const latestRender = renders[0];
  const unsavedTranscriptCount = Object.keys(stagedEdits).length;
  const trimDirty =
    Math.abs(trimStart - baseStart) > 0.05 ||
    Math.abs(trimEnd - baseEnd) > 0.05;

  const handleSelectPreset = (presetId: string, captionId: string, layout: string) => {
    const prevPreset = optimisticPresetId;
    const prevTemplate = optimisticTemplateId;
    const prevLayout = optimisticLayout;
    const nextLayout: "fill" | "fit_blur" =
      layout === "fit_blur" ? "fit_blur" : "fill";

    setOptimisticPresetId(presetId);
    setOptimisticTemplateId(captionId);
    setOptimisticLayout(nextLayout);
    handlePatchClip(
      {
        preset_id: presetId,
        layout: nextLayout,
        clip_plan: {
          preset_id: presetId,
          template_id: captionId,
          layout: nextLayout,
        },
      },
      () => {
        setOptimisticPresetId(prevPreset);
        setOptimisticTemplateId(prevTemplate);
        setOptimisticLayout(prevLayout);
      }
    );
  };

  const handleSelectTemplate = (templateId: string, latestVersion?: number) => {
    const prevTemplate = optimisticTemplateId;
    const prevVersion = optimisticTemplateVersion;
    setOptimisticTemplateId(templateId);
    if (latestVersion !== undefined) {
      setOptimisticTemplateVersion(latestVersion);
    }
    handlePatchClip(
      {
        clip_plan: {
          template_id: templateId,
          ...(latestVersion !== undefined ? { template_version: latestVersion } : {}),
        },
      },
      () => {
        setOptimisticTemplateId(prevTemplate);
        setOptimisticTemplateVersion(prevVersion);
      }
    );
  };

  const handleSaveTranscript = () => {
    const edits = Object.entries(stagedEdits).map(([idxStr, text]) => ({
      idx: Number(idxStr),
      text,
    }));
    if (edits.length === 0) return;
    patchTranscriptMutation.mutate(
      { jobId: clip.job_id, payload: { edits } },
      {
        onSuccess: () => {
          setStagedEdits({});
          setEditingWordIdx(null);
          toast({
            title: en.clipEditor.transcriptSavedToast,
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

  return (
    <div className="space-y-5">
      {/* Top Status & Shortcut Legend */}
      <Card className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-bold text-text">
              {clip.title ?? clip.id}
            </h2>
            <StatusBadge status={clip.status} />
            {isTemplateOutdated && activeTemplateMeta ? (
              <div className="flex items-center gap-1.5">
                <Badge tone="warn" size="sm">
                  {en.clipEditor.outdatedTemplateBadge} (v
                  {optimisticTemplateVersion} → v
                  {activeTemplateMeta.latest_version})
                </Badge>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    handleSelectTemplate(
                      activeTemplateMeta.id,
                      activeTemplateMeta.latest_version
                    );
                    toast({
                      title: en.clipEditor.templateUpdatedToast,
                      tone: "ok",
                    });
                  }}
                >
                  {en.clipEditor.updateTemplateCta}
                </Button>
              </div>
            ) : null}
          </div>
          <p className="mt-1 flex items-center gap-1.5 text-xs text-text-dim">
            <Keyboard className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>{en.clipEditor.shortcutsSummary}</span>
          </p>
        </div>

        {onOpenPublishPack ? (
          <Button
            size="sm"
            variant="secondary"
            onClick={onOpenPublishPack}
            leftIcon={<Sparkles className="h-4 w-4" aria-hidden="true" />}
          >
            {en.clipEditor.tabPublish}
          </Button>
        ) : null}
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left: 9:16 Video Player + Live CSS Caption Overlay */}
        <Card className="space-y-4 lg:col-span-5">
          <CardHeader>
            <div>
              <CardTitle>{en.home.previewTitle}</CardTitle>
              <CardDescription>
                {optimisticTemplateId} · {optimisticLayout}
              </CardDescription>
            </div>
            <label className="text-xs">
              <span className="sr-only">{en.clipEditor.safeAreaLabel}</span>
              <select
                aria-label={en.clipEditor.safeAreaLabel}
                value={safeAreaProfile}
                onChange={(e) =>
                  setSafeAreaProfile(e.target.value as SafeAreaProfile)
                }
                className="min-h-[44px] rounded-sm border border-border bg-surface-2 px-2.5 text-xs text-text"
              >
                <option value="shorts_default">Safe: Shorts</option>
                <option value="reels_default">Safe: Reels</option>
                <option value="tiktok_default">Safe: TikTok</option>
                <option value="none">Safe: Off</option>
              </select>
            </label>
          </CardHeader>

          {/* 9:16 Stage */}
          <div className="mx-auto w-full max-w-[260px]">
            <div
              data-testid="editor-9x16-stage"
              className={`relative aspect-[9/16] w-full overflow-hidden rounded-lg border border-border ${
                optimisticLayout === "fit_blur"
                  ? "bg-gradient-to-b from-[#1a2234] via-[#0e131b] to-[#1a2234]"
                  : "bg-gradient-to-b from-[#1C2230] via-[#10141C] to-[#0B0D10]"
              }`}
            >
              {/* Simulated / Rendered Frame Content */}
              {latestRender?.status === "DONE" && latestRender.file_url ? (
                <video
                  aria-label={clip.title ?? "Clip player"}
                  poster={latestRender.thumb_url ?? undefined}
                  className={`h-full w-full ${
                    optimisticLayout === "fit_blur"
                      ? "object-contain"
                      : "object-cover"
                  }`}
                />
              ) : null}

              {/* Progress bar overlay */}
              {progressOverlay ? (
                <div
                  aria-label="Clip progress overlay"
                  className="absolute inset-x-0 top-0 z-20 h-1 bg-surface-2/80"
                >
                  <div
                    className="h-full bg-accent transition-all"
                    style={{ width: `${playheadProgressPct}%` }}
                  />
                </div>
              ) : null}

              {/* Hook title overlay */}
              {hookOverlay && clip.hook_line ? (
                <div className="absolute inset-x-3 top-6 z-20 rounded-sm bg-bg/85 px-2.5 py-1.5 text-center text-[11px] font-bold text-accent border border-accent/40">
                  {clip.hook_line}
                </div>
              ) : null}

              {/* Safe Area Frame Overlay */}
              {safeAreaProfile !== "none" ? (
                <div
                  aria-label="Safe-area guide overlay"
                  className="pointer-events-none absolute border border-dashed border-ok/60 z-10"
                  style={{
                    top: `${safeArea.top_pct}%`,
                    bottom: `${safeArea.bottom_pct}%`,
                    left: `${safeArea.left_pct}%`,
                    right: `${safeArea.right_pct}%`,
                  }}
                />
              ) : null}

              {/* Live CSS Caption Overlay (<100ms switch) */}
              <div
                data-testid="live-caption-overlay"
                className="pointer-events-none absolute inset-x-3 z-20"
                style={previewStyles.containerStyle}
              >
                <p style={previewStyles.textStyle}>
                  {liveCaptionWords.map((word, idx) => {
                    const isActive =
                      activeWordIndexInClip >= 0
                        ? playhead >= word.s && playhead <= word.e
                        : idx === 1;
                    const display = applyTextCase(
                      word.w,
                      previewDoc.text.case,
                      effectiveLang
                    );
                    return (
                      <span
                        key={`${word.flatIdx}-${idx}`}
                        style={isActive ? previewStyles.activeWordStyle : undefined}
                      >
                        {idx > 0 ? " " : ""}
                        {display}
                      </span>
                    );
                  })}
                </p>
              </div>

              <div className="absolute bottom-2 left-2 right-2 z-20 flex items-center justify-between rounded-sm bg-bg/80 px-2 py-1 text-[10px] font-mono text-text-dim">
                <span>{formatMmSsDec(playhead)}</span>
                <span>{formatMmSsDec(trimEnd)}</span>
              </div>
            </div>
          </div>

          {/* Transport Controls & Playhead Scrub */}
          <div className="space-y-2">
            <input
              type="range"
              aria-label={en.clipEditor.scrubLabel}
              min={trimStart}
              max={trimEnd}
              step={0.1}
              value={Math.min(trimEnd, Math.max(trimStart, playhead))}
              onChange={(e) => setPlayhead(Number(e.target.value))}
              className="min-h-[44px] w-full accent-[#FFD400]"
            />

            <div className="flex items-center justify-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                aria-label={en.clipEditor.seekBackLabel}
                onClick={() =>
                  setPlayhead((p) => Math.max(trimStart, Number((p - 5).toFixed(1))))
                }
              >
                <SkipBack className="h-4 w-4" aria-hidden="true" />
              </Button>
              <Button
                size="sm"
                variant="primary"
                aria-label={
                  isPlaying ? en.clipEditor.pauseLabel : en.clipEditor.playLabel
                }
                onClick={() => setIsPlaying((p) => !p)}
                leftIcon={
                  isPlaying ? (
                    <Pause className="h-4 w-4" aria-hidden="true" />
                  ) : (
                    <Play className="h-4 w-4" aria-hidden="true" />
                  )
                }
              >
                {isPlaying ? en.clipEditor.pauseLabel : en.clipEditor.playLabel}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                aria-label={en.clipEditor.seekForwardLabel}
                onClick={() =>
                  setPlayhead((p) => Math.min(trimEnd, Number((p + 5).toFixed(1))))
                }
              >
                <SkipForward className="h-4 w-4" aria-hidden="true" />
              </Button>
            </div>
          </div>

          {/* Overlay Toggles */}
          <div className="grid grid-cols-2 gap-2 border-t border-border pt-3 text-xs">
            <label className="flex min-h-[44px] cursor-pointer items-center gap-2 rounded-sm border border-border bg-surface-2 px-2.5">
              <input
                type="checkbox"
                checked={hookOverlay}
                onChange={(e) => {
                  const next = e.target.checked;
                  const prev = hookOverlay;
                  setHookOverlay(next);
                  handlePatchClip(
                    {
                      clip_plan: {
                        overlays: { hook_title: next, progress_bar: progressOverlay },
                      },
                    },
                    () => setHookOverlay(prev)
                  );
                }}
                className="h-4 w-4 accent-[#FFD400]"
              />
              <span>{en.clipEditor.hookOverlayToggle}</span>
            </label>

            <label className="flex min-h-[44px] cursor-pointer items-center gap-2 rounded-sm border border-border bg-surface-2 px-2.5">
              <input
                type="checkbox"
                checked={progressOverlay}
                onChange={(e) => {
                  const next = e.target.checked;
                  const prev = progressOverlay;
                  setProgressOverlay(next);
                  handlePatchClip(
                    {
                      clip_plan: {
                        overlays: { hook_title: hookOverlay, progress_bar: next },
                      },
                    },
                    () => setProgressOverlay(prev)
                  );
                }}
                className="h-4 w-4 accent-[#FFD400]"
              />
              <span>{en.clipEditor.progressOverlayToggle}</span>
            </label>
          </div>
        </Card>

        {/* Right: Presets/Layout + Waveform Trim + Ops + Transcript + Render */}
        <div className="space-y-4 lg:col-span-7">
          {/* Preset Carousel & Layout Switch */}
          <Card className="space-y-4">
            <CardHeader>
              <CardTitle>{en.clipEditor.presetCarouselTitle}</CardTitle>
              <Badge tone="neutral">{optimisticPresetId}</Badge>
            </CardHeader>

            {/* Presets Carousel */}
            <div className="flex gap-2 overflow-x-auto pb-1">
              {presets.map((preset) => {
                const active = preset.id === optimisticPresetId;
                return (
                  <button
                    key={preset.id}
                    type="button"
                    aria-pressed={active}
                    onClick={() =>
                      handleSelectPreset(
                        preset.id,
                        preset.caption,
                        preset.layout
                      )
                    }
                    className={`min-h-[44px] shrink-0 rounded-sm border px-3 py-2 text-left text-xs transition ${
                      active
                        ? "border-accent bg-accent/15 text-text"
                        : "border-border bg-surface-2 text-text-dim hover:text-text"
                    }`}
                  >
                    <p className="font-bold text-text">{preset.name}</p>
                    <p className="text-[11px] text-text-dim">
                      {preset.caption}
                    </p>
                  </button>
                );
              })}
            </div>

            {/* Caption Templates Quick Switch */}
            <div className="flex flex-wrap gap-2">
              {captionTemplates.map((tpl) => {
                const active = tpl.id === optimisticTemplateId;
                return (
                  <button
                    key={tpl.id}
                    type="button"
                    aria-pressed={active}
                    onClick={() =>
                      handleSelectTemplate(tpl.id, tpl.latest_version)
                    }
                    className={`min-h-[44px] rounded-sm border px-3 py-1.5 text-xs font-medium transition ${
                      active
                        ? "border-accent bg-accent text-accent-fg font-semibold"
                        : "border-border bg-surface-2 text-text-dim hover:text-text"
                    }`}
                  >
                    {tpl.name} (v{tpl.latest_version})
                  </button>
                );
              })}
            </div>

            {/* Framing Layout Switch */}
            <div className="space-y-1.5 border-t border-border pt-3">
              <p className="text-xs font-semibold text-text-dim">
                {en.clipEditor.layoutTitle}
              </p>
              <div className="grid grid-cols-2 gap-2">
                {(
                  [
                    { id: "fill", label: en.clipEditor.layoutFill },
                    { id: "fit_blur", label: en.clipEditor.layoutFitBlur },
                  ] as const
                ).map((item) => (
                  <Button
                    key={item.id}
                    size="sm"
                    variant={
                      optimisticLayout === item.id ? "primary" : "secondary"
                    }
                    onClick={() => {
                      const prev = optimisticLayout;
                      setOptimisticLayout(item.id);
                      handlePatchClip(
                        {
                          layout: item.id,
                          clip_plan: { layout: item.id },
                        },
                        () => setOptimisticLayout(prev)
                      );
                    }}
                  >
                    {item.label}
                  </Button>
                ))}
              </div>
            </div>
          </Card>

          {/* Waveform Visualization & Trim Handles */}
          <Card className="space-y-3">
            <CardHeader>
              <div className="flex items-center gap-2">
                <Scissors className="h-4 w-4 text-accent" aria-hidden="true" />
                <CardTitle>{en.clipEditor.timelineTitle}</CardTitle>
              </div>
              <Badge tone="neutral">
                {trimDuration.toFixed(1)}s
              </Badge>
            </CardHeader>

            {/* SVG Waveform */}
            <div
              aria-label="Clip waveform timeline"
              className="relative h-20 w-full rounded-sm border border-border bg-bg p-2"
            >
              <svg
                viewBox="0 0 480 64"
                preserveAspectRatio="none"
                className="h-full w-full"
                aria-hidden="true"
              >
                {waveformBars.map((bar) => {
                  const h = Math.round((bar.heightPct / 100) * 56);
                  const y = Math.round((64 - h) / 2);
                  return (
                    <rect
                      key={bar.idx}
                      x={bar.idx * 10 + 1}
                      y={y}
                      width={7}
                      height={h}
                      rx={2}
                      fill={bar.inTrim ? "#FFD400" : "#2A3242"}
                      opacity={bar.inTrim ? 0.9 : 0.5}
                    />
                  );
                })}
                {/* Playhead line */}
                <line
                  x1={
                    ((playhead - timelineMin) /
                      Math.max(1, timelineMax - timelineMin)) *
                    480
                  }
                  x2={
                    ((playhead - timelineMin) /
                      Math.max(1, timelineMax - timelineMin)) *
                    480
                  }
                  y1={0}
                  y2={64}
                  stroke="#3DDC84"
                  strokeWidth={2}
                />
              </svg>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 text-xs">
              <label className="space-y-1">
                <div className="flex justify-between">
                  <span className="text-text-dim">
                    {en.clipEditor.trimStartLabel}
                  </span>
                  <span className="font-mono font-bold text-text">
                    {trimStart.toFixed(1)}s
                  </span>
                </div>
                <input
                  type="range"
                  aria-label={en.clipEditor.trimStartLabel}
                  min={timelineMin}
                  max={Math.max(timelineMin, trimEnd - 3)}
                  step={0.5}
                  value={trimStart}
                  onChange={(e) => {
                    const val = Math.min(Number(e.target.value), trimEnd - 3);
                    setTrimStart(val);
                    if (playhead < val) setPlayhead(val);
                  }}
                  className="min-h-[44px] w-full accent-[#FFD400]"
                />
              </label>

              <label className="space-y-1">
                <div className="flex justify-between">
                  <span className="text-text-dim">
                    {en.clipEditor.trimEndLabel}
                  </span>
                  <span className="font-mono font-bold text-text">
                    {trimEnd.toFixed(1)}s
                  </span>
                </div>
                <input
                  type="range"
                  aria-label={en.clipEditor.trimEndLabel}
                  min={Math.min(timelineMax, trimStart + 3)}
                  max={timelineMax}
                  step={0.5}
                  value={trimEnd}
                  onChange={(e) => {
                    const val = Math.max(Number(e.target.value), trimStart + 3);
                    setTrimEnd(val);
                    if (playhead > val) setPlayhead(val);
                  }}
                  className="min-h-[44px] w-full accent-[#FFD400]"
                />
              </label>
            </div>

            <div className="flex items-center justify-between gap-2">
              {trimDirty ? (
                <Badge tone="warn">Unsaved trim bounds</Badge>
              ) : (
                <span className="text-xs text-text-dim">
                  Window: {formatMmSsDec(trimStart)} – {formatMmSsDec(trimEnd)}
                </span>
              )}
              <Button
                size="sm"
                variant="secondary"
                loading={patchClipMutation.isPending}
                onClick={() =>
                  handlePatchClip({
                    start_s: trimStart,
                    end_s: trimEnd,
                    clip_plan: { trim: { start_s: trimStart, end_s: trimEnd } },
                  })
                }
              >
                {en.clipEditor.saveTrimCta}
              </Button>
            </div>
          </Card>

          {/* Word-Level Transcript Editor */}
          <Card className="space-y-3">
            <CardHeader>
              <div>
                <CardTitle>{en.clipEditor.transcriptTitle}</CardTitle>
                <CardDescription>
                  {en.clipEditor.transcriptHint}
                </CardDescription>
              </div>

              {unsavedTranscriptCount > 0 ? (
                <div className="flex items-center gap-2">
                  <Badge tone="warn">
                    {en.clipEditor.transcriptUnsavedBadge} ({unsavedTranscriptCount})
                  </Badge>
                  <Button
                    size="sm"
                    loading={patchTranscriptMutation.isPending}
                    onClick={handleSaveTranscript}
                    leftIcon={<Save className="h-4 w-4" aria-hidden="true" />}
                  >
                    {en.clipEditor.transcriptSaveCta}
                  </Button>
                </div>
              ) : null}
            </CardHeader>

            <div className="flex flex-wrap gap-1.5">
              {clipWords.map((word) => {
                const isLowConf = Boolean(word.flags?.includes("low_conf"));
                const isEdited =
                  stagedEdits[word.flatIdx] !== undefined ||
                  Boolean(word.flags?.includes("edited"));
                const isActive = playhead >= word.s && playhead <= word.e;
                return (
                  <button
                    key={word.flatIdx}
                    type="button"
                    onClick={() => {
                      setPlayhead(word.s);
                      setEditingWordIdx(word.flatIdx);
                      setEditingWordValue(word.w);
                    }}
                    className={`min-h-[44px] rounded-sm border px-2.5 py-1 text-xs font-medium transition ${
                      isActive
                        ? "border-accent bg-accent/20 text-text"
                        : isLowConf
                          ? "border-warn/60 border-dashed bg-warn/10 text-warn"
                          : isEdited
                            ? "border-ok/60 bg-ok/10 text-ok"
                            : "border-border bg-surface-2 text-text hover:border-text-dim"
                    }`}
                  >
                    {word.w}
                  </button>
                );
              })}
            </div>

            {editingWordIdx !== null ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const nextVal = editingWordValue.trim();
                  if (!nextVal) return;
                  setStagedEdits((prev) => ({
                    ...prev,
                    [editingWordIdx]: nextVal,
                  }));
                  setEditingWordIdx(null);
                }}
                className="flex flex-wrap items-center gap-2 rounded-sm border border-border bg-surface-2 p-2.5"
              >
                <input
                  type="text"
                  aria-label="Edit transcript word"
                  value={editingWordValue}
                  onChange={(e) => setEditingWordValue(e.target.value)}
                  placeholder={en.clipEditor.wordEditPlaceholder}
                  className="min-h-[44px] flex-1 rounded-sm border border-border bg-surface px-3 text-xs text-text"
                />
                <Button
                  type="submit"
                  size="sm"
                  leftIcon={<Check className="h-4 w-4" aria-hidden="true" />}
                >
                  {en.clipEditor.wordApplyCta}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setEditingWordIdx(null)}
                >
                  {en.clipEditor.wordCancelCta}
                </Button>
              </form>
            ) : null}
          </Card>

          {/* Ops Toggles + Render Draft / Final */}
          <Card className="space-y-4">
            <CardHeader>
              <div>
                <CardTitle>{en.clipEditor.opsTitle}</CardTitle>
              </div>
              {latestRender ? <StatusBadge status={latestRender.status} /> : null}
            </CardHeader>

            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 text-xs">
              <label className="flex min-h-[44px] cursor-pointer items-center gap-2 rounded-sm border border-border bg-surface-2 px-3">
                <input
                  type="checkbox"
                  checked={removeFillers}
                  onChange={(e) => {
                    const next = e.target.checked;
                    const prev = removeFillers;
                    setRemoveFillers(next);
                    handlePatchClip(
                      { clip_plan: { remove_fillers: next } },
                      () => setRemoveFillers(prev)
                    );
                  }}
                  className="h-4 w-4 accent-[#FFD400]"
                />
                <span>{en.clipEditor.removeFillersLabel}</span>
              </label>

              <label className="flex min-h-[44px] cursor-pointer items-center gap-2 rounded-sm border border-border bg-surface-2 px-3">
                <input
                  type="checkbox"
                  checked={trimSilence}
                  onChange={(e) => {
                    const next = e.target.checked;
                    const prev = trimSilence;
                    setTrimSilence(next);
                    handlePatchClip(
                      { clip_plan: { trim_silence: next } },
                      () => setTrimSilence(prev)
                    );
                  }}
                  className="h-4 w-4 accent-[#FFD400]"
                />
                <span>{en.clipEditor.trimSilenceLabel}</span>
              </label>
            </div>

            {latestRender?.status === "FAILED" ? (
              <div
                role="alert"
                className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-err/50 bg-err/10 p-3 text-xs"
              >
                <div className="flex items-start gap-2">
                  <AlertTriangle
                    className="mt-0.5 h-4 w-4 shrink-0 text-err"
                    aria-hidden="true"
                  />
                  <div>
                    <p className="font-bold text-text">
                      {en.clipEditor.renderFailedTitle}
                    </p>
                    <p className="text-text-dim">
                      {en.clipEditor.renderFailedHint}
                    </p>
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => handleTriggerRender("720p_h264_fast")}
                  leftIcon={
                    <RotateCcw className="h-4 w-4" aria-hidden="true" />
                  }
                >
                  {en.clipEditor.retryRenderCta}
                </Button>
              </div>
            ) : null}

            <CardContent className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3 mt-0">
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  loading={renderClipMutation.isPending}
                  onClick={() => handleTriggerRender("720p_h264_fast")}
                >
                  {en.clipEditor.renderDraftCta}
                </Button>
                <Button
                  size="sm"
                  variant="primary"
                  loading={renderClipMutation.isPending}
                  onClick={() => handleTriggerRender("1080p_h264_hq")}
                >
                  {en.clipEditor.renderFinalCta}
                </Button>
              </div>

              {latestRender?.status === "DONE" && latestRender.file_url ? (
                <a
                  href={latestRender.file_url}
                  download={`${clip.id}.mp4`}
                  className="inline-flex min-h-[44px] items-center gap-1.5 rounded-sm border border-border bg-surface-2 px-3 text-xs font-semibold text-text hover:border-accent"
                >
                  <Download className="h-4 w-4" aria-hidden="true" />
                  <span>{en.clipEditor.downloadMp4Cta}</span>
                </a>
              ) : null}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

export default ClipEditorView;
