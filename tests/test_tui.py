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
            "targets",
            "lists",
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
    session.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), base_per_unit=1.1))
    session.add(Forex(code="EUR", date=datetime.date(2026, 2, 1), base_per_unit=1.2))
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


async def test_target_handler_stores_the_currency(seeded):
    from buylog.services import PriceTargetService

    app = seeded.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._on_target_added(("iPhone 15", 800.0, "eur"))
        await pilot.pause()

        (target,) = PriceTargetService.get_all(app.session)
        assert target.currency == "EUR"
        assert target.label == "800.00 EUR"


async def test_target_handler_without_a_threshold_uses_considering(seeded):
    from buylog.services import PriceTargetService, PurchaseListService

    app = seeded.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._on_target_added(("iPhone 15", None, None))
        await pilot.pause()

        assert PriceTargetService.get_all(app.session) == []
        assert len(PurchaseListService.might_buy(app.session)) == 1


async def test_watch_key_prefills_the_selected_product(seeded):
    from textual.widgets import Input

    app = seeded.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_switch_tab("products-tab")
        await pilot.pause()
        app.action_watch()
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, seeded.AddTargetScreen)
        assert screen.query_one("#product-name", Input).value == "iPhone 15"


async def test_target_form_returns_the_currency(seeded):
    from textual.widgets import Input

    app = seeded.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        results = []
        screen = seeded.AddTargetScreen()
        await app.push_screen(screen, results.append)
        await pilot.pause()
        screen.query_one("#product-name", Input).value = "iPhone 15"
        screen.query_one("#target-price", Input).value = "800"
        screen.query_one("#currency", Input).value = " EUR "
        screen.action_save()
        await pilot.pause()

        assert results == [("iPhone 15", 800.0, "EUR")]


async def test_po_form_from_quote_matches_quote_total(seeded):
    """Pre-fills carry the quote's discount, shipping, tax, and currency."""
    from textual.widgets import Input

    from buylog.models import PurchaseOrder, Quote

    app = seeded.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        quote = app.session.query(Quote).one()
        quote.value, quote.discount = 200.0, 25.0
        quote.shipping_cost, quote.tax_rate = 20.0, 10.0
        app.session.commit()

        results = []
        screen = seeded.AddPurchaseOrderScreen(from_quote=quote)
        await app.push_screen(screen, results.append)
        await pilot.pause()
        assert screen.query_one("#unit-price", Input).value == "150.00"
        assert screen.query_one("#currency", Input).value == "USD"
        assert screen.query_one("#shipping-cost", Input).value == "20.00"
        assert screen.query_one("#tax", Input).value == "17.00"
        screen.action_save()
        await pilot.pause()

        app._on_purchase_order_added(results[0])
        po = app.session.query(PurchaseOrder).one()
        assert po.grand_total == pytest.approx(quote.total_cost)


async def test_tab_specific_keys_follow_the_active_tab(tui):
    """Footer keys that do nothing on a tab are disabled there."""
    app = tui.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_switch_tab("quotes-tab")
        await pilot.pause()
        assert app.check_action("set_status", ())
        assert app.check_action("filter_quotes", ())
        assert not app.check_action("open_url", ())

        app.action_switch_tab("vendors-tab")
        await pilot.pause()
        assert app.check_action("open_url", ())
        assert not app.check_action("set_status", ())

        app.action_switch_tab("specs-tab")
        await pilot.pause()
        assert not app.check_action("delete", ())
        assert app.check_action("add", ())


async def test_next_tab_reaches_every_tab(tui):
    """Regression: h/l skipped the Specs and POs tabs."""
    from textual.widgets import TabbedContent

    app = tui.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        seen = []
        for _ in range(len(app.TAB_ORDER)):
            seen.append(app.query_one(TabbedContent).active)
            app.action_next_tab()
            await pilot.pause()
        assert sorted(seen) == sorted(pane.id for pane in app.query("TabPane"))


async def test_targets_tab_shows_a_met_target_before_check(seeded):
    """The TUI reads `met` live; only `check` marks a target triggered."""
    from textual.widgets import DataTable

    from buylog.services import PriceTargetService

    session = seeded.Session()
    PriceTargetService.create(session, "iPhone 15", 1000.0)
    session.close()

    app = seeded.BuyerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        row = app.query_one("#targets-table", DataTable).get_row_at(0)
        assert str(row[3]) == "Met"
        (target,) = PriceTargetService.get_all(app.session)
        assert target.triggered_at is None
