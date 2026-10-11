#!/usr/bin/env python3
"""
CLI interface for buylog.

Commands are ``buylog <noun> <verb> [target] [--options]``. Every noun uses
the same verbs where they apply: ``add``, ``list``, ``show``, ``edit``, ``rm``.
Each verb is one handler, ``handler(session, args) -> exit code``, bound to
its parser with ``set_defaults(func=...)``. ``ServiceError`` and ``ValueError``
reach ``main``, which prints them and returns 1.
"""

import argparse
import datetime
import os
import sys
from pathlib import Path
from typing import Callable, Optional
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
    QUOTE_STATUSES,
    SPEC_DATA_TYPES,
    format_money,
    get_base_currency,
)
from .config import Config
from .services import (
    ForexService,
    ServiceError,
    ValidationError,
    DuplicateError,
)

# Setup logging
logger = Config.setup_logging()

# Database setup
engine = Config.get_engine()
init_db(engine)
Session = Config.get_session_maker()

ENTITY_KINDS = ["product", "vendor", "quote", "brand"]
TABLES = [
    "vendors",
    "products",
    "quotes",
    "specifications",
    "specs",
    "purchase_orders",
    "pos",
]


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


def _grid(rows: list, headers: list) -> None:
    print(tabulate(rows, headers=headers, tablefmt="grid"))


def _product_label(quote: Quote) -> str:
    return f"{quote.product.brand.name} {quote.product.name}"


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
    from .services import ProductService, NotFoundError

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


def add_vendor(
    session, name, currency=None, discount_code=None, discount=0.0, url=None
):
    """Add a new vendor"""
    from .services import VendorService

    try:
        vendor = VendorService.create(
            session,
            name,
            currency=currency,
            discount_code=discount_code,
            discount=discount,
            url=url,
        )
        print(f"Added vendor: {name} (currency: {vendor.currency})")
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
    from .services import QuoteService, NotFoundError

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


def add_fx(session, code, base_per_unit, date=None):
    """Add a new forex rate for a specific date (defaults to today)"""
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
        fx = ForexService.create(session, code, base_per_unit, date=date)
        base = get_base_currency(session)
        print(f"Added forex rate: {code} = {base_per_unit} {base} per unit on {date}")
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
    limit: int = 100,
) -> None:
    """
    Print a table of brands, products, vendors or forex rates.

    Args:
        session: Active database session
        entity_type: "brands", "products", "vendors" or "forex"
        filter_by: Optional case-insensitive partial name match
        limit: Maximum number of rows
    """
    from .services import BrandService, ProductService, VendorService

    if entity_type == "brands":
        brands = BrandService.get_all(session, filter_by=filter_by, limit=limit)
        _grid(
            [[b.id, b.name, ", ".join([p.name for p in b.products])] for b in brands],
            ["ID", "Name", "Products"],
        )

    elif entity_type == "products":
        products = ProductService.get_all(session, filter_by=filter_by, limit=limit)
        _grid(
            [[p.id, p.name, p.brand.name, p.category or "-"] for p in products],
            ["ID", "Name", "Brand", "Category"],
        )

    elif entity_type == "vendors":
        vendors = VendorService.get_all(session, filter_by=filter_by, limit=limit)
        _grid(
            [[v.id, v.name, v.currency, len(v.quotes), v.url or "-"] for v in vendors],
            ["ID", "Name", "Currency", "Quotes", "URL"],
        )

    elif entity_type == "forex":
        rates = (
            session.execute(
                select(Forex).order_by(Forex.date.desc(), Forex.code).limit(limit)
            )
            .scalars()
            .all()
        )
        _grid(
            [[f.id, f.date, f.code, f"{f.base_per_unit:.6f}"] for f in rates],
            ["ID", "Date", "Code", f"{get_base_currency(session)} per unit"],
        )


def delete_entity(session, entity_type, key, assume_yes=False) -> int:
    """
    Delete a brand, product or vendor by name, or a quote by ID.

    Deletes go through the service layer, so dependent rows (a quote's price
    history, a product's price targets) are removed with the entity instead of
    being left orphaned.

    Returns:
        A process exit code.
    """
    from .services import BrandService, ProductService, QuoteService, VendorService

    lookup: dict[str, Callable[..., object]] = {
        "brand": Brand.by_name,
        "product": Product.by_name,
        "vendor": Vendor.by_name,
        "quote": QuoteService.get_by_id,
    }
    entity = lookup[entity_type](session, key)
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
        f"Are you sure you want to delete {entity_type} '{key}'{dependents}?",
        assume_yes,
    ):
        print("Cancelled.")
        return 0

    deleters: dict[str, Callable[..., None]] = {
        "brand": BrandService.delete,
        "product": ProductService.delete,
        "vendor": VendorService.delete,
        "quote": QuoteService.delete,
    }
    deleters[entity_type](session, key)
    print(f"Deleted {entity_type}: '{key}'")
    return 0


def seed_database(session):
    """Populate database with sample data"""
    # The sample rates below are USD per unit.
    if get_base_currency(session) != "USD":
        raise ValueError("Sample data is priced in USD; seed needs a USD database")

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
            fx = Forex(code=code, base_per_unit=rate, date=today)
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

        value, original_value, original_currency = ForexService.to_base(
            session, price, vendor.currency
        )

        quote = Quote(
            vendor=vendor,
            product=product,
            value=value,
            original_value=original_value,
            original_currency=original_currency,
        )
        session.add(quote)
        quote_count += 1
    session.commit()
    print(f"Added {quote_count} quotes")
    print("Database seeded successfully!")


# ---------------------------------------------------------------------------
# brand


def brand_add(session, args):
    return 0 if add_brand(session, args.name) else 1


def brand_list(session, args):
    list_entities(session, "brands", args.search)


def brand_edit(session, args):
    from .services import BrandService

    BrandService.update(session, args.name, args.new_name)
    print(f"Updated brand '{args.name}' to '{args.new_name}'.")


def brand_rm(session, args):
    return delete_entity(session, "brand", args.name, args.yes)


# ---------------------------------------------------------------------------
# product


def product_add(session, args):
    return 0 if add_product(session, args.brand, args.name, args.category) else 1


def product_list(session, args):
    list_entities(session, "products", args.search)


def product_edit(session, args):
    from .services import ProductService

    if args.new_name is None and args.brand is None and args.category is None:
        print("Nothing to update: pass --name, --brand or --category.")
        return 1
    ProductService.update(
        session,
        args.name,
        new_name=args.new_name,
        brand_name=args.brand,
        category=args.category,
    )
    print(f"Updated product '{args.name}'.")


def product_rm(session, args):
    return delete_entity(session, "product", args.name, args.yes)


def product_categories(session, args):
    from .services import ComparisonService

    counts = ComparisonService.classification_counts(session)
    if not counts:
        print(
            "No categories defined. "
            "Use 'buylog product edit <product> --category <category>' to add one."
        )
        return
    print("Product Categories:")
    for cat in sorted(counts):
        print(f"  - {cat} ({counts[cat]} products)")


