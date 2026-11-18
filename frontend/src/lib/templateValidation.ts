import type { CaptionTemplateDoc } from "./captionPreview";

export interface TemplateValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

const HEX_COLOR_RE = /^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$/;
const ID_RE = /^[a-z][a-z0-9_]{2,47}$/;

function hexToRgb(hex: string): [number, number, number] | null {
  if (!HEX_COLOR_RE.test(hex)) return null;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return [r, g, b];
}

function relativeLuminance(rgb: [number, number, number]): number {
  const [r, g, b] = rgb.map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatio(hexA: string, hexB: string): number | null {
  const a = hexToRgb(hexA);
  const b = hexToRgb(hexB);
  if (!a || !b) return null;
  const lumA = relativeLuminance(a);
  const lumB = relativeLuminance(b);
  const bright = Math.max(lumA, lumB);
  const dark = Math.min(lumA, lumB);
  return (bright + 0.05) / (dark + 0.05);
}

export function validateTemplateDoc(doc: CaptionTemplateDoc): TemplateValidationResult {
  const errors: string[] = [];
  const warnings: string[] = [];

  if (!doc.meta?.id || !ID_RE.test(doc.meta.id)) {
    errors.push(
      "meta.id must be 3-48 lowercase snake_case characters starting with a letter."
    );
  }
  if (!doc.meta?.name?.trim()) {
    errors.push("meta.name is required.");
  }
  if (!doc.meta?.license?.trim()) {
    errors.push("meta.license is required.");
  } else if (doc.meta.license.toUpperCase().includes("UNKNOWN")) {
    warnings.push(
      "Font/template license is marked UNKNOWN — verify redistribution rights before publishing."
    );
  }

  if (!Array.isArray(doc.aspects) || doc.aspects.length === 0) {
    errors.push("At least one supported aspect ratio (9:16, 1:1, 4:5) is required.");
  }

  for (const script of ["latn", "deva", "beng"] as const) {
    if (!doc.fonts?.[script]?.family?.trim()) {
      errors.push(`fonts.${script}.family is required for multi-script captions.`);
    }
  }

  if (
    typeof doc.text?.size_px !== "number" ||
    doc.text.size_px < 24 ||
    doc.text.size_px > 200
  ) {
    errors.push("text.size_px must be between 24 and 200.");
  }

  if (!HEX_COLOR_RE.test(doc.colors?.primary ?? "")) {
    errors.push("colors.primary must be a valid #RRGGBB or #RRGGBBAA hex color.");
  }
  if (!HEX_COLOR_RE.test(doc.colors?.active ?? "")) {
    errors.push("colors.active must be a valid #RRGGBB or #RRGGBBAA hex color.");
  }
  if (!HEX_COLOR_RE.test(doc.colors?.outline ?? "")) {
    errors.push("colors.outline must be a valid #RRGGBB or #RRGGBBAA hex color.");
  }
  if (doc.colors?.box && !HEX_COLOR_RE.test(doc.colors.box)) {
    errors.push("colors.box must be null or a valid hex color.");
  }

  if (
    typeof doc.stroke?.outline_px !== "number" ||
    doc.stroke.outline_px < 0 ||
    doc.stroke.outline_px > 20
  ) {
    errors.push("stroke.outline_px must be between 0 and 20.");
  }

  if (
    typeof doc.layout?.y_percent !== "number" ||
    doc.layout.y_percent < 0 ||
    doc.layout.y_percent > 100
  ) {
    errors.push("layout.y_percent must be between 0 and 100.");
  } else if (doc.layout.y_percent < 14 || doc.layout.y_percent > 75) {
    warnings.push(
      `y_percent (${doc.layout.y_percent}%) sits inside Shorts/Reels/TikTok UI chrome safe-area margins.`
    );
  }

  if (doc.box?.enabled && doc.colors?.box) {
    const ratio = contrastRatio(doc.colors.primary, doc.colors.box);
    if (ratio !== null && ratio < 4.5) {
      warnings.push(
        `Low contrast (${ratio.toFixed(1)}:1) between primary text and background box (recommend >= 4.5:1).`
      );
    }
  } else if (doc.stroke.outline_px === 0) {
    const ratio = contrastRatio(doc.colors.primary, doc.colors.outline);
    if (ratio !== null && ratio < 3) {
      warnings.push(
        "Outline is 0px without an opaque box — captions may become unreadable over bright footage."
      );
    }
  }

  return {
    valid: errors.length === 0,
    errors,
    warnings,
  };
}

export function parseAndValidateTemplateJson(jsonText: string): {
  doc: CaptionTemplateDoc | null;
  result: TemplateValidationResult;
} {
  try {
    const parsed = JSON.parse(jsonText) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {
        doc: null,
        result: {
          valid: false,
          errors: ["Template JSON must be a top-level object."],
          warnings: [],
        },
      };
    }
    const doc = parsed as CaptionTemplateDoc;
    return { doc, result: validateTemplateDoc(doc) };
  } catch (err) {
    return {
      doc: null,
      result: {
        valid: false,
        errors: [
          `Invalid JSON syntax: ${err instanceof Error ? err.message : "parse error"}`,
        ],
        warnings: [],
      },
    };
  }
}
