import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  FileCode2,
  GitCompare,
  Play,
  RotateCcw,
  Save,
  XCircle,
} from "lucide-react";
import { ApiError } from "../../api/client";
import {
  useAdminActivatePromptMutation,
  useAdminPromptDetailQuery,
  useAdminPromptsQuery,
  useAdminTestPromptMutation,
  useAdminUpdatePromptMutation,
} from "../../api/hooks";
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
  useToast,
} from "../ui";

interface PromptVersionItem {
  version: number;
  created_at?: string;
  author?: string;
  content: string;
}

interface DiffLine {
  kind: "same" | "add" | "del";
  text: string;
}

function computeLineDiff(baseText: string, draftText: string): DiffLine[] {
  const baseLines = baseText.split("\n");
  const draftLines = draftText.split("\n");
  const result: DiffLine[] = [];
  const maxLen = Math.max(baseLines.length, draftLines.length);

  for (let i = 0; i < maxLen; i++) {
    const b = baseLines[i];
    const d = draftLines[i];
    if (b === d) {
      if (b !== undefined) {
        result.push({ kind: "same", text: b });
      }
    } else {
      if (b !== undefined) {
        result.push({ kind: "del", text: b });
      }
      if (d !== undefined) {
        result.push({ kind: "add", text: d });
      }
    }
  }
  return result;
}

