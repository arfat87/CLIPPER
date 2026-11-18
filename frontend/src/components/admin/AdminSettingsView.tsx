import { useEffect, useState } from "react";
import { CheckCircle2, PlugZap, Save, ShieldAlert } from "lucide-react";
import { ApiError } from "../../api/client";
import {
  useAdminSettingsQuery,
  useAdminUpdateSettingsMutation,
  useHealthQuery,
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

export function AdminSettingsView() {
  const { toast } = useToast();
  const settingsQuery = useAdminSettingsQuery();
  const healthQuery = useHealthQuery();
  const updateSettingsMutation = useAdminUpdateSettingsMutation();

  const data = (settingsQuery.data ?? {}) as Record<string, unknown>;
  const secrets =
    typeof data.secrets === "object" && data.secrets !== null
      ? (data.secrets as Record<string, { set?: boolean }>)
      : {};

  const [asrEngine, setAsrEngine] = useState("faster-whisper");
  const [asrModel, setAsrModel] = useState("large-v3");
  const [device, setDevice] = useState("cuda");
  const [llmProvider, setLlmProvider] = useState("gemini");
  const [llmModel, setLlmModel] = useState("gemini-2.5-flash");
  const [connectivityCheckedAt, setConnectivityCheckedAt] = useState<string | null>(
    null
  );
  const [testingConnectivity, setTestingConnectivity] = useState(false);

  useEffect(() => {
    if (data.asr_engine) setAsrEngine(String(data.asr_engine));
    if (data.asr_model) setAsrModel(String(data.asr_model));
    if (data.device) setDevice(String(data.device));
    if (data.llm_provider) setLlmProvider(String(data.llm_provider));
    if (data.llm_model) setLlmModel(String(data.llm_model));
  }, [
    data.asr_engine,
    data.asr_model,
    data.device,
    data.llm_provider,
    data.llm_model,
  ]);

  if (settingsQuery.isLoading) return <LoadingState />;
  if (settingsQuery.isError) {
    return (
      <ErrorState
        title={en.admin.errorTitle}
        message={
          settingsQuery.error instanceof ApiError
            ? settingsQuery.error.message
            : en.states.errorBody
        }
        onRetry={() => void settingsQuery.refetch()}
      />
    );
  }

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    updateSettingsMutation.mutate(
      {
        asr_engine: asrEngine,
        asr_model: asrModel,
        device,
        llm_provider: llmProvider,
        llm_model: llmModel,
      },
      {
        onSuccess: () => {
          toast({
            title: en.admin.settingsUi.savedToast,
            description: `${llmProvider} · ${asrEngine} (${device})`,
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

  const handleTestConnectivity = async () => {
    setTestingConnectivity(true);
    try {
      await healthQuery.refetch();
      setConnectivityCheckedAt(new Date().toLocaleTimeString());
      toast({
        title: en.admin.settingsUi.connectivityOkToast,
        tone: "ok",
      });
    } finally {
      setTestingConnectivity(false);
    }
  };

  const health = healthQuery.data;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        {/* Runtime Configuration Form */}
        <Card className="lg:col-span-7 space-y-4">
          <CardHeader>
            <CardTitle>{en.admin.settingsUi.runtimeTitle}</CardTitle>
          </CardHeader>

          <form onSubmit={handleSave} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-xs">
              <span className="text-text-dim">
                {en.admin.settingsUi.asrEngineLabel}
              </span>
              <select
                aria-label={en.admin.settingsUi.asrEngineLabel}
                value={asrEngine}
                onChange={(e) => setAsrEngine(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
              >
                <option value="faster-whisper">faster-whisper</option>
                <option value="whisper.cpp">whisper.cpp</option>
                <option value="indic-conformer">indic-conformer</option>
              </select>
            </label>

            <label className="space-y-1 text-xs">
              <span className="text-text-dim">
                {en.admin.settingsUi.asrModelLabel}
              </span>
              <select
                aria-label={en.admin.settingsUi.asrModelLabel}
                value={asrModel}
                onChange={(e) => setAsrModel(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
              >
                <option value="large-v3">large-v3</option>
                <option value="medium">medium</option>
                <option value="small">small</option>
              </select>
            </label>

            <label className="space-y-1 text-xs">
              <span className="text-text-dim">
                {en.admin.settingsUi.deviceLabel}
              </span>
              <select
                aria-label={en.admin.settingsUi.deviceLabel}
                value={device}
                onChange={(e) => setDevice(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
              >
                <option value="cuda">cuda</option>
                <option value="cpu">cpu</option>
                <option value="mps">mps</option>
              </select>
            </label>

            <label className="space-y-1 text-xs">
              <span className="text-text-dim">
                {en.admin.settingsUi.llmProviderLabel}
              </span>
              <select
                aria-label={en.admin.settingsUi.llmProviderLabel}
                value={llmProvider}
                onChange={(e) => setLlmProvider(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
              >
                <option value="gemini">gemini</option>
                <option value="ollama">ollama (local)</option>
                <option value="openai">openai</option>
              </select>
            </label>

            <label className="sm:col-span-2 space-y-1 text-xs">
              <span className="text-text-dim">
                {en.admin.settingsUi.llmModelLabel}
              </span>
              <input
                type="text"
                aria-label={en.admin.settingsUi.llmModelLabel}
                value={llmModel}
                onChange={(e) => setLlmModel(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 font-mono text-sm text-text"
              />
            </label>

            <div className="sm:col-span-2 flex justify-end pt-2">
              <Button
                type="submit"
                loading={updateSettingsMutation.isPending}
                leftIcon={<Save className="h-4 w-4" aria-hidden="true" />}
              >
                {en.admin.settingsUi.saveCta}
              </Button>
            </div>
          </form>
        </Card>

        {/* Secrets Presence Panel (never secret values) */}
        <Card className="lg:col-span-5 space-y-3">
          <CardHeader>
            <div>
              <CardTitle>{en.admin.settingsUi.secretsTitle}</CardTitle>
              <CardDescription className="mt-1">
                {en.admin.settingsUi.secretsNote}
              </CardDescription>
            </div>
          </CardHeader>

          <CardContent className="space-y-2.5">
            {Object.entries(secrets).map(([key, meta]) => {
              const isSet = Boolean(meta?.set);
              return (
                <div
                  key={key}
                  className="flex items-center justify-between gap-2 rounded-sm border border-border bg-surface-2 px-3 py-2.5 text-xs"
                >
                  <span className="font-mono font-medium text-text">{key}</span>
                  <Badge tone={isSet ? "ok" : "warn"}>
                    {isSet ? (
                      <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                    ) : (
                      <ShieldAlert className="h-3 w-3" aria-hidden="true" />
                    )}
                    <span>
                      {isSet
                        ? en.admin.settingsUi.keySet
                        : en.admin.settingsUi.keyNotSet}
                    </span>
                  </Badge>
                </div>
              );
            })}
          </CardContent>
        </Card>
      </div>

      {/* Connectivity Test Card */}
      <Card className="space-y-3">
        <CardHeader>
          <div>
            <CardTitle>{en.admin.settingsUi.connectivityTitle}</CardTitle>
            <CardDescription>
              Verify live reachability for LLM, FFmpeg/libass, and Google Drive.
              {connectivityCheckedAt ? ` Last checked: ${connectivityCheckedAt}` : ""}
            </CardDescription>
          </div>
          <Button
            variant="secondary"
            loading={testingConnectivity}
            onClick={handleTestConnectivity}
            leftIcon={<PlugZap className="h-4 w-4" aria-hidden="true" />}
          >
            {testingConnectivity
              ? en.admin.settingsUi.testingConnectivityCta
              : en.admin.settingsUi.testConnectivityCta}
          </Button>
        </CardHeader>

        <CardContent className="grid grid-cols-1 gap-3 sm:grid-cols-3 text-xs">
          <div className="rounded-sm border border-border bg-surface-2 p-3 flex items-center justify-between">
            <div>
              <p className="text-text-dim">LLM Provider</p>
              <p className="font-semibold text-text mt-0.5">
                {health?.models?.llm?.provider ?? llmProvider}
              </p>
            </div>
            <Badge tone={health?.models?.llm?.ok ? "ok" : "err"}>
              {health?.models?.llm?.ok ? "OK" : "CHECK"}
            </Badge>
          </div>

          <div className="rounded-sm border border-border bg-surface-2 p-3 flex items-center justify-between">
            <div>
              <p className="text-text-dim">FFmpeg + libass</p>
              <p className="font-semibold text-text mt-0.5">
                {health?.ffmpeg?.version ?? "—"}
              </p>
            </div>
            <Badge tone={health?.ffmpeg?.libass ? "ok" : "err"}>
              {health?.ffmpeg?.libass ? "OK" : "MISSING"}
            </Badge>
          </div>

          <div className="rounded-sm border border-border bg-surface-2 p-3 flex items-center justify-between">
            <div>
              <p className="text-text-dim">Google Drive</p>
              <p className="font-semibold text-text mt-0.5">
                {health?.drive?.status ?? "—"}
              </p>
            </div>
            <Badge
              tone={health?.drive?.status === "CONNECTED" ? "ok" : "warn"}
            >
              {health?.drive?.status ?? "UNKNOWN"}
            </Badge>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
