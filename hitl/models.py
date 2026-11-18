from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("created_at", "resolved_at", mode="before")
    @classmethod
    def _coerce_timestamp(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return str(value)


class BlockerRequired(Exception):
    """Raised when an execution step requires human intervention."""

    def __init__(self, blocker: Blocker):
        self.blocker = blocker
        super().__init__(f"[{blocker.id}] ({blocker.type.value}) {blocker.title}")


_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AIza[0-9A-Za-z\-_]{30,}"),  # Google / Gemini API key
    re.compile(r"(?<![A-Za-z0-9_-])sk-(?:proj-|ant-)?[0-9A-Za-z\-_]{20,}"),  # OpenAI / Anthropic
    re.compile(r"(?:gh[pousr]_[0-9A-Za-z_]{30,}|github_pat_[0-9A-Za-z_]{20,})"),  # GitHub PAT
    re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])"),  # AWS Access Key ID
    re.compile(r"\bhf_[0-9A-Za-z]{25,}\b"),  # HuggingFace token
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
)


def detect_leaked_secret(text: str) -> bool:
    """Detect whether text accidentally includes a raw API key or private key."""
    if not text:
        return False
    return any(pat.search(text) is not None for pat in _SECRET_PATTERNS)


def assert_no_leaked_secrets(blocker: Blocker, extra_text: str = "") -> None:
    """Raise ValueError if a Blocker or note contains a raw secret pattern."""
    fields_to_check = [
        blocker.title,
        blocker.why,
        blocker.resolution_note or "",
        *blocker.alternatives,
        *(item.key for item in blocker.needed),
        *(item.where for item in blocker.needed),
        *(item.how_to_get for item in blocker.needed),
        extra_text,
    ]
    for val in fields_to_check:
        if detect_leaked_secret(val):
            raise ValueError(
                "Secret leak detected in HITL blocker payload; place secrets in .env or secrets/ instead."
            )
