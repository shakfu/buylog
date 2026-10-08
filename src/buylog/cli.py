#!/usr/bin/env python3
"""CLI interface for buylog tool with CRUD operations"""

import argparse
import datetime
import os
import sys
from pathlib import Path
from typing import Optional
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as SessionType
from tabulate import tabulate
from .models import (
    Brand,
    Product,
    Vendor,
    Quote,
    Forex,
    init_db,
    PO_STATUSES,
    SPEC_DATA_TYPES,
)
from .config import Config
from .services import (
    ForexService,
    ServiceError,
    ValidationError,
    DuplicateError,
    NotFoundError,
)

# Setup logging
logger = Config.setup_logging()

# Database setup
engine = Config.get_engine()
init_db(engine)
Session = Config.get_session_maker()


def _parse_date(value: Optional[str]) -> Optional[datetime.date]:
    """
    Parse a YYYY-MM-DD command-line date.

    Args:
        value: Date string, or None

    Returns:
        Parsed date, or None if no value was given

    Raises:
        ValueError: If the string is not a valid YYYY-MM-DD date
    """
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD") from None


def confirm(question: str, assume_yes: bool = False) -> bool:
    """
    Ask the user to confirm an action.

    Args:
        question: Question to display, without the "(y/n)" suffix
        assume_yes: Skip the prompt and answer yes (the --yes flag)

    Returns:
        True if the action was confirmed

    Raises:
        ValueError: If confirmation is needed but stdin is not interactive.
                    Blocking on input() in a pipe or a cron job used to raise
                    EOFError and surface as "Unexpected error occurred".
    """
    if assume_yes:
        return True
    if not sys.stdin or not sys.stdin.isatty():
        raise ValueError(
            f"{question} - cannot prompt on a non-interactive stdin. "
            "Re-run with --yes to confirm automatically."
        )
    return input(f"{question} (y/n): ").strip().lower() == "y"


def add_brand(session: SessionType, name: str) -> Optional[Brand]:
    """
    Add a new brand to the database.

    Args:
        session: Active database session
        name: Brand name to add

    Returns:
        Created or existing Brand instance, or None on error

    Example:
        >>> brand = add_brand(session, "Apple")
        Added brand: Apple
    """
    from .services import BrandService

    try:
        brand = BrandService.create(session, name)
        print(f"Added brand: {name}")
        return brand
    except DuplicateError:
        print(f"Brand '{name}' already exists")
        return BrandService.get_by_name(session, name)
    except ValidationError as e:
        print(f"Error: {e}")
        return None


def add_product(session, brand_name, product_name, category=None):
    """Add a new product under a brand"""
    from .services import ProductService

    if not Brand.by_name(session, brand_name):
        print(f"Brand '{brand_name}' not found. Adding it first.")
        if add_brand(session, brand_name) is None:
            return None

    try:
        product = ProductService.create(
            session, product_name, brand_name, category=category
        )
        print(f"Added product: {product_name} under brand: {brand_name}")
        return product
    except DuplicateError:
        print(f"Product '{product_name}' already exists")
        return ProductService.get_by_name(session, product_name)
    except (ValidationError, NotFoundError) as e:
        print(f"Error: {e}")
        return None


def add_vendor(session, name, currency="USD", discount_code=None, discount=0.0):
    """Add a new vendor"""
    from .services import VendorService

    try:
        vendor = VendorService.create(
            session,
            name,
            currency=currency,
            discount_code=discount_code,
            discount=discount,
        )
        print(f"Added vendor: {name} (currency: {currency})")
        return vendor
    except DuplicateError:
        print(f"Vendor '{name}' already exists")
        return Vendor.by_name(session, name)
    except ValidationError as e:
        print(f"Error: {e}")
        return None


def add_quote(
    session,
    vendor_name,
    product_name,
    price,
    brand_name=None,
    shipping_cost=None,
    tax_rate=None,
    assume_yes=False,
):
    """Add a quote from a vendor for a product"""
    from .services import QuoteService

    vendor = Vendor.by_name(session, vendor_name)
    if not vendor:
        if not confirm(f"Vendor '{vendor_name}' not found. Create it?", assume_yes):
            return None
        vendor = add_vendor(session, vendor_name)
        if vendor is None:
            return None

    if not Product.by_name(session, product_name):
        if not confirm(f"Product '{product_name}' not found. Create it?", assume_yes):
            return None
        if not brand_name:
            raise ValueError(
                f"Product '{product_name}' does not exist; "
                "pass --brand to say which brand to create it under"
            )

    try:
        quote = QuoteService.create(
            session,
            vendor_name=vendor.name,
            product_name=product_name,
            price=float(price),
            brand_name=brand_name,
            shipping_cost=shipping_cost,
            tax_rate=tax_rate,
        )
    except (ValidationError, NotFoundError) as e:
        print(f"Error: {e}")
        return None

    print(f"Added quote: {vendor_name} -> {product_name} = {price} {vendor.currency}")
    if shipping_cost:
        print(f"  Shipping: {shipping_cost}")
    if tax_rate:
        print(f"  Tax rate: {tax_rate}%")
    return quote


def add_fx(session, code, usd_per_unit, date=None):
    """Add a new forex rate for a specific date (defaults to today)"""
    import datetime

    # Use provided date or default to today
    if date is None:
        date = datetime.date.today()
    elif isinstance(date, str):
        # Parse date string if provided
        try:
            date = datetime.datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            print("Error: Invalid date format. Use YYYY-MM-DD")
            return None

    try:
        fx = ForexService.create(session, code, usd_per_unit, date=date)
        print(f"Added forex rate: {code} = {usd_per_unit} USD per unit on {date}")
        return fx
    except DuplicateError:
        print(f"Forex rate for '{code}' on {date} already exists")
        return session.execute(
            select(Forex).where(Forex.code == code.upper(), Forex.date == date)
        ).scalar_one_or_none()
    except ValidationError as e:
        print(f"Error: {e}")
        return None


def list_entities(
    session: SessionType,
    entity_type: str,
    filter_by: Optional[str] = None,
    sort_by: Optional[str] = None,
    limit: int = 100,
) -> None:
    """
    List all entities of a given type with pagination.

    Uses service layer with eager loading to avoid N+1 query problems.

    Args:
        session: Active database session
        entity_type: Type of entity to list ("brands", "products", "vendors", "quotes")
        filter_by: Optional name filter for case-insensitive partial match
        sort_by: Optional column to sort by (currently unused, reserved for future)
        limit: Maximum number of results to display (default: 100)

    Example:
        >>> list_entities(session, "brands", filter_by="Apple")
        ╒══════╤═══════╤═════════════════════╕
        │   ID │ Name  │ Products            │
        ╞══════╪═══════╪═════════════════════╡
        │    1 │ Apple │ iPhone 15, iPhone 14│
        ╘══════╧═══════╧═════════════════════╛
    """
    from .services import BrandService, ProductService, VendorService, QuoteService

    if entity_type == "brands":
        brands = BrandService.get_all(session, filter_by=filter_by, limit=limit)
        headers = ["ID", "Name", "Products"]
        data = [[b.id, b.name, ", ".join([p.name for p in b.products])] for b in brands]
        print(tabulate(data, headers=headers, tablefmt="grid"))

    elif entity_type == "products":
        products = ProductService.get_all(session, filter_by=filter_by, limit=limit)
        headers = ["ID", "Name", "Brand"]
        data = [[p.id, p.name, p.brand.name] for p in products]
        print(tabulate(data, headers=headers, tablefmt="grid"))

    elif entity_type == "vendors":
        vendors = VendorService.get_all(session, filter_by=filter_by, limit=limit)
        headers = ["ID", "Name", "Currency", "Quotes"]
        data = [[v.id, v.name, v.currency, len(v.quotes)] for v in vendors]
        print(tabulate(data, headers=headers, tablefmt="grid"))

    elif entity_type == "quotes":
        quotes = QuoteService.get_all(session, filter_by=filter_by, limit=limit)
        headers = ["ID", "Vendor", "Product", "Price (USD)", "As Entered"]
        data = [
            [
                q.id,
                q.vendor.name,
                f"{q.product.brand.name} {q.product.name}",
                f"${q.value:,.2f}",
                q.source_price,
            ]
            for q in quotes
        ]
        print(tabulate(data, headers=headers, tablefmt="grid"))

    elif entity_type == "forex":
        rates = (
            session.execute(
                select(Forex).order_by(Forex.date.desc(), Forex.code).limit(limit)
            )
            .scalars()
            .all()
        )
        headers = ["ID", "Date", "Code", "USD per unit"]
        data = [[f.id, f.date, f.code, f"{f.usd_per_unit:.6f}"] for f in rates]
        print(tabulate(data, headers=headers, tablefmt="grid"))


def delete_entity(session, entity_type, name=None, id=None, assume_yes=False):
    """
    Delete an entity by name or ID.

    Deletes go through the service layer, so dependent rows (a quote's price
    history, a product's alerts and watchlist entries) are removed with the
    entity instead of being left orphaned.

    Returns:
        A process exit code.
    """
    from .services import BrandService, ProductService, QuoteService, VendorService

    if entity_type == "quote":
        if not id:
            print("Please provide the ID of the quote to delete using --id.")
            return 1
        entity = QuoteService.get_by_id(session, id)
        label = str(id)
    else:
        if not name:
            print(
                f"Please provide the name of the {entity_type} to delete using --name."
            )
            return 1
        lookup = {
            "brand": Brand.by_name,
            "product": Product.by_name,
            "vendor": Vendor.by_name,
        }[entity_type]
        entity = lookup(session, name)
        label = name

    if not entity:
        print(f"{entity_type.capitalize()} not found.")
        return 1

    # Deleting a vendor or product takes its quotes and their price history
    # with it, so say so before asking.
    dependents = ""
    quote_count = len(getattr(entity, "quotes", []) or [])
    if quote_count:
        dependents = f" and its {quote_count} quote(s)"

    if not confirm(
        f"Are you sure you want to delete {entity_type} '{label}'{dependents}?",
        assume_yes,
    ):
        print("Cancelled.")
        return 0

    deleters = {
        "brand": BrandService.delete,
        "product": ProductService.delete,
        "vendor": VendorService.delete,
        "quote": QuoteService.delete,
    }
    try:
        deleters[entity_type](session, id if entity_type == "quote" else name)
    except (NotFoundError, ValidationError) as e:
        print(f"Error: {e}")
        return 1

    print(f"Deleted {entity_type}: '{label}'")
    return 0


def update_entity(session, entity_type, name, new_name):
    """
    Rename an entity through the service layer.

    Returns:
        A process exit code.
    """
    from .services import BrandService, ProductService, VendorService

    updaters = {
        "brand": BrandService.update,
        "product": ProductService.update,
        "vendor": VendorService.update,
    }
    if entity_type not in updaters:
        print(f"Updating '{entity_type}' is not supported.")
        return 1

    if not new_name:
        print("Nothing to update: pass --new-name.")
        return 1

    try:
        updaters[entity_type](session, name, new_name)
    except NotFoundError:
        print(f"{entity_type.capitalize()} '{name}' not found.")
        return 1
    except (ValidationError, DuplicateError) as e:
        print(f"Error: {e}")
        return 1

    print(f"Updated {entity_type} '{name}' to '{new_name}'.")
    return 0


