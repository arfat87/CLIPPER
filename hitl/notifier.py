from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

import yaml
from jinja2 import Template

from hitl.models import Blocker, detect_leaked_secret

EMAIL_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background:#0f172a; color:#f8fafc; padding:24px;">
  <div style="max-width:640px; margin:0 auto; background:#1e293b; border:1px solid #334155; border-radius:12px; padding:24px;">
    <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #334155; padding-bottom:12px;">
      <span style="background:#f59e0b; color:#0f172a; font-weight:700; padding:4px 10px; border-radius:6px; font-size:12px;">
        ACTION NEEDED · {{ blocker.type.value }}
      </span>
      <span style="color:#94a3b8; font-size:13px;">{{ blocker.id }} · Task {{ blocker.task_id }} (Step {{ blocker.step }})</span>
    </div>
    <h2 style="color:#f8fafc; margin-top:16px;">{{ blocker.title }}</h2>
    <p style="color:#cbd5e1; line-height:1.5;"><strong>Why this is needed:</strong> {{ blocker.why }}</p>

    {% if blocker.needed %}
    <div style="background:#0f172a; border-radius:8px; padding:16px; margin:16px 0;">
      <h4 style="margin:0 0 8px 0; color:#38bdf8;">What to provide:</h4>
      <ul style="margin:0; padding-left:20px; color:#e2e8f0;">
      {% for item in blocker.needed %}
        <li style="margin-bottom:8px;">
          <code>{{ item.key }}</code> &rarr; place in <code>{{ item.where }}</code><br/>
          <span style="color:#94a3b8; font-size:13px;">How to obtain: {{ item.how_to_get }}</span>
        </li>
      {% endfor %}
      </ul>
    </div>
    {% endif %}

    {% if blocker.alternatives %}
    <div style="margin:16px 0;">
      <strong style="color:#a7f3d0;">Offline / Alternative Commands:</strong>
      <ul style="color:#cbd5e1;">
      {% for alt in blocker.alternatives %}
        <li>{{ alt }}</li>
      {% endfor %}
      </ul>
    </div>
    {% endif %}

    <div style="background:#172554; border-left:4px solid #3b82f6; padding:12px 16px; margin:16px 0; font-size:13px; color:#dbeafe;">
      <strong>Security Note:</strong> Never paste raw API keys or passwords inside an email reply.
      Add secrets directly to <code>.env</code> or the local CLIPPER Settings UI, then reply <strong>DONE</strong> to this email
      (keeping correlation token <code>{{ blocker.correlation_token }}</code> in the subject).
    </div>

    <p style="color:#64748b; font-size:12px; margin-top:20px;">
      Correlation token: <code>{{ blocker.correlation_token }}</code> · Created at {{ blocker.created_at }}
    </p>
  </div>
