import { API_BASE, getApiSimulationMode } from "./client";
import type { JobStatus, StageCheckpoint, StageName } from "./types";

export interface SseProgressEvent {
  job_id: string;
  stage: StageName;
  status: StageCheckpoint["status"];
  pct: number;
  message?: string;
  eta_s?: number | null;
  clip_id?: string | null;
}

export interface SseStateEvent {
  job_id: string;
  status: JobStatus;
  stage?: StageName | null;
  error_code?: string | null;
  error_message?: string | null;
}

export interface SseWarningEvent {
  job_id?: string;
  message: string;
}

export interface SseDoneEvent {
  job_id: string;
  status: JobStatus;
}

export interface JobEventHandlers {
  onOpen?: () => void;
  onReconnecting?: (attempt: number, lastEventId: string | null) => void;
  onProgress?: (data: SseProgressEvent, eventId: string | null) => void;
  onState?: (data: SseStateEvent, eventId: string | null) => void;
  onWarning?: (data: SseWarningEvent, eventId: string | null) => void;
  onDone?: (data: SseDoneEvent, eventId: string | null) => void;
  onError?: (error: string) => void;
}

export interface SubscribeJobEventsOptions {
  initialLastEventId?: string | null;
  maxRetries?: number;
  retryDelayMs?: number;
}

function parseSseFrames(
  chunkText: string,
  onFrame: (event: string, data: string, id: string | null) => void
): string {
  const normalized = chunkText.replace(/\r\n/g, "\n");
  const blocks = normalized.split("\n\n");
  const remainder = blocks.pop() ?? "";

  for (const block of blocks) {
    if (!block.trim()) {
      continue;
    }
    let eventName = "message";
    let eventId: string | null = null;
    const dataLines: string[] = [];

    for (const line of block.split("\n")) {
      if (line.startsWith(":")) {
        continue;
      }
      const colonIdx = line.indexOf(":");
      const field = colonIdx === -1 ? line : line.slice(0, colonIdx);
      const rawValue = colonIdx === -1 ? "" : line.slice(colonIdx + 1);
      const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;

      if (field === "event") {
        eventName = value;
      } else if (field === "id") {
        eventId = value;
      } else if (field === "data") {
        dataLines.push(value);
      }
    }

    if (dataLines.length > 0) {
      onFrame(eventName, dataLines.join("\n"), eventId);
    }
  }

  return remainder;
}

export function subscribeJobEvents(
  jobId: string,
  handlers: JobEventHandlers,
  options?: SubscribeJobEventsOptions
): () => void {
  let closed = false;
  let completed = false;
  let lastEventId: string | null = options?.initialLastEventId ?? null;
  let retryCount = 0;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let activeEventSource: EventSource | null = null;
  let activeReader: ReadableStreamDefaultReader<Uint8Array> | null = null;

  const maxRetries = options?.maxRetries ?? 4;
  const baseRetryDelayMs = options?.retryDelayMs ?? 250;

  const dispatchFrame = (eventName: string, rawData: string, id: string | null) => {
    if (id) {
      lastEventId = id;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(rawData);
    } catch {
      return;
    }

    if (eventName === "progress") {
      handlers.onProgress?.(parsed as SseProgressEvent, id);
    } else if (eventName === "state") {
      handlers.onState?.(parsed as SseStateEvent, id);
    } else if (eventName === "warning") {
      handlers.onWarning?.(parsed as SseWarningEvent, id);
    } else if (eventName === "done") {
      completed = true;
      handlers.onDone?.(parsed as SseDoneEvent, id);
    }
  };

  const scheduleReconnect = () => {
    if (closed || completed) {
      return;
    }
    if (retryCount >= maxRetries) {
      handlers.onError?.("Lost connection to live job event stream.");
      return;
    }
    retryCount += 1;
    handlers.onReconnecting?.(retryCount, lastEventId);
    const delayMs = Math.min(baseRetryDelayMs * 2 ** (retryCount - 1), 3000);
    retryTimer = setTimeout(() => {
      if (!closed && !completed) {
        connect();
      }
    }, delayMs);
  };

  const buildStreamUrl = () => {
    const qs = lastEventId
      ? `?last_event_id=${encodeURIComponent(lastEventId)}`
      : "";
    return `${API_BASE}/jobs/${encodeURIComponent(jobId)}/events${qs}`;
  };

  const connectWithEventSource = () => {
    const es = new EventSource(buildStreamUrl());
    activeEventSource = es;

    es.onopen = () => {
      retryCount = 0;
      handlers.onOpen?.();
    };

    const bind = (type: string) => {
      es.addEventListener(type, (evt) => {
        const msg = evt as MessageEvent<string>;
        dispatchFrame(type, msg.data, msg.lastEventId || null);
        if (type === "done") {
          es.close();
        }
      });
    };

    bind("progress");
    bind("state");
    bind("warning");
    bind("done");

    es.onerror = () => {
      es.close();
      activeEventSource = null;
      scheduleReconnect();
    };
  };

  const connectWithFetch = async () => {
    try {
      const headers: Record<string, string> = {
        Accept: "text/event-stream",
      };
      if (lastEventId) {
        headers["Last-Event-ID"] = lastEventId;
      }
      const sim = getApiSimulationMode();
      if (sim !== "normal") {
        headers["X-Clipper-Mock-Mode"] = sim;
      }

      const response = await fetch(buildStreamUrl(), {
        method: "GET",
        headers,
      });

      if (!response.ok) {
        throw new Error(`SSE stream returned ${response.status}`);
      }

      retryCount = 0;
      handlers.onOpen?.();

      if (response.body && typeof response.body.getReader === "function") {
        const reader = response.body.getReader();
        activeReader = reader;
        const decoder = new TextDecoder();
        let buffer = "";

        while (!closed && !completed) {
          const { done, value } = await reader.read();
          if (done) {
            break;
          }
          buffer += decoder.decode(value, { stream: true });
          buffer = parseSseFrames(buffer, dispatchFrame);
        }
        activeReader = null;
        if (buffer.trim()) {
          parseSseFrames(`${buffer}\n\n`, dispatchFrame);
        }
      } else {
        const fullText = await response.text();
        parseSseFrames(`${fullText}\n\n`, dispatchFrame);
      }

      if (!closed && !completed) {
        scheduleReconnect();
      }
    } catch {
      activeReader = null;
      if (!closed && !completed) {
        scheduleReconnect();
      }
    }
  };

  const connect = () => {
    if (closed || completed) {
      return;
    }
    if (typeof globalThis.EventSource === "function") {
      connectWithEventSource();
    } else {
      void connectWithFetch();
    }
  };

  connect();

  return () => {
    closed = true;
    if (retryTimer) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    if (activeEventSource) {
      activeEventSource.close();
      activeEventSource = null;
    }
    if (activeReader) {
      void activeReader.cancel().catch(() => {});
      activeReader = null;
    }
  };
}
