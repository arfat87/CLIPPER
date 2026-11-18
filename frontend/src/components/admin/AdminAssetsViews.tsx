import { useState } from "react";
import { CheckCircle2, Plus, Sparkles, Star, Upload } from "lucide-react";
import { ApiError } from "../../api/client";
import {
  useAdminBrandKitsQuery,
  useAdminFontsQuery,
  useAdminPresetsQuery,
  useAdminSaveBrandKitMutation,
  useAdminSavePresetMutation,
  useAdminTemplatesQuery,
  useAdminUploadFontMutation,
} from "../../api/hooks";
import type { Preset } from "../../api/types";
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

export function AdminPresetsView() {
  const { toast } = useToast();
  const presetsQuery = useAdminPresetsQuery();
  const templatesQuery = useAdminTemplatesQuery();
  const savePresetMutation = useAdminSavePresetMutation();

  const [name, setName] = useState("");
  const [captionTpl, setCaptionTpl] = useState("bold_pop_yellow");
  const [layoutTpl, setLayoutTpl] = useState("split_face_9x16");
  const [publishTpl, setPublishTpl] = useState("shorts_hinglish_pack");
  const [renderProfile, setRenderProfile] = useState("1080p_h264_fast");

  if (presetsQuery.isLoading) return <LoadingState />;
  if (presetsQuery.isError) {
    return (
      <ErrorState
        title={en.admin.errorTitle}
        message={
          presetsQuery.error instanceof ApiError
            ? presetsQuery.error.message
            : en.states.errorBody
        }
        onRetry={() => void presetsQuery.refetch()}
      />
    );
  }

  const presets = presetsQuery.data?.items ?? [];
  const templates = templatesQuery.data?.items ?? [];
  const captionTemplates = templates.filter((t) => t.kind === "caption");
  const layoutTemplates = templates.filter((t) => t.kind === "layout");
  const publishTemplates = templates.filter((t) => t.kind === "publish");

  const handleCreatePreset = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    const id = name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_|_$/g, "");
    const payload: Preset = {
      id: id || `preset_${Date.now().toString(36)}`,
      name: name.trim(),
      version: 1,
      caption: captionTpl,
      layout: layoutTpl,
      brand_kit: null,
      overlays: ["hook_title", "progress_bar"],
      publish: publishTpl,
      render_profile: renderProfile,
      is_default: false,
    };
    savePresetMutation.mutate(payload, {
      onSuccess: (saved) => {
        setName("");
        toast({
          title: en.admin.presetsUi.savedToast,
          description: saved.name,
          tone: "ok",
        });
      },
    });
  };

  const handleSetDefault = (preset: Preset) => {
    savePresetMutation.mutate(
      { ...preset, is_default: true },
      {
        onSuccess: (saved) => {
          toast({
            title: en.admin.presetsUi.defaultToast,
            description: saved.name,
            tone: "ok",
          });
        },
      }
    );
  };

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>{en.admin.presetsUi.createTitle}</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleCreatePreset} className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label className="space-y-1 text-xs">
              <span className="text-text-dim">{en.admin.presetsUi.nameLabel}</span>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Hindi Reels Fast"
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
              />
            </label>

            <label className="space-y-1 text-xs">
              <span className="text-text-dim">{en.admin.presetsUi.captionLabel}</span>
              <select
                value={captionTpl}
                onChange={(e) => setCaptionTpl(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2 text-xs text-text"
              >
                {(captionTemplates.length > 0
                  ? captionTemplates
                  : [{ id: "bold_pop_yellow", name: "Bold Pop — Yellow" }]
                ).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="space-y-1 text-xs">
              <span className="text-text-dim">{en.admin.presetsUi.layoutLabel}</span>
              <select
                value={layoutTpl}
                onChange={(e) => setLayoutTpl(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2 text-xs text-text"
              >
                {(layoutTemplates.length > 0
                  ? layoutTemplates
                  : [{ id: "split_face_9x16", name: "9:16 Face-Track Crop" }]
                ).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="space-y-1 text-xs">
              <span className="text-text-dim">{en.admin.presetsUi.publishLabel}</span>
              <select
                value={publishTpl}
                onChange={(e) => setPublishTpl(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2 text-xs text-text"
              >
                {(publishTemplates.length > 0
                  ? publishTemplates
                  : [{ id: "shorts_hinglish_pack", name: "YouTube Shorts Hinglish Pack" }]
                ).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="space-y-1 text-xs">
              <span className="text-text-dim">{en.admin.presetsUi.profileLabel}</span>
              <select
                value={renderProfile}
                onChange={(e) => setRenderProfile(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2 text-xs text-text"
              >
                <option value="1080p_h264_fast">1080p_h264_fast</option>
                <option value="1080p_h264_hq">1080p_h264_hq</option>
                <option value="720p_h264_fast">720p_h264_fast</option>
              </select>
            </label>

            <div className="flex items-end">
              <Button
                type="submit"
                fullWidth
                loading={savePresetMutation.isPending}
                leftIcon={<Plus className="h-4 w-4" aria-hidden="true" />}
              >
                {en.admin.presetsUi.saveCta}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {presets.length === 0 ? (
        <EmptyState title={en.admin.emptyTitle} body={en.admin.emptyBody} />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {presets.map((preset) => (
            <Card key={preset.id} selected={Boolean(preset.is_default)}>
              <CardHeader>
                <div>
                  <CardTitle>{preset.name}</CardTitle>
                  <CardDescription className="font-mono">
                    {preset.id} · v{preset.version}
                  </CardDescription>
                </div>
                {preset.is_default ? (
                  <Badge tone="accent">{en.templates.activePreset}</Badge>
                ) : (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => handleSetDefault(preset)}
                    leftIcon={<Star className="h-3.5 w-3.5" aria-hidden="true" />}
                  >
                    {en.admin.presetsUi.setDefaultCta}
                  </Button>
                )}
              </CardHeader>
              <CardContent className="space-y-1 text-xs text-text-dim">
                <p>Caption: {preset.caption}</p>
                <p>Layout: {preset.layout}</p>
                <p>Publish: {preset.publish}</p>
                <p>Render profile: {preset.render_profile}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

export function AdminFontsView() {
  const { toast } = useToast();
  const fontsQuery = useAdminFontsQuery();
  const uploadFontMutation = useAdminUploadFontMutation();

  const [fontFile, setFontFile] = useState<File | null>(null);
  const [license, setLicense] = useState("OFL-1.1");
  const [fallbackOverrides, setFallbackOverrides] = useState<
    Record<string, boolean>
  >({});

  if (fontsQuery.isLoading) return <LoadingState />;
  if (fontsQuery.isError) {
    return (
      <ErrorState
        title={en.admin.errorTitle}
        message={
          fontsQuery.error instanceof ApiError
            ? fontsQuery.error.message
            : en.states.errorBody
        }
        onRetry={() => void fontsQuery.refetch()}
      />
    );
  }

  const fonts = fontsQuery.data?.items ?? [];

  const handleUpload = (e: React.FormEvent) => {
    e.preventDefault();
    const targetFile =
      fontFile ??
      new File(["mock-ttf"], "NotoSansDevanagari-Bold.ttf", {
        type: "font/ttf",
      });
    uploadFontMutation.mutate(
      { file: targetFile, license },
      {
        onSuccess: () => {
          setFontFile(null);
          toast({
            title: en.admin.fontsUi.uploadedToast,
            description: targetFile.name,
            tone: "ok",
          });
        },
      }
    );
  };

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>{en.admin.fontsUi.uploadTitle}</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleUpload} className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label className="space-y-1 text-xs">
              <span className="text-text-dim">{en.admin.fontsUi.fileLabel}</span>
              <input
                type="file"
                accept=".ttf,.otf,.woff2"
                onChange={(e) => setFontFile(e.target.files?.[0] ?? null)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2 py-2 text-xs text-text"
              />
            </label>

            <label className="space-y-1 text-xs">
              <span className="text-text-dim">{en.admin.fontsUi.licenseLabel}</span>
              <input
                type="text"
                value={license}
                onChange={(e) => setLicense(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
              />
            </label>

            <div className="flex items-end">
              <Button
                type="submit"
                fullWidth
                loading={uploadFontMutation.isPending}
                leftIcon={<Upload className="h-4 w-4" aria-hidden="true" />}
              >
                {en.admin.fontsUi.uploadCta}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {fonts.length === 0 ? (
        <EmptyState title={en.admin.emptyTitle} body={en.admin.emptyBody} />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {fonts.map((font) => {
            const id = String(font.id ?? font.filename ?? "font");
            const family = String(font.family ?? id);
            const filename = String(font.filename ?? "");
            const licenseText = String(font.license ?? "UNKNOWN");
            const licenseOk = Boolean(font.license_ok);
            const coverage = (font.coverage ?? {}) as Record<string, boolean>;
            const isDefaultFallback =
              fallbackOverrides[id] ?? Boolean(font.is_default_fallback);

            return (
              <Card key={id} className="space-y-3">
                <CardHeader>
                  <div>
                    <CardTitle>{family}</CardTitle>
                    <CardDescription className="font-mono">
                      {filename} · {licenseText}
                    </CardDescription>
                  </div>
                  <Badge tone={licenseOk ? "ok" : "warn"}>
                    {licenseOk
                      ? en.admin.fontsUi.licenseOk
                      : en.admin.fontsUi.licenseWarn}
                  </Badge>
                </CardHeader>

                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap gap-1.5">
                    {(["latn", "deva", "beng"] as const).map((script) => (
                      <Badge
                        key={script}
                        tone={coverage[script] ? "ok" : "neutral"}
                        size="sm"
                      >
                        {coverage[script] ? (
                          <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                        ) : null}
                        <span>{script}</span>
                      </Badge>
                    ))}
                  </div>

                  <Button
                    size="sm"
                    variant={isDefaultFallback ? "primary" : "secondary"}
                    onClick={() => {
                      setFallbackOverrides((prev) => ({
                        ...prev,
                        [id]: !isDefaultFallback,
                      }));
                      toast({
                        title: en.admin.fontsUi.fallbackToggledToast,
                        description: family,
                        tone: "ok",
                      });
                    }}
                  >
                    {en.admin.fontsUi.defaultFallbackCta}:{" "}
                    {isDefaultFallback ? "ON" : "OFF"}
                  </Button>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function AdminBrandKitsView() {
  const { toast } = useToast();
  const brandKitsQuery = useAdminBrandKitsQuery();
  const presetsQuery = useAdminPresetsQuery();
  const saveBrandKitMutation = useAdminSaveBrandKitMutation();

  const [name, setName] = useState("");
  const [primaryColor, setPrimaryColor] = useState("#FFD400");
  const [accentColor, setAccentColor] = useState("#141820");
  const [watermarkText, setWatermarkText] = useState("@clipper.studio");
  const [logoAsset, setLogoAsset] = useState("studio_logo.png");
  const [defaultPresetId, setDefaultPresetId] = useState("default");

  if (brandKitsQuery.isLoading) return <LoadingState />;
  if (brandKitsQuery.isError) {
    return (
      <ErrorState
        title={en.admin.errorTitle}
        message={
          brandKitsQuery.error instanceof ApiError
            ? brandKitsQuery.error.message
            : en.states.errorBody
        }
        onRetry={() => void brandKitsQuery.refetch()}
      />
    );
  }

  const kits = brandKitsQuery.data?.items ?? [];
  const presets = presetsQuery.data?.items ?? [];

  const handleSaveKit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    saveBrandKitMutation.mutate(
      {
        name: name.trim(),
        primary_color: primaryColor,
        accent_color: accentColor,
        watermark_text: watermarkText,
        logo_asset: logoAsset,
        default_preset_id: defaultPresetId,
      },
      {
        onSuccess: () => {
          setName("");
          toast({
            title: en.admin.brandKitsUi.savedToast,
            description: name.trim(),
            tone: "ok",
          });
        },
      }
    );
  };

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>{en.admin.brandKitsUi.createTitle}</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSaveKit} className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label className="space-y-1 text-xs">
              <span className="text-text-dim">{en.admin.brandKitsUi.nameLabel}</span>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Podcast Gold Kit"
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
              />
            </label>

            <label className="space-y-1 text-xs">
              <span className="text-text-dim">
                {en.admin.brandKitsUi.primaryColorLabel}
              </span>
              <input
                type="text"
                value={primaryColor}
                onChange={(e) => setPrimaryColor(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 font-mono text-sm text-text"
              />
            </label>

            <label className="space-y-1 text-xs">
              <span className="text-text-dim">
                {en.admin.brandKitsUi.accentColorLabel}
              </span>
              <input
                type="text"
                value={accentColor}
                onChange={(e) => setAccentColor(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 font-mono text-sm text-text"
              />
            </label>

            <label className="space-y-1 text-xs">
              <span className="text-text-dim">
                {en.admin.brandKitsUi.watermarkLabel}
              </span>
              <input
                type="text"
                value={watermarkText}
                onChange={(e) => setWatermarkText(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
              />
            </label>

            <label className="space-y-1 text-xs">
              <span className="text-text-dim">{en.admin.brandKitsUi.logoLabel}</span>
              <input
                type="text"
                value={logoAsset}
                onChange={(e) => setLogoAsset(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-3 text-sm text-text"
              />
            </label>

            <label className="space-y-1 text-xs">
              <span className="text-text-dim">
                {en.admin.brandKitsUi.defaultPresetLabel}
              </span>
              <select
                value={defaultPresetId}
                onChange={(e) => setDefaultPresetId(e.target.value)}
                className="min-h-[44px] w-full rounded-sm border border-border bg-surface-2 px-2 text-xs text-text"
              >
                {(presets.length > 0
                  ? presets
                  : [{ id: "default", name: "Viral Hinglish 9:16" }]
                ).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>

            <div className="sm:col-span-3 flex justify-end">
              <Button
                type="submit"
                loading={saveBrandKitMutation.isPending}
                leftIcon={<Sparkles className="h-4 w-4" aria-hidden="true" />}
              >
                {en.admin.brandKitsUi.saveCta}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {kits.length === 0 ? (
        <EmptyState title={en.admin.emptyTitle} body={en.admin.emptyBody} />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {kits.map((kit) => {
            const id = String(kit.id ?? kit.name ?? "kit");
            const kitName = String(kit.name ?? id);
            const pColor = String(kit.primary_color ?? "#FFD400");
            const aColor = String(kit.accent_color ?? "#141820");
            return (
              <Card key={id} className="space-y-3">
                <CardHeader>
                  <div>
                    <CardTitle>{kitName}</CardTitle>
                    <CardDescription className="font-mono">{id}</CardDescription>
                  </div>
                  <Badge tone="route" size="sm">
                    {String(kit.default_preset_id ?? "default")}
                  </Badge>
                </CardHeader>
                <div className="flex items-center gap-3 text-xs text-text-dim">
                  <span className="inline-flex items-center gap-1.5">
                    <span
                      className="inline-block h-4 w-4 rounded-sm border border-border"
                      style={{ backgroundColor: pColor }}
                    />
                    {pColor}
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span
                      className="inline-block h-4 w-4 rounded-sm border border-border"
                      style={{ backgroundColor: aColor }}
                    />
                    {aColor}
                  </span>
                </div>
                <div className="text-xs text-text-dim space-y-0.5">
                  <p>Watermark: {String(kit.watermark_text ?? "—")}</p>
                  <p>Logo: {String(kit.logo_asset ?? "—")}</p>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
