import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { en } from "../../i18n/en";
import { cn } from "../../lib/cn";

export type ToastTone = "info" | "ok" | "warn" | "err";

export interface ToastItem {
  id: string;
  title: string;
  description?: string;
  tone: ToastTone;
}

export interface ToastInput {
  title: string;
  description?: string;
  tone?: ToastTone;
  durationMs?: number;
}

interface ToastContextValue {
  toasts: ToastItem[];
  toast: (input: ToastInput) => string;
  dismiss: (id: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const TONE_CLASSES: Record<ToastTone, string> = {
  info: "border-border bg-surface-2 text-text",
  ok: "border-ok/40 bg-surface-2 text-text",
  warn: "border-warn/40 bg-surface-2 text-text",
  err: "border-err/50 bg-surface-2 text-text",
};

function ToastIcon({ tone }: { tone: ToastTone }) {
  if (tone === "ok") {
    return <CheckCircle2 className="h-5 w-5 shrink-0 text-ok" aria-hidden="true" />;
  }
  if (tone === "warn") {
    return <AlertTriangle className="h-5 w-5 shrink-0 text-warn" aria-hidden="true" />;
  }
  if (tone === "err") {
    return <AlertTriangle className="h-5 w-5 shrink-0 text-err" aria-hidden="true" />;
  }
  return <Info className="h-5 w-5 shrink-0 text-accent" aria-hidden="true" />;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const toast = useCallback(
    ({ title, description, tone = "info", durationMs = 4000 }: ToastInput) => {
      const id = `toast_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
      setToasts((prev) => [...prev, { id, title, description, tone }]);
      if (durationMs > 0) {
        setTimeout(() => {
          setToasts((prev) => prev.filter((item) => item.id !== id));
        }, durationMs);
      }
      return id;
    },
    []
  );

  const value = useMemo(
    () => ({ toasts, toast, dismiss }),
    [toasts, toast, dismiss]
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    return {
      toasts: [],
      toast: () => "",
      dismiss: () => {},
    };
  }
  return ctx;
}

export function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
}) {
  if (toasts.length === 0) {
    return null;
  }

  return (
    <div
      aria-label="Notifications"
      className="pointer-events-none fixed bottom-20 right-4 left-4 z-50 flex flex-col gap-2 sm:left-auto sm:w-96 md:bottom-4"
    >
      {toasts.map((item) => (
        <div
          key={item.id}
          role={item.tone === "err" ? "alert" : "status"}
          aria-live={item.tone === "err" ? "assertive" : "polite"}
          className={cn(
            "pointer-events-auto flex items-start gap-3 rounded-md border p-3 shadow-lg transition-opacity duration-150 ease-out",
            TONE_CLASSES[item.tone]
          )}
        >
          <ToastIcon tone={item.tone} />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-text">{item.title}</p>
            {item.description ? (
              <p className="mt-0.5 text-xs text-text-dim">{item.description}</p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={() => onDismiss(item.id)}
            aria-label={en.states.closeToast}
            className="inline-flex min-h-[44px] min-w-[44px] shrink-0 items-center justify-center rounded-sm text-text-dim transition-colors hover:bg-surface hover:text-text"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      ))}
    </div>
  );
}
