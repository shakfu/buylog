"""Tests for service layer business logic"""

import datetime
import pytest
from buylog.services import (
    BrandService,
    ProductService,
    VendorService,
    QuoteService,
    ForexService,
    ValidationError,
    DuplicateError,
    NotFoundError,
)
from buylog.models import Brand, Product, Forex, Specification


# BrandService Tests
def test_brand_service_create(dbsession):
    """Test creating a brand via service layer"""
    brand = BrandService.create(dbsession, "Apple")
    assert brand.id is not None
    assert brand.name == "Apple"


def test_brand_service_create_strips_whitespace(dbsession):
    """Test that service layer strips whitespace"""
    brand = BrandService.create(dbsession, "  Apple  ")
    assert brand.name == "Apple"


def test_brand_service_create_empty_name(dbsession):
    """Test creating brand with empty name raises error"""
    with pytest.raises(ValidationError, match="cannot be empty"):
        BrandService.create(dbsession, "")


def test_brand_service_create_whitespace_only(dbsession):
    """Test creating brand with whitespace only raises error"""
    with pytest.raises(ValidationError, match="cannot be empty"):
        BrandService.create(dbsession, "   ")


def test_brand_service_create_too_long(dbsession):
    """Test creating brand with name too long raises error"""
    long_name = "A" * 256
    with pytest.raises(ValidationError, match="too long"):
        BrandService.create(dbsession, long_name)


def test_brand_service_create_duplicate(dbsession):
    """Test creating duplicate brand raises error"""
    BrandService.create(dbsession, "Apple")
    with pytest.raises(DuplicateError, match="already exists"):
        BrandService.create(dbsession, "Apple")


def test_brand_service_get_all(dbsession):
    """Test getting all brands"""
    BrandService.create(dbsession, "Apple")
    BrandService.create(dbsession, "Samsung")

    brands = BrandService.get_all(dbsession)
    assert len(brands) == 2
    assert {b.name for b in brands} == {"Apple", "Samsung"}


def test_brand_service_get_all_with_filter(dbsession):
    """Test getting brands with filter"""
    BrandService.create(dbsession, "Apple")
    BrandService.create(dbsession, "Samsung")

    brands = BrandService.get_all(dbsession, filter_by="App")
    assert len(brands) == 1
    assert brands[0].name == "Apple"


def test_brand_service_get_all_with_pagination(dbsession):
    """Test pagination in get_all"""
    for i in range(5):
        BrandService.create(dbsession, f"Brand{i}")

    # Get first 2
    brands = BrandService.get_all(dbsession, limit=2, offset=0)
    assert len(brands) == 2

    # Get next 2
    brands = BrandService.get_all(dbsession, limit=2, offset=2)
    assert len(brands) == 2


def test_brand_service_update(dbsession):
    """Test updating a brand"""
    BrandService.create(dbsession, "Apple")
    brand = BrandService.update(dbsession, "Apple", "Apple Inc.")

    assert brand.name == "Apple Inc."


def test_brand_service_update_not_found(dbsession):
    """Test updating non-existent brand raises error"""
    with pytest.raises(NotFoundError, match="not found"):
        BrandService.update(dbsession, "NonExistent", "NewName")


def test_brand_service_delete(dbsession):
    """Test deleting a brand"""
    BrandService.create(dbsession, "Apple")
    BrandService.delete(dbsession, "Apple")

    brand = BrandService.get_by_name(dbsession, "Apple")
    assert brand is None


def test_brand_service_delete_not_found(dbsession):
    """Test deleting non-existent brand raises error"""
    with pytest.raises(NotFoundError, match="not found"):
        BrandService.delete(dbsession, "NonExistent")


# ProductService Tests
def test_product_service_create(dbsession):
    """Test creating a product"""
    BrandService.create(dbsession, "Apple")
    product = ProductService.create(dbsession, "iPhone 15", "Apple")

    assert product.id is not None
    assert product.name == "iPhone 15"
    assert product.brand.name == "Apple"


def test_product_service_create_with_new_brand(dbsession):
    """Test creating product automatically creates brand"""
    product = ProductService.create(dbsession, "iPhone 15", "Apple")

    assert product.brand.name == "Apple"
    brand = BrandService.get_by_name(dbsession, "Apple")
    assert brand is not None


def test_product_service_create_duplicate(dbsession):
    """Test creating duplicate product raises error"""
    ProductService.create(dbsession, "iPhone 15", "Apple")
    with pytest.raises(DuplicateError, match="already exists"):
        ProductService.create(dbsession, "iPhone 15", "Samsung")


def test_product_service_get_all_eager_loads_brand(dbsession):
    """Test get_all eagerly loads brands (no N+1)"""
    ProductService.create(dbsession, "iPhone 15", "Apple")
    ProductService.create(dbsession, "Galaxy S23", "Samsung")

    products = ProductService.get_all(dbsession)
    # Access brand.name should not trigger additional queries
    # (verified by lack of SQL in logs with DEBUG enabled)
    for p in products:
        assert p.brand.name  # Should not cause N+1 query


# VendorService Tests
def test_vendor_service_create(dbsession):
    """Test creating a vendor"""
    vendor = VendorService.create(dbsession, "Amazon", currency="USD")

    assert vendor.id is not None
    assert vendor.name == "Amazon"
    assert vendor.currency == "USD"


