from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any, Dict

import requests

MAILERSEND_API_URL = "https://api.mailersend.com/v1/email"
MAILERSEND_TIMEOUT_SECONDS = 20
MAILERSEND_API_KEY_ENV = "MAILERSEND_API_KEY"

SENDER_NAME = "StorePilot"
SENDER_EMAIL = "report@storepilot.eu"
BCC_EMAIL = "storepilot.eu@gmail.com"

CALENDLY_URL = "https://calendly.com/d/ctpk-y67-vpr"

SUBJECT = "StorePilot — il tuo report è pronto"
TEXT_BODY = f"""Ciao,

la simulazione è completata.

Che tu stia valutando un nuovo progetto o stia analizzando un’attività già operativa, i numeri sono solo il punto di partenza.
La vera differenza sta nella loro interpretazione e nella capacità di trasformarli in decisioni concrete.

Se vuoi analizzare insieme i risultati della simulazione e verificare le ipotesi del modello, puoi prenotare una breve sessione di confronto strategico.

Prenota qui la tua sessione:
{CALENDLY_URL}

A presto,
StorePilot™
"""

HTML_BODY = f"""<!doctype html>
<html lang="it">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>StorePilot report</title>
  </head>
  <body style="margin:0;padding:0;background:#f6f7f9;color:#111827;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f7f9;padding:24px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;padding:24px;">
            <tr>
              <td style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.65;color:#111827;">
                <p style="margin:0 0 16px;">Ciao,</p>
                <p style="margin:0 0 16px;">la simulazione è completata.</p>
                <p style="margin:0 0 16px;">
                  Che tu stia valutando un nuovo progetto o stia analizzando un’attività già operativa,
                  i numeri sono solo il punto di partenza. La vera differenza sta nella loro interpretazione
                  e nella capacità di trasformarli in decisioni concrete.
                </p>
                <p style="margin:0 0 20px;">
                  Se vuoi analizzare insieme i risultati della simulazione e verificare le ipotesi del modello,
                  puoi prenotare una breve sessione di confronto strategico.
                </p>
                <p style="margin:0 0 24px;">
                  <a href="{CALENDLY_URL}" style="display:inline-block;background:#111827;color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:8px;font-weight:700;">
                    Prenota qui la tua sessione
                  </a>
                </p>
                <p style="margin:0;">A presto,<br />StorePilot™</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def _is_valid_email(email: str) -> bool:
    """Return True if email has a minimal valid structure."""
    value = str(email or "").strip()
    if not value:
        return False
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))


def _encode_attachment_base64(file_path: Path) -> str:
    """Read a file and return a base64-encoded UTF-8 string."""
    raw = file_path.read_bytes()
    return base64.b64encode(raw).decode("utf-8")


def send_storepilot_report(
    user_email: str,
    file_path: str,
    file_type: str,
) -> Dict[str, Any]:
    """Send StorePilot report by MailerSend with selected attachment format.

    Args:
        user_email: Destination user email.
        file_path: Absolute or relative path of generated report file.
        file_type: "pdf" or "excel".

    Returns:
        Structured response with success flag, status_code, message and response payload.
    """
    api_key = str(os.getenv(MAILERSEND_API_KEY_ENV, "") or "").strip()
    if not api_key:
        return {
            "success": False,
            "status_code": 0,
            "message": "Missing MailerSend API key",
            "response": {"error": f"{MAILERSEND_API_KEY_ENV} is not configured"},
        }

    if not _is_valid_email(user_email):
        return {
            "success": False,
            "status_code": 0,
            "message": "Invalid recipient email",
            "response": {"error": "Invalid user_email"},
        }

    path = Path(file_path).expanduser()
    if not path.exists() or not path.is_file():
        return {
            "success": False,
            "status_code": 0,
            "message": "Attachment file not found",
            "response": {"error": f"Missing file: {path}"},
        }

    normalized_type = str(file_type or "").strip().lower()
    if normalized_type == "pdf":
        attachment_name = "storepilot-report.pdf"
    elif normalized_type == "excel":
        attachment_name = "storepilot-report.xlsx"
    else:
        return {
            "success": False,
            "status_code": 0,
            "message": "Invalid file_type",
            "response": {"error": 'file_type must be "pdf" or "excel"'},
        }

    try:
        encoded_attachment = _encode_attachment_base64(path)
    except Exception as exc:
        return {
            "success": False,
            "status_code": 0,
            "message": "Failed to encode attachment",
            "response": {"error": str(exc)},
        }

    payload = {
        "from": {"email": SENDER_EMAIL, "name": SENDER_NAME},
        "to": [{"email": str(user_email).strip()}],
        "bcc": [{"email": BCC_EMAIL}],
        "subject": SUBJECT,
        "text": TEXT_BODY,
        "html": HTML_BODY,
        "attachments": [
            {
                "filename": attachment_name,
                "content": encoded_attachment,
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            MAILERSEND_API_URL,
            headers=headers,
            json=payload,
            timeout=MAILERSEND_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return {
            "success": False,
            "status_code": 0,
            "message": "Error sending email",
            "response": {"error": str(exc)},
        }

    try:
        response_payload: Any = response.json()
    except ValueError:
        response_payload = {"raw": response.text}

    if 200 <= response.status_code < 300:
        return {
            "success": True,
            "status_code": response.status_code,
            "message": "Email sent successfully",
            "response": response_payload,
        }

    return {
        "success": False,
        "status_code": response.status_code,
        "message": "Error sending email",
        "response": response_payload,
    }
