"""Smoke tests for the CLI.

These exercise `cli.main()` end-to-end against a temporary database, covering
argument parsing, the handler dispatch, and exit codes - none of which the
service-layer tests reach.
"""

import importlib
import sys

import pytest


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """A freshly imported cli module bound to a temporary database."""
    monkeypatch.setenv("BUYLOG_DB_PATH", str(tmp_path / "cli.db"))
    for name in ("buylog.cli", "buylog.config", "buylog"):
        sys.modules.pop(name, None)
    module = importlib.import_module("buylog.cli")
    yield module
    for name in ("buylog.cli", "buylog.config", "buylog"):
        sys.modules.pop(name, None)


def run(cli, *argv):
    """Run the CLI with argv and return its exit code (None means success)."""
    return cli.main(list(argv))


def test_add_brand_then_product(cli, capsys):
    assert not run(cli, "add", "--brand", "Apple")
    assert not run(cli, "add", "--brand", "Apple", "--product", "iPhone 15")
    out = capsys.readouterr().out
    assert "Added brand: Apple" in out
    assert "Added product: iPhone 15" in out


def test_add_brand_does_not_poison_later_commands(cli, capsys):
    """Regression: the failed audit write used to break every later command."""
    run(cli, "add", "--brand", "Apple")
    run(cli, "add", "--brand", "Apple", "--product", "iPhone 15")
    run(cli, "add", "--vendor", "Amazon")
    capsys.readouterr()
    run(cli, "list", "brands")
    out = capsys.readouterr().out
    assert "Apple" in out
    assert "PendingRollbackError" not in out
    assert "Database error" not in out


def test_add_vendor_with_quote_but_no_product_fails(cli, capsys):
    """`--quote` without `--product` must be rejected, not silently dropped."""
    code = run(cli, "add", "--vendor", "Amazon", "--quote", "600")
    out = capsys.readouterr().out
    assert code == 1
    assert "--product" in out


def test_po_lifecycle(cli, capsys):
    run(cli, "add", "--brand", "Apple", "--product", "iPhone 15")
    run(cli, "add", "--vendor", "Amazon")
    capsys.readouterr()

    assert not run(
        cli,
        "po",
        "create",
        "PO-001",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--price",
        "999.99",
        "--quantity",
        "2",
    )
    assert "PO-001" in capsys.readouterr().out

    assert not run(cli, "po", "list")
    assert "PO-001" in capsys.readouterr().out

    assert not run(cli, "po", "show", "PO-001")
    assert "1999.98" in capsys.readouterr().out

    assert not run(cli, "po", "update", "PO-001", "--status", "ordered")
    assert "ordered" in capsys.readouterr().out


def test_po_create_requires_vendor_product_price(cli, capsys):
    code = run(cli, "po", "create", "PO-002")
    assert code == 1
    assert "--vendor" in capsys.readouterr().out


def test_po_show_unknown_returns_error_code(cli, capsys):
    assert run(cli, "po", "show", "NOPE") == 1
    assert "not found" in capsys.readouterr().out


def test_spec_lifecycle(cli, capsys):
    assert not run(cli, "spec", "create", "Camera Spec", "--description", "Cameras")
    assert "Camera Spec" in capsys.readouterr().out

    assert not run(
        cli,
        "spec",
        "add-feature",
        "Camera Spec",
        "Resolution",
        "--type",
        "number",
        "--unit",
        "MP",
    )
    capsys.readouterr()

    assert not run(cli, "spec", "list")
    assert "Camera Spec" in capsys.readouterr().out

    assert not run(cli, "spec", "show", "Camera Spec")
    out = capsys.readouterr().out
    assert "Resolution" in out and "MP" in out

    assert not run(cli, "spec", "delete", "Camera Spec")
    capsys.readouterr()
    assert run(cli, "spec", "show", "Camera Spec") == 1


def test_spec_add_feature_unknown_spec_returns_error_code(cli, capsys):
    assert run(cli, "spec", "add-feature", "Nope", "Resolution") == 1
    assert "not found" in capsys.readouterr().out


def test_migrate_dry_run(cli, capsys):
    assert not run(cli, "migrate", "--dry-run")
    assert "up to date" in capsys.readouterr().out