def test_vendor_service_create_validates_currency_length(dbsession):
    """Test currency code must be 3 characters"""
    with pytest.raises(ValidationError, match="3 characters"):
        VendorService.create(dbsession, "Amazon", currency="US")


def test_vendor_service_create_validates_discount_range(dbsession):
    """Test discount must be 0-100"""
    with pytest.raises(ValidationError, match="between 0 and 100"):
        VendorService.create(dbsession, "Amazon", discount=150.0)


def test_vendor_service_create_normalizes_currency(dbsession):
    """Test currency code is normalized to uppercase"""
    vendor = VendorService.create(dbsession, "Amazon", currency="eur")
    assert vendor.currency == "EUR"


def test_vendor_service_create_with_enhanced_fields(dbsession):
    """Test creating vendor with all enhanced fields"""
    vendor = VendorService.create(
        dbsession,
        "Acme Corp",
        currency="USD",
        contact_person="John Smith",
        email="john@acme.com",
        phone="+1-555-1234",
        website="https://acme.com",
        address_line1="123 Main St",
        address_line2="Suite 100",
        city="New York",
        state="NY",
        postal_code="10001",
        country="USA",
        tax_id="12-3456789",
        payment_terms="Net 30",
    )

    assert vendor.id is not None
    assert vendor.contact_person == "John Smith"
    assert vendor.email == "john@acme.com"
    assert vendor.phone == "+1-555-1234"
    assert vendor.website == "https://acme.com"
    assert vendor.address_line1 == "123 Main St"
    assert vendor.address_line2 == "Suite 100"
    assert vendor.city == "New York"
    assert vendor.state == "NY"
    assert vendor.postal_code == "10001"
    assert vendor.country == "USA"
    assert vendor.tax_id == "12-3456789"
    assert vendor.payment_terms == "Net 30"


def test_vendor_service_create_validates_email(dbsession):
    """Test email validation in vendor service"""
    with pytest.raises(ValidationError, match="Invalid email"):
        VendorService.create(
            dbsession,
            "Bad Email Vendor",
            currency="USD",
            email="not-an-email",
        )


# QuoteService Tests
def test_quote_service_create(dbsession):
    """Test creating a quote"""
    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")

    quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.99)

    assert quote.id is not None
    assert quote.vendor.name == "Amazon"
    assert quote.product.name == "iPhone 15"
    assert quote.value == 999.99


def test_quote_service_create_vendor_not_found(dbsession):
    """Test creating quote with non-existent vendor raises error"""
    with pytest.raises(NotFoundError, match="not found"):
        QuoteService.create(dbsession, "NonExistent", "iPhone 15", 999.99)


def test_quote_service_create_with_new_product(dbsession):
    """Test creating quote can create new product"""
    VendorService.create(dbsession, "Amazon", currency="USD")

    quote = QuoteService.create(
        dbsession, "Amazon", "iPhone 15", 999.99, brand_name="Apple"
    )

    assert quote.product.name == "iPhone 15"
    assert quote.product.brand.name == "Apple"


def test_quote_service_create_negative_price(dbsession):
    """Test creating quote with negative price raises error"""
    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")

    with pytest.raises(ValidationError, match="cannot be negative"):
        QuoteService.create(dbsession, "Amazon", "iPhone 15", -100.0)


def test_quote_service_create_with_currency_conversion(dbsession):
    """Test quote with non-USD vendor converts currency"""
    ForexService.create(dbsession, "GBP", 1.25)
    VendorService.create(dbsession, "UK Shop", currency="GBP")
    ProductService.create(dbsession, "iPhone 15", "Apple")

    quote = QuoteService.create(dbsession, "UK Shop", "iPhone 15", 800.0)

    assert quote.original_value == 800.0
    assert quote.original_currency == "GBP"
    assert quote.value == 800.0 * 1.25  # Converted to USD
    assert quote.currency == "USD"


def test_quote_service_get_all_eager_loads(dbsession):
    """Test get_all eagerly loads vendor and product (no N+1)"""
    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")
    QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.99)

    quotes = QuoteService.get_all(dbsession)

    # Access related objects should not trigger additional queries
    for q in quotes:
        assert q.vendor.name
        assert q.product.name
        assert q.product.brand.name  # Should not cause N+1


# ForexService Tests
def test_forex_service_create(dbsession):
    """Test creating a forex rate"""
    fx = ForexService.create(dbsession, "EUR", 1.085)

    assert fx.id is not None
    assert fx.code == "EUR"
    assert fx.usd_per_unit == 1.085
    assert fx.date == datetime.date.today()


def test_forex_service_create_with_date(dbsession):
    """Test creating forex rate with specific date"""
    date = datetime.date(2025, 10, 15)
    fx = ForexService.create(dbsession, "EUR", 1.085, date=date)

    assert fx.date == date


def test_forex_service_create_normalizes_code(dbsession):
    """Test currency code is normalized to uppercase"""
    fx = ForexService.create(dbsession, "eur", 1.085)
    assert fx.code == "EUR"


def test_forex_service_create_validates_code_length(dbsession):
    """Test currency code must be 3 characters"""
    with pytest.raises(ValidationError, match="3 characters"):
        ForexService.create(dbsession, "EU", 1.085)