def product_history(session, args):
    from .services import QuoteHistoryService

    product = Product.by_name(session, args.name)
    if not product:
        print(f"Product '{args.name}' not found")
        return 1
    history = QuoteHistoryService.get_product_history(session, product.id)
    if not history:
        print(f"No history for product '{args.name}'")
        return
    print(f"Price history for '{args.name}':")
    _grid(
        [
            [
                str(h.changed_at),
                h.quote_id,
                f"{_money(session, h.old_value)}" if h.old_value else "-",
                f"{_money(session, h.new_value)}",
                h.change_type,
            ]
            for h in history
        ],
        ["Date", "Quote ID", "Old Price", "New Price", "Type"],
    )


def product_copy(session, args):
    from .services import ClipboardService

    text = ClipboardService.copy_product(session, args.name)
    print(f"Copied product '{args.name}' to clipboard:")
    print(text)


def product_dupes(session, args):
    from .services import DeduplicationService

    groups = DeduplicationService.find_similar_products(session, args.threshold)
    if not groups:
        print("No similar products found")
        return
    print(f"Found {len(groups)} groups of similar products:")
    for i, group in enumerate(groups, 1):
        print(f"\nGroup {i}:")
        for prod in group:
            print(f"  [{prod.id}] {prod.name} ({prod.brand.name})")
    print("\nTo merge: buylog product merge <keep_id> <merge_id> ...")


def product_merge(session, args):
    from .services import DeduplicationService

    product = DeduplicationService.merge_products(session, args.keep_id, args.ids)
    print(f"Merged products into: [{product.id}] {product.name}")


# ---------------------------------------------------------------------------
# vendor


def vendor_add(session, args):
    vendor = add_vendor(
        session, args.name, args.currency, args.discount_code, args.discount, args.url
    )
    return 0 if vendor else 1


def vendor_list(session, args):
    list_entities(session, "vendors", args.search)


def vendor_edit(session, args):
    from .services import VendorService, VendorURLService

    fields = {
        "new_name": args.new_name,
        "currency": args.currency,
        "discount_code": args.discount_code,
        "discount": args.discount,
    }
    if all(v is None for v in fields.values()) and args.url is None and not args.no_url:
        print(
            "Nothing to update: pass --name, --currency, --discount-code, "
            "--discount, --url or --no-url."
        )
        return 1
    # URL first: a rename below changes the name it is looked up by.
    if args.url is not None:
        VendorURLService.set_url(session, args.name, args.url)
    elif args.no_url:
        VendorURLService.clear_url(session, args.name)
    if any(v is not None for v in fields.values()):
        VendorService.update(session, args.name, **fields)
    print(f"Updated vendor '{args.name}'.")


def vendor_rm(session, args):
    return delete_entity(session, "vendor", args.name, args.yes)


def vendor_open(session, args):
    from .services import VendorURLService

    url = VendorURLService.open_url(session, args.name)
    print(f"Opening URL: {url}")


def vendor_copy(session, args):
    from .services import ClipboardService

    text = ClipboardService.copy_vendor(session, args.name)
    print(f"Copied vendor '{args.name}' to clipboard:")
    print(text)


def vendor_dupes(session, args):
    from .services import DeduplicationService

    groups = DeduplicationService.find_similar_vendors(session, args.threshold)
    if not groups:
        print("No similar vendors found")
        return
    print(f"Found {len(groups)} groups of similar vendors:")
    for i, group in enumerate(groups, 1):
        print(f"\nGroup {i}:")
        for v in group:
            print(f"  [{v.id}] {v.name} ({v.currency})")
    print("\nTo merge: buylog vendor merge <keep_id> <merge_id> ...")


def vendor_merge(session, args):
    from .services import DeduplicationService

    vendor = DeduplicationService.merge_vendors(session, args.keep_id, args.ids)
    print(f"Merged vendors into: [{vendor.id}] {vendor.name}")


# ---------------------------------------------------------------------------
# quote


def quote_add(session, args):
    if args.url is not None:
        from .services import ScraperService

        # The scraped price has no shipping or tax; dropping them silently
        # would record a lower total than the user typed.
        if args.shipping is not None or args.tax_rate is not None:
            print("Error: --shipping and --tax-rate need --price, not --url")
            return 1
        quote = ScraperService.create_quote_from_scrape(
            session, args.url, args.vendor, args.product, args.brand
        )
        print(f"Created quote #{quote.id} from scraped URL:")
        print(f"  Product: {quote.product.name}")
        print(f"  Vendor: {quote.vendor.name}")
        print(f"  Price: {_money(session, quote.value)}")
        return

    quote = add_quote(
        session,
        args.vendor,
        args.product,
        args.price,
        args.brand,
        args.shipping,
        args.tax_rate,
        assume_yes=args.yes,
    )
    return 0 if quote else 1


def quote_list(session, args):
    from .services import QuoteService, ReceiptService, ScrapeRefreshService

    extra: Optional[tuple[str, Callable[[Quote], str]]] = None
    if args.status:
        quotes = QuoteService.get_by_status(session, args.status)
    elif args.receipts:
        quotes = ReceiptService.get_quotes_with_receipts(session)
        extra = ("Receipt", lambda q: Path(q.receipt_path or "").name)
    elif args.tracked:
        quotes = ScrapeRefreshService.tracked_quotes(session)
        extra = ("URL", lambda q: q.source_url or "-")
    else:
        quotes = QuoteService.get_all(session, filter_by=args.search, limit=args.limit)

    if not quotes:
        print("No quotes found")
        return

    headers = [
        "ID",
        "Vendor",
        "Product",
        f"Price ({get_base_currency(session)})",
        "As Entered",
        "Status",
    ]
    rows = []
    for q in quotes:
        row = [
            q.id,
            q.vendor.name,
            _product_label(q),
            f"{_money(session, q.value)}",
            q.source_price,
            q.status or "-",
        ]
        if extra:
            row.append(extra[1](q))
        rows.append(row)
    if extra:
        headers.append(extra[0])
    _grid(rows, headers)


def quote_rm(session, args):
    return delete_entity(session, "quote", args.id, args.yes)


def quote_status(session, args):
    from .services import QuoteService

    QuoteService.set_status(session, args.id, args.status)
    print(f"Set status '{args.status}' for quote #{args.id}")


def quote_history(session, args):
    from .services import QuoteHistoryService

    history = QuoteHistoryService.get_history(session, args.id)
    if not history:
        print(f"No history for quote #{args.id}")
        return
    print(f"Price history for Quote #{args.id}:")
    _grid(
        [
            [
                str(h.changed_at),
                f"{_money(session, h.old_value)}" if h.old_value else "-",
                f"{_money(session, h.new_value)}",
                h.change_type,
            ]
            for h in history
        ],
        ["Date", "Old Price", "New Price", "Type"],
    )


def quote_copy(session, args):
    from .services import ClipboardService

    text = ClipboardService.copy_quote(session, args.id)
    print(f"Copied quote #{args.id} to clipboard:")
    print(text)


