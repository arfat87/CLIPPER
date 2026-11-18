import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Archive,
  ArrowLeft,
  CheckCircle2,
  Copy,
  Download,
  Edit3,
  Eye,
  Info,
  Play,
  Send,
  Upload,
  X,
} from "lucide-react";
import { ApiError, apiClient } from "../../api/client";
import {
  useAdminArchiveTemplateMutation,
  useAdminDuplicateTemplateMutation,
  useAdminImportTemplateMutation,
  useAdminPublishTemplateMutation,
  useAdminTemplatesQuery,
  useDriveFilesQuery,
  usePreviewTemplateMutation,
} from "../../api/hooks";
import type {
  LanguageCode,
  TemplateKind,
  TemplateStatus,
  TemplateSummary,
} from "../../api/types";
import { en } from "../../i18n/en";
import {
  applyTextCase,
  type CaptionTemplateDoc,
  computeCaptionPreviewStyle,
  createDefaultTemplateDoc,
  normalizeTemplateDoc,
  PREVIEW_LIMITATIONS,
  SAFE_AREAS,
  type SafeAreaProfile,
  SAMPLE_LINES_BY_LANG,
} from "../../lib/captionPreview";
import {
  parseAndValidateTemplateJson,
  validateTemplateDoc,
} from "../../lib/templateValidation";
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

const KIND_OPTIONS: Array<TemplateKind | "all"> = [
  "all",
  "caption",
  "layout",
  "publish",
  "brand_kit",
  "overlay",
  "preset",
];

const STATUS_OPTIONS: Array<TemplateStatus | "all"> = [
  "all",
  "draft",
  "published",
  "archived",
  "invalid",
];

function buildInitialDocForSummary(tpl: TemplateSummary): CaptionTemplateDoc {
  const doc = createDefaultTemplateDoc(tpl.id, tpl.name);
  doc.meta.version = tpl.latest_version;
  if (tpl.id === "devanagari_gold") {
    doc.fonts.deva = {
      family: "Mukta",
      weight: 800,
      fallback: ["Noto Sans Devanagari"],
    };
    doc.colors.active = "#FFB020";
  } else if (tpl.id === "bengali_bold_wave") {
    doc.fonts.beng = {
      family: "Hind Siliguri",
      weight: 700,
      fallback: ["Noto Sans Bengali"],
    };
    doc.colors.active = "#3DDC84";
  } else if (tpl.id === "clean_minimal_box") {
    doc.colors.box = "#0B0D10";
    doc.box = { enabled: true, padding_px: 16, radius_px: 12, opacity: 0.85 };
  }
  return doc;
}

