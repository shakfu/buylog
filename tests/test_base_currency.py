"""One base currency per database: every amount is normalized to it."""

import datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from buylog import models
from buylog.migrate import run_migrations
from buylog.models import get_base_currency
from buylog.services import (
    ForexRefreshService,
    ForexService,
    MonitorService,
    PriceTargetService,
    PurchaseOrderService,
    QuoteService,
    SettingService,
    ValidationError,
    VendorService,
)


@pytest.fixture
def yen(dbsession):
    """A JPY database with a USD rate of 150 yen."""
    SettingService.set_base_currency(dbsession, "jpy")
    ForexService.create(dbsession, "USD", 150.0, date=datetime.date(2026, 1, 1))
    return dbsession


def test_a_new_database_records_usd(dbsession):
    row = dbsession.get(models.Setting, models.BASE_CURRENCY_KEY)
    assert row.value == "USD"
    assert get_base_currency(dbsession) == "USD"


def test_the_base_is_set_while_the_database_is_empty(dbsession):
    assert SettingService.set_base_currency(dbsession, "jpy") == "JPY"
    assert get_base_currency(dbsession) == "JPY"


def test_the_base_is_fixed_once_amounts_exist(dbsession):
    VendorService.create(dbsession, "Amazon")
    QuoteService.create(dbsession, "Amazon", "iPhone", 999.0, brand_name="Apple")

    with pytest.raises(ValidationError, match="base currency is USD"):
        SettingService.set_base_currency(dbsession, "JPY")
    # Repeating the current base is not a change.
    assert SettingService.set_base_currency(dbsession, "usd") == "USD"


def test_a_forex_rate_also_fixes_the_base(dbsession):
    """Rates are stored relative to the base, so they are amounts too."""
    ForexService.create(dbsession, "EUR", 1.1)
    with pytest.raises(ValidationError, match="1 forex"):
        SettingService.set_base_currency(dbsession, "JPY")


def test_the_base_needs_a_currency_code(dbsession):
    with pytest.raises(ValidationError, match="3 letters"):
        SettingService.set_base_currency(dbsession, "YEN!")


def test_the_base_itself_takes_no_rate(yen):
    with pytest.raises(ValidationError, match="JPY is the base"):
        ForexService.create(yen, "JPY", 1.0)


def test_a_foreign_quote_is_normalized_to_the_base(yen):
    VendorService.create(yen, "Amazon US", currency="USD")
    quote = QuoteService.create(yen, "Amazon US", "iPhone", 10.0, brand_name="Apple")

    assert quote.value == pytest.approx(1500.0)
    assert (quote.original_value, quote.original_currency) == (10.0, "USD")
    assert quote.display_price == "10.00 USD (= 1,500.00 JPY)"


def test_a_quote_in_the_base_is_stored_as_entered(yen):
    vendor = VendorService.create(yen, "Yodobashi")
    quote = QuoteService.create(yen, "Yodobashi", "iPhone", 1200.0, brand_name="Apple")

    assert vendor.currency == "JPY"
    assert quote.value == 1200.0
    assert quote.original_currency is None
    assert quote.source_price == "1,200.00 JPY"


def test_an_order_total_is_kept_in_the_base(yen):
    VendorService.create(yen, "Amazon US", currency="USD")
    QuoteService.create(yen, "Amazon US", "iPhone", 10.0, brand_name="Apple")

    order = PurchaseOrderService.create(
        yen, "PO-1", vendor_name="Amazon US", product_name="iPhone", unit_price=10.0
    )

    assert order.currency == "USD"
    assert order.grand_total_base == pytest.approx(1500.0)


def test_a_target_defaults_to_the_base_and_converts_others(yen):
    VendorService.create(yen, "Yodobashi")
    QuoteService.create(yen, "Yodobashi", "iPhone", 1400.0, brand_name="Apple")

    in_base = PriceTargetService.create(yen, "iPhone", 1300.0)
    in_usd = PriceTargetService.create(yen, "iPhone", 10.0, "USD")  # 1,500 JPY

    assert in_base.currency == "JPY"
    findings = MonitorService.check(yen)
    assert [item["target"] for item in findings["targets"]] == [in_usd]
    assert "1,400.00 JPY" in findings["messages"][0]


def test_refresh_fetches_rates_against_the_base(yen, monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"rates": {"EUR": 0.00625}}  # 1 JPY buys 0.00625 EUR

    def fake_get(url, params, timeout):
        seen.update(params)
        return Response()

    monkeypatch.setattr("requests.get", fake_get)
    stats = ForexRefreshService.refresh(yen, codes=["EUR", "JPY"])

    assert seen["base"] == "JPY"
    assert stats["updated"] == ["EUR"]
    assert ForexService.get_latest(yen, "EUR").base_per_unit == pytest.approx(160.0)


def test_migrations_rename_usd_columns_and_keep_their_values(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE forex (id INTEGER PRIMARY KEY, date DATE, "
                "code TEXT, usd_per_unit REAL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO forex (date, code, usd_per_unit) "
                "VALUES ('2026-01-01', 'EUR', 1.1)"
            )
        )

    run_migrations(engine)

    columns = {c["name"] for c in inspect(engine).get_columns("forex")}
    assert "base_per_unit" in columns
    assert "usd_per_unit" not in columns
    with engine.connect() as conn:
        rate = conn.execute(text("SELECT base_per_unit FROM forex")).scalar()
    assert rate == 1.1


# CLI
@pytest.fixture
def cli(tmp_path, monkeypatch):
    import importlib
    import sys

    monkeypatch.setenv("BUYLOG_DB_PATH", str(tmp_path / "cli.db"))
    for name in ("buylog.cli", "buylog.config", "buylog"):
        sys.modules.pop(name, None)
    yield importlib.import_module("buylog.cli")
    for name in ("buylog.cli", "buylog.config", "buylog"):
        sys.modules.pop(name, None)


def test_cli_db_init_sets_the_base_and_lists_use_it(cli, capsys):
    assert not cli.main(["db", "init", "--base", "JPY"])
    assert "Base currency: JPY" in capsys.readouterr().out

    cli.main(["vendor", "add", "Yodobashi"])
    cli.main(
        [
            "-y",
            "quote",
            "add",
            "-v",
            "Yodobashi",
            "-p",
            "iPhone",
            "-b",
            "Apple",
            "--price",
            "120000",
        ]
    )
    capsys.readouterr()
    assert not cli.main(["quote", "list"])
    out = capsys.readouterr().out
    assert "Price (JPY)" in out
    assert "120,000.00 JPY" in out


def test_cli_db_init_refuses_a_new_base_once_amounts_exist(cli, capsys):
    cli.main(["vendor", "add", "Amazon"])
    cli.main(
        [
            "-y",
            "quote",
            "add",
            "-v",
            "Amazon",
            "-p",
            "iPhone",
            "-b",
            "Apple",
            "--price",
            "999",
        ]
    )
    capsys.readouterr()

    assert cli.main(["db", "init", "--base", "JPY"]) == 1
    assert "fixed once amounts exist" in capsys.readouterr().out


def test_cli_seed_needs_a_usd_database(cli, capsys):
    cli.main(["db", "init", "--base", "JPY"])
    capsys.readouterr()
    assert cli.main(["db", "seed"]) == 1
    assert "USD database" in capsys.readouterr().out