def test_forex_service_create_validates_positive_rate(dbsession):
    """Test exchange rate must be positive"""
    with pytest.raises(ValidationError, match="positive"):
        ForexService.create(dbsession, "EUR", 0.0)


def test_forex_service_create_duplicate(dbsession):
    """Test creating duplicate rate for same code and date raises error"""
    ForexService.create(dbsession, "EUR", 1.085)
    with pytest.raises(DuplicateError, match="already exists"):
        ForexService.create(dbsession, "EUR", 1.090)


def test_forex_service_create_same_code_different_date(dbsession):
    """Test can create multiple rates for same code on different dates"""
    ForexService.create(dbsession, "EUR", 1.085, date=datetime.date(2025, 10, 15))
    fx = ForexService.create(dbsession, "EUR", 1.090, date=datetime.date(2025, 10, 16))

    assert fx.usd_per_unit == 1.090


def test_forex_service_get_latest(dbsession):
    """Test getting latest rate for a currency"""
    ForexService.create(dbsession, "EUR", 1.080, date=datetime.date(2025, 10, 15))
    ForexService.create(dbsession, "EUR", 1.085, date=datetime.date(2025, 10, 16))
    ForexService.create(dbsession, "EUR", 1.090, date=datetime.date(2025, 10, 17))

    latest = ForexService.get_latest(dbsession, "EUR")

    assert latest.usd_per_unit == 1.090
    assert latest.date == datetime.date(2025, 10, 17)


# SpecificationService Tests
def test_specification_service_create(dbsession):
    """Test creating a specification"""
    from buylog.services import SpecificationService

    spec = SpecificationService.create(
        dbsession, "Camera Specs", description="Camera specifications"
    )

    assert spec.id is not None
    assert spec.name == "Camera Specs"
    assert spec.description == "Camera specifications"


def test_specification_service_create_duplicate(dbsession):
    """Test creating duplicate specification raises error"""
    from buylog.services import SpecificationService

    SpecificationService.create(dbsession, "Camera Specs")
    with pytest.raises(DuplicateError, match="already exists"):
        SpecificationService.create(dbsession, "Camera Specs")


def test_specification_service_create_empty_name(dbsession):
    """Test creating specification with empty name raises error"""
    from buylog.services import SpecificationService

    with pytest.raises(ValidationError, match="cannot be empty"):
        SpecificationService.create(dbsession, "")


def test_specification_service_add_feature(dbsession):
    """Test adding feature to specification"""
    from buylog.services import SpecificationService

    SpecificationService.create(dbsession, "Camera Specs")
    feature = SpecificationService.add_feature(
        dbsession,
        "Camera Specs",
        "Resolution",
        data_type="number",
        unit="MP",
        is_required=True,
        min_value=1.0,
        max_value=200.0,
    )

    assert feature.id is not None
    assert feature.name == "Resolution"
    assert feature.data_type == "number"
    assert feature.unit == "MP"
    assert feature.is_required == 1
    assert feature.min_value == 1.0
    assert feature.max_value == 200.0


def test_specification_service_add_feature_invalid_data_type(dbsession):
    """Test adding feature with invalid data type raises error"""
    from buylog.services import SpecificationService

    SpecificationService.create(dbsession, "Camera Specs")
    with pytest.raises(ValidationError, match="Invalid data type"):
        SpecificationService.add_feature(
            dbsession, "Camera Specs", "Resolution", data_type="invalid"
        )


def test_specification_service_add_feature_invalid_min_max(dbsession):
    """Test adding feature with min > max raises error"""
    from buylog.services import SpecificationService

    SpecificationService.create(dbsession, "Camera Specs")
    with pytest.raises(ValidationError, match="min_value cannot be greater"):
        SpecificationService.add_feature(
            dbsession,
            "Camera Specs",
            "Resolution",
            data_type="number",
            min_value=100.0,
            max_value=10.0,
        )


def test_specification_service_add_feature_not_found(dbsession):
    """Test adding feature to non-existent specification raises error"""
    from buylog.services import SpecificationService

    with pytest.raises(NotFoundError, match="not found"):
        SpecificationService.add_feature(dbsession, "NonExistent", "Feature")


def test_specification_service_get_all(dbsession):
    """Test getting all specifications"""
    from buylog.services import SpecificationService

    SpecificationService.create(dbsession, "Camera Specs")
    SpecificationService.create(dbsession, "Phone Specs")

    specs = SpecificationService.get_all(dbsession)
    assert len(specs) == 2


def test_specification_service_delete(dbsession):
    """Test deleting a specification"""
    from buylog.services import SpecificationService

    SpecificationService.create(dbsession, "Camera Specs")
    SpecificationService.delete(dbsession, "Camera Specs")

    spec = SpecificationService.get_by_name(dbsession, "Camera Specs")
    assert spec is None


# SpecificationFeatureService Tests
def test_specification_feature_service_update(dbsession):
    """Test updating a specification feature"""
    from buylog.services import SpecificationService, SpecificationFeatureService

    SpecificationService.create(dbsession, "Camera Specs")
    feature = SpecificationService.add_feature(
        dbsession, "Camera Specs", "Resolution", data_type="number"
    )

    updated = SpecificationFeatureService.update(
        dbsession, feature.id, name="Max Resolution", unit="MP"
    )

    assert updated.name == "Max Resolution"
    assert updated.unit == "MP"


