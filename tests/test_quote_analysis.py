"""Tests for quote analysis features: best prices, history tracking, and price alerts"""

import pytest
from buylog.services import (
    ProductService,
    VendorService,
    QuoteService,
    QuoteHistoryService,
    PriceAlertService,
    ComparisonService,
    SpecificationService,
    TagService,
    ValidationError,
    NotFoundError,
)


# Quote total_cost property tests
class TestQuoteTotalCost:
    """Tests for Quote.total_cost property"""

    def test_total_cost_no_extras(self, dbsession):
        """Base price with no shipping, tax, or discount"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 1000.0)

        assert quote.total_cost == 1000.0

    def test_total_cost_with_discount(self, dbsession):
        """Price with discount applied"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 1000.0)
        quote.discount = 10.0  # 10% discount
        dbsession.commit()

        # 1000 * 0.9 = 900
        assert quote.total_cost == 900.0

    def test_total_cost_with_shipping(self, dbsession):
        """Price with shipping cost"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 1000.0)
        quote.shipping_cost = 50.0
        dbsession.commit()

        # 1000 + 50 = 1050
        assert quote.total_cost == 1050.0

    def test_total_cost_with_tax(self, dbsession):
        """Price with tax rate"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 1000.0)
        quote.tax_rate = 8.5  # 8.5% tax
        dbsession.commit()

        # 1000 * 1.085 = 1085
        assert quote.total_cost == 1085.0

    def test_total_cost_full_calculation(self, dbsession):
        """Price with discount, shipping, and tax"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 1000.0)
        quote.discount = 10.0  # 10% discount
        quote.shipping_cost = 50.0
        quote.tax_rate = 10.0  # 10% tax
        dbsession.commit()

        # (1000 * 0.9 + 50) * 1.10 = (900 + 50) * 1.10 = 950 * 1.10 = 1045
        assert quote.total_cost == 1045.0


# QuoteHistoryService tests
class TestQuoteHistoryService:
    """Tests for QuoteHistoryService"""

    def test_record_change_create(self, dbsession):
        """Record a quote creation"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)

        history = QuoteHistoryService.record_change(
            dbsession, quote, None, 999.0, "create"
        )

        assert history.quote_id == quote.id
        assert history.old_value is None
        assert history.new_value == 999.0
        assert history.change_type == "create"

    def test_record_change_update(self, dbsession):
        """Record a price update"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)

        history = QuoteHistoryService.record_change(
            dbsession, quote, 999.0, 899.0, "update"
        )

        assert history.old_value == 999.0
        assert history.new_value == 899.0
        assert history.change_type == "update"

    def test_get_history(self, dbsession):
        """Get history for a specific quote"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)

        # create() already records the "create" entry
        QuoteHistoryService.record_change(dbsession, quote, 999.0, 899.0, "update")

        history = QuoteHistoryService.get_history(dbsession, quote.id)

        assert len(history) == 2
        # Most recent first
        assert history[0].new_value == 899.0
        assert history[1].new_value == 999.0

    def test_get_product_history(self, dbsession):
        """Get history across all quotes for a product"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        VendorService.create(dbsession, "BestBuy", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")

        # create() records a "create" history entry for each quote
        quote1 = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)
        QuoteService.create(dbsession, "BestBuy", "iPhone 15", 1049.0)

        history = QuoteHistoryService.get_product_history(dbsession, quote1.product_id)

        assert len(history) == 2

    def test_compute_trend_new(self, dbsession):
        """Compute trend for new quote"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)

        h = QuoteHistoryService.record_change(dbsession, quote, None, 999.0, "create")
        history = [h]

        trend = QuoteHistoryService.compute_trend(history)
        assert trend == "new"

    def test_compute_trend_up(self, dbsession):
        """Compute trend when price went up"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)

        QuoteHistoryService.record_change(dbsession, quote, None, 999.0, "create")
        QuoteHistoryService.record_change(dbsession, quote, 999.0, 1099.0, "update")

        history = QuoteHistoryService.get_history(dbsession, quote.id)
        trend = QuoteHistoryService.compute_trend(history)
        assert trend == "up"

    def test_compute_trend_down(self, dbsession):
        """Compute trend when price went down"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)

        QuoteHistoryService.record_change(dbsession, quote, None, 999.0, "create")
        QuoteHistoryService.record_change(dbsession, quote, 999.0, 899.0, "update")

        history = QuoteHistoryService.get_history(dbsession, quote.id)
        trend = QuoteHistoryService.compute_trend(history)
        assert trend == "down"

    def test_compute_trend_stable(self, dbsession):
        """Compute trend when price is stable"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)

        QuoteHistoryService.record_change(dbsession, quote, None, 999.0, "create")
        QuoteHistoryService.record_change(dbsession, quote, 999.0, 999.0, "update")

        history = QuoteHistoryService.get_history(dbsession, quote.id)
        trend = QuoteHistoryService.compute_trend(history)
        assert trend == "stable"

    def test_compute_trend_empty(self, dbsession):
        """Compute trend with no history"""
        trend = QuoteHistoryService.compute_trend([])
        assert trend == "new"


# PriceAlertService tests
class TestPriceAlertService:
    """Tests for PriceAlertService"""

    def test_create_alert(self, dbsession):
        """Create a price alert"""
        ProductService.create(dbsession, "iPhone 15", "Apple")

        alert = PriceAlertService.create(dbsession, "iPhone 15", 900.0)

        assert alert.id is not None
        assert alert.threshold_value == 900.0
        assert alert.active == 1
        assert alert.triggered_at is None

    def test_create_alert_product_not_found(self, dbsession):
        """Create alert for non-existent product raises error"""
        with pytest.raises(NotFoundError, match="not found"):
            PriceAlertService.create(dbsession, "NonExistent", 900.0)

    def test_create_alert_invalid_threshold(self, dbsession):
        """Create alert with invalid threshold raises error"""
        ProductService.create(dbsession, "iPhone 15", "Apple")

        with pytest.raises(ValidationError, match="positive"):
            PriceAlertService.create(dbsession, "iPhone 15", -100.0)

    def test_check_alerts_triggers(self, dbsession):
        """Check alerts triggers when price drops below threshold"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)

        alert = PriceAlertService.create(dbsession, "iPhone 15", 900.0)

        # Price above threshold - should not trigger
        triggered = PriceAlertService.check_alerts(dbsession, quote.product, 950.0)
        assert len(triggered) == 0

        # Price at threshold - should trigger
        triggered = PriceAlertService.check_alerts(dbsession, quote.product, 900.0)
        assert len(triggered) == 1
        assert triggered[0].id == alert.id
        assert triggered[0].triggered_at is not None

    def test_check_alerts_below_threshold(self, dbsession):
        """Check alerts triggers when price is below threshold"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)

        alert = PriceAlertService.create(dbsession, "iPhone 15", 900.0)

        triggered = PriceAlertService.check_alerts(dbsession, quote.product, 850.0)
        assert len(triggered) == 1
        assert triggered[0].id == alert.id

    def test_check_alerts_already_triggered(self, dbsession):
        """Already triggered alerts don't trigger again"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)

        PriceAlertService.create(dbsession, "iPhone 15", 900.0)

        # First trigger
        triggered = PriceAlertService.check_alerts(dbsession, quote.product, 850.0)
        assert len(triggered) == 1

        # Second check should not trigger again
        triggered = PriceAlertService.check_alerts(dbsession, quote.product, 800.0)
        assert len(triggered) == 0

    def test_get_active(self, dbsession):
        """Get all active alerts"""
        ProductService.create(dbsession, "iPhone 15", "Apple")
        ProductService.create(dbsession, "Galaxy S24", "Samsung")

        alert1 = PriceAlertService.create(dbsession, "iPhone 15", 900.0)
        alert2 = PriceAlertService.create(dbsession, "Galaxy S24", 1000.0)

        # Deactivate one
        PriceAlertService.deactivate(dbsession, alert2.id)

        active = PriceAlertService.get_active(dbsession)
        assert len(active) == 1
        assert active[0].id == alert1.id

    def test_get_triggered(self, dbsession):
        """Get all triggered alerts"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)

        PriceAlertService.create(dbsession, "iPhone 15", 900.0)
        PriceAlertService.create(dbsession, "iPhone 15", 800.0)

        # Trigger one
        PriceAlertService.check_alerts(dbsession, quote.product, 850.0)

        triggered = PriceAlertService.get_triggered(dbsession)
        assert len(triggered) == 1
        assert triggered[0].threshold_value == 900.0

    def test_deactivate(self, dbsession):
        """Deactivate an alert"""
        ProductService.create(dbsession, "iPhone 15", "Apple")
        alert = PriceAlertService.create(dbsession, "iPhone 15", 900.0)

        assert alert.active == 1

        updated = PriceAlertService.deactivate(dbsession, alert.id)

        assert updated.active == 0

    def test_deactivate_not_found(self, dbsession):
        """Deactivate non-existent alert raises error"""
        with pytest.raises(NotFoundError, match="not found"):
            PriceAlertService.deactivate(dbsession, 99999)


# QuoteService extension tests
class TestQuoteServiceExtensions:
    """Tests for QuoteService extensions"""

    def test_get_best_prices_by_product(self, dbsession):
        """Get best price for each product"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        VendorService.create(dbsession, "BestBuy", currency="USD")
        VendorService.create(dbsession, "B&H", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")

        QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)
        QuoteService.create(dbsession, "BestBuy", "iPhone 15", 1049.0)
        best = QuoteService.create(dbsession, "B&H", "iPhone 15", 949.0)

        best_prices = QuoteService.get_best_prices_by_product(dbsession)

        assert len(best_prices) == 1
        assert best_prices[best.product_id].id == best.id
        assert best_prices[best.product_id].value == 949.0

    def test_get_best_prices_multiple_products(self, dbsession):
        """Get best prices for multiple products"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        VendorService.create(dbsession, "BestBuy", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        ProductService.create(dbsession, "Galaxy S24", "Samsung")

        iphone_best = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)
        QuoteService.create(dbsession, "BestBuy", "iPhone 15", 1049.0)

        QuoteService.create(dbsession, "Amazon", "Galaxy S24", 1299.0)
        galaxy_best = QuoteService.create(dbsession, "BestBuy", "Galaxy S24", 1199.0)

        best_prices = QuoteService.get_best_prices_by_product(dbsession)

        assert len(best_prices) == 2
        assert best_prices[iphone_best.product_id].value == 999.0
        assert best_prices[galaxy_best.product_id].value == 1199.0

    def test_update_price(self, dbsession):
        """Update quote price and record history"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)

        updated = QuoteService.update_price(dbsession, quote.id, 899.0)

        assert updated.value == 899.0

        # Check history was recorded (newest first, after the "create" entry)
        history = QuoteHistoryService.get_history(dbsession, quote.id)
        assert len(history) == 2
        assert history[0].old_value == 999.0
        assert history[0].new_value == 899.0
        assert history[1].change_type == "create"

    def test_update_price_triggers_alert(self, dbsession):
        """Updating price triggers relevant alerts"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)

        alert = PriceAlertService.create(dbsession, "iPhone 15", 900.0)

        # Update price below threshold
        QuoteService.update_price(dbsession, quote.id, 850.0)

        # Check alert was triggered
        dbsession.refresh(alert)
        assert alert.triggered_at is not None

    def test_update_price_not_found(self, dbsession):
        """Update price for non-existent quote raises error"""
        with pytest.raises(NotFoundError, match="not found"):
            QuoteService.update_price(dbsession, 99999, 899.0)

    def test_update_price_negative(self, dbsession):
        """Update price with negative value raises error"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)

        with pytest.raises(ValidationError, match="cannot be negative"):
            QuoteService.update_price(dbsession, quote.id, -100.0)


