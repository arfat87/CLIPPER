import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Download,
  FileText,
  Loader2,
  RotateCcw,
  Settings,
  Trash2,
  Wifi,
  XCircle,
} from "lucide-react";
import { apiClient } from "../../api/client";
import {
  isActiveJobStatus,
  useCancelJobMutation,
  useJobEventsStream,
  useRetryJobMutation,
} from "../../api/hooks";
import type { JobDetail, StageCheckpoint, StageName } from "../../api/types";
import { en } from "../../i18n/en";
import { navigateToTab } from "../../router";
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

const ORDERED_STAGES: StageName[] = [
  "fetch",
  "prepare",
  "transcribe",
  "analyze",
  "select",
  "render",
  "package",
];

function getErrorHint(errorCode?: string | null): string {
  if (!errorCode) return en.timeline.errorHints.DEFAULT;
  const map = en.timeline.errorHints as Record<string, string>;
  return map[errorCode] ?? en.timeline.errorHints.DEFAULT;
}

function StageIcon({ status }: { status: StageCheckpoint["status"] }) {
  if (status === "DONE") {
    return <CheckCircle2 className="h-4 w-4 text-ok shrink-0" aria-hidden="true" />;
  }
  if (status === "RUNNING") {
    return (
      <Loader2 className="h-4 w-4 text-accent animate-spin shrink-0" aria-hidden="true" />
    );
  }
  if (status === "FAILED") {
    return <XCircle className="h-4 w-4 text-err shrink-0" aria-hidden="true" />;
  }
  return <Circle className="h-4 w-4 text-text-dim shrink-0" aria-hidden="true" />;
}

