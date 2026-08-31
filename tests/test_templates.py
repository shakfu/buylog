"""Tests for template generation and import functionality"""

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from buylog.templates import (
    vendor_template,
    vendor_template_str,
    vendor_import,
    specification_template,
    specification_template_str,
    specification_import,
    purchase_order_template,
    purchase_order_template_str,
    purchase_order_import,
    load_template,
    save_template,
    import_from_file,
)
from buylog.services import (
    VendorService,
    ProductService,
    QuoteService,
    DuplicateError,
)


# =============================================================================
# Vendor Template Tests
# =============================================================================


def test_vendor_template_structure():
    """Test vendor template has correct structure"""
    tpl = vendor_template("Test Vendor")

    assert tpl["name"] == "Test Vendor"
    assert tpl["currency"] == "USD"
    assert "contact" in tpl
    assert "address" in tpl
    assert "business" in tpl
    assert tpl["contact"]["email"] is None
    assert tpl["address"]["city"] is None


def test_vendor_template_str_yaml():
    """Test vendor template generates valid YAML"""
    output = vendor_template_str("My Vendor", fmt="yaml")
    data = yaml.safe_load(output)

    assert data["name"] == "My Vendor"
    assert data["currency"] == "USD"


def test_vendor_template_str_json():
    """Test vendor template generates valid JSON"""
    output = vendor_template_str("My Vendor", fmt="json")
    data = json.loads(output)

    assert data["name"] == "My Vendor"
    assert data["currency"] == "USD"


def test_vendor_import_basic(dbsession):
    """Test importing vendor from template data"""
    data = {
        "name": "Imported Vendor",
        "currency": "EUR",
        "contact": {"email": "test@example.com"},
        "address": {"city": "Berlin"},
        "business": {"payment_terms": "Net 30"},
    }

    vendor = vendor_import(dbsession, data)

    assert vendor.id is not None
    assert vendor.name == "Imported Vendor"
    assert vendor.currency == "EUR"
    assert vendor.email == "test@example.com"
    assert vendor.city == "Berlin"
    assert vendor.payment_terms == "Net 30"


def test_vendor_import_minimal(dbsession):
    """Test importing vendor with minimal data"""
    data = {"name": "Minimal Vendor", "currency": "USD"}

    vendor = vendor_import(dbsession, data)

    assert vendor.name == "Minimal Vendor"
    assert vendor.email is None


def test_vendor_import_duplicate(dbsession):
    """Test importing duplicate vendor raises error"""
    data = {"name": "Dupe Vendor", "currency": "USD"}
    vendor_import(dbsession, data)

    with pytest.raises(DuplicateError):
        vendor_import(dbsession, data)


# =============================================================================
# Specification Template Tests
# =============================================================================


def test_specification_template_structure():
    """Test specification template has correct structure"""
    tpl = specification_template("Test Spec")

    assert tpl["name"] == "Test Spec"
    assert "features" in tpl
    assert len(tpl["features"]) == 3  # Example features
    assert tpl["features"][0]["data_type"] == "text"
    assert tpl["features"][1]["data_type"] == "number"
    assert tpl["features"][2]["data_type"] == "boolean"


def test_specification_template_str_yaml():
    """Test specification template generates valid YAML"""
    output = specification_template_str("My Spec", fmt="yaml")
    data = yaml.safe_load(output)

    assert data["name"] == "My Spec"
    assert "features" in data


def test_specification_import(dbsession):
    """Test importing specification with features"""
    data = {
        "name": "Camera Specs",
        "description": "Camera product specifications",
        "features": [
            {
                "name": "Resolution",
                "data_type": "number",
                "unit": "MP",
                "is_required": True,
                "min_value": 1,
                "max_value": 200,
            },
            {
                "name": "Brand",
                "data_type": "text",
                "is_required": False,
            },
        ],
    }

    spec = specification_import(dbsession, data)

    assert spec.id is not None
    assert spec.name == "Camera Specs"
    assert spec.description == "Camera product specifications"
    assert len(spec.features) == 2

    resolution = next(f for f in spec.features if f.name == "Resolution")
    assert resolution.data_type == "number"
    assert resolution.unit == "MP"
    assert resolution.is_required == 1
    assert resolution.min_value == 1
    assert resolution.max_value == 200


def test_specification_import_minimal(dbsession):
    """Test importing specification with no features"""
    data = {"name": "Empty Spec", "features": []}

    spec = specification_import(dbsession, data)

    assert spec.name == "Empty Spec"
    assert len(spec.features) == 0


# =============================================================================
# PurchaseOrder Template Tests
# =============================================================================


def test_purchase_order_template_structure():
    """Test purchase order template has correct structure"""
    tpl = purchase_order_template(po_number="PO-TEST-001")

    assert tpl["po_number"] == "PO-TEST-001"
    assert tpl["vendor"] == "Vendor Name"
    assert tpl["product"] == "Product Name"
    assert tpl["quantity"] == 1
    assert tpl["status"] == "pending"