def test_specification_feature_service_update_not_found(dbsession):
    """Test updating non-existent feature raises error"""
    from buylog.services import SpecificationFeatureService

    with pytest.raises(NotFoundError, match="not found"):
        SpecificationFeatureService.update(dbsession, 9999, name="New Name")


def test_specification_feature_service_delete(dbsession):
    """Test deleting a specification feature"""
    from buylog.services import SpecificationService, SpecificationFeatureService

    SpecificationService.create(dbsession, "Camera Specs")
    feature = SpecificationService.add_feature(
        dbsession, "Camera Specs", "Resolution", data_type="number"
    )

    SpecificationFeatureService.delete(dbsession, feature.id)

    spec = SpecificationService.get_by_name(dbsession, "Camera Specs")
    assert len(spec.features) == 0


# ProductFeatureService Tests
def test_product_feature_service_set_value_text(dbsession):
    """Test setting a text feature value"""
    from buylog.services import SpecificationService, ProductFeatureService

    SpecificationService.create(dbsession, "Phone Specs")
    feature = SpecificationService.add_feature(
        dbsession, "Phone Specs", "Color", data_type="text"
    )
    ProductService.create(dbsession, "iPhone 15", "Apple")

    pf = ProductFeatureService.set_value(
        dbsession, "iPhone 15", feature.id, "Space Black"
    )

    assert pf.value_text == "Space Black"
    assert pf.value == "Space Black"


def test_product_feature_service_set_value_number(dbsession):
    """Test setting a number feature value"""
    from buylog.services import SpecificationService, ProductFeatureService

    SpecificationService.create(dbsession, "Phone Specs")
    feature = SpecificationService.add_feature(
        dbsession,
        "Phone Specs",
        "Battery",
        data_type="number",
        unit="mAh",
        min_value=1000.0,
        max_value=10000.0,
    )
    ProductService.create(dbsession, "iPhone 15", "Apple")

    pf = ProductFeatureService.set_value(dbsession, "iPhone 15", feature.id, 4000)

    assert pf.value_number == 4000.0
    assert pf.value == 4000.0


def test_product_feature_service_set_value_number_validation(dbsession):
    """Test number feature validation with min/max"""
    from buylog.services import SpecificationService, ProductFeatureService

    SpecificationService.create(dbsession, "Phone Specs")
    feature = SpecificationService.add_feature(
        dbsession,
        "Phone Specs",
        "Battery",
        data_type="number",
        min_value=1000.0,
        max_value=10000.0,
    )
    ProductService.create(dbsession, "iPhone 15", "Apple")

    with pytest.raises(ValidationError, match="below minimum"):
        ProductFeatureService.set_value(dbsession, "iPhone 15", feature.id, 500)

    with pytest.raises(ValidationError, match="exceeds maximum"):
        ProductFeatureService.set_value(dbsession, "iPhone 15", feature.id, 20000)


def test_product_feature_service_set_value_boolean(dbsession):
    """Test setting a boolean feature value"""
    from buylog.services import SpecificationService, ProductFeatureService

    SpecificationService.create(dbsession, "Phone Specs")
    feature = SpecificationService.add_feature(
        dbsession, "Phone Specs", "Wireless Charging", data_type="boolean"
    )
    ProductService.create(dbsession, "iPhone 15", "Apple")

    pf = ProductFeatureService.set_value(dbsession, "iPhone 15", feature.id, True)

    assert pf.value_boolean == 1
    assert pf.value is True


def test_product_feature_service_get_features(dbsession):
    """Test getting all features for a product"""
    from buylog.services import SpecificationService, ProductFeatureService

    SpecificationService.create(dbsession, "Phone Specs")
    feature1 = SpecificationService.add_feature(
        dbsession, "Phone Specs", "Color", data_type="text"
    )
    feature2 = SpecificationService.add_feature(
        dbsession, "Phone Specs", "Battery", data_type="number"
    )
    ProductService.create(dbsession, "iPhone 15", "Apple")

    ProductFeatureService.set_value(dbsession, "iPhone 15", feature1.id, "Black")
    ProductFeatureService.set_value(dbsession, "iPhone 15", feature2.id, 4000)

    features = ProductFeatureService.get_features(dbsession, "iPhone 15")
    assert len(features) == 2


def test_product_feature_service_validate_required(dbsession):
    """Test validation of required features"""
    from buylog.services import SpecificationService, ProductFeatureService

    SpecificationService.create(dbsession, "Phone Specs")
    SpecificationService.add_feature(
        dbsession, "Phone Specs", "Color", data_type="text", is_required=True
    )
    SpecificationService.add_feature(
        dbsession, "Phone Specs", "Battery", data_type="number", is_required=True
    )
    SpecificationService.add_feature(
        dbsession, "Phone Specs", "Optional", data_type="text", is_required=False
    )

    # Create product with specification
    ProductService.create(dbsession, "iPhone 15", "Apple")
    spec = Specification.by_name(dbsession, "Phone Specs")
    product = Product.by_name(dbsession, "iPhone 15")
    product.specification = spec
    dbsession.commit()

    # Initially, both required features are missing
    missing = ProductFeatureService.validate_required(dbsession, "iPhone 15")
    assert len(missing) == 2
    assert "Color" in missing
    assert "Battery" in missing