def quote_receipt_attach(session, args):
    from .services import ReceiptService

    ReceiptService.attach(session, args.id, args.file)
    print(f"Attached receipt to quote #{args.id}: {args.file}")


def quote_receipt_open(session, args):
    from .services import ReceiptService

    path = ReceiptService.open(session, args.id)
    print(f"Opening receipt: {path}")


def quote_receipt_rm(session, args):
    from .services import ReceiptService

    ReceiptService.detach(session, args.id)
    print(f"Detached receipt from quote #{args.id}")


def quote_scrape(session, args):
    from .services import ScraperService

    result = ScraperService.scrape_price(args.url)
    print(f"Scraped data from: {args.url}")
    if result.get("title"):
        print(f"  Title: {result['title'][:80]}...")
    if result.get("price"):
        print(f"  Price: {result['currency']} {result['price']:.2f}")
        print(f"  Raw: {result['raw_price']}")
    else:
        print("  Could not extract price from page")


def quote_refresh(session, args):
    from .services import ScrapeRefreshService

    stats = ScrapeRefreshService.refresh(session, quote_ids=args.ids or None)
    if not stats["checked"]:
        print("No quotes carry a source URL to re-scrape.")
        return

    for change in stats["changed"]:
        direction = "down" if change["new_value"] < change["old_value"] else "up"
        print(
            f"  #{change['quote_id']} {change['product']} at "
            f"{change['vendor']}: {_money(session, change['old_value'])} -> "
            f"{_money(session, change['new_value'])} ({direction})"
        )
    for message in stats["errors"]:
        print(f"  Error: {message}")

    print(
        f"Checked {stats['checked']}, changed {len(stats['changed'])}, "
        f"unchanged {stats['unchanged']}, errors {len(stats['errors'])}"
    )
    return 1 if stats["errors"] else 0


# ---------------------------------------------------------------------------
# compare


def _print_comparison(session, comparison: dict) -> None:
    product = comparison["product"]
    quotes = comparison["quotes"]

    print(f"\n{product.brand.name} {product.name}")
    if product.category:
        print(f"  Category: {product.category}")
    print("-" * 60)

    if not quotes:
        print("  No quotes available")
        return

    data = [
        [
            q.vendor.name,
            f"{_money(session, q.value)}",
            f"{_money(session, q.total_cost)}",
            f"{q.discount}%" if q.discount else "-",
            f"{_money(session, q.shipping_cost)}" if q.shipping_cost else "-",
        ]
        for q in quotes
    ]
    print(
        tabulate(
            data,
            headers=["Vendor", "Price", "Total", "Discount", "Shipping"],
            tablefmt="simple",
        )
    )

    print(f"\n  Best Price:  {_money(session, comparison['best_price'])}")
    print(f"  Worst Price: {_money(session, comparison['worst_price'])}")
    print(f"  Avg Price:   {_money(session, comparison['avg_price'])}")
    print(
        f"  Savings:     {_money(session, comparison['savings'])} ({comparison['num_vendors']} vendors)"
    )


def compare(session, args):
    from .services import ComparisonService

    if args.product:
        _print_comparison(
            session, ComparisonService.compare_product(session, args.product)
        )
        return

    if args.search:
        result = ComparisonService.compare_by_search(session, args.search)
        heading = f"matching '{args.search}'"
    elif args.category:
        result = ComparisonService.compare_by_category(session, args.category)
        heading = f"in category '{args.category}'"
    elif args.brand:
        result = ComparisonService.compare_by_brand(session, args.brand)
        heading = f"from '{args.brand}'"
    else:
        print(
            "Usage: buylog compare [PRODUCT | --search TERM | --category CAT | --brand NAME]"
        )
        return 1

    print(f"Found {result['total_products']} products {heading}:")
    for comp in result["products"]:
        _print_comparison(session, comp)


# ---------------------------------------------------------------------------
# watch


def watch_add(session, args):
    from .services import PriceTargetService

    target = PriceTargetService.create(
        session, args.product, args.threshold, args.currency
    )
    print(f"Watching '{args.product}' for {target.label} or less (ID: {target.id})")


def watch_list(session, args):
    from .services import PriceTargetService

    if args.triggered:
        targets = PriceTargetService.get_triggered(session)
    else:
        targets = PriceTargetService.get_all(session, active_only=not args.all)
    if not targets:
        print("No price targets")
        return
    _grid(
        [
            [
                t.id,
                t.product.name,
                t.label,
                "Active" if t.active else "Inactive",
                str(t.triggered_at) if t.triggered_at else "-",
            ]
            for t in targets
        ],
        ["ID", "Product", "Target", "Status", "Triggered At"],
    )


def watch_edit(session, args):
    from .services import PriceTargetService

    target = PriceTargetService.update(session, args.id, args.threshold, args.currency)
    print(f"Target #{target.id} is now {target.label}, re-armed")


def watch_off(session, args):
    from .services import PriceTargetService

    PriceTargetService.deactivate(session, args.id)
    print(f"Deactivated target #{args.id}")


def watch_rm(session, args):
    from .services import PriceTargetService

    PriceTargetService.delete(session, args.id)
    print(f"Deleted target #{args.id}")


# ---------------------------------------------------------------------------
# purchase-list


def plist_add(session, args):
    from .services import PurchaseListService

    plist = PurchaseListService.create(session, args.name, args.description)
    print(f"Created purchase list '{plist.name}'")


def plist_list(session, args):
    from .services import PurchaseListService

    plists = PurchaseListService.get_all(session)
    if not plists:
        print("No purchase lists")
        return
    _grid(
        [
            [
                p.name,
                p.description or "-",
                len(p.quotes),
                f"{_money(session, p.total_value)}",
            ]
            for p in plists
        ],
        ["Name", "Description", "Quotes", "Total"],
    )


def plist_show(session, args):
    from .services import PurchaseListService

    plist = PurchaseListService.get_by_name(session, args.name)
    if not plist:
        print(f"Purchase list '{args.name}' not found")
        return 1
    print(f"Purchase List: {plist.name}")
    if plist.description:
        print(f"Description: {plist.description}")
    print(f"Created: {plist.created_at}")
    print(f"Total Value: {_money(session, plist.total_value)}")
    print("-" * 60)
    if not plist.quotes:
        print("  No quotes in list")
        return
    _grid(
        [
            [
                q.id,
                q.vendor.name,
                _product_label(q),
                f"{_money(session, q.total_cost)}",
                q.status or "-",
            ]
            for q in plist.quotes
        ],
        ["ID", "Vendor", "Product", "Price", "Status"],
    )


def plist_rm(session, args):
    from .services import PurchaseListService

    PurchaseListService.delete(session, args.name)
    print(f"Deleted purchase list '{args.name}'")


def plist_add_quote(session, args):
    from .services import PurchaseListService

    PurchaseListService.add_quote(session, args.name, args.quote_id)
    print(f"Added quote #{args.quote_id} to list '{args.name}'")


def plist_rm_quote(session, args):
    from .services import PurchaseListService

    PurchaseListService.remove_quote(session, args.name, args.quote_id)
    print(f"Removed quote #{args.quote_id} from list '{args.name}'")