def test_delete_without_yes_on_non_interactive_stdin_errors(cli, capsys):
    """Regression: delete used to block on input() and die with EOFError."""
    run(cli, "add", "--brand", "Apple")
    capsys.readouterr()
    code = run(cli, "delete", "brand", "--name", "Apple")
    out = capsys.readouterr().out
    assert code == 1
    assert "--yes" in out
    assert "EOF" not in out


def test_delete_with_yes_succeeds(cli, capsys):
    run(cli, "add", "--brand", "Apple")
    capsys.readouterr()
    assert not run(cli, "--yes", "delete", "brand", "--name", "Apple")
    assert "Deleted brand" in capsys.readouterr().out


def test_delete_unknown_entity_returns_error_code(cli, capsys):
    assert run(cli, "--yes", "delete", "brand", "--name", "Nope") == 1
    assert "not found" in capsys.readouterr().out.lower()


def test_update_unknown_entity_returns_error_code(cli, capsys):
    assert run(cli, "update", "brand", "Nope", "--new-name", "X") == 1


def test_update_renames_brand(cli, capsys):
    run(cli, "add", "--brand", "Apple")
    capsys.readouterr()
    assert not run(cli, "update", "brand", "Apple", "--new-name", "Apple Inc")
    capsys.readouterr()
    run(cli, "list", "brands")
    assert "Apple Inc" in capsys.readouterr().out


def test_no_command_prints_help_and_returns_error(cli, capsys):
    assert run(cli) == 1
    assert "usage" in capsys.readouterr().out.lower()


def test_add_quote_creates_missing_entities_with_yes(cli, capsys):
    run(cli, "add", "--vendor", "Amazon")
    capsys.readouterr()
    assert not run(
        cli,
        "--yes",
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--brand",
        "Apple",
        "--quote",
        "999",
    )
    assert "Added quote" in capsys.readouterr().out


# Automation and analytics commands
def test_check_reports_nothing_on_an_empty_database(cli, capsys):
    assert not run(cli, "check")
    assert "Nothing triggered" in capsys.readouterr().out


def test_check_quiet_prints_nothing_when_idle(cli, capsys):
    assert not run(cli, "check", "--quiet")
    assert capsys.readouterr().out == ""


def test_check_reports_a_triggered_alert(cli, capsys):
    run(cli, "add", "--vendor", "Amazon")
    run(
        cli,
        "--yes",
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--brand",
        "Apple",
        "--quote",
        "899",
    )
    run(cli, "alert", "add", "iPhone 15", "900")
    capsys.readouterr()

    assert not run(cli, "check")
    out = capsys.readouterr().out
    assert "iPhone 15" in out
    assert "899.00" in out


def test_check_notifies_on_the_requested_channel(cli, capsys):
    run(cli, "add", "--vendor", "Amazon")
    run(
        cli,
        "--yes",
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--brand",
        "Apple",
        "--quote",
        "899",
    )
    run(cli, "alert", "add", "iPhone 15", "900")
    capsys.readouterr()

    assert not run(cli, "check", "--notify", "stdout")
    assert "Notified via: stdout" in capsys.readouterr().out


def test_check_rejects_an_unknown_channel(cli, capsys):
    run(cli, "add", "--vendor", "Amazon")
    run(
        cli,
        "--yes",
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--brand",
        "Apple",
        "--quote",
        "899",
    )
    run(cli, "alert", "add", "iPhone 15", "900")
    capsys.readouterr()

    assert run(cli, "check", "--notify", "carrier-pigeon") == 1
    assert "Unknown notification channel" in capsys.readouterr().out


def test_fx_refresh_stores_fetched_rates(cli, capsys, monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"rates": {"EUR": 0.8}}

    monkeypatch.setattr("requests.get", lambda *a, **k: Response())
    run(cli, "add", "--vendor", "EuroShop", "--currency", "EUR")
    capsys.readouterr()

    assert not run(cli, "fx", "refresh")
    assert "EUR" in capsys.readouterr().out

    assert not run(cli, "fx", "list")
    assert "1.25" in capsys.readouterr().out


def test_fx_refresh_with_no_foreign_vendors_says_so(cli, capsys):
    assert not run(cli, "fx", "refresh")
    assert "Nothing to refresh" in capsys.readouterr().out


def test_fx_refresh_rejects_a_bad_date(cli, capsys):
    assert run(cli, "fx", "refresh", "EUR", "--date", "31-08-2026") == 1
    assert "YYYY-MM-DD" in capsys.readouterr().out