# PurchaseOrderService Tests
def test_purchase_order_service_create(dbsession):
    """Test creating a purchase order"""
    from buylog.services import PurchaseOrderService

    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")

    po = PurchaseOrderService.create(
        dbsession,
        po_number="PO-00001",
        vendor_name="Amazon",
        product_name="iPhone 15",
        unit_price=999.99,
        quantity=2,
        shipping_cost=25.00,
        tax=100.00,
    )

    assert po.id is not None
    assert po.po_number == "PO-00001"
    assert po.unit_price == 999.99
    assert po.quantity == 2
    assert po.total_amount == 999.99 * 2
    assert po.grand_total == 999.99 * 2 + 25.00 + 100.00
    assert po.status == "pending"


def test_purchase_order_service_create_duplicate_po_number(dbsession):
    """Test creating duplicate PO number raises error"""
    from buylog.services import PurchaseOrderService

    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")

    PurchaseOrderService.create(
        dbsession,
        po_number="PO-DUPE",
        vendor_name="Amazon",
        product_name="iPhone 15",
        unit_price=999.99,
    )

    with pytest.raises(DuplicateError, match="already exists"):
        PurchaseOrderService.create(
            dbsession,
            po_number="PO-DUPE",
            vendor_name="Amazon",
            product_name="iPhone 15",
            unit_price=888.88,
        )


def test_purchase_order_service_create_invalid_status(dbsession):
    """Test creating PO with invalid status raises error"""
    from buylog.services import PurchaseOrderService

    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")

    with pytest.raises(ValidationError, match="Invalid status"):
        PurchaseOrderService.create(
            dbsession,
            po_number="PO-00001",
            vendor_name="Amazon",
            product_name="iPhone 15",
            unit_price=999.99,
            status="invalid_status",
        )


def test_purchase_order_service_create_from_quote(dbsession):
    """Test creating PO from existing quote"""
    from buylog.services import PurchaseOrderService

    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")
    quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.99)

    po = PurchaseOrderService.create_from_quote(
        dbsession,
        po_number="PO-FROM-QUOTE",
        quote_id=quote.id,
        quantity=3,
    )

    assert po.quote_id == quote.id
    assert po.unit_price == quote.value
    assert po.quantity == 3
    assert po.vendor.name == "Amazon"
    assert po.product.name == "iPhone 15"


def test_purchase_order_service_create_from_quote_not_found(dbsession):
    """Test creating PO from non-existent quote raises error"""
    from buylog.services import PurchaseOrderService

    with pytest.raises(NotFoundError, match="Quote with ID"):
        PurchaseOrderService.create_from_quote(
            dbsession,
            po_number="PO-00001",
            quote_id=9999,
        )


def test_purchase_order_service_update_status(dbsession):
    """Test updating PO status"""
    from buylog.services import PurchaseOrderService

    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")

    PurchaseOrderService.create(
        dbsession,
        po_number="PO-STATUS",
        vendor_name="Amazon",
        product_name="iPhone 15",
        unit_price=999.99,
    )

    po = PurchaseOrderService.update_status(dbsession, "PO-STATUS", "approved")
    assert po.status == "approved"

    po = PurchaseOrderService.update_status(dbsession, "PO-STATUS", "ordered")
    assert po.status == "ordered"


def test_purchase_order_service_update_status_received_auto_delivery(dbsession):
    """Test updating status to received auto-sets actual_delivery"""
    import datetime
    from buylog.services import PurchaseOrderService

    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")

    PurchaseOrderService.create(
        dbsession,
        po_number="PO-RECEIVED",
        vendor_name="Amazon",
        product_name="iPhone 15",
        unit_price=999.99,
    )

    po = PurchaseOrderService.update_status(dbsession, "PO-RECEIVED", "received")
    assert po.status == "received"
    assert po.actual_delivery == datetime.date.today()


def test_purchase_order_service_update_status_invalid(dbsession):
    """Test updating PO with invalid status raises error"""
    from buylog.services import PurchaseOrderService

    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")

    PurchaseOrderService.create(
        dbsession,
        po_number="PO-INVALID",
        vendor_name="Amazon",
        product_name="iPhone 15",
        unit_price=999.99,
    )

    with pytest.raises(ValidationError, match="Invalid status"):
        PurchaseOrderService.update_status(dbsession, "PO-INVALID", "bad_status")


def test_purchase_order_service_get_by_po_number(dbsession):
    """Test getting PO by PO number"""
    from buylog.services import PurchaseOrderService

    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")

    PurchaseOrderService.create(
        dbsession,
        po_number="PO-GET",
        vendor_name="Amazon",
        product_name="iPhone 15",
        unit_price=999.99,
    )

    po = PurchaseOrderService.get_by_po_number(dbsession, "PO-GET")
    assert po is not None
    assert po.po_number == "PO-GET"

    po = PurchaseOrderService.get_by_po_number(dbsession, "PO-NONEXISTENT")
    assert po is None


def test_purchase_order_service_get_all(dbsession):
    """Test getting all POs"""
    from buylog.services import PurchaseOrderService

    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")

    PurchaseOrderService.create(
        dbsession,
        po_number="PO-LIST-1",
        vendor_name="Amazon",
        product_name="iPhone 15",
        unit_price=999.99,
    )
    PurchaseOrderService.create(
        dbsession,
        po_number="PO-LIST-2",
        vendor_name="Amazon",
        product_name="iPhone 15",
        unit_price=888.88,
    )

    pos = PurchaseOrderService.get_all(dbsession)
    assert len(pos) == 2


