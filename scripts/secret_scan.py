from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "data",
        "dist",
        "build",
        "drive_mirror",
        "secrets",
        ".eggs",
    }
)

IGNORED_FILENAMES: frozenset[str] = frozenset({".env", ".env.local"})

BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp4",
        ".mkv",
        ".mov",
        ".avi",
        ".webm",
        ".wav",
        ".mp3",
        ".flac",
        ".aac",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".pyc",
        ".pyo",
        ".so",
        ".zip",
        ".tar",
        ".gz",
        ".pdf",
    }
)

INLINE_ALLOW_MARKERS: tuple[str, ...] = (
    "# secret-scan: allow",
    "# pragma: allowlist secret",
)

# Pre-existing dummy test fixture in backend/tests/test_clipper_e2e.py (owned by another lane)
KNOWN_SAFE_FIXTURES: dict[str, frozenset[str]] = {
    "backend/tests/test_clipper_e2e.py": frozenset(
        {"AIza" + "SyD-1234567890abcdefghijklmnopqrstuvwx"}
    ),
}

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Google / Gemini API key", re.compile(r"AIza[0-9A-Za-z\-_]{30,}")),
    (
        "GitHub token",
        re.compile(r"(?:gh[pousr]_[0-9A-Za-z_]{30,}|github_pat_[0-9A-Za-z_]{20,})"),
    ),
    (
        "OpenAI / Anthropic key",
        re.compile(r"(?<![A-Za-z0-9_-])sk-(?:proj-|ant-)?[0-9A-Za-z\-_]{20,}"),
    ),
    (
        "AWS Access Key ID",
        re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])"),
    ),
    (
        "AWS Secret Access Key",
        re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{35,}['\"]?"),
    ),
    ("HuggingFace token", re.compile(r"\bhf_[0-9A-Za-z]{25,}\b")),
    (
        "Private key PEM",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
    ),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b")),
)


@dataclass(frozen=True)
class SecretFinding:
    path: str
    line_number: int
    rule: str
    redacted: str


def redact_secret(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-2:]}"


def _is_binary_file(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        with path.open("rb") as fh:
            chunk = fh.read(4096)
    except OSError:
        return True
    return b"\x00" in chunk


def _should_skip_dir(dir_name: str) -> bool:
    return dir_name in IGNORED_DIRS or dir_name.endswith(".egg-info")


def _should_skip_file(path: Path, root: Path, explicit: bool) -> bool:
    if not explicit:
        if path.name in IGNORED_FILENAMES or path.name.startswith(".env.") and path.name.endswith(".local"):
            return True
    if not path.is_file():
        return True
    return _is_binary_file(path)


def scan_text(text: str, rel_path: str = "<string>") -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    safe_tokens = KNOWN_SAFE_FIXTURES.get(rel_path, frozenset())

    for line_no, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        if any(marker in lowered for marker in INLINE_ALLOW_MARKERS):
            continue
        for rule_name, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(line):
                token = match.group(0)
                if token in safe_tokens:
                    continue
                findings.append(
                    SecretFinding(
                        path=rel_path,
                        line_number=line_no,
                        rule=rule_name,
                        redacted=redact_secret(token),
                    )
                )
    return findings


def iter_scannable_files(root: Path) -> Iterable[Path]:
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            if _should_skip_dir(entry.name):
                continue
            yield from iter_scannable_files(entry)
        elif entry.is_file():
            if not _should_skip_file(entry, root, explicit=False):
                yield entry


def scan_paths(paths: list[Path], root: Path | None = None) -> list[SecretFinding]:
    base = (root or Path.cwd()).resolve()
    findings: list[SecretFinding] = []

    if paths:
        candidates: list[tuple[Path, bool]] = [(p, True) for p in paths]
    else:
        candidates = [(p, False) for p in iter_scannable_files(base)]

    for raw_path, explicit in candidates:
        resolved = raw_path if raw_path.is_absolute() else (base / raw_path)
        if explicit and _should_skip_file(resolved, base, explicit=True):
            continue
        try:
            rel_path = resolved.resolve().relative_to(base).as_posix()
        except ValueError:
            rel_path = resolved.as_posix()
        try:
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(scan_text(content, rel_path=rel_path))

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secret_scan",
        description="Fast secret scanner for CLIPPER repository and pre-commit hooks.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Optional explicit file paths to scan (defaults to scanning workspace files).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root directory (default: current working directory).",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress summary output when clean.",
    )
    args = parser.parse_args(argv)

    findings = scan_paths(args.paths, root=args.root)
    if findings:
        for finding in findings:
            print(
                f"FOUND [{finding.rule}] {finding.path}:{finding.line_number}: {finding.redacted}",
                file=sys.stderr,
            )
        print(
            f"Secret scan failed: {len(findings)} potential secret(s) detected.",
            file=sys.stderr,
        )
        return 1

    if not args.quiet:
        print("Secret scan passed: 0 secrets detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
