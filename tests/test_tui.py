"""Smoke tests for the Textual TUI.

`tui.py` had no coverage at all, which is where two of the reported bugs lived.
These drive the real app through Textual's test pilot against a temporary
database: it must boot, populate every tab, and route writes through the
service layer.
"""

import importlib
import sys

import pytest

MODULES = ("buylog.tui", "buylog.cli", "buylog.config", "buylog")


@pytest.fixture
def tui(tmp_path, monkeypatch):
    """A freshly imported tui module bound to a temporary database."""
    monkeypatch.setenv("BUYLOG_DB_PATH", str(tmp_path / "tui.db"))
    for name in MODULES:
        sys.modules.pop(name, None)
    module = importlib.import_module("buylog.tui")
    yield module
    for name in MODULES:
        sys.modules.pop(name, None)


@pytest.fixture
def seeded(tui):
    """The tui module, with one brand, product, vendor and quote on file."""
    from buylog.services import QuoteService, VendorService

    session = tui.Session()
    VendorService.create(session, "Amazon")
    QuoteService.create(session, "Amazon", "iPhone 15", 999.0, brand_name="Apple")
    session.close()
    return tui


async def test_app_starts_and_builds_every_tab(tui):
    from textual.widgets import DataTable

    app = tui.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        for table_id in (
            "brands",
            "products",
            "vendors",
            "quotes",
            "forex",
            "alerts",
            "lists",
            "watchlist",
            "specs",
            "pos",
        ):
            table = app.query_one(f"#{table_id}-table", DataTable)
            assert table.columns, f"{table_id} table has no columns"


async def test_tables_show_seeded_rows(seeded):
    from textual.widgets import DataTable

    app = seeded.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#brands-table", DataTable).row_count == 1
        assert app.query_one("#products-table", DataTable).row_count == 1
        assert app.query_one("#vendors-table", DataTable).row_count == 1
        assert app.query_one("#quotes-table", DataTable).row_count == 1


async def test_adding_a_brand_does_not_poison_the_session(tui):
    """Regression: the failed audit write broke every later TUI operation.

    The TUI holds one session for the app's whole lifetime, so this was the
    worst instance of the bug.
    """
    from textual.widgets import DataTable

    app = tui.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._on_brand_added("Apple")
        app._on_product_added(("Apple", "iPhone 15"))
        await pilot.pause()

        assert app.query_one("#brands-table", DataTable).row_count == 1
        assert app.query_one("#products-table", DataTable).row_count == 1


async def test_quote_handler_uses_latest_forex_rate(tui):
    """Regression: a second rate for a currency raised MultipleResultsFound."""
    import datetime

    from textual.widgets import DataTable

    from buylog.models import Forex
    from buylog.services import QuoteService, VendorService

    session = tui.Session()
    session.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.1))
    session.add(Forex(code="EUR", date=datetime.date(2026, 2, 1), usd_per_unit=1.2))
    session.commit()
    VendorService.create(session, "EuroShop", currency="EUR")
    session.close()

    app = tui.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._on_quote_added(("EuroShop", "TV", "Sony", 100.0))
        await pilot.pause()

        assert app.query_one("#quotes-table", DataTable).row_count == 1
        quote = QuoteService.get_all(app.session)[0]
        assert quote.value == pytest.approx(120.0)
        assert quote.original_currency == "EUR"


async def test_deleting_a_quote_removes_its_history(seeded):
    from sqlalchemy import select

    from buylog.models import QuoteHistory
    from buylog.services import QuoteService

    app = seeded.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        quote_id = QuoteService.get_all(app.session)[0].id

        app._on_delete_confirmed(True, "quote", quote_id)
        await pilot.pause()

        assert QuoteService.get_all(app.session) == []
        assert (
            app.session.execute(
                select(QuoteHistory).where(QuoteHistory.quote_id == quote_id)
            )
            .scalars()
            .all()
            == []
        )


async def test_forex_handler_rejects_duplicate_rate(tui):
    from textual.widgets import DataTable

    app = tui.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._on_forex_added(("EUR", 1.1, "2026-01-01"))
        app._on_forex_added(("EUR", 1.2, "2026-01-01"))
        await pilot.pause()

        assert app.query_one("#forex-table", DataTable).row_count == 1


async def test_watchlist_handler_stores_the_target_currency(seeded):
    from buylog.services import WatchlistService

    app = seeded.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._on_watchlist_added(("iPhone 15", 800.0, None, "eur"))
        await pilot.pause()

        (entry,) = WatchlistService.get_all(app.session)
        assert entry.target_currency == "EUR"
        assert entry.target_label == "800.00 EUR"


async def test_watchlist_form_returns_the_currency(seeded):
    from textual.widgets import Input

    app = seeded.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        results = []
        screen = seeded.AddWatchlistScreen()
        await app.push_screen(screen, results.append)
        await pilot.pause()
        screen.query_one("#product-name", Input).value = "iPhone 15"
        screen.query_one("#target-price", Input).value = "800"
        screen.query_one("#currency", Input).value = " EUR "
        screen.action_save()
        await pilot.pause()

        assert results == [("iPhone 15", 800.0, None, "EUR")]
