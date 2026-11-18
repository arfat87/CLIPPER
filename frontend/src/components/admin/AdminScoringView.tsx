import { useEffect, useState } from "react";
import { Play, Save, ThumbsDown, ThumbsUp } from "lucide-react";
import { ApiError } from "../../api/client";
import {
  useAdminDryRunScoringMutation,
  useAdminJobsQuery,
  useAdminScoringQuery,
  useAdminUpdateScoringMutation,
} from "../../api/hooks";
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

const WEIGHT_KEYS = [
  "hook",
  "pacing",
  "clarity",
  "payoff",
  "visual",
  "heatmap",
] as const;

interface GenreScoringConfig {
  threshold: number;
  fallback_threshold: number;
  weights: Record<string, number>;
}

interface DryRunPick {
  clip_id: string;
  rank: number;
  title: string;
  score: number;
  old_score?: number;
  delta?: number;
}

export function AdminScoringView() {
  const { toast } = useToast();
  const scoringQuery = useAdminScoringQuery();
  const jobsQuery = useAdminJobsQuery();
  const updateScoringMutation = useAdminUpdateScoringMutation();
  const dryRunMutation = useAdminDryRunScoringMutation();

  const data = scoringQuery.data as
    | {
        active_genre?: string;
        genres?: Record<string, GenreScoringConfig>;
        feedback_stats?: { up?: number; down?: number; total?: number };
      }
    | undefined;

  const genresMap = data?.genres ?? {};
  const genreNames = Object.keys(genresMap);
  const [selectedGenre, setSelectedGenre] = useState<string>("podcast");
  const resolvedGenre = genresMap[selectedGenre]
    ? selectedGenre
    : genreNames[0] ?? "podcast";

  const activeGenreCfg = genresMap[resolvedGenre];

  const [weights, setWeights] = useState<Record<string, number>>({
    hook: 0.28,
    pacing: 0.16,
    clarity: 0.16,
    payoff: 0.2,
    visual: 0.1,
    heatmap: 0.1,
  });
  const [threshold, setThreshold] = useState<number>(0.55);
  const [fallbackThreshold, setFallbackThreshold] = useState<number>(0.4);

  const jobs = jobsQuery.data?.items ?? [];
  const [dryRunJobId, setDryRunJobId] = useState<string>("job_hinglish_01");
  const resolvedJobId = jobs.some((j) => j.id === dryRunJobId)
    ? dryRunJobId
    : jobs[0]?.id ?? "";

  const [dryRunResult, setDryRunResult] = useState<{
    job_id?: string;
    old_picks?: DryRunPick[];
    new_picks?: DryRunPick[];
  } | null>(null);

  useEffect(() => {
    if (activeGenreCfg) {
      setWeights({
        hook: activeGenreCfg.weights?.hook ?? 0.28,
        pacing: activeGenreCfg.weights?.pacing ?? 0.16,
        clarity: activeGenreCfg.weights?.clarity ?? 0.16,
        payoff: activeGenreCfg.weights?.payoff ?? 0.2,
        visual: activeGenreCfg.weights?.visual ?? 0.1,
        heatmap: activeGenreCfg.weights?.heatmap ?? 0.1,
      });
      setThreshold(activeGenreCfg.threshold ?? 0.55);
      setFallbackThreshold(activeGenreCfg.fallback_threshold ?? 0.4);
    }
  }, [resolvedGenre, activeGenreCfg]);

  if (scoringQuery.isLoading) return <LoadingState />;
  if (scoringQuery.isError) {
    return (
      <ErrorState
        title={en.admin.errorTitle}
        message={
          scoringQuery.error instanceof ApiError
            ? scoringQuery.error.message
            : en.states.errorBody
        }
        onRetry={() => void scoringQuery.refetch()}
      />
    );
  }

  const totalWeight = Object.values(weights).reduce((acc, v) => acc + v, 0);
  const upCount = Number(data?.feedback_stats?.up ?? 0);
  const downCount = Number(data?.feedback_stats?.down ?? 0);
  const totalRated = upCount + downCount;
  const approvalPct =
    totalRated > 0 ? Math.round((upCount / totalRated) * 100) : 0;

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    updateScoringMutation.mutate(
      {
        active_genre: resolvedGenre,
        genres: {
          ...genresMap,
          [resolvedGenre]: {
            threshold,
            fallback_threshold: fallbackThreshold,
            weights,
          },
        },
      },
      {
        onSuccess: () => {
          toast({
            title: en.admin.scoringUi.savedToast,
            description: resolvedGenre,
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

  const handleDryRun = () => {
    if (!resolvedJobId) return;
    dryRunMutation.mutate(
      { job_id: resolvedJobId, weights },
      {
        onSuccess: (res) => {
          setDryRunResult(
            res as {
              job_id?: string;
              old_picks?: DryRunPick[];
              new_picks?: DryRunPick[];
            }
          );
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
      {/* Feedback Stats & Genre Selector */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card className="sm:col-span-2 flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>{en.admin.scoringUi.weightsTitle}</CardTitle>
            <CardDescription>
              Tune highlight fusion signals and minimum acceptance thresholds per genre.
            </CardDescription>
          </div>
          <label className="space-y-1 text-xs">
            <span className="text-text-dim block">
              {en.admin.scoringUi.genreLabel}
            </span>
            <select
              aria-label={en.admin.scoringUi.genreLabel}
              value={resolvedGenre}
              onChange={(e) => setSelectedGenre(e.target.value)}
              className="min-h-[44px] rounded-sm border border-border bg-surface-2 px-3 text-sm text-text capitalize"
            >
              {(genreNames.length > 0
                ? genreNames
                : ["podcast", "tutorial", "creator", "interview"]
              ).map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
            </select>
          </label>
        </Card>

        <Card className="space-y-2">
          <CardTitle>{en.admin.scoringUi.feedbackStatsTitle}</CardTitle>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge tone="ok" size="sm">
              <ThumbsUp className="h-3 w-3" aria-hidden="true" />
              <span>
                {upCount} {en.admin.scoringUi.thumbsUpLabel}
              </span>
            </Badge>
            <Badge tone="err" size="sm">
              <ThumbsDown className="h-3 w-3" aria-hidden="true" />
              <span>
                {downCount} {en.admin.scoringUi.thumbsDownLabel}
              </span>
            </Badge>
          </div>
          <p className="text-xs text-text-dim">
            {en.admin.scoringUi.approvalRateLabel}:{" "}
            <span className="font-mono font-bold text-text">{approvalPct}%</span>
          </p>
        </Card>
      </div>

      {/* Fusion Weight Sliders + Thresholds */}
      <Card>
        <form onSubmit={handleSave} className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle className="capitalize">
              {resolvedGenre} Signals
            </CardTitle>
            <Badge tone={Math.abs(totalWeight - 1) <= 0.05 ? "ok" : "warn"}>
              {en.admin.scoringUi.weightsSumLabel}: {totalWeight.toFixed(2)}
            </Badge>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {WEIGHT_KEYS.map((key) => {
              const val = weights[key] ?? 0;
              return (
                <label
                  key={key}
                  className="block rounded-sm border border-border bg-surface-2/60 p-3 text-xs space-y-1.5"
                >
                  <div className="flex items-center justify-between">
                    <span className="capitalize font-semibold text-text">
                      {key}
                    </span>
                    <span className="font-mono text-accent">
                      {val.toFixed(2)}
                    </span>
                  </div>
                  <input
                    type="range"
                    aria-label={`${key} weight`}
                    min={0}
                    max={1}
                    step={0.01}
                    value={val}
                    onChange={(e) =>
                      setWeights((prev) => ({
                        ...prev,
                        [key]: Number(e.target.value),
                      }))
                    }
                    className="min-h-[44px] w-full accent-[#FFD400]"
                  />
                </label>
              );
            })}
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 border-t border-border pt-4">
            <label className="block space-y-1 text-xs">
              <div className="flex items-center justify-between">
                <span className="text-text-dim">
                  {en.admin.scoringUi.thresholdLabel}
                </span>
                <span className="font-mono font-bold text-text">
                  {threshold.toFixed(2)}
                </span>
              </div>
              <input
                type="range"
                aria-label={en.admin.scoringUi.thresholdLabel}
                min={0.2}
                max={0.9}
                step={0.01}
                value={threshold}
                onChange={(e) => setThreshold(Number(e.target.value))}
                className="min-h-[44px] w-full accent-[#FFD400]"
              />
            </label>

            <label className="block space-y-1 text-xs">
              <div className="flex items-center justify-between">
                <span className="text-text-dim">
                  {en.admin.scoringUi.fallbackThresholdLabel}
                </span>
                <span className="font-mono font-bold text-text">
                  {fallbackThreshold.toFixed(2)}
                </span>
              </div>
              <input
                type="range"
                aria-label={en.admin.scoringUi.fallbackThresholdLabel}
                min={0.15}
                max={0.85}
                step={0.01}
                value={fallbackThreshold}
                onChange={(e) => setFallbackThreshold(Number(e.target.value))}
                className="min-h-[44px] w-full accent-[#FFD400]"
              />
            </label>
          </div>

          <div className="flex justify-end">
            <Button
              type="submit"
              loading={updateScoringMutation.isPending}
              leftIcon={<Save className="h-4 w-4" aria-hidden="true" />}
            >
              {en.admin.scoringUi.saveWeightsCta}
            </Button>
          </div>
        </form>
      </Card>

      {/* Dry-run Comparison */}
      <Card className="space-y-4">
        <CardHeader>
          <div>
            <CardTitle>{en.admin.scoringUi.dryRunTitle}</CardTitle>
            <CardDescription>
              Preview how proposed weights reorder picks on a completed job before saving.
            </CardDescription>
          </div>

          <div className="flex flex-wrap items-end gap-2">
            <label className="space-y-1 text-xs">
              <span className="sr-only">{en.admin.scoringUi.dryRunJobLabel}</span>
              <select
                aria-label={en.admin.scoringUi.dryRunJobLabel}
                value={resolvedJobId}
                onChange={(e) => setDryRunJobId(e.target.value)}
                className="min-h-[44px] rounded-sm border border-border bg-surface-2 px-3 text-xs text-text"
              >
                {jobs.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.title ?? j.id}
                  </option>
                ))}
              </select>
            </label>

            <Button
              variant="secondary"
              loading={dryRunMutation.isPending}
              disabled={!resolvedJobId}
              onClick={handleDryRun}
              leftIcon={<Play className="h-4 w-4" aria-hidden="true" />}
            >
              {dryRunMutation.isPending
                ? en.admin.scoringUi.runningDryRunCta
                : en.admin.scoringUi.dryRunCta}
            </Button>
          </div>
        </CardHeader>

        {dryRunResult ? (
          <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-2 rounded-sm border border-border bg-surface-2/50 p-3">
              <p className="text-xs font-bold text-text-dim">
                {en.admin.scoringUi.oldPicksTitle}
              </p>
              {(dryRunResult.old_picks ?? []).map((pick) => (
                <div
                  key={pick.clip_id}
                  className="flex items-center justify-between gap-2 rounded-sm border border-border bg-surface px-2.5 py-2 text-xs"
                >
                  <span className="truncate font-medium text-text">
                    #{pick.rank} · {pick.title}
                  </span>
                  <span className="font-mono font-bold text-text-dim">
                    {Math.round(pick.score * 100)}
                  </span>
                </div>
              ))}
            </div>

            <div className="space-y-2 rounded-sm border border-accent/40 bg-accent/5 p-3">
              <p className="text-xs font-bold text-accent">
                {en.admin.scoringUi.newPicksTitle}
              </p>
              {(dryRunResult.new_picks ?? []).map((pick) => {
                const delta = pick.delta ?? 0;
                return (
                  <div
                    key={pick.clip_id}
                    className="flex items-center justify-between gap-2 rounded-sm border border-border bg-surface px-2.5 py-2 text-xs"
                  >
                    <span className="truncate font-medium text-text">
                      #{pick.rank} · {pick.title}
                    </span>
                    <div className="flex items-center gap-2 shrink-0">
                      <Badge
                        tone={delta > 0 ? "ok" : delta < 0 ? "warn" : "neutral"}
                        size="sm"
                      >
                        {delta >= 0 ? `+${delta.toFixed(2)}` : delta.toFixed(2)}
                      </Badge>
                      <span className="font-mono font-bold text-text">
                        {Math.round(pick.score * 100)}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        ) : null}
      </Card>
    </div>
  );
}
