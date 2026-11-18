from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

EVENT_FILE_SYNCED = "file.synced"
EVENT_FILE_STALE = "file.stale"
EVENT_FILE_REMOVED = "file.removed"
EVENT_FILE_ERROR = "file.error"
EVENT_SYNC_COMPLETED = "sync.completed"

VALID_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_FILE_SYNCED,
        EVENT_FILE_STALE,
        EVENT_FILE_REMOVED,
        EVENT_FILE_ERROR,
        EVENT_SYNC_COMPLETED,
    }
)


@dataclass(frozen=True)
class DriveSyncEvent:
    """Structured event emitted after a Drive sync commits manifest state."""

    event_type: str
    file_id: str | None = None
    path: str | None = None
    route: str | None = None
    mirror_path: str | None = None
    status: str | None = None
    md5: str | None = None
    exported_as: str | None = None
    timestamp: str = ""
    error: str | None = None
    mode: str | None = None
    stats: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.event_type not in VALID_EVENT_TYPES:
            raise ValueError(
                f"Invalid DriveSyncEvent type {self.event_type!r}; "
                f"expected one of {sorted(VALID_EVENT_TYPES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_type": self.event_type,
            "file_id": self.file_id,
            "path": self.path,
            "route": self.route,
            "mirror_path": self.mirror_path,
            "status": self.status,
            "md5": self.md5,
            "exported_as": self.exported_as,
            "timestamp": self.timestamp,
        }
        if self.error is not None:
            payload["error"] = self.error
        if self.mode is not None:
            payload["mode"] = self.mode
        if self.stats is not None:
            payload["stats"] = dict(self.stats)
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DriveSyncEvent:
        raw_stats = data.get("stats")
        return cls(
            event_type=str(data["event_type"]),
            file_id=str(data["file_id"]) if data.get("file_id") is not None else None,
            path=str(data["path"]) if data.get("path") is not None else None,
            route=str(data["route"]) if data.get("route") is not None else None,
            mirror_path=(
                str(data["mirror_path"]) if data.get("mirror_path") is not None else None
            ),
            status=str(data["status"]) if data.get("status") is not None else None,
            md5=str(data["md5"]) if data.get("md5") is not None else None,
            exported_as=(
                str(data["exported_as"]) if data.get("exported_as") is not None else None
            ),
            timestamp=str(data.get("timestamp", "")),
            error=str(data["error"]) if data.get("error") is not None else None,
            mode=str(data["mode"]) if data.get("mode") is not None else None,
            stats=dict(raw_stats) if isinstance(raw_stats, Mapping) else None,
        )


EventCallback = Callable[[DriveSyncEvent], None]


@dataclass
class DriveEventBus:
    """In-process event bus and history sink for DriveSyncEvent notifications."""

    _subscribers: list[EventCallback] = field(default_factory=list)
    events: list[DriveSyncEvent] = field(default_factory=list)

    def subscribe(self, handler: EventCallback) -> None:
        if handler not in self._subscribers:
            self._subscribers.append(handler)

    def unsubscribe(self, handler: EventCallback) -> None:
        self._subscribers = [sub for sub in self._subscribers if sub != handler]

    def emit(self, event: DriveSyncEvent) -> None:
        self.events.append(event)
        for handler in list(self._subscribers):
            handler(event)

    def clear(self) -> None:
        self.events.clear()
