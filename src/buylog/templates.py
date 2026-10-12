#!/usr/bin/env python3
"""
Template generation and import for buylog entities.

Supports YAML (default) and JSON formats for:
- Vendor (with enhanced fields)
- Specification (with nested features)
- PurchaseOrder
"""

import json
import datetime
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from .models import Quote, PO_STATUSES, SPEC_DATA_TYPES
from .services import (
    VendorService,
    SpecificationService,
    PurchaseOrderService,
)


def _detect_format(filepath: Union[str, Path]) -> str:
    """Detect format from file extension."""
    ext = Path(filepath).suffix.lower()
    if ext in (".yaml", ".yml"):
        return "yaml"
    elif ext == ".json":
        return "json"
    raise ValueError(f"Unsupported file format: {ext}. Use .yaml, .yml, or .json")


def _serialize(data: dict, fmt: str) -> str:
    """Serialize data to YAML or JSON string."""
    if fmt == "json":
        return json.dumps(data, indent=2, default=str)
    return yaml.dump(
        data, default_flow_style=False, sort_keys=False, allow_unicode=True
    )


def _deserialize(content: str, fmt: str) -> dict:
    """Deserialize YAML or JSON string to dict."""
    if fmt == "json":
        return json.loads(content)
    return yaml.safe_load(content)


# =============================================================================
# Vendor Templates
# =============================================================================


def vendor_template(name: str = "Example Vendor") -> dict:
    """Generate a vendor template with all fields."""
    return {
        "_comment": "Fill in the fields below. Required: name, currency. All others are optional.",
        "name": name,
        "currency": "USD",
        "discount_code": None,
        "discount": 0.0,
        "url": None,
        "contact": {
            "person": None,
            "email": None,
            "phone": None,
            "website": None,
        },
        "address": {
            "line1": None,
            "line2": None,
            "city": None,
            "state": None,
            "postal_code": None,
            "country": None,
        },
        "business": {
            "tax_id": None,
            "payment_terms": None,
        },
    }


def vendor_template_str(name: str = "Example Vendor", fmt: str = "yaml") -> str:
    """Generate vendor template as string."""
    return _serialize(vendor_template(name), fmt)


def vendor_import(session, data: dict) -> Any:
    """
    Import vendor from template data.

    Returns created Vendor instance.
    Raises ValidationError or DuplicateError on failure.
    """
    # Flatten nested structure
    contact = data.get("contact", {}) or {}
    address = data.get("address", {}) or {}
    business = data.get("business", {}) or {}

    return VendorService.create(
        session,
        name=data["name"],
        currency=data.get("currency"),  # None: the base currency
        discount_code=data.get("discount_code"),
        discount=data.get("discount", 0.0),
        url=data.get("url"),
        contact_person=contact.get("person"),
        email=contact.get("email"),
        phone=contact.get("phone"),
        website=contact.get("website"),
        address_line1=address.get("line1"),
        address_line2=address.get("line2"),
        city=address.get("city"),
        state=address.get("state"),
        postal_code=address.get("postal_code"),
        country=address.get("country"),
        tax_id=business.get("tax_id"),
        payment_terms=business.get("payment_terms"),
    )


# =============================================================================
# Specification Templates
# =============================================================================


def specification_template(name: str = "Example Specification") -> dict:
    """Generate a specification template with example features."""
    return {
        "_comment": f"Define features with data_type: {', '.join(SPEC_DATA_TYPES)}",
        "name": name,
        "description": None,
        "features": [
            {
                "name": "Example Text Feature",
                "data_type": "text",
                "unit": None,
                "is_required": False,
                "min_value": None,
                "max_value": None,
            },
            {
                "name": "Example Number Feature",
                "data_type": "number",
                "unit": "units",
                "is_required": True,
                "min_value": 0,
                "max_value": 100,
            },
            {
                "name": "Example Boolean Feature",
                "data_type": "boolean",
                "unit": None,
                "is_required": False,
                "min_value": None,
                "max_value": None,
            },
        ],
    }


def specification_template_str(
    name: str = "Example Specification", fmt: str = "yaml"
) -> str:
    """Generate specification template as string."""
    return _serialize(specification_template(name), fmt)


def specification_import(session, data: dict) -> Any:
    """
    Import specification with features from template data.

    Returns created Specification instance.
    """
    # Create specification
    spec = SpecificationService.create(
        session,
        name=data["name"],
        description=data.get("description"),
    )

    # Add features
    for feature_data in data.get("features", []):
        SpecificationService.add_feature(
            session,
            spec_name=spec.name,
            feature_name=feature_data["name"],
            data_type=feature_data.get("data_type", "text"),
            unit=feature_data.get("unit"),
            is_required=feature_data.get("is_required", False),
            min_value=feature_data.get("min_value"),
            max_value=feature_data.get("max_value"),
        )

    return spec


