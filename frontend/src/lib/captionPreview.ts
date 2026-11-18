import type { CSSProperties } from "react";
import type { LanguageCode } from "../api/types";

export interface FontSpec {
  family: string;
  weight: number;
  italic?: boolean;
  fallback?: string[];
}

export interface CaptionTemplateDoc {
  schema_version: 2;
  meta: {
    id: string;
    name: string;
    version: number;
    author?: string;
    license: string;
    tags?: string[];
  };
  aspects: Array<"9:16" | "1:1" | "4:5">;
  fonts: {
    latn: FontSpec;
    deva: FontSpec;
    beng: FontSpec;
  };
  text: {
    size_px: number;
    case: "none" | "upper" | "lower" | "title";
    letter_spacing_px?: number;
    line_height?: number;
  };
  colors: {
    primary: string;
    active: string;
    outline: string;
    shadow?: string;
    box?: string | null;
  };
  stroke: {
    outline_px: number;
    shadow_px: number;
    shadow_offset?: [number, number];
  };
  box?: {
    enabled?: boolean;
    padding_px?: number;
    radius_px?: number;
    opacity?: number;
  };
  layout: {
    anchor: "bottom_center" | "center" | "top_center" | "lower_third";
    y_percent: number;
    align: "left" | "center" | "right";
    max_words_per_line: number;
    max_lines: number;
    safe_area_profile?: string;
  };
  animation: {
    mode: "pages" | "word_by_word" | "karaoke";
    active_word?: "none" | "highlight" | "scale" | "underline";
    in?: "none" | "pop" | "fade" | "slide_up" | "typewriter" | "bounce";
    out?: "none" | "fade" | "slide_down";
    duration_ms?: number;
  };
}

export type SafeAreaProfile =
  | "none"
  | "shorts_default"
  | "reels_default"
  | "tiktok_default";

export const SAFE_AREAS: Record<
  SafeAreaProfile,
  { label: string; top_pct: number; bottom_pct: number; left_pct: number; right_pct: number }
> = {
  none: { label: "Off", top_pct: 0, bottom_pct: 0, left_pct: 0, right_pct: 0 },
  shorts_default: { label: "Shorts", top_pct: 12, bottom_pct: 25, left_pct: 6, right_pct: 12 },
  reels_default: { label: "Reels", top_pct: 14, bottom_pct: 25, left_pct: 6, right_pct: 12 },
  tiktok_default: { label: "TikTok", top_pct: 14, bottom_pct: 28, left_pct: 6, right_pct: 14 },
};

export const SAMPLE_LINES_BY_LANG: Record<
  Exclude<LanguageCode, "auto">,
  { words: string[]; activeIdx: number }
> = {
  en: {
    words: ["90% of", "CREATORS", "skip this hook"],
    activeIdx: 1,
  },
  hinglish: {
    words: ["90% startups yahi", "GALTI", "karte hain!"],
    activeIdx: 1,
  },
  hi: {
    words: ["पहले ३ सेकंड में", "ध्यान", "खींचना ज़रूरी है"],
    activeIdx: 1,
  },
  bn: {
    words: ["এক ক্লিকেই", "ভিডিও", "ভাইরাল হবে!"],
    activeIdx: 1,
  },
};

export const PREVIEW_LIMITATIONS = [
  "HarfBuzz complex Indic ligatures and kerning are approximated by the browser.",
  "libass glow blur radius and multi-pass shadow falloff differ slightly from CSS text-shadow.",
  "Karaoke sweep timing and exact safe-area clamping are authoritative in server test frames.",
] as const;

