import pytest
from sqlalchemy.exc import IntegrityError

from buylog.models import Brand, Product, Vendor, Quote, Forex


# Brand Tests
def test_brand_creation(dbsession):
    """Test brand can be created with valid name"""
    brand = Brand(name="Apple")
    dbsession.add(brand)
    dbsession.flush()

    assert brand.id is not None
    assert brand.name == "Apple"
    assert repr(brand) == "<Brand(name='Apple')>"


def test_brand_by_name_exists(dbsession):
    """Test Brand.by_name() returns existing brand"""
    brand = Brand(name="Apple")
    dbsession.add(brand)
    dbsession.commit()

    result = Brand.by_name(dbsession, "Apple")
    assert result is not None
    assert result.name == "Apple"
    assert result.id == brand.id


def test_brand_by_name_not_exists(dbsession):
    """Test Brand.by_name() returns None for non-existent brand"""
    result = Brand.by_name(dbsession, "NonExistent")
    assert result is None


def test_brand_unique_constraint(dbsession):
    """Test duplicate brand names are rejected"""
    brand1 = Brand(name="Apple")
    brand2 = Brand(name="Apple")
    dbsession.add(brand1)
    dbsession.add(brand2)

    with pytest.raises(IntegrityError):
        dbsession.commit()


def test_brand_products_relationship(dbsession):
    """Test brand can have multiple products"""
    brand = Brand(name="Apple")
    product1 = Product(name="iPhone 15", brand=brand)
    product2 = Product(name="iPhone 14", brand=brand)
    dbsession.add_all([brand, product1, product2])
    dbsession.commit()

    assert len(brand.products) == 2
    assert product1 in brand.products
    assert product2 in brand.products


# Product Tests
def test_product_creation(dbsession):
    """Test product can be created with brand"""
    brand = Brand(name="Apple")
    product = Product(name="iPhone 15", brand=brand)
    dbsession.add_all([brand, product])
    dbsession.flush()

    assert product.id is not None
    assert product.name == "iPhone 15"
    assert product.brand == brand
    assert product.brand_id == brand.id


def test_product_by_name_exists(dbsession):
    """Test Product.by_name() returns existing product"""
    brand = Brand(name="Apple")
    product = Product(name="iPhone 15", brand=brand)
    dbsession.add_all([brand, product])
    dbsession.commit()

    result = Product.by_name(dbsession, "iPhone 15")
    assert result is not None
    assert result.name == "iPhone 15"
    assert result.brand.name == "Apple"


def test_product_unique_constraint(dbsession):
    """Test duplicate product names are rejected"""
    brand = Brand(name="Apple")
    product1 = Product(name="iPhone 15", brand=brand)
    product2 = Product(name="iPhone 15", brand=brand)
    dbsession.add_all([brand, product1, product2])

    with pytest.raises(IntegrityError):
        dbsession.commit()


# Vendor Tests
def test_vendor_creation(dbsession):
    """Test vendor can be created with currency"""
    vendor = Vendor(
        name="Amazon", currency="USD", discount_code="SAVE10", discount=10.0
    )
    dbsession.add(vendor)
    dbsession.flush()

    assert vendor.id is not None
    assert vendor.name == "Amazon"
    assert vendor.currency == "USD"
    assert vendor.discount_code == "SAVE10"
    assert vendor.discount == 10.0


def test_vendor_by_name_exists(dbsession):
    """Test Vendor.by_name() returns existing vendor"""
    vendor = Vendor(name="Amazon", currency="USD")
    dbsession.add(vendor)
    dbsession.commit()

    result = Vendor.by_name(dbsession, "Amazon")
    assert result is not None
    assert result.name == "Amazon"


def test_vendor_unique_constraint(dbsession):
    """Test duplicate vendor names are rejected"""
    vendor1 = Vendor(name="Amazon", currency="USD")
    vendor2 = Vendor(name="Amazon", currency="GBP")
    dbsession.add_all([vendor1, vendor2])

    with pytest.raises(IntegrityError):
        dbsession.commit()


def test_vendor_brand_relationship(dbsession):
    """Test many-to-many relationship between vendors and brands"""
    vendor = Vendor(name="Amazon", currency="USD")
    brand1 = Brand(name="Apple")
    brand2 = Brand(name="Samsung")
    vendor.brands = [brand1, brand2]
    dbsession.add_all([vendor, brand1, brand2])
    dbsession.commit()

    assert len(vendor.brands) == 2
    assert brand1 in vendor.brands
    assert brand2 in vendor.brands
    assert vendor in brand1.vendors
    assert vendor in brand2.vendors