def seed_database(session):
    """Populate database with sample data"""
    import datetime

    # Brands
    brands_data = [
        "Apple",
        "Samsung",
        "Sony",
        "LG",
        "Dell",
        "HP",
        "Lenovo",
        "Asus",
        "Acer",
        "Microsoft",
    ]
    brands = {}
    for name in brands_data:
        existing = Brand.by_name(session, name)
        if existing:
            brands[name] = existing
        else:
            brand = Brand(name=name)
            session.add(brand)
            brands[name] = brand
    session.commit()
    print(f"Added {len(brands_data)} brands")

    # Products (brand, name, category)
    products_data = [
        ("Apple", "iPhone 15 Pro", "Mobile Phones"),
        ("Apple", "MacBook Air M3", "Laptops"),
        ("Apple", "iPad Pro 12.9", "Tablets"),
        ("Samsung", "Galaxy S24 Ultra", "Mobile Phones"),
        ("Samsung", "Galaxy Tab S9", "Tablets"),
        ("Sony", "WH-1000XM5", "Headphones"),
        ("Sony", "PlayStation 5", "Gaming Consoles"),
        ("LG", "C3 OLED 65", "TVs"),
        ("Dell", "XPS 15", "Laptops"),
        ("HP", "Spectre x360", "Laptops"),
        ("Lenovo", "ThinkPad X1 Carbon", "Laptops"),
        ("Asus", "ROG Zephyrus G14", "Laptops"),
        ("Microsoft", "Surface Pro 9", "Tablets"),
        ("Microsoft", "Xbox Series X", "Gaming Consoles"),
    ]
    products = {}
    for brand_name, product_name, category in products_data:
        existing = Product.by_name(session, product_name)
        if existing:
            existing.category = category  # Update category if exists
            products[product_name] = existing
        else:
            product = Product(
                name=product_name, brand=brands[brand_name], category=category
            )
            session.add(product)
            products[product_name] = product
    session.commit()
    print(f"Added {len(products_data)} products")

    # Vendors
    vendors_data = [
        ("Amazon US", "USD", None, 0.0),
        ("Amazon UK", "GBP", "UKPRIME", 5.0),
        ("Amazon DE", "EUR", None, 0.0),
        ("Best Buy", "USD", "BBY10", 10.0),
        ("B&H Photo", "USD", None, 0.0),
        ("Newegg", "USD", "NEWEGG5", 5.0),
        ("Adorama", "USD", None, 0.0),
        ("Currys UK", "GBP", None, 0.0),
        ("MediaMarkt", "EUR", "MM15", 15.0),
    ]
    vendors = {}
    for name, currency, discount_code, discount in vendors_data:
        existing = Vendor.by_name(session, name)
        if existing:
            vendors[name] = existing
        else:
            vendor = Vendor(
                name=name,
                currency=currency,
                discount_code=discount_code,
                discount=discount,
            )
            session.add(vendor)
            vendors[name] = vendor
    session.commit()
    print(f"Added {len(vendors_data)} vendors")

    # Forex rates
    today = datetime.date.today()
    forex_data = [
        ("EUR", 1.08),
        ("GBP", 1.27),
        ("JPY", 0.0067),
        ("CAD", 0.74),
        ("AUD", 0.65),
    ]
    for code, rate in forex_data:
        existing = session.execute(
            select(Forex).where(Forex.code == code, Forex.date == today)
        ).scalar_one_or_none()
        if not existing:
            fx = Forex(code=code, usd_per_unit=rate, date=today)
            session.add(fx)
    session.commit()
    print(f"Added {len(forex_data)} forex rates")

    # Quotes
    quotes_data = [
        ("Amazon US", "iPhone 15 Pro", 999.00),
        ("Best Buy", "iPhone 15 Pro", 999.00),
        ("B&H Photo", "iPhone 15 Pro", 979.00),
        ("Amazon US", "MacBook Air M3", 1099.00),
        ("Best Buy", "MacBook Air M3", 1099.00),
        ("Amazon US", "Galaxy S24 Ultra", 1299.00),
        ("Newegg", "Galaxy S24 Ultra", 1249.00),
        ("Amazon US", "WH-1000XM5", 348.00),
        ("Amazon UK", "WH-1000XM5", 279.00),
        ("Amazon DE", "WH-1000XM5", 299.00),
        ("Best Buy", "PlayStation 5", 499.00),
        ("Amazon US", "PlayStation 5", 499.00),
        ("Amazon US", "XPS 15", 1499.00),
        ("Dell", "XPS 15", 1399.00) if Vendor.by_name(session, "Dell") else None,
        ("Amazon US", "C3 OLED 65", 1499.00),
        ("Best Buy", "C3 OLED 65", 1399.00),
        ("Currys UK", "C3 OLED 65", 1199.00),
        ("Amazon US", "ThinkPad X1 Carbon", 1649.00),
        ("Newegg", "ThinkPad X1 Carbon", 1599.00),
        ("Amazon US", "Surface Pro 9", 999.00),
        ("Best Buy", "Surface Pro 9", 999.00),
        ("MediaMarkt", "Surface Pro 9", 949.00),
    ]
    quote_count = 0
    for item in quotes_data:
        if item is None:
            continue
        vendor_name, product_name, price = item
        vendor = vendors.get(vendor_name)
        product = products.get(product_name)
        if not vendor or not product:
            continue

        value = price
        original_value = None
        original_currency = None

        if vendor.currency != "USD":
            fx_rate = ForexService.get_latest(session, vendor.currency)
            if fx_rate:
                original_value = value
                original_currency = vendor.currency
                value = value * fx_rate.usd_per_unit

        quote = Quote(
            vendor=vendor,
            product=product,
            currency="USD",
            value=value,
            original_value=original_value,
            original_currency=original_currency,
        )
        session.add(quote)
        quote_count += 1
    session.commit()
    print(f"Added {quote_count} quotes")
    print("Database seeded successfully!")


def search_entities(session, query):
    """Search for items in the database"""

    print(f"Searching for '{query}'...")

    brands = (
        session.execute(select(Brand).where(Brand.name.ilike(f"%{query}%")))
        .scalars()
        .all()
    )

    products = (
        session.execute(select(Product).where(Product.name.ilike(f"%{query}%")))
        .scalars()
        .all()
    )

    vendors = (
        session.execute(select(Vendor).where(Vendor.name.ilike(f"%{query}%")))
        .scalars()
        .all()
    )

    if brands:
        print("\n--- Brands ---")

        headers = ["ID", "Name", "Products"]

        data = [[b.id, b.name, ", ".join([p.name for p in b.products])] for b in brands]

        print(tabulate(data, headers=headers, tablefmt="grid"))

    if products:
        print("\n--- Products ---")

        headers = ["ID", "Name", "Brand"]

        data = [[p.id, p.name, p.brand.name] for p in products]

        print(tabulate(data, headers=headers, tablefmt="grid"))

    if vendors:
        print("\n--- Vendors ---")

        headers = ["ID", "Name", "Currency", "Quotes"]

        data = [[v.id, v.name, v.currency, len(v.quotes)] for v in vendors]

        print(tabulate(data, headers=headers, tablefmt="grid"))


