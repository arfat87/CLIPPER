import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  FolderOpen,
  HardDrive,
  RefreshCw,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { ApiError } from "../../api/client";
import {
  useAdminAuditQuery,
  useAdminJobsQuery,
  useDeleteJobMutation,
  useHealthQuery,
  useRetryJobMutation,
} from "../../api/hooks";
import type { JobDetail, StageName } from "../../api/types";
import { en } from "../../i18n/en";
import { EmptyState, ErrorState, LoadingState } from "../StateViews";
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
} from "../ui";

function estimateJobDiskMb(job: JobDetail): number {
  const durationMin = Math.max(1, Math.round((job.duration_s ?? 300) / 60));
  const clipCount = job.clips?.length ?? 0;
  return durationMin * 14 + clipCount * 28;
}

function buildArtifactRows(job: JobDetail): Array<{ path: string; kind: string; sizeMb: string }> {
  const rows: Array<{ path: string; kind: string; sizeMb: string }> = [
    {
      path: `data/jobs/${job.id}/source.mp4`,
      kind: "source",
      sizeMb: `${Math.max(24, Math.round((job.duration_s ?? 300) * 0.22))} MB`,
    },
    {
      path: `data/jobs/${job.id}/audio_16k.wav`,
      kind: "audio",
      sizeMb: `${Math.max(4, Math.round((job.duration_s ?? 300) * 0.03))} MB`,
    },
    {
      path: `data/jobs/${job.id}/transcript.json`,
      kind: "transcript",
      sizeMb: "0.4 MB",
    },
  ];
  for (const clip of job.clips ?? []) {
    rows.push({
      path: `data/jobs/${job.id}/clips/${clip.id}.ass`,
      kind: "subtitle",
      sizeMb: "0.1 MB",
    });
    rows.push({
      path: `data/jobs/${job.id}/renders/${clip.id}_9x16.mp4`,
      kind: "render",
      sizeMb: "24.5 MB",
    });
    rows.push({
      path: `data/jobs/${job.id}/publish/${clip.id}_pack.json`,
      kind: "publish_pack",
      sizeMb: "0.1 MB",
    });
  }
  return rows;
}

