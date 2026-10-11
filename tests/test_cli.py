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


def test_brand_add_then_product_add(cli, capsys):
    assert not run(cli, "brand", "add", "Apple")
    assert not run(cli, "product", "add", "iPhone 15", "--brand", "Apple")
    out = capsys.readouterr().out
    assert "Added brand: Apple" in out
    assert "Added product: iPhone 15" in out


def test_brand_add_does_not_poison_later_commands(cli, capsys):
    """Regression: the failed audit write used to break every later command."""
    run(cli, "brand", "add", "Apple")
    run(cli, "product", "add", "iPhone 15", "--brand", "Apple")
    run(cli, "vendor", "add", "Amazon")
    capsys.readouterr()
    run(cli, "brand", "list")
    out = capsys.readouterr().out
    assert "Apple" in out
    assert "PendingRollbackError" not in out
    assert "Database error" not in out


def test_quote_without_product_fails(cli, capsys):
    """A price without a product must be rejected, not silently dropped."""
    with pytest.raises(SystemExit) as exc:
        run(cli, "quote", "add", "--vendor", "Amazon", "--price", "600")
    assert exc.value.code == 2
    assert "--product" in capsys.readouterr().err


def test_po_lifecycle(cli, capsys):
    run(cli, "product", "add", "iPhone 15", "--brand", "Apple")
    run(cli, "vendor", "add", "Amazon")
    capsys.readouterr()

    assert not run(
        cli,
        "po",
        "add",
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

    assert not run(cli, "po", "edit", "PO-001", "--status", "ordered")
    assert "ordered" in capsys.readouterr().out


def test_po_add_requires_vendor_product_price(cli, capsys):
    code = run(cli, "po", "add", "PO-002")
    assert code == 1
    assert "--vendor" in capsys.readouterr().out


def test_po_show_unknown_returns_error_code(cli, capsys):
    assert run(cli, "po", "show", "NOPE") == 1
    assert "not found" in capsys.readouterr().out


def test_spec_lifecycle(cli, capsys):
    assert not run(cli, "spec", "add", "Camera Spec", "--description", "Cameras")
    assert "Camera Spec" in capsys.readouterr().out

    assert not run(
        cli,
        "spec",
        "feature",
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

    assert not run(cli, "spec", "rm", "Camera Spec")
    capsys.readouterr()
    assert run(cli, "spec", "show", "Camera Spec") == 1


def test_spec_feature_unknown_spec_returns_error_code(cli, capsys):
    assert run(cli, "spec", "feature", "Nope", "Resolution") == 1
    assert "not found" in capsys.readouterr().out


def test_migrate_dry_run(cli, capsys):
    assert not run(cli, "db", "migrate", "--dry-run")
    assert "up to date" in capsys.readouterr().out


def test_rm_without_yes_on_non_interactive_stdin_errors(cli, capsys):
    """Regression: delete used to block on input() and die with EOFError."""
    run(cli, "brand", "add", "Apple")
    capsys.readouterr()
    code = run(cli, "brand", "rm", "Apple")
    out = capsys.readouterr().out
    assert code == 1
    assert "--yes" in out
    assert "EOF" not in out


def test_rm_with_yes_succeeds(cli, capsys):
    run(cli, "brand", "add", "Apple")
    capsys.readouterr()
    assert not run(cli, "--yes", "brand", "rm", "Apple")
    assert "Deleted brand" in capsys.readouterr().out


def test_rm_unknown_entity_returns_error_code(cli, capsys):
    assert run(cli, "brand", "rm", "Nope", "--yes") == 1
    assert "not found" in capsys.readouterr().out.lower()


def test_edit_unknown_entity_returns_error_code(cli, capsys):
    assert run(cli, "brand", "edit", "Nope", "--name", "X") == 1


def test_edit_renames_brand(cli, capsys):
    run(cli, "brand", "add", "Apple")
    capsys.readouterr()
    assert not run(cli, "brand", "edit", "Apple", "--name", "Apple Inc")
    capsys.readouterr()
    run(cli, "brand", "list")
    assert "Apple Inc" in capsys.readouterr().out


def test_no_command_prints_help_and_returns_error(cli, capsys):
    assert run(cli) == 1
    assert "usage" in capsys.readouterr().out.lower()


def test_quote_add_creates_missing_entities_with_yes(cli, capsys):
    run(cli, "vendor", "add", "Amazon")
    capsys.readouterr()
    assert not run(
        cli,
        "--yes",
        "quote",
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--brand",
        "Apple",
        "--price",
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
    run(cli, "vendor", "add", "Amazon")
    run(
        cli,
        "--yes",
        "quote",
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--brand",
        "Apple",
        "--price",
        "899",
    )
    run(cli, "watch", "add", "iPhone 15", "900")
    capsys.readouterr()

    assert not run(cli, "check")
    out = capsys.readouterr().out
    assert "iPhone 15" in out
    assert "899.00" in out


def test_check_notifies_on_the_requested_channel(cli, capsys):
    run(cli, "vendor", "add", "Amazon")
    run(
        cli,
        "--yes",
        "quote",
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--brand",
        "Apple",
        "--price",
        "899",
    )
    run(cli, "watch", "add", "iPhone 15", "900")
    capsys.readouterr()

    assert not run(cli, "check", "--notify", "stdout")
    assert "Notified via: stdout" in capsys.readouterr().out


def test_check_rejects_an_unknown_channel(cli, capsys):
    run(cli, "vendor", "add", "Amazon")
    run(
        cli,
        "--yes",
        "quote",
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--brand",
        "Apple",
        "--price",
        "899",
    )
    run(cli, "watch", "add", "iPhone 15", "900")
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
    run(cli, "vendor", "add", "EuroShop", "--currency", "EUR")
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


def test_quote_status_considering_shows_up_as_might_buy(cli, capsys):
    run(cli, "product", "add", "iPhone 15", "--brand", "Apple")
    run(cli, "vendor", "add", "Amazon")
    run(
        cli,
        "quote",
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--price",
        "40",
    )
    capsys.readouterr()

    assert not run(cli, "quote", "status", "1", "considering")
    capsys.readouterr()

    assert not run(cli, "purchase-list", "might-buy")
    out = capsys.readouterr().out
    assert "iPhone 15" in out
    assert "Considering" in out


def test_quote_status_ordered_shows_up_in_spend(cli, capsys):
    """Marking a quote ordered records the purchase spend reports."""
    run(cli, "product", "add", "iPhone 15", "--brand", "Apple")
    run(cli, "vendor", "add", "Amazon")
    run(
        cli,
        "quote",
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--price",
        "40",
    )
    capsys.readouterr()

    assert not run(cli, "quote", "status", "1", "ordered")
    capsys.readouterr()

    assert not run(cli, "spend")
    out = capsys.readouterr().out
    assert "quote-1" in out or "$40.00" in out
    assert "$40.00" in out


def test_spend_on_an_empty_database(cli, capsys):
    assert not run(cli, "spend")
    assert "No purchase orders found" in capsys.readouterr().out


def test_spend_reports_committed_money(cli, capsys):
    run(cli, "product", "add", "iPhone 15", "--brand", "Apple", "--category", "phones")
    run(cli, "vendor", "add", "Amazon")
    run(
        cli,
        "po",
        "add",
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
    run(cli, "product", "add", "iPhone 15", "--brand", "Apple")
    run(cli, "vendor", "add", "Amazon")
    run(
        cli,
        "po",
        "add",
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


def test_quote_refresh_with_nothing_tracked(cli, capsys):
    assert not run(cli, "quote", "refresh")
    assert "No quotes carry a source URL" in capsys.readouterr().out


def test_quote_list_shows_the_source_currency(cli, capsys):
    run(cli, "fx", "add", "EUR", "1.1")
    run(cli, "vendor", "add", "EuroShop", "--currency", "EUR")
    run(
        cli,
        "--yes",
        "quote",
        "add",
        "--vendor",
        "EuroShop",
        "--product",
        "iPhone 15",
        "--brand",
        "Apple",
        "--price",
        "850",
    )
    capsys.readouterr()

    assert not run(cli, "quote", "list")
    out = capsys.readouterr().out
    assert "850.00 EUR" in out
    assert "$935.00" in out


def test_spend_analysis_report_renders(cli, capsys):
    run(cli, "product", "add", "iPhone 15", "--brand", "Apple")
    run(cli, "vendor", "add", "Amazon")
    run(
        cli,
        "po",
        "add",
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

    assert not run(cli, "report", "spend")
    out = capsys.readouterr().out
    assert "<html" in out.lower()
    assert "Spend Analysis" in out
    assert "$999.00" in out


def test_check_reports_a_partial_notification_failure(cli, capsys, monkeypatch):
    """A cron run must not report success when a channel silently lost the alert."""
    monkeypatch.delenv("BUYLOG_NTFY_TOPIC", raising=False)
    run(cli, "vendor", "add", "Amazon")
    run(
        cli,
        "--yes",
        "quote",
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--brand",
        "Apple",
        "--price",
        "899",
    )
    run(cli, "watch", "add", "iPhone 15", "900")
    capsys.readouterr()

    # ntfy is unconfigured, so it fails while stdout succeeds.
    assert run(cli, "check", "--notify", "stdout,ntfy") == 1
    out = capsys.readouterr().out
    assert "Notified via: stdout" in out
    assert "Notification failed via ntfy" in out


# Command grammar: buylog <noun> <verb>
def test_noun_without_verb_prints_its_help(cli, capsys):
    assert run(cli, "quote") == 1
    out = capsys.readouterr().out
    assert "usage: buylog quote" in out
    assert "status" in out


def test_yes_is_accepted_after_the_command(cli, capsys):
    run(cli, "brand", "add", "Apple")
    capsys.readouterr()
    assert not run(cli, "brand", "rm", "Apple", "-y")
    assert "Deleted brand" in capsys.readouterr().out


def test_vendor_edit_renames_and_sets_then_clears_url(cli, capsys):
    run(cli, "vendor", "add", "Amazon")
    assert not run(
        cli, "vendor", "edit", "Amazon", "--name", "Amazon US", "--url", "https://a.com"
    )
    capsys.readouterr()
    run(cli, "vendor", "list")
    out = capsys.readouterr().out
    assert "Amazon US" in out
    assert "https://a.com" in out

    assert not run(cli, "vendor", "edit", "Amazon US", "--no-url")
    capsys.readouterr()
    run(cli, "vendor", "list")
    assert "https://a.com" not in capsys.readouterr().out


def test_vendor_edit_with_nothing_to_change_fails(cli, capsys):
    run(cli, "vendor", "add", "Amazon")
    capsys.readouterr()
    assert run(cli, "vendor", "edit", "Amazon") == 1
    assert "Nothing to update" in capsys.readouterr().out


def test_product_edit_category_shows_in_categories(cli, capsys):
    run(cli, "product", "add", "iPhone 15", "--brand", "Apple")
    assert not run(cli, "product", "edit", "iPhone 15", "--category", "phones")
    capsys.readouterr()
    assert not run(cli, "product", "categories")
    assert "phones (1 products)" in capsys.readouterr().out


def test_quote_add_from_url_rejects_shipping(cli, capsys):
    """A scraped price has no shipping; the flag must not be dropped silently."""
    code = run(
        cli,
        "quote",
        "add",
        "--vendor",
        "Amazon",
        "--product",
        "iPhone 15",
        "--url",
        "https://example.com/p",
        "--shipping",
        "5",
    )
    assert code == 1
    assert "--shipping" in capsys.readouterr().out


def test_quote_list_filters_by_status(cli, capsys):
    run(cli, "product", "add", "iPhone 15", "--brand", "Apple")
    run(cli, "product", "add", "iPad", "--brand", "Apple")
    run(cli, "vendor", "add", "Amazon")
    run(cli, "quote", "add", "-v", "Amazon", "-p", "iPhone 15", "--price", "40")
    run(cli, "quote", "add", "-v", "Amazon", "-p", "iPad", "--price", "50")
    run(cli, "quote", "status", "1", "ordered")
    capsys.readouterr()

    assert not run(cli, "quote", "list", "--status", "ordered")
    out = capsys.readouterr().out
    assert "iPhone 15" in out
    assert "iPad" not in out


def test_tag_list_needs_kind_and_id_together(cli, capsys):
    assert run(cli, "tag", "list", "product") == 1
    assert "both KIND and ID" in capsys.readouterr().out


def test_tag_and_note_share_argument_order(cli, capsys):
    run(cli, "product", "add", "iPhone 15", "--brand", "Apple")
    assert not run(cli, "tag", "add", "product", "1", "sale")
    assert not run(cli, "note", "add", "product", "1", "good price")
    capsys.readouterr()
    assert not run(cli, "tag", "list", "product", "1")
    assert "sale" in capsys.readouterr().out
    assert not run(cli, "note", "list", "product", "1")
    assert "good price" in capsys.readouterr().out


def test_watch_fires_once_until_edited(cli, capsys):
    run(cli, "vendor", "add", "Amazon")
    run(
        cli,
        "-y",
        "quote",
        "add",
        "-v",
        "Amazon",
        "-p",
        "iPhone 15",
        "-b",
        "Apple",
        "--price",
        "899",
    )
    assert not run(cli, "watch", "add", "iPhone 15", "900")
    capsys.readouterr()

    run(cli, "check")
    assert "iPhone 15" in capsys.readouterr().out
    run(cli, "check")
    assert "Nothing triggered" in capsys.readouterr().out

    assert not run(cli, "watch", "edit", "1", "950")
    assert "re-armed" in capsys.readouterr().out
    run(cli, "check")
    assert "$950.00" in capsys.readouterr().out


def test_watch_list_shows_the_target_currency(cli, capsys):
    run(cli, "product", "add", "iPhone 15", "--brand", "Apple")
    assert not run(cli, "watch", "add", "iPhone 15", "800", "--currency", "eur")
    capsys.readouterr()
    assert not run(cli, "watch", "list")
    assert "800.00 EUR" in capsys.readouterr().out


def test_watch_off_hides_the_target_from_list(cli, capsys):
    run(cli, "product", "add", "iPhone 15", "--brand", "Apple")
    run(cli, "watch", "add", "iPhone 15", "800")
    assert not run(cli, "watch", "off", "1")
    capsys.readouterr()
    run(cli, "watch", "list")
    assert "No price targets" in capsys.readouterr().out
    run(cli, "watch", "list", "--all")
    assert "Inactive" in capsys.readouterr().out


def test_purchase_list_consider_puts_quotes_on_considering(cli, capsys):
    run(cli, "vendor", "add", "Amazon")
    run(
        cli,
        "-y",
        "quote",
        "add",
        "-v",
        "Amazon",
        "-p",
        "iPhone 15",
        "-b",
        "Apple",
        "--price",
        "899",
    )
    assert not run(cli, "purchase-list", "consider", "iPhone 15")
    capsys.readouterr()
    run(cli, "purchase-list", "might-buy")
    assert "Considering" in capsys.readouterr().out