def main(argv: Optional[list[str]] = None) -> int:
    """
    Run the buylog CLI.

    Args:
        argv: Argument list, defaulting to ``sys.argv[1:]``

    Returns:
        Process exit code: 0 on success, 1 on a handled error. Returning a code
        rather than always falling off the end lets scripts and cron detect
        failures.
    """
    parser = argparse.ArgumentParser(
        description="Buyer - Purchasing support and vendor quote management"
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Assume yes for all confirmation prompts (for non-interactive use)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Add command
    add_parser = subparsers.add_parser("add", help="Add entities")
    opt = add_parser.add_argument
    opt("-b", "--brand", type=str, help="Add a brand")
    opt("-p", "--product", type=str, help="Add a product (requires --brand)")
    opt("--category", type=str, help="Category for a new product")
    opt("-v", "--vendor", type=str, help="Add a vendor")
    opt(
        "-q",
        "--quote",
        type=float,
        help="Add a quote (requires --vendor and --product)",
    )
    opt(
        "-c",
        "--currency",
        type=str,
        default="USD",
        help="Currency for vendor (default: USD)",
    )
    opt("-dc", "--discount-code", type=str, help="Discount code for vendor")
    opt(
        "-d",
        "--discount",
        type=float,
        default=0.0,
        help="Discount percentage for vendor",
    )
    opt(
        "--shipping",
        type=float,
        help="Shipping cost for quote",
    )
    opt(
        "--tax-rate",
        type=float,
        help="Tax rate percentage for quote",
    )

    # Add-fx command
    add_fx_parser = subparsers.add_parser("add-fx", help="Add forex rates")
    opt = add_fx_parser.add_argument
    opt("--code", type=str, required=True, help="Currency code (e.g., EUR, GBP)")
    opt(
        "--usd-per-unit",
        type=float,
        required=True,
        help="USD per unit (e.g., 1.085 for EUR)",
    )
    opt("--date", type=str, help="Date for rate (YYYY-MM-DD, defaults to today)")

    # List command
    list_parser = subparsers.add_parser("list", help="List entities")
    list_parser.add_argument(
        "type",
        choices=["brands", "products", "vendors", "quotes"],
        help="Type of entity to list",
    )
    list_parser.add_argument("--filter", type=str, help="Filter by name")
    list_parser.add_argument("--sort-by", type=str, help="Sort by column")

    # Delete command
    delete_parser = subparsers.add_parser("delete", help="Delete entities")
    opt = delete_parser.add_argument
    opt(
        "type",
        choices=["brand", "product", "vendor", "quote"],
        help="Type of entity to delete",
    )
    opt("--name", type=str, help="Name of the entity to delete")
    opt("--id", type=int, help="ID of the quote to delete")

    # Update command
    update_parser = subparsers.add_parser("update", help="Update entities")
    opt = update_parser.add_argument
    opt("type", choices=["brand", "product", "vendor"], help="Type of entity to update")
    opt("name", type=str, help="Name of the entity to update")
    opt("--new-name", type=str, help="New name for the entity")

    # Search command
    search_parser = subparsers.add_parser("search", help="Search for items")
    search_parser.add_argument("query", type=str, help="Search query")

    # TUI command
    subparsers.add_parser("tui", help="Launch interactive TUI")

    # Seed command
    subparsers.add_parser("seed", help="Populate database with sample data")

    # Alert command
    alert_parser = subparsers.add_parser("alert", help="Manage price alerts")
    alert_subparsers = alert_parser.add_subparsers(
        dest="alert_command", help="Alert commands"
    )

    # Alert add
    alert_add_parser = alert_subparsers.add_parser("add", help="Add a price alert")
    alert_add_parser.add_argument("product", type=str, help="Product name")
    alert_add_parser.add_argument("threshold", type=float, help="Price threshold")

    # Alert list
    alert_list_parser = alert_subparsers.add_parser("list", help="List price alerts")
    alert_list_parser.add_argument(
        "--triggered", action="store_true", help="Show only triggered alerts"
    )

    # Alert deactivate
    alert_deactivate_parser = alert_subparsers.add_parser(
        "deactivate", help="Deactivate an alert"
    )
    alert_deactivate_parser.add_argument("id", type=int, help="Alert ID")

    # History command
    history_parser = subparsers.add_parser("history", help="View price history")
    history_parser.add_argument("--product", type=str, help="Product name")
    history_parser.add_argument("--quote-id", type=int, help="Quote ID")

    # Compare command
    compare_parser = subparsers.add_parser("compare", help="Compare prices")
    compare_parser.add_argument("--product", type=str, help="Exact product name")
    compare_parser.add_argument("--search", type=str, help="Search term for products")
    compare_parser.add_argument("--category", type=str, help="Product category")
    compare_parser.add_argument("--brand", type=str, help="Brand name")

    # Category command
    category_parser = subparsers.add_parser(
        "category", help="Manage product categories"
    )
    category_subparsers = category_parser.add_subparsers(
        dest="category_command", help="Category commands"
    )

    # Category set
    category_set_parser = category_subparsers.add_parser(
        "set", help="Set product category"
    )
    category_set_parser.add_argument("product", type=str, help="Product name")
    category_set_parser.add_argument("category", type=str, help="Category name")

    # Category list
    category_subparsers.add_parser("list", help="List all categories")

    # Purchase list command
    list_parser = subparsers.add_parser("purchase-list", help="Manage purchase lists")
    list_subparsers = list_parser.add_subparsers(
        dest="list_command", help="Purchase list commands"
    )

    # Purchase list create
    list_create_parser = list_subparsers.add_parser(
        "create", help="Create a purchase list"
    )
    list_create_parser.add_argument("name", type=str, help="List name")
    list_create_parser.add_argument("--description", type=str, help="List description")

    # Purchase list add quote
    list_add_parser = list_subparsers.add_parser("add", help="Add quote to list")
    list_add_parser.add_argument("list_name", type=str, help="Purchase list name")
    list_add_parser.add_argument("quote_id", type=int, help="Quote ID to add")

    # Purchase list remove quote
    list_remove_parser = list_subparsers.add_parser(
        "remove", help="Remove quote from list"
    )
    list_remove_parser.add_argument("list_name", type=str, help="Purchase list name")
    list_remove_parser.add_argument("quote_id", type=int, help="Quote ID to remove")

    # Purchase list show
    list_show_parser = list_subparsers.add_parser(
        "show", help="Show purchase list contents"
    )
    list_show_parser.add_argument("name", type=str, help="List name")

    # Purchase list delete
    list_delete_parser = list_subparsers.add_parser(
        "delete", help="Delete a purchase list"
    )
    list_delete_parser.add_argument("name", type=str, help="List name")

    # Purchase list all
    list_subparsers.add_parser("all", help="List all purchase lists")

    # Quotes you might buy, across every list
    list_subparsers.add_parser(
        "might-buy", help="Quotes you might buy, from every purchase list"
    )

    # Status command
    status_parser = subparsers.add_parser("status", help="Manage quote status")
    status_subparsers = status_parser.add_subparsers(
        dest="status_command", help="Status commands"
    )

    # Status set
    status_set_parser = status_subparsers.add_parser("set", help="Set quote status")
    status_set_parser.add_argument("quote_id", type=int, help="Quote ID")
    status_set_parser.add_argument(
        "status",
        type=str,
        choices=["considering", "ordered", "received"],
        help="Status",
    )

    # Status list
    status_list_parser = status_subparsers.add_parser(
        "list", help="List quotes by status"
    )
    status_list_parser.add_argument(
        "status",
        type=str,
        choices=["considering", "ordered", "received"],
        help="Status",
    )

    # Note command
    note_parser = subparsers.add_parser("note", help="Manage notes")
    note_subparsers = note_parser.add_subparsers(
        dest="note_command", help="Note commands"
    )

    # Note add
    note_add_parser = note_subparsers.add_parser("add", help="Add a note")
    note_add_parser.add_argument(
        "entity_type",
        type=str,
        choices=["product", "vendor", "quote", "brand"],
        help="Entity type",
    )
    note_add_parser.add_argument("entity_id", type=int, help="Entity ID")
    note_add_parser.add_argument("content", type=str, help="Note content")

    # Note list
    note_list_parser = note_subparsers.add_parser("list", help="List notes for entity")
    note_list_parser.add_argument(
        "entity_type",
        type=str,
        choices=["product", "vendor", "quote", "brand"],
        help="Entity type",
    )
    note_list_parser.add_argument("entity_id", type=int, help="Entity ID")

    # Note delete
    note_delete_parser = note_subparsers.add_parser("delete", help="Delete a note")
    note_delete_parser.add_argument("note_id", type=int, help="Note ID")

    # Tag command
    tag_parser = subparsers.add_parser("tag", help="Manage tags")
    tag_subparsers = tag_parser.add_subparsers(dest="tag_command", help="Tag commands")

    # Tag add
    tag_add_parser = tag_subparsers.add_parser("add", help="Add tag to entity")
    tag_add_parser.add_argument("tag_name", type=str, help="Tag name")
    tag_add_parser.add_argument(
        "entity_type",
        type=str,
        choices=["product", "vendor", "quote", "brand"],
        help="Entity type",
    )
    tag_add_parser.add_argument("entity_id", type=int, help="Entity ID")

    # Tag remove
    tag_remove_parser = tag_subparsers.add_parser(
        "remove", help="Remove tag from entity"
    )
    tag_remove_parser.add_argument("tag_name", type=str, help="Tag name")
    tag_remove_parser.add_argument(
        "entity_type",
        type=str,
        choices=["product", "vendor", "quote", "brand"],
        help="Entity type",
    )
    tag_remove_parser.add_argument("entity_id", type=int, help="Entity ID")

    # Tag list
    tag_list_parser = tag_subparsers.add_parser("list", help="List tags")
    tag_list_parser.add_argument(
        "--entity-type",
        type=str,
        choices=["product", "vendor", "quote", "brand"],
        help="Filter by entity type",
    )
    tag_list_parser.add_argument(
        "--entity-id",
        type=int,
        help="Show tags for specific entity (requires --entity-type)",
    )

    # Tag search
    tag_search_parser = tag_subparsers.add_parser("search", help="Find entities by tag")
    tag_search_parser.add_argument("tag_name", type=str, help="Tag name")
    tag_search_parser.add_argument(
        "--entity-type",
        type=str,
        choices=["product", "vendor", "quote", "brand"],
        help="Filter by entity type",
    )

    # Watchlist command
    watchlist_parser = subparsers.add_parser("watchlist", help="Manage watchlist")
    watchlist_subparsers = watchlist_parser.add_subparsers(
        dest="watchlist_command", help="Watchlist commands"
    )

    # Watchlist add
    watchlist_add_parser = watchlist_subparsers.add_parser(
        "add", help="Add product to watchlist"
    )
    watchlist_add_parser.add_argument("product", type=str, help="Product name")
    watchlist_add_parser.add_argument("--target-price", type=float, help="Target price")
    watchlist_add_parser.add_argument(
        "--currency",
        type=str,
        help="Currency of --target-price (default USD)",
    )
    watchlist_add_parser.add_argument("--notes", type=str, help="Notes")

    # Watchlist list
    watchlist_list_parser = watchlist_subparsers.add_parser(
        "list", help="List watchlist"
    )
    watchlist_list_parser.add_argument(
        "--all", action="store_true", help="Include inactive items"
    )

    # Watchlist update
    watchlist_update_parser = watchlist_subparsers.add_parser(
        "update", help="Update watchlist entry"
    )
    watchlist_update_parser.add_argument("id", type=int, help="Watchlist entry ID")
    watchlist_update_parser.add_argument(
        "--target-price", type=float, help="New target price"
    )
    watchlist_update_parser.add_argument(
        "--currency",
        type=str,
        help="Currency of the target price",
    )
    watchlist_update_parser.add_argument("--notes", type=str, help="New notes")

    # Watchlist remove
    watchlist_remove_parser = watchlist_subparsers.add_parser(
        "remove", help="Remove from watchlist"
    )
    watchlist_remove_parser.add_argument("id", type=int, help="Watchlist entry ID")

    # Purchase order command
    po_parser = subparsers.add_parser("po", help="Manage purchase orders")
    po_subparsers = po_parser.add_subparsers(
        dest="po_command", help="Purchase order commands"
    )

    # PO create
    po_create_parser = po_subparsers.add_parser(
        "create", help="Create a purchase order"
    )
    po_create_parser.add_argument("po_number", type=str, help="Unique PO number")
    po_create_parser.add_argument("--vendor", type=str, help="Vendor name")
    po_create_parser.add_argument("--product", type=str, help="Product name")
    po_create_parser.add_argument("--price", type=float, help="Unit price")
    po_create_parser.add_argument(
        "--quantity", type=int, default=1, help="Number of units (default: 1)"
    )
    po_create_parser.add_argument(
        "--from-quote", type=int, help="Create from an existing quote ID"
    )
    po_create_parser.add_argument("--shipping", type=float, help="Shipping cost")
    po_create_parser.add_argument("--tax", type=float, help="Tax amount")
    po_create_parser.add_argument(
        "--order-date", type=str, help="Order date (YYYY-MM-DD)"
    )
    po_create_parser.add_argument(
        "--expected-delivery", type=str, help="Expected delivery date (YYYY-MM-DD)"
    )
    po_create_parser.add_argument("--invoice", type=str, help="Invoice number")
    po_create_parser.add_argument("--notes", type=str, help="Notes")

    # PO list
    po_list_parser = po_subparsers.add_parser("list", help="List purchase orders")
    po_list_parser.add_argument(
        "--status", type=str, choices=PO_STATUSES, help="Filter by status"
    )
    po_list_parser.add_argument("--vendor", type=str, help="Filter by vendor name")
    po_list_parser.add_argument(
        "--limit", type=int, default=100, help="Maximum rows (default: 100)"
    )

    # PO show
    po_show_parser = po_subparsers.add_parser("show", help="Show a purchase order")
    po_show_parser.add_argument("po_number", type=str, help="PO number")

    # PO update
    po_update_parser = po_subparsers.add_parser(
        "update", help="Update a purchase order"
    )
    po_update_parser.add_argument("po_number", type=str, help="PO number")
    po_update_parser.add_argument(
        "--status", type=str, choices=PO_STATUSES, required=True, help="New status"
    )
    po_update_parser.add_argument(
        "--delivery-date", type=str, help="Actual delivery date (YYYY-MM-DD)"
    )

    # Specification command
    spec_parser = subparsers.add_parser("spec", help="Manage specifications")
    spec_subparsers = spec_parser.add_subparsers(
        dest="spec_command", help="Specification commands"
    )

    # Spec create
    spec_create_parser = spec_subparsers.add_parser(
        "create", help="Create a specification"
    )
    spec_create_parser.add_argument("name", type=str, help="Specification name")
    spec_create_parser.add_argument("--description", type=str, help="Description")

    # Spec add-feature
    spec_feature_parser = spec_subparsers.add_parser(
        "add-feature", help="Add a feature to a specification"
    )
    spec_feature_parser.add_argument("spec", type=str, help="Specification name")
    spec_feature_parser.add_argument("feature", type=str, help="Feature name")
    spec_feature_parser.add_argument(
        "--type",
        dest="data_type",
        type=str,
        choices=SPEC_DATA_TYPES,
        default="text",
        help="Data type (default: text)",
    )
    spec_feature_parser.add_argument("--unit", type=str, help='Unit (e.g. "MP", "kg")')
    spec_feature_parser.add_argument(
        "--required", action="store_true", help="Mark the feature as required"
    )
    spec_feature_parser.add_argument("--min", type=float, help="Minimum value")
    spec_feature_parser.add_argument("--max", type=float, help="Maximum value")

    # Spec list
    spec_list_parser = spec_subparsers.add_parser("list", help="List specifications")
    spec_list_parser.add_argument(
        "--limit", type=int, default=100, help="Maximum rows (default: 100)"
    )

    # Spec show
    spec_show_parser = spec_subparsers.add_parser(
        "show", help="Show a specification and its features"
    )
    spec_show_parser.add_argument("name", type=str, help="Specification name")

    # Spec delete
    spec_delete_parser = spec_subparsers.add_parser(
        "delete", help="Delete a specification"
    )
    spec_delete_parser.add_argument("name", type=str, help="Specification name")

    # Check command (for cron)
    check_parser = subparsers.add_parser(
        "check",
        help="Check price alerts and watchlist targets, and notify (for cron)",
    )
    check_parser.add_argument(
        "--notify",
        type=str,
        help="Comma-separated notification channels "
        "(stdout, ntfy, webhook, desktop, email); "
        "defaults to $BUYLOG_NOTIFY, else stdout",
    )
    check_parser.add_argument(
        "--refresh-fx",
        action="store_true",
        help="Refresh forex rates before checking",
    )
    check_parser.add_argument(
        "--refresh-scrapes",
        action="store_true",
        help="Re-scrape tracked product URLs before checking",
    )
    check_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print nothing when nothing triggered (for cron)",
    )

    # Forex refresh command
    fx_parser = subparsers.add_parser("fx", help="Manage forex rates")
    fx_subparsers = fx_parser.add_subparsers(dest="fx_command", help="Forex commands")
    fx_refresh_parser = fx_subparsers.add_parser(
        "refresh", help="Fetch current rates from a public API"
    )
    fx_refresh_parser.add_argument(
        "codes",
        type=str,
        nargs="*",
        help="Currency codes to refresh (default: every currency your vendors use)",
    )
    fx_refresh_parser.add_argument(
        "--date", type=str, help="Record the rates under this date (YYYY-MM-DD)"
    )
    fx_list_parser = fx_subparsers.add_parser("list", help="List forex rates")
    fx_list_parser.add_argument(
        "--limit", type=int, default=100, help="Maximum rows (default: 100)"
    )

    # Spend analytics command
    spend_parser = subparsers.add_parser(
        "spend", help="Report on money committed via purchase orders"
    )
    spend_parser.add_argument(
        "report",
        nargs="?",
        default="summary",
        choices=["summary", "month", "vendor", "category", "variance"],
        help="Which report to show (default: summary)",
    )
    spend_parser.add_argument(
        "--from", dest="start", type=str, help="Start date (YYYY-MM-DD)"
    )
    spend_parser.add_argument(
        "--to", dest="end", type=str, help="End date (YYYY-MM-DD)"
    )

    # Import command
    import_parser = subparsers.add_parser("import", help="Import data from files")
    import_parser.add_argument(
        "file", type=str, help="Path to Excel (.xlsx) or CSV file"
    )
    import_parser.add_argument(
        "-t",
        "--table",
        type=str,
        choices=[
            "vendors",
            "products",
            "quotes",
            "specifications",
            "specs",
            "purchase_orders",
            "pos",
        ],
        help="Table to import (required for Excel, auto-detected for CSV)",
    )

    # Export command
    export_parser = subparsers.add_parser("export", help="Export data to Excel/CSV")
    export_parser.add_argument(
        "-t",
        "--table",
        type=str,
        choices=[
            "vendors",
            "products",
            "quotes",
            "specifications",
            "specs",
            "purchase_orders",
            "pos",
            "brands",
        ],
        help="Table to export (default: all tables)",
    )
    export_parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Output file path (default: buylog-db.xlsx or buylog-<table>.xlsx)",
    )
    export_parser.add_argument(
        "--format",
        type=str,
        choices=["xlsx", "csv"],
        default="xlsx",
        help="Output format (default: xlsx)",
    )

    # Backup command
    backup_parser = subparsers.add_parser("backup", help="Backup the database")
    backup_parser.add_argument(
        "--output", type=str, help="Backup file path (default: auto-generated)"
    )

    # Restore command
    restore_parser = subparsers.add_parser(
        "restore", help="Restore database from backup"
    )
    restore_parser.add_argument("backup_file", type=str, help="Path to backup file")
    restore_parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Don't backup current database before restore",
    )

    # Backups command (list backups)
    subparsers.add_parser("backups", help="List available backups")

    # Duplicates command
    duplicates_parser = subparsers.add_parser(
        "duplicates", help="Find and manage duplicate entities"
    )
    duplicates_subparsers = duplicates_parser.add_subparsers(
        dest="duplicates_command", help="Duplicates commands"
    )

    # Find duplicate vendors
    dup_vendors_parser = duplicates_subparsers.add_parser(
        "vendors", help="Find similar vendors"
    )
    dup_vendors_parser.add_argument(
        "--threshold", type=float, default=0.8, help="Similarity threshold (0-1)"
    )

    # Find duplicate products
    dup_products_parser = duplicates_subparsers.add_parser(
        "products", help="Find similar products"
    )
    dup_products_parser.add_argument(
        "--threshold", type=float, default=0.8, help="Similarity threshold (0-1)"
    )

    # Merge vendors
    merge_vendors_parser = duplicates_subparsers.add_parser(
        "merge-vendors", help="Merge vendors"
    )
    merge_vendors_parser.add_argument("keep_id", type=int, help="ID of vendor to keep")
    merge_vendors_parser.add_argument(
        "merge_ids", type=int, nargs="+", help="IDs of vendors to merge"
    )

    # Merge products
    merge_products_parser = duplicates_subparsers.add_parser(
        "merge-products", help="Merge products"
    )
    merge_products_parser.add_argument(
        "keep_id", type=int, help="ID of product to keep"
    )
    merge_products_parser.add_argument(
        "merge_ids", type=int, nargs="+", help="IDs of products to merge"
    )

    # Clipboard command
    clipboard_parser = subparsers.add_parser("clipboard", help="Copy data to clipboard")
    clipboard_subparsers = clipboard_parser.add_subparsers(
        dest="clipboard_command", help="Clipboard commands"
    )

    # Clipboard copy quote
    clip_quote_parser = clipboard_subparsers.add_parser(
        "quote", help="Copy quote to clipboard"
    )
    clip_quote_parser.add_argument("quote_id", type=int, help="Quote ID")

    # Clipboard copy product
    clip_product_parser = clipboard_subparsers.add_parser(
        "product", help="Copy product to clipboard"
    )
    clip_product_parser.add_argument("product_name", type=str, help="Product name")

    # Clipboard copy vendor
    clip_vendor_parser = clipboard_subparsers.add_parser(
        "vendor", help="Copy vendor to clipboard"
    )
    clip_vendor_parser.add_argument("vendor_name", type=str, help="Vendor name")

    # Vendor URL command
    vendor_url_parser = subparsers.add_parser("vendor-url", help="Manage vendor URLs")
    vendor_url_subparsers = vendor_url_parser.add_subparsers(
        dest="vendor_url_command", help="Vendor URL commands"
    )

    # Vendor URL set
    url_set_parser = vendor_url_subparsers.add_parser("set", help="Set vendor URL")
    url_set_parser.add_argument("vendor", type=str, help="Vendor name")
    url_set_parser.add_argument("url", type=str, help="URL to set")

    # Vendor URL open
    url_open_parser = vendor_url_subparsers.add_parser(
        "open", help="Open vendor URL in browser"
    )
    url_open_parser.add_argument("vendor", type=str, help="Vendor name")

    # Vendor URL clear
    url_clear_parser = vendor_url_subparsers.add_parser(
        "clear", help="Clear vendor URL"
    )
    url_clear_parser.add_argument("vendor", type=str, help="Vendor name")

    # Receipt command
    receipt_parser = subparsers.add_parser("receipt", help="Manage receipt attachments")
    receipt_subparsers = receipt_parser.add_subparsers(
        dest="receipt_command", help="Receipt commands"
    )

    # Receipt attach
    receipt_attach_parser = receipt_subparsers.add_parser(
        "attach", help="Attach receipt to quote"
    )
    receipt_attach_parser.add_argument("quote_id", type=int, help="Quote ID")
    receipt_attach_parser.add_argument(
        "file_path", type=str, help="Path to receipt file"
    )

    # Receipt open
    receipt_open_parser = receipt_subparsers.add_parser(
        "open", help="Open receipt file"
    )
    receipt_open_parser.add_argument("quote_id", type=int, help="Quote ID")

    # Receipt detach
    receipt_detach_parser = receipt_subparsers.add_parser(
        "detach", help="Remove receipt from quote"
    )
    receipt_detach_parser.add_argument("quote_id", type=int, help="Quote ID")

    # Receipt list
    receipt_subparsers.add_parser("list", help="List quotes with receipts")

    # Scrape command
    scrape_parser = subparsers.add_parser(
        "scrape", help="Scrape product prices from URLs"
    )
    scrape_subparsers = scrape_parser.add_subparsers(
        dest="scrape_command", help="Scrape commands"
    )

    # Scrape URL
    scrape_url_parser = scrape_subparsers.add_parser(
        "url", help="Scrape price from URL"
    )
    scrape_url_parser.add_argument("url", type=str, help="URL to scrape")

    # Scrape and create quote
    scrape_quote_parser = scrape_subparsers.add_parser(
        "quote", help="Create quote from scraped URL"
    )
    scrape_quote_parser.add_argument("url", type=str, help="URL to scrape")
    scrape_quote_parser.add_argument(
        "--vendor", type=str, required=True, help="Vendor name"
    )
    scrape_quote_parser.add_argument(
        "--product", type=str, required=True, help="Product name"
    )
    scrape_quote_parser.add_argument(
        "--brand", type=str, help="Brand name (for new products)"
    )

    # Scrape refresh
    scrape_refresh_parser = scrape_subparsers.add_parser(
        "refresh", help="Re-scrape tracked product URLs and record price changes"
    )
    scrape_refresh_parser.add_argument(
        "--quote-id",
        type=int,
        action="append",
        dest="quote_ids",
        help="Only refresh this quote (repeatable)",
    )
    scrape_refresh_parser.add_argument(
        "--list",
        action="store_true",
        dest="list_tracked",
        help="List tracked quotes without re-scraping",
    )

    # Report command
    report_parser = subparsers.add_parser("report", help="Generate HTML reports")
    report_subparsers = report_parser.add_subparsers(
        dest="report_command", help="Report commands"
    )

    # Report price-comparison
    report_price_parser = report_subparsers.add_parser(
        "price-comparison", help="Compare prices across vendors"
    )
    report_price_parser.add_argument(
        "--filter", type=str, help="Filter products by name"
    )
    report_price_parser.add_argument("--output", type=str, help="Output HTML file path")

    # Report purchase-summary
    report_purchase_parser = report_subparsers.add_parser(
        "purchase-summary", help="Summary of quotes by status"
    )
    report_purchase_parser.add_argument(
        "--output", type=str, help="Output HTML file path"
    )

    # Report vendor-analysis
    report_vendor_parser = report_subparsers.add_parser(
        "vendor-analysis", help="Vendor statistics and analysis"
    )
    report_vendor_parser.add_argument(
        "--output", type=str, help="Output HTML file path"
    )

    # Report spend-analysis
    report_spend_parser = report_subparsers.add_parser(
        "spend-analysis", help="What was actually spent, from purchase orders"
    )
    report_spend_parser.add_argument("--output", type=str, help="Output HTML file path")
    report_spend_parser.add_argument(
        "--from", dest="start", type=str, help="Start date (YYYY-MM-DD)"
    )
    report_spend_parser.add_argument(
        "--to", dest="end", type=str, help="End date (YYYY-MM-DD)"
    )

    # Migrate command
    migrate_parser = subparsers.add_parser(
        "migrate", help="Migrate database schema to match models"
    )
    migrate_parser.add_argument(
        "--dry-run", action="store_true", help="Show SQL without executing"
    )

    # Template command - generate import templates
    template_parser = subparsers.add_parser(
        "template", help="Generate import templates (Excel/YAML/JSON)"
    )
    template_parser.add_argument(
        "-t",
        "--table",
        type=str,
        required=True,
        choices=[
            "vendors",
            "products",
            "quotes",
            "specifications",
            "specs",
            "purchase_orders",
            "pos",
        ],
        help="Table type for template",
    )
    template_parser.add_argument(
        "-f",
        "--format",
        type=str,
        choices=["xlsx", "yaml", "yml", "json"],
        default="yaml",
        help="Output format (default: yaml)",
    )
    template_parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Output file path (default: <table>-template.<format>)",
    )

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    session = Session()

    try:
        if args.command == "add":
            # A quote needs both a vendor and a product; without them the
            # price used to be accepted and then silently discarded.
            if args.quote is not None and not (args.vendor and args.product):
                missing = []
                if not args.vendor:
                    missing.append("--vendor")
                if not args.product:
                    missing.append("--product")
                print(f"Error: --quote also requires {' and '.join(missing)}")
                return 1

            if args.brand and not args.product and not args.vendor:
                # Add brand only
                if add_brand(session, args.brand) is None:
                    return 1

            elif args.brand and args.product and not args.vendor:
                # Add product under brand
                if (
                    add_product(
                        session,
                        args.brand,
                        args.product,
                        getattr(args, "category", None),
                    )
                    is None
                ):
                    return 1

            elif args.vendor and not args.product:
                # Add vendor only
                if (
                    add_vendor(
                        session,
                        args.vendor,
                        args.currency,
                        args.discount_code,
                        args.discount,
                    )
                    is None
                ):
                    return 1

            elif args.vendor and args.product and args.quote is not None:
                # Add quote
                if (
                    add_quote(
                        session,
                        args.vendor,
                        args.product,
                        args.quote,
                        args.brand,
                        getattr(args, "shipping", None),
                        getattr(args, "tax_rate", None),
                        assume_yes=args.yes,
                    )
                    is None
                ):
                    return 1

            else:
                print("Invalid combination of arguments for 'add' command")
                add_parser.print_help()
                return 1

        elif args.command == "add-fx":
            if (
                add_fx(
                    session,
                    args.code,
                    args.usd_per_unit,
                    args.date if hasattr(args, "date") else None,
                )
                is None
            ):
                return 1

        elif args.command == "list":
            list_entities(session, args.type, args.filter, args.sort_by)

        elif args.command == "delete":
            return delete_entity(
                session, args.type, args.name, args.id, assume_yes=args.yes
            )

        elif args.command == "update":
            return update_entity(session, args.type, args.name, args.new_name)

        elif args.command == "search":
            search_entities(session, args.query)

        elif args.command == "tui":
            session.close()
            from .tui import main as tui_main

            tui_main()
            return 0

        elif args.command == "seed":
            seed_database(session)

        elif args.command == "alert":
            from .services import PriceAlertService

            if args.alert_command == "add":
                try:
                    alert = PriceAlertService.create(
                        session, args.product, args.threshold
                    )
                    print(
                        f"Created alert #{alert.id} for '{args.product}' at ${args.threshold:.2f}"
                    )
                except NotFoundError as e:
                    print(f"Error: {e}")
                    return 1
                except ValidationError as e:
                    print(f"Error: {e}")
                    return 1

            elif args.alert_command == "list":
                if args.triggered:
                    alerts = PriceAlertService.get_triggered(session)
                    if not alerts:
                        print("No triggered alerts")
                        return 0
                    headers = ["ID", "Product", "Threshold", "Triggered At"]
                    data = [
                        [
                            a.id,
                            a.product.name,
                            f"${a.threshold_value:.2f}",
                            str(a.triggered_at),
                        ]
                        for a in alerts
                    ]
                else:
                    alerts = PriceAlertService.get_all(session)
                    if not alerts:
                        print("No alerts")
                        return 0
                    headers = ["ID", "Product", "Threshold", "Status", "Triggered At"]
                    data = [
                        [
                            a.id,
                            a.product.name,
                            f"${a.threshold_value:.2f}",
                            "Active" if a.active else "Inactive",
                            str(a.triggered_at) if a.triggered_at else "-",
                        ]
                        for a in alerts
                    ]
                print(tabulate(data, headers=headers, tablefmt="grid"))

            elif args.alert_command == "deactivate":
                try:
                    alert = PriceAlertService.deactivate(session, args.id)
                    print(f"Deactivated alert #{alert.id}")
                except NotFoundError as e:
                    print(f"Error: {e}")
                    return 1

            else:
                print("Usage: buylog alert [add|list|deactivate]")
                return 1

        elif args.command == "history":
            from .services import QuoteHistoryService, QuoteService

            if args.quote_id:
                history = QuoteHistoryService.get_history(session, args.quote_id)
                if not history:
                    print(f"No history for quote #{args.quote_id}")
                    return 0
                headers = ["Date", "Old Price", "New Price", "Type"]
                data = [
                    [
                        str(h.changed_at),
                        f"${h.old_value:.2f}" if h.old_value else "-",
                        f"${h.new_value:.2f}",
                        h.change_type,
                    ]
                    for h in history
                ]
                print(f"Price history for Quote #{args.quote_id}:")
                print(tabulate(data, headers=headers, tablefmt="grid"))

            elif args.product:
                product = Product.by_name(session, args.product)
                if not product:
                    print(f"Product '{args.product}' not found")
                    return 1
                history = QuoteHistoryService.get_product_history(session, product.id)
                if not history:
                    print(f"No history for product '{args.product}'")
                    return 0
                headers = ["Date", "Quote ID", "Old Price", "New Price", "Type"]
                data = [
                    [
                        str(h.changed_at),
                        h.quote_id,
                        f"${h.old_value:.2f}" if h.old_value else "-",
                        f"${h.new_value:.2f}",
                        h.change_type,
                    ]
                    for h in history
                ]
                print(f"Price history for '{args.product}':")
                print(tabulate(data, headers=headers, tablefmt="grid"))

            else:
                print("Usage: buylog history [--product NAME | --quote-id ID]")
                return 1

        elif args.command == "compare":
            from .services import ComparisonService

            def print_comparison(comparison: dict) -> None:
                """Print a single product comparison."""
                product = comparison["product"]
                quotes = comparison["quotes"]

                print(f"\n{product.brand.name} {product.name}")
                if product.category:
                    print(f"  Category: {product.category}")
                print("-" * 60)

                if not quotes:
                    print("  No quotes available")
                    return

                headers = ["Vendor", "Price", "Total", "Discount", "Shipping"]
                data = [
                    [
                        q.vendor.name,
                        f"${q.value:.2f}",
                        f"${q.total_cost:.2f}",
                        f"{q.discount}%" if q.discount else "-",
                        f"${q.shipping_cost:.2f}" if q.shipping_cost else "-",
                    ]
                    for q in quotes
                ]
                print(tabulate(data, headers=headers, tablefmt="simple"))

                print(f"\n  Best Price:  ${comparison['best_price']:.2f}")
                print(f"  Worst Price: ${comparison['worst_price']:.2f}")
                print(f"  Avg Price:   ${comparison['avg_price']:.2f}")
                print(
                    f"  Savings:     ${comparison['savings']:.2f} ({comparison['num_vendors']} vendors)"
                )

            try:
                if args.product:
                    result = ComparisonService.compare_product(session, args.product)
                    print_comparison(result)

                elif args.search:
                    result = ComparisonService.compare_by_search(session, args.search)
                    print(
                        f"Found {result['total_products']} products matching '{args.search}':"
                    )
                    for comp in result["products"]:
                        print_comparison(comp)

                elif args.category:
                    result = ComparisonService.compare_by_category(
                        session, args.category
                    )
                    print(
                        f"Found {result['total_products']} products in category '{args.category}':"
                    )
                    for comp in result["products"]:
                        print_comparison(comp)

                elif args.brand:
                    result = ComparisonService.compare_by_brand(session, args.brand)
                    print(
                        f"Found {result['total_products']} products from '{args.brand}':"
                    )
                    for comp in result["products"]:
                        print_comparison(comp)

                else:
                    print(
                        "Usage: buylog compare [--product NAME | --search TERM | --category CAT | --brand NAME]"
                    )
                    return 1

            except NotFoundError as e:
                print(f"Error: {e}")
                return 1

        elif args.command == "category":
            from .services import ComparisonService

            if args.category_command == "set":
                try:
                    product = ComparisonService.set_product_category(
                        session, args.product, args.category
                    )
                    print(
                        f"Set category '{args.category}' for product '{args.product}'"
                    )
                except NotFoundError as e:
                    print(f"Error: {e}")
                    return 1

            elif args.category_command == "list":
                counts = ComparisonService.classification_counts(session)
                if counts:
                    print("Product Categories:")
                    for cat in sorted(counts):
                        print(f"  - {cat} ({counts[cat]} products)")
                else:
                    print(
                        "No categories defined. Use 'buylog category set <product> <category>' to add one."
                    )

            else:
                print("Usage: buylog category [set|list]")
                return 1

        elif args.command == "purchase-list":
            from .services import (
                PurchaseListService,
            )

            if args.list_command == "create":
                try:
                    plist = PurchaseListService.create(
                        session, args.name, getattr(args, "description", None)
                    )
                    print(f"Created purchase list '{plist.name}'")
                except DuplicateError as e:
                    print(f"Error: {e}")
                    return 1
                except ValidationError as e:
                    print(f"Error: {e}")
                    return 1

            elif args.list_command == "add":
                try:
                    plist = PurchaseListService.add_quote(
                        session, args.list_name, args.quote_id
                    )
                    print(f"Added quote #{args.quote_id} to list '{args.list_name}'")
                except (NotFoundError, DuplicateError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.list_command == "remove":
                try:
                    plist = PurchaseListService.remove_quote(
                        session, args.list_name, args.quote_id
                    )
                    print(
                        f"Removed quote #{args.quote_id} from list '{args.list_name}'"
                    )
                except NotFoundError as e:
                    print(f"Error: {e}")
                    return 1

            elif args.list_command == "show":
                found_list = PurchaseListService.get_by_name(session, args.name)
                if not found_list:
                    print(f"Purchase list '{args.name}' not found")
                    return 1
                plist = found_list
                print(f"Purchase List: {plist.name}")
                if plist.description:
                    print(f"Description: {plist.description}")
                print(f"Created: {plist.created_at}")
                print(f"Total Value: ${plist.total_value:.2f}")
                print("-" * 60)
                if plist.quotes:
                    headers = ["ID", "Vendor", "Product", "Price", "Status"]
                    data = [
                        [
                            q.id,
                            q.vendor.name,
                            f"{q.product.brand.name} {q.product.name}",
                            f"${q.total_cost:.2f}",
                            q.status or "-",
                        ]
                        for q in plist.quotes
                    ]
                    print(tabulate(data, headers=headers, tablefmt="grid"))
                else:
                    print("  No quotes in list")

            elif args.list_command == "delete":
                try:
                    PurchaseListService.delete(session, args.name)
                    print(f"Deleted purchase list '{args.name}'")
                except NotFoundError as e:
                    print(f"Error: {e}")
                    return 1

            elif args.list_command == "all":
                plists = PurchaseListService.get_all(session)
                if not plists:
                    print("No purchase lists")
                    return 0
                headers = ["Name", "Description", "Quotes", "Total"]
                data = [
                    [
                        p.name,
                        p.description or "-",
                        len(p.quotes),
                        f"${p.total_value:.2f}",
                    ]
                    for p in plists
                ]
                print(tabulate(data, headers=headers, tablefmt="grid"))

            elif args.list_command == "might-buy":
                quotes = PurchaseListService.might_buy(session)
                if not quotes:
                    print("Nothing you might buy")
                    return 0
                headers = ["ID", "Vendor", "Product", "Price", "Lists"]
                data = [
                    [
                        quote.id,
                        quote.vendor.name,
                        f"{quote.product.brand.name} {quote.product.name}",
                        quote.display_price,
                        ", ".join(item.name for item in quote.purchase_lists),
                    ]
                    for quote in quotes
                ]
                print(tabulate(data, headers=headers, tablefmt="grid"))

            else:
                print(
                    "Usage: buylog purchase-list "
                    "[create|add|remove|show|delete|all|might-buy]"
                )
                return 1

        elif args.command == "status":
            from .services import QuoteService

            if args.status_command == "set":
                try:
                    quote = QuoteService.set_status(session, args.quote_id, args.status)
                    print(f"Set status '{args.status}' for quote #{args.quote_id}")
                except (NotFoundError, ValidationError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.status_command == "list":
                try:
                    quotes = QuoteService.get_by_status(session, args.status)
                    if not quotes:
                        print(f"No quotes with status '{args.status}'")
                        return 0
                    headers = ["ID", "Vendor", "Product", "Price", "Status"]
                    data = [
                        [
                            q.id,
                            q.vendor.name,
                            f"{q.product.brand.name} {q.product.name}",
                            f"${q.total_cost:.2f}",
                            q.status,
                        ]
                        for q in quotes
                    ]
                    print(f"Quotes with status '{args.status}':")
                    print(tabulate(data, headers=headers, tablefmt="grid"))
                except ValidationError as e:
                    print(f"Error: {e}")
                    return 1

            else:
                print("Usage: buylog status [set|list]")
                return 1

        elif args.command == "note":
            from .services import NoteService

            if args.note_command == "add":
                try:
                    note = NoteService.create(
                        session, args.entity_type, args.entity_id, args.content
                    )
                    print(
                        f"Added note #{note.id} to {args.entity_type}:{args.entity_id}"
                    )
                except ValidationError as e:
                    print(f"Error: {e}")
                    return 1

            elif args.note_command == "list":
                notes = NoteService.get_for_entity(
                    session, args.entity_type, args.entity_id
                )
                if not notes:
                    print(f"No notes for {args.entity_type}:{args.entity_id}")
                    return 0
                print(f"Notes for {args.entity_type}:{args.entity_id}:")
                for note in notes:
                    print(f"\n#{note.id} ({note.created_at}):")
                    print(f"  {note.content}")

            elif args.note_command == "delete":
                try:
                    NoteService.delete(session, args.note_id)
                    print(f"Deleted note #{args.note_id}")
                except NotFoundError as e:
                    print(f"Error: {e}")
                    return 1

            else:
                print("Usage: buylog note [add|list|delete]")
                return 1

        elif args.command == "tag":
            from .services import TagService

            if args.tag_command == "add":
                try:
                    TagService.add_to_entity(
                        session, args.tag_name, args.entity_type, args.entity_id
                    )
                    print(
                        f"Added tag '{args.tag_name}' to {args.entity_type}:{args.entity_id}"
                    )
                except DuplicateError as e:
                    print(f"Error: {e}")
                    return 1

            elif args.tag_command == "remove":
                try:
                    TagService.remove_from_entity(
                        session, args.tag_name, args.entity_type, args.entity_id
                    )
                    print(
                        f"Removed tag '{args.tag_name}' from {args.entity_type}:{args.entity_id}"
                    )
                except NotFoundError as e:
                    print(f"Error: {e}")
                    return 1

            elif args.tag_command == "list":
                if args.entity_type and args.entity_id:
                    # List tags for specific entity
                    tags = TagService.get_for_entity(
                        session, args.entity_type, args.entity_id
                    )
                    if not tags:
                        print(f"No tags for {args.entity_type}:{args.entity_id}")
                        return 0
                    print(f"Tags for {args.entity_type}:{args.entity_id}:")
                    for tag in tags:
                        color_info = f" ({tag.color})" if tag.color else ""
                        print(f"  - {tag.name}{color_info}")
                else:
                    # List all tags
                    tags = TagService.get_all(session)
                    if not tags:
                        print("No tags")
                        return 0
                    headers = ["Name", "Color"]
                    data = [[t.name, t.color or "-"] for t in tags]
                    print(tabulate(data, headers=headers, tablefmt="grid"))

            elif args.tag_command == "search":
                try:
                    entity_tags = TagService.get_entities_by_tag(
                        session, args.tag_name, getattr(args, "entity_type", None)
                    )
                    if not entity_tags:
                        print(f"No entities with tag '{args.tag_name}'")
                        return 0
                    print(f"Entities with tag '{args.tag_name}':")
                    for et in entity_tags:
                        print(f"  - {et.entity_type}:{et.entity_id}")
                except NotFoundError as e:
                    print(f"Error: {e}")
                    return 1

            else:
                print("Usage: buylog tag [add|remove|list|search]")
                return 1

        elif args.command == "watchlist":
            from .services import PurchaseListService, WatchlistService

            if args.watchlist_command == "add":
                try:
                    target_price = getattr(args, "target_price", None)
                    if target_price is None:
                        quotes = PurchaseListService.consider_product(
                            session, args.product
                        )
                        print(
                            f"Added {len(quotes)} quote(s) for '{args.product}' "
                            "to the Considering list"
                        )
                    else:
                        watchlist = WatchlistService.create(
                            session,
                            args.product,
                            target_price,
                            getattr(args, "notes", None),
                            getattr(args, "currency", None),
                        )
                        print(
                            f"Added '{args.product}' to watchlist (ID: {watchlist.id})"
                        )
                except (NotFoundError, DuplicateError, ValidationError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.watchlist_command == "list":
                if getattr(args, "all", False):
                    items = WatchlistService.get_all(session)
                else:
                    items = WatchlistService.get_active(session)
                if not items:
                    print("Watchlist is empty")
                    return 0
                headers = ["ID", "Product", "Brand", "Target Price", "Notes", "Status"]
                data = [
                    [
                        w.id,
                        w.product.name,
                        w.product.brand.name,
                        w.target_label,
                        (w.notes[:30] + "...")
                        if w.notes and len(w.notes) > 30
                        else (w.notes or "-"),
                        "Active" if w.active else "Inactive",
                    ]
                    for w in items
                ]
                print(tabulate(data, headers=headers, tablefmt="grid"))

            elif args.watchlist_command == "update":
                try:
                    watchlist = WatchlistService.update(
                        session,
                        args.id,
                        getattr(args, "target_price", None),
                        getattr(args, "notes", None),
                        getattr(args, "currency", None),
                    )
                    print(f"Updated watchlist entry #{args.id}")
                except (NotFoundError, ValidationError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.watchlist_command == "remove":
                try:
                    WatchlistService.delete(session, args.id)
                    print(f"Removed watchlist entry #{args.id}")
                except NotFoundError as e:
                    print(f"Error: {e}")
                    return 1

            else:
                print("Usage: buylog watchlist [add|list|update|remove]")
                return 1

        elif args.command == "po":
            from .services import PurchaseOrderService

            if args.po_command == "create":
                try:
                    order_date = _parse_date(getattr(args, "order_date", None))
                    expected = _parse_date(getattr(args, "expected_delivery", None))
                except ValueError as e:
                    print(f"Error: {e}")
                    return 1

                try:
                    if args.from_quote:
                        po = PurchaseOrderService.create_from_quote(
                            session,
                            po_number=args.po_number,
                            quote_id=args.from_quote,
                            quantity=args.quantity,
                            order_date=order_date,
                            expected_delivery=expected,
                            shipping_cost=args.shipping,
                            tax=args.tax,
                            notes=args.notes,
                        )
                    else:
                        missing = [
                            flag
                            for flag, value in (
                                ("--vendor", args.vendor),
                                ("--product", args.product),
                                ("--price", args.price),
                            )
                            if value is None
                        ]
                        if missing:
                            print(
                                "Error: "
                                + ", ".join(missing)
                                + " required (or use --from-quote)"
                            )
                            return 1
                        po = PurchaseOrderService.create(
                            session,
                            po_number=args.po_number,
                            vendor_name=args.vendor,
                            product_name=args.product,
                            unit_price=args.price,
                            quantity=args.quantity,
                            order_date=order_date,
                            expected_delivery=expected,
                            shipping_cost=args.shipping,
                            tax=args.tax,
                            invoice_number=args.invoice,
                            notes=args.notes,
                        )
                    print(
                        f"Created purchase order {po.po_number}: "
                        f"{po.quantity} x {po.product.name} = ${po.grand_total:.2f}"
                    )
                except (ValidationError, DuplicateError, NotFoundError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.po_command == "list":
                try:
                    orders = PurchaseOrderService.get_all(
                        session,
                        status=args.status,
                        vendor_name=args.vendor,
                        limit=args.limit,
                    )
                except ValidationError as e:
                    print(f"Error: {e}")
                    return 1
                if not orders:
                    print("No purchase orders found")
                    return 0
                headers = [
                    "PO Number",
                    "Vendor",
                    "Product",
                    "Qty",
                    "Unit Price",
                    "Total",
                    "Status",
                ]
                data = [
                    [
                        po.po_number,
                        po.vendor.name,
                        po.product.name,
                        po.quantity,
                        f"{po.unit_price:.2f} {po.currency}",
                        f"{po.grand_total:.2f} {po.currency}",
                        po.status,
                    ]
                    for po in orders
                ]
                print(tabulate(data, headers=headers, tablefmt="grid"))

            elif args.po_command == "show":
                found_po = PurchaseOrderService.get_by_po_number(
                    session, args.po_number
                )
                if not found_po:
                    print(f"Purchase order '{args.po_number}' not found")
                    return 1
                po = found_po
                rows = [
                    ("PO Number", po.po_number),
                    ("Vendor", po.vendor.name),
                    ("Product", po.product.name),
                    ("Quantity", po.quantity),
                    ("Unit Price", f"{po.unit_price:.2f} {po.currency}"),
                    ("Subtotal", f"{po.total_amount:.2f} {po.currency}"),
                    (
                        "Shipping",
                        f"{po.shipping_cost:.2f}" if po.shipping_cost else "-",
                    ),
                    ("Tax", f"{po.tax:.2f}" if po.tax else "-"),
                    ("Grand Total", f"{po.grand_total:.2f} {po.currency}"),
                    ("Status", po.status),
                    ("Order Date", po.order_date or "-"),
                    ("Expected Delivery", po.expected_delivery or "-"),
                    ("Actual Delivery", po.actual_delivery or "-"),
                    ("Invoice", po.invoice_number or "-"),
                    ("Notes", po.notes or "-"),
                ]
                print(tabulate(rows, headers=["Field", "Value"], tablefmt="grid"))

            elif args.po_command == "update":
                try:
                    delivery = _parse_date(getattr(args, "delivery_date", None))
                except ValueError as e:
                    print(f"Error: {e}")
                    return 1
                try:
                    po = PurchaseOrderService.update_status(
                        session, args.po_number, args.status, actual_delivery=delivery
                    )
                    print(f"Updated {po.po_number} status to '{po.status}'")
                except (NotFoundError, ValidationError) as e:
                    print(f"Error: {e}")
                    return 1

            else:
                print("Usage: buylog po [create|list|show|update]")
                return 1

        elif args.command == "spec":
            from .services import SpecificationService

            if args.spec_command == "create":
                try:
                    spec = SpecificationService.create(
                        session, args.name, args.description
                    )
                    print(f"Created specification: {spec.name}")
                except (ValidationError, DuplicateError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.spec_command == "add-feature":
                try:
                    feature = SpecificationService.add_feature(
                        session,
                        spec_name=args.spec,
                        feature_name=args.feature,
                        data_type=args.data_type,
                        unit=args.unit,
                        is_required=args.required,
                        min_value=args.min,
                        max_value=args.max,
                    )
                    print(f"Added feature '{feature.name}' to '{args.spec}'")
                except (ValidationError, NotFoundError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.spec_command == "list":
                specs = SpecificationService.get_all(session, limit=args.limit)
                if not specs:
                    print("No specifications found")
                    return 0
                headers = ["Name", "Description", "Features"]
                data = [[s.name, s.description or "-", len(s.features)] for s in specs]
                print(tabulate(data, headers=headers, tablefmt="grid"))

            elif args.spec_command == "show":
                found_spec = SpecificationService.get_by_name(session, args.name)
                if not found_spec:
                    print(f"Specification '{args.name}' not found")
                    return 1
                spec = found_spec
                print(f"{spec.name}: {spec.description or '(no description)'}")
                if not spec.features:
                    print("No features defined")
                    return 0
                headers = ["Feature", "Type", "Unit", "Required", "Min", "Max"]
                data = [
                    [
                        f.name,
                        f.data_type,
                        f.unit or "-",
                        "yes" if f.is_required else "no",
                        f.min_value if f.min_value is not None else "-",
                        f.max_value if f.max_value is not None else "-",
                    ]
                    for f in spec.features
                ]
                print(tabulate(data, headers=headers, tablefmt="grid"))

            elif args.spec_command == "delete":
                try:
                    SpecificationService.delete(session, args.name)
                    print(f"Deleted specification: {args.name}")
                except NotFoundError as e:
                    print(f"Error: {e}")
                    return 1

            else:
                print("Usage: buylog spec [create|add-feature|list|show|delete]")
                return 1

        elif args.command == "check":
            from .notify import Notification, NotifyError
            from .notify import send as send_notification
            from .services import MonitorService

            exit_code = 0

            if args.refresh_fx:
                from .services import ForexRefreshService

                try:
                    fx_stats = ForexRefreshService.refresh(session)
                    print(
                        f"Forex: updated {len(fx_stats['updated'])}, "
                        f"skipped {len(fx_stats['skipped'])}"
                    )
                except ServiceError as e:
                    print(f"Forex refresh failed: {e}")
                    exit_code = 1

            if args.refresh_scrapes:
                from .services import ScrapeRefreshService

                scrape_stats = ScrapeRefreshService.refresh(session)
                print(
                    f"Scrapes: checked {scrape_stats['checked']}, "
                    f"changed {len(scrape_stats['changed'])}, "
                    f"errors {len(scrape_stats['errors'])}"
                )
                for message in scrape_stats["errors"]:
                    print(f"  {message}")
                if scrape_stats["errors"]:
                    exit_code = 1

            findings = MonitorService.check(session)
            messages = findings["messages"]

            if not messages:
                if not args.quiet:
                    print("Nothing triggered.")
                return exit_code

            for message in messages:
                print(message)

            notification = Notification(
                title=f"buylog: {len(messages)} price alert(s)",
                body="\n".join(messages),
                priority="high",
                data={
                    "alerts": [
                        {
                            "product": item["quote"].product.name,
                            "vendor": item["quote"].vendor.name,
                            "price": item["quote"].value,
                            "threshold": item["alert"].threshold_value,
                            "threshold_currency": item["alert"].threshold_currency,
                        }
                        for item in findings["alerts"]
                    ],
                    "watchlist": [
                        {
                            "product": item["quote"].product.name,
                            "vendor": item["quote"].vendor.name,
                            "price": item["quote"].value,
                            "target": item["watchlist"].target_price,
                            "target_currency": item["watchlist"].target_currency
                            or "USD",
                        }
                        for item in findings["watchlist"]
                    ],
                },
            )

            channels = args.notify
            # stdout would repeat what was printed above, so drop it unless the
            # user asked for it explicitly.
            if channels is None and os.getenv("BUYLOG_NOTIFY") is None:
                return exit_code

            try:
                delivered, failures = send_notification(notification, channels)
            except NotifyError as e:
                print(f"Notification failed: {e}")
                return 1

            if delivered:
                print(f"Notified via: {', '.join(delivered)}")
            for failure in failures:
                print(f"Notification failed via {failure}")
            if failures:
                # A partially-delivered notification must not look like success,
                # or a cron job silently loses those alerts forever.
                return 1

            return exit_code

        elif args.command == "fx":
            from .services import ForexRefreshService

            if args.fx_command == "refresh":
                try:
                    date = _parse_date(getattr(args, "date", None))
                except ValueError as e:
                    print(f"Error: {e}")
                    return 1
                try:
                    stats = ForexRefreshService.refresh(
                        session, codes=args.codes or None, date=date
                    )
                except ServiceError as e:
                    print(f"Error: {e}")
                    return 1

                if not stats["updated"] and not stats["skipped"]:
                    print(
                        "Nothing to refresh: no currency codes given and no "
                        "non-USD vendor currencies on file."
                    )
                    return 0

                for code in stats["updated"]:
                    print(f"  {code}: {stats['rates'][code]:.6f} USD per unit")
                print(
                    f"Updated {len(stats['updated'])} rate(s)"
                    + (
                        f", skipped {len(stats['skipped'])} "
                        f"({', '.join(stats['skipped'])})"
                        if stats["skipped"]
                        else ""
                    )
                )

            elif args.fx_command == "list":
                list_entities(session, "forex", limit=args.limit)

            else:
                print("Usage: buylog fx [refresh|list]")
                return 1

        elif args.command == "spend":
            from .services import SpendAnalyticsService

            try:
                start = _parse_date(getattr(args, "start", None))
                end = _parse_date(getattr(args, "end", None))
            except ValueError as e:
                print(f"Error: {e}")
                return 1

            def money(value: float) -> str:
                return f"${value:,.2f}"

            if args.report in ("summary", "month"):
                spend_rows = SpendAnalyticsService.spend_by_month(session, start, end)
                if spend_rows:
                    print("Spend by month")
                    print(
                        tabulate(
                            [
                                [r["period"], r["orders"], money(r["total"])]
                                for r in spend_rows
                            ],
                            headers=["Month", "Orders", "Total (USD)"],
                            tablefmt="grid",
                        )
                    )
                elif args.report == "month":
                    print("No purchase orders found")

            if args.report in ("summary", "vendor"):
                spend_rows = SpendAnalyticsService.spend_by_vendor(session, start, end)
                if spend_rows:
                    print("\nSpend by vendor")
                    print(
                        tabulate(
                            [
                                [r["vendor"], r["orders"], money(r["total"])]
                                for r in spend_rows
                            ],
                            headers=["Vendor", "Orders", "Total (USD)"],
                            tablefmt="grid",
                        )
                    )
                elif args.report == "vendor":
                    print("No purchase orders found")

            if args.report in ("summary", "category"):
                spend_rows = SpendAnalyticsService.spend_by_category(
                    session, start, end
                )
                if spend_rows:
                    print("\nSpend by category")
                    print(
                        tabulate(
                            [
                                [r["category"], r["orders"], money(r["total"])]
                                for r in spend_rows
                            ],
                            headers=["Category", "Orders", "Total (USD)"],
                            tablefmt="grid",
                        )
                    )
                elif args.report == "category":
                    print("No purchase orders found")

            if args.report in ("summary", "variance"):
                spend_rows = SpendAnalyticsService.price_variance(session, start, end)
                if spend_rows:
                    print("\nPaid vs. best recorded price")
                    print(
                        tabulate(
                            [
                                [
                                    r["po_number"],
                                    r["product"],
                                    r["vendor"],
                                    money(r["unit_paid"]),
                                    f"{money(r['best_price'])} ({r['best_vendor']})",
                                    f"{r['delta']:+,.2f} ({r['delta_pct']:+.1f}%)",
                                ]
                                for r in spend_rows
                            ],
                            headers=[
                                "PO",
                                "Product",
                                "Vendor",
                                "Unit Paid",
                                "Best Recorded",
                                "Delta",
                            ],
                            tablefmt="grid",
                        )
                    )
                elif args.report == "variance":
                    print("No purchase orders with comparable quotes found")

            if args.report == "summary":
                totals = SpendAnalyticsService.summary(session, start, end)
                if not totals["orders"]:
                    print("No purchase orders found")
                    return 0
                print(
                    f"\n{totals['orders']} order(s), "
                    f"{money(totals['total'])} total, "
                    f"{money(totals['average'])} average"
                )

        elif args.command == "import":
            from .excel import IMPORTERS

            filepath = args.file
            table = getattr(args, "table", None)

            try:
                if filepath.endswith(".xlsx"):
                    # Excel import
                    if not table:
                        print("Error: --table is required for Excel import")
                        print("Usage: buylog import -t <table> file.xlsx")
                        return 1

                    importer = IMPORTERS.get(table)
                    if not importer:
                        print(f"Error: Unknown table: {table}")
                        return 1

                    success, errors = importer(session, filepath)
                    print(f"Import complete: {success} rows imported")
                    if errors:
                        print(f"Errors ({len(errors)}):")
                        for err in errors[:10]:
                            print(f"  - {err}")
                        if len(errors) > 10:
                            print(f"  ... and {len(errors) - 10} more errors")

                elif filepath.endswith(".csv") or filepath.endswith(".json"):
                    # Legacy CSV/JSON import (quotes only)
                    from .services import ImportService

                    if filepath.endswith(".json"):
                        stats = ImportService.import_quotes_json(
                            session, filepath, True
                        )
                    else:
                        stats = ImportService.import_quotes_csv(session, filepath, True)

                    print("Import complete:")
                    print(f"  Imported: {stats['imported']}")
                    print(f"  Skipped: {stats['skipped']}")
                    if stats.get("created_vendors"):
                        print(
                            f"  Created vendors: {', '.join(stats['created_vendors'])}"
                        )
                    if stats.get("created_products"):
                        print(
                            f"  Created products: {', '.join(stats['created_products'])}"
                        )
                    if stats.get("errors"):
                        print("  Errors:")
                        for err in stats["errors"][:10]:
                            print(f"    - {err}")
                else:
                    print("Error: Unsupported file format. Use .xlsx, .csv, or .json")

            except FileNotFoundError:
                print(f"Error: File not found: {filepath}")
                return 1
            except Exception as e:
                print(f"Error: {e}")
                return 1

        elif args.command == "export":
            from .excel import (
                export_all,
                export_brands,
                export_products,
                export_vendors,
                export_quotes,
                export_specifications,
                export_purchase_orders,
            )

            table = getattr(args, "table", None)
            output = getattr(args, "output", None)
            fmt = getattr(args, "format", "xlsx")

            # Determine output filename
            if output:
                filepath = output
            elif table:
                filepath = f"buylog-{table}.{fmt}"
            else:
                filepath = f"buylog-db.{fmt}"

            # Ensure correct extension
            if fmt == "xlsx" and not filepath.endswith(".xlsx"):
                filepath = filepath.rsplit(".", 1)[0] + ".xlsx"

            # Export based on table selection
            try:
                if fmt == "xlsx":
                    if table is None:
                        # Export all tables
                        counts = export_all(session, filepath)
                        print(f"Exported to: {filepath}")
                        for name, count in counts.items():
                            print(f"  {name}: {count} rows")
                    else:
                        # Export specific table
                        exporters = {
                            "brands": export_brands,
                            "products": export_products,
                            "vendors": export_vendors,
                            "quotes": export_quotes,
                            "specifications": export_specifications,
                            "specs": export_specifications,
                            "purchase_orders": export_purchase_orders,
                            "pos": export_purchase_orders,
                        }
                        exporter = exporters.get(table)
                        if exporter:
                            count = exporter(session, filepath)
                            print(f"Exported {count} rows to: {filepath}")
                        else:
                            print(f"Error: Unknown table: {table}")
                            return 1
                else:
                    # CSV export (legacy support)
                    from .services import ExportService

                    if table == "quotes":
                        ExportService.export_quotes_csv(session, filepath)
                    elif table == "products":
                        ExportService.export_products_csv(session, filepath)
                    elif table == "vendors":
                        ExportService.export_vendors_csv(session, filepath)
                    else:
                        print("CSV export only supports: quotes, products, vendors")
                        return 1
                    print(f"Exported to: {filepath}")
            except Exception as e:
                print(f"Error: {e}")
                return 1

        elif args.command == "backup":
            from .services import BackupService
            from .config import Config

            db_path = Config.get_db_path()
            output = getattr(args, "output", None)

            try:
                backup_path = BackupService.backup(db_path, output)
                print(f"Backup created: {backup_path}")
            except Exception as e:
                print(f"Error: {e}")
                return 1

        elif args.command == "restore":
            from .services import BackupService
            from .config import Config

            backup_file = args.backup_file
            db_path = Config.get_db_path()
            create_backup = not getattr(args, "no_backup", False)

            try:
                session.close()  # Close current session before restore
                BackupService.restore(backup_file, db_path, create_backup)
                print(f"Database restored from: {backup_file}")
                if create_backup:
                    print("(Previous database was backed up)")
            except NotFoundError as e:
                print(f"Error: {e}")
                return 1
            except Exception as e:
                print(f"Error: {e}")
            return 0  # Exit after restore

        elif args.command == "backups":
            from .services import BackupService
            from .config import Config

            db_path = Config.get_db_path()
            backups = BackupService.list_backups(db_path)

            if not backups:
                print("No backups found")
            else:
                print(f"Available backups ({len(backups)}):")
                for b in backups:
                    size_kb = b["size"] / 1024
                    print(f"  {b['name']} ({size_kb:.1f} KB) - {b['modified']}")

        elif args.command == "duplicates":
            from .services import DeduplicationService

            if args.duplicates_command == "vendors":
                threshold = getattr(args, "threshold", 0.8)
                groups = DeduplicationService.find_similar_vendors(session, threshold)

                if not groups:
                    print("No similar vendors found")
                else:
                    print(f"Found {len(groups)} groups of similar vendors:")
                    for i, group in enumerate(groups, 1):
                        print(f"\nGroup {i}:")
                        for v in group:
                            print(f"  [{v.id}] {v.name} ({v.currency})")
                    print(
                        "\nTo merge: buylog duplicates merge-vendors <keep_id> <merge_id1> <merge_id2> ..."
                    )

            elif args.duplicates_command == "products":
                threshold = getattr(args, "threshold", 0.8)
                product_groups = DeduplicationService.find_similar_products(
                    session, threshold
                )

                if not product_groups:
                    print("No similar products found")
                else:
                    print(f"Found {len(product_groups)} groups of similar products:")
                    for i, product_group in enumerate(product_groups, 1):
                        print(f"\nGroup {i}:")
                        for prod in product_group:
                            print(f"  [{prod.id}] {prod.name} ({prod.brand.name})")
                    print(
                        "\nTo merge: buylog duplicates merge-products <keep_id> <merge_id1> <merge_id2> ..."
                    )

            elif args.duplicates_command == "merge-vendors":
                try:
                    vendor = DeduplicationService.merge_vendors(
                        session, args.keep_id, args.merge_ids
                    )
                    print(f"Merged vendors into: [{vendor.id}] {vendor.name}")
                except NotFoundError as e:
                    print(f"Error: {e}")
                    return 1

            elif args.duplicates_command == "merge-products":
                try:
                    product = DeduplicationService.merge_products(
                        session, args.keep_id, args.merge_ids
                    )
                    print(f"Merged products into: [{product.id}] {product.name}")
                except NotFoundError as e:
                    print(f"Error: {e}")
                    return 1

            else:
                print(
                    "Usage: buylog duplicates [vendors|products|merge-vendors|merge-products]"
                )
                return 1

        elif args.command == "clipboard":
            from .services import ClipboardService

            if args.clipboard_command == "quote":
                try:
                    text = ClipboardService.copy_quote(session, args.quote_id)
                    print(f"Copied quote #{args.quote_id} to clipboard:")
                    print(text)
                except (NotFoundError, ServiceError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.clipboard_command == "product":
                try:
                    text = ClipboardService.copy_product(session, args.product_name)
                    print(f"Copied product '{args.product_name}' to clipboard:")
                    print(text)
                except (NotFoundError, ServiceError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.clipboard_command == "vendor":
                try:
                    text = ClipboardService.copy_vendor(session, args.vendor_name)
                    print(f"Copied vendor '{args.vendor_name}' to clipboard:")
                    print(text)
                except (NotFoundError, ServiceError) as e:
                    print(f"Error: {e}")
                    return 1

            else:
                print("Usage: buylog clipboard [quote|product|vendor]")
                return 1

        elif args.command == "vendor-url":
            from .services import (
                VendorURLService,
            )

            if args.vendor_url_command == "set":
                try:
                    vendor = VendorURLService.set_url(session, args.vendor, args.url)
                    print(f"Set URL for '{args.vendor}': {args.url}")
                except (NotFoundError, ValidationError, ServiceError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.vendor_url_command == "open":
                try:
                    url = VendorURLService.open_url(session, args.vendor)
                    print(f"Opening URL: {url}")
                except (NotFoundError, ValidationError, ServiceError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.vendor_url_command == "clear":
                try:
                    vendor = VendorURLService.clear_url(session, args.vendor)
                    print(f"Cleared URL for '{args.vendor}'")
                except (NotFoundError, ServiceError) as e:
                    print(f"Error: {e}")
                    return 1

            else:
                print("Usage: buylog vendor-url [set|open|clear]")
                return 1

        elif args.command == "receipt":
            from .services import (
                ReceiptService,
            )

            if args.receipt_command == "attach":
                try:
                    quote = ReceiptService.attach(
                        session, args.quote_id, args.file_path
                    )
                    print(
                        f"Attached receipt to quote #{args.quote_id}: {args.file_path}"
                    )
                except (NotFoundError, ServiceError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.receipt_command == "open":
                try:
                    path = ReceiptService.open(session, args.quote_id)
                    print(f"Opening receipt: {path}")
                except (NotFoundError, ValidationError, ServiceError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.receipt_command == "detach":
                try:
                    quote = ReceiptService.detach(session, args.quote_id)
                    print(f"Detached receipt from quote #{args.quote_id}")
                except (NotFoundError, ServiceError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.receipt_command == "list":
                quotes = ReceiptService.get_quotes_with_receipts(session)
                if not quotes:
                    print("No quotes with receipts attached")
                else:
                    headers = ["ID", "Vendor", "Product", "Price", "Receipt"]
                    data = [
                        [
                            q.id,
                            q.vendor.name,
                            f"{q.product.brand.name} {q.product.name}",
                            f"${q.total_cost:.2f}",
                            Path(q.receipt_path).name if q.receipt_path else "-",
                        ]
                        for q in quotes
                    ]
                    print(f"Quotes with receipts ({len(quotes)}):")
                    print(tabulate(data, headers=headers, tablefmt="grid"))

            else:
                print("Usage: buylog receipt [attach|open|detach|list]")
                return 1

        elif args.command == "scrape":
            from .services import (
                ScraperService,
            )

            if args.scrape_command == "url":
                try:
                    result = ScraperService.scrape_price(args.url)
                    print(f"Scraped data from: {args.url}")
                    if result.get("title"):
                        print(f"  Title: {result['title'][:80]}...")
                    if result.get("price"):
                        print(f"  Price: {result['currency']} {result['price']:.2f}")
                        print(f"  Raw: {result['raw_price']}")
                    else:
                        print("  Could not extract price from page")
                except (ValidationError, ServiceError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.scrape_command == "quote":
                try:
                    quote = ScraperService.create_quote_from_scrape(
                        session,
                        args.url,
                        args.vendor,
                        args.product,
                        getattr(args, "brand", None),
                    )
                    print(f"Created quote #{quote.id} from scraped URL:")
                    print(f"  Product: {quote.product.name}")
                    print(f"  Vendor: {quote.vendor.name}")
                    print(f"  Price: ${quote.value:.2f}")
                except (NotFoundError, ValidationError, ServiceError) as e:
                    print(f"Error: {e}")
                    return 1

            elif args.scrape_command == "refresh":
                from .services import ScrapeRefreshService

                if args.list_tracked:
                    tracked = ScrapeRefreshService.tracked_quotes(session)
                    if not tracked:
                        print("No quotes carry a source URL to re-scrape.")
                        return 0
                    print(
                        tabulate(
                            [
                                [
                                    q.id,
                                    q.product.name,
                                    q.vendor.name,
                                    f"${q.value:.2f}",
                                    q.source_url,
                                ]
                                for q in tracked
                            ],
                            headers=["ID", "Product", "Vendor", "Price", "URL"],
                            tablefmt="grid",
                        )
                    )
                    return 0

                stats = ScrapeRefreshService.refresh(
                    session, quote_ids=getattr(args, "quote_ids", None)
                )
                if not stats["checked"]:
                    print("No quotes carry a source URL to re-scrape.")
                    return 0

                for change in stats["changed"]:
                    direction = (
                        "down" if change["new_value"] < change["old_value"] else "up"
                    )
                    print(
                        f"  #{change['quote_id']} {change['product']} at "
                        f"{change['vendor']}: ${change['old_value']:.2f} -> "
                        f"${change['new_value']:.2f} ({direction})"
                    )
                for message in stats["errors"]:
                    print(f"  Error: {message}")

                print(
                    f"Checked {stats['checked']}, changed {len(stats['changed'])}, "
                    f"unchanged {stats['unchanged']}, errors {len(stats['errors'])}"
                )
                if stats["errors"]:
                    return 1

            else:
                print("Usage: buylog scrape [url|quote|refresh]")
                return 1

        elif args.command == "report":
            from .services import ReportService

            output_file = getattr(args, "output", None)

            if args.report_command == "price-comparison":
                try:
                    filter_term = getattr(args, "filter", None)
                    report = ReportService.generate_report(
                        session,
                        "price-comparison",
                        output_file,
                        filter_term=filter_term,
                    )
                    if output_file:
                        print(f"Generated price comparison report: {report}")
                    else:
                        print(report)
                except ValidationError as e:
                    print(f"Error: {e}")
                    return 1

            elif args.report_command == "purchase-summary":
                try:
                    report = ReportService.generate_report(
                        session,
                        "purchase-summary",
                        output_file,
                    )
                    if output_file:
                        print(f"Generated purchase summary report: {report}")
                    else:
                        print(report)
                except ValidationError as e:
                    print(f"Error: {e}")
                    return 1

            elif args.report_command == "vendor-analysis":
                try:
                    report = ReportService.generate_report(
                        session,
                        "vendor-analysis",
                        output_file,
                    )
                    if output_file:
                        print(f"Generated vendor analysis report: {report}")
                    else:
                        print(report)
                except ValidationError as e:
                    print(f"Error: {e}")
                    return 1

            elif args.report_command == "spend-analysis":
                try:
                    start = _parse_date(getattr(args, "start", None))
                    end = _parse_date(getattr(args, "end", None))
                except ValueError as e:
                    print(f"Error: {e}")
                    return 1
                try:
                    report = ReportService.generate_report(
                        session,
                        "spend-analysis",
                        output_file,
                        start=start,
                        end=end,
                    )
                    if output_file:
                        print(f"Generated spend analysis report: {report}")
                    else:
                        print(report)
                except ValidationError as e:
                    print(f"Error: {e}")
                    return 1

            else:
                print(
                    "Usage: buylog report [price-comparison|purchase-summary"
                    "|vendor-analysis|spend-analysis]"
                )
                return 1

        elif args.command == "migrate":
            from .migrate import MigrationError, run_migrations, check_migrations

            if args.dry_run:
                migrations = check_migrations(engine)
                if not migrations:
                    print("Database schema is up to date.")
                else:
                    print(f"Would apply {len(migrations)} migration(s):\n")
                    for sql in migrations:
                        print(f"  {sql};\n")
            else:
                try:
                    migrations = run_migrations(engine)
                except MigrationError as e:
                    print(f"Migration failed: {e}")
                    return 1
                if not migrations:
                    print("Database schema is up to date.")
                else:
                    print(f"Applied {len(migrations)} migration(s):")
                    for sql in migrations:
                        # Truncate long SQL for display
                        display = sql[:70] + "..." if len(sql) > 70 else sql
                        print(f"  {display}")
                    print("\nMigration complete.")

        elif args.command == "template":
            # Normalize table name
            table = args.table
            if table == "specs":
                table = "specifications"
            elif table == "pos":
                table = "purchase_orders"

            fmt = args.format
            if fmt == "yml":
                fmt = "yaml"

            # Generate default filename if not provided
            if args.output:
                filepath = args.output
            else:
                filepath = f"{table}-template.{args.format}"

            if fmt == "xlsx":
                # Excel template generation
                from .excel import TEMPLATE_GENERATORS

                generator = TEMPLATE_GENERATORS.get(table)
                if not generator:
                    print(f"Error: Unknown table type: {table}")
                    return 1

                if not filepath.endswith(".xlsx"):
                    filepath += ".xlsx"

                try:
                    # Some templates need session for data validation dropdowns
                    if table in ("products", "quotes", "purchase_orders"):
                        generator(filepath, session)
                    else:
                        generator(filepath)
                    print(f"Generated template: {filepath}")
                except Exception as e:
                    print(f"Error: {e}")
                    return 1

            else:
                # YAML/JSON template generation
                from .templates import (
                    vendor_template_str,
                    specification_template_str,
                    purchase_order_template_str,
                )

                # Map table names to template generators
                yaml_generators = {
                    "vendors": lambda: vendor_template_str(fmt=fmt),
                    "specifications": lambda: specification_template_str(fmt=fmt),
                    "purchase_orders": lambda: purchase_order_template_str(fmt=fmt),
                }

                if table not in yaml_generators:
                    print(
                        f"Error: {fmt.upper()} templates only available for: vendors, specifications, purchase_orders"
                    )
                    return 1
                    print(f"Use -f xlsx for {table} templates")
                    return 1

                try:
                    output = yaml_generators[table]()
                    Path(filepath).write_text(output)
                    print(f"Generated template: {filepath}")
                except Exception as e:
                    print(f"Error: {e}")
                    return 1

    except IntegrityError as e:
        session.rollback()
        logger.error(f"Integrity error: {e}")
        if "UNIQUE constraint failed" in str(e):
            print("Error: Duplicate entry. Please use a unique name.")
        else:
            print(f"Error: Database constraint violation: {e}")
        return 1
    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Database error: {e}")
        print(f"Error: Database error occurred: {e}")
        return 1
    except ServiceError as e:
        session.rollback()
        logger.error(f"Service error: {e}")
        print(f"Error: {e}")
        return 1
    except ValueError as e:
        session.rollback()
        logger.warning(f"Invalid input: {e}")
        print(f"Error: Invalid input - {e}")
        return 1
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        print("\nOperation cancelled by user.")
        session.rollback()
        return 130
    except Exception as e:
        session.rollback()
        logger.exception(f"Unexpected error: {e}")
        print(f"Error: Unexpected error occurred: {e}")
        return 1
    finally:
        session.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
