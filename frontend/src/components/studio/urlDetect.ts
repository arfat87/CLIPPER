import { en } from "../../i18n/en";

export type DetectedPlatform = "youtube" | "instagram" | "facebook" | "direct";

export interface UrlDetectionResult {
  valid: boolean;
  platform: DetectedPlatform | null;
  badgeLabel: string | null;
  hint: string | null;
  error: string | null;
}

const DIRECT_EXT_RE = /\.(mp4|mov|mkv|webm|m4v)(\?.*)?$/i;

export function detectVideoPlatform(rawUrl: string): UrlDetectionResult {
  const trimmed = rawUrl.trim();
  if (!trimmed) {
    return {
      valid: false,
      platform: null,
      badgeLabel: null,
      hint: null,
      error: null,
    };
  }

  if (trimmed.startsWith("sample://")) {
    return {
      valid: true,
      platform: "direct",
      badgeLabel: en.home.platforms.direct,
      hint: null,
      error: null,
    };
  }

  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return {
      valid: false,
      platform: null,
      badgeLabel: null,
      hint: null,
      error: en.home.urlInvalid,
    };
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return {
      valid: false,
      platform: null,
      badgeLabel: null,
      hint: null,
      error: en.home.urlInvalid,
    };
  }

  const host = parsed.hostname.toLowerCase().replace(/^www\./, "").replace(/^m\./, "");

  if (
    host === "youtube.com" ||
    host === "youtu.be" ||
    host.endsWith(".youtube.com")
  ) {
    return {
      valid: true,
      platform: "youtube",
      badgeLabel: en.home.platforms.youtube,
      hint: null,
      error: null,
    };
  }

  if (host === "instagram.com" || host.endsWith(".instagram.com")) {
    return {
      valid: true,
      platform: "instagram",
      badgeLabel: en.home.platforms.instagram,
      hint: en.home.igLoginHint,
      error: null,
    };
  }

  if (
    host === "facebook.com" ||
    host === "fb.watch" ||
    host.endsWith(".facebook.com")
  ) {
    return {
      valid: true,
      platform: "facebook",
      badgeLabel: en.home.platforms.facebook,
      hint: en.home.fbLoginHint,
      error: null,
    };
  }

  if (DIRECT_EXT_RE.test(parsed.pathname)) {
    return {
      valid: true,
      platform: "direct",
      badgeLabel: en.home.platforms.direct,
      hint: null,
      error: null,
    };
  }

  return {
    valid: false,
    platform: null,
    badgeLabel: null,
    hint: null,
    error: en.home.urlUnsupported,
  };
}
