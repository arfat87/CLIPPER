import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ClipboardCopy,
  Download,
  Image as ImageIcon,
  Plus,
  RefreshCw,
  Save,
  X,
} from "lucide-react";
import { apiClient, ApiError } from "../../api/client";
import {
  useClipPublishPackQuery,
  useClipQuery,
  useClipRendersQuery,
  useJobTranscriptQuery,
  useRegenerateClipPublishPackMutation,
  useUpdateClipPublishPackMutation,
} from "../../api/hooks";
import type { PlatformTarget } from "../../api/types";
import { en } from "../../i18n/en";
import { ErrorState, LoadingState } from "../StateViews";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  useToast,
} from "../ui";

function formatSrtTime(sec: number): string {
  const safe = Math.max(0, sec);
  const hrs = Math.floor(safe / 3600);
  const mins = Math.floor((safe % 3600) / 60);
  const secs = Math.floor(safe % 60);
  const ms = Math.floor((safe % 1) * 1000);
  return `${String(hrs).padStart(2, "0")}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")},${String(ms).padStart(3, "0")}`;
}

function formatVttTime(sec: number): string {
  return formatSrtTime(sec).replace(",", ".");
}

function downloadTextFile(filename: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export interface PublishPackViewProps {
  clipId: string;
}

export function PublishPackView({ clipId }: PublishPackViewProps) {
  const { toast } = useToast();

  const clipQuery = useClipQuery(clipId);
  const packQuery = useClipPublishPackQuery(clipId);
  const rendersQuery = useClipRendersQuery(clipId);
  const clip = clipQuery.data;
  const transcriptQuery = useJobTranscriptQuery(clip?.job_id, {
    enabled: Boolean(clip?.job_id),
  });

  const updatePackMutation = useUpdateClipPublishPackMutation();
  const regenerateMutation = useRegenerateClipPublishPackMutation();

  const pack = packQuery.data;

  const [platform, setPlatform] = useState<PlatformTarget>("youtube_shorts");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [hashtags, setHashtags] = useState<string[]>([]);
  const [newHashtag, setNewHashtag] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [newTag, setNewTag] = useState("");
  const [pinnedComment, setPinnedComment] = useState("");
  const [selectedThumbIdx, setSelectedThumbIdx] = useState(1);
  const [checkedSteps, setCheckedSteps] = useState<Record<number, boolean>>({});

  useEffect(() => {
    if (!pack) return;
    setPlatform(pack.platform);
    setTitle(pack.title);
    setDescription(pack.description);
    setHashtags(pack.hashtags ?? []);
    setTags(pack.tags ?? []);
    setPinnedComment(pack.pinned_comment ?? "");
  }, [pack]);

  const clipTranscriptText = useMemo(() => {
    const rawPackTxt = (pack as { transcript_txt?: string } | undefined)
      ?.transcript_txt;
    if (rawPackTxt) return rawPackTxt;
    const segs = transcriptQuery.data?.segments ?? [];
    const start = clip?.start_s ?? 0;
    const end = clip?.end_s ?? 99999;
    const matching = segs.filter((s) => {
      const sEnd = typeof s.end === "number" ? s.end : 0;
      const sStart = typeof s.start === "number" ? s.start : 0;
      return sEnd >= start - 2 && sStart <= end + 2;
    });
    const source = matching.length > 0 ? matching : segs;
    return source.map((s) => String(s.text ?? "")).join("\n");
  }, [pack, transcriptQuery.data?.segments, clip?.start_s, clip?.end_s]);

  const thumbFrames = useMemo(() => {
    const start = clip?.start_s ?? 0;
    const dur = Math.max(10, (clip?.end_s ?? 40) - start);
    return [0, 0.25, 0.5, 0.75].map((pct, idx) => {
      const ts = Math.round(start + dur * pct);
      const mins = Math.floor(ts / 60);
      const secs = ts % 60;
      return {
        idx,
        label: `Frame ${idx + 1}`,
        timeLabel: `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`,
      };
    });
  }, [clip?.start_s, clip?.end_s]);

  if (packQuery.isLoading) return <LoadingState />;
  if (packQuery.isError || !pack) {
    return (
      <ErrorState
        title={en.states.errorTitle}
        message={
          packQuery.error instanceof ApiError
            ? packQuery.error.message
            : en.states.errorBody
        }
        onRetry={() => void packQuery.refetch()}
      />
    );
  }

  const titleLen = title.length;
  const titleTooLong = titleLen > 100;
  const titleWarn70 = titleLen > 70 && titleLen <= 100;
  const titleEmpty = title.trim().length === 0;

  const hashtagCount = hashtags.length;
  const hashtagsOver15 = hashtagCount > 15;
  const hashtagsOver5 = hashtagCount > 5 && hashtagCount <= 15;

  const hasBlockingValidationError =
    titleEmpty || titleTooLong || hashtagsOver15;

  const copyText = async (label: string, text: string) => {
    try {
      await navigator.clipboard?.writeText(text);
    } catch {
      // Clipboard API may be absent in jsdom
    }
    toast({
      title: `${en.publishPack.copiedToast}: ${label}`,
      tone: "ok",
    });
  };

  const handleAddHashtag = () => {
    const cleaned = newHashtag.trim().replace(/\s+/g, "");
    if (!cleaned) return;
    const normalized = cleaned.startsWith("#") ? cleaned : `#${cleaned}`;
    if (!hashtags.includes(normalized)) {
      setHashtags((prev) => [...prev, normalized]);
    }
    setNewHashtag("");
  };

  const handleAddTag = () => {
    const cleaned = newTag.trim();
    if (!cleaned) return;
    if (!tags.includes(cleaned)) {
      setTags((prev) => [...prev, cleaned]);
    }
    setNewTag("");
  };

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    if (hasBlockingValidationError) return;
    updatePackMutation.mutate(
      {
        clipId,
        payload: {
          title: title.trim(),
          description,
          hashtags,
          tags,
          pinned_comment: pinnedComment.trim() || null,
        },
      },
      {
        onSuccess: () => {
          toast({
            title: en.publishPack.savedToast,
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

  const handleRegenerate = () => {
    regenerateMutation.mutate(
      {
        clipId,
        payload: { platform },
      },
      {
        onSuccess: () => {
          toast({
            title: en.publishPack.regeneratedToast,
            description: platform,
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

  const handleDownloadSubtitle = (ext: "srt" | "vtt" | "txt") => {
    if (ext === "txt") {
      downloadTextFile(`${clipId}_transcript.txt`, clipTranscriptText, "text/plain");
      return;
    }
    const segs = transcriptQuery.data?.segments ?? [];
    const lines = segs.map((seg, idx) => {
      const start = typeof seg.start === "number" ? seg.start : idx * 4;
      const end = typeof seg.end === "number" ? seg.end : start + 3.5;
      const txt = String(seg.text ?? "");
      if (ext === "srt") {
        return `${idx + 1}\n${formatSrtTime(start)} --> ${formatSrtTime(end)}\n${txt}\n`;
      }
      return `${formatVttTime(start)} --> ${formatVttTime(end)}\n${txt}\n`;
    });
    const body =
      ext === "vtt" ? `WEBVTT\n\n${lines.join("\n")}` : lines.join("\n");
    downloadTextFile(`${clipId}.${ext}`, body, "text/plain");
  };

  const latestRender = rendersQuery.data?.items?.[0];
  const foldAbove = description.slice(0, 100);
  const foldBelow = description.slice(100);
  const completedChecklistCount = Object.values(checkedSteps).filter(Boolean).length;

  return (
    <div className="space-y-5">
      {/* Header Toolbar */}
      <Card className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <CardTitle>{en.publishPack.heading}</CardTitle>
          <CardDescription>{en.publishPack.subtitle}</CardDescription>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs">
            <span className="sr-only">{en.publishPack.platformLabel}</span>
            <select
              aria-label={en.publishPack.platformLabel}
              value={platform}
              onChange={(e) => setPlatform(e.target.value as PlatformTarget)}
              className="min-h-[44px] rounded-sm border border-border bg-surface-2 px-3 text-xs text-text"
            >
              <option value="youtube_shorts">YouTube Shorts</option>
              <option value="instagram_reels">Instagram Reels</option>
              <option value="tiktok">TikTok</option>
              <option value="facebook_reels">Facebook Reels</option>
            </select>
          </label>

          <Button
            size="sm"
            variant="secondary"
            loading={regenerateMutation.isPending}
            onClick={handleRegenerate}
            leftIcon={<RefreshCw className="h-4 w-4" aria-hidden="true" />}
          >
            {regenerateMutation.isPending
              ? en.publishPack.regeneratingCta
              : en.publishPack.regenerateCta}
          </Button>

          <Button
            size="sm"
            variant="secondary"
            onClick={() =>
              void copyText(
                "All Metadata",
                `${title}\n\n${description}\n\n${hashtags.join(" ")}\n\nPinned: ${pinnedComment}`
              )
            }
            leftIcon={<ClipboardCopy className="h-4 w-4" aria-hidden="true" />}
          >
            {en.publishPack.copyAllCta}
          </Button>
        </div>
      </Card>

      <form onSubmit={handleSave} className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left: Title, Description (100-char fold), Hashtags, Tags, Pinned Comment */}
        <div className="space-y-4 lg:col-span-7">
          {/* Title Selector & Editor */}
          <Card className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-bold text-text">
                {en.publishPack.titleOptionsLabel}
              </span>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void copyText("Title", title)}
                leftIcon={<ClipboardCopy className="h-3.5 w-3.5" aria-hidden="true" />}
              >
                Copy Title
              </Button>
            </div>

            {pack.title_options && pack.title_options.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {pack.title_options.map((opt) => {
                  const active = title === opt;
                  return (
                    <button
                      key={opt}
                      type="button"
                      onClick={() => setTitle(opt)}
                      className={`min-h-[44px] rounded-sm border px-3 py-1.5 text-left text-xs transition ${
                        active
                          ? "border-accent bg-accent/15 text-text font-semibold"
                          : "border-border bg-surface-2 text-text-dim hover:text-text"
                      }`}
                    >
                      {opt}
                    </button>
                  );
                })}
              </div>
            ) : null}

            <label className="block space-y-1 text-xs">
              <div className="flex items-center justify-between">
                <span className="text-text-dim">{en.publishPack.titleLabel}</span>
                <span
                  className={`font-mono font-bold ${
                    titleTooLong || titleEmpty
                      ? "text-err"
                      : titleWarn70
                        ? "text-warn"
                        : "text-text"
                  }`}
                >
                  {titleLen} / 100
                </span>
              </div>
              <input
                type="text"
                aria-label={en.publishPack.titleLabel}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
              />
            </label>

            {titleTooLong || titleEmpty ? (
              <p role="alert" className="flex items-center gap-1.5 text-xs text-err">
                <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span>{en.publishPack.titleErr100}</span>
              </p>
            ) : titleWarn70 ? (
              <p role="status" className="flex items-center gap-1.5 text-xs text-warn">
                <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span>{en.publishPack.titleWarn70}</span>
              </p>
            ) : null}
          </Card>

          {/* Description Editor with 100-char fold marker */}
          <Card className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-bold text-text">
                {en.publishPack.descriptionLabel}
              </span>
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-text-dim">
                  {description.length} / 5000
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void copyText("Description", description)}
                  leftIcon={<ClipboardCopy className="h-3.5 w-3.5" aria-hidden="true" />}
                >
                  Copy Description
                </Button>
              </div>
            </div>

            <textarea
              aria-label={en.publishPack.descriptionLabel}
              rows={4}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full rounded-sm border border-border bg-surface-2 p-3 text-xs text-text leading-relaxed"
            />

            <div className="rounded-sm border border-border bg-bg p-3 space-y-1.5 text-xs">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-text-dim">
                  {en.publishPack.foldMarkerLabel}
                </span>
                <Badge tone={description.length >= 20 ? "ok" : "warn"} size="sm">
                  {Math.min(description.length, 100)} / 100 above fold
                </Badge>
              </div>
              <p className="text-text leading-relaxed">
                <span>{foldAbove || "—"}</span>
                {foldBelow ? (
                  <span className="text-text-dim">
                    {" "}
                    <span className="mx-1 rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-accent">
                      {en.publishPack.foldBelowLabel}
                    </span>{" "}
                    {foldBelow}
                  </span>
                ) : null}
              </p>
            </div>
          </Card>

          {/* Hashtags & SEO Tags */}
          <Card className="space-y-4">
            <div className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <span className="text-xs font-bold text-text">
                    {en.publishPack.hashtagsLabel}
                  </span>
                  <span className="ml-2 text-[11px] text-text-dim">
                    ({en.publishPack.hashtagsHint})
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className={`font-mono text-xs font-bold ${
                      hashtagsOver15
                        ? "text-err"
                        : hashtagsOver5
                          ? "text-warn"
                          : "text-text"
                    }`}
                  >
                    {hashtagCount} / 15
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void copyText("Hashtags", hashtags.join(" "))}
                    leftIcon={<ClipboardCopy className="h-3.5 w-3.5" aria-hidden="true" />}
                  >
                    Copy Hashtags
                  </Button>
                </div>
              </div>

              <div className="flex flex-wrap gap-1.5">
                {hashtags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-flex min-h-[44px] items-center gap-1 rounded-sm border border-border bg-surface-2 pl-2.5 pr-1 text-xs text-text"
                  >
                    <span>{tag}</span>
                    <button
                      type="button"
                      aria-label={`Remove ${tag}`}
                      onClick={() =>
                        setHashtags((prev) => prev.filter((t) => t !== tag))
                      }
                      className="inline-flex h-8 w-8 items-center justify-center rounded-sm text-text-dim hover:text-err"
                    >
                      <X className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </span>
                ))}
              </div>

              <div className="flex gap-2">
                <input
                  type="text"
                  aria-label={en.publishPack.hashtagPlaceholder}
                  value={newHashtag}
                  onChange={(e) => setNewHashtag(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddHashtag();
                    }
                  }}
                  placeholder={en.publishPack.hashtagPlaceholder}
                  className="min-h-[44px] flex-1 rounded-sm border border-border bg-surface-2 px-3 text-xs text-text"
                />
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={handleAddHashtag}
                  leftIcon={<Plus className="h-4 w-4" aria-hidden="true" />}
                >
                  {en.publishPack.addHashtagCta}
                </Button>
              </div>

              {hashtagsOver15 ? (
                <p role="alert" className="text-xs text-err">
                  {en.publishPack.hashtagsErrMax15}
                </p>
              ) : hashtagsOver5 ? (
                <p role="status" className="text-xs text-warn">
                  {en.publishPack.hashtagsWarnOver5}
                </p>
              ) : null}
            </div>

            {/* SEO Tags */}
            <div className="space-y-2 border-t border-border pt-3">
              <span className="text-xs font-bold text-text">
                {en.publishPack.tagsLabel}
              </span>
              <div className="flex flex-wrap gap-1.5">
                {tags.map((t) => (
                  <span
                    key={t}
                    className="inline-flex min-h-[36px] items-center gap-1 rounded-sm border border-border bg-bg pl-2.5 pr-1 text-[11px] text-text-dim"
                  >
                    <span>{t}</span>
                    <button
                      type="button"
                      aria-label={`Remove tag ${t}`}
                      onClick={() =>
                        setTags((prev) => prev.filter((item) => item !== t))
                      }
                      className="inline-flex h-7 w-7 items-center justify-center rounded-sm hover:text-err"
                    >
                      <X className="h-3 w-3" aria-hidden="true" />
                    </button>
                  </span>
                ))}
              </div>
              <div className="flex gap-2">
                <input
                  type="text"
                  aria-label={en.publishPack.tagPlaceholder}
                  value={newTag}
                  onChange={(e) => setNewTag(e.target.value)}
                  placeholder={en.publishPack.tagPlaceholder}
                  className="min-h-[44px] flex-1 rounded-sm border border-border bg-surface-2 px-3 text-xs text-text"
                />
                <Button size="sm" variant="secondary" onClick={handleAddTag}>
                  Add Tag
                </Button>
              </div>
            </div>
          </Card>

          {/* Pinned Comment & Save */}
          <Card className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-bold text-text">
                {en.publishPack.pinnedCommentLabel}
              </span>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void copyText("Pinned Comment", pinnedComment)}
                leftIcon={<ClipboardCopy className="h-3.5 w-3.5" aria-hidden="true" />}
              >
                Copy Pinned Comment
              </Button>
            </div>
            <textarea
              aria-label={en.publishPack.pinnedCommentLabel}
              rows={2}
              value={pinnedComment}
              onChange={(e) => setPinnedComment(e.target.value)}
              className="w-full rounded-sm border border-border bg-surface-2 p-2.5 text-xs text-text"
            />
            <div className="flex justify-end">
              <Button
                type="submit"
                disabled={hasBlockingValidationError}
                loading={updatePackMutation.isPending}
                leftIcon={<Save className="h-4 w-4" aria-hidden="true" />}
              >
                {updatePackMutation.isPending
                  ? en.publishPack.savingCta
                  : en.publishPack.saveCta}
              </Button>
            </div>
          </Card>
        </div>

        {/* Right: Thumbnail Picker, Downloads, Transcript, Manual Upload Checklist */}
        <div className="space-y-4 lg:col-span-5">
          {/* Thumbnail Frame Picker */}
          <Card className="space-y-3">
            <CardHeader>
              <div className="flex items-center gap-2">
                <ImageIcon className="h-4 w-4 text-accent" aria-hidden="true" />
                <CardTitle>{en.publishPack.thumbnailPickerTitle}</CardTitle>
              </div>
            </CardHeader>
            <div className="grid grid-cols-2 gap-2.5">
              {thumbFrames.map((frame) => {
                const selected = selectedThumbIdx === frame.idx;
                return (
                  <button
                    key={frame.idx}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => setSelectedThumbIdx(frame.idx)}
                    className={`flex min-h-[84px] flex-col justify-between rounded-sm border p-2.5 text-left transition ${
                      selected
                        ? "border-accent bg-accent/10"
                        : "border-border bg-surface-2 hover:border-text-dim"
                    }`}
                  >
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="font-semibold text-text">
                        {frame.label}
                      </span>
                      <span className="font-mono text-text-dim">
                        {frame.timeLabel}
                      </span>
                    </div>
                    <p className="line-clamp-2 text-[11px] font-bold text-accent">
                      {pack.hook_line ?? title}
                    </p>
                  </button>
                );
              })}
            </div>
          </Card>

          {/* Media & Subtitle Downloads */}
          <Card className="space-y-3">
            <CardTitle>{en.publishPack.downloadsTitle}</CardTitle>
            <CardContent className="grid grid-cols-2 gap-2 mt-0">
              {latestRender?.file_url ? (
                <a
                  href={latestRender.file_url}
                  download={`${clipId}.mp4`}
                  className="inline-flex min-h-[44px] items-center justify-center gap-1.5 rounded-sm border border-accent bg-accent text-accent-fg px-3 text-xs font-semibold"
                >
                  <Download className="h-4 w-4" aria-hidden="true" />
                  <span>{en.publishPack.downloadMp4}</span>
                </a>
              ) : (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    toast({
                      title: "Render clip first in Clip Editor to download MP4.",
                      tone: "warn",
                    })
                  }
                  leftIcon={<Download className="h-4 w-4" aria-hidden="true" />}
                >
                  {en.publishPack.downloadMp4}
                </Button>
              )}

              <Button
                size="sm"
                variant="secondary"
                onClick={() => handleDownloadSubtitle("srt")}
                leftIcon={<Download className="h-4 w-4" aria-hidden="true" />}
              >
                {en.publishPack.downloadSrt}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => handleDownloadSubtitle("vtt")}
                leftIcon={<Download className="h-4 w-4" aria-hidden="true" />}
              >
                {en.publishPack.downloadVtt}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => handleDownloadSubtitle("txt")}
                leftIcon={<Download className="h-4 w-4" aria-hidden="true" />}
              >
                {en.publishPack.downloadTxt}
              </Button>

              {clip?.job_id ? (
                <a
                  href={apiClient.downloadJobZipUrl(clip.job_id)}
                  download={`${clip.job_id}.zip`}
                  className="col-span-2 inline-flex min-h-[44px] items-center justify-center gap-1.5 rounded-sm border border-border bg-surface-2 px-3 text-xs font-semibold text-text hover:border-accent"
                >
                  <Download className="h-4 w-4" aria-hidden="true" />
                  <span>{en.publishPack.downloadZip}</span>
                </a>
              ) : null}
            </CardContent>
          </Card>

          {/* Full Clip Transcript */}
          <Card className="space-y-2.5">
            <div className="flex items-center justify-between gap-2">
              <CardTitle>{en.publishPack.transcriptTitle}</CardTitle>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void copyText("Transcript", clipTranscriptText)}
                leftIcon={<ClipboardCopy className="h-3.5 w-3.5" aria-hidden="true" />}
              >
                Copy Transcript
              </Button>
            </div>
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-sm border border-border bg-bg p-3 font-sans text-xs text-text-dim leading-relaxed">
              {clipTranscriptText || "Transcript pending..."}
            </pre>
          </Card>

          {/* Manual Upload Checklist */}
          <Card className="space-y-3">
            <CardHeader>
              <CardTitle>{en.publishPack.checklistTitle}</CardTitle>
              <Badge
                tone={
                  completedChecklistCount === en.publishPack.checklistItems.length
                    ? "ok"
                    : "neutral"
                }
              >
                <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                <span>
                  {completedChecklistCount}/{en.publishPack.checklistItems.length}
                </span>
              </Badge>
            </CardHeader>
            <div className="space-y-2">
              {en.publishPack.checklistItems.map((item, idx) => (
                <label
                  key={item}
                  className="flex min-h-[44px] cursor-pointer items-center gap-2.5 rounded-sm border border-border bg-surface-2 px-3 py-2 text-xs text-text"
                >
                  <input
                    type="checkbox"
                    checked={Boolean(checkedSteps[idx])}
                    onChange={(e) =>
                      setCheckedSteps((prev) => ({
                        ...prev,
                        [idx]: e.target.checked,
                      }))
                    }
                    className="h-4 w-4 accent-[#FFD400]"
                  />
                  <span>{item}</span>
                </label>
              ))}
            </div>
          </Card>
        </div>
      </form>
    </div>
  );
}

export default PublishPackView;
