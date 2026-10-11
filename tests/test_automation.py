"""Tests for the automation and analytics services.

Covers `buylog check` (MonitorService), `buylog fx refresh`
(ForexRefreshService), `buylog quote refresh` (ScrapeRefreshService) and
`buylog spend` (SpendAnalyticsService).
"""

import datetime

import pytest

from buylog.models import Forex
from buylog.services import (
    ForexRefreshService,
    MonitorService,
    ProductService,
    PurchaseOrderService,
    QuoteService,
    SpecificationService,
    TagService,
    ScrapeRefreshService,
    ServiceError,
    SpendAnalyticsService,
    PriceTargetService,
    VendorService,
)


class FakeResponse:
    """Minimal stand-in for a requests Response."""

    def __init__(self, payload, error=None):
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


# ForexRefreshService
def test_fetch_rates_inverts_units_per_base(monkeypatch):
    """The API quotes units-per-USD; we store USD-per-unit."""
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update(url=url, params=params)
        return FakeResponse({"base": "USD", "rates": {"EUR": 0.8, "GBP": 0.5}})

    monkeypatch.setattr("requests.get", fake_get)

    rates = ForexRefreshService.fetch_rates(["EUR", "GBP"])

    assert rates["EUR"] == pytest.approx(1.25)
    assert rates["GBP"] == pytest.approx(2.0)
    assert captured["params"]["base"] == "USD"
    assert captured["params"]["symbols"] == "EUR,GBP"


def test_fetch_rates_ignores_unusable_values(monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: FakeResponse({"rates": {"EUR": 0.8, "XXX": 0, "YYY": "n/a"}}),
    )
    rates = ForexRefreshService.fetch_rates()
    assert set(rates) == {"EUR"}


def test_fetch_rates_reports_an_empty_body(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: FakeResponse({"rates": {}}))
    with pytest.raises(ServiceError, match="no rates"):
        ForexRefreshService.fetch_rates()


def test_fetch_rates_reports_network_failure(monkeypatch):
    import requests

    def fake_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("requests.get", fake_get)
    with pytest.raises(ServiceError, match="Failed to fetch forex rates"):
        ForexRefreshService.fetch_rates(["EUR"])


def test_refresh_stores_rates_for_vendor_currencies(dbsession, monkeypatch):
    VendorService.create(dbsession, "EuroShop", currency="EUR")
    VendorService.create(dbsession, "Amazon", currency="USD")
    monkeypatch.setattr(
        "requests.get", lambda *a, **k: FakeResponse({"rates": {"EUR": 0.8}})
    )

    stats = ForexRefreshService.refresh(dbsession, date=datetime.date(2026, 3, 1))

    assert stats["updated"] == ["EUR"]
    rate = dbsession.query(Forex).filter_by(code="EUR").one()
    assert rate.base_per_unit == pytest.approx(1.25)
    assert rate.date == datetime.date(2026, 3, 1)


def test_refresh_is_idempotent_for_a_date(dbsession, monkeypatch):
    VendorService.create(dbsession, "EuroShop", currency="EUR")
    monkeypatch.setattr(
        "requests.get", lambda *a, **k: FakeResponse({"rates": {"EUR": 0.8}})
    )
    date = datetime.date(2026, 3, 1)

    ForexRefreshService.refresh(dbsession, date=date)
    second = ForexRefreshService.refresh(dbsession, date=date)

    assert second["updated"] == []
    assert second["skipped"] == ["EUR"]


def test_refresh_with_no_foreign_vendors_does_nothing(dbsession):
    VendorService.create(dbsession, "Amazon", currency="USD")
    stats = ForexRefreshService.refresh(dbsession)
    assert stats == {"updated": [], "skipped": [], "rates": {}}


def test_refresh_reports_a_currency_the_api_omits(dbsession, monkeypatch):
    monkeypatch.setattr(
        "requests.get", lambda *a, **k: FakeResponse({"rates": {"EUR": 0.8}})
    )
    stats = ForexRefreshService.refresh(dbsession, codes=["EUR", "ZWL"])
    assert stats["updated"] == ["EUR"]
    assert stats["skipped"] == ["ZWL"]


# MonitorService
def _quote(dbsession, price):
    VendorService.create(dbsession, "Amazon")
    QuoteService.create(dbsession, "Amazon", "iPhone 15", price, brand_name="Apple")


def test_check_reports_a_met_target(dbsession):
    _quote(dbsession, 899.0)
    PriceTargetService.create(dbsession, "iPhone 15", 900.0)

    findings = MonitorService.check(dbsession)

    assert len(findings["targets"]) == 1
    assert "iPhone 15" in findings["messages"][0]
    assert "899.00" in findings["messages"][0]
    assert "$900.00" in findings["messages"][0]


