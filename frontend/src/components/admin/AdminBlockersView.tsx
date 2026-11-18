import { useState } from "react";
import { CheckCircle2, ClipboardCheck } from "lucide-react";
import { ApiError } from "../../api/client";
import {
  useAdminBlockersQuery,
  useAdminResolveBlockerMutation,
} from "../../api/hooks";
import type { Blocker } from "../../api/types";
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
  StatusBadge,
  useToast,
} from "../ui";

type BlockerFilter = "all" | "OPEN" | "RESOLVED";

export function AdminBlockersView() {
  const { toast } = useToast();
  const blockersQuery = useAdminBlockersQuery();
  const resolveMutation = useAdminResolveBlockerMutation();

  const [statusFilter, setStatusFilter] = useState<BlockerFilter>("all");
  const [notesById, setNotesById] = useState<Record<string, string>>({});

  if (blockersQuery.isLoading) return <LoadingState />;
  if (blockersQuery.isError) {
    return (
      <ErrorState
        title={en.admin.errorTitle}
        message={
          blockersQuery.error instanceof ApiError
            ? blockersQuery.error.message
            : en.states.errorBody
        }
        onRetry={() => void blockersQuery.refetch()}
      />
    );
  }

  const allBlockers = blockersQuery.data?.items ?? [];
  const openCount = allBlockers.filter((b) => b.status === "OPEN").length;
  const resolvedCount = allBlockers.filter(
    (b) => b.status === "RESOLVED"
  ).length;

  const filtered = allBlockers.filter((b) =>
    statusFilter === "all" ? true : b.status === statusFilter
  );

  const handleResolve = (blocker: Blocker) => {
    const note = notesById[blocker.id]?.trim();
    resolveMutation.mutate(
      {
        id: blocker.id,
        payload: note ? { note } : {},
      },
      {
        onSuccess: (updated) => {
          toast({
            title: en.admin.blockersUi.resolvedToast,
            description: `${updated.id} — ${updated.title}`,
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
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {(
          [
            {
              id: "all",
              label: `${en.admin.blockersUi.filterAll} (${allBlockers.length})`,
            },
            {
              id: "OPEN",
              label: `${en.admin.blockersUi.filterOpen} (${openCount})`,
            },
            {
              id: "RESOLVED",
              label: `${en.admin.blockersUi.filterResolved} (${resolvedCount})`,
            },
          ] as const
        ).map(({ id, label }) => (
          <Button
            key={id}
            size="sm"
            variant={statusFilter === id ? "primary" : "secondary"}
            onClick={() => setStatusFilter(id)}
          >
            {label}
          </Button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          title={en.admin.blockersUi.emptyTitle}
          body={en.admin.blockersUi.emptyBody}
        />
      ) : (
        <div className="space-y-3">
          {filtered.map((blk) => {
            const payload = (blk.payload ?? {}) as Record<string, unknown>;
            const instructions =
              typeof payload.instructions === "string"
                ? payload.instructions
                : null;
            const steps = Array.isArray(payload.steps)
              ? (payload.steps as string[])
              : [];
            const envKey =
              typeof payload.env_key === "string" ? payload.env_key : null;
            const secretPath =
              typeof payload.secret_path === "string"
                ? payload.secret_path
                : null;
            const resolutionNote =
              typeof payload.resolution_note === "string"
                ? payload.resolution_note
                : null;

            return (
              <Card key={blk.id} className="space-y-3">
                <CardHeader>
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="route" size="sm">
                        {blk.id}
                      </Badge>
                      <Badge tone="neutral" size="sm">
                        {blk.type}
                      </Badge>
                      {envKey ? (
                        <Badge tone="warn" size="sm">
                          {envKey}
                        </Badge>
                      ) : null}
                      {secretPath ? (
                        <Badge tone="warn" size="sm">
                          {secretPath}
                        </Badge>
                      ) : null}
                    </div>
                    <CardTitle className="mt-1.5">{blk.title}</CardTitle>
                    <CardDescription className="mt-0.5">
                      Task: {blk.task_id}
                      {blk.step ? ` · Step: ${blk.step}` : ""}
                      {blk.resolved_at
                        ? ` · Resolved: ${blk.resolved_at}`
                        : ""}
                    </CardDescription>
                  </div>
                  <StatusBadge status={blk.status} />
                </CardHeader>

                {(instructions || steps.length > 0) && (
                  <CardContent className="rounded-sm border border-border bg-surface-2/70 p-3 space-y-2 text-xs">
                    <p className="font-semibold text-text">
                      {en.admin.blockersUi.instructionsTitle}
                    </p>
                    {instructions ? (
                      <p className="text-text-dim">{instructions}</p>
                    ) : null}
                    {steps.length > 0 ? (
                      <ol className="list-decimal pl-5 space-y-1 text-text-dim">
                        {steps.map((stepText) => (
                          <li key={stepText}>{stepText}</li>
                        ))}
                      </ol>
                    ) : null}
                  </CardContent>
                )}

                {blk.status === "OPEN" ? (
                  <div className="flex flex-col gap-2 border-t border-border pt-3 sm:flex-row sm:items-end">
                    <label className="flex-1 space-y-1 text-xs">
                      <span className="text-text-dim">
                        {en.admin.blockersUi.noteLabel}
                      </span>
                      <input
                        type="text"
                        value={notesById[blk.id] ?? ""}
                        onChange={(e) =>
                          setNotesById((prev) => ({
                            ...prev,
                            [blk.id]: e.target.value,
                          }))
                        }
                        placeholder={en.admin.blockersUi.notePlaceholder}
                        className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-xs text-text placeholder:text-text-dim"
                      />
                    </label>
                    <Button
                      size="sm"
                      loading={resolveMutation.isPending}
                      onClick={() => handleResolve(blk)}
                      leftIcon={
                        <ClipboardCheck className="h-4 w-4" aria-hidden="true" />
                      }
                    >
                      {resolveMutation.isPending
                        ? en.admin.blockersUi.resolvingCta
                        : en.admin.blockersUi.resolveCta}
                    </Button>
                  </div>
                ) : resolutionNote ? (
                  <p className="flex items-center gap-1.5 text-xs text-ok">
                    <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />
                    <span>{resolutionNote}</span>
                  </p>
                ) : null}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