def test_vendor_add_product_creates_new_brand(dbsession):
    """Test Vendor.add_product() creates brand if not exists"""
    vendor = Vendor(name="Amazon", currency="USD")
    dbsession.add(vendor)
    dbsession.flush()

    vendor.add_product(dbsession, "NewBrand", "NewProduct", 99.99)
    dbsession.commit()

    brand = Brand.by_name(dbsession, "NewBrand")
    assert brand is not None
    product = Product.by_name(dbsession, "NewProduct")
    assert product is not None
    assert product.brand == brand


def test_vendor_add_product_creates_quote(dbsession):
    """Test Vendor.add_product() creates a quote"""
    vendor = Vendor(name="Amazon", currency="USD")
    dbsession.add(vendor)
    dbsession.flush()

    vendor.add_product(dbsession, "Apple", "iPhone 15", 999.99, discount=5.0)
    dbsession.commit()

    product = Product.by_name(dbsession, "iPhone 15")
    assert len(product.quotes) == 1
    quote = product.quotes[0]
    assert quote.vendor == vendor
    assert quote.value == 999.99
    assert quote.discount == 5.0


def test_vendor_enhanced_fields(dbsession):
    """Test vendor with enhanced contact/address/payment fields"""
    vendor = Vendor(
        name="Acme Corp",
        currency="USD",
        contact_person="John Smith",
        email="john@acme.com",
        phone="+1-555-1234",
        url="https://acme.com",
        address_line1="123 Main St",
        address_line2="Suite 100",
        city="New York",
        state="NY",
        postal_code="10001",
        country="USA",
        tax_id="12-3456789",
        payment_terms="Net 30",
    )
    dbsession.add(vendor)
    dbsession.commit()

    result = Vendor.by_name(dbsession, "Acme Corp")
    assert result is not None
    assert result.contact_person == "John Smith"
    assert result.email == "john@acme.com"
    assert result.phone == "+1-555-1234"
    assert result.url == "https://acme.com"
    assert result.address_line1 == "123 Main St"
    assert result.address_line2 == "Suite 100"
    assert result.city == "New York"
    assert result.state == "NY"
    assert result.postal_code == "10001"
    assert result.country == "USA"
    assert result.tax_id == "12-3456789"
    assert result.payment_terms == "Net 30"


def test_vendor_enhanced_fields_nullable(dbsession):
    """Test all enhanced vendor fields can be null"""
    vendor = Vendor(name="Minimal Vendor", currency="USD")
    dbsession.add(vendor)
    dbsession.commit()

    result = Vendor.by_name(dbsession, "Minimal Vendor")
    assert result is not None
    assert result.contact_person is None
    assert result.email is None
    assert result.phone is None
    assert result.url is None
    assert result.address_line1 is None
    assert result.address_line2 is None
    assert result.city is None
    assert result.state is None
    assert result.postal_code is None
    assert result.country is None
    assert result.tax_id is None
    assert result.payment_terms is None


# Specification Tests
def test_specification_creation(dbsession):
    """Test specification can be created"""
    from buylog.models import Specification

    spec = Specification(
        name="Camera Specs", description="Camera product specifications"
    )
    dbsession.add(spec)
    dbsession.commit()

    result = Specification.by_name(dbsession, "Camera Specs")
    assert result is not None
    assert result.name == "Camera Specs"
    assert result.description == "Camera product specifications"


def test_specification_with_features(dbsession):
    """Test specification with features"""
    from buylog.models import Specification, SpecificationFeature

    spec = Specification(name="Laptop Specs")
    feature1 = SpecificationFeature(
        specification=spec,
        name="Screen Size",
        data_type="number",
        unit="inch",
        is_required=1,
        min_value=10.0,
        max_value=20.0,
    )
    feature2 = SpecificationFeature(
        specification=spec,
        name="Brand Name",
        data_type="text",
        is_required=0,
    )
    dbsession.add_all([spec, feature1, feature2])
    dbsession.commit()

    assert len(spec.features) == 2
    assert feature1.name == "Screen Size"
    assert feature1.data_type == "number"
    assert feature1.unit == "inch"
    assert feature1.is_required == 1
    assert feature1.min_value == 10.0
    assert feature1.max_value == 20.0
    assert feature2.name == "Brand Name"
    assert feature2.data_type == "text"