# ComparisonService tests
class TestComparisonService:
    """Tests for ComparisonService"""

    def test_compare_product(self, dbsession):
        """Compare prices for a specific product"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        VendorService.create(dbsession, "BestBuy", currency="USD")
        VendorService.create(dbsession, "B&H", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")

        QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)
        QuoteService.create(dbsession, "BestBuy", "iPhone 15", 1049.0)
        QuoteService.create(dbsession, "B&H", "iPhone 15", 949.0)

        result = ComparisonService.compare_product(dbsession, "iPhone 15")

        assert result["product"].name == "iPhone 15"
        assert len(result["quotes"]) == 3
        assert result["best_price"] == 949.0
        assert result["worst_price"] == 1049.0
        assert result["savings"] == 100.0
        assert result["num_vendors"] == 3

    def test_compare_product_not_found(self, dbsession):
        """Compare non-existent product raises error"""
        with pytest.raises(NotFoundError, match="not found"):
            ComparisonService.compare_product(dbsession, "NonExistent")

    def test_compare_product_no_quotes(self, dbsession):
        """Compare product with no quotes"""
        ProductService.create(dbsession, "iPhone 15", "Apple")

        result = ComparisonService.compare_product(dbsession, "iPhone 15")

        assert result["quotes"] == []
        assert result["best_price"] is None

    def test_compare_by_search(self, dbsession):
        """Compare products by search term"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        ProductService.create(dbsession, "iPhone 14", "Apple")
        ProductService.create(dbsession, "Galaxy S24", "Samsung")

        QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)
        QuoteService.create(dbsession, "Amazon", "iPhone 14", 799.0)
        QuoteService.create(dbsession, "Amazon", "Galaxy S24", 899.0)

        result = ComparisonService.compare_by_search(dbsession, "iPhone")

        assert result["search_term"] == "iPhone"
        assert result["total_products"] == 2
        # Should be sorted by best price
        assert result["products"][0]["product"].name == "iPhone 14"

    def test_compare_by_search_not_found(self, dbsession):
        """Compare by search with no matches raises error"""
        with pytest.raises(NotFoundError, match="No products found"):
            ComparisonService.compare_by_search(dbsession, "NonExistent")

    def test_compare_by_category(self, dbsession):
        """Compare products by category"""
        VendorService.create(dbsession, "Amazon", currency="USD")

        # Create products with categories
        product1 = ProductService.create(dbsession, "iPhone 15", "Apple")
        product1.category = "Mobile Phones"
        product2 = ProductService.create(dbsession, "Galaxy S24", "Samsung")
        product2.category = "Mobile Phones"
        product3 = ProductService.create(dbsession, "MacBook", "Apple")
        product3.category = "Laptops"
        dbsession.commit()

        QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)
        QuoteService.create(dbsession, "Amazon", "Galaxy S24", 899.0)
        QuoteService.create(dbsession, "Amazon", "MacBook", 1299.0)

        result = ComparisonService.compare_by_category(dbsession, "Mobile Phones")

        assert result["category"] == "Mobile Phones"
        assert result["total_products"] == 2

    def test_compare_finds_a_product_filed_only_as_a_tag(self, dbsession):
        VendorService.create(dbsession, "Amazon", currency="USD")
        product = ProductService.create(dbsession, "Lens", "Sony")
        TagService.add_to_entity(dbsession, "cameras", "product", product.id)
        QuoteService.create(dbsession, "Amazon", "Lens", 100.0)

        result = ComparisonService.compare_by_category(dbsession, "cameras")

        assert result["total_products"] == 1
        assert result["products"][0]["product"].name == "Lens"

    def test_compare_finds_a_product_filed_only_as_a_specification(self, dbsession):
        VendorService.create(dbsession, "Amazon", currency="USD")
        product = ProductService.create(dbsession, "Body", "Sony")
        product.specification = SpecificationService.create(dbsession, "Cameras")
        dbsession.commit()
        QuoteService.create(dbsession, "Amazon", "Body", 900.0)

        result = ComparisonService.compare_by_category(dbsession, "Cameras")

        assert result["total_products"] == 1
        assert result["products"][0]["product"].name == "Body"

    def test_several_tags_form_one_label(self, dbsession):
        """Tags join into one sorted label, so a product counts once."""
        ProductService.create(dbsession, "Lens", "Sony")
        product = ProductService.get_by_name(dbsession, "Lens")
        TagService.add_to_entity(dbsession, "optics", "product", product.id)
        TagService.add_to_entity(dbsession, "cameras", "product", product.id)

        assert ComparisonService.classification_counts(dbsession) == {
            "cameras, optics": 1
        }

    def test_compare_uses_category_ahead_of_a_tag(self, dbsession):
        VendorService.create(dbsession, "Amazon", currency="USD")
        product = ProductService.create(dbsession, "Lens", "Sony", category="optics")
        TagService.add_to_entity(dbsession, "cameras", "product", product.id)
        QuoteService.create(dbsession, "Amazon", "Lens", 100.0)

        with pytest.raises(NotFoundError):
            ComparisonService.compare_by_category(dbsession, "cameras")
        result = ComparisonService.compare_by_category(dbsession, "optics")
        assert result["total_products"] == 1

    def test_compare_by_category_not_found(self, dbsession):
        """Compare by category with no matches raises error"""
        with pytest.raises(NotFoundError, match="No products found"):
            ComparisonService.compare_by_category(dbsession, "NonExistent")

    def test_compare_by_brand(self, dbsession):
        """Compare products by brand"""
        VendorService.create(dbsession, "Amazon", currency="USD")
        ProductService.create(dbsession, "iPhone 15", "Apple")
        ProductService.create(dbsession, "MacBook", "Apple")
        ProductService.create(dbsession, "Galaxy S24", "Samsung")

        QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0)
        QuoteService.create(dbsession, "Amazon", "MacBook", 1299.0)
        QuoteService.create(dbsession, "Amazon", "Galaxy S24", 899.0)

        result = ComparisonService.compare_by_brand(dbsession, "Apple")

        assert result["brand"].name == "Apple"
        assert result["total_products"] == 2

    def test_compare_by_brand_not_found(self, dbsession):
        """Compare by brand not found raises error"""
        with pytest.raises(NotFoundError, match="not found"):
            ComparisonService.compare_by_brand(dbsession, "NonExistent")

    def test_get_categories(self, dbsession):
        """Get all product categories"""
        product1 = ProductService.create(dbsession, "iPhone 15", "Apple")
        product1.category = "Mobile Phones"
        product2 = ProductService.create(dbsession, "MacBook", "Apple")
        product2.category = "Laptops"
        product3 = ProductService.create(dbsession, "iPad", "Apple")
        product3.category = "Tablets"
        dbsession.commit()

        categories = ComparisonService.get_categories(dbsession)

        assert "Mobile Phones" in categories
        assert "Laptops" in categories
        assert "Tablets" in categories

    def test_set_product_category(self, dbsession):
        """Set product category"""
        ProductService.create(dbsession, "iPhone 15", "Apple")

        product = ComparisonService.set_product_category(
            dbsession, "iPhone 15", "Mobile Phones"
        )

        assert product.category == "Mobile Phones"

    def test_set_product_category_not_found(self, dbsession):
        """Set category for non-existent product raises error"""
        with pytest.raises(NotFoundError, match="not found"):
            ComparisonService.set_product_category(dbsession, "NonExistent", "Category")
