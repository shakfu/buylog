#!/usr/bin/env python3
"""
Notification delivery for buylog.

Price alerts and watchlist targets are only useful if they reach the user
without opening the app. This module turns a triggered alert into a message on
one or more configured channels.

Channels are chosen with the ``BUYLOG_NOTIFY`` environment variable, as a
comma-separated list. Everything is configured by environment variable so a
cron entry needs no arguments:

    BUYLOG_NOTIFY=ntfy                 push via ntfy.sh (or a self-hosted server)
      BUYLOG_NTFY_TOPIC=my-topic       required
      BUYLOG_NTFY_SERVER=https://ntfy.sh
      BUYLOG_NTFY_TOKEN=...            optional bearer token

    BUYLOG_NOTIFY=webhook              HTTP POST of a JSON payload
      BUYLOG_WEBHOOK_URL=https://...   required

    BUYLOG_NOTIFY=desktop              local desktop notification (notify-send,
                                       osascript, or terminal-notifier)

    BUYLOG_NOTIFY=email                SMTP
      BUYLOG_SMTP_HOST=...             required
      BUYLOG_SMTP_PORT=587
      BUYLOG_SMTP_USER=...             optional
      BUYLOG_SMTP_PASSWORD=...         optional
      BUYLOG_SMTP_STARTTLS=1
      BUYLOG_EMAIL_FROM=...            defaults to BUYLOG_SMTP_USER
      BUYLOG_EMAIL_TO=a@b,c@d          required

    BUYLOG_NOTIFY=stdout               print to the terminal (the default)

A channel that is selected but not configured raises ``NotifyError`` naming the
missing variable, rather than failing silently.
"""

import json
import logging
import os
import shutil
import smtplib
import subprocess
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Callable, Optional

logger = logging.getLogger("buylog")

DEFAULT_CHANNEL = "stdout"


class NotifyError(Exception):
    """Raised when a notification channel is misconfigured or unreachable."""


@dataclass
class Notification:
    """A message to deliver."""

    title: str
    body: str
    #: Structured detail, sent as-is by the webhook channel.
    data: dict[str, Any] = field(default_factory=dict)
    #: "default", "high" or "urgent"; channels that support priority use it.
    priority: str = "default"


def _require_env(name: str, channel: str) -> str:
    """Read a required environment variable or explain what is missing."""
    value = os.getenv(name)
    if not value:
        raise NotifyError(f"Notification channel '{channel}' needs {name} to be set.")
    return value


def send_stdout(notification: Notification) -> None:
    """Print the notification to the terminal."""
    print(f"[{notification.title}] {notification.body}")