export function AdminJobsView() {
  const { toast } = useToast();
  const jobsQuery = useAdminJobsQuery();
  const retryMutation = useRetryJobMutation();
  const deleteMutation = useDeleteJobMutation();

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [expandedJobId, setExpandedJobId] = useState<string | null>(null);
  const [jobPendingDelete, setJobPendingDelete] = useState<JobDetail | null>(
    null
  );

  const items = jobsQuery.data?.items ?? [];

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((job) => {
      if (statusFilter !== "all" && job.status !== statusFilter) {
        return false;
      }
      if (!q) return true;
      return (
        job.id.toLowerCase().includes(q) ||
        (job.title ?? "").toLowerCase().includes(q)
      );
    });
  }, [items, search, statusFilter]);

  const totalDiskMb = items.reduce((acc, j) => acc + estimateJobDiskMb(j), 0);

  if (jobsQuery.isLoading) return <LoadingState />;
  if (jobsQuery.isError) {
    return (
      <ErrorState
        title={en.admin.errorTitle}
        message={
          jobsQuery.error instanceof ApiError
            ? jobsQuery.error.message
            : en.states.errorBody
        }
        onRetry={() => void jobsQuery.refetch()}
      />
    );
  }

  const handleRetry = (job: JobDetail) => {
    const failedStage =
      job.stages?.find((s) => s.status === "FAILED")?.stage ??
      ((job.stage as StageName | undefined) || "fetch");
    retryMutation.mutate(
      { id: job.id, payload: { from_stage: failedStage } },
      {
        onSuccess: () => {
          toast({
            title: en.admin.jobsUi.retriedToast,
            description: `${job.id} (${failedStage})`,
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

  const handleConfirmDelete = () => {
    if (!jobPendingDelete) return;
    const target = jobPendingDelete;
    deleteMutation.mutate(target.id, {
      onSuccess: () => {
        setJobPendingDelete(null);
        toast({
          title: en.jobs.deletedToast,
          description: target.title ?? target.id,
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

  return (
    <div className="space-y-4">
      <Card className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <HardDrive className="h-4 w-4 text-accent" aria-hidden="true" />
          <span className="text-xs text-text-dim">
            {en.admin.jobsUi.diskSummaryLabel}:
          </span>
          <Badge tone="neutral">{totalDiskMb} MB</Badge>
        </div>

        <div className="flex flex-wrap gap-2">
          <input
            type="search"
            aria-label={en.admin.jobsUi.searchPlaceholder}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={en.admin.jobsUi.searchPlaceholder}
            className="min-h-[44px] rounded-sm border border-border bg-surface-2 px-3 text-xs text-text placeholder:text-text-dim"
          />
          <select
            aria-label={en.admin.jobsUi.statusFilterLabel}
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="min-h-[44px] rounded-sm border border-border bg-surface-2 px-3 text-xs text-text"
          >
            <option value="all">All Statuses</option>
            <option value="COMPLETED">COMPLETED</option>
            <option value="FAILED">FAILED</option>
            <option value="QUEUED">QUEUED</option>
            <option value="RENDERING">RENDERING</option>
            <option value="CANCELLED">CANCELLED</option>
          </select>
        </div>
      </Card>

      {filtered.length === 0 ? (
        <EmptyState title={en.jobs.emptyTitle} body={en.jobs.emptyBody} />
      ) : (
        <div className="space-y-3">
          {filtered.map((job) => {
            const diskMb = estimateJobDiskMb(job);
            const isExpanded = expandedJobId === job.id;
            const artifacts = buildArtifactRows(job);

            return (
              <Card key={job.id} className="space-y-3">
                <CardHeader>
                  <div>
                    <CardTitle>{job.title ?? job.id}</CardTitle>
                    <CardDescription className="font-mono">
                      {job.id} · {job.source_kind} · {job.language} · {diskMb} MB
                    </CardDescription>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge status={job.status} />
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-expanded={isExpanded}
                      onClick={() =>
                        setExpandedJobId(isExpanded ? null : job.id)
                      }
                      leftIcon={
                        <FolderOpen className="h-4 w-4" aria-hidden="true" />
                      }
                      rightIcon={
                        isExpanded ? (
                          <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
                        ) : (
                          <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
                        )
                      }
                    >
                      {isExpanded
                        ? en.admin.jobsUi.hideArtifactsCta
                        : en.admin.jobsUi.artifactsCta}
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      loading={retryMutation.isPending}
                      onClick={() => handleRetry(job)}
                      leftIcon={
                        <RotateCcw className="h-4 w-4" aria-hidden="true" />
                      }
                    >
                      {en.admin.jobsUi.retryCta}
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      aria-label={`${en.admin.jobsUi.deleteCta} ${job.title ?? job.id}`}
                      onClick={() => setJobPendingDelete(job)}
                      leftIcon={<Trash2 className="h-4 w-4" aria-hidden="true" />}
                    >
                      {en.admin.jobsUi.deleteCta}
                    </Button>
                  </div>
                </CardHeader>

                {isExpanded ? (
                  <CardContent className="rounded-sm border border-border bg-bg p-3 space-y-1.5">
                    {artifacts.map((art) => (
                      <div
                        key={art.path}
                        className="flex items-center justify-between gap-2 text-xs"
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <Badge tone="neutral" size="sm">
                            {art.kind}
                          </Badge>
                          <span className="truncate font-mono text-text-dim">
                            {art.path}
                          </span>
                        </div>
                        <span className="font-mono text-text shrink-0">
                          {art.sizeMb}
                        </span>
                      </div>
                    ))}
                  </CardContent>
                ) : null}
              </Card>
            );
          })}
        </div>
      )}

      <ConfirmModal
        open={Boolean(jobPendingDelete)}
        title={en.jobs.deleteConfirmTitle}
        description={en.jobs.deleteConfirmBody}
        confirmLabel={en.jobs.deleteConfirmCta}
        variant="destructive"
        loading={deleteMutation.isPending}
        onConfirm={handleConfirmDelete}
        onCancel={() => setJobPendingDelete(null)}
      />
    </div>
  );
}

export function AdminHealthView() {
  const healthQuery = useHealthQuery();

  if (healthQuery.isLoading) return <LoadingState />;
  if (healthQuery.isError) {
    return (
      <ErrorState
        title={en.admin.errorTitle}
        message={
          healthQuery.error instanceof ApiError
            ? healthQuery.error.message
            : en.states.errorBody
        }
        onRetry={() => void healthQuery.refetch()}
      />
    );
  }

  const h = healthQuery.data;
  const asrModels = h?.models?.asr ?? [];

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button
          size="sm"
          variant="secondary"
          loading={healthQuery.isFetching}
          onClick={() => void healthQuery.refetch()}
          leftIcon={<RefreshCw className="h-4 w-4" aria-hidden="true" />}
        >
          {en.admin.healthUi.refreshCta}
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Card className="space-y-2">
          <CardHeader>
            <CardTitle>{en.admin.healthUi.ffmpegTitle}</CardTitle>
            <Badge tone={h?.ffmpeg?.libass ? "ok" : "err"}>
              {h?.ffmpeg?.libass ? "READY" : "CHECK"}
            </Badge>
          </CardHeader>
          <CardContent className="space-y-1.5 text-xs">
            <div className="flex justify-between">
              <span className="text-text-dim">FFmpeg Version</span>
              <span className="font-mono">{h?.ffmpeg?.version ?? "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-dim">libass</span>
              <Badge tone={h?.ffmpeg?.libass ? "ok" : "err"} size="sm">
                {h?.ffmpeg?.libass ? "Enabled" : "Missing"}
              </Badge>
            </div>
            <div className="flex justify-between">
              <span className="text-text-dim">HarfBuzz (Indic shaping)</span>
              <Badge tone={h?.ffmpeg?.harfbuzz ? "ok" : "err"} size="sm">
                {h?.ffmpeg?.harfbuzz ? "Enabled" : "Missing"}
              </Badge>
            </div>
            <div className="flex justify-between">
              <span className="text-text-dim">FriBidi</span>
              <Badge tone={h?.ffmpeg?.fribidi ? "ok" : "err"} size="sm">
                {h?.ffmpeg?.fribidi ? "Enabled" : "Missing"}
              </Badge>
            </div>
          </CardContent>
        </Card>

        <Card className="space-y-2">
          <CardHeader>
            <CardTitle>{en.admin.healthUi.gpuTitle}</CardTitle>
            <Badge tone={h?.gpu?.cuda ? "ok" : "neutral"}>
              {h?.gpu?.cuda ? "CUDA" : "CPU"}
            </Badge>
          </CardHeader>
          <CardContent className="space-y-1.5 text-xs">
            <div className="flex justify-between">
              <span className="text-text-dim">Device</span>
              <span>{h?.gpu?.name ?? "CPU mode"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-dim">VRAM</span>
              <span className="font-mono">
                {h?.gpu?.vram_gb ? `${h.gpu.vram_gb} GB` : "—"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-dim">CUDA Acceleration</span>
              <span>{h?.gpu?.cuda ? "Available" : "Disabled"}</span>
            </div>
          </CardContent>
        </Card>

        <Card className="space-y-2">
          <CardHeader>
            <CardTitle>{en.admin.healthUi.modelsTitle}</CardTitle>
            <Badge tone={h?.models?.llm?.ok ? "ok" : "warn"}>
              {h?.models?.llm?.provider ?? "ollama"}
            </Badge>
          </CardHeader>
          <CardContent className="space-y-2 text-xs">
            <div>
              <p className="text-text-dim mb-1">Cached ASR Models</p>
              <div className="flex flex-wrap gap-1.5">
                {asrModels.length > 0 ? (
                  asrModels.map((m) => (
                    <Badge key={m} tone="neutral" size="sm">
                      {m}
                    </Badge>
                  ))
                ) : (
                  <span className="text-text-dim">None cached yet</span>
                )}
              </div>
            </div>
            <div className="flex justify-between">
              <span className="text-text-dim">LLM Reachable</span>
              <Badge tone={h?.models?.llm?.ok ? "ok" : "err"} size="sm">
                {h?.models?.llm?.ok ? "OK" : "UNREACHABLE"}
              </Badge>
            </div>
          </CardContent>
        </Card>

        <Card className="space-y-2">
          <CardHeader>
            <CardTitle>{en.admin.healthUi.systemTitle}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5 text-xs">
            <div className="flex justify-between">
              <span className="text-text-dim">Disk Free</span>
              <span className="font-mono">{h?.disk?.free_gb ?? "—"} GB</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-dim">yt-dlp Version</span>
              <span className="font-mono">{h?.ytdlp?.version ?? "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-dim">Queue Depth</span>
              <span className="font-mono">{h?.queue?.depth ?? 0}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-dim">Drive Status</span>
              <StatusBadge status={h?.drive?.status ?? "DISABLED"} />
            </div>
            <div className="flex justify-between">
              <span className="text-text-dim">Templates Valid / Invalid</span>
              <span className="font-mono">
                {h?.templates?.valid ?? 0} / {h?.templates?.invalid ?? 0}
              </span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export function AdminAuditView() {
  const auditQuery = useAdminAuditQuery(50);
  const [search, setSearch] = useState("");
  const [actionFilter, setActionFilter] = useState("all");

  const items = auditQuery.data?.items ?? [];

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((entry) => {
      const action = String(entry.action ?? "");
      if (actionFilter !== "all" && !action.startsWith(actionFilter)) {
        return false;
      }
      if (!q) return true;
      return (
        action.toLowerCase().includes(q) ||
        String(entry.actor ?? "").toLowerCase().includes(q) ||
        String(entry.target ?? "").toLowerCase().includes(q) ||
        String(entry.diff ?? "").toLowerCase().includes(q)
      );
    });
  }, [items, search, actionFilter]);

  if (auditQuery.isLoading) return <LoadingState />;
  if (auditQuery.isError) {
    return (
      <ErrorState
        title={en.admin.errorTitle}
        message={
          auditQuery.error instanceof ApiError
            ? auditQuery.error.message
            : en.states.errorBody
        }
        onRetry={() => void auditQuery.refetch()}
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <input
          type="search"
          aria-label={en.admin.auditUi.searchPlaceholder}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={en.admin.auditUi.searchPlaceholder}
          className="min-h-[44px] flex-1 min-w-[220px] rounded-sm border border-border bg-surface-2 px-3 text-xs text-text placeholder:text-text-dim"
        />
        <select
          aria-label={en.admin.auditUi.actionFilterLabel}
          value={actionFilter}
          onChange={(e) => setActionFilter(e.target.value)}
          className="min-h-[44px] rounded-sm border border-border bg-surface-2 px-3 text-xs text-text"
        >
          <option value="all">All Actions</option>
          <option value="template">template.*</option>
          <option value="prompt">prompt.*</option>
          <option value="scoring">scoring.*</option>
          <option value="settings">settings.*</option>
          <option value="blocker">blocker.*</option>
          <option value="drive">drive.*</option>
          <option value="preset">preset.*</option>
          <option value="font">font.*</option>
          <option value="brandkit">brandkit.*</option>
        </select>
      </div>

      {filtered.length === 0 ? (
        <EmptyState title={en.admin.emptyTitle} body={en.admin.emptyBody} />
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-border bg-surface-2 text-[11px] uppercase text-text-dim">
              <tr>
                <th className="px-3 py-2.5">{en.admin.auditUi.colTime}</th>
                <th className="px-3 py-2.5">{en.admin.auditUi.colActor}</th>
                <th className="px-3 py-2.5">{en.admin.auditUi.colAction}</th>
                <th className="px-3 py-2.5">{en.admin.auditUi.colResource}</th>
                <th className="px-3 py-2.5">{en.admin.auditUi.colDiff}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filtered.map((entry, idx) => (
                <tr key={String(entry.id ?? idx)} className="hover:bg-surface-2/40">
                  <td className="px-3 py-2.5 font-mono text-[11px] text-text-dim whitespace-nowrap">
                    {String(entry.timestamp ?? "—")}
                  </td>
                  <td className="px-3 py-2.5 text-text whitespace-nowrap">
                    {String(entry.actor ?? "owner")}
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    <Badge tone="route" size="sm">
                      {String(entry.action ?? "write")}
                    </Badge>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-text whitespace-nowrap">
                    {String(entry.target ?? "—")}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-[11px] text-text-dim">
                    {String(entry.diff ?? "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
