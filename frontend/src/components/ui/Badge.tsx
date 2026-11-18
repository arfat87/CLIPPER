import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

export type BadgeTone = "neutral" | "accent" | "ok" | "warn" | "err" | "route";
export type BadgeSize = "sm" | "md";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  size?: BadgeSize;
}

const TONE_CLASSES: Record<BadgeTone, string> = {
  neutral: "border-border bg-surface-2 text-text-dim",
  accent: "border-accent/40 bg-accent/15 text-accent",
  ok: "border-ok/40 bg-ok/15 text-ok",
  warn: "border-warn/40 bg-warn/15 text-warn",
  err: "border-err/40 bg-err/15 text-err",
  route: "border-border bg-surface-2 text-text font-mono uppercase tracking-wide",
};

const SIZE_CLASSES: Record<BadgeSize, string> = {
  sm: "px-2 py-0.5 text-[11px]",
  md: "px-2.5 py-0.5 text-xs",
};

export function Badge({
  tone = "neutral",
  size = "md",
  className,
  children,
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border font-semibold leading-tight",
        TONE_CLASSES[tone],
        SIZE_CLASSES[size],
        className
      )}
      {...props}
    >
      {children}
    </span>
  );
}

export function getStatusTone(status: string): BadgeTone {
  const normalized = status.trim().toUpperCase();
  if (
    normalized === "COMPLETED" ||
    normalized === "RENDERED" ||
    normalized === "DONE" ||
    normalized === "CONNECTED" ||
    normalized === "PUBLISHED" ||
    normalized === "RESOLVED" ||
    normalized === "READY"
  ) {
    return "ok";
  }
  if (
    normalized === "FAILED" ||
    normalized === "AUTH_ERROR" ||
    normalized === "INVALID" ||
    normalized === "NOT_FOUND" ||
    normalized === "OPEN"
  ) {
    return "err";
  }
  if (normalized === "DISABLED" || normalized === "ARCHIVED" || normalized === "PENDING" || normalized === "CANCELLED") {
    return "neutral";
  }
  return "warn";
}

export function StatusBadge({
  status,
  className,
}: {
  status: string;
  className?: string;
}) {
  return (
    <Badge tone={getStatusTone(status)} className={className}>
      {status}
    </Badge>
  );
}
