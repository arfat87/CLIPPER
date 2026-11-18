import { AlertTriangle, Inbox, Loader2, RefreshCw, WifiOff } from "lucide-react";
import { en } from "../i18n/en";
import { Button, Card, SkeletonCard } from "./ui";

export function LoadingState({
  title = en.states.loadingTitle,
  body = en.states.loadingBody,
  showSkeletons = true,
}: {
  title?: string;
  body?: string;
  showSkeletons?: boolean;
}) {
  return (
    <div role="status" aria-live="polite" className="space-y-4">
      <Card className="flex flex-col items-center justify-center p-8 text-center">
        <Loader2 className="h-7 w-7 animate-spin text-accent" aria-hidden="true" />
        <p className="mt-3 text-base font-semibold text-text">{title}</p>
        {body ? <p className="mt-1 text-sm text-text-dim">{body}</p> : null}
      </Card>
      {showSkeletons ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : null}
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
    <Card
      variant="dashed"
      className="flex flex-col items-center justify-center p-8 text-center"
    >
      <div className="flex h-11 w-11 items-center justify-center rounded-md bg-surface-2 text-accent">
        <Inbox className="h-5 w-5" aria-hidden="true" />
      </div>
      <h3 className="mt-3 text-base font-semibold text-text">{title}</h3>
      <p className="mt-1 max-w-md text-sm text-text-dim">{body}</p>
      {actionLabel && onAction ? (
        <Button onClick={onAction} className="mt-4">
          {actionLabel}
        </Button>
      ) : null}
    </Card>
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
        <AlertTriangle
          className="mt-0.5 h-5 w-5 shrink-0 text-err"
          aria-hidden="true"
        />
        <div>
          <h3 className="text-sm font-semibold text-text">{title}</h3>
          <p className="mt-0.5 text-sm text-text-dim">{message}</p>
        </div>
      </div>
      {onRetry ? (
        <Button
          variant="secondary"
          onClick={onRetry}
          leftIcon={<RefreshCw className="h-4 w-4" aria-hidden="true" />}
          className="shrink-0"
        >
          {en.states.retry}
        </Button>
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