export function createDefaultTemplateDoc(
  id = "custom_caption_draft",
  name = "Custom Caption Draft"
): CaptionTemplateDoc {
  return {
    schema_version: 2,
    meta: {
      id,
      name,
      version: 1,
      author: "owner",
      license: "OFL-1.1 / MIT",
      tags: ["shorts", "custom"],
    },
    aspects: ["9:16", "1:1", "4:5"],
    fonts: {
      latn: { family: "Montserrat", weight: 800, fallback: ["Inter", "Noto Sans"] },
      deva: { family: "Mukta", weight: 800, fallback: ["Noto Sans Devanagari"] },
      beng: { family: "Hind Siliguri", weight: 700, fallback: ["Noto Sans Bengali"] },
    },
    text: {
      size_px: 76,
      case: "upper",
      letter_spacing_px: 0,
      line_height: 1.15,
    },
    colors: {
      primary: "#FFFFFF",
      active: "#FFD400",
      outline: "#000000",
      shadow: "#000000CC",
      box: null,
    },
    stroke: {
      outline_px: 6,
      shadow_px: 3,
      shadow_offset: [2, 3],
    },
    box: {
      enabled: false,
      padding_px: 14,
      radius_px: 10,
      opacity: 0.85,
    },
    layout: {
      anchor: "lower_third",
      y_percent: 68,
      align: "center",
      max_words_per_line: 3,
      max_lines: 2,
      safe_area_profile: "shorts_default",
    },
    animation: {
      mode: "pages",
      active_word: "highlight",
      in: "pop",
      out: "none",
      duration_ms: 160,
    },
  };
}

/**
 * Upgrade v1 caption template JSON (used in starter assets) into v2 shape for editor/preview.
 */
export function normalizeTemplateDoc(raw: Record<string, unknown>): CaptionTemplateDoc {
  if (raw.schema_version === 2 && raw.meta && raw.fonts && raw.text && raw.stroke) {
    return raw as unknown as CaptionTemplateDoc;
  }

  const base = createDefaultTemplateDoc(
    typeof raw.id === "string" ? raw.id : "imported_template",
    typeof raw.name === "string" ? raw.name : "Imported Template"
  );

  const rawFonts = (raw.fonts ?? {}) as Record<string, unknown>;
  const toFont = (val: unknown, fallbackFamily: string): FontSpec =>
    typeof val === "string"
      ? { family: val === "Sans" ? fallbackFamily : val, weight: 800, fallback: [fallbackFamily] }
      : typeof val === "object" && val !== null
        ? (val as FontSpec)
        : { family: fallbackFamily, weight: 800 };

  const rawColors = (raw.colors ?? {}) as Record<string, unknown>;
  const rawLayout = (raw.layout ?? {}) as Record<string, unknown>;
  const rawAnim = (raw.animation ?? {}) as Record<string, unknown>;
  const rawBox = (raw.box ?? {}) as Record<string, unknown>;

  return {
    ...base,
    meta: {
      ...base.meta,
      version: typeof raw.version === "number" ? raw.version : 1,
      license: typeof raw.license === "string" ? raw.license : base.meta.license,
    },
    fonts: {
      latn: toFont(rawFonts.latn, "Montserrat"),
      deva: toFont(rawFonts.deva, "Noto Sans Devanagari"),
      beng: toFont(rawFonts.beng, "Noto Sans Bengali"),
    },
    text: {
      ...base.text,
      size_px: typeof raw.size_px === "number" ? raw.size_px : base.text.size_px,
      case:
        raw.case === "none" ||
        raw.case === "upper" ||
        raw.case === "lower" ||
        raw.case === "title"
          ? raw.case
          : base.text.case,
    },
    colors: {
      primary:
        typeof rawColors.primary === "string" ? rawColors.primary : base.colors.primary,
      active:
        typeof rawColors.active === "string" ? rawColors.active : base.colors.active,
      outline:
        typeof rawColors.outline === "string" ? rawColors.outline : base.colors.outline,
      shadow:
        typeof rawColors.shadow === "string" ? rawColors.shadow : base.colors.shadow,
      box: typeof rawColors.box === "string" ? rawColors.box : null,
    },
    stroke: {
      outline_px:
        typeof raw.outline_px === "number" ? raw.outline_px : base.stroke.outline_px,
      shadow_px:
        typeof raw.shadow_px === "number" ? raw.shadow_px : base.stroke.shadow_px,
      shadow_offset: [2, 3],
    },
    box: {
      enabled: Boolean(rawBox.enabled),
      padding_px: typeof rawBox.padding_px === "number" ? rawBox.padding_px : 14,
      radius_px: typeof rawBox.radius_px === "number" ? rawBox.radius_px : 10,
      opacity: typeof rawBox.opacity === "number" ? rawBox.opacity : 0.85,
    },
    layout: {
      ...base.layout,
      anchor:
        rawLayout.position === "top_center" ||
        rawLayout.position === "center" ||
        rawLayout.position === "bottom_center" ||
        rawLayout.position === "lower_third"
          ? rawLayout.position
          : base.layout.anchor,
      y_percent:
        typeof rawLayout.y_percent === "number"
          ? rawLayout.y_percent
          : base.layout.y_percent,
      align:
        rawLayout.align === "left" ||
        rawLayout.align === "center" ||
        rawLayout.align === "right"
          ? rawLayout.align
          : "center",
    },
    animation: {
      ...base.animation,
      in:
        rawAnim.in === "none" ||
        rawAnim.in === "pop" ||
        rawAnim.in === "fade" ||
        rawAnim.in === "slide_up" ||
        rawAnim.in === "typewriter" ||
        rawAnim.in === "bounce"
          ? rawAnim.in
          : "pop",
    },
  };
}