def test_product_with_specification(dbsession):
    """Test product can have a specification"""
    from buylog.models import Specification

    spec = Specification(name="Phone Specs")
    brand = Brand(name="Apple")
    product = Product(name="iPhone 15", brand=brand, specification=spec)
    dbsession.add_all([spec, brand, product])
    dbsession.commit()

    assert product.specification == spec
    assert product in spec.products


def test_product_feature_value(dbsession):
    """Test product feature values"""
    from buylog.models import Specification, SpecificationFeature, ProductFeature

    spec = Specification(name="Phone Specs")
    brand = Brand(name="Apple")
    product = Product(name="iPhone 15", brand=brand, specification=spec)
    feature = SpecificationFeature(
        specification=spec,
        name="Battery Capacity",
        data_type="number",
        unit="mAh",
    )
    dbsession.add_all([spec, brand, product, feature])
    dbsession.flush()

    pf = ProductFeature(
        product=product,
        specification_feature=feature,
        value_number=4000.0,
    )
    dbsession.add(pf)
    dbsession.commit()

    assert pf.value == 4000.0
    assert pf.specification_feature.name == "Battery Capacity"
    assert pf in product.features


def test_product_feature_value_property(dbsession):
    """Test ProductFeature.value property returns correct typed value"""
    from buylog.models import Specification, SpecificationFeature, ProductFeature

    spec = Specification(name="Test Specs")
    brand = Brand(name="Test Brand")
    product = Product(name="Test Product", brand=brand, specification=spec)

    text_feature = SpecificationFeature(
        specification=spec, name="Color", data_type="text"
    )
    number_feature = SpecificationFeature(
        specification=spec, name="Weight", data_type="number"
    )
    bool_feature = SpecificationFeature(
        specification=spec, name="Wireless", data_type="boolean"
    )

    dbsession.add_all(
        [spec, brand, product, text_feature, number_feature, bool_feature]
    )
    dbsession.flush()

    pf_text = ProductFeature(
        product=product, specification_feature=text_feature, value_text="Red"
    )
    pf_number = ProductFeature(
        product=product, specification_feature=number_feature, value_number=1.5
    )
    pf_bool = ProductFeature(
        product=product, specification_feature=bool_feature, value_boolean=1
    )

    dbsession.add_all([pf_text, pf_number, pf_bool])
    dbsession.commit()

    assert pf_text.value == "Red"
    assert pf_number.value == 1.5
    assert pf_bool.value is True


# PurchaseOrder Tests
def test_purchase_order_creation(dbsession):
    """Test purchase order can be created"""
    from buylog.models import PurchaseOrder, PO_STATUS_PENDING

    brand = Brand(name="Apple")
    product = Product(name="iPhone 15", brand=brand)
    vendor = Vendor(name="Amazon", currency="USD")
    dbsession.add_all([brand, product, vendor])
    dbsession.flush()

    po = PurchaseOrder(
        po_number="PO-00001",
        vendor=vendor,
        product=product,
        unit_price=999.99,
        quantity=2,
        currency="USD",
        total_amount=1999.98,
        shipping_cost=25.00,
        tax=100.00,
        grand_total=2124.98,
        status=PO_STATUS_PENDING,
    )
    dbsession.add(po)
    dbsession.commit()

    assert po.id is not None
    assert po.po_number == "PO-00001"
    assert po.vendor == vendor
    assert po.product == product
    assert po.unit_price == 999.99
    assert po.quantity == 2
    assert po.total_amount == 1999.98
    assert po.grand_total == 2124.98
    assert po.status == PO_STATUS_PENDING


def test_purchase_order_with_quote(dbsession):
    """Test purchase order can reference a quote"""
    from buylog.models import PurchaseOrder, PO_STATUS_ORDERED

    brand = Brand(name="Apple")
    product = Product(name="iPhone 15", brand=brand)
    vendor = Vendor(name="Amazon", currency="USD")
    quote = Quote(product=product, vendor=vendor, currency="USD", value=999.99)
    dbsession.add_all([brand, product, vendor, quote])
    dbsession.flush()

    po = PurchaseOrder(
        po_number="PO-00002",
        vendor=vendor,
        product=product,
        quote=quote,
        unit_price=quote.value,
        quantity=1,
        currency="USD",
        total_amount=999.99,
        grand_total=999.99,
        status=PO_STATUS_ORDERED,
    )
    dbsession.add(po)
    dbsession.commit()

    assert po.quote == quote
    assert po.quote_id == quote.id


