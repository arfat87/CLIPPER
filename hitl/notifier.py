from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Template

from hitl.models import Blocker

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


def send_blocker_notification(blocker: Blocker, root_dir: Path | None = None) -> dict:
    """Send blocker email via SMTP if configured; always update BLOCKERS.md fallback."""
    root = root_dir or Path.cwd()
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

    # Always write human-readable fallback to BLOCKERS.md
    blockers_md = root / "BLOCKERS.md"
    entry = f"\n## {blocker.id}: {blocker.title} (`{blocker.status.value}`)\n\n```text\n{text_body}\n```\n"
    if blockers_md.exists():
        existing = blockers_md.read_text(encoding="utf-8")
        if blocker.id not in existing:
            blockers_md.write_text(existing + entry, encoding="utf-8")
    else:
        blockers_md.write_text("# CLIPPER — Active & Historical HITL Blockers\n" + entry, encoding="utf-8")

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