def test_purchase_order_service_get_all_by_status(dbsession):
    """Test getting POs by status"""
    from buylog.services import PurchaseOrderService

    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")

    PurchaseOrderService.create(
        dbsession,
        po_number="PO-PENDING",
        vendor_name="Amazon",
        product_name="iPhone 15",
        unit_price=999.99,
        status="pending",
    )
    PurchaseOrderService.create(
        dbsession,
        po_number="PO-APPROVED",
        vendor_name="Amazon",
        product_name="iPhone 15",
        unit_price=888.88,
        status="approved",
    )

    pending = PurchaseOrderService.get_all(dbsession, status="pending")
    assert len(pending) == 1
    assert pending[0].po_number == "PO-PENDING"

    approved = PurchaseOrderService.get_all(dbsession, status="approved")
    assert len(approved) == 1
    assert approved[0].po_number == "PO-APPROVED"


# Forex normalization (multi-rate) tests
def test_forex_get_latest_with_multiple_rates(dbsession):
    """get_latest must pick the newest rate, not error on multiple rows."""
    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.1))
    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 2, 1), usd_per_unit=1.2))
    dbsession.commit()
    assert ForexService.get_latest(dbsession, "EUR").usd_per_unit == 1.2


def test_forex_to_usd_passes_through_usd(dbsession):
    assert ForexService.to_usd(dbsession, 100.0, "USD") == (100.0, None, None)


def test_forex_to_usd_uses_latest_rate(dbsession):
    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.1))
    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 2, 1), usd_per_unit=1.2))
    dbsession.commit()
    value, original_value, original_currency = ForexService.to_usd(
        dbsession, 100.0, "EUR"
    )
    assert value == pytest.approx(120.0)
    assert original_value == 100.0
    assert original_currency == "EUR"


def test_forex_to_usd_raises_without_rate(dbsession):
    with pytest.raises(ValidationError, match="Forex rate for 'JPY' not found"):
        ForexService.to_usd(dbsession, 100.0, "JPY")


def test_quote_create_with_multiple_forex_rates(dbsession):
    """Regression: a second rate for a currency broke foreign-currency quotes."""
    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.1))
    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 2, 1), usd_per_unit=1.2))
    dbsession.commit()
    VendorService.create(dbsession, "EuroShop", currency="EUR")
    quote = QuoteService.create(
        dbsession,
        product_name="TV",
        vendor_name="EuroShop",
        price=100.0,
        brand_name="Sony",
    )
    assert quote.value == pytest.approx(120.0)
    assert quote.original_value == 100.0
    assert quote.original_currency == "EUR"


def test_forex_duplicate_code_and_date_rejected_by_database(dbsession):
    """The (code, date) unique constraint is enforced at the database level."""
    from sqlalchemy.exc import IntegrityError

    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.1))
    dbsession.commit()
    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.2))
    with pytest.raises(IntegrityError):
        dbsession.commit()
    dbsession.rollback()


# Cascade / orphan tests
def test_delete_quote_removes_its_history(dbsession):
    """Regression: deleting a quote left orphaned quote_history rows."""
    from buylog.models import QuoteHistory
    from sqlalchemy import select

    VendorService.create(dbsession, "Amazon")
    quote = QuoteService.create(
        dbsession, "Amazon", "iPhone 15", 999.0, brand_name="Apple"
    )
    quote_id = quote.id
    assert (
        dbsession.execute(select(QuoteHistory).where(QuoteHistory.quote_id == quote_id))
        .scalars()
        .all()
    )

    QuoteService.delete(dbsession, quote_id)

    orphans = (
        dbsession.execute(select(QuoteHistory).where(QuoteHistory.quote_id == quote_id))
        .scalars()
        .all()
    )
    assert orphans == []


def test_delete_product_removes_alerts_and_watchlist(dbsession):
    from buylog.models import PriceAlert, Watchlist
    from buylog.services import PriceAlertService, WatchlistService
    from sqlalchemy import select

    ProductService.create(dbsession, "iPhone 15", "Apple")
    PriceAlertService.create(dbsession, "iPhone 15", 900.0)
    WatchlistService.create(dbsession, "iPhone 15", 800.0)

    ProductService.delete(dbsession, "iPhone 15")

    assert dbsession.execute(select(PriceAlert)).scalars().all() == []
    assert dbsession.execute(select(Watchlist)).scalars().all() == []


def test_delete_vendor_removes_its_quotes(dbsession):
    from sqlalchemy import select
    from buylog.models import Quote as QuoteModel

    VendorService.create(dbsession, "Amazon")
    QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.0, brand_name="Apple")

    VendorService.delete(dbsession, "Amazon")

    assert dbsession.execute(select(QuoteModel)).scalars().all() == []


# Service update/delete coverage
def test_product_service_update_renames(dbsession):
    ProductService.create(dbsession, "iPhone 15", "Apple")
    updated = ProductService.update(dbsession, "iPhone 15", new_name="iPhone 15 Pro")
    assert updated.name == "iPhone 15 Pro"
    assert ProductService.get_by_name(dbsession, "iPhone 15") is None