def test_purchase_order_template_from_quote(dbsession):
    """Test purchase order template pre-fills from quote"""
    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")
    quote = QuoteService.create(dbsession, "Amazon", "iPhone 15", 999.99)

    tpl = purchase_order_template(po_number="PO-Q-001", from_quote=quote)

    assert tpl["quote_id"] == quote.id
    assert tpl["vendor"] == "Amazon"
    assert tpl["product"] == "iPhone 15"
    assert tpl["unit_price"] == 999.99


def test_purchase_order_template_str_yaml():
    """Test PO template generates valid YAML"""
    output = purchase_order_template_str(po_number="PO-001", fmt="yaml")
    data = yaml.safe_load(output)

    assert data["po_number"] == "PO-001"
    assert "vendor" in data


def test_purchase_order_import(dbsession):
    """Test importing purchase order"""
    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")

    data = {
        "po_number": "PO-IMPORT-001",
        "vendor": "Amazon",
        "product": "iPhone 15",
        "unit_price": 999.99,
        "quantity": 2,
        "shipping_cost": 25.00,
        "tax": 50.00,
        "status": "pending",
    }

    po = purchase_order_import(dbsession, data)

    assert po.id is not None
    assert po.po_number == "PO-IMPORT-001"
    assert po.unit_price == 999.99
    assert po.quantity == 2
    assert po.total_amount == 999.99 * 2
    assert po.grand_total == 999.99 * 2 + 25.00 + 50.00


def test_purchase_order_import_with_date(dbsession):
    """Test importing PO with dates"""
    import datetime

    VendorService.create(dbsession, "Amazon", currency="USD")
    ProductService.create(dbsession, "iPhone 15", "Apple")

    data = {
        "po_number": "PO-DATE-001",
        "vendor": "Amazon",
        "product": "iPhone 15",
        "unit_price": 100.00,
        "order_date": "2025-01-15",
        "expected_delivery": "2025-01-30",
    }

    po = purchase_order_import(dbsession, data)

    assert po.order_date == datetime.date(2025, 1, 15)
    assert po.expected_delivery == datetime.date(2025, 1, 30)


# =============================================================================
# File I/O Tests
# =============================================================================


def test_load_template_yaml():
    """Test loading YAML template from file"""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        f.write("name: Test\ncurrency: EUR\n")
        f.flush()

        data = load_template(f.name)
        assert data["name"] == "Test"
        assert data["currency"] == "EUR"


def test_load_template_json():
    """Test loading JSON template from file"""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        f.write('{"name": "Test", "currency": "GBP"}')
        f.flush()

        data = load_template(f.name)
        assert data["name"] == "Test"
        assert data["currency"] == "GBP"


def test_save_template_yaml():
    """Test saving template to YAML file"""
    data = {"name": "Test", "value": 123}

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
        save_template(data, f.name)

        content = Path(f.name).read_text()
        loaded = yaml.safe_load(content)
        assert loaded["name"] == "Test"
        assert loaded["value"] == 123


def test_save_template_json():
    """Test saving template to JSON file"""
    data = {"name": "Test", "value": 456}

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        save_template(data, f.name)

        content = Path(f.name).read_text()
        loaded = json.loads(content)
        assert loaded["name"] == "Test"
        assert loaded["value"] == 456


def test_import_from_file_vendor(dbsession):
    """Test importing vendor from file"""
    data = {"name": "File Vendor", "currency": "USD"}

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        yaml.dump(data, f)
        f.flush()

        vendor = import_from_file(dbsession, f.name, "vendor")
        assert vendor.name == "File Vendor"


def test_import_from_file_spec(dbsession):
    """Test importing specification from file"""
    data = {"name": "File Spec", "features": []}

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        yaml.dump(data, f)
        f.flush()

        spec = import_from_file(dbsession, f.name, "spec")
        assert spec.name == "File Spec"


def test_import_from_file_po(dbsession):
    """Test importing purchase order from file"""
    VendorService.create(dbsession, "File Vendor", currency="USD")
    ProductService.create(dbsession, "File Product", "File Brand")

    data = {
        "po_number": "PO-FILE-001",
        "vendor": "File Vendor",
        "product": "File Product",
        "unit_price": 100.00,
    }

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump(data, f)
        f.flush()

        po = import_from_file(dbsession, f.name, "po")
        assert po.po_number == "PO-FILE-001"


def test_import_from_file_invalid_entity_type(dbsession):
    """Test importing with invalid entity type raises error"""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        f.write("name: Test\n")
        f.flush()

        with pytest.raises(ValueError, match="Unknown entity type"):
            import_from_file(dbsession, f.name, "invalid_type")
