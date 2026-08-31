"""Tests for the notification layer."""

import json

import pytest

from buylog import notify
from buylog.notify import Notification, NotifyError


@pytest.fixture
def message():
    return Notification(title="buylog", body="iPhone 15 is $899", priority="high")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start each test with no notification configuration."""
    for name in list(vars(notify)) + [
        "BUYLOG_NOTIFY",
        "BUYLOG_NTFY_TOPIC",
        "BUYLOG_NTFY_SERVER",
        "BUYLOG_NTFY_TOKEN",
        "BUYLOG_WEBHOOK_URL",
        "BUYLOG_SMTP_HOST",
        "BUYLOG_EMAIL_TO",
        "BUYLOG_EMAIL_FROM",
        "BUYLOG_SMTP_USER",
    ]:
        monkeypatch.delenv(name, raising=False)


def test_default_channel_is_stdout():
    assert notify.configured_channels() == ["stdout"]


def test_channels_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("BUYLOG_NOTIFY", "stdout, ntfy")
    assert notify.configured_channels() == ["stdout", "ntfy"]


def test_explicit_channels_override_the_environment(monkeypatch):
    monkeypatch.setenv("BUYLOG_NOTIFY", "ntfy")
    assert notify.configured_channels("stdout") == ["stdout"]


def test_unknown_channel_is_rejected():
    with pytest.raises(NotifyError, match="Unknown notification channel"):
        notify.configured_channels("carrier-pigeon")


def test_stdout_channel_prints(message, capsys):
    assert notify.send(message, "stdout") == (["stdout"], [])
    assert "iPhone 15 is $899" in capsys.readouterr().out


def test_ntfy_requires_a_topic(message):
    with pytest.raises(NotifyError, match="BUYLOG_NTFY_TOPIC"):
        notify.send(message, "ntfy")


def test_ntfy_posts_to_the_topic(message, monkeypatch):
    posted = {}

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, data=None, headers=None, timeout=None):
        posted.update(url=url, data=data, headers=headers)
        return Response()

    monkeypatch.setenv("BUYLOG_NTFY_TOPIC", "my-topic")
    monkeypatch.setenv("BUYLOG_NTFY_SERVER", "https://ntfy.example/")
    monkeypatch.setenv("BUYLOG_NTFY_TOKEN", "secret")
    monkeypatch.setattr("requests.post", fake_post)

    assert notify.send(message, "ntfy") == (["ntfy"], [])
    assert posted["url"] == "https://ntfy.example/my-topic"
    assert posted["data"] == b"iPhone 15 is $899"
    assert posted["headers"]["Title"] == "buylog"
    assert posted["headers"]["Priority"] == "high"
    assert posted["headers"]["Authorization"] == "Bearer secret"


def test_webhook_requires_a_url(message):
    with pytest.raises(NotifyError, match="BUYLOG_WEBHOOK_URL"):
        notify.send(message, "webhook")


def test_webhook_posts_json(message, monkeypatch):
    posted = {}

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, json=None, timeout=None):
        posted.update(url=url, payload=json)
        return Response()

    monkeypatch.setenv("BUYLOG_WEBHOOK_URL", "https://hooks.example/buylog")
    monkeypatch.setattr("requests.post", fake_post)

    assert notify.send(message, "webhook") == (["webhook"], [])
    assert posted["url"] == "https://hooks.example/buylog"
    assert posted["payload"]["title"] == "buylog"
    assert posted["payload"]["priority"] == "high"
    # The payload must be JSON-serializable.
    json.dumps(posted["payload"])


def test_email_requires_a_host(message):
    with pytest.raises(NotifyError, match="BUYLOG_SMTP_HOST"):
        notify.send(message, "email")


def test_email_requires_recipients(message, monkeypatch):
    monkeypatch.setenv("BUYLOG_SMTP_HOST", "smtp.example")
    with pytest.raises(NotifyError, match="BUYLOG_EMAIL_TO"):
        notify.send(message, "email")


def test_email_sends_over_smtp(message, monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent.update(host=host, port=port)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            sent["starttls"] = True

        def send_message(self, msg):
            sent["message"] = msg

    monkeypatch.setenv("BUYLOG_SMTP_HOST", "smtp.example")
    monkeypatch.setenv("BUYLOG_EMAIL_TO", "me@example.com, you@example.com")
    monkeypatch.setenv("BUYLOG_EMAIL_FROM", "buylog@example.com")
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)

    assert notify.send(message, "email") == (["email"], [])
    assert sent["host"] == "smtp.example"
    assert sent["port"] == 587
    assert sent["starttls"] is True
    assert sent["message"]["To"] == "me@example.com, you@example.com"
    assert sent["message"]["Subject"] == "buylog"


def test_desktop_without_a_notifier_reports_clearly(message, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(NotifyError, match="found no notifier"):
        notify.send(message, "desktop")


def test_one_channel_failing_does_not_stop_the_others(message, capsys):
    """A misconfigured channel must not swallow a working one."""
    delivered, failures = notify.send(message, "stdout,ntfy")
    assert delivered == ["stdout"]
    assert "iPhone 15" in capsys.readouterr().out
    # The failure must be reported, not just logged: a caller that only saw
    # the successes would report a cron run as fine while losing the alert.
    assert len(failures) == 1
    assert failures[0].startswith("ntfy: ")


def test_all_channels_failing_raises(message):
    with pytest.raises(NotifyError):
        notify.send(message, "ntfy,webhook")


def test_partial_failure_is_reported_to_the_caller(message, capsys):
    delivered, failures = notify.send(message, "stdout,webhook,ntfy")
    assert delivered == ["stdout"]
    assert len(failures) == 2