def plist_consider(session, args):
    from .services import PurchaseListService

    quotes = PurchaseListService.consider_product(session, args.product)
    print(f"Added {len(quotes)} quote(s) for '{args.product}' to the Considering list")


def plist_might_buy(session, args):
    from .services import PurchaseListService

    quotes = PurchaseListService.might_buy(session)
    if not quotes:
        print("Nothing you might buy")
        return
    _grid(
        [
            [
                q.id,
                q.vendor.name,
                _product_label(q),
                q.display_price,
                ", ".join(item.name for item in q.purchase_lists),
            ]
            for q in quotes
        ],
        ["ID", "Vendor", "Product", "Price", "Lists"],
    )


# ---------------------------------------------------------------------------
# po


def po_add(session, args):
    from .services import PurchaseOrderService

    order_date = _parse_date(args.order_date)
    expected = _parse_date(args.expected_delivery)

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
            print("Error: " + ", ".join(missing) + " required (or use --from-quote)")
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
        f"{po.quantity} x {po.product.name} = {po.grand_total:.2f} {po.currency}"
    )


def po_list(session, args):
    from .services import PurchaseOrderService

    orders = PurchaseOrderService.get_all(
        session, status=args.status, vendor_name=args.vendor, limit=args.limit
    )
    if not orders:
        print("No purchase orders found")
        return
    _grid(
        [
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
        ],
        ["PO Number", "Vendor", "Product", "Qty", "Unit Price", "Total", "Status"],
    )


def po_show(session, args):
    from .services import PurchaseOrderService

    po = PurchaseOrderService.get_by_po_number(session, args.po_number)
    if not po:
        print(f"Purchase order '{args.po_number}' not found")
        return 1
    rows = [
        ("PO Number", po.po_number),
        ("Vendor", po.vendor.name),
        ("Product", po.product.name),
        ("Quantity", po.quantity),
        ("Unit Price", f"{po.unit_price:.2f} {po.currency}"),
        ("Subtotal", f"{po.total_amount:.2f} {po.currency}"),
        ("Shipping", f"{po.shipping_cost:.2f}" if po.shipping_cost else "-"),
        ("Tax", f"{po.tax:.2f}" if po.tax else "-"),
        ("Grand Total", f"{po.grand_total:.2f} {po.currency}"),
        ("Status", po.status),
        ("Order Date", po.order_date or "-"),
        ("Expected Delivery", po.expected_delivery or "-"),
        ("Actual Delivery", po.actual_delivery or "-"),
        ("Invoice", po.invoice_number or "-"),
        ("Notes", po.notes or "-"),
    ]
    _grid(rows, ["Field", "Value"])


def po_edit(session, args):
    from .services import PurchaseOrderService

    delivery = _parse_date(args.delivery_date)
    po = PurchaseOrderService.update_status(
        session, args.po_number, args.status, actual_delivery=delivery
    )
    print(f"Updated {po.po_number} status to '{po.status}'")


# ---------------------------------------------------------------------------
# spec


def spec_add(session, args):
    from .services import SpecificationService

    spec = SpecificationService.create(session, args.name, args.description)
    print(f"Created specification: {spec.name}")


def spec_feature(session, args):
    from .services import SpecificationService

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


def spec_list(session, args):
    from .services import SpecificationService

    specs = SpecificationService.get_all(session, limit=args.limit)
    if not specs:
        print("No specifications found")
        return
    _grid(
        [[s.name, s.description or "-", len(s.features)] for s in specs],
        ["Name", "Description", "Features"],
    )


def spec_show(session, args):
    from .services import SpecificationService

    spec = SpecificationService.get_by_name(session, args.name)
    if not spec:
        print(f"Specification '{args.name}' not found")
        return 1
    print(f"{spec.name}: {spec.description or '(no description)'}")
    if not spec.features:
        print("No features defined")
        return
    _grid(
        [
            [
                f.name,
                f.data_type,
                f.unit or "-",
                "yes" if f.is_required else "no",
                f.min_value if f.min_value is not None else "-",
                f.max_value if f.max_value is not None else "-",
            ]
            for f in spec.features
        ],
        ["Feature", "Type", "Unit", "Required", "Min", "Max"],
    )


def spec_rm(session, args):
    from .services import SpecificationService

    SpecificationService.delete(session, args.name)
    print(f"Deleted specification: {args.name}")


# ---------------------------------------------------------------------------
# tag and note


def tag_add(session, args):
    from .services import TagService

    TagService.add_to_entity(session, args.tag, args.kind, args.id)
    print(f"Added tag '{args.tag}' to {args.kind}:{args.id}")


def tag_rm(session, args):
    from .services import TagService

    TagService.remove_from_entity(session, args.tag, args.kind, args.id)
    print(f"Removed tag '{args.tag}' from {args.kind}:{args.id}")


def tag_list(session, args):
    from .services import TagService

    if (args.kind is None) != (args.id is None):
        print("Error: pass both KIND and ID, or neither")
        return 1
    if args.kind:
        tags = TagService.get_for_entity(session, args.kind, args.id)
        if not tags:
            print(f"No tags for {args.kind}:{args.id}")
            return
        print(f"Tags for {args.kind}:{args.id}:")
        for tag in tags:
            color_info = f" ({tag.color})" if tag.color else ""
            print(f"  - {tag.name}{color_info}")
        return

    tags = TagService.get_all(session)
    if not tags:
        print("No tags")
        return
    _grid([[t.name, t.color or "-"] for t in tags], ["Name", "Color"])


def tag_find(session, args):
    from .services import TagService

    entity_tags = TagService.get_entities_by_tag(session, args.tag, args.kind)
    if not entity_tags:
        print(f"No entities with tag '{args.tag}'")
        return
    print(f"Entities with tag '{args.tag}':")
    for et in entity_tags:
        print(f"  - {et.entity_type}:{et.entity_id}")


def note_add(session, args):
    from .services import NoteService

    note = NoteService.create(session, args.kind, args.id, args.text)
    print(f"Added note #{note.id} to {args.kind}:{args.id}")


def note_list(session, args):
    from .services import NoteService

    notes = NoteService.get_for_entity(session, args.kind, args.id)
    if not notes:
        print(f"No notes for {args.kind}:{args.id}")
        return
    print(f"Notes for {args.kind}:{args.id}:")
    for note in notes:
        print(f"\n#{note.id} ({note.created_at}):")
        print(f"  {note.content}")


def note_rm(session, args):
    from .services import NoteService

    NoteService.delete(session, args.note_id)
    print(f"Deleted note #{args.note_id}")


# ---------------------------------------------------------------------------
# fx


def fx_add(session, args):
    return 0 if add_fx(session, args.code, args.rate, args.date) else 1


def fx_list(session, args):
    list_entities(session, "forex", limit=args.limit)