export function AdminTemplatesView() {
  const { toast } = useToast();
  const templatesQuery = useAdminTemplatesQuery();

  const importMutation = useAdminImportTemplateMutation();
  const publishMutation = useAdminPublishTemplateMutation();
  const archiveMutation = useAdminArchiveTemplateMutation();
  const duplicateMutation = useAdminDuplicateTemplateMutation();
  const previewMutation = usePreviewTemplateMutation();

  const [searchText, setSearchText] = useState("");
  const [kindFilter, setKindFilter] = useState<TemplateKind | "all">("all");
  const [statusFilter, setStatusFilter] = useState<TemplateStatus | "all">("all");

  // Import modal state
  const [importOpen, setImportOpen] = useState(false);
  const [importTab, setImportTab] = useState<"file" | "drive" | "paste">("paste");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importJsonText, setImportJsonText] = useState(() =>
    JSON.stringify(
      createDefaultTemplateDoc("neon_hinglish_draft", "Neon Hinglish Draft"),
      null,
      2
    )
  );
  const [importDriveId, setImportDriveId] = useState<string | null>(null);

  const driveTemplateFilesQuery = useDriveFilesQuery("template", {
    enabled: importOpen && importTab === "drive",
  });

  // Archive confirm modal state
  const [pendingArchive, setPendingArchive] = useState<TemplateSummary | null>(
    null
  );

  // Editor state
  const [editingTemplate, setEditingTemplate] = useState<TemplateSummary | null>(
    null
  );
  const [editorTab, setEditorTab] = useState<"form" | "json">("form");
  const [editorDoc, setEditorDoc] = useState<CaptionTemplateDoc>(() =>
    createDefaultTemplateDoc()
  );
  const [editorJsonText, setEditorJsonText] = useState<string>("");
  const [previewLang, setPreviewLang] = useState<Exclude<LanguageCode, "auto">>(
    "hinglish"
  );
  const [safeAreaProfile, setSafeAreaProfile] =
    useState<SafeAreaProfile>("shorts_default");
  const [warningsAcknowledged, setWarningsAcknowledged] = useState(false);
  const [serverFrameUrl, setServerFrameUrl] = useState<string | null>(null);
  const [showLimitations, setShowLimitations] = useState(false);

  const openEditor = (tpl: TemplateSummary, customDoc?: CaptionTemplateDoc) => {
    const doc = customDoc ?? buildInitialDocForSummary(tpl);
    setEditingTemplate(tpl);
    setEditorDoc(doc);
    setEditorJsonText(JSON.stringify(doc, null, 2));
    setEditorTab("form");
    setWarningsAcknowledged(false);
    setServerFrameUrl(null);
  };

  const updateDoc = (next: CaptionTemplateDoc) => {
    setEditorDoc(next);
    setEditorJsonText(JSON.stringify(next, null, 2));
    setWarningsAcknowledged(false);
  };

  const validation = useMemo(() => {
    if (editorTab === "json") {
      return parseAndValidateTemplateJson(editorJsonText).result;
    }
    return validateTemplateDoc(editorDoc);
  }, [editorTab, editorDoc, editorJsonText]);

  const handleJsonChange = (text: string) => {
    setEditorJsonText(text);
    setWarningsAcknowledged(false);
    const { doc } = parseAndValidateTemplateJson(text);
    if (doc) {
      setEditorDoc(doc);
    }
  };

  const handleImportSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    let fileToUpload: File | null = null;
    let parsedDoc: CaptionTemplateDoc | undefined;

    if (importTab === "file") {
      if (!importFile) {
        toast({
          title: en.states.errorTitle,
          description: "Choose a .json or .cliptpl.zip file first.",
          tone: "err",
        });
        return;
      }
      fileToUpload = importFile;
      if (importFile.name.endsWith(".json")) {
        const text = await importFile.text().catch(() => "");
        if (text) {
          try {
            parsedDoc = normalizeTemplateDoc(
              JSON.parse(text) as Record<string, unknown>
            );
          } catch {
            parsedDoc = undefined;
          }
        }
      }
    } else if (importTab === "drive") {
      if (!importDriveId) {
        toast({
          title: en.states.errorTitle,
          description: "Pick a Drive template package first.",
          tone: "err",
        });
        return;
      }
      const driveDoc = createDefaultTemplateDoc(
        `drive_${importDriveId}`,
        `Drive Imported (${importDriveId})`
      );
      parsedDoc = driveDoc;
      fileToUpload = new File(
        [JSON.stringify(driveDoc, null, 2)],
        `${importDriveId}.json`,
        { type: "application/json" }
      );
    } else {
      try {
        const raw = JSON.parse(importJsonText) as Record<string, unknown>;
        parsedDoc = normalizeTemplateDoc(raw);
      } catch (err) {
        toast({
          title: en.states.errorTitle,
          description:
            err instanceof Error ? err.message : "Invalid template JSON",
          tone: "err",
        });
        return;
      }
      fileToUpload = new File([importJsonText], `${parsedDoc.meta.id}.json`, {
        type: "application/json",
      });
    }

    importMutation.mutate(fileToUpload, {
      onSuccess: (created) => {
        setImportOpen(false);
        toast({
          title: en.admin.templatesUi.importSuccessToast,
          description: created.name,
          tone: "ok",
        });
        openEditor(created, parsedDoc);
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

  const handleDuplicate = (tpl: TemplateSummary) => {
    duplicateMutation.mutate(tpl.id, {
      onSuccess: (copy) => {
        toast({
          title: en.admin.templatesUi.duplicatedToast,
          description: copy.name,
          tone: "ok",
        });
        openEditor(copy);
      },
      onError: (err) => {
        toast({ title: en.states.errorTitle, description: err.message, tone: "err" });
      },
    });
  };

  const handleExport = async (tpl: TemplateSummary) => {
    try {
      const blob = await apiClient.adminExportTemplate(tpl.id);
      if (typeof URL !== "undefined" && typeof URL.createObjectURL === "function") {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${tpl.id}.json`;
        a.click();
        URL.revokeObjectURL(url);
      }
      toast({
        title: en.admin.templatesUi.exportedToast,
        description: `${tpl.id}.json`,
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

  const handleConfirmArchive = () => {
    if (!pendingArchive) return;
    archiveMutation.mutate(pendingArchive.id, {
      onSuccess: (archived) => {
        setPendingArchive(null);
        if (editingTemplate?.id === archived.id) {
          setEditingTemplate(archived);
        }
        toast({
          title: en.admin.templatesUi.archivedToast,
          description: archived.name,
          tone: "warn",
        });
      },
      onError: (err) => {
        setPendingArchive(null);
        toast({ title: en.states.errorTitle, description: err.message, tone: "err" });
      },
    });
  };

  const handlePublish = () => {
    if (!editingTemplate) return;
    if (!validation.valid) return;
    if (validation.warnings.length > 0 && !warningsAcknowledged) return;

    publishMutation.mutate(editingTemplate.id, {
      onSuccess: (published) => {
        setEditingTemplate(published);
        toast({
          title: en.admin.templatesUi.publishedToast,
          description: `${published.name} · v${published.latest_version}`,
          tone: "ok",
        });
      },
      onError: (err) => {
        toast({ title: en.states.errorTitle, description: err.message, tone: "err" });
      },
    });
  };

  const handleRenderTestFrame = () => {
    if (!editingTemplate) return;
    const sample = SAMPLE_LINES_BY_LANG[previewLang].words.join(" ");
    previewMutation.mutate(
      {
        id: editingTemplate.id,
        payload: { language: previewLang, sample_text: sample },
      },
      {
        onSuccess: ({ previewUrl }) => {
          setServerFrameUrl(previewUrl);
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

  if (templatesQuery.isLoading) {
    return <LoadingState />;
  }

  if (templatesQuery.isError) {
    return (
      <ErrorState
        title={en.admin.errorTitle}
        message={
          templatesQuery.error instanceof ApiError
            ? templatesQuery.error.message
            : en.states.errorBody
        }
        onRetry={() => void templatesQuery.refetch()}
      />
    );
  }

  const allItems = templatesQuery.data?.items ?? [];

  if (editingTemplate) {
    const sample = SAMPLE_LINES_BY_LANG[previewLang];
    const previewStyles = computeCaptionPreviewStyle(editorDoc, 240, previewLang);
    const safeArea = SAFE_AREAS[safeAreaProfile];
    const publishBlocked =
      !validation.valid ||
      (validation.warnings.length > 0 && !warningsAcknowledged);

    return (
      <div className="space-y-4">
        {/* Editor Top Bar */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-surface p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setEditingTemplate(null)}
              leftIcon={<ArrowLeft className="h-4 w-4" aria-hidden="true" />}
            >
              {en.admin.templatesUi.backToList}
            </Button>
            <h2 className="text-base font-bold text-text">
              {editorDoc.meta.name || editingTemplate.name}
            </h2>
            <StatusBadge status={editingTemplate.status} />
            <Badge tone="neutral" size="sm">
              v{editingTemplate.latest_version}
            </Badge>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setEditingTemplate((prev) =>
                  prev ? { ...prev, name: editorDoc.meta.name } : prev
                );
                toast({
                  title: en.admin.templatesUi.savedDraftToast,
                  tone: "ok",
                });
              }}
            >
              {en.admin.templatesUi.saveDraftCta}
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={publishBlocked}
              loading={publishMutation.isPending}
              onClick={handlePublish}
              leftIcon={<Send className="h-4 w-4" aria-hidden="true" />}
            >
              {publishMutation.isPending
                ? en.admin.templatesUi.publishingCta
                : en.admin.templatesUi.publishCta}
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
          {/* Left: Form + JSON + Validation */}
          <Card className="space-y-4 lg:col-span-7">
            <div className="flex items-center justify-between gap-2 border-b border-border pb-3">
              <div
                role="tablist"
                aria-label="Template editor mode"
                className="inline-flex rounded-sm border border-border bg-surface-2 p-1"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={editorTab === "form"}
                  onClick={() => setEditorTab("form")}
                  className={`min-h-[44px] rounded-sm px-3 text-xs font-semibold ${
                    editorTab === "form"
                      ? "bg-accent text-accent-fg"
                      : "text-text-dim hover:text-text"
                  }`}
                >
                  {en.admin.templatesUi.formTab}
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={editorTab === "json"}
                  onClick={() => setEditorTab("json")}
                  className={`min-h-[44px] rounded-sm px-3 text-xs font-semibold ${
                    editorTab === "json"
                      ? "bg-accent text-accent-fg"
                      : "text-text-dim hover:text-text"
                  }`}
                >
                  {en.admin.templatesUi.jsonTab}
                </button>
              </div>
              <span className="font-mono text-xs text-text-dim">
                {editorDoc.meta.id}
              </span>
            </div>

            {editorTab === "form" ? (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="space-y-1 text-xs">
                  <span className="text-text-dim">Template Name</span>
                  <input
                    type="text"
                    value={editorDoc.meta.name}
                    onChange={(e) =>
                      updateDoc({
                        ...editorDoc,
                        meta: { ...editorDoc.meta, name: e.target.value },
                      })
                    }
                    className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
                  />
                </label>

                <label className="space-y-1 text-xs">
                  <span className="text-text-dim">License</span>
                  <input
                    type="text"
                    value={editorDoc.meta.license}
                    onChange={(e) =>
                      updateDoc({
                        ...editorDoc,
                        meta: { ...editorDoc.meta, license: e.target.value },
                      })
                    }
                    className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
                  />
                </label>

                <label className="space-y-1 text-xs">
                  <span className="text-text-dim">Font Family (Latin)</span>
                  <input
                    type="text"
                    value={editorDoc.fonts.latn.family}
                    onChange={(e) =>
                      updateDoc({
                        ...editorDoc,
                        fonts: {
                          ...editorDoc.fonts,
                          latn: { ...editorDoc.fonts.latn, family: e.target.value },
                        },
                      })
                    }
                    className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
                  />
                </label>

                <label className="space-y-1 text-xs">
                  <span className="text-text-dim">Font Size (px @ 1080w)</span>
                  <input
                    type="number"
                    min={24}
                    max={200}
                    value={editorDoc.text.size_px}
                    onChange={(e) =>
                      updateDoc({
                        ...editorDoc,
                        text: {
                          ...editorDoc.text,
                          size_px: Number(e.target.value),
                        },
                      })
                    }
                    className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
                  />
                </label>

                <label className="space-y-1 text-xs">
                  <span className="text-text-dim">Primary Color</span>
                  <input
                    type="text"
                    value={editorDoc.colors.primary}
                    onChange={(e) =>
                      updateDoc({
                        ...editorDoc,
                        colors: { ...editorDoc.colors, primary: e.target.value },
                      })
                    }
                    className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 font-mono text-sm text-text"
                  />
                </label>

                <label className="space-y-1 text-xs">
                  <span className="text-text-dim">Highlight Color</span>
                  <input
                    type="text"
                    value={editorDoc.colors.active}
                    onChange={(e) =>
                      updateDoc({
                        ...editorDoc,
                        colors: { ...editorDoc.colors, active: e.target.value },
                      })
                    }
                    className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 font-mono text-sm text-text"
                  />
                </label>

                <label className="space-y-1 text-xs">
                  <span className="text-text-dim">Animation</span>
                  <select
                    value={editorDoc.animation.in ?? "pop"}
                    onChange={(e) =>
                      updateDoc({
                        ...editorDoc,
                        animation: {
                          ...editorDoc.animation,
                          in: e.target.value as CaptionTemplateDoc["animation"]["in"],
                        },
                      })
                    }
                    className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2.5 text-sm text-text"
                  >
                    <option value="none">None</option>
                    <option value="pop">Pop</option>
                    <option value="fade">Fade</option>
                    <option value="slide_up">Slide Up</option>
                    <option value="bounce">Bounce</option>
                    <option value="typewriter">Typewriter</option>
                  </select>
                </label>

                <label className="space-y-1 text-xs">
                  <span className="text-text-dim">Position (Anchor)</span>
                  <select
                    value={editorDoc.layout.anchor}
                    onChange={(e) =>
                      updateDoc({
                        ...editorDoc,
                        layout: {
                          ...editorDoc.layout,
                          anchor: e.target
                            .value as CaptionTemplateDoc["layout"]["anchor"],
                        },
                      })
                    }
                    className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2.5 text-sm text-text"
                  >
                    <option value="lower_third">Lower Third</option>
                    <option value="bottom_center">Bottom Center</option>
                    <option value="center">Center</option>
                    <option value="top_center">Top Center</option>
                  </select>
                </label>

                <label className="space-y-1 text-xs">
                  <span className="text-text-dim">Vertical Y (%)</span>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={editorDoc.layout.y_percent}
                    onChange={(e) =>
                      updateDoc({
                        ...editorDoc,
                        layout: {
                          ...editorDoc.layout,
                          y_percent: Number(e.target.value),
                        },
                      })
                    }
                    className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
                  />
                </label>

                <label className="space-y-1 text-xs">
                  <span className="text-text-dim">Outline Width (px)</span>
                  <input
                    type="number"
                    min={0}
                    max={20}
                    value={editorDoc.stroke.outline_px}
                    onChange={(e) =>
                      updateDoc({
                        ...editorDoc,
                        stroke: {
                          ...editorDoc.stroke,
                          outline_px: Number(e.target.value),
                        },
                      })
                    }
                    className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
                  />
                </label>
              </div>
            ) : (
              <div className="space-y-2">
                <label htmlFor="template-json-editor" className="sr-only">
                  Template JSON
                </label>
                <textarea
                  id="template-json-editor"
                  rows={16}
                  value={editorJsonText}
                  onChange={(e) => handleJsonChange(e.target.value)}
                  spellCheck={false}
                  className="w-full rounded-sm border border-border bg-bg p-3 font-mono text-xs text-text"
                />
              </div>
            )}

            {/* Inline Schema Validation */}
            <div className="space-y-2 border-t border-border pt-3">
              {validation.errors.length > 0 ? (
                <div
                  role="alert"
                  className="rounded-sm border border-err/50 bg-err/10 p-3 text-xs space-y-1"
                >
                  <p className="font-semibold text-err">
                    {en.admin.templatesUi.validationErrorsTitle}
                  </p>
                  <ul className="list-disc pl-4 text-text space-y-0.5">
                    {validation.errors.map((err) => (
                      <li key={err}>{err}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {validation.warnings.length > 0 ? (
                <div className="rounded-sm border border-warn/40 bg-warn/10 p-3 text-xs space-y-2">
                  <p className="flex items-center gap-1.5 font-semibold text-warn">
                    <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
                    <span>{en.admin.templatesUi.validationWarningsTitle}</span>
                  </p>
                  <ul className="list-disc pl-4 text-text-dim space-y-0.5">
                    {validation.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                  <label className="flex min-h-[44px] items-center gap-2 text-text">
                    <input
                      type="checkbox"
                      checked={warningsAcknowledged}
                      onChange={(e) => setWarningsAcknowledged(e.target.checked)}
                      className="h-4 w-4 accent-[#FFD400]"
                    />
                    <span>{en.admin.templatesUi.ackWarningsLabel}</span>
                  </label>
                </div>
              ) : null}

              {validation.valid && validation.warnings.length === 0 ? (
                <p className="flex items-center gap-1.5 text-xs font-medium text-ok">
                  <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                  <span>{en.admin.templatesUi.validationOk}</span>
                </p>
              ) : null}
            </div>
          </Card>

          {/* Right: Live 9:16 CSS Preview + Safe Area + Server Test Frame */}
          <Card className="space-y-4 lg:col-span-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Eye className="h-4 w-4 text-accent" aria-hidden="true" />
                <span className="text-xs font-bold uppercase tracking-wide text-text-dim">
                  9:16 CSS Preview
                </span>
              </div>
              <button
                type="button"
                onClick={() => setShowLimitations((v) => !v)}
                className="inline-flex min-h-[36px] items-center gap-1 text-[11px] text-text-dim hover:text-text"
              >
                <Info className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{en.admin.templatesUi.previewLimitationsTitle}</span>
              </button>
            </div>

            {showLimitations ? (
              <div className="rounded-sm border border-border bg-surface-2 p-2.5 text-[11px] text-text-dim space-y-1">
                <p>{en.admin.templatesUi.previewLimitationsBody}</p>
                <ul className="list-disc pl-4 space-y-0.5">
                  {PREVIEW_LIMITATIONS.map((lim) => (
                    <li key={lim}>{lim}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {/* Sample Language Switcher & Safe-area toggle */}
            <div className="grid grid-cols-2 gap-2">
              <label className="space-y-1 text-xs">
                <span className="text-text-dim">
                  {en.admin.templatesUi.sampleLangLabel}
                </span>
                <select
                  aria-label={en.admin.templatesUi.sampleLangLabel}
                  value={previewLang}
                  onChange={(e) =>
                    setPreviewLang(
                      e.target.value as Exclude<LanguageCode, "auto">
                    )
                  }
                  className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2 text-xs text-text"
                >
                  <option value="hinglish">Hinglish</option>
                  <option value="en">English</option>
                  <option value="hi">Hindi (हिन्दी)</option>
                  <option value="bn">Bengali (বাংলা)</option>
                </select>
              </label>

              <label className="space-y-1 text-xs">
                <span className="text-text-dim">
                  {en.admin.templatesUi.safeAreaLabel}
                </span>
                <select
                  aria-label={en.admin.templatesUi.safeAreaLabel}
                  value={safeAreaProfile}
                  onChange={(e) =>
                    setSafeAreaProfile(e.target.value as SafeAreaProfile)
                  }
                  className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2 text-xs text-text"
                >
                  {(Object.keys(SAFE_AREAS) as SafeAreaProfile[]).map((key) => (
                    <option key={key} value={key}>
                      {SAFE_AREAS[key].label}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {/* 9:16 CSS Frame */}
            <div
              data-testid="template-css-preview"
              className="relative mx-auto aspect-[9/16] w-full max-w-[240px] overflow-hidden rounded-md border border-border bg-bg"
            >
              {safeAreaProfile !== "none" ? (
                <div
                  aria-label={`Safe area overlay: ${safeArea.label}`}
                  className="pointer-events-none absolute border border-dashed border-ok/60"
                  style={{
                    top: `${safeArea.top_pct}%`,
                    bottom: `${safeArea.bottom_pct}%`,
                    left: `${safeArea.left_pct}%`,
                    right: `${safeArea.right_pct}%`,
                  }}
                >
                  <span className="bg-bg/80 px-1 text-[9px] font-mono text-ok">
                    {safeArea.label} safe
                  </span>
                </div>
              ) : null}

              <div
                className="absolute inset-x-3"
                style={previewStyles.containerStyle}
              >
                <p style={previewStyles.textStyle}>
                  {sample.words.map((word, idx) => {
                    const display = applyTextCase(
                      word,
                      editorDoc.text.case,
                      previewLang
                    );
                    const isActive = idx === sample.activeIdx;
                    return (
                      <span
                        key={`${word}-${idx}`}
                        style={isActive ? previewStyles.activeWordStyle : undefined}
                      >
                        {display}
                        {idx < sample.words.length - 1 ? " " : ""}
                      </span>
                    );
                  })}
                </p>
              </div>
            </div>

            <Button
              variant="secondary"
              fullWidth
              loading={previewMutation.isPending}
              onClick={handleRenderTestFrame}
              leftIcon={<Play className="h-4 w-4" aria-hidden="true" />}
            >
              {previewMutation.isPending
                ? en.admin.templatesUi.renderingTestCta
                : en.admin.templatesUi.renderTestCta}
            </Button>

            {serverFrameUrl ? (
              <div className="space-y-2 border-t border-border pt-3">
                <p className="text-xs font-semibold text-ok">
                  {en.admin.templatesUi.renderTestTitle}
                </p>
                <img
                  src={serverFrameUrl}
                  alt={en.admin.templatesUi.renderTestTitle}
                  className="mx-auto aspect-[9/16] w-full max-w-[200px] rounded-md border border-border object-cover"
                />
              </div>
            ) : null}
          </Card>
        </div>
      </div>
    );
  }

  const filteredItems = allItems.filter((tpl) => {
    if (kindFilter !== "all" && tpl.kind !== kindFilter) return false;
    if (statusFilter !== "all" && tpl.status !== statusFilter) return false;
    if (searchText.trim()) {
      const q = searchText.trim().toLowerCase();
      if (
        !tpl.name.toLowerCase().includes(q) &&
        !tpl.id.toLowerCase().includes(q)
      ) {
        return false;
      }
    }
    return true;
  });

  return (
    <div className="space-y-4">
      {/* Filter & Import Toolbar */}
      <div className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-1 flex-wrap items-center gap-2">
          <input
            type="search"
            aria-label={en.admin.templatesUi.searchPlaceholder}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            placeholder={en.admin.templatesUi.searchPlaceholder}
            className="min-h-[44px] min-w-[200px] flex-1 rounded-sm border border-border bg-surface-2 px-3 text-sm text-text placeholder:text-text-dim"
          />

          <select
            aria-label={en.admin.templatesUi.kindFilterLabel}
            value={kindFilter}
            onChange={(e) =>
              setKindFilter(e.target.value as TemplateKind | "all")
            }
            className="min-h-[44px] rounded-sm border border-border bg-surface-2 px-2.5 text-xs text-text"
          >
            {KIND_OPTIONS.map((k) => (
              <option key={k} value={k}>
                Kind: {k}
              </option>
            ))}
          </select>

          <select
            aria-label={en.admin.templatesUi.statusFilterLabel}
            value={statusFilter}
            onChange={(e) =>
              setStatusFilter(e.target.value as TemplateStatus | "all")
            }
            className="min-h-[44px] rounded-sm border border-border bg-surface-2 px-2.5 text-xs text-text"
          >
            {STATUS_OPTIONS.map((st) => (
              <option key={st} value={st}>
                Status: {st}
              </option>
            ))}
          </select>
        </div>

        <Button
          variant="primary"
          onClick={() => setImportOpen(true)}
          leftIcon={<Upload className="h-4 w-4" aria-hidden="true" />}
        >
          {en.admin.templatesUi.importButton}
        </Button>
      </div>

      {/* Template Grid */}
      {filteredItems.length === 0 ? (
        <EmptyState
          title={en.admin.emptyTitle}
          body={en.admin.emptyBody}
          actionLabel={en.admin.templatesUi.importButton}
          onAction={() => setImportOpen(true)}
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filteredItems.map((tpl) => (
            <Card key={tpl.id} className="flex flex-col justify-between gap-3">
              <div>
                <CardHeader>
                  <div className="min-w-0">
                    <CardTitle className="truncate">{tpl.name}</CardTitle>
                    <CardDescription className="font-mono">
                      {tpl.id}
                    </CardDescription>
                  </div>
                  <StatusBadge status={tpl.status} />
                </CardHeader>
                <div className="mt-2 space-y-1 text-xs text-text-dim">
                  <p>
                    Kind: <span className="text-text">{tpl.kind}</span> · v
                    {tpl.latest_version}
                  </p>
                  <p>
                    Source:{" "}
                    <span className="text-text">{tpl.source ?? "builtin"}</span>
                  </p>
                  {tpl.validation_error ? (
                    <p className="text-err">{tpl.validation_error}</p>
                  ) : null}
                </div>
              </div>

              <CardContent className="mt-0 flex flex-wrap gap-1.5 border-t border-border pt-3">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => openEditor(tpl)}
                  leftIcon={<Edit3 className="h-3.5 w-3.5" aria-hidden="true" />}
                >
                  {en.admin.templatesUi.editCta}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => handleDuplicate(tpl)}
                  leftIcon={<Copy className="h-3.5 w-3.5" aria-hidden="true" />}
                >
                  {en.admin.templatesUi.duplicateCta}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void handleExport(tpl)}
                  leftIcon={<Download className="h-3.5 w-3.5" aria-hidden="true" />}
                >
                  {en.admin.templatesUi.exportCta}
                </Button>
                {tpl.status !== "archived" ? (
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setPendingArchive(tpl)}
                    leftIcon={
                      <Archive className="h-3.5 w-3.5" aria-hidden="true" />
                    }
                  >
                    {en.admin.templatesUi.archiveCta}
                  </Button>
                ) : null}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Import Modal */}
      {importOpen ? (
        <div className="fixed inset-0 z-50 flex items-end justify-center p-4 sm:items-center">
          <div
            aria-hidden="true"
            onClick={() => setImportOpen(false)}
            className="fixed inset-0 bg-bg/80 backdrop-blur-sm"
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="import-tpl-title"
            className="relative z-10 w-full max-w-lg rounded-lg border border-border bg-surface p-5 shadow-2xl space-y-4"
          >
            <div className="flex items-start justify-between gap-2">
              <div>
                <h3 id="import-tpl-title" className="text-base font-bold text-text">
                  {en.admin.templatesUi.importModalTitle}
                </h3>
                <p className="mt-1 text-xs text-text-dim">
                  {en.admin.templatesUi.importModalDesc}
                </p>
              </div>
              <button
                type="button"
                aria-label={en.states.confirmCancel}
                onClick={() => setImportOpen(false)}
                className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-sm text-text-dim hover:bg-surface-2 hover:text-text"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>

            <div
              role="tablist"
              aria-label="Import source"
              className="grid grid-cols-3 rounded-sm border border-border bg-surface-2 p-1"
            >
              {(["file", "drive", "paste"] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  role="tab"
                  aria-selected={importTab === tab}
                  onClick={() => setImportTab(tab)}
                  className={`min-h-[44px] rounded-sm px-2 text-xs font-semibold ${
                    importTab === tab
                      ? "bg-accent text-accent-fg"
                      : "text-text-dim hover:text-text"
                  }`}
                >
                  {en.admin.templatesUi.importTabs[tab]}
                </button>
              ))}
            </div>

            <form onSubmit={handleImportSubmit} className="space-y-4">
              {importTab === "file" ? (
                <label className="block space-y-1 text-xs">
                  <span className="text-text-dim">Select .cliptpl.zip or .json</span>
                  <input
                    type="file"
                    accept=".json,.zip,.cliptpl.zip"
                    onChange={(e) => setImportFile(e.target.files?.[0] ?? null)}
                    className="block w-full rounded-sm border border-border bg-surface-2 p-2 text-xs text-text"
                  />
                </label>
              ) : importTab === "drive" ? (
                <div className="space-y-2">
                  {(driveTemplateFilesQuery.data?.items ?? []).map((item) => {
                    const id = String(item.id ?? "");
                    const name = String(item.name ?? id);
                    return (
                      <button
                        key={id}
                        type="button"
                        onClick={() => setImportDriveId(id)}
                        className={`flex min-h-[44px] w-full items-center justify-between rounded-sm border px-3 text-xs ${
                          importDriveId === id
                            ? "border-accent bg-accent/10 text-accent"
                            : "border-border bg-surface-2 text-text"
                        }`}
                      >
                        <span>{name}</span>
                        <Badge tone="route" size="sm">
                          template
                        </Badge>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <label className="block space-y-1 text-xs">
                  <span className="text-text-dim">Template JSON</span>
                  <textarea
                    rows={8}
                    value={importJsonText}
                    onChange={(e) => setImportJsonText(e.target.value)}
                    placeholder={en.admin.templatesUi.importPastePlaceholder}
                    className="w-full rounded-sm border border-border bg-bg p-2.5 font-mono text-xs text-text"
                  />
                </label>
              )}

              <div className="flex justify-end gap-2">
                <Button
                  variant="secondary"
                  onClick={() => setImportOpen(false)}
                >
                  {en.states.confirmCancel}
                </Button>
                <Button
                  type="submit"
                  loading={importMutation.isPending}
                >
                  {importMutation.isPending
                    ? en.admin.templatesUi.importing
                    : en.admin.templatesUi.importSubmit}
                </Button>
              </div>
            </form>
          </div>
        </div>
      ) : null}

      <ConfirmModal
        open={Boolean(pendingArchive)}
        title={en.admin.templatesUi.archiveConfirmTitle}
        description={en.admin.templatesUi.archiveConfirmBody}
        confirmLabel={en.admin.templatesUi.archiveConfirmCta}
        variant="destructive"
        loading={archiveMutation.isPending}
        onConfirm={handleConfirmArchive}
        onCancel={() => setPendingArchive(null)}
      />
    </div>
  );
}
