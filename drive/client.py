from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from backend.app.config import ROOT_DIR, get_settings
from hitl.models import BlockerRequired, BlockerType, NeededItem
from hitl.raise_blocker import create_or_get_blocker

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

_FILE_FIELDS = (
    "id,name,mimeType,size,md5Checksum,modifiedTime,parents,"
    "headRevisionId,trashed,shortcutDetails(targetId,targetMimeType)"
)


class DriveError(Exception):
    """Base exception for Google Drive read-only client errors."""


class DriveAuthError(DriveError):
    """Authentication or permission failure against Google Drive."""


class DriveNotFoundError(DriveError):
    """Requested Drive file or root folder does not exist or is not shared."""


class DriveRateLimitError(DriveError):
    """Transient rate-limit or server error from Google Drive API."""


class DriveExportTooLargeError(DriveError):
    """Native Google file export exceeded Drive or policy size cap."""


@dataclass
class DriveFileMetadata:
    id: str
    name: str
    mime_type: str
    parents: list[str] = field(default_factory=list)
    size: int | None = None
    md5_checksum: str | None = None
    modified_time: str = ""
    head_revision_id: str | None = None
    trashed: bool = False
    shortcut_target_id: str | None = None
    shortcut_target_mime_type: str | None = None

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME

    @property
    def is_shortcut(self) -> bool:
        return self.mime_type == SHORTCUT_MIME

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> DriveFileMetadata:
        raw_size = data.get("size")
        size = int(raw_size) if raw_size is not None and str(raw_size).isdigit() else None
        shortcut = data.get("shortcutDetails") or {}
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", "")),
            mime_type=str(data.get("mimeType", "application/octet-stream")),
            parents=[str(p) for p in (data.get("parents") or [])],
            size=size,
            md5_checksum=(str(data["md5Checksum"]) if data.get("md5Checksum") else None),
            modified_time=str(data.get("modifiedTime", "")),
            head_revision_id=(
                str(data["headRevisionId"]) if data.get("headRevisionId") else None
            ),
            trashed=bool(data.get("trashed", False)),
            shortcut_target_id=(
                str(shortcut["targetId"]) if shortcut.get("targetId") else None
            ),
            shortcut_target_mime_type=(
                str(shortcut["targetMimeType"]) if shortcut.get("targetMimeType") else None
            ),
        )


@dataclass
class ListFilesPage:
    files: list[DriveFileMetadata]
    next_page_token: str | None = None


@dataclass
class DriveChange:
    file_id: str
    removed: bool = False
    file: DriveFileMetadata | None = None
    time: str = ""

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> DriveChange:
        raw_file = data.get("file")
        parsed_file = (
            DriveFileMetadata.from_api_dict(raw_file)
            if isinstance(raw_file, dict) and raw_file.get("id")
            else None
        )
        return cls(
            file_id=str(data.get("fileId") or (parsed_file.id if parsed_file else "")),
            removed=bool(data.get("removed", False)),
            file=parsed_file,
            time=str(data.get("time", "")),
        )


@dataclass
class ChangesPage:
    changes: list[DriveChange]
    next_page_token: str | None = None
    new_start_page_token: str | None = None


@runtime_checkable
class DriveClient(Protocol):
    """Strictly read-only Google Drive client protocol."""

    def list_files(
        self,
        folder_id: str,
        page_token: str | None = None,
        page_size: int = 1000,
    ) -> ListFilesPage: ...

    def get_start_page_token(self) -> str: ...

    def list_changes(
        self,
        page_token: str,
        page_size: int = 1000,
    ) -> ChangesPage: ...

    def download_file(self, file_id: str, dest_path: Path) -> int: ...

    def export_file(self, file_id: str, mime_type: str, dest_path: Path) -> int: ...