def fx_refresh(session, args):
    from .services import ForexRefreshService

    date = _parse_date(args.date)
    stats = ForexRefreshService.refresh(session, codes=args.codes or None, date=date)

    if not stats["updated"] and not stats["skipped"]:
        print(
            "Nothing to refresh: no currency codes given and no vendor "
            f"currencies other than {get_base_currency(session)} on file."
        )
        return

    for code in stats["updated"]:
        print(
            f"  {code}: {stats['rates'][code]:.6f} "
            f"{get_base_currency(session)} per unit"
        )
    skipped = (
        f", skipped {len(stats['skipped'])} ({', '.join(stats['skipped'])})"
        if stats["skipped"]
        else ""
    )
    print(f"Updated {len(stats['updated'])} rate(s){skipped}")


# ---------------------------------------------------------------------------
# spend and report


def _money(session, value: float) -> str:
    """An amount in the database's base currency, formatted for display."""
    return format_money(value, get_base_currency(session))


def spend(session, args):
    from .services import SpendAnalyticsService

    start = _parse_date(args.start)
    end = _parse_date(args.end)
    view = args.view

    sections = [
        (
            "month",
            "Spend by month",
            SpendAnalyticsService.spend_by_month,
            lambda r: [r["period"], r["orders"], _money(session, r["total"])],
            ["Month", "Orders", f"Total ({get_base_currency(session)})"],
        ),
        (
            "vendor",
            "Spend by vendor",
            SpendAnalyticsService.spend_by_vendor,
            lambda r: [r["vendor"], r["orders"], _money(session, r["total"])],
            ["Vendor", "Orders", f"Total ({get_base_currency(session)})"],
        ),
        (
            "category",
            "Spend by category",
            SpendAnalyticsService.spend_by_category,
            lambda r: [r["category"], r["orders"], _money(session, r["total"])],
            ["Category", "Orders", f"Total ({get_base_currency(session)})"],
        ),
        (
            "variance",
            "Paid vs. best recorded price",
            SpendAnalyticsService.price_variance,
            lambda r: [
                r["po_number"],
                r["product"],
                r["vendor"],
                _money(session, r["unit_paid"]),
                f"{_money(session, r['best_price'])} ({r['best_vendor']})",
                f"{r['delta']:+,.2f} ({r['delta_pct']:+.1f}%)",
            ],
            ["PO", "Product", "Vendor", "Unit Paid", "Best Recorded", "Delta"],
        ),
    ]

    first = True
    for name, title, query, to_row, headers in sections:
        if view not in ("summary", name):
            continue
        rows = query(session, start, end)
        if rows:
            print(("" if first else "\n") + title)
            _grid([to_row(r) for r in rows], headers)
            first = False
        elif view == "variance":
            print("No purchase orders with comparable quotes found")
        elif view == name:
            print("No purchase orders found")

    if view == "summary":
        totals = SpendAnalyticsService.summary(session, start, end)
        if not totals["orders"]:
            print("No purchase orders found")
            return
        print(
            f"\n{totals['orders']} order(s), "
            f"{_money(session, totals['total'])} total, "
            f"{_money(session, totals['average'])} average"
        )


def report(session, args):
    from .services import ReportService

    kwargs = {}
    if args.preset == "price-comparison":
        kwargs["filter_term"] = args.search
    elif args.preset == "spend-analysis":
        kwargs["start"] = _parse_date(args.start)
        kwargs["end"] = _parse_date(args.end)
    result = ReportService.generate_report(session, args.preset, args.output, **kwargs)
    print(f"Generated {args.preset} report: {result}" if args.output else result)


# ---------------------------------------------------------------------------
# check