def test_purchase_order_repr(dbsession):
    """Test purchase order string representation"""
    from buylog.models import PurchaseOrder

    brand = Brand(name="Apple")
    product = Product(name="iPhone 15", brand=brand)
    vendor = Vendor(name="Amazon", currency="USD")
    dbsession.add_all([brand, product, vendor])
    dbsession.flush()

    po = PurchaseOrder(
        po_number="PO-00003",
        vendor=vendor,
        product=product,
        unit_price=999.99,
        quantity=1,
        currency="USD",
        total_amount=999.99,
        grand_total=999.99,
        status="pending",
    )
    dbsession.add(po)
    dbsession.commit()

    assert repr(po) == "<PurchaseOrder(po_number='PO-00003', status='pending')>"


def test_purchase_order_unique_po_number(dbsession):
    """Test PO number must be unique"""
    from buylog.models import PurchaseOrder

    brand = Brand(name="Apple")
    product = Product(name="iPhone 15", brand=brand)
    vendor = Vendor(name="Amazon", currency="USD")
    dbsession.add_all([brand, product, vendor])
    dbsession.flush()

    po1 = PurchaseOrder(
        po_number="PO-UNIQUE",
        vendor=vendor,
        product=product,
        unit_price=999.99,
        quantity=1,
        currency="USD",
        total_amount=999.99,
        grand_total=999.99,
    )
    po2 = PurchaseOrder(
        po_number="PO-UNIQUE",
        vendor=vendor,
        product=product,
        unit_price=888.88,
        quantity=1,
        currency="USD",
        total_amount=888.88,
        grand_total=888.88,
    )
    dbsession.add_all([po1, po2])

    with pytest.raises(IntegrityError):
        dbsession.commit()


# Quote Tests
def test_quote_creation(dbsession):
    """Test quote can be created with vendor and product"""
    brand = Brand(name="Apple")
    product = Product(name="iPhone 15", brand=brand)
    vendor = Vendor(name="Amazon", currency="USD")
    quote = Quote(product=product, vendor=vendor, currency="USD", value=999.99)
    dbsession.add_all([brand, product, vendor, quote])
    dbsession.flush()

    assert quote.id is not None
    assert quote.product == product
    assert quote.vendor == vendor
    assert quote.value == 999.99
    assert quote.currency == "USD"


def test_quote_repr(dbsession):
    """Test quote string representation"""
    brand = Brand(name="Apple")
    product = Product(name="iPhone 15", brand=brand)
    vendor = Vendor(name="Amazon", currency="USD")
    quote = Quote(product=product, vendor=vendor, currency="USD", value=999.99)
    dbsession.add_all([brand, product, vendor, quote])
    dbsession.commit()

    assert repr(quote) == "<Quote(Amazon / Apple iPhone 15 / 999.99 USD)>"


def test_quote_with_original_currency(dbsession):
    """Test quote stores original currency and value"""
    brand = Brand(name="Apple")
    product = Product(name="iPhone 15", brand=brand)
    vendor = Vendor(name="UK Shop", currency="GBP")
    quote = Quote(
        product=product,
        vendor=vendor,
        currency="USD",
        value=1250.0,
        original_value=1000.0,
        original_currency="GBP",
    )
    dbsession.add_all([brand, product, vendor, quote])
    dbsession.commit()

    assert quote.original_value == 1000.0
    assert quote.original_currency == "GBP"
    assert quote.value == 1250.0
    assert quote.currency == "USD"


# Forex Tests
def test_forex_creation(dbsession):
    """Test forex rate can be created"""

    fx = Forex(code="EUR", usd_per_unit=1.085)
    dbsession.add(fx)
    dbsession.flush()

    assert fx.id is not None
    assert fx.code == "EUR"
    assert fx.usd_per_unit == 1.085
    assert fx.date is not None
    # Test backward compatibility property
    assert abs(fx.units_per_usd - (1.0 / 1.085)) < 0.001