def test_check_ignores_a_target_below_the_price(dbsession):
    _quote(dbsession, 999.0)
    PriceTargetService.create(dbsession, "iPhone 15", 900.0)

    assert MonitorService.check(dbsession)["targets"] == []


def test_check_fires_a_target_once(dbsession):
    """Regression: a met watchlist target was re-sent on every cron run."""
    _quote(dbsession, 750.0)
    PriceTargetService.create(dbsession, "iPhone 15", 800.0)

    assert len(MonitorService.check(dbsession)["targets"]) == 1
    assert MonitorService.check(dbsession)["targets"] == []


def test_check_fires_again_after_an_edit(dbsession):
    _quote(dbsession, 750.0)
    target = PriceTargetService.create(dbsession, "iPhone 15", 800.0)
    MonitorService.check(dbsession)

    PriceTargetService.update(dbsession, target.id, threshold=760.0)

    assert len(MonitorService.check(dbsession)["targets"]) == 1


def test_check_converts_a_foreign_target(dbsession):
    """100 EUR at 1.25 is $125, so a $110 price meets the target."""
    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), base_per_unit=1.25))
    dbsession.commit()
    _quote(dbsession, 110.0)
    PriceTargetService.create(dbsession, "iPhone 15", 100.0, "EUR")

    findings = MonitorService.check(dbsession)

    assert len(findings["targets"]) == 1
    assert "100.00 EUR" in findings["messages"][0]


def test_check_does_not_treat_a_foreign_target_as_dollars(dbsession):
    """100 EUR at 0.50 is $50. A $90 price is under 100 and over $50."""
    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), base_per_unit=0.5))
    dbsession.commit()
    _quote(dbsession, 90.0)
    PriceTargetService.create(dbsession, "iPhone 15", 100.0, "EUR")

    assert MonitorService.check(dbsession)["targets"] == []


def test_check_skips_a_target_with_no_rate(dbsession):
    _quote(dbsession, 50.0)
    target = PriceTargetService.create(dbsession, "iPhone 15", 100.0, "EUR")

    assert MonitorService.check(dbsession)["targets"] == []
    # Skipped, not used up: it fires once a rate exists.
    assert target.triggered_at is None


def test_check_ignores_an_inactive_target(dbsession):
    _quote(dbsession, 50.0)
    target = PriceTargetService.create(dbsession, "iPhone 15", 100.0)
    PriceTargetService.deactivate(dbsession, target.id)

    assert MonitorService.check(dbsession)["targets"] == []


def test_check_on_an_empty_database_finds_nothing(dbsession):
    findings = MonitorService.check(dbsession)
    assert findings["messages"] == []


# ScrapeRefreshService
def _scraped(price, currency="USD"):
    return {"price": price, "currency": currency, "title": "t", "url": "u"}


def test_scrape_refresh_records_a_price_drop(dbsession, monkeypatch):
    VendorService.create(dbsession, "Amazon")
    quote = QuoteService.create(
        dbsession, "Amazon", "iPhone 15", 999.0, brand_name="Apple"
    )
    quote.source_url = "https://example.com/iphone"
    dbsession.commit()

    monkeypatch.setattr(
        "buylog.services.ScraperService.scrape_price", lambda url: _scraped(899.0)
    )

    stats = ScrapeRefreshService.refresh(dbsession)

    assert stats["checked"] == 1
    assert len(stats["changed"]) == 1
    assert stats["changed"][0]["old_value"] == 999.0
    assert stats["changed"][0]["new_value"] == 899.0
    assert QuoteService.get_by_id(dbsession, quote.id).value == 899.0


def test_scrape_refresh_skips_an_unchanged_price(dbsession, monkeypatch):
    VendorService.create(dbsession, "Amazon")
    quote = QuoteService.create(
        dbsession, "Amazon", "iPhone 15", 999.0, brand_name="Apple"
    )
    quote.source_url = "https://example.com/iphone"
    dbsession.commit()

    monkeypatch.setattr(
        "buylog.services.ScraperService.scrape_price", lambda url: _scraped(999.0)
    )

    stats = ScrapeRefreshService.refresh(dbsession)
    assert stats["unchanged"] == 1
    assert stats["changed"] == []


def test_scrape_refresh_ignores_quotes_without_a_url(dbsession):
    VendorService.create(dbsession, "Amazon")
    QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0, brand_name="Apple")
    assert ScrapeRefreshService.refresh(dbsession)["checked"] == 0


