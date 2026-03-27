"""Email delivery utility for generated legal drafts."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def send_legal_draft_email(
	recipient_email: str,
	subject: str,
	draft_title: str,
	draft_content: str,
	disclaimer: str,
) -> tuple[bool, str]:
	"""Send generated legal draft to user's email via SMTP.

	Returns:
		(success, message)
	"""
	smtp_host = os.environ.get("SMTP_HOST", "").strip()
	smtp_port = int(os.environ.get("SMTP_PORT", "587").strip() or "587")
	smtp_username = os.environ.get("SMTP_USERNAME", "").strip()
	smtp_password = os.environ.get("SMTP_PASSWORD", "").strip()
	smtp_from_email = os.environ.get("SMTP_FROM_EMAIL", smtp_username).strip()
	smtp_use_tls = os.environ.get("SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes", "on"}

	if not smtp_host or not smtp_from_email:
		return (
			False,
			"SMTP is not configured. Set SMTP_HOST and SMTP_FROM_EMAIL (and auth vars if required).",
		)

	if not recipient_email:
		return False, "Recipient email is missing."

	msg = EmailMessage()
	msg["From"] = smtp_from_email
	msg["To"] = recipient_email
	msg["Subject"] = subject

	plain_body = (
		f"{disclaimer}\n\n"
		"You can copy this draft into your email client and add sender/To/CC/BCC as needed.\n\n"
		f"Draft Title: {draft_title}\n\n"
		"--- DRAFT START ---\n"
		f"{draft_content}\n"
		"--- DRAFT END ---\n"
	)
	msg.set_content(plain_body)

	try:
		with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as smtp:
			if smtp_use_tls:
				smtp.starttls()
			if smtp_username and smtp_password:
				smtp.login(smtp_username, smtp_password)
			smtp.send_message(msg)
		return True, "Draft sent to your email."
	except Exception as exc:
		return False, f"Email delivery failed: {exc}"