def _load_and_validate_sa_file(
    sa_file: Path,
    *,
    root_dir: Path,
    state_dir: Path | str | None,
    task_id: str,
    strict: bool,
) -> dict[str, Any]:
    try:
        rel_display = sa_file.relative_to(root_dir).as_posix()
    except ValueError:
        rel_display = str(sa_file)

    def _raise_credential_blocker(reason: str) -> dict[str, Any]:
        blocker = create_or_get_blocker(
            btype=BlockerType.CREDENTIAL,
            task_id=task_id,
            title="Google Drive service-account JSON required",
            needed=[
                NeededItem(
                    key=rel_display,
                    where=rel_display,
                    how_to_get=(
                        "Google Cloud Console -> IAM & Admin -> Service Accounts -> "
                        "Create key (JSON) -> save to secrets/drive_sa.json and share "
                        "the Clipper Drive folder with the service account email as Viewer."
                    ),
                )
            ],
            why=reason,
            alternatives=[
                "Keep drive.enabled=false in config/drive.yaml to run CLIPPER completely offline."
            ],
            root_dir=root_dir,
            state_dir=state_dir,
            raise_exc=strict,
        )
        if strict:
            raise BlockerRequired(blocker)
        return {}

    if not sa_file.exists() or not sa_file.is_file() or sa_file.stat().st_size == 0:
        return _raise_credential_blocker(f"Missing or empty service-account file: {rel_display}")

    try:
        data = json.loads(sa_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _raise_credential_blocker(
            f"Invalid JSON in service-account file {rel_display}: {exc}"
        )

    if not isinstance(data, dict) or data.get("type") != "service_account":
        return _raise_credential_blocker(
            f"Service-account file {rel_display} must have type='service_account'."
        )

    missing = [
        k for k in ("client_email", "private_key", "token_uri") if not str(data.get(k, "")).strip()
    ]
    if missing:
        return _raise_credential_blocker(
            f"Service-account file {rel_display} is missing required field(s): {', '.join(missing)}"
        )

    return data


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _extract_error_reason(response: httpx.Response) -> tuple[str, str]:
    try:
        payload = response.json()
        err = payload.get("error", {})
        if isinstance(err, dict):
            msg = str(err.get("message", response.text))
            errors = err.get("errors") or []
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                return str(errors[0].get("reason", "")), msg
            return "", msg
    except Exception:
        pass
    return "", response.text[:300]


def _raise_for_drive_response(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    reason, message = _extract_error_reason(response)
    if response.status_code == 401:
        raise DriveAuthError(f"Drive authentication failed (401): {message}")
    if response.status_code == 403:
        if reason in {"userRateLimitExceeded", "rateLimitExceeded", "quotaExceeded"}:
            raise DriveRateLimitError(f"Drive rate limit exceeded ({reason}): {message}")
        if reason == "exportSizeLimitExceeded":
            raise DriveExportTooLargeError(f"Drive export size limit exceeded: {message}")
        raise DriveAuthError(f"Drive access denied (403 {reason or 'forbidden'}): {message}")
    if response.status_code == 404:
        raise DriveNotFoundError(f"DRIVE_NOT_FOUND: {message}")
    if response.status_code == 429 or response.status_code >= 500:
        raise DriveRateLimitError(f"Drive transient error ({response.status_code}): {message}")
    raise DriveError(f"Drive API error ({response.status_code}): {message}")


class ServiceAccountDriveClient:
    """Read-only Google Drive v3 client authenticated via a service-account JSON."""

    def __init__(
        self,
        sa_file: Path | str | None = None,
        *,
        root_dir: Path | None = None,
        state_dir: Path | str | None = None,
        task_id: str = "T-9.1",
        strict: bool = True,
        timeout: float = 60.0,
        http_client: httpx.Client | None = None,
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        self.root_dir = (root_dir or ROOT_DIR).resolve()
        self.state_dir = state_dir
        if sa_file is None:
            resolved_sa = get_settings().drive_sa_file
        else:
            p = Path(sa_file)
            resolved_sa = p if p.is_absolute() else (self.root_dir / p)
        self.sa_file = resolved_sa

        self._sa_info = _load_and_validate_sa_file(
            self.sa_file,
            root_dir=self.root_dir,
            state_dir=self.state_dir,
            task_id=task_id,
            strict=strict,
        )
        self._timeout = timeout
        self._http = http_client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._owns_http = http_client is None
        self._token_provider = token_provider
        self._cached_token: str = ""
        self._token_expires_at: float = 0.0

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _get_access_token(self) -> str:
        if self._token_provider is not None:
            return self._token_provider()

        now = time.time()
        if self._cached_token and now < (self._token_expires_at - 60):
            return self._cached_token

        if not self._sa_info:
            raise DriveAuthError("Service account credentials are not loaded.")

        # Prefer google-auth when available in environment
        try:
            from google.auth.transport.requests import Request as GoogleAuthRequest  # type: ignore[import-not-found]  # noqa: E501
            from google.oauth2 import service_account as google_sa  # type: ignore[import-not-found]

            creds = google_sa.Credentials.from_service_account_info(
                self._sa_info,
                scopes=[DRIVE_READONLY_SCOPE],
            )
            creds.refresh(GoogleAuthRequest())
            if not creds.token:
                raise DriveAuthError("google-auth returned empty access token.")
            self._cached_token = str(creds.token)
            self._token_expires_at = now + 3300
            return self._cached_token
        except ImportError:
            pass

        # Fallback: mint JWT via cryptography if installed
        try:
            from cryptography.hazmat.primitives import (  # type: ignore[import-not-found]
                hashes,
                serialization,
            )
            from cryptography.hazmat.primitives.asymmetric import (  # type: ignore[import-not-found]
                padding,
                rsa,
            )
        except ImportError as exc:
            raise DriveAuthError(
                "No JWT signer available; install 'google-auth' or 'cryptography' "
                "for live Google Drive service-account authentication."
            ) from exc

        iat = int(now)
        exp = iat + 3600
        header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode("utf-8"))
        claims = _b64url(
            json.dumps(
                {
                    "iss": self._sa_info["client_email"],
                    "scope": DRIVE_READONLY_SCOPE,
                    "aud": self._sa_info["token_uri"],
                    "exp": exp,
                    "iat": iat,
                }
            ).encode("utf-8")
        )
        signing_input = f"{header}.{claims}".encode("ascii")
        key_obj = serialization.load_pem_private_key(
            str(self._sa_info["private_key"]).encode("utf-8"),
            password=None,
        )
        if not isinstance(key_obj, rsa.RSAPrivateKey):
            raise DriveAuthError("Service-account private_key must be an RSA key.")
        signature = key_obj.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        assertion = f"{header}.{claims}.{_b64url(signature)}"

        resp = self._http.post(
            str(self._sa_info["token_uri"]),
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        if resp.status_code >= 400:
            raise DriveAuthError(f"OAuth token exchange failed ({resp.status_code}): {resp.text[:200]}")
        token_data = resp.json()
        access_token = str(token_data.get("access_token", ""))
        if not access_token:
            raise DriveAuthError("OAuth token response missing access_token.")
        expires_in = int(token_data.get("expires_in", 3600))
        self._cached_token = access_token
        self._token_expires_at = now + expires_in
        return access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_access_token()}"}

    def list_files(
        self,
        folder_id: str,
        page_token: str | None = None,
        page_size: int = 1000,
    ) -> ListFilesPage:
        params: dict[str, Any] = {
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": f"nextPageToken, files({_FILE_FIELDS})",
            "pageSize": min(max(1, page_size), 1000),
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        resp = self._http.get(f"{DRIVE_API_BASE}/files", params=params, headers=self._headers())
        _raise_for_drive_response(resp)
        payload = resp.json()
        files = [DriveFileMetadata.from_api_dict(item) for item in (payload.get("files") or [])]
        nxt = payload.get("nextPageToken")
        return ListFilesPage(files=files, next_page_token=str(nxt) if nxt else None)

    def get_start_page_token(self) -> str:
        resp = self._http.get(
            f"{DRIVE_API_BASE}/changes/startPageToken",
            params={"supportsAllDrives": "true"},
            headers=self._headers(),
        )
        _raise_for_drive_response(resp)
        payload = resp.json()
        token = payload.get("startPageToken")
        if not token:
            raise DriveError("Drive API returned empty startPageToken.")
        return str(token)

    def list_changes(
        self,
        page_token: str,
        page_size: int = 1000,
    ) -> ChangesPage:
        params: dict[str, Any] = {
            "pageToken": page_token,
            "includeRemoved": "true",
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
            "pageSize": min(max(1, page_size), 1000),
            "fields": (
                f"nextPageToken, newStartPageToken, "
                f"changes(fileId, removed, time, file({_FILE_FIELDS}))"
            ),
        }
        resp = self._http.get(f"{DRIVE_API_BASE}/changes", params=params, headers=self._headers())
        _raise_for_drive_response(resp)
        payload = resp.json()
        changes = [DriveChange.from_api_dict(item) for item in (payload.get("changes") or [])]
        nxt = payload.get("nextPageToken")
        new_start = payload.get("newStartPageToken")
        return ChangesPage(
            changes=changes,
            next_page_token=str(nxt) if nxt else None,
            new_start_page_token=str(new_start) if new_start else None,
        )

    def _stream_to_path(
        self,
        url: str,
        params: dict[str, Any],
        dest_path: Path,
    ) -> int:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with self._http.stream("GET", url, params=params, headers=self._headers()) as resp:
            if resp.status_code >= 400:
                resp.read()
                _raise_for_drive_response(resp)
            with dest_path.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    if chunk:
                        fh.write(chunk)
                        written += len(chunk)
        return written

    def download_file(self, file_id: str, dest_path: Path) -> int:
        return self._stream_to_path(
            f"{DRIVE_API_BASE}/files/{file_id}",
            {"alt": "media", "supportsAllDrives": "true"},
            dest_path,
        )

    def export_file(self, file_id: str, mime_type: str, dest_path: Path) -> int:
        return self._stream_to_path(
            f"{DRIVE_API_BASE}/files/{file_id}/export",
            {"mimeType": mime_type},
            dest_path,
        )


class MockDriveClient:
    """Deterministic in-memory read-only Drive client for offline tests."""

    def __init__(self, root_folder_id: str = "root_clipper") -> None:
        self.root_folder_id = root_folder_id
        self.files: dict[str, DriveFileMetadata] = {
            root_folder_id: DriveFileMetadata(
                id=root_folder_id,
                name="Clipper",
                mime_type=FOLDER_MIME,
                parents=[],
                modified_time="2026-11-18T10:00:00Z",
            )
        }
        self.contents: dict[str, bytes] = {}
        self.exports: dict[tuple[str, str], bytes] = {}
        self._changes: list[DriveChange] = []
        self._download_outcomes: dict[str, list[bytes | Exception]] = {}
        self._export_outcomes: dict[tuple[str, str], list[bytes | Exception]] = {}
        self._list_outcomes: list[Exception | None] = []
        self.download_calls: list[str] = []
        self.export_calls: list[tuple[str, str]] = []

    def add_folder(
        self,
        file_id: str,
        name: str,
        parent_id: str,
        *,
        modified_time: str = "2026-11-18T10:00:00Z",
    ) -> DriveFileMetadata:
        meta = DriveFileMetadata(
            id=file_id,
            name=name,
            mime_type=FOLDER_MIME,
            parents=[parent_id],
            modified_time=modified_time,
        )
        self.files[file_id] = meta
        return meta

    def add_file(
        self,
        file_id: str,
        name: str,
        parent_id: str,
        content: bytes = b"",
        mime_type: str = "application/octet-stream",
        *,
        md5_checksum: str | None = None,
        size: int | None = None,
        modified_time: str = "2026-11-18T10:00:00Z",
        head_revision_id: str = "1",
        is_native: bool = False,
    ) -> DriveFileMetadata:
        computed_md5 = None if is_native else (md5_checksum or hashlib.md5(content).hexdigest())
        computed_size = size if size is not None else len(content)
        meta = DriveFileMetadata(
            id=file_id,
            name=name,
            mime_type=mime_type,
            parents=[parent_id],
            size=computed_size,
            md5_checksum=computed_md5,
            modified_time=modified_time,
            head_revision_id=head_revision_id,
        )
        self.files[file_id] = meta
        self.contents[file_id] = content
        return meta

    def add_shortcut(
        self,
        file_id: str,
        name: str,
        parent_id: str,
        target_id: str,
        target_mime_type: str = "video/mp4",
        *,
        modified_time: str = "2026-11-18T10:00:00Z",
    ) -> DriveFileMetadata:
        meta = DriveFileMetadata(
            id=file_id,
            name=name,
            mime_type=SHORTCUT_MIME,
            parents=[parent_id],
            modified_time=modified_time,
            shortcut_target_id=target_id,
            shortcut_target_mime_type=target_mime_type,
        )
        self.files[file_id] = meta
        return meta

    def set_export_bytes(self, file_id: str, mime_type: str, data: bytes) -> None:
        self.exports[(file_id, mime_type)] = data

    def set_download_outcomes(self, file_id: str, outcomes: list[bytes | Exception]) -> None:
        self._download_outcomes[file_id] = list(outcomes)

    def set_export_outcomes(
        self, file_id: str, mime_type: str, outcomes: list[bytes | Exception]
    ) -> None:
        self._export_outcomes[(file_id, mime_type)] = list(outcomes)

    def set_list_outcomes(self, outcomes: list[Exception | None]) -> None:
        self._list_outcomes = list(outcomes)

    def simulate_move(
        self,
        file_id: str,
        new_parent_id: str,
        *,
        new_name: str | None = None,
        modified_time: str = "2026-11-18T11:00:00Z",
    ) -> None:
        meta = self.files[file_id]
        meta.parents = [new_parent_id]
        if new_name is not None:
            meta.name = new_name
        meta.modified_time = modified_time
        self._changes.append(DriveChange(file_id=file_id, removed=False, file=meta, time=modified_time))

    def simulate_rename(
        self,
        file_id: str,
        new_name: str,
        *,
        modified_time: str = "2026-11-18T11:05:00Z",
    ) -> None:
        meta = self.files[file_id]
        meta.name = new_name
        meta.modified_time = modified_time
        self._changes.append(DriveChange(file_id=file_id, removed=False, file=meta, time=modified_time))

    def simulate_removal(
        self,
        file_id: str,
        *,
        trashed: bool = False,
        modified_time: str = "2026-11-18T11:10:00Z",
    ) -> None:
        meta = self.files.get(file_id)
        if trashed and meta is not None:
            meta.trashed = True
            meta.modified_time = modified_time
            self._changes.append(
                DriveChange(file_id=file_id, removed=False, file=meta, time=modified_time)
            )
        else:
            self.files.pop(file_id, None)
            self._changes.append(
                DriveChange(file_id=file_id, removed=True, file=None, time=modified_time)
            )

    def simulate_content_change(
        self,
        file_id: str,
        content: bytes,
        *,
        md5_checksum: str | None = None,
        head_revision_id: str = "2",
        modified_time: str = "2026-11-18T11:15:00Z",
    ) -> None:
        meta = self.files[file_id]
        self.contents[file_id] = content
        meta.size = len(content)
        if meta.md5_checksum is not None or md5_checksum is not None:
            meta.md5_checksum = md5_checksum or hashlib.md5(content).hexdigest()
        meta.head_revision_id = head_revision_id
        meta.modified_time = modified_time
        self._changes.append(DriveChange(file_id=file_id, removed=False, file=meta, time=modified_time))

    def list_files(
        self,
        folder_id: str,
        page_token: str | None = None,
        page_size: int = 1000,
    ) -> ListFilesPage:
        if self._list_outcomes:
            outcome = self._list_outcomes.pop(0)
            if outcome is not None:
                raise outcome

        if folder_id not in self.files:
            raise DriveNotFoundError(f"DRIVE_NOT_FOUND: {folder_id}")

        children = sorted(
            (
                f
                for f in self.files.values()
                if not f.trashed and folder_id in f.parents
            ),
            key=lambda item: (item.name, item.id),
        )
        offset = int(page_token) if page_token else 0
        page = children[offset : offset + page_size]
        next_tok = str(offset + page_size) if (offset + page_size) < len(children) else None
        return ListFilesPage(files=page, next_page_token=next_tok)

    def get_start_page_token(self) -> str:
        return str(len(self._changes) + 1)

    def list_changes(
        self,
        page_token: str,
        page_size: int = 1000,
    ) -> ChangesPage:
        offset = max(0, int(page_token) - 1)
        page = self._changes[offset : offset + page_size]
        end_idx = offset + len(page)
        if end_idx < len(self._changes):
            return ChangesPage(changes=page, next_page_token=str(end_idx + 1))
        return ChangesPage(
            changes=page,
            next_page_token=None,
            new_start_page_token=str(len(self._changes) + 1),
        )

    def download_file(self, file_id: str, dest_path: Path) -> int:
        self.download_calls.append(file_id)
        outcomes = self._download_outcomes.get(file_id)
        if outcomes:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            payload = outcome
        else:
            if file_id not in self.contents:
                raise DriveNotFoundError(f"DRIVE_NOT_FOUND: {file_id}")
            payload = self.contents[file_id]

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(payload)
        return len(payload)

    def export_file(self, file_id: str, mime_type: str, dest_path: Path) -> int:
        self.export_calls.append((file_id, mime_type))
        outcomes = self._export_outcomes.get((file_id, mime_type))
        if outcomes:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            payload = outcome
        elif (file_id, mime_type) in self.exports:
            payload = self.exports[(file_id, mime_type)]
        elif file_id in self.contents:
            payload = self.contents[file_id]
        else:
            raise DriveNotFoundError(f"DRIVE_NOT_FOUND: {file_id}")

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(payload)
        return len(payload)