def test_scrape_refresh_reports_a_dead_link_without_stopping(dbsession, monkeypatch):
    VendorService.create(dbsession, "Amazon")
    for name, url in (("iPhone 15", "https://dead"), ("iPhone 14", "https://live")):
        quote = QuoteService.create(
            dbsession, "Amazon", name, 999.0, brand_name="Apple"
        )
        quote.source_url = url
    dbsession.commit()

    def fake_scrape(url):
        if url == "https://dead":
            raise ServiceError("Failed to fetch URL: 404")
        return _scraped(899.0)

    monkeypatch.setattr("buylog.services.ScraperService.scrape_price", fake_scrape)

    stats = ScrapeRefreshService.refresh(dbsession)

    assert len(stats["errors"]) == 1
    assert len(stats["changed"]) == 1


def test_scrape_refresh_converts_a_foreign_scraped_price(dbsession, monkeypatch):
    dbsession.add(Forex(code="GBP", date=datetime.date(2026, 1, 1), base_per_unit=1.25))
    dbsession.commit()
    VendorService.create(dbsession, "UKShop")
    quote = QuoteService.create(
        dbsession, "UKShop", "iPhone 15", 999.0, brand_name="Apple"
    )
    quote.source_url = "https://example.co.uk/iphone"
    dbsession.commit()

    monkeypatch.setattr(
        "buylog.services.ScraperService.scrape_price",
        lambda url: _scraped(800.0, "GBP"),
    )

    ScrapeRefreshService.refresh(dbsession)

    refreshed = QuoteService.get_by_id(dbsession, quote.id)
    assert refreshed.value == pytest.approx(1000.0)
    assert refreshed.original_currency == "GBP"


# SpendAnalyticsService
@pytest.fixture
def spending(dbsession):
    """Two vendors, two products with categories, and three purchase orders."""
    VendorService.create(dbsession, "Amazon")
    VendorService.create(dbsession, "BestBuy")
    ProductService.create(dbsession, "iPhone 15", "Apple", category="phones")
    ProductService.create(dbsession, "MacBook", "Apple", category="laptops")

    QuoteService.create(dbsession, "Amazon", "iPhone 15", 900.0)
    QuoteService.create(dbsession, "BestBuy", "MacBook", 2000.0)

    PurchaseOrderService.create(
        dbsession,
        "PO-1",
        "Amazon",
        "iPhone 15",
        1000.0,
        order_date=datetime.date(2026, 1, 15),
    )
    PurchaseOrderService.create(
        dbsession,
        "PO-2",
        "BestBuy",
        "MacBook",
        2000.0,
        order_date=datetime.date(2026, 1, 20),
    )
    PurchaseOrderService.create(
        dbsession,
        "PO-3",
        "Amazon",
        "iPhone 15",
        800.0,
        order_date=datetime.date(2026, 2, 3),
    )
    return dbsession


def test_spend_by_month(spending):
    rows = SpendAnalyticsService.spend_by_month(spending)
    assert [r["period"] for r in rows] == ["2026-01", "2026-02"]
    assert rows[0]["total"] == pytest.approx(3000.0)
    assert rows[1]["total"] == pytest.approx(800.0)


def test_spend_by_vendor_is_ordered_by_total(spending):
    rows = SpendAnalyticsService.spend_by_vendor(spending)
    assert [r["vendor"] for r in rows] == ["BestBuy", "Amazon"]
    assert rows[0]["total"] == pytest.approx(2000.0)
    assert rows[1]["total"] == pytest.approx(1800.0)


def test_spend_by_category(spending):
    rows = SpendAnalyticsService.spend_by_category(spending)
    assert {r["category"]: r["total"] for r in rows} == {
        "laptops": pytest.approx(2000.0),
        "phones": pytest.approx(1800.0),
    }


def test_spend_uses_a_tag_when_the_product_has_no_category(dbsession):
    VendorService.create(dbsession, "Amazon")
    product = ProductService.create(dbsession, "Lens", "Sony")
    TagService.add_to_entity(dbsession, "cameras", "product", product.id)
    PurchaseOrderService.create(dbsession, "PO-LENS", "Amazon", "Lens", 400.0)

    rows = SpendAnalyticsService.spend_by_category(dbsession)
    assert rows == [{"category": "cameras", "total": pytest.approx(400.0), "orders": 1}]


def test_spend_uses_a_specification_when_the_product_has_no_category(dbsession):
    VendorService.create(dbsession, "Amazon")
    product = ProductService.create(dbsession, "Body", "Sony")
    spec = SpecificationService.create(dbsession, "Cameras")
    product.specification = spec
    dbsession.commit()
    PurchaseOrderService.create(dbsession, "PO-BODY", "Amazon", "Body", 900.0)

    rows = SpendAnalyticsService.spend_by_category(dbsession)
    assert rows[0]["category"] == "Cameras"
    assert rows[0]["total"] == pytest.approx(900.0)


def test_spend_prefers_category_over_a_tag(dbsession):
    VendorService.create(dbsession, "Amazon")
    product = ProductService.create(dbsession, "Lens", "Sony", category="optics")
    TagService.add_to_entity(dbsession, "cameras", "product", product.id)
    PurchaseOrderService.create(dbsession, "PO-OPT", "Amazon", "Lens", 400.0)

    rows = SpendAnalyticsService.spend_by_category(dbsession)
    assert rows[0]["category"] == "optics"


