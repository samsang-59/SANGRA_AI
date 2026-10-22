"""Mailer (Layer 7, item 8): one send_email(to, subject, body) with two versions chosen by MAILER_MODE.

- console (development): prints the email text and link in the terminal (stdout). This is the only place
  an email token is ever printed; it is NOT written to the log (the log never holds tokens).
- smtp: real sending, for when the app is deployed.

Callers use deliver(), which never raises: a failed email is logged (without its body) and reported as False,
so for example a register still succeeds and the user can ask for the link again.
"""

import asyncio
import logging
import smtplib
import sys
from email.message import EmailMessage
from typing import Protocol, TextIO

from app.core.config import Settings

logger = logging.getLogger("app.mailer")


class Mailer(Protocol):
    async def send_email(self, to: str, subject: str, body: str) -> None: ...


class ConsoleMailer:
    def __init__(self, stream: TextIO | None = None):
        self._stream = stream

    async def send_email(self, to: str, subject: str, body: str) -> None:
        stream = self._stream or sys.stdout
        separator = "-" * 72
        stream.write(f"\n{separator}\nEMAIL (console mailer)\nTo: {to}\nSubject: {subject}\n\n{body}\n{separator}\n")
        stream.flush()


class SmtpMailer:
    def __init__(self, settings: Settings):
        self._host = settings.smtp_host or ""
        self._port = settings.smtp_port
        self._username = settings.smtp_username
        self._password = settings.smtp_password.get_secret_value() if settings.smtp_password else None
        self._starttls = settings.smtp_starttls
        self._timeout = settings.smtp_timeout_seconds
        self._from = settings.mail_from

    def _send_sync(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
            if self._starttls:
                smtp.starttls()
            if self._username and self._password:
                smtp.login(self._username, self._password)
            smtp.send_message(message)

    async def send_email(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        await asyncio.to_thread(self._send_sync, message)


def build_mailer(settings: Settings) -> Mailer:
    if settings.mailer_mode == "smtp":
        return SmtpMailer(settings)
    return ConsoleMailer()


async def deliver(mailer: Mailer, to: str, subject: str, body: str) -> bool:
    """Sends an email. Never raises; returns False (and logs) when sending failed."""
    try:
        await mailer.send_email(to, subject, body)
        return True
    except Exception as exc:
        logger.error("Sending the email %r failed (%s)", subject, type(exc).__name__)
        return False