def send_ntfy(notification: Notification) -> None:
    """Publish to an ntfy topic."""
    import requests

    topic = _require_env("BUYLOG_NTFY_TOPIC", "ntfy")
    server = os.getenv("BUYLOG_NTFY_SERVER", "https://ntfy.sh").rstrip("/")

    headers = {"Title": notification.title, "Priority": notification.priority}
    token = os.getenv("BUYLOG_NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.post(
            f"{server}/{topic}",
            data=notification.body.encode("utf-8"),
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        raise NotifyError(f"ntfy delivery failed: {e}") from e


def send_webhook(notification: Notification) -> None:
    """POST the notification as JSON."""
    import requests

    url = _require_env("BUYLOG_WEBHOOK_URL", "webhook")
    payload = {
        "title": notification.title,
        "body": notification.body,
        "priority": notification.priority,
        "data": notification.data,
    }
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        raise NotifyError(f"Webhook delivery failed: {e}") from e


def send_desktop(notification: Notification) -> None:
    """Raise a desktop notification using whatever the platform provides."""
    if shutil.which("notify-send"):
        command = ["notify-send", notification.title, notification.body]
    elif shutil.which("terminal-notifier"):
        command = [
            "terminal-notifier",
            "-title",
            notification.title,
            "-message",
            notification.body,
        ]
    elif shutil.which("osascript"):
        script = (
            f"display notification {json.dumps(notification.body)} "
            f"with title {json.dumps(notification.title)}"
        )
        command = ["osascript", "-e", script]
    else:
        raise NotifyError(
            "Notification channel 'desktop' found no notifier "
            "(notify-send, terminal-notifier or osascript)."
        )

    try:
        subprocess.run(command, check=True, capture_output=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as e:
        raise NotifyError(f"Desktop notification failed: {e}") from e


def send_email(notification: Notification) -> None:
    """Send the notification over SMTP."""
    host = _require_env("BUYLOG_SMTP_HOST", "email")
    recipients = [
        address.strip()
        for address in _require_env("BUYLOG_EMAIL_TO", "email").split(",")
        if address.strip()
    ]
    if not recipients:
        raise NotifyError(
            "Notification channel 'email' needs BUYLOG_EMAIL_TO to be set."
        )

    user = os.getenv("BUYLOG_SMTP_USER")
    sender = os.getenv("BUYLOG_EMAIL_FROM") or user
    if not sender:
        raise NotifyError(
            "Notification channel 'email' needs BUYLOG_EMAIL_FROM "
            "(or BUYLOG_SMTP_USER) to be set."
        )

    message = EmailMessage()
    message["Subject"] = notification.title
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(notification.body)

    port = int(os.getenv("BUYLOG_SMTP_PORT", "587"))
    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if os.getenv("BUYLOG_SMTP_STARTTLS", "1") != "0":
                smtp.starttls()
            if user and os.getenv("BUYLOG_SMTP_PASSWORD"):
                smtp.login(user, os.environ["BUYLOG_SMTP_PASSWORD"])
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as e:
        raise NotifyError(f"Email delivery failed: {e}") from e


CHANNELS: dict[str, Callable[[Notification], None]] = {
    "stdout": send_stdout,
    "ntfy": send_ntfy,
    "webhook": send_webhook,
    "desktop": send_desktop,
    "email": send_email,
}


def configured_channels(channels: Optional[str] = None) -> list[str]:
    """
    Resolve the channel list.

    Args:
        channels: Comma-separated override; falls back to ``BUYLOG_NOTIFY``.

    Returns:
        Channel names, in order.

    Raises:
        NotifyError: If a named channel does not exist
    """
    raw = (
        channels
        if channels is not None
        else os.getenv("BUYLOG_NOTIFY", DEFAULT_CHANNEL)
    )
    names = [name.strip().lower() for name in raw.split(",") if name.strip()]
    if not names:
        names = [DEFAULT_CHANNEL]

    unknown = [name for name in names if name not in CHANNELS]
    if unknown:
        raise NotifyError(
            f"Unknown notification channel(s): {', '.join(unknown)}. "
            f"Available: {', '.join(sorted(CHANNELS))}"
        )
    return names


def send(
    notification: Notification, channels: Optional[str] = None
) -> tuple[list[str], list[str]]:
    """
    Deliver a notification to every configured channel.

    One channel failing does not stop the others, but the failures are returned
    alongside the successes: a partial failure that looked like a success would
    mean a cron job silently losing every email alert while reporting fine.

    Args:
        notification: The message to deliver
        channels: Comma-separated channel override

    Returns:
        ``(delivered, failures)`` - the channels that succeeded, and one
        ``"channel: reason"`` string per channel that did not.

    Raises:
        NotifyError: If every configured channel failed
    """
    names = configured_channels(channels)
    delivered: list[str] = []
    failures: list[str] = []

    for name in names:
        try:
            CHANNELS[name](notification)
            delivered.append(name)
        except NotifyError as e:
            logger.error(f"Notification via '{name}' failed: {e}")
            failures.append(f"{name}: {e}")

    if failures and not delivered:
        raise NotifyError("; ".join(failures))

    return delivered, failures