# =============================================================================
# PurchaseOrder Templates
# =============================================================================


def purchase_order_template(
    po_number: Optional[str] = None,
    from_quote: Optional[Quote] = None,
) -> dict:
    """
    Generate a purchase order template.

    If from_quote is provided, pre-fills vendor, product, and pricing.
    """
    today = datetime.date.today().isoformat()

    if from_quote:
        unit_price, tax = PurchaseOrderService.quote_pricing(
            from_quote, 1, from_quote.shipping_cost
        )
        return {
            "_comment": f"Pre-filled from quote #{from_quote.id}. Adjust as needed.",
            "po_number": po_number
            or f"PO-{datetime.date.today().strftime('%Y%m%d')}-001",
            "quote_id": from_quote.id,
            "vendor": from_quote.vendor.name,
            "product": from_quote.product.name,
            "quantity": 1,
            "unit_price": unit_price,
            "currency": from_quote.base_currency,  # from_quote.value is in it
            "shipping_cost": from_quote.shipping_cost,
            "tax": tax,
            "order_date": today,
            "expected_delivery": None,
            "invoice_number": None,
            "notes": None,
            "status": "pending",
        }

    return {
        "_comment": f"Status options: {', '.join(PO_STATUSES)}",
        "po_number": po_number or f"PO-{datetime.date.today().strftime('%Y%m%d')}-001",
        "quote_id": None,
        "vendor": "Vendor Name",
        "product": "Product Name",
        "quantity": 1,
        "unit_price": 0.0,
        "currency": "USD",
        "shipping_cost": None,
        "tax": None,
        "order_date": today,
        "expected_delivery": None,
        "invoice_number": None,
        "notes": None,
        "status": "pending",
    }


def purchase_order_template_str(
    po_number: Optional[str] = None,
    from_quote: Optional[Quote] = None,
    fmt: str = "yaml",
) -> str:
    """Generate purchase order template as string."""
    return _serialize(purchase_order_template(po_number, from_quote), fmt)


def _parse_date(value: Any) -> Optional[datetime.date]:
    """Parse date from string or date object."""
    if value is None:
        return None
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        return datetime.date.fromisoformat(value)
    raise ValueError(f"Invalid date format: {value}")


def purchase_order_import(session, data: dict) -> Any:
    """
    Import purchase order from template data.

    Returns created PurchaseOrder instance.
    """
    return PurchaseOrderService.create(
        session,
        po_number=data["po_number"],
        vendor_name=data["vendor"],
        product_name=data["product"],
        unit_price=float(data["unit_price"]),
        quantity=int(data.get("quantity", 1)),
        currency=data.get("currency"),  # None: the vendor's currency
        quote_id=data.get("quote_id"),
        order_date=_parse_date(data.get("order_date")),
        expected_delivery=_parse_date(data.get("expected_delivery")),
        shipping_cost=data.get("shipping_cost"),
        tax=data.get("tax"),
        invoice_number=data.get("invoice_number"),
        notes=data.get("notes"),
        status=data.get("status", "pending"),
    )


# =============================================================================
# Generic Import/Export Functions
# =============================================================================


def load_template(filepath: Union[str, Path]) -> dict:
    """Load template from file, auto-detecting format."""
    filepath = Path(filepath)
    fmt = _detect_format(filepath)
    content = filepath.read_text()
    return _deserialize(content, fmt)


def save_template(
    data: dict, filepath: Union[str, Path], fmt: Optional[str] = None
) -> None:
    """Save template to file."""
    filepath = Path(filepath)
    if fmt is None:
        fmt = _detect_format(filepath)
    content = _serialize(data, fmt)
    filepath.write_text(content)


def import_from_file(session, filepath: Union[str, Path], entity_type: str) -> Any:
    """
    Import entity from template file.

    Args:
        session: Database session
        filepath: Path to YAML/JSON file
        entity_type: One of 'vendor', 'specification', 'po'

    Returns:
        Created entity instance
    """
    data = load_template(filepath)

    importers = {
        "vendor": vendor_import,
        "specification": specification_import,
        "spec": specification_import,
        "po": purchase_order_import,
        "purchase_order": purchase_order_import,
    }

    importer = importers.get(entity_type.lower())
    if not importer:
        raise ValueError(
            f"Unknown entity type: {entity_type}. Use: {', '.join(importers.keys())}"
        )

    return importer(session, data)