export function JobTimelineView({
  job,
  onRequestDelete,
}: {
  job: JobDetail;
  onRequestDelete: (job: JobDetail) => void;
}) {
  const { toast } = useToast();
  const isActive = isActiveJobStatus(job.status);

  const stream = useJobEventsStream(job.id, {
    enabled: isActive,
  });

  const cancelMutation = useCancelJobMutation();
  const retryMutation = useRetryJobMutation();

  const failedStage =
    job.stages?.find((s) => s.status === "FAILED")?.stage ??
    ((ORDERED_STAGES.includes(job.stage as StageName)
      ? job.stage
      : "fetch") as StageName);

  const [retryStage, setRetryStage] = useState<StageName>(failedStage);

  const mergedWarnings = Array.from(
    new Set([...(job.warnings ?? []), ...stream.liveWarnings])
  );

  const handleCancel = () => {
    cancelMutation.mutate(job.id, {
      onSuccess: () => {
        toast({ title: en.timeline.cancelledToast, tone: "warn" });
      },
      onError: (err) => {
        toast({
          title: en.states.errorTitle,
          description: err.message,
          tone: "err",
        });
      },
    });
  };

  const handleRetry = (stageOverride?: StageName) => {
    const targetStage = stageOverride ?? retryStage;
    retryMutation.mutate(
      { id: job.id, payload: { from_stage: targetStage } },
      {
        onSuccess: () => {
          toast({
            title: en.timeline.retriedToast,
            description: `${en.timeline.retryStageLabel}: ${targetStage}`,
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

  const handleDownloadLogs = () => {
    const logContent = [
      `CLIPPER Job Log: ${job.id}`,
      `Title: ${job.title ?? "Untitled"}`,
      `Status: ${job.status}`,
      `Platform: ${job.platform ?? job.source_kind}`,
      `Created: ${job.created_at}`,
      "",
      "Stages:",
      ...ORDERED_STAGES.map((stageName) => {
        const st = job.stages?.find((s) => s.stage === stageName);
        return `- ${stageName}: ${st?.status ?? "PENDING"} (attempt ${st?.attempt ?? 1})`;
      }),
      ...(mergedWarnings.length > 0
        ? ["", "Warnings:", ...mergedWarnings.map((w) => `- ${w}`)]
        : []),
      ...(job.error_message
        ? ["", `Error (${job.error_code ?? "UNKNOWN"}): ${job.error_message}`]
        : []),
    ].join("\n");

    const blob = new Blob([logContent], { type: "text/plain;charset=utf-8" });
    if (typeof URL !== "undefined" && typeof URL.createObjectURL === "function") {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${job.id}_logs.txt`;
      a.click();
      URL.revokeObjectURL(url);
    }
    toast({ title: en.timeline.downloadLogsCta, description: `${job.id}_logs.txt`, tone: "info" });
  };

  const needsSettingsRecovery =
    job.error_code === "LOGIN_REQUIRED" ||
    job.error_code === "LLM_UNAVAILABLE" ||
    job.error_code === "DRIVE_AUTH";

  return (
    <Card aria-label={`${en.timeline.title} ${job.title ?? job.id}`} className="space-y-4">
      <CardHeader>
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="truncate">{job.title ?? job.id}</CardTitle>
            <Badge tone="route" size="sm">
              {job.platform ?? job.source_kind}
            </Badge>
            {stream.connected ? (
              <Badge tone="accent" size="sm">
                <Wifi className="h-3 w-3" aria-hidden="true" />
                <span>{en.timeline.liveBadge}</span>
              </Badge>
            ) : null}
            {stream.reconnecting ? (
              <Badge tone="warn" size="sm">
                {en.timeline.reconnectingBadge}
              </Badge>
            ) : null}
          </div>
          <CardDescription>
            {job.id} · {job.language.toUpperCase()}
            {isActive && stream.etaSeconds !== null
              ? ` · ${en.timeline.etaPrefix} ~${stream.etaSeconds}s`
              : ""}
            {stream.liveMessage ? ` · ${stream.liveMessage}` : ""}
          </CardDescription>
        </div>

        <StatusBadge status={job.status} />
      </CardHeader>

      {/* Warnings Banner */}
      {mergedWarnings.length > 0 ? (
        <div
          role="status"
          className="rounded-sm border border-warn/40 bg-warn/10 px-3 py-2.5 text-xs text-text"
        >
          <p className="flex items-center gap-1.5 font-semibold text-warn">
            <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
            <span>{en.timeline.warningsTitle}</span>
          </p>
          <ul className="mt-1 list-disc pl-5 text-text-dim space-y-0.5">
            {mergedWarnings.map((warn) => (
              <li key={warn}>{warn}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* Plain-language Error Card + Recovery CTA */}
      {job.status === "FAILED" ? (
        <div
          role="alert"
          className="rounded-md border border-err/50 bg-err/10 p-4 space-y-3"
        >
          <div className="flex items-start gap-2.5">
            <XCircle className="h-5 w-5 shrink-0 text-err mt-0.5" aria-hidden="true" />
            <div className="space-y-1">
              <p className="text-sm font-bold text-text">
                {en.timeline.errorCardTitle}
                {job.error_code ? ` (${job.error_code})` : ""}
              </p>
              <p className="text-xs text-text">
                {getErrorHint(job.error_code)}
              </p>
              {job.error_message ? (
                <p className="text-xs text-text-dim">{job.error_message}</p>
              ) : null}
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {needsSettingsRecovery ? (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => navigateToTab("settings")}
                leftIcon={<Settings className="h-4 w-4" aria-hidden="true" />}
              >
                {en.timeline.recoveryActionSettings}
              </Button>
            ) : null}
            <Button
              variant="primary"
              size="sm"
              loading={retryMutation.isPending}
              onClick={() => handleRetry(failedStage)}
              leftIcon={<RotateCcw className="h-4 w-4" aria-hidden="true" />}
            >
              {en.timeline.recoveryActionRetry}
            </Button>
          </div>
        </div>
      ) : null}

      {/* Vertical 7-Stage Progress Timeline */}
      <ol aria-label="Pipeline stages" className="space-y-2.5">
        {ORDERED_STAGES.map((stageName) => {
          const checkpoint = job.stages?.find((s) => s.stage === stageName);
          const status: StageCheckpoint["status"] =
            checkpoint?.status ?? "PENDING";
          const live = stream.stageProgress[stageName];
          const metricPct =
            typeof checkpoint?.metrics?.pct === "number"
              ? checkpoint.metrics.pct
              : undefined;
          const pct =
            live?.pct ??
            metricPct ??
            (status === "DONE" ? 100 : status === "RUNNING" ? 45 : 0);

          return (
            <li
              key={stageName}
              data-stage={stageName}
              data-status={status}
              className="rounded-sm border border-border bg-surface-2/70 px-3 py-2.5"
            >
              <div className="flex items-center justify-between gap-2 text-xs">
                <div className="flex items-center gap-2 min-w-0">
                  <StageIcon status={status} />
                  <span className="font-semibold text-text">
                    {en.timeline.stages[stageName]}
                  </span>
                  {checkpoint?.attempt && checkpoint.attempt > 1 ? (
                    <Badge tone="neutral" size="sm">
                      Attempt {checkpoint.attempt}
                    </Badge>
                  ) : null}
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {status === "RUNNING" && live?.eta_s !== undefined && live.eta_s !== null ? (
                    <span className="text-[11px] text-text-dim">
                      ~{live.eta_s}s
                    </span>
                  ) : null}
                  <span className="font-mono text-[11px] text-text-dim">
                    {pct}%
                  </span>
                  <StatusBadge status={status} />
                </div>
              </div>

              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-bg">
                <div
                  className={`h-full transition-[width] duration-150 ease-out ${
                    status === "FAILED"
                      ? "bg-err"
                      : status === "DONE"
                        ? "bg-ok"
                        : "bg-accent"
                  }`}
                  style={{ width: `${pct}%` }}
                />
              </div>

              {live?.message && status === "RUNNING" ? (
                <p className="mt-1.5 text-[11px] text-text-dim">{live.message}</p>
              ) : null}
            </li>
          );
        })}
      </ol>

      {/* Action Controls */}
      <CardContent className="mt-0 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
        <div className="flex flex-wrap items-center gap-2">
          {isActive ? (
            <Button
              variant="destructive"
              size="sm"
              loading={cancelMutation.isPending}
              onClick={handleCancel}
            >
              {cancelMutation.isPending
                ? en.timeline.cancellingCta
                : en.timeline.cancelCta}
            </Button>
          ) : null}

          <div className="flex items-center gap-1.5">
            <label className="sr-only" htmlFor={`retry-stage-${job.id}`}>
              {en.timeline.retryStageLabel}
            </label>
            <select
              id={`retry-stage-${job.id}`}
              aria-label={en.timeline.retryStageLabel}
              value={retryStage}
              onChange={(e) => setRetryStage(e.target.value as StageName)}
              className="min-h-[44px] rounded-sm border border-border bg-surface-2 px-2.5 text-xs text-text"
            >
              {ORDERED_STAGES.map((st) => (
                <option key={st} value={st}>
                  {en.timeline.stages[st]}
                </option>
              ))}
            </select>
            <Button
              variant="secondary"
              size="sm"
              loading={retryMutation.isPending}
              onClick={() => handleRetry()}
              leftIcon={<RotateCcw className="h-4 w-4" aria-hidden="true" />}
            >
              {retryMutation.isPending
                ? en.timeline.retryingCta
                : en.timeline.retryCta}
            </Button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleDownloadLogs}
            leftIcon={<FileText className="h-4 w-4" aria-hidden="true" />}
          >
            {en.timeline.downloadLogsCta}
          </Button>

          {job.status === "COMPLETED" ? (
            <a
              href={apiClient.downloadJobZipUrl(job.id)}
              download={`${job.id}.zip`}
              className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center gap-1.5 rounded-sm border border-border bg-surface-2 px-3 py-2 text-xs font-semibold text-text transition-colors hover:border-accent"
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              <span>{en.timeline.downloadZipCta}</span>
            </a>
          ) : null}

          <Button
            variant="destructive"
            size="sm"
            aria-label={`${en.jobs.deleteJob} ${job.title ?? job.id}`}
            onClick={() => onRequestDelete(job)}
            leftIcon={<Trash2 className="h-4 w-4" aria-hidden="true" />}
          >
            {en.timeline.deleteCta}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
