import { useMemo, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import {
  AlertCircle,
  ChevronDown,
  ChevronUp,
  Clapperboard,
  Eye,
  Play,
  SlidersHorizontal,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { apiClient } from "../../api/client";
import {
  queryKeys,
  useRenderAllJobClipsMutation,
  useRenderClipMutation,
  useReselectJobClipsMutation,
  useSubmitClipFeedbackMutation,
} from "../../api/hooks";
import type {
  AspectRatio,
  ClipDetail,
  ClipSummary,
  JobDetail,
} from "../../api/types";
import { en } from "../../i18n/en";
import { useStudioStore } from "../../store/studioStore";
import { EmptyState } from "../StateViews";
import { Badge, Button, Card, StatusBadge, useToast } from "../ui";

function formatMmSs(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  const mins = Math.floor(safe / 60);
  const secs = safe % 60;
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function formatDurationSeconds(startS: number, endS: number): string {
  const dur = Math.max(1, Math.round(endS - startS));
  return `${dur}s`;
}

export interface ClipGridViewProps {
  job: JobDetail | undefined;
  selectedClipId: string | null;
  activeClipDetail?: ClipDetail;
  requestedMaxClips?: number;
  onSelectClipId: (clipId: string) => void;
  onOpenEditor?: (clip: ClipSummary) => void;
  onOpenPublishPack?: (clip: ClipSummary) => void;
}

export function ClipGridView({
  job,
  selectedClipId,
  activeClipDetail,
  requestedMaxClips = 8,
  onSelectClipId,
  onOpenEditor,
  onOpenPublishPack,
}: ClipGridViewProps) {
  const { toast } = useToast();
  const simulation = useStudioStore((s) => s.simulation);
  const openClipWorkspace = useStudioStore((s) => s.openClipWorkspace);

  const [selectedIds, setSelectedIds] = useState<Record<string, boolean>>({});
  const [collapsedReasons, setCollapsedReasons] = useState<
    Record<string, boolean>
  >({});
  const [viewsPopoverClipId, setViewsPopoverClipId] = useState<string | null>(
    null
  );
  const [viewsInput, setViewsInput] = useState<string>("");
  const [viewsFeedbackValue, setViewsFeedbackValue] = useState<"up" | "down">(
    "up"
  );

  // Reselect drawer/panel state
  const [reselectOpen, setReselectOpen] = useState(false);
  const [reselectBrief, setReselectBrief] = useState("");
  const [reselectThreshold, setReselectThreshold] = useState(0.4);
  const [reselectMaxClips, setReselectMaxClips] = useState(requestedMaxClips);

  const feedbackMutation = useSubmitClipFeedbackMutation();
  const reselectMutation = useReselectJobClipsMutation();
  const renderAllMutation = useRenderAllJobClipsMutation();
  const renderClipMutation = useRenderClipMutation();

  const sortedClips = useMemo(() => {
    const raw = job?.clips ?? [];
    return [...raw].sort((a, b) => b.score - a.score);
  }, [job?.clips]);

  // Fetch ClipDetail (for score_breakdown & clip_plan) for visible clips
  const clipDetailQueries = useQueries({
    queries: sortedClips.slice(0, 20).map((clip) => ({
      queryKey: queryKeys.clip(clip.id, simulation),
      queryFn: () => apiClient.getClip(clip.id),
      enabled: simulation === "normal",
      staleTime: 10_000,
      retry: false,
    })),
  });

  // Fetch RenderRecords (for thumb_url) for visible clips
  const clipRenderQueries = useQueries({
    queries: sortedClips.slice(0, 20).map((clip) => ({
      queryKey: queryKeys.clipRenders(clip.id, simulation),
      queryFn: () => apiClient.listClipRenders(clip.id),
      enabled: simulation === "normal",
      staleTime: 10_000,
      retry: false,
    })),
  });

  const detailById = useMemo(() => {
    const map: Record<string, ClipDetail> = {};
    sortedClips.slice(0, 20).forEach((clip, idx) => {
      const data = clipDetailQueries[idx]?.data;
      if (data) {
        map[clip.id] = data;
      }
    });
    if (activeClipDetail) {
      map[activeClipDetail.id] = activeClipDetail;
    }
    return map;
  }, [sortedClips, clipDetailQueries, activeClipDetail]);

  const thumbById = useMemo(() => {
    const map: Record<string, string> = {};
    sortedClips.slice(0, 20).forEach((clip, idx) => {
      const renders = clipRenderQueries[idx]?.data?.items ?? [];
      const withThumb = renders.find((r) => Boolean(r.thumb_url));
      if (withThumb?.thumb_url) {
        map[clip.id] = withThumb.thumb_url;
      }
    });
    return map;
  }, [sortedClips, clipRenderQueries]);

  const selectedClipList = sortedClips.filter((c) => selectedIds[c.id]);
  const selectedCount = selectedClipList.length;

  const toggleSelectClip = (clipId: string) => {
    setSelectedIds((prev) => ({
      ...prev,
      [clipId]: !prev[clipId],
    }));
  };

  const handleSelectAll = () => {
    if (selectedCount === sortedClips.length && sortedClips.length > 0) {
      setSelectedIds({});
      return;
    }
    const next: Record<string, boolean> = {};
    for (const c of sortedClips) {
      next[c.id] = true;
    }
    setSelectedIds(next);
  };

  const handleSubmitFeedback = (
    clip: ClipSummary,
    value: "up" | "down",
    actualViews?: number
  ) => {
    feedbackMutation.mutate(
      {
        id: clip.id,
        jobId: clip.job_id,
        payload: {
          value,
          ...(actualViews !== undefined ? { actual_views: actualViews } : {}),
        },
      },
      {
        onSuccess: () => {
          setViewsPopoverClipId(null);
          toast({
            title: en.clipGrid.feedbackSavedToast,
            description: clip.title ?? clip.id,
            tone: "ok",
          });
        },
        onError: (err) => {
          toast({
            title: en.clipGrid.feedbackErrorToast,
            description: err.message,
            tone: "err",
          });
        },
      }
    );
  };

  const handleOpenViewsPopover = (clip: ClipSummary) => {
    if (viewsPopoverClipId === clip.id) {
      setViewsPopoverClipId(null);
      return;
    }
    setViewsPopoverClipId(clip.id);
    setViewsInput(
      clip.actual_views !== null && clip.actual_views !== undefined
        ? String(clip.actual_views)
        : ""
    );
    setViewsFeedbackValue(clip.feedback ?? "up");
  };

  const handleBatchRenderSelected = async () => {
    if (!job || selectedClipList.length === 0) return;
    try {
      await Promise.all(
        selectedClipList.map((clip) =>
          renderClipMutation.mutateAsync({
            id: clip.id,
            payload: {
              preset_id: clip.preset_id ?? job.preset_id ?? "default",
            },
          })
        )
      );
      setSelectedIds({});
      toast({
        title: `${en.clipGrid.renderQueuedToast} (${selectedClipList.length})`,
        tone: "ok",
      });
    } catch (err) {
      toast({
        title: en.states.errorTitle,
        description: err instanceof Error ? err.message : en.states.errorBody,
        tone: "err",
      });
    }
  };

  const handleRenderAll = () => {
    if (!job) return;
    renderAllMutation.mutate(
      {
        id: job.id,
        payload: job.preset_id ? { preset_id: job.preset_id } : {},
      },
      {
        onSuccess: () => {
          toast({
            title: en.clipGrid.renderAllQueuedToast,
            description: `${sortedClips.length} clips`,
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

  const handleReselectSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!job) return;
    reselectMutation.mutate(
      {
        id: job.id,
        payload: {
          ...(reselectBrief.trim() ? { clip_brief: reselectBrief.trim() } : {}),
          threshold: reselectThreshold,
          max_clips: Math.min(20, Math.max(1, reselectMaxClips)),
        },
      },
      {
        onSuccess: () => {
          setReselectOpen(false);
          toast({
            title: en.clipGrid.reselectSuccessToast,
            description: `Threshold ${reselectThreshold.toFixed(2)}`,
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

  const handleOpenEditor = (clip: ClipSummary) => {
    onSelectClipId(clip.id);
    if (onOpenEditor) {
      onOpenEditor(clip);
    } else {
      openClipWorkspace(clip.id, "editor");
    }
  };

  const handleOpenPublishPack = (clip: ClipSummary) => {
    onSelectClipId(clip.id);
    if (onOpenPublishPack) {
      onOpenPublishPack(clip);
    } else {
      openClipWorkspace(clip.id, "publish");
    }
  };

  const fewerClips = Boolean(job && sortedClips.length < requestedMaxClips);

  return (
    <section aria-labelledby="clips-heading" className="space-y-3">
      {/* Header & Batch Render Controls */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 id="clips-heading" className="text-base font-bold text-text">
            {en.clipGrid.heading} · {job?.title ?? "Untitled"}
          </h2>
          <p className="text-xs text-text-dim">
            {job?.platform?.toUpperCase() ?? "SOURCE"} ·{" "}
            {job?.language.toUpperCase() ?? "AUTO"} · {sortedClips.length} clips
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {job ? <StatusBadge status={job.status} /> : null}

          {sortedClips.length > 0 ? (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleSelectAll}
              >
                {selectedCount === sortedClips.length
                  ? en.clipGrid.clearSelection
                  : en.clipGrid.selectAll}
              </Button>

              {selectedCount > 0 ? (
                <Button
                  variant="primary"
                  size="sm"
                  loading={renderClipMutation.isPending}
                  onClick={handleBatchRenderSelected}
                  leftIcon={<Play className="h-4 w-4" aria-hidden="true" />}
                >
                  {en.clipGrid.renderSelected} ({selectedCount})
                </Button>
              ) : null}

              <Button
                variant="secondary"
                size="sm"
                loading={renderAllMutation.isPending}
                onClick={handleRenderAll}
                leftIcon={<Sparkles className="h-4 w-4" aria-hidden="true" />}
              >
                {en.clipGrid.renderAll}
              </Button>
            </>
          ) : null}
        </div>
      </div>

      {/* Fewer Clips Explanation Banner & Reselect Panel */}
      {fewerClips ? (
        <div
          role="status"
          className="rounded-md border border-warn/40 bg-warn/10 p-3.5 space-y-3"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-start gap-2.5">
              <AlertCircle
                className="mt-0.5 h-4 w-4 shrink-0 text-warn"
                aria-hidden="true"
              />
              <div>
                <p className="text-xs font-bold text-text">
                  {sortedClips.length === 0
                    ? en.clipGrid.fewerClipsZeroTitle
                    : en.clipGrid.fewerClipsTitle}
                </p>
                <p className="text-xs text-text-dim">
                  {en.clipGrid.fewerClipsBody
                    .replace("{found}", String(sortedClips.length))
                    .replace("{requested}", String(requestedMaxClips))}
                </p>
              </div>
            </div>

            <Button
              variant="secondary"
              size="sm"
              aria-expanded={reselectOpen}
              onClick={() => setReselectOpen((v) => !v)}
              leftIcon={
                <SlidersHorizontal className="h-4 w-4" aria-hidden="true" />
              }
            >
              {en.clipGrid.reselectToggle}
            </Button>
          </div>

          {reselectOpen ? (
            <form
              onSubmit={handleReselectSubmit}
              className="rounded-sm border border-border bg-surface p-3 space-y-3"
            >
              <p className="text-xs font-bold text-text">
                {en.clipGrid.reselectTitle}
              </p>

              <label className="block space-y-1 text-xs">
                <span className="text-text-dim">
                  {en.clipGrid.reselectBriefLabel}
                </span>
                <input
                  type="text"
                  value={reselectBrief}
                  onChange={(e) => setReselectBrief(e.target.value)}
                  placeholder={en.home.briefPlaceholder}
                  className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text placeholder:text-text-dim"
                />
              </label>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="block space-y-1 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="text-text-dim">
                      {en.clipGrid.reselectThresholdLabel}
                    </span>
                    <span className="font-mono font-semibold text-accent">
                      {reselectThreshold.toFixed(2)}
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0.2}
                    max={0.9}
                    step={0.05}
                    value={reselectThreshold}
                    onChange={(e) =>
                      setReselectThreshold(Number(e.target.value))
                    }
                    className="min-h-[44px] w-full accent-[#FFD400]"
                  />
                </label>

                <label className="block space-y-1 text-xs">
                  <span className="text-text-dim">
                    {en.clipGrid.reselectMaxClipsLabel}
                  </span>
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={reselectMaxClips}
                    onChange={(e) =>
                      setReselectMaxClips(Number(e.target.value) || 1)
                    }
                    className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
                  />
                </label>
              </div>

              <div className="flex justify-end gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setReselectOpen(false)}
                >
                  {en.states.confirmCancel}
                </Button>
                <Button
                  type="submit"
                  size="sm"
                  loading={reselectMutation.isPending}
                >
                  {reselectMutation.isPending
                    ? en.clipGrid.reselectingCta
                    : en.clipGrid.reselectSubmit}
                </Button>
              </div>
            </form>
          ) : null}
        </div>
      ) : null}

      {/* Ranked Clip Cards */}
      {sortedClips.length === 0 ? (
        <EmptyState
          title={en.clipGrid.emptyClipsTitle}
          body={en.clipGrid.emptyClipsBody}
          actionLabel={en.clipGrid.reselectToggle}
          onAction={() => setReselectOpen(true)}
        />
      ) : (
        <div className="space-y-3">
          {sortedClips.map((clip, idx) => {
            const rankNum = idx + 1;
            const selected = clip.id === selectedClipId;
            const checked = Boolean(selectedIds[clip.id]);
            const detail = detailById[clip.id];
            const breakdown = detail?.score_breakdown;
            const thumbUrl = thumbById[clip.id];
            const aspect =
              (detail?.clip_plan?.aspect as AspectRatio | undefined) ??
              en.clipGrid.aspectFallbackLabel;
            const scorePct = Math.round(clip.score * 100);
            const isReasonCollapsed = Boolean(collapsedReasons[clip.id]);
            const isViewsOpen = viewsPopoverClipId === clip.id;

            return (
              <Card
                key={clip.id}
                selected={selected}
                data-clip-id={clip.id}
                className="space-y-3"
              >
                <div className="flex flex-col gap-3 sm:flex-row">
                  {/* Selection Checkbox + Thumbnail Preview */}
                  <div className="flex items-start gap-2.5 shrink-0">
                    <label
                      className="inline-flex min-h-[44px] min-w-[44px] cursor-pointer items-center justify-center rounded-sm hover:bg-surface-2"
                      aria-label={`Select ${clip.title ?? clip.id}`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleSelectClip(clip.id)}
                        className="h-4 w-4 accent-[#FFD400]"
                      />
                    </label>

                    <button
                      type="button"
                      onClick={() => onSelectClipId(clip.id)}
                      aria-label={`Preview ${clip.title ?? clip.id}`}
                      className="relative flex h-28 w-20 shrink-0 flex-col justify-between overflow-hidden rounded-sm border border-border bg-bg p-1.5 text-left transition hover:border-accent"
                    >
                      {thumbUrl ? (
                        <img
                          src={thumbUrl}
                          alt={clip.title ?? clip.id}
                          className="absolute inset-0 h-full w-full object-cover"
                        />
                      ) : (
                        <>
                          <div className="flex items-center justify-between text-[10px] text-text-dim">
                            <span className="rounded bg-surface-2 px-1 py-0.5 font-mono">
                              {aspect}
                            </span>
                            <Clapperboard
                              className="h-3 w-3 text-accent"
                              aria-hidden="true"
                            />
                          </div>
                          <p className="line-clamp-3 text-[10px] font-bold leading-tight text-text">
                            {clip.hook_line ?? clip.title ?? "Clip"}
                          </p>
                        </>
                      )}
                      <span className="relative z-10 self-end rounded bg-bg/90 px-1 py-0.5 font-mono text-[10px] font-semibold text-text">
                        {formatDurationSeconds(clip.start_s, clip.end_s)}
                      </span>
                    </button>
                  </div>

                  {/* Clip Metadata + Score Breakdown + Why This Clip */}
                  <div className="min-w-0 flex-1 space-y-2.5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone="accent" size="sm">
                          #{rankNum} · {scorePct}/100
                        </Badge>
                        <span className="font-mono text-xs text-text-dim">
                          {formatMmSs(clip.start_s)} - {formatMmSs(clip.end_s)}
                        </span>
                        {clip.score_mode ? (
                          <Badge tone="neutral" size="sm">
                            {clip.score_mode}
                          </Badge>
                        ) : null}
                      </div>
                      <StatusBadge status={clip.status} />
                    </div>

                    <button
                      type="button"
                      onClick={() => onSelectClipId(clip.id)}
                      className="block min-h-[44px] w-full text-left"
                    >
                      <p className="text-sm font-bold text-text hover:text-accent">
                        {clip.title ?? "Untitled Clip"}
                      </p>
                      {clip.hook_line ? (
                        <p className="mt-0.5 text-xs text-text-dim">
                          “{clip.hook_line}”
                        </p>
                      ) : null}
                    </button>

                    {/* Score Breakdown Bars */}
                    {breakdown && Object.keys(breakdown).length > 0 ? (
                      <div
                        aria-label={`${en.clipGrid.scoreBreakdownLabel} for ${clip.title ?? clip.id}`}
                        className="grid grid-cols-2 gap-x-4 gap-y-1.5 rounded-sm border border-border bg-surface-2/60 p-2.5 sm:grid-cols-3"
                      >
                        {Object.entries(breakdown).map(([metric, rawVal]) => {
                          const num = typeof rawVal === "number" ? rawVal : 0;
                          const pct = Math.max(
                            0,
                            Math.min(100, Math.round(num * 100))
                          );
                          return (
                            <div key={metric} className="space-y-0.5 text-[11px]">
                              <div className="flex items-center justify-between">
                                <span className="capitalize text-text-dim">
                                  {metric}
                                </span>
                                <span className="font-mono font-semibold text-text">
                                  {pct}
                                </span>
                              </div>
                              <div
                                role="progressbar"
                                aria-label={metric}
                                aria-valuenow={pct}
                                aria-valuemin={0}
                                aria-valuemax={100}
                                className="h-1.5 w-full overflow-hidden rounded-full bg-bg"
                              >
                                <div
                                  className="h-full bg-accent"
                                  style={{ width: `${pct}%` }}
                                />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ) : null}

                    {/* Why this clip */}
                    {clip.reason ? (
                      <div className="rounded-sm border border-border/70 bg-surface-2/40 px-2.5 py-2 text-xs">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-semibold text-text">
                            {en.clipGrid.whyThisClip}
                          </span>
                          <button
                            type="button"
                            aria-expanded={!isReasonCollapsed}
                            onClick={() =>
                              setCollapsedReasons((prev) => ({
                                ...prev,
                                [clip.id]: !isReasonCollapsed,
                              }))
                            }
                            className="inline-flex min-h-[44px] items-center gap-1 px-2 text-[11px] font-medium text-text-dim hover:text-text"
                          >
                            <span>
                              {isReasonCollapsed
                                ? en.clipGrid.showReason
                                : en.clipGrid.hideReason}
                            </span>
                            {isReasonCollapsed ? (
                              <ChevronDown
                                className="h-3.5 w-3.5"
                                aria-hidden="true"
                              />
                            ) : (
                              <ChevronUp
                                className="h-3.5 w-3.5"
                                aria-hidden="true"
                              />
                            )}
                          </button>
                        </div>
                        {!isReasonCollapsed ? (
                          <p className="mt-1 text-text-dim leading-relaxed">
                            {clip.reason}
                          </p>
                        ) : null}
                      </div>
                    ) : null}

                    {/* Card Footer: Feedback + Views + Open Editor */}
                    <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Button
                          size="sm"
                          variant={clip.feedback === "up" ? "primary" : "secondary"}
                          aria-label={`${en.clipGrid.thumbsUpLabel} ${clip.title ?? clip.id}`}
                          aria-pressed={clip.feedback === "up"}
                          onClick={() => handleSubmitFeedback(clip, "up")}
                          leftIcon={
                            <ThumbsUp className="h-4 w-4" aria-hidden="true" />
                          }
                        >
                          Up
                        </Button>

                        <Button
                          size="sm"
                          variant={
                            clip.feedback === "down" ? "destructive" : "secondary"
                          }
                          aria-label={`${en.clipGrid.thumbsDownLabel} ${clip.title ?? clip.id}`}
                          aria-pressed={clip.feedback === "down"}
                          onClick={() => handleSubmitFeedback(clip, "down")}
                          leftIcon={
                            <ThumbsDown className="h-4 w-4" aria-hidden="true" />
                          }
                        >
                          Down
                        </Button>

                        <Button
                          size="sm"
                          variant="ghost"
                          aria-expanded={isViewsOpen}
                          onClick={() => handleOpenViewsPopover(clip)}
                          leftIcon={<Eye className="h-4 w-4" aria-hidden="true" />}
                        >
                          {clip.actual_views !== null &&
                          clip.actual_views !== undefined
                            ? `${clip.actual_views.toLocaleString()} ${en.clipGrid.viewsLabel}`
                            : en.clipGrid.logViewsCta}
                        </Button>
                      </div>

                      <div className="flex flex-wrap items-center gap-1.5">
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => handleOpenEditor(clip)}
                        >
                          {en.clipGrid.openEditor}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => handleOpenPublishPack(clip)}
                        >
                          {en.clipGrid.openPublishPack}
                        </Button>
                      </div>
                    </div>

                    {/* Optional actual_views popover */}
                    {isViewsOpen ? (
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          const parsed = viewsInput.trim()
                            ? Math.max(0, Math.floor(Number(viewsInput)))
                            : undefined;
                          handleSubmitFeedback(
                            clip,
                            viewsFeedbackValue,
                            Number.isFinite(parsed) ? parsed : undefined
                          );
                        }}
                        className="flex flex-wrap items-end gap-2 rounded-sm border border-border bg-surface-2 p-2.5"
                      >
                        <label className="space-y-1 text-xs">
                          <span className="text-text-dim">Rating</span>
                          <select
                            value={viewsFeedbackValue}
                            onChange={(e) =>
                              setViewsFeedbackValue(
                                e.target.value as "up" | "down"
                              )
                            }
                            className="min-h-[44px] rounded-sm border border-border bg-surface px-2 text-xs text-text"
                          >
                            <option value="up">Thumbs Up</option>
                            <option value="down">Thumbs Down</option>
                          </select>
                        </label>

                        <label className="flex-1 min-w-[140px] space-y-1 text-xs">
                          <span className="text-text-dim">
                            {en.clipGrid.actualViewsLabel}
                          </span>
                          <input
                            type="number"
                            min={0}
                            value={viewsInput}
                            onChange={(e) => setViewsInput(e.target.value)}
                            placeholder={en.clipGrid.actualViewsPlaceholder}
                            className="min-h-[44px] w-full rounded-sm border border-border bg-surface px-2.5 text-xs text-text"
                          />
                        </label>

                        <Button
                          type="submit"
                          size="sm"
                          loading={feedbackMutation.isPending}
                        >
                          {en.clipGrid.saveFeedbackCta}
                        </Button>
                      </form>
                    ) : null}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </section>
  );
}