def test_product_service_update_rejects_duplicate_name(dbsession):
    ProductService.create(dbsession, "iPhone 15", "Apple")
    ProductService.create(dbsession, "iPhone 14", "Apple")
    with pytest.raises(DuplicateError):
        ProductService.update(dbsession, "iPhone 14", new_name="iPhone 15")


def test_product_service_update_missing_raises(dbsession):
    with pytest.raises(NotFoundError):
        ProductService.update(dbsession, "Nope", new_name="X")


def test_vendor_service_update_changes_currency(dbsession):
    VendorService.create(dbsession, "EuroShop")
    updated = VendorService.update(dbsession, "EuroShop", currency="eur")
    assert updated.currency == "EUR"


def test_vendor_service_update_rejects_bad_currency(dbsession):
    VendorService.create(dbsession, "Amazon")
    with pytest.raises(ValidationError, match="3 characters"):
        VendorService.update(dbsession, "Amazon", currency="EURO")


def test_vendor_service_delete_missing_raises(dbsession):
    with pytest.raises(NotFoundError):
        VendorService.delete(dbsession, "Nope")


def test_quote_service_update_converts_price(dbsession):
    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.2))
    dbsession.commit()
    VendorService.create(dbsession, "EuroShop", currency="EUR")
    quote = QuoteService.create(dbsession, "EuroShop", "TV", 100.0, brand_name="Sony")

    updated = QuoteService.update(dbsession, quote.id, price=200.0)

    assert updated.value == pytest.approx(240.0)
    assert updated.original_value == 200.0
    assert updated.original_currency == "EUR"


def test_quote_service_update_rejects_bad_status(dbsession):
    VendorService.create(dbsession, "Amazon")
    quote = QuoteService.create(
        dbsession, "Amazon", "iPhone 15", 999.0, brand_name="Apple"
    )
    with pytest.raises(ValidationError, match="Invalid status"):
        QuoteService.update(dbsession, quote.id, status="nonsense")


def test_quote_service_delete_missing_raises(dbsession):
    with pytest.raises(NotFoundError):
        QuoteService.delete(dbsession, 99999)


def test_quote_create_records_shipping_and_tax(dbsession):
    VendorService.create(dbsession, "Amazon")
    quote = QuoteService.create(
        dbsession,
        "Amazon",
        "iPhone 15",
        1000.0,
        brand_name="Apple",
        shipping_cost=10.0,
        tax_rate=5.0,
    )
    assert quote.shipping_cost == 10.0
    assert quote.tax_rate == 5.0
    assert quote.total_cost == pytest.approx(1060.5)


# Currency correctness in import, scraping, alerts and purchase orders
def test_import_row_without_forex_rate_is_rejected(dbsession, tmp_path):
    """A foreign price with no rate used to be stored labelled USD."""
    from buylog.services import ImportService

    VendorService.create(dbsession, "EuroShop", currency="EUR")
    ProductService.create(dbsession, "TV", "Sony")

    csv_file = tmp_path / "quotes.csv"
    csv_file.write_text("vendor,product,brand,price\nEuroShop,TV,Sony,100\n")

    stats = ImportService.import_quotes_csv(dbsession, str(csv_file))

    assert stats["imported"] == 0
    assert stats["skipped"] == 1
    assert any("EUR" in e for e in stats["errors"])


def test_import_row_converts_with_forex_rate(dbsession, tmp_path):
    from buylog.services import ImportService

    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.2))
    dbsession.commit()
    VendorService.create(dbsession, "EuroShop", currency="EUR")
    ProductService.create(dbsession, "TV", "Sony")

    csv_file = tmp_path / "quotes.csv"
    csv_file.write_text("vendor,product,brand,price\nEuroShop,TV,Sony,100\n")

    stats = ImportService.import_quotes_csv(dbsession, str(csv_file))

    assert stats["imported"] == 1
    quote = QuoteService.get_all(dbsession)[0]
    assert quote.value == pytest.approx(120.0)
    assert quote.original_currency == "EUR"


def test_failed_import_row_leaves_no_orphans(dbsession, tmp_path):
    """A row that fails after creating a vendor must not commit that vendor."""
    from buylog.services import ImportService

    csv_file = tmp_path / "quotes.csv"
    # Second row has no brand for a new product, so it fails after the vendor
    # has been flushed.
    csv_file.write_text(
        "vendor,product,brand,price\n"
        "GoodShop,TV,Sony,100\n"
        "OrphanShop,Unknown Thing,,200\n"
    )

    stats = ImportService.import_quotes_csv(dbsession, str(csv_file))

    assert stats["imported"] == 1
    assert stats["skipped"] == 1
    assert VendorService.get_all(dbsession, filter_by="OrphanShop") == []


def test_alert_threshold_in_foreign_currency_is_converted(dbsession):
    """A non-USD threshold used to be compared as if it were USD."""
    from buylog.models import PriceAlert
    from buylog.services import PriceAlertService

    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.2))
    dbsession.commit()
    product = ProductService.create(dbsession, "TV", "Sony")

    # 100 EUR == 120 USD
    alert = PriceAlert(
        product_id=product.id, threshold_value=100.0, threshold_currency="EUR"
    )
    dbsession.add(alert)
    dbsession.commit()

    # 110 USD is below the 120 USD threshold, but above the raw value of 100
    triggered = PriceAlertService.check_alerts(dbsession, product, 110.0)
    assert triggered == [alert]


