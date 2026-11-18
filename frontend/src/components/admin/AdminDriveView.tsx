import { useState } from "react";
import { Copy, Folder, RefreshCw } from "lucide-react";
import { ApiError } from "../../api/client";
import {
  useAdminDriveQuery,
  useDriveTreeQuery,
  useTriggerDriveSyncMutation,
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
  StatusBadge,
  useToast,
} from "../ui";

const DRIVE_ROUTES = [
  "source",
  "template",
  "font",
  "brandkit",
  "doc",
  "reference",
  "export",
  "unsorted",
] as const;

interface DriveTreeFolder {
  id: string;
  name: string;
  route: string;
  enabled?: boolean;
  file_count?: number;
  children?: string[];
}

export function AdminDriveView() {
  const { toast } = useToast();
  const driveQuery = useAdminDriveQuery();
  const treeQuery = useDriveTreeQuery();
  const syncMutation = useTriggerDriveSyncMutation();

  const [folderEnabledOverrides, setFolderEnabledOverrides] = useState<
    Record<string, boolean>
  >({});
  const [folderRouteOverrides, setFolderRouteOverrides] = useState<
    Record<string, string>
  >({});

  if (driveQuery.isLoading) return <LoadingState />;
  if (driveQuery.isError) {
    return (
      <ErrorState
        title={en.library.errorTitle}
        message={
          driveQuery.error instanceof ApiError
            ? driveQuery.error.message
            : en.states.errorBody
        }
        onRetry={() => void driveQuery.refetch()}
      />
    );
  }

  const drive = driveQuery.data;
  const manifest = (treeQuery.data?.manifest ?? {}) as {
    folders?: DriveTreeFolder[];
  };
  const folders = Array.isArray(manifest.folders) ? manifest.folders : [];

  const handleSync = (full: boolean) => {
    syncMutation.mutate(
      { full },
      {
        onSuccess: () => {
          toast({
            title: en.admin.driveUi.resyncToast,
            description: full ? "Full resync" : "Incremental sync",
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

  const handleCopyRoot = async () => {
    if (!drive?.root_folder_id) return;
    try {
      await navigator.clipboard?.writeText(drive.root_folder_id);
    } catch {
      // Clipboard API may be unavailable in jsdom
    }
    toast({
      title: en.admin.driveUi.copiedRootToast,
      description: drive.root_folder_id,
      tone: "info",
    });
  };

  return (
    <div className="space-y-5">
      {/* Status & Resync Header Card */}
      <Card className="space-y-4">
        <CardHeader>
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle>{en.admin.driveUi.statusTitle}</CardTitle>
              <StatusBadge status={drive?.status ?? "DISABLED"} />
            </div>
            <CardDescription>
              {en.admin.driveUi.lastSyncLabel}:{" "}
              {drive?.last_sync_at ?? "Never"}
            </CardDescription>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="secondary"
              loading={syncMutation.isPending}
              onClick={() => handleSync(false)}
              leftIcon={<RefreshCw className="h-4 w-4" aria-hidden="true" />}
            >
              {en.admin.driveUi.incrementalSyncCta}
            </Button>
            <Button
              size="sm"
              variant="primary"
              loading={syncMutation.isPending}
              onClick={() => handleSync(true)}
              leftIcon={<RefreshCw className="h-4 w-4" aria-hidden="true" />}
            >
              {en.admin.driveUi.forceResyncCta}
            </Button>
          </div>
        </CardHeader>

        {drive?.last_error ? (
          <div
            role="alert"
            className="rounded-sm border border-err/50 bg-err/10 px-3 py-2 text-xs text-err"
          >
            {drive.last_error}
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border bg-surface-2 px-3 py-2 text-xs">
          <div>
            <span className="text-text-dim">
              {en.admin.driveUi.rootFolderLabel}:{" "}
            </span>
            <span className="font-mono text-text">
              {drive?.root_folder_id ?? "—"}
            </span>
          </div>
          {drive?.root_folder_id ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void handleCopyRoot()}
              leftIcon={<Copy className="h-3.5 w-3.5" aria-hidden="true" />}
            >
              {en.admin.driveUi.copyRootCta}
            </Button>
          ) : null}
        </div>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          {Object.entries(drive?.counts_by_route ?? {}).map(([route, count]) => (
            <div
              key={route}
              className="rounded-sm border border-border bg-surface-2 p-3"
            >
              <p className="text-[11px] uppercase text-text-dim">{route}</p>
              <p className="text-lg font-bold text-text">{count}</p>
            </div>
          ))}
        </div>
      </Card>

      {/* Folder Tree & Routing Overrides */}
      <Card className="space-y-4">
        <CardHeader>
          <CardTitle>{en.admin.driveUi.treeTitle}</CardTitle>
        </CardHeader>

        {treeQuery.isLoading ? (
          <LoadingState showSkeletons={false} title="Loading Drive tree..." body="" />
        ) : (
          <div className="space-y-3">
            {folders.map((folder) => {
              const isEnabled =
                folderEnabledOverrides[folder.id] ?? (folder.enabled ?? true);
              const routeVal = folderRouteOverrides[folder.id] ?? folder.route;

              return (
                <div
                  key={folder.id}
                  className="rounded-sm border border-border bg-surface-2/70 p-3 space-y-2"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Folder
                        className="h-4 w-4 text-accent shrink-0"
                        aria-hidden="true"
                      />
                      <span className="font-mono text-xs font-bold text-text">
                        {folder.name}/
                      </span>
                      <Badge tone="route" size="sm">
                        {routeVal}
                      </Badge>
                      {typeof folder.file_count === "number" ? (
                        <span className="text-[11px] text-text-dim">
                          {folder.file_count} files
                        </span>
                      ) : null}
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      <label className="inline-flex min-h-[44px] cursor-pointer items-center gap-1.5 px-2 text-xs text-text">
                        <input
                          type="checkbox"
                          checked={isEnabled}
                          onChange={(e) => {
                            setFolderEnabledOverrides((prev) => ({
                              ...prev,
                              [folder.id]: e.target.checked,
                            }));
                            toast({
                              title: en.admin.driveUi.overrideUpdatedToast,
                              description: `${folder.name}: ${e.target.checked ? "enabled" : "disabled"}`,
                              tone: "ok",
                            });
                          }}
                          className="h-4 w-4 accent-[#FFD400]"
                        />
                        <span>{en.admin.driveUi.enableLabel}</span>
                      </label>

                      <label className="flex items-center gap-1.5 text-xs">
                        <span className="sr-only">
                          {en.admin.driveUi.routeOverrideLabel} {folder.name}
                        </span>
                        <select
                          aria-label={`${en.admin.driveUi.routeOverrideLabel} ${folder.name}`}
                          value={routeVal}
                          onChange={(e) => {
                            const nextRoute = e.target.value;
                            setFolderRouteOverrides((prev) => ({
                              ...prev,
                              [folder.id]: nextRoute,
                            }));
                            toast({
                              title: en.admin.driveUi.overrideUpdatedToast,
                              description: `${folder.name} → ${nextRoute}`,
                              tone: "ok",
                            });
                          }}
                          className="min-h-[44px] rounded-sm border border-border bg-surface px-2.5 text-xs text-text"
                        >
                          {DRIVE_ROUTES.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  </div>

                  {folder.children && folder.children.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5 pl-6">
                      {folder.children.map((child) => (
                        <span
                          key={child}
                          className="rounded border border-border bg-bg px-2 py-0.5 font-mono text-[11px] text-text-dim"
                        >
                          {child}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}

        {treeQuery.data?.tree_md ? (
          <CardContent className="border-t border-border pt-3">
            <p className="mb-1.5 text-xs font-semibold text-text-dim">
              {en.admin.driveUi.treeMarkdownTitle}
            </p>
            <pre className="overflow-auto rounded-sm border border-border bg-bg p-3 font-mono text-[11px] text-text-dim">
              {treeQuery.data.tree_md}
            </pre>
          </CardContent>
        ) : null}
      </Card>
    </div>
  );
}