def test_spend_respects_a_date_range(spending):
    rows = SpendAnalyticsService.spend_by_month(
        spending, start=datetime.date(2026, 2, 1)
    )
    assert [r["period"] for r in rows] == ["2026-02"]


def test_price_variance_flags_overpayment(spending):
    rows = SpendAnalyticsService.price_variance(spending)
    by_po = {r["po_number"]: r for r in rows}

    # Paid 1000 against a best recorded price of 900.
    assert by_po["PO-1"]["delta"] == pytest.approx(100.0)
    # Paid 800 against the same 900, so this one beat every quote on file.
    assert by_po["PO-3"]["delta"] == pytest.approx(-100.0)
    # Worst overpayment first.
    assert rows[0]["po_number"] == "PO-1"


def test_summary_totals(spending):
    totals = SpendAnalyticsService.summary(spending)
    assert totals["orders"] == 3
    assert totals["total"] == pytest.approx(3800.0)
    assert totals["average"] == pytest.approx(3800.0 / 3)


def test_summary_on_an_empty_database(dbsession):
    totals = SpendAnalyticsService.summary(dbsession)
    assert totals["orders"] == 0
    assert totals["total"] == 0
    assert totals["average"] == 0


def test_spend_uses_usd_totals_for_foreign_orders(dbsession):
    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), base_per_unit=1.2))
    dbsession.commit()
    VendorService.create(dbsession, "EuroShop", currency="EUR")
    ProductService.create(dbsession, "TV", "Sony")
    PurchaseOrderService.create(
        dbsession,
        "PO-EUR",
        "EuroShop",
        "TV",
        100.0,
        quantity=2,
        order_date=datetime.date(2026, 1, 5),
    )

    rows = SpendAnalyticsService.spend_by_vendor(dbsession)
    assert rows[0]["total"] == pytest.approx(240.0)


def test_spend_excludes_cancelled_orders(dbsession):
    """Money that was explicitly never committed is not spend."""
    VendorService.create(dbsession, "Amazon")
    ProductService.create(dbsession, "iPhone 15", "Apple")
    PurchaseOrderService.create(
        dbsession,
        "PO-OK",
        "Amazon",
        "iPhone 15",
        100.0,
        order_date=datetime.date(2026, 1, 1),
    )
    PurchaseOrderService.create(
        dbsession,
        "PO-CANCELLED",
        "Amazon",
        "iPhone 15",
        900.0,
        order_date=datetime.date(2026, 1, 2),
    )
    PurchaseOrderService.update_status(dbsession, "PO-CANCELLED", "cancelled")

    totals = SpendAnalyticsService.summary(dbsession)
    assert totals["orders"] == 1
    assert totals["total"] == pytest.approx(100.0)


def test_spend_includes_cancelled_orders_on_request(dbsession):
    VendorService.create(dbsession, "Amazon")
    ProductService.create(dbsession, "iPhone 15", "Apple")
    PurchaseOrderService.create(
        dbsession,
        "PO-1",
        "Amazon",
        "iPhone 15",
        100.0,
        order_date=datetime.date(2026, 1, 1),
    )
    PurchaseOrderService.update_status(dbsession, "PO-1", "cancelled")

    assert SpendAnalyticsService._orders(dbsession, include_cancelled=True)


def test_spend_date_filter_keeps_undated_orders(dbsession):
    """An order with no order_date must not vanish when a range is given."""
    VendorService.create(dbsession, "Amazon")
    ProductService.create(dbsession, "iPhone 15", "Apple")
    PurchaseOrderService.create(dbsession, "PO-UNDATED", "Amazon", "iPhone 15", 500.0)

    unfiltered = SpendAnalyticsService.summary(dbsession)
    # created_at is "now", so a range that spans it must include the order.
    filtered = SpendAnalyticsService.summary(
        dbsession,
        start=datetime.date(2000, 1, 1),
        end=datetime.date(2100, 1, 1),
    )

    assert unfiltered["orders"] == 1
    assert filtered["orders"] == unfiltered["orders"]
    assert filtered["total"] == pytest.approx(unfiltered["total"])


def test_spend_buckets_an_undated_order_by_creation_date(dbsession):
    VendorService.create(dbsession, "Amazon")
    ProductService.create(dbsession, "iPhone 15", "Apple")
    PurchaseOrderService.create(dbsession, "PO-UNDATED", "Amazon", "iPhone 15", 500.0)

    rows = SpendAnalyticsService.spend_by_month(dbsession)
    assert len(rows) == 1
    assert rows[0]["period"] != "unknown"