export function AdminPromptsView() {
  const { toast } = useToast();
  const promptsQuery = useAdminPromptsQuery();
  const updatePromptMutation = useAdminUpdatePromptMutation();
  const activatePromptMutation = useAdminActivatePromptMutation();
  const testPromptMutation = useAdminTestPromptMutation();

  const items = promptsQuery.data?.items ?? [];
  const [selectedName, setSelectedName] = useState<string>("highlight_map");
  const resolvedName =
    items.find((p) => String(p.name) === selectedName)?.name
      ? selectedName
      : items[0]?.name
        ? String(items[0].name)
        : null;

  const detailQuery = useAdminPromptDetailQuery(resolvedName);
  const detail = detailQuery.data as
    | {
        name?: string;
        path?: string;
        active_version?: number;
        versions?: PromptVersionItem[];
      }
    | undefined;

  const versions = detail?.versions ?? [];
  const activeVersionNum = Number(detail?.active_version ?? 1);
  const activeVersionObj =
    versions.find((v) => v.version === activeVersionNum) ?? versions[0];
  const activeContent = activeVersionObj?.content ?? "";

  const [draftContent, setDraftContent] = useState("");
  const [showDiff, setShowDiff] = useState(true);
  const [testOutput, setTestOutput] = useState<Record<string, unknown> | null>(
    null
  );

  useEffect(() => {
    setDraftContent(activeContent);
    setTestOutput(null);
  }, [resolvedName, activeVersionNum, activeContent]);

  const diffLines = useMemo(
    () => computeLineDiff(activeContent, draftContent),
    [activeContent, draftContent]
  );
  const hasDiffChanges = diffLines.some((l) => l.kind !== "same");

  if (promptsQuery.isLoading) return <LoadingState />;
  if (promptsQuery.isError) {
    return (
      <ErrorState
        title={en.admin.errorTitle}
        message={
          promptsQuery.error instanceof ApiError
            ? promptsQuery.error.message
            : en.states.errorBody
        }
        onRetry={() => void promptsQuery.refetch()}
      />
    );
  }
  if (items.length === 0) {
    return <EmptyState title={en.admin.emptyTitle} body={en.admin.emptyBody} />;
  }

  const handleSaveVersion = (e: React.FormEvent) => {
    e.preventDefault();
    if (!resolvedName || !draftContent.trim()) return;
    updatePromptMutation.mutate(
      { name: resolvedName, payload: { content: draftContent } },
      {
        onSuccess: () => {
          toast({
            title: en.admin.promptsUi.savedToast,
            description: resolvedName,
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

  const handleActivateVersion = (version: number) => {
    if (!resolvedName) return;
    activatePromptMutation.mutate(
      { name: resolvedName, payload: { version } },
      {
        onSuccess: () => {
          toast({
            title: en.admin.promptsUi.activatedToast,
            description: `${resolvedName}@v${version}`,
            tone: "ok",
          });
        },
      }
    );
  };

  const handleTestFixture = () => {
    if (!resolvedName) return;
    testPromptMutation.mutate(resolvedName, {
      onSuccess: (res) => {
        setTestOutput(res);
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

  const schemaValid = Boolean(testOutput?.schema_valid);

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
      {/* Left: Prompt Files List + Version History */}
      <div className="space-y-4 lg:col-span-4">
        <Card className="space-y-3">
          <CardHeader>
            <CardTitle>{en.admin.promptsUi.listTitle}</CardTitle>
          </CardHeader>
          <div className="space-y-2">
            {items.map((item) => {
              const name = String(item.name ?? "");
              const active = name === resolvedName;
              return (
                <button
                  key={name}
                  type="button"
                  onClick={() => setSelectedName(name)}
                  className={`flex min-h-[44px] w-full items-center justify-between gap-2 rounded-sm border px-3 py-2 text-left text-xs transition ${
                    active
                      ? "border-accent bg-accent/10 text-text"
                      : "border-border bg-surface-2 text-text-dim hover:text-text"
                  }`}
                >
                  <span className="flex items-center gap-2 font-mono font-semibold">
                    <FileCode2 className="h-4 w-4 text-accent" aria-hidden="true" />
                    {name}.md
                  </span>
                  <Badge tone={active ? "accent" : "neutral"} size="sm">
                    v{String(item.active_version ?? 1)}
                  </Badge>
                </button>
              );
            })}
          </div>
        </Card>

        <Card className="space-y-3">
          <CardHeader>
            <CardTitle>{en.admin.promptsUi.versionsTitle}</CardTitle>
          </CardHeader>
          {detailQuery.isLoading ? (
            <LoadingState showSkeletons={false} title="Loading versions..." body="" />
          ) : (
            <div className="space-y-2">
              {[...versions]
                .sort((a, b) => b.version - a.version)
                .map((ver) => {
                  const isActive = ver.version === activeVersionNum;
                  return (
                    <div
                      key={ver.version}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border bg-surface-2 px-3 py-2 text-xs"
                    >
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-mono font-bold text-text">
                            v{ver.version}
                          </span>
                          {isActive ? (
                            <Badge tone="ok" size="sm">
                              {en.admin.promptsUi.activeVersionBadge}
                            </Badge>
                          ) : null}
                        </div>
                        <p className="text-[11px] text-text-dim">
                          {ver.author ?? "owner"}
                        </p>
                      </div>

                      {!isActive ? (
                        <Button
                          size="sm"
                          variant="secondary"
                          loading={activatePromptMutation.isPending}
                          onClick={() => handleActivateVersion(ver.version)}
                          leftIcon={
                            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                          }
                        >
                          {en.admin.promptsUi.activateCta}
                        </Button>
                      ) : null}
                    </div>
                  );
                })}
            </div>
          )}
        </Card>
      </div>

      {/* Right: Editor, Inline Diff vs Active, Fixture Dry-Run */}
      <div className="space-y-4 lg:col-span-8">
        <Card className="space-y-4">
          <CardHeader>
            <div>
              <CardTitle className="font-mono">
                {detail?.path ?? `config/prompts/${resolvedName}.md`}
              </CardTitle>
              <CardDescription>
                Active version: v{activeVersionNum}
              </CardDescription>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant={showDiff ? "primary" : "secondary"}
                aria-pressed={showDiff}
                onClick={() => setShowDiff((v) => !v)}
                leftIcon={<GitCompare className="h-4 w-4" aria-hidden="true" />}
              >
                {en.admin.promptsUi.diffToggleLabel}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                loading={testPromptMutation.isPending}
                onClick={handleTestFixture}
                leftIcon={<Play className="h-4 w-4" aria-hidden="true" />}
              >
                {testPromptMutation.isPending
                  ? en.admin.promptsUi.testingFixtureCta
                  : en.admin.promptsUi.testFixtureCta}
              </Button>
            </div>
          </CardHeader>

          <form onSubmit={handleSaveVersion} className="space-y-3">
            <label className="block space-y-1 text-xs">
              <span className="text-text-dim">
                {en.admin.promptsUi.editorLabel}
              </span>
              <textarea
                rows={10}
                value={draftContent}
                onChange={(e) => setDraftContent(e.target.value)}
                className="w-full rounded-sm border border-border bg-bg p-3 font-mono text-xs text-text leading-relaxed"
              />
            </label>

            <div className="flex justify-end">
              <Button
                type="submit"
                loading={updatePromptMutation.isPending}
                leftIcon={<Save className="h-4 w-4" aria-hidden="true" />}
              >
                {en.admin.promptsUi.saveVersionCta}
              </Button>
            </div>
          </form>

          {showDiff ? (
            <div
              aria-label="Inline diff against active prompt version"
              className="rounded-sm border border-border bg-bg p-3 space-y-1.5"
            >
              <p className="text-xs font-semibold text-text-dim">
                {en.admin.promptsUi.diffToggleLabel} (v{activeVersionNum})
              </p>
              {!hasDiffChanges ? (
                <p className="text-xs text-text-dim">
                  {en.admin.promptsUi.diffNoChanges}
                </p>
              ) : (
                <pre className="max-h-56 overflow-auto font-mono text-[11px] leading-relaxed">
                  {diffLines.map((line, i) => (
                    <div
                      key={`${i}-${line.kind}`}
                      className={
                        line.kind === "add"
                          ? "bg-ok/15 text-ok px-2"
                          : line.kind === "del"
                            ? "bg-err/15 text-err px-2"
                            : "text-text-dim px-2"
                      }
                    >
                      {line.kind === "add"
                        ? `+ ${line.text}`
                        : line.kind === "del"
                          ? `- ${line.text}`
                          : `  ${line.text}`}
                    </div>
                  ))}
                </pre>
              )}
            </div>
          ) : null}
        </Card>

        {testOutput ? (
          <Card className="space-y-3">
            <CardHeader>
              <div>
                <CardTitle>{en.admin.promptsUi.testResultTitle}</CardTitle>
                <CardDescription>
                  Fixture: {String(testOutput.fixture ?? "default")} ·{" "}
                  {String(testOutput.latency_ms ?? 0)} ms
                </CardDescription>
              </div>
              <Badge tone={schemaValid ? "ok" : "err"}>
                {schemaValid ? (
                  <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                ) : (
                  <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                <span>
                  {schemaValid
                    ? en.admin.promptsUi.schemaValidBadge
                    : en.admin.promptsUi.schemaInvalidBadge}
                </span>
              </Badge>
            </CardHeader>
            <CardContent>
              <pre className="overflow-auto rounded-sm border border-border bg-bg p-3 font-mono text-xs text-text">
                {JSON.stringify(testOutput.output ?? testOutput, null, 2)}
              </pre>
            </CardContent>
          </Card>
        ) : null}
      </div>
    </div>
  );
}
