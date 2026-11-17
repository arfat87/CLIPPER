from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class BlockerType(str, Enum):
    SECRET = "SECRET"
    CREDENTIAL = "CREDENTIAL"
    ASSET = "ASSET"
    DECISION = "DECISION"
    ENVIRONMENT = "ENVIRONMENT"
    ACCOUNT = "ACCOUNT"


class BlockerStatus(str, Enum):
    OPEN = "OPEN"
    AWAITING_VALIDATION = "AWAITING_VALIDATION"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


class NeededItem(BaseModel):
    key: str
    where: str = ".env"
    how_to_get: str = ""


class Blocker(BaseModel):
    id: str
    type: BlockerType
    status: BlockerStatus = BlockerStatus.OPEN
    task_id: str
    step: int = 1
    title: str
    needed: list[NeededItem] = Field(default_factory=list)
    why: str = ""
    alternatives: list[str] = Field(default_factory=list)
    unblocked_meanwhile: list[str] = Field(default_factory=list)
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    resolved_at: str | None = None
    resolution_note: str | None = None
    reminders_sent: int = 0
    correlation_token: str = ""


class BlockerRequired(Exception):
    """Raised when an execution step requires human intervention."""

    def __init__(self, blocker: Blocker):
        self.blocker = blocker
        super().__init__(f"[{blocker.id}] ({blocker.type.value}) {blocker.title}")


def detect_leaked_secret(text: str) -> bool:
    """Detect whether an email reply body accidentally includes a raw API key."""
    import re

    patterns = [
        r"AIza[0-9A-Za-z\-_]{30,}",  # Google / Gemini API key
        r"sk-[0-9A-Za-z]{20,}",      # OpenAI style key
        r"ghp_[0-9A-Za-z]{30,}",     # GitHub PAT
    ]
    for pat in patterns:
        if re.search(pat, text):
            return True
    return False