def test_forex_repr(dbsession):
    """Test forex string representation"""
    import datetime

    fx = Forex(code="EUR", usd_per_unit=1.085, date=datetime.date(2025, 10, 17))
    dbsession.add(fx)
    dbsession.commit()

    assert "EUR" in repr(fx)
    assert "USD" in repr(fx)
    assert "1.085" in repr(fx)


def test_forex_multiple_currencies(dbsession):
    """Test multiple forex rates can be stored"""
    eur = Forex(code="EUR", usd_per_unit=1.085)
    gbp = Forex(code="GBP", usd_per_unit=1.292)
    jpy = Forex(code="JPY", usd_per_unit=0.0067)
    dbsession.add_all([eur, gbp, jpy])
    dbsession.commit()

    assert dbsession.query(Forex).count() == 3


def test_forex_historical_rates(dbsession):
    """Test forex rates can be stored with different dates"""
    import datetime

    # Same currency, different dates
    eur_today = Forex(code="EUR", usd_per_unit=1.085, date=datetime.date(2025, 10, 17))
    eur_yesterday = Forex(
        code="EUR", usd_per_unit=1.080, date=datetime.date(2025, 10, 16)
    )
    dbsession.add_all([eur_today, eur_yesterday])
    dbsession.commit()

    # Query rates for specific date
    rates = dbsession.query(Forex).filter(Forex.code == "EUR").all()
    assert len(rates) == 2


def test_forex_units_per_usd_property(dbsession):
    """Test backward compatibility property calculation"""
    fx = Forex(code="EUR", usd_per_unit=1.085)
    dbsession.add(fx)
    dbsession.commit()

    # units_per_usd should be 1/usd_per_unit
    expected = 1.0 / 1.085
    assert abs(fx.units_per_usd - expected) < 0.001


# Integration Tests
def test_full_workflow(dbsession):
    """Test complete workflow: brand -> product -> vendor -> quote"""
    # Create forex rates
    eur = Forex(code="EUR", usd_per_unit=1.085)
    gbp = Forex(code="GBP", usd_per_unit=1.292)

    # Create vendors
    acme_corp = Vendor(
        name="acme-corp", currency="USD", discount_code="abc123", discount=0.1
    )
    amazon = Vendor(name="amazon", currency="GBP")

    # Create brands
    yamaha = Brand(name="yamaha")
    samsung = Brand(name="samsung")
    microsoft = Brand(name="microsoft")

    # Create products
    product_a = Product(name="productA", brand=yamaha)
    product_b = Product(name="productB", brand=samsung)
    product_c = Product(name="productC", brand=microsoft)

    # Create price quotes
    quote1 = Quote(product=product_a, vendor=acme_corp, currency="USD", value=280)
    quote2 = Quote(product=product_a, vendor=amazon, currency="GBP", value=210)

    # Set up relationships
    acme_corp.brands = [yamaha, samsung]
    amazon.brands = [yamaha, samsung, microsoft]

    # Test vendor.add_product
    acme_corp.add_product(dbsession, "sony", "walkman", 99.99)

    dbsession.add_all(
        [
            eur,
            gbp,
            acme_corp,
            amazon,
            yamaha,
            samsung,
            microsoft,
            product_a,
            product_b,
            product_c,
            quote1,
            quote2,
        ]
    )
    dbsession.flush()

    # Assertions
    assert product_a.name == "productA"
    assert Product.by_name(dbsession, "productB").brand.name == "samsung"
    assert repr(product_a) == "<Product(name='productA')>"
    assert "EUR" in repr(eur)
    assert "USD" in repr(eur)
    assert repr(quote1) == "<Quote(acme-corp / yamaha productA / 280 USD)>"
    assert "walkman" in [p.name for p in dbsession.query(Product).all()]

    # Additional assertions
    # Note: add_product creates "sony" brand and adds it to acme_corp.brands
    assert len(acme_corp.brands) == 3  # yamaha, samsung, sony
    assert len(amazon.brands) == 3
    assert product_a.quotes[0].vendor == acme_corp
    sony_brand = Brand.by_name(dbsession, "sony")
    assert sony_brand is not None
    assert sony_brand in acme_corp.brands
    walkman = Product.by_name(dbsession, "walkman")
    assert walkman.brand == sony_brand