def check(session, args):
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
        title=f"buylog: {len(messages)} price target(s) met",
        body="\n".join(messages),
        priority="high",
        data={
            "targets": [
                {
                    "product": item["quote"].product.name,
                    "vendor": item["quote"].vendor.name,
                    "price": item["quote"].value,
                    "threshold": item["target"].threshold,
                    "currency": item["target"].currency,
                }
                for item in findings["targets"]
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


# ---------------------------------------------------------------------------
# data


def data_import(session, args):
    from .excel import IMPORTERS

    filepath = args.file
    table = args.table

    try:
        if filepath.endswith(".xlsx"):
            if not table:
                print("Error: --table is required for Excel import")
                print("Usage: buylog data import -t <table> file.xlsx")
                return 1

            success, errors = IMPORTERS[table](session, filepath)
            print(f"Import complete: {success} rows imported")
            if errors:
                print(f"Errors ({len(errors)}):")
                for err in errors[:10]:
                    print(f"  - {err}")
                if len(errors) > 10:
                    print(f"  ... and {len(errors) - 10} more errors")

        elif filepath.endswith(".csv") or filepath.endswith(".json"):
            # CSV/JSON import (quotes only)
            from .services import ImportService

            if filepath.endswith(".json"):
                stats = ImportService.import_quotes_json(session, filepath, True)
            else:
                stats = ImportService.import_quotes_csv(session, filepath, True)

            print("Import complete:")
            print(f"  Imported: {stats['imported']}")
            print(f"  Skipped: {stats['skipped']}")
            if stats.get("created_vendors"):
                print(f"  Created vendors: {', '.join(stats['created_vendors'])}")
            if stats.get("created_products"):
                print(f"  Created products: {', '.join(stats['created_products'])}")
            if stats.get("errors"):
                print("  Errors:")
                for err in stats["errors"][:10]:
                    print(f"    - {err}")
        else:
            print("Error: Unsupported file format. Use .xlsx, .csv, or .json")
            return 1

    except FileNotFoundError:
        print(f"Error: File not found: {filepath}")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1


def data_export(session, args):
    from .excel import (
        export_all,
        export_brands,
        export_products,
        export_vendors,
        export_quotes,
        export_specifications,
        export_purchase_orders,
    )

    table = args.table
    fmt = args.format

    if args.output:
        filepath = args.output
    elif table:
        filepath = f"buylog-{table}.{fmt}"
    else:
        filepath = f"buylog-db.{fmt}"

    if fmt == "xlsx" and not filepath.endswith(".xlsx"):
        filepath = filepath.rsplit(".", 1)[0] + ".xlsx"

    try:
        if fmt == "xlsx":
            if table is None:
                counts = export_all(session, filepath)
                print(f"Exported to: {filepath}")
                for name, count in counts.items():
                    print(f"  {name}: {count} rows")
            else:
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
                count = exporters[table](session, filepath)
                print(f"Exported {count} rows to: {filepath}")
        else:
            from .services import ExportService

            csv_exporters = {
                "quotes": ExportService.export_quotes_csv,
                "products": ExportService.export_products_csv,
                "vendors": ExportService.export_vendors_csv,
            }
            if table not in csv_exporters:
                print("CSV export only supports: quotes, products, vendors")
                return 1
            csv_exporters[table](session, filepath)
            print(f"Exported to: {filepath}")
    except Exception as e:
        print(f"Error: {e}")
        return 1


def data_template(session, args):
    table = {"specs": "specifications", "pos": "purchase_orders"}.get(
        args.table, args.table
    )
    fmt = "yaml" if args.format == "yml" else args.format
    filepath = args.output or f"{table}-template.{args.format}"

    try:
        if fmt == "xlsx":
            from .excel import TEMPLATE_GENERATORS

            if not filepath.endswith(".xlsx"):
                filepath += ".xlsx"
            generator = TEMPLATE_GENERATORS[table]
            # Some templates need the session for data validation dropdowns
            if table in ("products", "quotes", "purchase_orders"):
                generator(filepath, session)
            else:
                generator(filepath)
        else:
            from .templates import (
                vendor_template_str,
                specification_template_str,
                purchase_order_template_str,
            )

            text_generators = {
                "vendors": vendor_template_str,
                "specifications": specification_template_str,
                "purchase_orders": purchase_order_template_str,
            }
            if table not in text_generators:
                print(
                    f"Error: {fmt.upper()} templates only available for: "
                    f"{', '.join(text_generators)}. Use -f xlsx for {table}."
                )
                return 1
            Path(filepath).write_text(text_generators[table](fmt=fmt))
        print(f"Generated template: {filepath}")
    except Exception as e:
        print(f"Error: {e}")
        return 1


# ---------------------------------------------------------------------------
# db


def db_backup(session, args):
    from .services import BackupService

    try:
        backup_path = BackupService.backup(Config.get_db_path(), args.output)
        print(f"Backup created: {backup_path}")
    except Exception as e:
        print(f"Error: {e}")
        return 1


def db_restore(session, args):
    from .services import BackupService

    create_backup = not args.no_backup
    session.close()  # Close current session before restore
    BackupService.restore(args.file, Config.get_db_path(), create_backup)
    print(f"Database restored from: {args.file}")
    if create_backup:
        print("(Previous database was backed up)")


def db_backups(session, args):
    from .services import BackupService

    backups = BackupService.list_backups(Config.get_db_path())
    if not backups:
        print("No backups found")
        return
    print(f"Available backups ({len(backups)}):")
    for b in backups:
        print(f"  {b['name']} ({b['size'] / 1024:.1f} KB) - {b['modified']}")


def db_migrate(session, args):
    from .migrate import MigrationError, run_migrations, check_migrations

    if args.dry_run:
        migrations = check_migrations(engine)
        if not migrations:
            print("Database schema is up to date.")
            return
        print(f"Would apply {len(migrations)} migration(s):\n")
        for sql in migrations:
            print(f"  {sql};\n")
        return

    try:
        migrations = run_migrations(engine)
    except MigrationError as e:
        print(f"Migration failed: {e}")
        return 1
    if not migrations:
        print("Database schema is up to date.")
        return
    print(f"Applied {len(migrations)} migration(s):")
    for sql in migrations:
        display = sql[:70] + "..." if len(sql) > 70 else sql
        print(f"  {display}")
    print("\nMigration complete.")


def db_init(session, args):
    from .services import SettingService

    base = SettingService.set_base_currency(session, args.base)
    print(f"Base currency: {base}")
    print(f"Database: {Config.get_db_path()}")


def db_seed(session, args):
    seed_database(session)


def tui(session, args):
    session.close()
    from .tui import main as tui_main

    tui_main()


# ---------------------------------------------------------------------------
# parser


def build_parser() -> argparse.ArgumentParser:
    """Build the ``buylog <noun> <verb>`` argument parser."""
    yes_help = "Assume yes for all confirmation prompts (for non-interactive use)"
    # Shared by every command so -y works before or after the command name.
    # SUPPRESS keeps a command's unset -y from overwriting a top-level -y.
    yes = argparse.ArgumentParser(add_help=False)
    yes.add_argument(
        "-y", "--yes", action="store_true", default=argparse.SUPPRESS, help=yes_help
    )

    parser = argparse.ArgumentParser(
        prog="buylog",
        description="Buyer - Purchasing support and vendor quote management",
    )
    parser.add_argument("-y", "--yes", action="store_true", help=yes_help)
    nouns = parser.add_subparsers(title="commands", metavar="COMMAND")

    def noun(name, help):
        p = nouns.add_parser(name, help=help, description=help)
        p.set_defaults(func=None, usage_parser=p)
        return p.add_subparsers(title="commands", metavar="COMMAND")

    def verb(group, name, func, help):
        p = group.add_parser(name, help=help, description=help, parents=[yes])
        p.set_defaults(func=func)
        return p

    def leaf(name, func, help):
        return verb(nouns, name, func, help)

    # brand
    g = noun("brand", "Brands")
    verb(g, "add", brand_add, "Add a brand").add_argument("name")
    verb(g, "list", brand_list, "List brands").add_argument(
        "--search", help="Filter by name"
    )
    p = verb(g, "edit", brand_edit, "Rename a brand")
    p.add_argument("name")
    p.add_argument("--name", dest="new_name", required=True, help="New name")
    verb(g, "rm", brand_rm, "Delete a brand").add_argument("name")

    # product
    g = noun("product", "Products")
    p = verb(g, "add", product_add, "Add a product")
    p.add_argument("name")
    p.add_argument("-b", "--brand", required=True, help="Brand (created if missing)")
    p.add_argument("--category", help="Category")
    verb(g, "list", product_list, "List products").add_argument(
        "--search", help="Filter by name"
    )
    p = verb(g, "edit", product_edit, "Change a product's name, brand or category")
    p.add_argument("name")
    p.add_argument("--name", dest="new_name", help="New name")
    p.add_argument("-b", "--brand", help="New brand")
    p.add_argument("--category", help="New category")
    verb(g, "rm", product_rm, "Delete a product and its quotes").add_argument("name")
    verb(g, "categories", product_categories, "List product classifications")
    verb(g, "history", product_history, "Price history of a product").add_argument(
        "name"
    )
    verb(g, "copy", product_copy, "Copy a product to the clipboard").add_argument(
        "name"
    )
    verb(g, "dupes", product_dupes, "Find similar product names").add_argument(
        "--threshold", type=float, default=0.8, help="Similarity 0-1 (default: 0.8)"
    )
    p = verb(g, "merge", product_merge, "Merge products into one")
    p.add_argument("keep_id", type=int, help="ID of the product to keep")
    p.add_argument("ids", type=int, nargs="+", help="IDs to merge into it")

    # vendor
    g = noun("vendor", "Vendors")
    p = verb(g, "add", vendor_add, "Add a vendor")
    p.add_argument("name")
    p.add_argument("-c", "--currency", help="Currency (default: the base currency)")
    p.add_argument("--discount-code", help="Discount code")
    p.add_argument("--discount", type=float, default=0.0, help="Discount percentage")
    p.add_argument("--url", help="Vendor URL")
    verb(g, "list", vendor_list, "List vendors").add_argument(
        "--search", help="Filter by name"
    )
    p = verb(g, "edit", vendor_edit, "Change a vendor")
    p.add_argument("name")
    p.add_argument("--name", dest="new_name", help="New name")
    p.add_argument("-c", "--currency", help="Currency")
    p.add_argument("--discount-code", help="Discount code")
    p.add_argument("--discount", type=float, help="Discount percentage")
    url = p.add_mutually_exclusive_group()
    url.add_argument("--url", help="Set the vendor URL")
    url.add_argument("--no-url", action="store_true", help="Clear the vendor URL")
    verb(g, "rm", vendor_rm, "Delete a vendor and its quotes").add_argument("name")
    verb(g, "open", vendor_open, "Open the vendor URL in a browser").add_argument(
        "name"
    )
    verb(g, "copy", vendor_copy, "Copy a vendor to the clipboard").add_argument("name")
    verb(g, "dupes", vendor_dupes, "Find similar vendor names").add_argument(
        "--threshold", type=float, default=0.8, help="Similarity 0-1 (default: 0.8)"
    )
    p = verb(g, "merge", vendor_merge, "Merge vendors into one")
    p.add_argument("keep_id", type=int, help="ID of the vendor to keep")
    p.add_argument("ids", type=int, nargs="+", help="IDs to merge into it")

    # quote
    g = noun("quote", "Vendor quotes")
    p = verb(g, "add", quote_add, "Add a quote")
    p.add_argument("-v", "--vendor", required=True, help="Vendor (asks to create)")
    p.add_argument("-p", "--product", required=True, help="Product (asks to create)")
    p.add_argument("-b", "--brand", help="Brand, for a new product")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--price", type=float, help="Price in the vendor's currency")
    source.add_argument("--url", help="Scrape the price from this page")
    p.add_argument("--shipping", type=float, help="Shipping cost")
    p.add_argument("--tax-rate", type=float, help="Tax rate percentage")
    p = verb(g, "list", quote_list, "List quotes")
    which = p.add_mutually_exclusive_group()
    which.add_argument("--search", help="Filter by product name")
    which.add_argument("--status", choices=QUOTE_STATUSES, help="Filter by status")
    which.add_argument(
        "--receipts", action="store_true", help="Only quotes with a receipt"
    )
    which.add_argument(
        "--tracked", action="store_true", help="Only quotes with a source URL"
    )
    p.add_argument("--limit", type=int, default=100, help="Maximum rows")
    verb(g, "rm", quote_rm, "Delete a quote").add_argument("id", type=int)
    p = verb(g, "status", quote_status, "Set quote status")
    p.add_argument("id", type=int)
    p.add_argument("status", choices=QUOTE_STATUSES)
    verb(g, "history", quote_history, "Price history of a quote").add_argument(
        "id", type=int
    )
    verb(g, "copy", quote_copy, "Copy a quote to the clipboard").add_argument(
        "id", type=int
    )
    receipt = verb(g, "receipt", None, "Receipt attachments")
    receipt.set_defaults(usage_parser=receipt)
    r = receipt.add_subparsers(title="commands", metavar="COMMAND")
    p = verb(r, "attach", quote_receipt_attach, "Attach a receipt file")
    p.add_argument("id", type=int)
    p.add_argument("file")
    verb(r, "open", quote_receipt_open, "Open the receipt").add_argument("id", type=int)
    verb(r, "rm", quote_receipt_rm, "Detach the receipt").add_argument("id", type=int)
    verb(g, "scrape", quote_scrape, "Show the price scraped from a URL").add_argument(
        "url"
    )
    verb(
        g, "refresh", quote_refresh, "Re-scrape quotes that have a source URL"
    ).add_argument("ids", type=int, nargs="*", help="Quote IDs (default: all tracked)")

    # compare
    p = leaf("compare", compare, "Compare prices across vendors")
    p.add_argument("product", nargs="?", help="Exact product name")
    p.add_argument("--search", help="Products whose name contains this")
    p.add_argument("--category", help="Products in this category")
    p.add_argument("--brand", help="Products from this brand")

    # watch
    g = noun("watch", "Price targets; each fires once")
    p = verb(g, "add", watch_add, "Notify when the best price reaches a target")
    p.add_argument("product")
    p.add_argument("threshold", type=float)
    p.add_argument("-c", "--currency", help="Target currency (default: the base)")
    p = verb(g, "list", watch_list, "List active targets")
    which = p.add_mutually_exclusive_group()
    which.add_argument("--all", action="store_true", help="Include inactive targets")
    which.add_argument("--triggered", action="store_true", help="Only fired targets")
    p = verb(g, "edit", watch_edit, "Change a target and re-arm it")
    p.add_argument("id", type=int)
    p.add_argument("threshold", type=float, nargs="?")
    p.add_argument("-c", "--currency")
    verb(g, "off", watch_off, "Deactivate a target").add_argument("id", type=int)
    verb(g, "rm", watch_rm, "Delete a target").add_argument("id", type=int)

    # purchase-list
    g = noun("purchase-list", "Named lists of quotes you might buy")
    p = verb(g, "add", plist_add, "Create a purchase list")
    p.add_argument("name")
    p.add_argument("--description")
    verb(g, "list", plist_list, "List purchase lists")
    verb(g, "show", plist_show, "Show a purchase list").add_argument("name")
    verb(g, "rm", plist_rm, "Delete a purchase list").add_argument("name")
    p = verb(g, "add-quote", plist_add_quote, "Put a quote on a list")
    p.add_argument("name")
    p.add_argument("quote_id", type=int)
    p = verb(g, "rm-quote", plist_rm_quote, "Take a quote off a list")
    p.add_argument("name")
    p.add_argument("quote_id", type=int)
    verb(
        g, "consider", plist_consider, "Put every quote for a product on Considering"
    ).add_argument("product")
    verb(g, "might-buy", plist_might_buy, "Unbought quotes on any list")

    # po
    g = noun("po", "Purchase orders")
    p = verb(g, "add", po_add, "Create a purchase order")
    p.add_argument("po_number", help="Unique PO number")
    p.add_argument("--from-quote", type=int, help="Copy vendor, product, price")
    p.add_argument("-v", "--vendor")
    p.add_argument("-p", "--product")
    p.add_argument("--price", type=float, help="Unit price")
    p.add_argument("--quantity", type=int, default=1, help="Units (default: 1)")
    p.add_argument("--shipping", type=float, help="Shipping cost")
    p.add_argument("--tax", type=float, help="Tax amount")
    p.add_argument("--order-date", help="YYYY-MM-DD")
    p.add_argument("--expected-delivery", help="YYYY-MM-DD")
    p.add_argument("--invoice", help="Invoice number")
    p.add_argument("--notes")
    p = verb(g, "list", po_list, "List purchase orders")
    p.add_argument("--status", choices=PO_STATUSES)
    p.add_argument("-v", "--vendor")
    p.add_argument("--limit", type=int, default=100, help="Maximum rows")
    verb(g, "show", po_show, "Show a purchase order").add_argument("po_number")
    p = verb(g, "edit", po_edit, "Change a purchase order's status")
    p.add_argument("po_number")
    p.add_argument("--status", choices=PO_STATUSES, required=True)
    p.add_argument("--delivery-date", help="Actual delivery, YYYY-MM-DD")

    # spec
    g = noun("spec", "Specifications")
    p = verb(g, "add", spec_add, "Create a specification")
    p.add_argument("name")
    p.add_argument("--description")
    p = verb(g, "feature", spec_feature, "Add a feature to a specification")
    p.add_argument("spec")
    p.add_argument("feature")
    p.add_argument("--type", dest="data_type", choices=SPEC_DATA_TYPES, default="text")
    p.add_argument("--unit", help='e.g. "MP", "kg"')
    p.add_argument("--required", action="store_true")
    p.add_argument("--min", type=float)
    p.add_argument("--max", type=float)
    verb(g, "list", spec_list, "List specifications").add_argument(
        "--limit", type=int, default=100, help="Maximum rows"
    )
    verb(g, "show", spec_show, "Show a specification").add_argument("name")
    verb(g, "rm", spec_rm, "Delete a specification").add_argument("name")

    # tag
    g = noun("tag", "Tags on brands, products, vendors and quotes")
    for name, func, help in (
        ("add", tag_add, "Tag an entity"),
        ("rm", tag_rm, "Untag an entity"),
    ):
        p = verb(g, name, func, help)
        p.add_argument("kind", choices=ENTITY_KINDS)
        p.add_argument("id", type=int)
        p.add_argument("tag")
    p = verb(g, "list", tag_list, "List all tags, or one entity's tags")
    p.add_argument("kind", nargs="?", choices=ENTITY_KINDS)
    p.add_argument("id", nargs="?", type=int)
    p = verb(g, "find", tag_find, "Find entities with a tag")
    p.add_argument("tag")
    p.add_argument("--kind", choices=ENTITY_KINDS)

    # note
    g = noun("note", "Notes on brands, products, vendors and quotes")
    p = verb(g, "add", note_add, "Add a note")
    p.add_argument("kind", choices=ENTITY_KINDS)
    p.add_argument("id", type=int)
    p.add_argument("text")
    p = verb(g, "list", note_list, "List an entity's notes")
    p.add_argument("kind", choices=ENTITY_KINDS)
    p.add_argument("id", type=int)
    verb(g, "rm", note_rm, "Delete a note").add_argument("note_id", type=int)

    # fx
    g = noun("fx", "Forex rates")
    p = verb(g, "add", fx_add, "Add a rate by hand")
    p.add_argument("code", help="Currency code, e.g. EUR")
    p.add_argument(
        "rate", type=float, help="Units of the base currency per unit, e.g. 1.085"
    )
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")
    verb(g, "list", fx_list, "List rates").add_argument(
        "--limit", type=int, default=100, help="Maximum rows"
    )
    p = verb(g, "refresh", fx_refresh, "Fetch current rates from Frankfurter")
    p.add_argument(
        "codes", nargs="*", help="Currency codes (default: every vendor currency)"
    )
    p.add_argument("--date", help="Record the rates under this date (YYYY-MM-DD)")

    # spend
    p = leaf("spend", spend, "Money committed via purchase orders")
    p.add_argument(
        "view",
        nargs="?",
        default="summary",
        choices=["summary", "month", "vendor", "category", "variance"],
        help="Which table to show (default: summary, all of them)",
    )
    p.add_argument("--from", dest="start", help="Start date (YYYY-MM-DD)")
    p.add_argument("--to", dest="end", help="End date (YYYY-MM-DD)")

    # report
    g = noun("report", "HTML reports")
    p = verb(g, "prices", report, "Prices across vendors")
    p.set_defaults(preset="price-comparison")
    p.add_argument("--search", help="Filter products by name")
    p.add_argument("-o", "--output", help="HTML file (default: stdout)")
    for name, preset, help in (
        ("purchases", "purchase-summary", "Quotes by status"),
        ("vendors", "vendor-analysis", "Vendor statistics"),
    ):
        p = verb(g, name, report, help)
        p.set_defaults(preset=preset)
        p.add_argument("-o", "--output", help="HTML file (default: stdout)")
    p = verb(g, "spend", report, "What was spent, from purchase orders")
    p.set_defaults(preset="spend-analysis")
    p.add_argument("-o", "--output", help="HTML file (default: stdout)")
    p.add_argument("--from", dest="start", help="Start date (YYYY-MM-DD)")
    p.add_argument("--to", dest="end", help="End date (YYYY-MM-DD)")

    # check
    p = leaf("check", check, "Check price targets, and notify (cron)")
    p.add_argument(
        "--notify",
        help="Comma-separated channels (stdout, ntfy, webhook, desktop, email); "
        "defaults to $BUYLOG_NOTIFY, else stdout",
    )
    p.add_argument("--refresh-fx", action="store_true", help="Refresh forex first")
    p.add_argument(
        "--refresh-scrapes", action="store_true", help="Re-scrape tracked quotes first"
    )
    p.add_argument(
        "--quiet", action="store_true", help="Print nothing when nothing triggered"
    )

    # data
    g = noun("data", "Import, export and templates")
    p = verb(g, "import", data_import, "Import from .xlsx, .csv or .json")
    p.add_argument("file")
    p.add_argument("-t", "--table", choices=TABLES, help="Table (required for .xlsx)")
    p = verb(g, "export", data_export, "Export to .xlsx or .csv")
    p.add_argument(
        "-t", "--table", choices=TABLES + ["brands"], help="Table (default: all)"
    )
    p.add_argument("-o", "--output", help="Output file")
    p.add_argument("--format", choices=["xlsx", "csv"], default="xlsx")
    p = verb(g, "template", data_template, "Write an import template")
    p.add_argument("-t", "--table", choices=TABLES, required=True)
    p.add_argument(
        "-f", "--format", choices=["xlsx", "yaml", "yml", "json"], default="yaml"
    )
    p.add_argument("-o", "--output", help="Output file")

    # db
    g = noun("db", "Database backup, restore and schema")
    verb(g, "backup", db_backup, "Back up the database").add_argument(
        "-o", "--output", help="Backup file (default: timestamped)"
    )
    p = verb(g, "restore", db_restore, "Restore the database from a backup")
    p.add_argument("file")
    p.add_argument(
        "--no-backup", action="store_true", help="Do not back up the current database"
    )
    verb(g, "backups", db_backups, "List backups")
    verb(
        g, "migrate", db_migrate, "Migrate the schema to match the models"
    ).add_argument("--dry-run", action="store_true", help="Show SQL without executing")
    verb(
        g, "init", db_init, "Set the base currency, before any amounts exist"
    ).add_argument(
        "--base",
        default="USD",
        help="Base currency every amount is kept in (default: USD)",
    )
    verb(g, "seed", db_seed, "Populate the database with sample data")

    leaf("tui", tui, "Launch the interactive TUI")

    return parser


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
    parser = build_parser()
    args = parser.parse_args(argv)

    func = getattr(args, "func", None)
    if func is None:
        getattr(args, "usage_parser", parser).print_help()
        return 1

    session = Session()
    try:
        return func(session, args) or 0
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
        # Not found, duplicate, invalid input: the user's error, already
        # printed, so it is not logged as an application error.
        session.rollback()
        logger.debug(f"Service error: {e}")
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


if __name__ == "__main__":
    sys.exit(main())