export function applyTextCase(
  input: string,
  textCase: CaptionTemplateDoc["text"]["case"],
  lang: Exclude<LanguageCode, "auto">
): string {
  if (lang === "hi" || lang === "bn") {
    return input;
  }
  if (textCase === "upper") return input.toUpperCase();
  if (textCase === "lower") return input.toLowerCase();
  if (textCase === "title") {
    return input.replace(/\b\w/g, (ch) => ch.toUpperCase());
  }
  return input;
}

export function computeCaptionPreviewStyle(
  doc: CaptionTemplateDoc,
  containerWidthPx = 220,
  lang: Exclude<LanguageCode, "auto"> = "hinglish"
): {
  containerStyle: CSSProperties;
  textStyle: CSSProperties;
  activeWordStyle: CSSProperties;
} {
  const scale = Math.max(containerWidthPx / 1080, 0.14);
  const fontSize = Math.round(doc.text.size_px * scale);
  const outlinePx = Math.max(1, Math.round(doc.stroke.outline_px * scale));
  const shadowPx = Math.round(doc.stroke.shadow_px * scale);

  const fontSpec =
    lang === "hi"
      ? doc.fonts.deva
      : lang === "bn"
        ? doc.fonts.beng
        : doc.fonts.latn;
  const fallbackList = [
    fontSpec.family,
    ...(fontSpec.fallback ?? []),
    "Noto Sans Devanagari",
    "Noto Sans Bengali",
    "sans-serif",
  ];

  const shadowOffsets = [
    `${-outlinePx}px ${-outlinePx}px 0 ${doc.colors.outline}`,
    `${outlinePx}px ${-outlinePx}px 0 ${doc.colors.outline}`,
    `${-outlinePx}px ${outlinePx}px 0 ${doc.colors.outline}`,
    `${outlinePx}px ${outlinePx}px 0 ${doc.colors.outline}`,
    `0 ${Math.max(1, shadowPx)}px ${Math.max(2, shadowPx * 2)}px ${doc.colors.shadow ?? "#00000099"}`,
  ].join(", ");

  const yPct =
    doc.layout.anchor === "top_center"
      ? Math.min(doc.layout.y_percent, 25)
      : doc.layout.anchor === "center"
        ? 50
        : doc.layout.y_percent;

  const boxEnabled = Boolean(doc.box?.enabled && doc.colors.box);

  return {
    containerStyle: {
      top: `${yPct}%`,
      transform: "translateY(-50%)",
      textAlign: doc.layout.align,
    },
    textStyle: {
      fontFamily: fallbackList.map((f) => `"${f}"`).join(", "),
      fontWeight: fontSpec.weight,
      fontSize: `${Math.max(11, fontSize)}px`,
      lineHeight: doc.text.line_height ?? 1.15,
      letterSpacing: `${(doc.text.letter_spacing_px ?? 0) * scale}px`,
      color: doc.colors.primary,
      textShadow: shadowOffsets,
      ...(boxEnabled
        ? {
            backgroundColor: doc.colors.box ?? "rgba(0,0,0,0.75)",
            padding: `${Math.max(4, Math.round((doc.box?.padding_px ?? 12) * scale))}px ${Math.max(6, Math.round((doc.box?.padding_px ?? 12) * scale * 1.4))}px`,
            borderRadius: `${Math.round((doc.box?.radius_px ?? 8) * scale)}px`,
          }
        : {}),
    },
    activeWordStyle: {
      color: doc.colors.active,
      textDecoration:
        doc.animation.active_word === "underline" ? "underline" : undefined,
      transform:
        doc.animation.active_word === "scale" ? "scale(1.08)" : undefined,
      display: "inline-block",
    },
  };
}