def test_alert_without_forex_rate_is_skipped_not_miscompared(dbsession):
    from buylog.models import PriceAlert
    from buylog.services import PriceAlertService

    product = ProductService.create(dbsession, "TV", "Sony")
    alert = PriceAlert(
        product_id=product.id, threshold_value=100.0, threshold_currency="JPY"
    )
    dbsession.add(alert)
    dbsession.commit()

    assert PriceAlertService.check_alerts(dbsession, product, 50.0) == []


def test_purchase_order_defaults_to_vendor_currency(dbsession):
    from buylog.services import PurchaseOrderService

    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.2))
    dbsession.commit()
    VendorService.create(dbsession, "EuroShop", currency="EUR")
    ProductService.create(dbsession, "TV", "Sony")

    po = PurchaseOrderService.create(
        dbsession,
        po_number="PO-EUR-1",
        vendor_name="EuroShop",
        product_name="TV",
        unit_price=100.0,
        quantity=2,
    )

    assert po.currency == "EUR"
    assert po.grand_total == pytest.approx(200.0)
    assert po.grand_total_usd == pytest.approx(240.0)


# Deletes must not destroy or strand purchase orders
def test_deleting_a_vendor_with_purchase_orders_is_refused(dbsession):
    from buylog.services import PurchaseOrderService

    VendorService.create(dbsession, "Amazon")
    ProductService.create(dbsession, "iPhone 15", "Apple")
    PurchaseOrderService.create(dbsession, "PO-1", "Amazon", "iPhone 15", 999.0)

    with pytest.raises(ValidationError, match="PO-1"):
        VendorService.delete(dbsession, "Amazon")

    assert VendorService.get_all(dbsession, filter_by="Amazon")


def test_deleting_a_product_with_purchase_orders_is_refused(dbsession):
    from buylog.services import PurchaseOrderService

    VendorService.create(dbsession, "Amazon")
    ProductService.create(dbsession, "iPhone 15", "Apple")
    PurchaseOrderService.create(dbsession, "PO-1", "Amazon", "iPhone 15", 999.0)

    with pytest.raises(ValidationError, match="PO-1"):
        ProductService.delete(dbsession, "iPhone 15")


def test_deleting_a_quote_detaches_it_from_its_purchase_order(dbsession):
    """A PO's link back to its quote is optional, so the PO survives."""
    from buylog.services import PurchaseOrderService

    VendorService.create(dbsession, "Amazon")
    quote = QuoteService.create(
        dbsession, "Amazon", "iPhone 15", 999.0, brand_name="Apple"
    )
    PurchaseOrderService.create_from_quote(dbsession, "PO-1", quote.id)

    QuoteService.delete(dbsession, quote.id)

    po = PurchaseOrderService.get_by_po_number(dbsession, "PO-1")
    assert po is not None
    assert po.quote_id is None
    assert po.grand_total == pytest.approx(999.0)


def test_commit_or_rollback_discards_work_when_the_body_fails(dbsession):
    """A non-database error inside the block must not leave writes pending."""
    from buylog.services import commit_or_rollback

    with pytest.raises(RuntimeError):
        with commit_or_rollback(dbsession, "create brand 'Ghost'"):
            dbsession.add(Brand(name="Ghost"))
            raise RuntimeError("boom")

    assert BrandService.get_by_name(dbsession, "Ghost") is None
    # The session is still usable.
    assert BrandService.create(dbsession, "Apple").id is not None


def test_shipping_cost_is_normalized_to_usd(dbsession):
    """Regression: EUR shipping was added onto a USD base in total_cost."""
    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.1))
    dbsession.commit()
    VendorService.create(dbsession, "EuroShop", currency="EUR")

    quote = QuoteService.create(
        dbsession, "EuroShop", "TV", 100.0, brand_name="Sony", shipping_cost=20.0
    )

    assert quote.value == pytest.approx(110.0)
    assert quote.shipping_cost == pytest.approx(22.0)
    assert quote.total_cost == pytest.approx(132.0)


def test_quote_update_normalizes_shipping_cost(dbsession):
    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.1))
    dbsession.commit()
    VendorService.create(dbsession, "EuroShop", currency="EUR")
    quote = QuoteService.create(dbsession, "EuroShop", "TV", 100.0, brand_name="Sony")

    QuoteService.update(dbsession, quote.id, shipping_cost=20.0)

    assert quote.shipping_cost == pytest.approx(22.0)


def test_import_normalizes_shipping_cost(dbsession, tmp_path):
    from buylog.services import ImportService

    dbsession.add(Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.1))
    dbsession.commit()
    VendorService.create(dbsession, "EuroShop", currency="EUR")
    ProductService.create(dbsession, "TV", "Sony")

    csv_file = tmp_path / "quotes.csv"
    csv_file.write_text(
        "vendor,product,brand,price,shipping\nEuroShop,TV,Sony,100,20\n"
    )
    ImportService.import_quotes_csv(dbsession, str(csv_file))

    quote = QuoteService.get_all(dbsession)[0]
    assert quote.shipping_cost == pytest.approx(22.0)