</body>
</html>
"""


def render_blocker_email_html(blocker: Blocker) -> str:
    tmpl = Template(EMAIL_HTML_TEMPLATE)
    return tmpl.render(blocker=blocker)


def render_blocker_email_text(blocker: Blocker) -> str:
    lines = [
        f"[CLIPPER][{blocker.id}][ACTION NEEDED] {blocker.title}",
        f"Task: {blocker.task_id} (step {blocker.step}) | Type: {blocker.type.value}",
        f"Correlation Token: {blocker.correlation_token}",
        "",
        f"Why needed: {blocker.why}",
        "",
        "Items needed:",
    ]
    for item in blocker.needed:
        lines.append(f"  - {item.key} (in {item.where}): {item.how_to_get}")
    if blocker.alternatives:
        lines.append("")
        lines.append("Alternatives:")
        for alt in blocker.alternatives:
            lines.append(f"  * {alt}")
    lines.extend(
        [
            "",
            "HOW TO RESOLVE:",
            "1. Provide the item in .env / assets folder (do NOT send raw keys by email).",
            f"2. Reply 'DONE' to this email (keep [{blocker.correlation_token}] in subject) or run:",
            f"   python -m hitl.cli resolve {blocker.id}",
        ]
    )
    return "\n".join(lines)


def render_blockers_md(
    blockers: list[Blocker],
    state_label: str = ".clipper-state/blockers/",
) -> str:
    """Render deterministic BLOCKERS.md markdown from blocker records."""
    lines: list[str] = [
        f"# BLOCKERS (auto-generated / synced from `{state_label}`)",
        "",
    ]
    if not blockers:
        lines.append("No blockers recorded.")
        lines.append("")
        return "\n".join(lines)

    lines.extend(
        [
            "| ID | Type | Status | Task | Title | Unblocks |",
            "|---|---|---|---|---|---|",
        ]
    )
    for b in blockers:
        unblocks_list = b.unblocked_meanwhile if b.unblocked_meanwhile else [b.task_id]
        unblocks = ", ".join(f"`{t}`" for t in unblocks_list)
        safe_title = b.title.replace("|", "\\|")
        lines.append(
            f"| `{b.id}` | `{b.type.value}` | `{b.status.value}` | `{b.task_id}` | {safe_title} | {unblocks} |"
        )

    for b in blockers:
        lines.extend(
            [
                "",
                f"### `{b.id}` Details",
                f"- **Status:** `{b.status.value}` (task `{b.task_id}`, step {b.step})",
                f"- **Why:** {b.why or 'N/A'}",
            ]
        )
        if b.needed:
            needed_desc = "; ".join(
                f"`{item.key}` in `{item.where}`"
                + (f" ({item.how_to_get})" if item.how_to_get else "")
                for item in b.needed
            )
            lines.append(f"- **Needed:** {needed_desc}")
        if b.alternatives:
            lines.append(f"- **Alternatives:** {'; '.join(b.alternatives)}")
        if b.correlation_token:
            lines.append(f"- **Correlation Token:** `{b.correlation_token}`")
        if b.resolution_note:
            lines.append(f"- **Resolution:** {b.resolution_note}")
        if b.resolved_at:
            lines.append(f"- **Resolved At:** `{b.resolved_at}`")

    lines.append("")
    return "\n".join(lines)


def write_blockers_md(
    blockers: list[Blocker],
    root_dir: Path | None = None,
    state_label: str = ".clipper-state/blockers/",
) -> Path:
    """Render and write BLOCKERS.md after verifying no secret values leaked."""
    root = root_dir or Path.cwd()
    content = render_blockers_md(blockers, state_label=state_label)
    if detect_leaked_secret(content):
        raise ValueError("Refusing to write BLOCKERS.md because a secret pattern was detected.")
    blockers_md = root / "BLOCKERS.md"
    blockers_md.write_text(content, encoding="utf-8")
    return blockers_md


def _load_blockers_from_dir(blockers_dir: Path) -> list[Blocker]:
    items: list[Blocker] = []
    if not blockers_dir.exists():
        return items
    for p in sorted(blockers_dir.glob("BLK-*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if data:
            items.append(Blocker.model_validate(data))
    return items


def send_blocker_notification(
    blocker: Blocker,
    root_dir: Path | None = None,
    state_dir: Path | None = None,
    blockers: list[Blocker] | None = None,
) -> dict[str, object]:
    """Send blocker email via SMTP if configured; always sync BLOCKERS.md fallback."""
    root = root_dir or Path.cwd()
    if state_dir is not None:
        sdir = state_dir
    elif (root / ".clipper-state").exists():
        sdir = root / ".clipper-state"
    elif (root / ".agent").exists():
        sdir = root / ".agent"
    else:
        sdir = root / ".clipper-state"

    try:
        rel_label = f"{sdir.relative_to(root).as_posix()}/blockers/"
    except ValueError:
        rel_label = f"{sdir.name}/blockers/"

    all_blockers = blockers if blockers is not None else _load_blockers_from_dir(sdir / "blockers")
    if all(b.id != blocker.id for b in all_blockers):
        all_blockers = [*all_blockers, blocker]
    write_blockers_md(all_blockers, root_dir=root, state_label=rel_label)

    smtp_user = os.getenv("NOTIFY_SMTP_USER", "").strip()
    smtp_pass = os.getenv("NOTIFY_SMTP_APP_PASSWORD", "").strip()
    owner_email = os.getenv("OWNER_EMAIL", "").strip()
    smtp_host = os.getenv("NOTIFY_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("NOTIFY_SMTP_PORT", "587"))

    subject = (
        f"[CLIPPER][{blocker.id}][ACTION NEEDED] {blocker.title} "
        f"(task {blocker.task_id}) [{blocker.correlation_token}]"
    )
    text_body = render_blocker_email_text(blocker)
    html_body = render_blocker_email_html(blocker)

    if smtp_user and smtp_pass and owner_email:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = smtp_user
            msg["To"] = owner_email
            msg.set_content(text_body)
            msg.add_alternative(html_body, subtype="html")

            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            return {"sent_email": True, "wrote_fallback": True, "recipient": owner_email}
        except Exception as exc:
            return {"sent_email": False, "wrote_fallback": True, "error": str(exc)}

    return {"sent_email": False, "wrote_fallback": True, "reason": "SMTP credentials not configured"}