def test_status_considering_shows_up_as_might_buy(cli, capsys):
    run(cli, "add", "--brand", "Apple", "--product", "iPhone 15")
    run(cli, "add", "--vendor", "Amazon")
    run(
        cli,
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--quote",
        "40",
    )
    capsys.readouterr()

    assert not run(cli, "status", "set", "1", "considering")
    capsys.readouterr()

    assert not run(cli, "purchase-list", "might-buy")
    out = capsys.readouterr().out
    assert "iPhone 15" in out
    assert "Considering" in out


def test_status_ordered_shows_up_in_spend(cli, capsys):
    """Marking a quote ordered records the purchase spend reports."""
    run(cli, "add", "--brand", "Apple", "--product", "iPhone 15")
    run(cli, "add", "--vendor", "Amazon")
    run(
        cli,
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--quote",
        "40",
    )
    capsys.readouterr()

    assert not run(cli, "status", "set", "1", "ordered")
    capsys.readouterr()

    assert not run(cli, "spend")
    out = capsys.readouterr().out
    assert "quote-1" in out or "$40.00" in out
    assert "$40.00" in out


def test_spend_on_an_empty_database(cli, capsys):
    assert not run(cli, "spend")
    assert "No purchase orders found" in capsys.readouterr().out


def test_spend_reports_committed_money(cli, capsys):
    run(
        cli, "add", "--brand", "Apple", "--product", "iPhone 15", "--category", "phones"
    )
    run(cli, "add", "--vendor", "Amazon")
    run(
        cli,
        "po",
        "create",
        "PO-1",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--price",
        "999",
        "--order-date",
        "2026-01-15",
    )
    capsys.readouterr()

    assert not run(cli, "spend")
    out = capsys.readouterr().out
    assert "2026-01" in out
    assert "Amazon" in out
    assert "phones" in out
    assert "$999.00" in out


def test_spend_vendor_report_only(cli, capsys):
    run(cli, "add", "--brand", "Apple", "--product", "iPhone 15")
    run(cli, "add", "--vendor", "Amazon")
    run(
        cli,
        "po",
        "create",
        "PO-1",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--price",
        "999",
        "--order-date",
        "2026-01-15",
    )
    capsys.readouterr()

    assert not run(cli, "spend", "vendor")
    out = capsys.readouterr().out
    assert "Spend by vendor" in out
    assert "Spend by month" not in out


def test_scrape_refresh_with_nothing_tracked(cli, capsys):
    assert not run(cli, "scrape", "refresh")
    assert "No quotes carry a source URL" in capsys.readouterr().out


def test_list_quotes_shows_the_source_currency(cli, capsys):
    run(cli, "add-fx", "--code", "EUR", "--usd-per-unit", "1.1")
    run(cli, "add", "--vendor", "EuroShop", "--currency", "EUR")
    run(
        cli,
        "--yes",
        "add",
        "--vendor",
        "EuroShop",
        "--product",
        "iPhone 15",
        "--brand",
        "Apple",
        "--quote",
        "850",
    )
    capsys.readouterr()

    assert not run(cli, "list", "quotes")
    out = capsys.readouterr().out
    assert "850.00 EUR" in out
    assert "$935.00" in out


def test_spend_analysis_report_renders(cli, capsys):
    run(cli, "add", "--brand", "Apple", "--product", "iPhone 15")
    run(cli, "add", "--vendor", "Amazon")
    run(
        cli,
        "po",
        "create",
        "PO-1",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--price",
        "999",
        "--order-date",
        "2026-01-15",
    )
    capsys.readouterr()

    assert not run(cli, "report", "spend-analysis")
    out = capsys.readouterr().out
    assert "<html" in out.lower()
    assert "Spend Analysis" in out
    assert "$999.00" in out


def test_check_reports_a_partial_notification_failure(cli, capsys, monkeypatch):
    """A cron run must not report success when a channel silently lost the alert."""
    monkeypatch.delenv("BUYLOG_NTFY_TOPIC", raising=False)
    run(cli, "add", "--vendor", "Amazon")
    run(
        cli,
        "--yes",
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--brand",
        "Apple",
        "--quote",
        "899",
    )
    run(cli, "alert", "add", "iPhone 15", "900")
    capsys.readouterr()

    # ntfy is unconfigured, so it fails while stdout succeeds.
    assert run(cli, "check", "--notify", "stdout,ntfy") == 1
    out = capsys.readouterr().out
    assert "Notified via: stdout" in out
    assert "Notification failed via ntfy" in out
