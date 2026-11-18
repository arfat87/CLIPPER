import { AlertTriangle, Inbox, Loader2, RefreshCw, WifiOff } from "lucide-react";
import { en } from "../i18n/en";

export function LoadingState({
  title = en.states.loadingTitle,
  body = en.states.loadingBody,
}: {
  title?: string;
  body?: string;
}) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-col items-center justify-center rounded-lg border border-border bg-surface p-8 text-center"
    >
      <Loader2 className="h-7 w-7 animate-spin text-accent" aria-hidden="true" />
      <p className="mt-3 text-base font-semibold text-text">{title}</p>
      <p className="mt-1 text-sm text-text-dim">{body}</p>
    </div>
  );
}

export function EmptyState({
  title,
  body,
  actionLabel,
  onAction,
}: {
  title: string;
  body: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border bg-surface p-8 text-center">
      <div className="flex h-11 w-11 items-center justify-center rounded-md bg-surface-2 text-accent">
        <Inbox className="h-5 w-5" aria-hidden="true" />
      </div>
      <h3 className="mt-3 text-base font-semibold text-text">{title}</h3>
      <p className="mt-1 max-w-md text-sm text-text-dim">{body}</p>
      {actionLabel && onAction ? (
        <button
          type="button"
          onClick={onAction}
          className="mt-4 inline-flex min-h-[44px] items-center justify-center rounded-sm bg-accent px-4 py-2 text-sm font-semibold text-accent-fg transition hover:opacity-90"
        >
          {actionLabel}
        </button>
      ) : null}
    </div>
  );
}

export function ErrorState({
  title = en.states.errorTitle,
  message = en.states.errorBody,
  onRetry,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-3 rounded-lg border border-err/50 bg-err/10 p-5 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-err" aria-hidden="true" />
        <div>
          <h3 className="text-sm font-semibold text-text">{title}</h3>
          <p className="mt-0.5 text-sm text-text-dim">{message}</p>
        </div>
      </div>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex min-h-[44px] shrink-0 items-center gap-2 rounded-sm border border-border bg-surface-2 px-4 py-2 text-sm font-semibold text-text transition hover:border-accent"
        >
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          <span>{en.states.retry}</span>
        </button>
      ) : null}
    </div>
  );
}

export function OfflineBanner() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-2 border-b border-warn/40 bg-warn/15 px-4 py-2 text-xs text-text"
    >
      <WifiOff className="h-4 w-4 shrink-0 text-warn" aria-hidden="true" />
      <span className="font-semibold">{en.states.offlineTitle}:</span>
      <span className="text-text-dim">{en.states.offlineBody}</span>
    </div>
  );
}
