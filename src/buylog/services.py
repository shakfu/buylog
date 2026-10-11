#!/usr/bin/env python3
"""
Business logic service layer for buylog application.

This module contains service classes that handle business logic,
keeping it separate from models (data) and views (presentation).
"""

import csv
import datetime
import io
import json
import logging
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any, Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .models import (
    Brand,
    Product,
    Vendor,
    Quote,
    Forex,
    QuoteHistory,
    PriceTarget,
    PurchaseList,
    Note,
    Tag,
    EntityTag,
    Specification,
    SpecificationFeature,
    ProductFeature,
    PurchaseOrder,
    QUOTE_STATUSES,
    SPEC_DATA_TYPES,
    QUOTE_STATUS_CONSIDERING,
    PO_STATUSES,
    PO_STATUS_PENDING,
    PO_STATUS_APPROVED,
    PO_STATUS_ORDERED,
    PO_STATUS_SHIPPED,
    PO_STATUS_RECEIVED,
    PO_STATUS_CANCELLED,
    BASE_CURRENCY_KEY,
    Setting,
    format_money,
    get_base_currency,
)
from .audit import AuditService

logger = logging.getLogger("buylog")


def _money(session: Session, amount: float) -> str:
    """An amount in the database's base currency, formatted for display."""
    return format_money(amount, get_base_currency(session))


class ServiceError(Exception):
    """Base exception for service layer errors"""

    pass


class ValidationError(ServiceError):
    """Raised when input validation fails"""

    pass


class DuplicateError(ServiceError):
    """Raised when attempting to create a duplicate entity"""

    pass


class NotFoundError(ServiceError):
    """Raised when an entity is not found"""

    pass


def _blocking_purchase_orders(session: Session, column: Any, value: Any) -> List[str]:
    """
    PO numbers that reference ``value`` through ``column``.

    A purchase order is a record of money committed, so it is never deleted as
    a side effect of tidying up a vendor, product or quote.
    """
    return [
        po.po_number
        for po in session.execute(select(PurchaseOrder).where(column == value))
        .scalars()
        .all()
    ]


def _refuse_if_referenced_by_purchase_orders(
    session: Session, column: Any, value: Any, what: str
) -> None:
    """
    Raise a clear error if purchase orders reference this entity.

    Without this the delete still fails, but as a bare "FOREIGN KEY constraint
    failed" that says nothing about which orders are in the way.
    """
    blocking = _blocking_purchase_orders(session, column, value)
    if blocking:
        raise ValidationError(
            f"Cannot delete {what}: referenced by purchase order(s) "
            f"{', '.join(sorted(blocking))}. Delete or reassign them first."
        )


@contextmanager
def commit_or_rollback(
    session: Session,
    what: str,
    duplicate_message: Optional[str] = None,
) -> Iterator[None]:
    """
    Commit the session on success, roll back and translate errors on failure.

    Wraps the ``try / commit / except IntegrityError / except SQLAlchemyError /
    rollback / raise`` block that every write in this module needs, so the
    rollback cannot be forgotten and the error mapping stays consistent.

    Args:
        session: Database session
        what: Short description of the operation, used in log and error text
              (e.g. ``"create brand 'Apple'"``)
        duplicate_message: Message for the ``DuplicateError`` raised on an
                           integrity violation. When omitted, an integrity
                           violation is reported as a ``ServiceError``.

    Raises:
        DuplicateError: On an integrity violation, if ``duplicate_message`` is set
        ServiceError: On any other database error
    """
    try:
        yield
        session.commit()
    except IntegrityError as e:
        session.rollback()
        logger.error(f"Failed to {what}: {e}")
        if duplicate_message:
            raise DuplicateError(duplicate_message) from e
        raise ServiceError(f"Failed to {what}: {e}") from e
    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Database error trying to {what}: {e}")
        raise ServiceError(f"Failed to {what}: {e}") from e
    except Exception:
        # Any other failure inside the block would otherwise leave the caller's
        # mutations pending on the session, to be committed by whatever writes
        # next. Discard them and let the original exception through unchanged.
        session.rollback()
        raise


class BrandService:
    """Service for brand-related business logic"""

    @staticmethod
    def create(session: Session, name: str) -> Brand:
        """
        Create a new brand.

        Args:
            session: Database session
            name: Brand name

        Returns:
            Created Brand instance

        Raises:
            ValidationError: If name is invalid
            DuplicateError: If brand already exists
        """
        # Validate input
        name = name.strip()
        if not name:
            raise ValidationError("Brand name cannot be empty")
        if len(name) > 255:
            raise ValidationError("Brand name too long (max 255 characters)")

        # Check for duplicate
        existing = Brand.by_name(session, name)
        if existing:
            raise DuplicateError(f"Brand '{name}' already exists")

        # Create brand
        try:
            brand = Brand(name=name)
            session.add(brand)
            session.commit()
            logger.info(f"Created brand: {name}")

            # Audit log
            AuditService.log_create(
                entity_type="brand",
                entity_id=brand.id,
                entity_name=brand.name,
                session=session,
            )

            return brand
        except IntegrityError as e:
            session.rollback()
            logger.error(f"Failed to create brand '{name}': {e}")
            raise DuplicateError(f"Brand '{name}' already exists") from e
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error creating brand '{name}': {e}")
            raise ServiceError(f"Failed to create brand: {e}") from e

    @staticmethod
    def get_by_name(session: Session, name: str) -> Optional[Brand]:
        """
        Get a brand by name.

        Args:
            session: Database session
            name: Brand name

        Returns:
            Brand instance or None if not found
        """
        return Brand.by_name(session, name)

    @staticmethod
    def get_all(
        session: Session,
        filter_by: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Brand]:
        """
        Get all brands with optional filtering and pagination.

        Args:
            session: Database session
            filter_by: Optional name filter (case-insensitive partial match)
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of Brand instances
        """
        query = select(Brand).options(joinedload(Brand.products))

        if filter_by:
            query = query.where(Brand.name.ilike(f"%{filter_by}%"))

        query = query.limit(limit).offset(offset)
        results = session.execute(query).unique().scalars().all()
        return list(results)

    @staticmethod
    def update(session: Session, name: str, new_name: str) -> Brand:
        """
        Update a brand's name.

        Args:
            session: Database session
            name: Current brand name
            new_name: New brand name

        Returns:
            Updated Brand instance

        Raises:
            NotFoundError: If brand not found
            ValidationError: If new name is invalid
            DuplicateError: If new name already exists
        """
        # Validate new name
        new_name = new_name.strip()
        if not new_name:
            raise ValidationError("Brand name cannot be empty")
        if len(new_name) > 255:
            raise ValidationError("Brand name too long (max 255 characters)")

        # Find brand
        brand = Brand.by_name(session, name)
        if not brand:
            raise NotFoundError(f"Brand '{name}' not found")

        # Check if new name is already taken
        if new_name != name:
            existing = Brand.by_name(session, new_name)
            if existing:
                raise DuplicateError(f"Brand '{new_name}' already exists")

        # Update
        try:
            brand.name = new_name
            session.commit()
            logger.info(f"Updated brand '{name}' to '{new_name}'")
            return brand
        except IntegrityError as e:
            session.rollback()
            logger.error(f"Failed to update brand '{name}': {e}")
            raise DuplicateError(f"Brand '{new_name}' already exists") from e
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error updating brand '{name}': {e}")
            raise ServiceError(f"Failed to update brand: {e}") from e

    @staticmethod
    def delete(session: Session, name: str) -> None:
        """
        Delete a brand that has no products.

        A brand's products are independently meaningful records, and deleting
        them would take their quotes and price history with them, so this
        refuses rather than cascading.

        Args:
            session: Database session
            name: Brand name

        Raises:
            NotFoundError: If brand not found
            ValidationError: If the brand still has products
        """
        brand = Brand.by_name(session, name)
        if not brand:
            raise NotFoundError(f"Brand '{name}' not found")

        if brand.products:
            names = sorted(p.name for p in brand.products)
            shown = ", ".join(names[:5])
            if len(names) > 5:
                shown += f", and {len(names) - 5} more"
            raise ValidationError(
                f"Cannot delete brand '{name}': it still has "
                f"{len(names)} product(s) ({shown}). Delete or reassign them first."
            )

        with commit_or_rollback(session, f"delete brand '{name}'"):
            session.delete(brand)

        logger.info(f"Deleted brand: {name}")


class ProductService:
    """Service for product-related business logic"""

    @staticmethod
    def create(
        session: Session,
        name: str,
        brand_name: str,
        category: Optional[str] = None,
    ) -> Product:
        """
        Create a new product under a brand.

        Args:
            session: Database session
            name: Product name
            brand_name: Brand name (will be created if doesn't exist)
            category: Optional category, used by the spend-by-category report

        Returns:
            Created Product instance

        Raises:
            ValidationError: If input is invalid
            DuplicateError: If product already exists
        """
        # Validate input
        name = name.strip()
        brand_name = brand_name.strip()

        if not name:
            raise ValidationError("Product name cannot be empty")
        if len(name) > 255:
            raise ValidationError("Product name too long (max 255 characters)")
        if not brand_name:
            raise ValidationError("Brand name cannot be empty")

        # Get or create brand
        brand = Brand.by_name(session, brand_name)
        if not brand:
            brand = BrandService.create(session, brand_name)

        # Check for duplicate product
        existing = Product.by_name(session, name)
        if existing:
            raise DuplicateError(f"Product '{name}' already exists")

        # Create product
        product = Product(
            name=name,
            brand=brand,
            category=category.strip() if category else None,
        )
        with commit_or_rollback(
            session,
            f"create product '{name}'",
            duplicate_message=f"Product '{name}' already exists",
        ):
            session.add(product)

        logger.info(f"Created product: {name} under brand: {brand_name}")
        return product

    @staticmethod
    def report_classification(
        product: Product, tag_names: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        The one label compare and spend use for a product.

        ``category`` wins when it is set. Otherwise the specification name,
        otherwise the product's tags. A tag or a spec is invisible to those
        reports only when a category is already set.
        """
        if product.category and product.category.strip():
            return product.category.strip()
        specification = product.specification
        if specification is not None and specification.name:
            return specification.name
        names = sorted(name for name in (tag_names or []) if name)
        if not names:
            return None
        if len(names) == 1:
            return names[0]
        return ", ".join(names)

    @staticmethod
    def get_by_name(session: Session, name: str) -> Optional[Product]:
        """Get a product by name."""
        return Product.by_name(session, name)

    @staticmethod
    def get_all(
        session: Session,
        filter_by: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Product]:
        """
        Get all products with optional filtering and pagination.

        Args:
            session: Database session
            filter_by: Optional name filter
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of Product instances with brands eagerly loaded
        """
        query = select(Product).options(joinedload(Product.brand))

        if filter_by:
            query = query.where(Product.name.ilike(f"%{filter_by}%"))

        query = query.limit(limit).offset(offset)
        results = session.execute(query).unique().scalars().all()
        return list(results)

    @staticmethod
    def update(
        session: Session,
        name: str,
        new_name: Optional[str] = None,
        brand_name: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Product:
        """
        Update a product.

        Args:
            session: Database session
            name: Current product name
            new_name: Optional new product name
            brand_name: Optional new brand name
            category: Optional new category

        Returns:
            Updated Product instance

        Raises:
            NotFoundError: If the product or the new brand is not found
            ValidationError: If the new name is invalid
            DuplicateError: If the new name is already taken
        """
        product = Product.by_name(session, name)
        if not product:
            raise NotFoundError(f"Product '{name}' not found")

        if new_name is not None:
            new_name = new_name.strip()
            if not new_name:
                raise ValidationError("Product name cannot be empty")
            if len(new_name) > 255:
                raise ValidationError("Product name too long (max 255 characters)")
            if new_name != name and Product.by_name(session, new_name):
                raise DuplicateError(f"Product '{new_name}' already exists")

        if brand_name is not None:
            brand = Brand.by_name(session, brand_name)
            if not brand:
                raise NotFoundError(f"Brand '{brand_name}' not found")
        else:
            brand = None

        with commit_or_rollback(
            session,
            f"update product '{name}'",
            duplicate_message=f"Product '{new_name}' already exists",
        ):
            if new_name is not None:
                product.name = new_name
            if brand is not None:
                product.brand = brand
            if category is not None:
                product.category = category

        logger.info(f"Updated product '{name}'")
        return product

    @staticmethod
    def delete(session: Session, name: str) -> None:
        """
        Delete a product and everything that hangs off it.

        Args:
            session: Database session
            name: Product name

        Raises:
            NotFoundError: If product not found
        """
        product = Product.by_name(session, name)
        if not product:
            raise NotFoundError(f"Product '{name}' not found")

        _refuse_if_referenced_by_purchase_orders(
            session, PurchaseOrder.product_id, product.id, f"product '{name}'"
        )

        with commit_or_rollback(session, f"delete product '{name}'"):
            session.delete(product)

        logger.info(f"Deleted product: {name}")


def _one_vendor_link(url: Optional[str], website: Optional[str]) -> Optional[str]:
    """The single link for a vendor. ``website`` is the old name for ``url``."""
    url = url.strip() if isinstance(url, str) else None
    website = website.strip() if isinstance(website, str) else None
    url = url or None
    website = website or None
    if url and website and url != website:
        raise ValidationError(
            f"A vendor has one link. url ({url}) and website ({website}) differ."
        )
    return url or website


class VendorService:
    """Service for vendor-related business logic"""

    @staticmethod
    def create(
        session: Session,
        name: str,
        currency: Optional[str] = None,
        discount_code: Optional[str] = None,
        discount: float = 0.0,
        url: Optional[str] = None,
        contact_person: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        website: Optional[str] = None,
        address_line1: Optional[str] = None,
        address_line2: Optional[str] = None,
        city: Optional[str] = None,
        state: Optional[str] = None,
        postal_code: Optional[str] = None,
        country: Optional[str] = None,
        tax_id: Optional[str] = None,
        payment_terms: Optional[str] = None,
    ) -> Vendor:
        """
        Create a new vendor.

        Args:
            session: Database session
            name: Vendor name
            currency: Currency code (default: the base currency)
            discount_code: Optional discount code
            discount: Discount percentage (default: 0.0)
            url: Optional vendor URL
            contact_person: Optional contact person name
            email: Optional email address
            phone: Optional phone number
            website: Old name for ``url``. Stored in ``url``. Both may be passed
                when they are the same string.
            address_line1: Optional street address line 1
            address_line2: Optional street address line 2
            city: Optional city
            state: Optional state/province
            postal_code: Optional postal/zip code
            country: Optional country
            tax_id: Optional tax ID number
            payment_terms: Optional payment terms description

        Returns:
            Created Vendor instance

        Raises:
            ValidationError: If input is invalid
            DuplicateError: If vendor already exists
        """
        # Validate input
        name = name.strip()
        currency = (currency or get_base_currency(session)).strip().upper()

        if not name:
            raise ValidationError("Vendor name cannot be empty")
        if len(name) > 255:
            raise ValidationError("Vendor name too long (max 255 characters)")
        if len(currency) != 3:
            raise ValidationError("Currency code must be 3 characters (ISO 4217)")
        if not (0 <= discount <= 100):
            raise ValidationError("Discount must be between 0 and 100")
        if email and "@" not in email:
            raise ValidationError("Invalid email address format")

        # url and website were two columns for one link. Callers may still
        # pass either name. They have to agree when both are set.
        url = _one_vendor_link(url, website)

        # Check for duplicate
        existing = Vendor.by_name(session, name)
        if existing:
            raise DuplicateError(f"Vendor '{name}' already exists")

        # Create vendor
        try:
            vendor = Vendor(
                name=name,
                currency=currency,
                discount_code=discount_code,
                discount=discount,
                url=url,
                contact_person=contact_person,
                email=email,
                phone=phone,
                address_line1=address_line1,
                address_line2=address_line2,
                city=city,
                state=state,
                postal_code=postal_code,
                country=country,
                tax_id=tax_id,
                payment_terms=payment_terms,
            )
            session.add(vendor)
            session.commit()
            logger.info(f"Created vendor: {name} (currency: {currency})")
            return vendor
        except IntegrityError as e:
            session.rollback()
            logger.error(f"Failed to create vendor '{name}': {e}")
            raise DuplicateError(f"Vendor '{name}' already exists") from e
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error creating vendor '{name}': {e}")
            raise ServiceError(f"Failed to create vendor: {e}") from e

    @staticmethod
    def get_all(
        session: Session,
        filter_by: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Vendor]:
        """Get all vendors with pagination and eager loading."""
        query = select(Vendor).options(joinedload(Vendor.quotes))

        if filter_by:
            query = query.where(Vendor.name.ilike(f"%{filter_by}%"))

        query = query.limit(limit).offset(offset)
        results = session.execute(query).unique().scalars().all()
        return list(results)

    @staticmethod
    def update(
        session: Session,
        name: str,
        new_name: Optional[str] = None,
        currency: Optional[str] = None,
        discount_code: Optional[str] = None,
        discount: Optional[float] = None,
        url: Optional[str] = None,
    ) -> Vendor:
        """
        Update a vendor.

        Note that changing ``currency`` does not re-convert existing quotes;
        their stored base-currency values were normalized at the rate in force
        when they were entered.

        Args:
            session: Database session
            name: Current vendor name
            new_name: Optional new vendor name
            currency: Optional new currency code
            discount_code: Optional new discount code
            discount: Optional new discount percentage
            url: Optional new vendor URL

        Returns:
            Updated Vendor instance

        Raises:
            NotFoundError: If vendor not found
            ValidationError: If a new value is invalid
            DuplicateError: If the new name is already taken
        """
        vendor = Vendor.by_name(session, name)
        if not vendor:
            raise NotFoundError(f"Vendor '{name}' not found")

        if new_name is not None:
            new_name = new_name.strip()
            if not new_name:
                raise ValidationError("Vendor name cannot be empty")
            if len(new_name) > 255:
                raise ValidationError("Vendor name too long (max 255 characters)")
            if new_name != name and Vendor.by_name(session, new_name):
                raise DuplicateError(f"Vendor '{new_name}' already exists")

        if currency is not None:
            currency = currency.strip().upper()
            if len(currency) != 3:
                raise ValidationError("Currency code must be 3 characters (ISO 4217)")

        if discount is not None and not 0 <= discount <= 100:
            raise ValidationError("Discount must be between 0 and 100")

        with commit_or_rollback(
            session,
            f"update vendor '{name}'",
            duplicate_message=f"Vendor '{new_name}' already exists",
        ):
            if new_name is not None:
                vendor.name = new_name
            if currency is not None:
                vendor.currency = currency
            if discount_code is not None:
                vendor.discount_code = discount_code
            if discount is not None:
                vendor.discount = discount
            if url is not None:
                vendor.url = url

        logger.info(f"Updated vendor '{name}'")
        return vendor

    @staticmethod
    def delete(session: Session, name: str) -> None:
        """
        Delete a vendor and everything that hangs off it.

        Args:
            session: Database session
            name: Vendor name

        Raises:
            NotFoundError: If vendor not found
        """
        vendor = Vendor.by_name(session, name)
        if not vendor:
            raise NotFoundError(f"Vendor '{name}' not found")

        _refuse_if_referenced_by_purchase_orders(
            session, PurchaseOrder.vendor_id, vendor.id, f"vendor '{name}'"
        )

        with commit_or_rollback(session, f"delete vendor '{name}'"):
            session.delete(vendor)

        logger.info(f"Deleted vendor: {name}")


# Quote.status reads these two collections; load them with the quotes.
QUOTE_STATUS_LOAD = (
    selectinload(Quote.purchase_orders),
    selectinload(Quote.purchase_lists),
)


class QuoteService:
    """Service for quote-related business logic"""

    @staticmethod
    def create(
        session: Session,
        vendor_name: str,
        product_name: str,
        price: float,
        brand_name: Optional[str] = None,
        shipping_cost: Optional[float] = None,
        tax_rate: Optional[float] = None,
        record_history: bool = True,
    ) -> Quote:
        """
        Create a new quote from a vendor for a product.

        Args:
            session: Database session
            vendor_name: Vendor name
            product_name: Product name
            price: Price in vendor's currency
            brand_name: Optional brand name (for creating new products)
            shipping_cost: Optional shipping cost, in the vendor's currency
            tax_rate: Optional tax rate, as a percentage
            record_history: Whether to write a "create" price-history entry

        Returns:
            Created Quote instance

        Raises:
            ValidationError: If input is invalid
            NotFoundError: If vendor or product not found
        """
        # Validate input
        if price < 0:
            raise ValidationError("Price cannot be negative")
        if shipping_cost is not None and shipping_cost < 0:
            raise ValidationError("Shipping cost cannot be negative")
        if tax_rate is not None and tax_rate < 0:
            raise ValidationError("Tax rate cannot be negative")

        # Get vendor
        vendor = Vendor.by_name(session, vendor_name)
        if not vendor:
            raise NotFoundError(f"Vendor '{vendor_name}' not found")

        # Get or create product
        product = Product.by_name(session, product_name)
        if not product:
            if not brand_name:
                raise ValidationError(
                    f"Product '{product_name}' not found and no brand name provided"
                )
            product = ProductService.create(session, product_name, brand_name)

        # Convert currency if needed. Shipping is quoted in the same currency
        # as the price, so it has to be normalized too - otherwise total_cost
        # adds e.g. EUR shipping onto a base-currency price.
        value, original_value, original_currency = ForexService.to_base(
            session, price, vendor.currency
        )
        if shipping_cost is not None:
            shipping_cost, _, _ = ForexService.to_base(
                session, shipping_cost, vendor.currency
            )

        # Create quote
        quote = Quote(
            vendor=vendor,
            product=product,
            value=value,
            original_value=original_value,
            original_currency=original_currency,
            shipping_cost=shipping_cost,
            tax_rate=tax_rate,
        )
        with commit_or_rollback(
            session, f"create quote for '{product_name}' from '{vendor_name}'"
        ):
            session.add(quote)

        if record_history:
            QuoteHistoryService.record_change(session, quote, None, value, "create")

        logger.info(
            f"Created quote: {vendor_name} -> {product_name} = {price} {vendor.currency}"
        )
        return quote

    @staticmethod
    def get_all(
        session: Session,
        filter_by: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Quote]:
        """
        Get all quotes with pagination and eager loading.

        Eagerly loads vendor, product, and brand to avoid N+1 queries.
        """
        query = select(Quote).options(
            joinedload(Quote.vendor),
            joinedload(Quote.product).joinedload(Product.brand),
            *QUOTE_STATUS_LOAD,
        )

        if filter_by:
            query = query.join(Product).where(Product.name.ilike(f"%{filter_by}%"))

        query = query.limit(limit).offset(offset)
        results = session.execute(query).unique().scalars().all()
        return list(results)

    @staticmethod
    def get_best_prices_by_product(
        session: Session, product_ids: Optional[List[int]] = None
    ) -> Dict[int, Quote]:
        """
        Get the best (lowest) price quote for each product.

        Args:
            session: Database session
            product_ids: Optional list of product IDs to filter

        Returns:
            Dict mapping product_id to the Quote with lowest value
        """
        from sqlalchemy import func as sqlfunc

        # Subquery to get minimum price per product
        min_price_query = select(
            Quote.product_id, sqlfunc.min(Quote.value).label("min_value")
        ).group_by(Quote.product_id)

        if product_ids:
            min_price_query = min_price_query.where(Quote.product_id.in_(product_ids))

        min_price_subquery = min_price_query.subquery()

        # Main query to get the actual quotes
        query = (
            select(Quote)
            .options(
                joinedload(Quote.vendor),
                joinedload(Quote.product).joinedload(Product.brand),
            )
            .join(
                min_price_subquery,
                (Quote.product_id == min_price_subquery.c.product_id)
                & (Quote.value == min_price_subquery.c.min_value),
            )
        )

        results = session.execute(query).unique().scalars().all()

        # Build dict, handling ties by keeping first encountered
        best_prices: Dict[int, Quote] = {}
        for quote in results:
            if quote.product_id not in best_prices:
                best_prices[quote.product_id] = quote

        return best_prices

    @staticmethod
    def update_price(session: Session, quote_id: int, new_price: float) -> Quote:
        """
        Update a quote's price and record the change in history.

        Args:
            session: Database session
            quote_id: Quote ID
            new_price: New price value

        Returns:
            Updated Quote instance

        Raises:
            ValidationError: If price is invalid
            NotFoundError: If quote not found
        """
        if new_price < 0:
            raise ValidationError("Price cannot be negative")

        quote = session.get(Quote, quote_id)
        if not quote:
            raise NotFoundError(f"Quote with ID {quote_id} not found")

        old_value = quote.value
        try:
            quote.value = new_price
            session.flush()

            # Record history
            history = QuoteHistory(
                quote_id=quote.id,
                old_value=old_value,
                new_value=new_price,
                change_type="update",
            )
            session.add(history)
            session.commit()

            logger.info(f"Updated quote {quote_id} price: {old_value} -> {new_price}")
            return quote
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to update quote price: {e}")
            raise ServiceError(f"Failed to update quote price: {e}") from e

    @staticmethod
    def set_status(session: Session, quote_id: int, status: str) -> Quote:
        """
        Set the status of a quote.

        ``ordered`` and ``received`` also ensure a purchase order for the
        quote, which is what ``buylog spend`` counts. ``considering`` puts
        the quote on the Considering purchase list. That list membership is
        what "might buy" means.

        Args:
            session: Database session
            quote_id: Quote ID
            status: Status value (considering, ordered, received)

        Returns:
            Updated Quote instance

        Raises:
            ValidationError: If status is invalid
            NotFoundError: If quote not found
        """
        status = status.lower()
        if status not in QUOTE_STATUSES:
            raise ValidationError(
                f"Invalid status '{status}'. Must be one of: {', '.join(QUOTE_STATUSES)}"
            )

        quote = session.get(Quote, quote_id)
        if not quote:
            raise NotFoundError(f"Quote with ID {quote_id} not found")

        if status == QUOTE_STATUS_CONSIDERING:
            active = PurchaseOrderService.active_for_quote(session, quote.id)
            if active is not None:
                raise ValidationError(
                    f"Quote {quote_id} was bought on order '{active.po_number}'. "
                    "Cancel that order before marking the quote considering."
                )

        try:
            # Status is derived from these two records, so write one of them.
            if status == QUOTE_STATUS_CONSIDERING:
                PurchaseListService.ensure_considering(session, quote)
            else:
                PurchaseOrderService.ensure_for_quote(session, quote, status)
            logger.info(f"Set status '{status}' for quote {quote_id}")
            return quote
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to set quote status: {e}")
            raise ServiceError(f"Failed to set quote status: {e}") from e

    @staticmethod
    def get_by_status(session: Session, status: str) -> List[Quote]:
        """
        Get all quotes with a specific status.

        Args:
            session: Database session
            status: Status to filter by

        Returns:
            List of Quote instances
        """
        status = status.lower()
        if status not in QUOTE_STATUSES:
            raise ValidationError(
                f"Invalid status '{status}'. Must be one of: {', '.join(QUOTE_STATUSES)}"
            )

        results = (
            session.execute(
                select(Quote)
                .options(
                    joinedload(Quote.vendor),
                    joinedload(Quote.product).joinedload(Product.brand),
                    *QUOTE_STATUS_LOAD,
                )
                .where(Quote.status == status)
                .order_by(Quote.created_at.desc())
            )
            .unique()
            .scalars()
            .all()
        )
        return list(results)

    @staticmethod
    def get_by_id(session: Session, quote_id: int) -> Optional[Quote]:
        """Get a quote by ID, with vendor, product and brand eagerly loaded."""
        return (
            session.execute(
                select(Quote)
                .options(
                    joinedload(Quote.vendor),
                    joinedload(Quote.product).joinedload(Product.brand),
                )
                .where(Quote.id == quote_id)
            )
            .unique()
            .scalar_one_or_none()
        )

    @staticmethod
    def update(
        session: Session,
        quote_id: int,
        price: Optional[float] = None,
        shipping_cost: Optional[float] = None,
        tax_rate: Optional[float] = None,
        discount: Optional[float] = None,
        price_currency: Optional[str] = None,
    ) -> Quote:
        """
        Update a quote.

        A ``price`` is normalized to the base the same way ``create`` does, and the
        change is recorded in the quote's price history.

        Args:
            session: Database session
            quote_id: Quote ID
            price: Optional new price
            shipping_cost: Optional new shipping cost, in the same currency
                           as ``price``
            tax_rate: Optional new tax rate, as a percentage
            discount: Optional new discount, as a percentage
            price_currency: Currency of ``price`` (default: vendor's currency).
                            A re-scraped page may quote a different currency
                            than the vendor's default.

        Returns:
            Updated Quote instance

        Raises:
            NotFoundError: If quote not found
            ValidationError: If a new value is invalid
        """
        quote = session.get(Quote, quote_id)
        if not quote:
            raise NotFoundError(f"Quote with ID {quote_id} not found")

        if price is not None and price < 0:
            raise ValidationError("Price cannot be negative")
        if shipping_cost is not None and shipping_cost < 0:
            raise ValidationError("Shipping cost cannot be negative")
        if tax_rate is not None and tax_rate < 0:
            raise ValidationError("Tax rate cannot be negative")
        if discount is not None and not 0 <= discount <= 100:
            raise ValidationError("Discount must be between 0 and 100")
        old_value = quote.value
        new_value = old_value
        original_value: Optional[float] = quote.original_value
        original_currency: Optional[str] = quote.original_currency
        if price is not None:
            new_value, original_value, original_currency = ForexService.to_base(
                session, price, price_currency or quote.vendor.currency
            )

        if shipping_cost is not None:
            shipping_cost, _, _ = ForexService.to_base(
                session, shipping_cost, price_currency or quote.vendor.currency
            )

        with commit_or_rollback(session, f"update quote {quote_id}"):
            if price is not None:
                quote.value = new_value
                quote.original_value = original_value
                quote.original_currency = original_currency
            if shipping_cost is not None:
                quote.shipping_cost = shipping_cost
            if tax_rate is not None:
                quote.tax_rate = tax_rate
            if discount is not None:
                quote.discount = discount

        if price is not None and new_value != old_value:
            QuoteHistoryService.record_change(
                session, quote, old_value, new_value, "update"
            )

        logger.info(f"Updated quote {quote_id}")
        return quote

    @staticmethod
    def delete(session: Session, quote_id: int) -> None:
        """
        Delete a quote and its price history.

        Args:
            session: Database session
            quote_id: Quote ID

        Raises:
            NotFoundError: If quote not found
        """
        quote = session.get(Quote, quote_id)
        if not quote:
            raise NotFoundError(f"Quote with ID {quote_id} not found")

        with commit_or_rollback(session, f"delete quote {quote_id}"):
            # A purchase order's link back to the quote it came from is
            # optional, so detach rather than block the delete - the order
            # keeps its own recorded prices.
            for po in (
                session.execute(
                    select(PurchaseOrder).where(PurchaseOrder.quote_id == quote_id)
                )
                .scalars()
                .all()
            ):
                po.quote_id = None
            session.flush()
            session.delete(quote)

        logger.info(f"Deleted quote {quote_id}")


class ForexService:
    """Service for forex rate management"""

    @staticmethod
    def create(
        session: Session,
        code: str,
        base_per_unit: float,
        date: Optional[datetime.date] = None,
    ) -> Forex:
        """
        Create a new forex rate.

        Args:
            session: Database session
            code: Currency code (ISO 4217)
            base_per_unit: Units of the base currency per unit of ``code``
            date: Date for rate (defaults to today)

        Returns:
            Created Forex instance

        Raises:
            ValidationError: If input is invalid
            DuplicateError: If rate already exists for code and date
        """
        # Validate input
        code = code.strip().upper()
        if len(code) != 3:
            raise ValidationError("Currency code must be 3 characters (ISO 4217)")
        if base_per_unit <= 0:
            raise ValidationError("Exchange rate must be positive")
        base = get_base_currency(session)
        if code == base:
            raise ValidationError(f"{code} is the base currency; it needs no rate")

        # Default to today
        if date is None:
            date = datetime.date.today()

        # Check for duplicate
        existing = session.execute(
            select(Forex).where(Forex.code == code, Forex.date == date)
        ).scalar_one_or_none()

        if existing:
            raise DuplicateError(f"Forex rate for '{code}' on {date} already exists")

        # Create forex rate
        try:
            fx = Forex(code=code, base_per_unit=base_per_unit, date=date)
            session.add(fx)
            session.commit()
            logger.info(
                f"Created forex rate: {code} = {base_per_unit} {base} per unit on {date}"
            )
            return fx
        except IntegrityError as e:
            session.rollback()
            logger.error(f"Failed to create forex rate '{code}': {e}")
            raise DuplicateError(
                f"Forex rate for '{code}' on {date} already exists"
            ) from e
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error creating forex rate '{code}': {e}")
            raise ServiceError(f"Failed to create forex rate: {e}") from e

    @staticmethod
    def get_latest(session: Session, code: str) -> Optional[Forex]:
        """
        Get the latest forex rate for a currency.

        Args:
            session: Database session
            code: Currency code

        Returns:
            Latest Forex instance or None if not found
        """
        return session.execute(
            select(Forex)
            .where(Forex.code == code.upper())
            .order_by(Forex.date.desc())
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def to_base(
        session: Session, amount: float, currency: Optional[str]
    ) -> tuple[float, Optional[float], Optional[str]]:
        """
        Normalize an amount to the base currency using the latest rate on file.

        This is the single place currency normalization happens; the CLI, the
        TUI, quote creation and import all route through it so they cannot
        drift apart.

        Args:
            session: Database session
            amount: Amount in ``currency``
            currency: ISO 4217 code of ``amount`` (``None`` means the base)

        Returns:
            ``(base_value, original_value, original_currency)``. The original
            pair is ``(None, None)`` when the amount was already in the base.

        Raises:
            ValidationError: If no rate is on file for ``currency``
        """
        base = get_base_currency(session)
        code = (currency or base).strip().upper()
        if code == base:
            return amount, None, None

        rate = ForexService.get_latest(session, code)
        if rate is None:
            raise ValidationError(
                f"Forex rate for '{code}' not found. "
                f"Add it with `buylog fx add {code} RATE` or `buylog fx refresh`."
            )
        return amount * rate.base_per_unit, amount, code


class QuoteHistoryService:
    """Service for quote history tracking"""

    @staticmethod
    def record_change(
        session: Session,
        quote: Quote,
        old_value: Optional[float],
        new_value: float,
        change_type: str = "update",
    ) -> QuoteHistory:
        """
        Record a price change for a quote.

        Args:
            session: Database session
            quote: Quote instance
            old_value: Previous price (None for new quotes)
            new_value: New price
            change_type: "create" or "update"

        Returns:
            Created QuoteHistory instance
        """
        try:
            history = QuoteHistory(
                quote_id=quote.id,
                old_value=old_value,
                new_value=new_value,
                change_type=change_type,
            )
            session.add(history)
            session.commit()
            logger.info(
                f"Recorded price change for quote {quote.id}: {old_value} -> {new_value}"
            )
            return history
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to record price history: {e}")
            raise ServiceError(f"Failed to record price history: {e}") from e

    @staticmethod
    def get_history(session: Session, quote_id: int) -> List[QuoteHistory]:
        """
        Get price history for a specific quote.

        Args:
            session: Database session
            quote_id: Quote ID

        Returns:
            List of QuoteHistory entries ordered by changed_at descending, then id descending
        """
        results = (
            session.execute(
                select(QuoteHistory)
                .where(QuoteHistory.quote_id == quote_id)
                .order_by(QuoteHistory.changed_at.desc(), QuoteHistory.id.desc())
            )
            .scalars()
            .all()
        )
        return list(results)

    @staticmethod
    def get_product_history(session: Session, product_id: int) -> List[QuoteHistory]:
        """
        Get all price changes for a product across all vendors.

        Args:
            session: Database session
            product_id: Product ID

        Returns:
            List of QuoteHistory entries ordered by changed_at descending, then id descending
        """
        results = (
            session.execute(
                select(QuoteHistory)
                .join(Quote)
                .where(Quote.product_id == product_id)
                .order_by(QuoteHistory.changed_at.desc(), QuoteHistory.id.desc())
            )
            .scalars()
            .all()
        )
        return list(results)

    @staticmethod
    def compute_trend(history: List[QuoteHistory]) -> str:
        """
        Compute price trend from history.

        Args:
            history: List of QuoteHistory entries (newest first)

        Returns:
            "up", "down", "stable", or "new"
        """
        if not history:
            return "new"

        if len(history) < 2:
            if history[0].change_type == "create":
                return "new"
            return "stable"

        latest = history[0].new_value
        previous = history[1].new_value

        if latest > previous:
            return "up"
        elif latest < previous:
            return "down"
        else:
            return "stable"


class PriceTargetService:
    """
    Price targets: a threshold per product that fires once when met.

    One record for what used to be a price alert and a watchlist target.
    """

    @staticmethod
    def _currency(session: Session, code: Optional[str]) -> str:
        code = (code or get_base_currency(session)).strip().upper()
        if len(code) != 3:
            raise ValidationError("Currency code must be 3 characters (ISO 4217)")
        return code

    @staticmethod
    def create(
        session: Session,
        product_name: str,
        threshold: float,
        currency: Optional[str] = None,
    ) -> PriceTarget:
        """
        Create a price target.

        Args:
            session: Database session
            product_name: Product name
            threshold: Price at or below which the target fires
            currency: Currency of ``threshold`` (default: the base currency)

        Returns:
            Created PriceTarget instance

        Raises:
            ValidationError: If the threshold or currency is invalid
            NotFoundError: If the product is not found
            DuplicateError: If the same active target already exists
        """
        if threshold <= 0:
            raise ValidationError("Threshold must be positive")
        code = PriceTargetService._currency(session, currency)

        product = Product.by_name(session, product_name)
        if not product:
            raise NotFoundError(f"Product '{product_name}' not found")

        existing = session.execute(
            select(PriceTarget)
            .where(PriceTarget.product_id == product.id)
            .where(PriceTarget.threshold == threshold)
            .where(PriceTarget.currency == code)
            .where(PriceTarget.active == 1)
        ).first()
        if existing:
            raise DuplicateError(
                f"'{product_name}' already has an active target at {threshold:.2f} {code}"
            )

        try:
            target = PriceTarget(
                product_id=product.id, threshold=threshold, currency=code
            )
            session.add(target)
            session.commit()
            logger.info(f"Created price target for {product_name} at {target.label}")
            return target
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to create price target: {e}")
            raise ServiceError(f"Failed to create price target: {e}") from e

    @staticmethod
    def check(
        session: Session, product: Product, current_price: float
    ) -> List[PriceTarget]:
        """
        Fire the product's armed targets that ``current_price`` meets.

        Args:
            session: Database session
            product: Product instance
            current_price: Current price in the base currency

        Returns:
            Targets that fired now. Each fires once, until it is edited.
        """
        targets = (
            session.execute(
                select(PriceTarget)
                .where(PriceTarget.product_id == product.id)
                .where(PriceTarget.active == 1)
                .where(PriceTarget.triggered_at.is_(None))
            )
            .scalars()
            .all()
        )

        fired = []
        for target in targets:
            threshold_base = PriceTargetService._threshold_base(session, target)
            if threshold_base is None:
                logger.warning(
                    f"Skipping target {target.id} for product {product.name}: "
                    f"no forex rate for '{target.currency}'"
                )
                continue

            if current_price <= threshold_base:
                target.triggered_at = datetime.datetime.now()
                fired.append(target)
                logger.info(
                    f"Target met for {product.name}: {current_price} <= "
                    f"{target.label} ({threshold_base} in the base currency)"
                )

        if fired:
            session.commit()

        return fired

    @staticmethod
    def _threshold_base(session: Session, target: PriceTarget) -> Optional[float]:
        """The threshold in the base currency, or None if its currency has no rate."""
        try:
            return ForexService.to_base(session, target.threshold, target.currency)[0]
        except ValidationError:
            return None

    @staticmethod
    def met_now(session: Session) -> List[PriceTarget]:
        """
        Active targets that the current best price meets, fired or not.

        Read-only. Only ``check`` sets ``triggered_at``, so that field means
        the user was notified. Views use this to show a target that ``check``
        has not reported yet.
        """
        best_prices = QuoteService.get_best_prices_by_product(session)
        met = []
        for target in PriceTargetService.get_all(session, active_only=True):
            best = best_prices.get(target.product_id)
            if best is None:
                continue
            threshold_base = PriceTargetService._threshold_base(session, target)
            if threshold_base is not None and best.value <= threshold_base:
                met.append(target)
        return met

    @staticmethod
    def get_all(session: Session, active_only: bool = False) -> List[PriceTarget]:
        """Get targets, newest first, optionally only active ones."""
        query = (
            select(PriceTarget)
            .options(joinedload(PriceTarget.product).joinedload(Product.brand))
            .order_by(PriceTarget.created_at.desc(), PriceTarget.id.desc())
        )
        if active_only:
            query = query.where(PriceTarget.active == 1)
        return list(session.execute(query).unique().scalars().all())

    @staticmethod
    def get_triggered(session: Session) -> List[PriceTarget]:
        """Get targets that have fired, most recent first."""
        results = (
            session.execute(
                select(PriceTarget)
                .options(joinedload(PriceTarget.product))
                .where(PriceTarget.triggered_at.isnot(None))
                .order_by(PriceTarget.triggered_at.desc())
            )
            .unique()
            .scalars()
            .all()
        )
        return list(results)

    @staticmethod
    def _get(session: Session, target_id: int) -> PriceTarget:
        target = session.get(PriceTarget, target_id)
        if not target:
            raise NotFoundError(f"Price target {target_id} not found")
        return target

    @staticmethod
    def update(
        session: Session,
        target_id: int,
        threshold: Optional[float] = None,
        currency: Optional[str] = None,
    ) -> PriceTarget:
        """
        Change a target's threshold or currency, and re-arm it.

        Raises:
            ValidationError: If nothing is given, or a value is invalid
            NotFoundError: If the target is not found
        """
        if threshold is None and currency is None:
            raise ValidationError("Nothing to update: give a threshold or currency")
        if threshold is not None and threshold <= 0:
            raise ValidationError("Threshold must be positive")
        target = PriceTargetService._get(session, target_id)

        try:
            if threshold is not None:
                target.threshold = threshold
            if currency is not None:
                target.currency = PriceTargetService._currency(session, currency)
            # A changed threshold is a new question, so it may fire again.
            target.triggered_at = None
            session.commit()
            logger.info(f"Updated price target {target_id} to {target.label}")
            return target
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to update price target: {e}")
            raise ServiceError(f"Failed to update price target: {e}") from e

    @staticmethod
    def deactivate(session: Session, target_id: int) -> PriceTarget:
        """Stop checking a target without deleting it."""
        target = PriceTargetService._get(session, target_id)
        try:
            target.active = 0
            session.commit()
            logger.info(f"Deactivated price target {target_id}")
            return target
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to deactivate price target: {e}")
            raise ServiceError(f"Failed to deactivate price target: {e}") from e

    @staticmethod
    def delete(session: Session, target_id: int) -> None:
        """Delete a target."""
        target = PriceTargetService._get(session, target_id)
        try:
            session.delete(target)
            session.commit()
            logger.info(f"Deleted price target {target_id}")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to delete price target: {e}")
            raise ServiceError(f"Failed to delete price target: {e}") from e


class SpecificationService:
    """Service for specification template management"""

    @staticmethod
    def create(
        session: Session,
        name: str,
        description: Optional[str] = None,
    ) -> Specification:
        """
        Create a new specification template.

        Args:
            session: Database session
            name: Specification name
            description: Optional description

        Returns:
            Created Specification instance

        Raises:
            ValidationError: If name is invalid
            DuplicateError: If specification already exists
        """
        name = name.strip()
        if not name:
            raise ValidationError("Specification name cannot be empty")
        if len(name) > 255:
            raise ValidationError("Specification name too long (max 255 characters)")

        existing = Specification.by_name(session, name)
        if existing:
            raise DuplicateError(f"Specification '{name}' already exists")

        try:
            spec = Specification(name=name, description=description)
            session.add(spec)
            session.commit()
            logger.info(f"Created specification: {name}")
            return spec
        except IntegrityError as e:
            session.rollback()
            logger.error(f"Failed to create specification '{name}': {e}")
            raise DuplicateError(f"Specification '{name}' already exists") from e
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error creating specification '{name}': {e}")
            raise ServiceError(f"Failed to create specification: {e}") from e

    @staticmethod
    def get_by_name(session: Session, name: str) -> Optional[Specification]:
        """Get a specification by name."""
        return Specification.by_name(session, name)

    @staticmethod
    def get_all(
        session: Session,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Specification]:
        """Get all specifications with pagination."""
        query = (
            select(Specification)
            .options(joinedload(Specification.features))
            .limit(limit)
            .offset(offset)
        )
        results = session.execute(query).unique().scalars().all()
        return list(results)

    @staticmethod
    def add_feature(
        session: Session,
        spec_name: str,
        feature_name: str,
        data_type: str = "text",
        unit: Optional[str] = None,
        is_required: bool = False,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
    ) -> SpecificationFeature:
        """
        Add a feature to a specification.

        Args:
            session: Database session
            spec_name: Specification name
            feature_name: Feature name
            data_type: Data type (text, number, boolean)
            unit: Optional unit (e.g., "mm", "kg")
            is_required: Whether the feature is required
            min_value: Optional minimum value (for number type)
            max_value: Optional maximum value (for number type)

        Returns:
            Created SpecificationFeature instance

        Raises:
            ValidationError: If input is invalid
            NotFoundError: If specification not found
        """
        if not feature_name or not feature_name.strip():
            raise ValidationError("Feature name cannot be empty")

        if data_type not in SPEC_DATA_TYPES:
            raise ValidationError(
                f"Invalid data type '{data_type}'. Must be one of: {', '.join(SPEC_DATA_TYPES)}"
            )

        if min_value is not None and max_value is not None:
            if min_value > max_value:
                raise ValidationError("min_value cannot be greater than max_value")

        spec = Specification.by_name(session, spec_name)
        if not spec:
            raise NotFoundError(f"Specification '{spec_name}' not found")

        try:
            feature = SpecificationFeature(
                specification=spec,
                name=feature_name.strip(),
                data_type=data_type,
                unit=unit,
                is_required=1 if is_required else 0,
                min_value=min_value,
                max_value=max_value,
            )
            session.add(feature)
            session.commit()
            logger.info(
                f"Added feature '{feature_name}' to specification '{spec_name}'"
            )
            return feature
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to add feature: {e}")
            raise ServiceError(f"Failed to add feature: {e}") from e

    @staticmethod
    def delete(session: Session, name: str) -> None:
        """
        Delete a specification.

        Args:
            session: Database session
            name: Specification name

        Raises:
            NotFoundError: If specification not found
        """
        spec = Specification.by_name(session, name)
        if not spec:
            raise NotFoundError(f"Specification '{name}' not found")

        try:
            session.delete(spec)
            session.commit()
            logger.info(f"Deleted specification: {name}")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to delete specification '{name}': {e}")
            raise ServiceError(f"Failed to delete specification: {e}") from e


class SpecificationFeatureService:
    """Service for managing specification features"""

    @staticmethod
    def update(
        session: Session,
        feature_id: int,
        name: Optional[str] = None,
        data_type: Optional[str] = None,
        unit: Optional[str] = None,
        is_required: Optional[bool] = None,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
    ) -> SpecificationFeature:
        """
        Update a specification feature.

        Args:
            session: Database session
            feature_id: Feature ID
            name: New name (optional)
            data_type: New data type (optional)
            unit: New unit (optional)
            is_required: New required flag (optional)
            min_value: New min value (optional)
            max_value: New max value (optional)

        Returns:
            Updated SpecificationFeature instance

        Raises:
            NotFoundError: If feature not found
            ValidationError: If input is invalid
        """
        feature = session.get(SpecificationFeature, feature_id)
        if not feature:
            raise NotFoundError(f"Feature with ID {feature_id} not found")

        if name is not None:
            if not name.strip():
                raise ValidationError("Feature name cannot be empty")
            feature.name = name.strip()

        if data_type is not None:
            if data_type not in SPEC_DATA_TYPES:
                raise ValidationError(
                    f"Invalid data type '{data_type}'. Must be one of: {', '.join(SPEC_DATA_TYPES)}"
                )
            feature.data_type = data_type

        if unit is not None:
            feature.unit = unit

        if is_required is not None:
            feature.is_required = 1 if is_required else 0

        if min_value is not None:
            feature.min_value = min_value

        if max_value is not None:
            feature.max_value = max_value

        # Validate min/max relationship
        if feature.min_value is not None and feature.max_value is not None:
            if feature.min_value > feature.max_value:
                raise ValidationError("min_value cannot be greater than max_value")

        try:
            session.commit()
            logger.info(f"Updated feature {feature_id}")
            return feature
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to update feature: {e}")
            raise ServiceError(f"Failed to update feature: {e}") from e

    @staticmethod
    def delete(session: Session, feature_id: int) -> None:
        """
        Delete a specification feature.

        Args:
            session: Database session
            feature_id: Feature ID

        Raises:
            NotFoundError: If feature not found
        """
        feature = session.get(SpecificationFeature, feature_id)
        if not feature:
            raise NotFoundError(f"Feature with ID {feature_id} not found")

        try:
            session.delete(feature)
            session.commit()
            logger.info(f"Deleted feature {feature_id}")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to delete feature: {e}")
            raise ServiceError(f"Failed to delete feature: {e}") from e


class ProductFeatureService:
    """Service for managing product feature values"""

    @staticmethod
    def set_value(
        session: Session,
        product_name: str,
        feature_id: int,
        value: Any,
    ) -> ProductFeature:
        """
        Set a feature value for a product.

        Args:
            session: Database session
            product_name: Product name
            feature_id: SpecificationFeature ID
            value: Value to set (type depends on feature data_type)

        Returns:
            Created or updated ProductFeature instance

        Raises:
            NotFoundError: If product or feature not found
            ValidationError: If value doesn't match feature data type or constraints
        """
        product = Product.by_name(session, product_name)
        if not product:
            raise NotFoundError(f"Product '{product_name}' not found")

        feature = session.get(SpecificationFeature, feature_id)
        if not feature:
            raise NotFoundError(f"Feature with ID {feature_id} not found")

        # Validate value against data type
        value_text = None
        value_number = None
        value_boolean = None

        if feature.data_type == "text":
            if value is not None and not isinstance(value, str):
                raise ValidationError(f"Feature '{feature.name}' requires a text value")
            value_text = value
        elif feature.data_type == "number":
            if value is not None:
                try:
                    value_number = float(value)
                except (TypeError, ValueError):
                    raise ValidationError(
                        f"Feature '{feature.name}' requires a numeric value"
                    )

                # Validate min/max
                if feature.min_value is not None and value_number < feature.min_value:
                    raise ValidationError(
                        f"Value {value_number} is below minimum {feature.min_value}"
                    )
                if feature.max_value is not None and value_number > feature.max_value:
                    raise ValidationError(
                        f"Value {value_number} exceeds maximum {feature.max_value}"
                    )
        elif feature.data_type == "boolean":
            if value is not None:
                if isinstance(value, bool):
                    value_boolean = 1 if value else 0
                elif isinstance(value, int):
                    value_boolean = 1 if value else 0
                else:
                    raise ValidationError(
                        f"Feature '{feature.name}' requires a boolean value"
                    )

        try:
            # Check for existing value
            existing = session.execute(
                select(ProductFeature).where(
                    ProductFeature.product_id == product.id,
                    ProductFeature.specification_feature_id == feature_id,
                )
            ).scalar_one_or_none()

            if existing:
                existing.value_text = value_text
                existing.value_number = value_number
                existing.value_boolean = value_boolean
                pf = existing
            else:
                pf = ProductFeature(
                    product=product,
                    specification_feature=feature,
                    value_text=value_text,
                    value_number=value_number,
                    value_boolean=value_boolean,
                )
                session.add(pf)

            session.commit()
            logger.info(f"Set feature '{feature.name}' for product '{product_name}'")
            return pf
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to set feature value: {e}")
            raise ServiceError(f"Failed to set feature value: {e}") from e

    @staticmethod
    def get_features(session: Session, product_name: str) -> List[ProductFeature]:
        """
        Get all feature values for a product.

        Args:
            session: Database session
            product_name: Product name

        Returns:
            List of ProductFeature instances

        Raises:
            NotFoundError: If product not found
        """
        product = Product.by_name(session, product_name)
        if not product:
            raise NotFoundError(f"Product '{product_name}' not found")

        results = (
            session.execute(
                select(ProductFeature)
                .options(joinedload(ProductFeature.specification_feature))
                .where(ProductFeature.product_id == product.id)
            )
            .unique()
            .scalars()
            .all()
        )
        return list(results)

    @staticmethod
    def validate_required(session: Session, product_name: str) -> List[str]:
        """
        Validate that all required features have values for a product.

        Args:
            session: Database session
            product_name: Product name

        Returns:
            List of missing required feature names

        Raises:
            NotFoundError: If product not found
        """
        product = Product.by_name(session, product_name)
        if not product:
            raise NotFoundError(f"Product '{product_name}' not found")

        if not product.specification:
            return []

        # Get all required features for the specification
        required_features = (
            session.execute(
                select(SpecificationFeature).where(
                    SpecificationFeature.specification_id == product.specification_id,
                    SpecificationFeature.is_required == 1,
                )
            )
            .scalars()
            .all()
        )

        # Get existing feature values for the product
        existing_feature_ids = {
            pf.specification_feature_id
            for pf in session.execute(
                select(ProductFeature).where(ProductFeature.product_id == product.id)
            )
            .scalars()
            .all()
        }

        missing = []
        for feature in required_features:
            if feature.id not in existing_feature_ids:
                missing.append(feature.name)

        return missing


class PurchaseOrderService:
    """Service for purchase order management"""

    # How far along an order is. A quote status only moves an existing
    # order forward: shipped is not pulled back to ordered.
    _STATUS_RANK = {
        PO_STATUS_PENDING: 0,
        PO_STATUS_APPROVED: 1,
        PO_STATUS_ORDERED: 2,
        PO_STATUS_SHIPPED: 3,
        PO_STATUS_RECEIVED: 4,
    }

    @staticmethod
    def create(
        session: Session,
        po_number: str,
        vendor_name: str,
        product_name: str,
        unit_price: float,
        quantity: int = 1,
        currency: Optional[str] = None,
        quote_id: Optional[int] = None,
        order_date: Optional[datetime.date] = None,
        expected_delivery: Optional[datetime.date] = None,
        shipping_cost: Optional[float] = None,
        tax: Optional[float] = None,
        invoice_number: Optional[str] = None,
        notes: Optional[str] = None,
        status: str = PO_STATUS_PENDING,
    ) -> PurchaseOrder:
        """
        Create a new purchase order.

        Args:
            session: Database session
            po_number: Unique PO number
            vendor_name: Vendor name
            product_name: Product name
            unit_price: Price per unit, in ``currency``
            quantity: Number of units (default: 1)
            currency: Currency code (defaults to the vendor's currency)
            quote_id: Optional associated quote ID
            order_date: Optional order date
            expected_delivery: Optional expected delivery date
            shipping_cost: Optional shipping cost
            tax: Optional tax amount
            invoice_number: Optional invoice number
            notes: Optional notes
            status: Initial status (default: pending)

        Returns:
            Created PurchaseOrder instance

        Raises:
            ValidationError: If input is invalid
            DuplicateError: If PO number already exists
            NotFoundError: If vendor or product not found
        """
        # Validate input
        po_number = po_number.strip()
        if not po_number:
            raise ValidationError("PO number cannot be empty")

        if status not in PO_STATUSES:
            raise ValidationError(
                f"Invalid status '{status}'. Must be one of: {', '.join(PO_STATUSES)}"
            )

        if unit_price < 0:
            raise ValidationError("Unit price cannot be negative")

        if quantity < 1:
            raise ValidationError("Quantity must be at least 1")

        # Get vendor
        vendor = Vendor.by_name(session, vendor_name)
        if not vendor:
            raise NotFoundError(f"Vendor '{vendor_name}' not found")

        # Get product
        product = Product.by_name(session, product_name)
        if not product:
            raise NotFoundError(f"Product '{product_name}' not found")

        # Check for duplicate PO number
        existing = session.execute(
            select(PurchaseOrder).where(PurchaseOrder.po_number == po_number)
        ).scalar_one_or_none()
        if existing:
            raise DuplicateError(f"PO number '{po_number}' already exists")

        # An order is priced in the vendor's currency unless told otherwise;
        # defaulting to USD used to label a EUR vendor's prices as USD.
        currency = (
            (currency or vendor.currency or get_base_currency(session)).strip().upper()
        )

        # Compute totals
        total_amount = unit_price * quantity
        grand_total = total_amount + (shipping_cost or 0.0) + (tax or 0.0)
        grand_total_base, _, _ = ForexService.to_base(session, grand_total, currency)

        try:
            po = PurchaseOrder(
                po_number=po_number,
                vendor_id=vendor.id,
                product_id=product.id,
                quote_id=quote_id,
                unit_price=unit_price,
                quantity=quantity,
                currency=currency,
                total_amount=total_amount,
                shipping_cost=shipping_cost,
                tax=tax,
                grand_total=grand_total,
                grand_total_base=grand_total_base,
                order_date=order_date,
                expected_delivery=expected_delivery,
                invoice_number=invoice_number,
                notes=notes,
                status=status,
                # Same rule as update_status: received means delivered today.
                actual_delivery=datetime.date.today()
                if status == PO_STATUS_RECEIVED
                else None,
            )
            session.add(po)
            session.commit()
            logger.info(f"Created purchase order: {po_number}")
            return po
        except IntegrityError as e:
            session.rollback()
            logger.error(f"Failed to create PO '{po_number}': {e}")
            raise DuplicateError(f"PO number '{po_number}' already exists") from e
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error creating PO '{po_number}': {e}")
            raise ServiceError(f"Failed to create purchase order: {e}") from e

    @staticmethod
    def create_from_quote(
        session: Session,
        po_number: str,
        quote_id: int,
        quantity: int = 1,
        order_date: Optional[datetime.date] = None,
        expected_delivery: Optional[datetime.date] = None,
        shipping_cost: Optional[float] = None,
        tax: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> PurchaseOrder:
        """
        Create a purchase order from an existing quote.

        Args:
            session: Database session
            po_number: Unique PO number
            quote_id: Quote ID to create PO from
            quantity: Number of units (default: 1)
            order_date: Optional order date
            expected_delivery: Optional expected delivery date
            shipping_cost: Optional shipping cost (overrides quote if provided)
            tax: Optional tax amount
            notes: Optional notes

        Returns:
            Created PurchaseOrder instance

        Raises:
            NotFoundError: If quote not found
            ValidationError: If input is invalid
            DuplicateError: If PO number already exists
        """
        quote = session.get(Quote, quote_id)
        if not quote:
            raise NotFoundError(f"Quote with ID {quote_id} not found")

        # Use quote values, with optional overrides
        effective_shipping = (
            shipping_cost if shipping_cost is not None else quote.shipping_cost
        )

        return PurchaseOrderService.create(
            session=session,
            po_number=po_number,
            vendor_name=quote.vendor.name,
            product_name=quote.product.name,
            unit_price=quote.value,
            quantity=quantity,
            currency=get_base_currency(session),  # quote.value is in the base
            quote_id=quote_id,
            order_date=order_date,
            expected_delivery=expected_delivery,
            shipping_cost=effective_shipping,
            tax=tax,
            notes=notes,
        )

    @staticmethod
    def _po_number_for_quote(session: Session, quote_id: int) -> str:
        """A PO number for an order recorded from a quote status change."""
        base = f"quote-{quote_id}"
        candidate = base
        suffix = 2
        while (
            session.execute(
                select(PurchaseOrder.id).where(PurchaseOrder.po_number == candidate)
            ).scalar_one_or_none()
            is not None
        ):
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    @staticmethod
    def active_for_quote(session: Session, quote_id: int) -> Optional[PurchaseOrder]:
        """The oldest purchase order for a quote that is not cancelled."""
        return (
            session.execute(
                select(PurchaseOrder)
                .where(PurchaseOrder.quote_id == quote_id)
                .where(PurchaseOrder.status != PO_STATUS_CANCELLED)
                .order_by(PurchaseOrder.id)
            )
            .scalars()
            .first()
        )

    @staticmethod
    def ensure_for_quote(session: Session, quote: Quote, status: str) -> PurchaseOrder:
        """
        Make sure this quote has one purchase order that spend can see.

        ``ordered`` and ``received`` mean the quote was bought. Spend reads
        purchase orders, so those statuses create an order when the quote
        does not already have an active one. An existing order is advanced
        when the new status is further along, and left alone otherwise, so a
        shipped order is not pulled back to ordered. The new order's total
        follows the quote's total cost: discount, shipping, and tax included.

        Args:
            session: Database session
            quote: Quote being marked ordered or received
            status: ``ordered`` or ``received``

        Returns:
            The purchase order spend will count

        Raises:
            ValidationError: If status is not a purchase
        """
        if status not in (PO_STATUS_ORDERED, PO_STATUS_RECEIVED):
            raise ValidationError(f"Cannot record a purchase for status '{status}'")

        existing = PurchaseOrderService.active_for_quote(session, quote.id)
        if existing is not None:
            current = PurchaseOrderService._STATUS_RANK.get(existing.status, 0)
            wanted = PurchaseOrderService._STATUS_RANK[status]
            if wanted > current:
                return PurchaseOrderService.update_status(
                    session, existing.po_number, status
                )
            # The quote status change is still uncommitted.
            session.commit()
            return existing

        discount = quote.discount or 0.0
        unit_price = quote.value * (1 - discount / 100.0)
        shipping = quote.shipping_cost
        tax_rate = quote.tax_rate or 0.0
        tax = (
            (unit_price + (shipping or 0.0)) * (tax_rate / 100.0) if tax_rate else None
        )

        return PurchaseOrderService.create(
            session=session,
            po_number=PurchaseOrderService._po_number_for_quote(session, quote.id),
            vendor_name=quote.vendor.name,
            product_name=quote.product.name,
            unit_price=unit_price,
            quantity=1,
            currency=get_base_currency(session),  # quote.value is in the base
            quote_id=quote.id,
            order_date=datetime.date.today(),
            shipping_cost=shipping,
            tax=tax,
            status=status,
        )

    @staticmethod
    def update_status(
        session: Session,
        po_number: str,
        new_status: str,
        actual_delivery: Optional[datetime.date] = None,
    ) -> PurchaseOrder:
        """
        Update the status of a purchase order.

        Args:
            session: Database session
            po_number: PO number
            new_status: New status
            actual_delivery: Optional actual delivery date (auto-set if status is 'received')

        Returns:
            Updated PurchaseOrder instance

        Raises:
            NotFoundError: If PO not found
            ValidationError: If status is invalid
        """
        if new_status not in PO_STATUSES:
            raise ValidationError(
                f"Invalid status '{new_status}'. Must be one of: {', '.join(PO_STATUSES)}"
            )

        po = session.execute(
            select(PurchaseOrder).where(PurchaseOrder.po_number == po_number)
        ).scalar_one_or_none()
        if not po:
            raise NotFoundError(f"Purchase order '{po_number}' not found")

        try:
            po.status = new_status

            # Auto-set actual delivery date when status is received
            if new_status == PO_STATUS_RECEIVED:
                if actual_delivery:
                    po.actual_delivery = actual_delivery
                elif not po.actual_delivery:
                    po.actual_delivery = datetime.date.today()

            session.commit()
            logger.info(f"Updated PO '{po_number}' status to '{new_status}'")
            return po
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to update PO status: {e}")
            raise ServiceError(f"Failed to update PO status: {e}") from e

    @staticmethod
    def get_by_po_number(session: Session, po_number: str) -> Optional[PurchaseOrder]:
        """Get a purchase order by PO number."""
        return (
            session.execute(
                select(PurchaseOrder)
                .options(
                    joinedload(PurchaseOrder.vendor),
                    joinedload(PurchaseOrder.product),
                    joinedload(PurchaseOrder.quote),
                )
                .where(PurchaseOrder.po_number == po_number)
            )
            .unique()
            .scalar_one_or_none()
        )

    @staticmethod
    def get_all(
        session: Session,
        status: Optional[str] = None,
        vendor_name: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[PurchaseOrder]:
        """
        Get all purchase orders with optional filtering.

        Args:
            session: Database session
            status: Optional status filter
            vendor_name: Optional vendor name filter
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of PurchaseOrder instances
        """
        query = select(PurchaseOrder).options(
            joinedload(PurchaseOrder.vendor),
            joinedload(PurchaseOrder.product),
        )

        if status:
            if status not in PO_STATUSES:
                raise ValidationError(
                    f"Invalid status '{status}'. Must be one of: {', '.join(PO_STATUSES)}"
                )
            query = query.where(PurchaseOrder.status == status)

        if vendor_name:
            vendor = Vendor.by_name(session, vendor_name)
            if vendor:
                query = query.where(PurchaseOrder.vendor_id == vendor.id)

        query = query.order_by(PurchaseOrder.created_at.desc())
        query = query.limit(limit).offset(offset)

        results = session.execute(query).unique().scalars().all()
        return list(results)


class ComparisonService:
    """Service for price comparison functionality"""

    @staticmethod
    def compare_product(session: Session, product_name: str) -> Dict[str, Any]:
        """
        Compare prices for a specific product across all vendors.

        Args:
            session: Database session
            product_name: Exact product name

        Returns:
            Dict with product info, quotes sorted by price, and statistics
        """
        product = Product.by_name(session, product_name)
        if not product:
            raise NotFoundError(f"Product '{product_name}' not found")

        quotes = (
            session.execute(
                select(Quote)
                .options(
                    joinedload(Quote.vendor),
                    joinedload(Quote.product).joinedload(Product.brand),
                )
                .where(Quote.product_id == product.id)
                .order_by(Quote.value.asc())
            )
            .unique()
            .scalars()
            .all()
        )

        if not quotes:
            return {
                "product": product,
                "quotes": [],
                "best_price": None,
                "worst_price": None,
                "avg_price": None,
                "price_spread": None,
                "savings": None,
            }

        prices = [q.value for q in quotes]
        best = min(prices)
        worst = max(prices)
        avg = sum(prices) / len(prices)

        return {
            "product": product,
            "quotes": list(quotes),
            "best_price": best,
            "worst_price": worst,
            "avg_price": avg,
            "price_spread": worst - best,
            "savings": worst - best,
            "num_vendors": len(quotes),
        }

    @staticmethod
    def compare_by_search(session: Session, search_term: str) -> Dict[str, Any]:
        """
        Compare prices for products matching a search term.

        Args:
            session: Database session
            search_term: Partial product name to search

        Returns:
            Dict with matching products and their comparison data
        """
        # Find matching products
        products = (
            session.execute(
                select(Product)
                .options(joinedload(Product.brand))
                .where(Product.name.ilike(f"%{search_term}%"))
                .order_by(Product.name)
            )
            .unique()
            .scalars()
            .all()
        )

        if not products:
            raise NotFoundError(f"No products found matching '{search_term}'")

        comparisons = []
        for product in products:
            comparison = ComparisonService.compare_product(session, product.name)
            if comparison["quotes"]:  # Only include products with quotes
                comparisons.append(comparison)

        # Sort by best price
        comparisons.sort(key=lambda c: c["best_price"] or float("inf"))

        return {
            "search_term": search_term,
            "products": comparisons,
            "total_products": len(comparisons),
        }

    @staticmethod
    def compare_by_category(session: Session, category: str) -> Dict[str, Any]:
        """
        Compare prices for all products in a category.

        Args:
            session: Database session
            category: Product category name

        Returns:
            Dict with products in category and their comparison data
        """
        needle = category.strip().lower()
        products = (
            session.execute(
                select(Product)
                .options(
                    joinedload(Product.brand),
                    joinedload(Product.specification),
                )
                .order_by(Product.name)
            )
            .unique()
            .scalars()
            .all()
        )
        tags = TagService.product_tag_names(session)
        products = [
            product
            for product in products
            if (
                label := ProductService.report_classification(
                    product, tags.get(product.id, [])
                )
            )
            and needle in label.lower()
        ]

        if not products:
            raise NotFoundError(f"No products found in category '{category}'")

        comparisons = []
        for product in products:
            comparison = ComparisonService.compare_product(session, product.name)
            if comparison["quotes"]:
                comparisons.append(comparison)

        comparisons.sort(key=lambda c: c["best_price"] or float("inf"))

        return {
            "category": category,
            "products": comparisons,
            "total_products": len(comparisons),
        }

    @staticmethod
    def compare_by_brand(session: Session, brand_name: str) -> Dict[str, Any]:
        """
        Compare prices for all products from a brand.

        Args:
            session: Database session
            brand_name: Brand name

        Returns:
            Dict with products from brand and their comparison data
        """
        brand = Brand.by_name(session, brand_name)
        if not brand:
            raise NotFoundError(f"Brand '{brand_name}' not found")

        comparisons = []
        for product in brand.products:
            comparison = ComparisonService.compare_product(session, product.name)
            if comparison["quotes"]:
                comparisons.append(comparison)

        comparisons.sort(key=lambda c: c["best_price"] or float("inf"))

        return {
            "brand": brand,
            "products": comparisons,
            "total_products": len(comparisons),
        }

    @staticmethod
    def classification_counts(session: Session) -> Dict[str, int]:
        """How many products use each report classification."""
        products = (
            session.execute(select(Product).options(joinedload(Product.specification)))
            .unique()
            .scalars()
            .all()
        )
        tags = TagService.product_tag_names(session)
        counts: Dict[str, int] = {}
        for product in products:
            label = ProductService.report_classification(
                product, tags.get(product.id, [])
            )
            if label:
                counts[label] = counts.get(label, 0) + 1
        return counts

    @staticmethod
    def get_categories(session: Session) -> List[str]:
        """
        Get all unique product classifications.

        Returns:
            List of category names
        """
        return sorted(ComparisonService.classification_counts(session))

    @staticmethod
    def set_product_category(
        session: Session, product_name: str, category: str
    ) -> Product:
        """
        Set or update a product's category.

        Args:
            session: Database session
            product_name: Product name
            category: Category to assign

        Returns:
            Updated Product instance
        """
        product = Product.by_name(session, product_name)
        if not product:
            raise NotFoundError(f"Product '{product_name}' not found")

        try:
            product.category = category
            session.commit()
            logger.info(f"Set category '{category}' for product '{product_name}'")
            return product
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to set product category: {e}")
            raise ServiceError(f"Failed to set product category: {e}") from e


# Quotes on this list are the ones you might buy. Quote status `considering`
# and `buylog purchase-list consider` are written through it.
CONSIDERING_LIST = "Considering"


class PurchaseListService:
    """Service for purchase list management"""

    @staticmethod
    def create(
        session: Session,
        name: str,
        description: Optional[str] = None,
    ) -> PurchaseList:
        """
        Create a new purchase list.

        Args:
            session: Database session
            name: List name
            description: Optional description

        Returns:
            Created PurchaseList instance
        """
        name = name.strip()
        if not name:
            raise ValidationError("Purchase list name cannot be empty")

        existing = PurchaseList.by_name(session, name)
        if existing:
            raise DuplicateError(f"Purchase list '{name}' already exists")

        try:
            purchase_list = PurchaseList(name=name, description=description)
            session.add(purchase_list)
            session.commit()
            logger.info(f"Created purchase list: {name}")
            return purchase_list
        except IntegrityError as e:
            session.rollback()
            raise DuplicateError(f"Purchase list '{name}' already exists") from e
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to create purchase list: {e}")
            raise ServiceError(f"Failed to create purchase list: {e}") from e

    @staticmethod
    def get_by_name(session: Session, name: str) -> Optional[PurchaseList]:
        """Get a purchase list by name."""
        return PurchaseList.by_name(session, name)

    @staticmethod
    def get_all(session: Session) -> List[PurchaseList]:
        """Get all purchase lists."""
        results = (
            session.execute(
                select(PurchaseList).order_by(PurchaseList.created_at.desc())
            )
            .scalars()
            .all()
        )
        return list(results)

    @staticmethod
    def add_quote(session: Session, list_name: str, quote_id: int) -> PurchaseList:
        """
        Add a quote to a purchase list.

        Args:
            session: Database session
            list_name: Purchase list name
            quote_id: Quote ID to add

        Returns:
            Updated PurchaseList instance
        """
        purchase_list = PurchaseList.by_name(session, list_name)
        if not purchase_list:
            raise NotFoundError(f"Purchase list '{list_name}' not found")

        quote = session.get(Quote, quote_id)
        if not quote:
            raise NotFoundError(f"Quote with ID {quote_id} not found")

        if quote in purchase_list.quotes:
            raise DuplicateError(f"Quote {quote_id} already in list '{list_name}'")

        try:
            purchase_list.quotes.append(quote)
            session.commit()
            logger.info(f"Added quote {quote_id} to list '{list_name}'")
            return purchase_list
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to add quote to list: {e}")
            raise ServiceError(f"Failed to add quote to list: {e}") from e

    @staticmethod
    def remove_quote(session: Session, list_name: str, quote_id: int) -> PurchaseList:
        """
        Remove a quote from a purchase list.

        Args:
            session: Database session
            list_name: Purchase list name
            quote_id: Quote ID to remove

        Returns:
            Updated PurchaseList instance
        """
        purchase_list = PurchaseList.by_name(session, list_name)
        if not purchase_list:
            raise NotFoundError(f"Purchase list '{list_name}' not found")

        quote = session.get(Quote, quote_id)
        if not quote:
            raise NotFoundError(f"Quote with ID {quote_id} not found")

        if quote not in purchase_list.quotes:
            raise NotFoundError(f"Quote {quote_id} not in list '{list_name}'")

        try:
            purchase_list.quotes.remove(quote)
            session.commit()
            logger.info(f"Removed quote {quote_id} from list '{list_name}'")
            return purchase_list
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to remove quote from list: {e}")
            raise ServiceError(f"Failed to remove quote from list: {e}") from e

    @staticmethod
    def delete(session: Session, name: str) -> None:
        """Delete a purchase list."""
        purchase_list = PurchaseList.by_name(session, name)
        if not purchase_list:
            raise NotFoundError(f"Purchase list '{name}' not found")

        try:
            session.delete(purchase_list)
            session.commit()
            logger.info(f"Deleted purchase list: {name}")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to delete purchase list: {e}")
            raise ServiceError(f"Failed to delete purchase list: {e}") from e

    @staticmethod
    def _list_named(session: Session, name: str, description: str) -> PurchaseList:
        purchase_list = PurchaseList.by_name(session, name)
        if purchase_list is not None:
            return purchase_list
        purchase_list = PurchaseList(name=name, description=description)
        session.add(purchase_list)
        session.flush()
        return purchase_list

    @staticmethod
    def ensure_considering(session: Session, quote: Quote) -> PurchaseList:
        """Put ``quote`` on the Considering list, which makes it considering."""
        purchase_list = PurchaseListService._list_named(
            session, CONSIDERING_LIST, "Quotes you might buy"
        )
        if quote not in purchase_list.quotes:
            purchase_list.quotes.append(quote)
        session.commit()
        logger.info(f"Quote {quote.id} is on '{CONSIDERING_LIST}'")
        return purchase_list

    @staticmethod
    def consider_product(session: Session, product_name: str) -> List[Quote]:
        """
        Put every quote for a product on the Considering list.

        A product you might buy is a set of quotes, so this adds them all
        rather than storing a flag on the product.
        """
        product = Product.by_name(session, product_name)
        if not product:
            raise NotFoundError(f"Product '{product_name}' not found")

        quotes = list(product.quotes)
        if not quotes:
            raise ValidationError(
                f"'{product_name}' has no quotes to consider. Add a quote first."
            )

        purchase_list = PurchaseListService._list_named(
            session, CONSIDERING_LIST, "Quotes you might buy"
        )
        for quote in quotes:
            if quote not in purchase_list.quotes:
                purchase_list.quotes.append(quote)
        session.commit()
        logger.info(
            f"Added {len(quotes)} quote(s) for '{product_name}' to '{CONSIDERING_LIST}'"
        )
        return quotes

    @staticmethod
    def might_buy(session: Session) -> List[Quote]:
        """
        Quotes you might buy: on a purchase list, and not yet ordered or received.

        This is the one answer: a quote on a list with no active order has
        the derived status ``considering``.
        """
        quotes = (
            session.execute(
                select(Quote)
                .options(
                    joinedload(Quote.vendor),
                    joinedload(Quote.product).joinedload(Product.brand),
                    joinedload(Quote.purchase_lists),
                )
                .where(Quote.status == QUOTE_STATUS_CONSIDERING)
            )
            .unique()
            .scalars()
            .all()
        )
        return sorted(quotes, key=lambda quote: (quote.product.name.lower(), quote.id))


class NoteService:
    """Service for notes management"""

    @staticmethod
    def create(
        session: Session,
        entity_type: str,
        entity_id: int,
        content: str,
    ) -> Note:
        """
        Create a note for an entity.

        Args:
            session: Database session
            entity_type: Type of entity ('product', 'vendor', 'quote', etc.)
            entity_id: ID of the entity
            content: Note content

        Returns:
            Created Note instance
        """
        content = content.strip()
        if not content:
            raise ValidationError("Note content cannot be empty")

        entity_type = entity_type.lower()
        valid_types = ["product", "vendor", "quote", "brand", "purchase_list"]
        if entity_type not in valid_types:
            raise ValidationError(
                f"Invalid entity type. Must be one of: {', '.join(valid_types)}"
            )

        try:
            note = Note(
                entity_type=entity_type,
                entity_id=entity_id,
                content=content,
            )
            session.add(note)
            session.commit()
            logger.info(f"Created note for {entity_type}:{entity_id}")
            return note
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to create note: {e}")
            raise ServiceError(f"Failed to create note: {e}") from e

    @staticmethod
    def get_for_entity(
        session: Session, entity_type: str, entity_id: int
    ) -> List[Note]:
        """
        Get all notes for an entity.

        Args:
            session: Database session
            entity_type: Type of entity
            entity_id: ID of the entity

        Returns:
            List of Note instances
        """
        results = (
            session.execute(
                select(Note)
                .where(Note.entity_type == entity_type.lower())
                .where(Note.entity_id == entity_id)
                .order_by(Note.created_at.desc())
            )
            .scalars()
            .all()
        )
        return list(results)

    @staticmethod
    def update(session: Session, note_id: int, content: str) -> Note:
        """
        Update a note's content.

        Args:
            session: Database session
            note_id: Note ID
            content: New content

        Returns:
            Updated Note instance
        """
        content = content.strip()
        if not content:
            raise ValidationError("Note content cannot be empty")

        note = session.get(Note, note_id)
        if not note:
            raise NotFoundError(f"Note with ID {note_id} not found")

        try:
            note.content = content
            note.updated_at = datetime.datetime.now()
            session.commit()
            logger.info(f"Updated note {note_id}")
            return note
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to update note: {e}")
            raise ServiceError(f"Failed to update note: {e}") from e

    @staticmethod
    def delete(session: Session, note_id: int) -> None:
        """Delete a note."""
        note = session.get(Note, note_id)
        if not note:
            raise NotFoundError(f"Note with ID {note_id} not found")

        try:
            session.delete(note)
            session.commit()
            logger.info(f"Deleted note {note_id}")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to delete note: {e}")
            raise ServiceError(f"Failed to delete note: {e}") from e


class TagService:
    """Service for tag management"""

    @staticmethod
    def create(
        session: Session,
        name: str,
        color: Optional[str] = None,
    ) -> Tag:
        """
        Create a new tag.

        Args:
            session: Database session
            name: Tag name
            color: Optional hex color for UI

        Returns:
            Created Tag instance
        """
        name = name.strip().lower()
        if not name:
            raise ValidationError("Tag name cannot be empty")

        existing = Tag.by_name(session, name)
        if existing:
            raise DuplicateError(f"Tag '{name}' already exists")

        try:
            tag = Tag(name=name, color=color)
            session.add(tag)
            session.commit()
            logger.info(f"Created tag: {name}")
            return tag
        except IntegrityError as e:
            session.rollback()
            raise DuplicateError(f"Tag '{name}' already exists") from e
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to create tag: {e}")
            raise ServiceError(f"Failed to create tag: {e}") from e

    @staticmethod
    def product_tag_names(session: Session) -> Dict[int, List[str]]:
        """Tag names keyed by product id."""
        rows = session.execute(
            select(EntityTag.entity_id, Tag.name)
            .join(Tag, Tag.id == EntityTag.tag_id)
            .where(EntityTag.entity_type == "product")
        ).all()
        grouped: Dict[int, List[str]] = {}
        for entity_id, name in rows:
            grouped.setdefault(entity_id, []).append(name)
        return grouped

    @staticmethod
    def get_all(session: Session) -> List[Tag]:
        """Get all tags."""
        results = session.execute(select(Tag).order_by(Tag.name)).scalars().all()
        return list(results)

    @staticmethod
    def add_to_entity(
        session: Session,
        tag_name: str,
        entity_type: str,
        entity_id: int,
    ) -> EntityTag:
        """
        Add a tag to an entity.

        Args:
            session: Database session
            tag_name: Tag name
            entity_type: Type of entity
            entity_id: ID of the entity

        Returns:
            Created EntityTag instance
        """
        tag = Tag.by_name(session, tag_name.lower())
        if not tag:
            # Auto-create tag if it doesn't exist
            tag = TagService.create(session, tag_name)

        entity_type = entity_type.lower()

        # Check if already tagged
        existing = session.execute(
            select(EntityTag)
            .where(EntityTag.tag_id == tag.id)
            .where(EntityTag.entity_type == entity_type)
            .where(EntityTag.entity_id == entity_id)
        ).scalar_one_or_none()

        if existing:
            raise DuplicateError(f"Entity already has tag '{tag_name}'")

        try:
            entity_tag = EntityTag(
                tag_id=tag.id,
                entity_type=entity_type,
                entity_id=entity_id,
            )
            session.add(entity_tag)
            session.commit()
            logger.info(f"Added tag '{tag_name}' to {entity_type}:{entity_id}")
            return entity_tag
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to add tag to entity: {e}")
            raise ServiceError(f"Failed to add tag to entity: {e}") from e

    @staticmethod
    def remove_from_entity(
        session: Session,
        tag_name: str,
        entity_type: str,
        entity_id: int,
    ) -> None:
        """
        Remove a tag from an entity.

        Args:
            session: Database session
            tag_name: Tag name
            entity_type: Type of entity
            entity_id: ID of the entity
        """
        tag = Tag.by_name(session, tag_name.lower())
        if not tag:
            raise NotFoundError(f"Tag '{tag_name}' not found")

        entity_type = entity_type.lower()

        entity_tag = session.execute(
            select(EntityTag)
            .where(EntityTag.tag_id == tag.id)
            .where(EntityTag.entity_type == entity_type)
            .where(EntityTag.entity_id == entity_id)
        ).scalar_one_or_none()

        if not entity_tag:
            raise NotFoundError(f"Entity does not have tag '{tag_name}'")

        try:
            session.delete(entity_tag)
            session.commit()
            logger.info(f"Removed tag '{tag_name}' from {entity_type}:{entity_id}")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to remove tag from entity: {e}")
            raise ServiceError(f"Failed to remove tag from entity: {e}") from e

    @staticmethod
    def get_for_entity(session: Session, entity_type: str, entity_id: int) -> List[Tag]:
        """
        Get all tags for an entity.

        Args:
            session: Database session
            entity_type: Type of entity
            entity_id: ID of the entity

        Returns:
            List of Tag instances
        """
        results = (
            session.execute(
                select(Tag)
                .join(EntityTag)
                .where(EntityTag.entity_type == entity_type.lower())
                .where(EntityTag.entity_id == entity_id)
                .order_by(Tag.name)
            )
            .scalars()
            .all()
        )
        return list(results)

    @staticmethod
    def get_entities_by_tag(
        session: Session, tag_name: str, entity_type: Optional[str] = None
    ) -> List[EntityTag]:
        """
        Get all entities with a specific tag.

        Args:
            session: Database session
            tag_name: Tag name
            entity_type: Optional filter by entity type

        Returns:
            List of EntityTag instances
        """
        tag = Tag.by_name(session, tag_name.lower())
        if not tag:
            raise NotFoundError(f"Tag '{tag_name}' not found")

        query = (
            select(EntityTag)
            .options(joinedload(EntityTag.tag))
            .where(EntityTag.tag_id == tag.id)
        )

        if entity_type:
            query = query.where(EntityTag.entity_type == entity_type.lower())

        results = session.execute(query).unique().scalars().all()
        return list(results)


class ImportService:
    """Service for importing data from CSV/JSON files."""

    @staticmethod
    def import_quotes_csv(
        session: Session,
        file_path: str | Path,
        create_missing: bool = True,
    ) -> Dict[str, Any]:
        """
        Import quotes from a CSV file.

        Expected CSV columns: vendor, product, brand, price, currency, shipping, tax_rate

        Args:
            session: Database session
            file_path: Path to CSV file
            create_missing: If True, create missing vendors/products/brands

        Returns:
            Dict with import statistics
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise NotFoundError(f"File not found: {file_path}")

        imported = 0
        skipped = 0
        errors: list[str] = []
        created_vendors: list[str] = []
        created_products: list[str] = []

        try:
            with open(file_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row_num, row in enumerate(reader, start=2):
                    try:
                        result = ImportService._import_quote_row(
                            session, row, create_missing
                        )
                        if result["imported"]:
                            imported += 1
                            if result.get("created_vendor"):
                                created_vendors.append(str(result["created_vendor"]))
                            if result.get("created_product"):
                                created_products.append(str(result["created_product"]))
                        else:
                            skipped += 1
                    except Exception as e:
                        errors.append(f"Row {row_num}: {e}")
                        skipped += 1

            logger.info(f"Imported {imported} quotes from {file_path}")
            return {
                "imported": imported,
                "skipped": skipped,
                "errors": errors,
                "created_vendors": created_vendors,
                "created_products": created_products,
            }
        except Exception as e:
            logger.error(f"Failed to import CSV: {e}")
            raise ServiceError(f"Failed to import CSV: {e}") from e

    @staticmethod
    def _import_quote_row(
        session: Session, row: Dict[str, str], create_missing: bool
    ) -> Dict[str, Any]:
        """Import a single quote row."""
        result: Dict[str, Any] = {"imported": False}

        vendor_name = row.get("vendor", "").strip()
        product_name = row.get("product", "").strip()
        brand_name = row.get("brand", "").strip()
        price_str = row.get("price", "").strip()
        currency = row.get("currency", "").strip().upper() or get_base_currency(session)
        shipping_str = row.get("shipping", "").strip()
        tax_rate_str = row.get("tax_rate", "").strip()

        if not vendor_name or not product_name or not price_str:
            raise ValidationError("Missing required fields: vendor, product, price")

        try:
            price = float(price_str)
        except ValueError:
            raise ValidationError(f"Invalid price: {price_str}")

        shipping = float(shipping_str) if shipping_str else None
        tax_rate = float(tax_rate_str) if tax_rate_str else None

        # A row that fails part-way through must not leave a half-created vendor
        # or product pending in the session for the next successful row to
        # commit, so roll back this row's own work before re-raising.
        try:
            # Get or create vendor
            vendor = Vendor.by_name(session, vendor_name)
            if not vendor:
                if not create_missing:
                    raise NotFoundError(f"Vendor not found: {vendor_name}")
                vendor = Vendor(name=vendor_name, currency=currency)
                session.add(vendor)
                session.flush()
                result["created_vendor"] = vendor_name

            # Get or create product
            product = Product.by_name(session, product_name)
            if not product:
                if not create_missing:
                    raise NotFoundError(f"Product not found: {product_name}")
                if not brand_name:
                    raise ValidationError(
                        f"Brand required for new product: {product_name}"
                    )
                brand = Brand.by_name(session, brand_name)
                if not brand:
                    brand = Brand(name=brand_name)
                    session.add(brand)
                    session.flush()
                product = Product(name=product_name, brand=brand)
                session.add(product)
                session.flush()
                result["created_product"] = product_name

            # Normalize to the base. A missing rate is an error, matching
            # QuoteService.create - storing a foreign price as a base amount
            # silently corrupts every later comparison.
            value, original_value, original_currency = ForexService.to_base(
                session, price, vendor.currency
            )
            if shipping is not None:
                shipping, _, _ = ForexService.to_base(
                    session, shipping, vendor.currency
                )

            quote = Quote(
                vendor=vendor,
                product=product,
                value=value,
                original_value=original_value,
                original_currency=original_currency,
                shipping_cost=shipping,
                tax_rate=tax_rate,
            )
            session.add(quote)
        except Exception:
            session.rollback()
            raise

        session.commit()
        result["imported"] = True
        return result

    @staticmethod
    def import_quotes_json(
        session: Session,
        file_path: str | Path,
        create_missing: bool = True,
    ) -> Dict[str, Any]:
        """
        Import quotes from a JSON file.

        Expected JSON structure: [{"vendor": "...", "product": "...", "brand": "...", "price": 100, ...}, ...]

        Args:
            session: Database session
            file_path: Path to JSON file
            create_missing: If True, create missing vendors/products/brands

        Returns:
            Dict with import statistics
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise NotFoundError(f"File not found: {file_path}")

        imported = 0
        skipped = 0
        errors: list[str] = []
        created_vendors: list[str] = []
        created_products: list[str] = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, list):
                raise ValidationError("JSON must be an array of quote objects")

            for idx, item in enumerate(data):
                try:
                    # Convert JSON item to row dict
                    row = {
                        "vendor": str(item.get("vendor", "")),
                        "product": str(item.get("product", "")),
                        "brand": str(item.get("brand", "")),
                        "price": str(item.get("price", "")),
                        "currency": str(item.get("currency", "")),
                        "shipping": str(item.get("shipping", "")),
                        "tax_rate": str(item.get("tax_rate", "")),
                    }
                    result = ImportService._import_quote_row(
                        session, row, create_missing
                    )
                    if result["imported"]:
                        imported += 1
                        if result.get("created_vendor"):
                            created_vendors.append(str(result["created_vendor"]))
                        if result.get("created_product"):
                            created_products.append(str(result["created_product"]))
                    else:
                        skipped += 1
                except Exception as e:
                    errors.append(f"Item {idx}: {e}")
                    skipped += 1

            logger.info(f"Imported {imported} quotes from {file_path}")
            return {
                "imported": imported,
                "skipped": skipped,
                "errors": errors,
                "created_vendors": created_vendors,
                "created_products": created_products,
            }
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
            raise ValidationError(f"Invalid JSON format: {e}") from e
        except Exception as e:
            logger.error(f"Failed to import JSON: {e}")
            raise ServiceError(f"Failed to import JSON: {e}") from e


class ExportService:
    """Service for exporting data to CSV/Markdown files."""

    @staticmethod
    def export_quotes_csv(
        session: Session,
        file_path: str | Path | None = None,
        filter_by: Optional[str] = None,
    ) -> str:
        """
        Export quotes to CSV format.

        Args:
            session: Database session
            file_path: Optional path to write file (if None, returns string)
            filter_by: Optional filter string for product names

        Returns:
            CSV string if no file_path provided, otherwise the file path
        """
        quotes = QuoteService.get_all(session, filter_by=filter_by, limit=10000)
        base = get_base_currency(session)

        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow(
            [
                "id",
                "vendor",
                "product",
                "brand",
                "price",
                "total_cost",
                "currency",
                "original_price",
                "original_currency",
                "shipping",
                "tax_rate",
                "status",
                "created_at",
            ]
        )

        # Data rows
        for q in quotes:
            writer.writerow(
                [
                    q.id,
                    q.vendor.name,
                    q.product.name,
                    q.product.brand.name,
                    f"{q.value:.2f}",
                    f"{q.total_cost:.2f}",
                    base,
                    f"{q.original_value:.2f}" if q.original_value is not None else "",
                    q.original_currency or "",
                    f"{q.shipping_cost:.2f}" if q.shipping_cost else "",
                    f"{q.tax_rate:.2f}" if q.tax_rate else "",
                    q.status or "",
                    str(q.created_at) if q.created_at else "",
                ]
            )

        csv_content = output.getvalue()

        if file_path:
            file_path = Path(file_path)
            file_path.write_text(csv_content, encoding="utf-8")
            logger.info(f"Exported {len(quotes)} quotes to {file_path}")
            return str(file_path)

        return csv_content

    @staticmethod
    def export_quotes_markdown(
        session: Session,
        file_path: str | Path | None = None,
        filter_by: Optional[str] = None,
        title: str = "Quote Report",
    ) -> str:
        """
        Export quotes to Markdown format.

        Args:
            session: Database session
            file_path: Optional path to write file (if None, returns string)
            filter_by: Optional filter string for product names
            title: Report title

        Returns:
            Markdown string if no file_path provided, otherwise the file path
        """
        quotes = QuoteService.get_all(session, filter_by=filter_by, limit=10000)
        base = get_base_currency(session)

        def money(amount: float) -> str:
            return format_money(amount, base)

        lines = [
            f"# {title}",
            "",
            f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"Total quotes: {len(quotes)}",
            "",
            "| ID | Vendor | Product | Brand | Price | Total | Status |",
            "|---|---|---|---|---|---|---|",
        ]

        for q in quotes:
            status = q.status.capitalize() if q.status else "-"
            lines.append(
                f"| {q.id} | {q.vendor.name} | {q.product.name} | "
                f"{q.product.brand.name} | {money(q.value)} | "
                f"{money(q.total_cost)} | {status} |"
            )

        # Summary section
        if quotes:
            prices = [q.value for q in quotes]
            lines.extend(
                [
                    "",
                    "## Summary",
                    "",
                    f"- **Total quotes:** {len(quotes)}",
                    f"- **Min price:** {money(min(prices))}",
                    f"- **Max price:** {money(max(prices))}",
                    f"- **Avg price:** {money(sum(prices) / len(prices))}",
                ]
            )

        md_content = "\n".join(lines)

        if file_path:
            file_path = Path(file_path)
            file_path.write_text(md_content, encoding="utf-8")
            logger.info(f"Exported {len(quotes)} quotes to {file_path}")
            return str(file_path)

        return md_content

    @staticmethod
    def export_products_csv(
        session: Session,
        file_path: str | Path | None = None,
    ) -> str:
        """Export products to CSV format."""
        products = ProductService.get_all(session, limit=10000)

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["id", "name", "brand", "category"])

        for p in products:
            writer.writerow([p.id, p.name, p.brand.name, p.category or ""])

        csv_content = output.getvalue()

        if file_path:
            file_path = Path(file_path)
            file_path.write_text(csv_content, encoding="utf-8")
            logger.info(f"Exported {len(products)} products to {file_path}")
            return str(file_path)

        return csv_content

    @staticmethod
    def export_vendors_csv(
        session: Session,
        file_path: str | Path | None = None,
    ) -> str:
        """Export vendors to CSV format."""
        vendors = VendorService.get_all(session, limit=10000)

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["id", "name", "currency", "discount_code", "discount"])

        for v in vendors:
            writer.writerow(
                [v.id, v.name, v.currency, v.discount_code or "", v.discount]
            )

        csv_content = output.getvalue()

        if file_path:
            file_path = Path(file_path)
            file_path.write_text(csv_content, encoding="utf-8")
            logger.info(f"Exported {len(vendors)} vendors to {file_path}")
            return str(file_path)

        return csv_content


class SettingService:
    """Database-wide settings."""

    # Rows stored relative to the base currency. Once any exist, changing the
    # base would need every one re-normalized.
    _BASE_DEPENDENT = (Quote, QuoteHistory, PurchaseOrder, Forex)

    @staticmethod
    def set_base_currency(session: Session, code: str) -> str:
        """
        Set the base currency every amount is normalized to.

        Args:
            session: Database session
            code: ISO 4217 currency code

        Returns:
            The base currency now in force

        Raises:
            ValidationError: If the code is invalid, or amounts are already
                stored in a different base
        """
        code = code.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValidationError("Currency code must be 3 letters (ISO 4217)")

        current = get_base_currency(session)
        if code == current:
            return code

        held = []
        for model in SettingService._BASE_DEPENDENT:
            count = session.execute(select(func.count()).select_from(model)).scalar()
            if count:
                held.append(f"{count} {model.__tablename__}")
        if held:
            raise ValidationError(
                f"The base currency is {current}, and {', '.join(held)} "
                "row(s) are stored in it. It is fixed once amounts exist."
            )

        row = session.get(Setting, BASE_CURRENCY_KEY)
        if row is None:
            session.add(Setting(key=BASE_CURRENCY_KEY, value=code))
        else:
            row.value = code
        session.commit()
        session.info[BASE_CURRENCY_KEY] = code
        logger.info(f"Base currency set to {code}")
        return code


class BackupService:
    """Service for database backup and restore operations."""

    @staticmethod
    def backup(
        db_path: str | Path,
        backup_path: str | Path | None = None,
    ) -> Path:
        """
        Create a backup of the database.

        Args:
            db_path: Path to the database file
            backup_path: Optional path for backup (defaults to db_path with timestamp)

        Returns:
            Path to the backup file
        """
        db_path = Path(db_path)
        if not db_path.exists():
            raise NotFoundError(f"Database not found: {db_path}")

        if backup_path is None:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = (
                db_path.parent / f"{db_path.stem}_backup_{timestamp}{db_path.suffix}"
            )
        else:
            backup_path = Path(backup_path)

        try:
            shutil.copy2(db_path, backup_path)
            logger.info(f"Created backup: {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            raise ServiceError(f"Failed to create backup: {e}") from e

    @staticmethod
    def restore(
        backup_path: str | Path,
        db_path: str | Path,
        create_backup: bool = True,
    ) -> Path:
        """
        Restore database from a backup.

        Args:
            backup_path: Path to the backup file
            db_path: Path where database should be restored
            create_backup: If True, backup current db before restoring

        Returns:
            Path to the restored database
        """
        backup_path = Path(backup_path)
        db_path = Path(db_path)

        if not backup_path.exists():
            raise NotFoundError(f"Backup not found: {backup_path}")

        # Create backup of current database if it exists
        if create_backup and db_path.exists():
            BackupService.backup(db_path)

        try:
            shutil.copy2(backup_path, db_path)
            logger.info(f"Restored database from {backup_path}")
            return db_path
        except Exception as e:
            logger.error(f"Failed to restore database: {e}")
            raise ServiceError(f"Failed to restore database: {e}") from e

    @staticmethod
    def list_backups(db_path: str | Path) -> List[Dict[str, Any]]:
        """
        List available backups for a database.

        Args:
            db_path: Path to the database file

        Returns:
            List of backup info dicts with path, size, and modified time
        """
        db_path = Path(db_path)
        backup_pattern = f"{db_path.stem}_backup_*{db_path.suffix}"
        backup_dir = db_path.parent

        backups: List[Dict[str, Any]] = []
        for backup_file in backup_dir.glob(backup_pattern):
            stat = backup_file.stat()
            backups.append(
                {
                    "path": backup_file,
                    "name": backup_file.name,
                    "size": stat.st_size,
                    "modified": datetime.datetime.fromtimestamp(stat.st_mtime),
                }
            )

        # Sort by modified time, newest first
        backups.sort(
            key=lambda b: b["modified"]
            if isinstance(b["modified"], datetime.datetime)
            else datetime.datetime.min,
            reverse=True,
        )
        return backups


class DeduplicationService:
    """Service for detecting and merging duplicate entities."""

    @staticmethod
    def find_similar_vendors(
        session: Session,
        threshold: float = 0.8,
    ) -> List[List[Vendor]]:
        """
        Find potentially duplicate vendors based on name similarity.

        Args:
            session: Database session
            threshold: Similarity threshold (0-1)

        Returns:
            List of groups of similar vendors
        """
        vendors = VendorService.get_all(session, limit=10000)
        groups = []
        processed = set()

        for i, v1 in enumerate(vendors):
            if v1.id in processed:
                continue

            similar = [v1]
            for v2 in vendors[i + 1 :]:
                if v2.id in processed:
                    continue
                if DeduplicationService._name_similarity(v1.name, v2.name) >= threshold:
                    similar.append(v2)
                    processed.add(v2.id)

            if len(similar) > 1:
                groups.append(similar)
                processed.add(v1.id)

        return groups

    @staticmethod
    def find_similar_products(
        session: Session,
        threshold: float = 0.8,
    ) -> List[List[Product]]:
        """
        Find potentially duplicate products based on name similarity.

        Args:
            session: Database session
            threshold: Similarity threshold (0-1)

        Returns:
            List of groups of similar products
        """
        products = ProductService.get_all(session, limit=10000)
        groups = []
        processed = set()

        for i, p1 in enumerate(products):
            if p1.id in processed:
                continue

            similar = [p1]
            for p2 in products[i + 1 :]:
                if p2.id in processed:
                    continue
                if DeduplicationService._name_similarity(p1.name, p2.name) >= threshold:
                    similar.append(p2)
                    processed.add(p2.id)

            if len(similar) > 1:
                groups.append(similar)
                processed.add(p1.id)

        return groups

    @staticmethod
    def _name_similarity(name1: str, name2: str) -> float:
        """
        Calculate similarity between two names (simple Jaccard similarity).

        Returns a value between 0 and 1.
        """
        # Normalize names
        n1 = name1.lower().strip()
        n2 = name2.lower().strip()

        if n1 == n2:
            return 1.0

        # Tokenize
        tokens1 = set(n1.split())
        tokens2 = set(n2.split())

        if not tokens1 or not tokens2:
            return 0.0

        # Jaccard similarity
        intersection = len(tokens1 & tokens2)
        union = len(tokens1 | tokens2)

        return intersection / union if union > 0 else 0.0

    @staticmethod
    def merge_vendors(
        session: Session,
        keep_id: int,
        merge_ids: List[int],
    ) -> Vendor:
        """
        Merge multiple vendors into one, reassigning all quotes.

        Args:
            session: Database session
            keep_id: ID of vendor to keep
            merge_ids: IDs of vendors to merge (will be deleted)

        Returns:
            The kept Vendor instance
        """
        keep_vendor = session.get(Vendor, keep_id)
        if not keep_vendor:
            raise NotFoundError(f"Vendor {keep_id} not found")

        try:
            for merge_id in merge_ids:
                if merge_id == keep_id:
                    continue

                merge_vendor = session.get(Vendor, merge_id)
                if not merge_vendor:
                    continue

                # Reassign quotes
                for quote in merge_vendor.quotes:
                    quote.vendor_id = keep_id
                    quote.vendor = keep_vendor

                # Delete merged vendor
                session.delete(merge_vendor)

            session.commit()
            logger.info(f"Merged vendors {merge_ids} into {keep_id}")
            return keep_vendor
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to merge vendors: {e}")
            raise ServiceError(f"Failed to merge vendors: {e}") from e

    @staticmethod
    def merge_products(
        session: Session,
        keep_id: int,
        merge_ids: List[int],
    ) -> Product:
        """
        Merge multiple products into one, reassigning all quotes.

        Args:
            session: Database session
            keep_id: ID of product to keep
            merge_ids: IDs of products to merge (will be deleted)

        Returns:
            The kept Product instance
        """
        keep_product = session.get(Product, keep_id)
        if not keep_product:
            raise NotFoundError(f"Product {keep_id} not found")

        try:
            for merge_id in merge_ids:
                if merge_id == keep_id:
                    continue

                merge_product = session.get(Product, merge_id)
                if not merge_product:
                    continue

                # Reassign quotes
                for quote in merge_product.quotes:
                    quote.product_id = keep_id
                    quote.product = keep_product

                # Delete merged product
                session.delete(merge_product)

            session.commit()
            logger.info(f"Merged products {merge_ids} into {keep_id}")
            return keep_product
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to merge products: {e}")
            raise ServiceError(f"Failed to merge products: {e}") from e


class ClipboardService:
    """Service for clipboard operations."""

    @staticmethod
    def copy_quote(session: Session, quote_id: int) -> str:
        """
        Copy quote information to clipboard.

        Args:
            session: Database session
            quote_id: Quote ID

        Returns:
            String that was copied to clipboard
        """
        try:
            import pyperclip
        except ImportError:
            raise ServiceError("pyperclip is not installed. Run: pip install pyperclip")

        quote = (
            session.execute(
                select(Quote)
                .options(
                    joinedload(Quote.vendor),
                    joinedload(Quote.product).joinedload(Product.brand),
                )
                .where(Quote.id == quote_id)
            )
            .unique()
            .scalar_one_or_none()
        )

        if not quote:
            raise NotFoundError(f"Quote with ID {quote_id} not found")

        text = (
            f"Product: {quote.product.name}\n"
            f"Brand: {quote.product.brand.name}\n"
            f"Vendor: {quote.vendor.name}\n"
            f"Price: {_money(session, quote.value)}\n"
            f"Total Cost: {_money(session, quote.total_cost)}"
        )

        if quote.vendor.url:
            text += f"\nVendor URL: {quote.vendor.url}"

        try:
            pyperclip.copy(text)
            logger.info(f"Copied quote {quote_id} to clipboard")
            return text
        except Exception as e:
            raise ServiceError(f"Failed to copy to clipboard: {e}") from e

    @staticmethod
    def copy_product(session: Session, product_name: str) -> str:
        """
        Copy product information to clipboard.

        Args:
            session: Database session
            product_name: Product name

        Returns:
            String that was copied to clipboard
        """
        try:
            import pyperclip
        except ImportError:
            raise ServiceError("pyperclip is not installed. Run: pip install pyperclip")

        product = (
            session.execute(
                select(Product)
                .options(joinedload(Product.brand))
                .where(Product.name == product_name)
            )
            .unique()
            .scalar_one_or_none()
        )

        if not product:
            raise NotFoundError(f"Product '{product_name}' not found")

        # Get best price for product
        quotes = (
            session.execute(
                select(Quote)
                .options(joinedload(Quote.vendor))
                .where(Quote.product_id == product.id)
                .order_by(Quote.value.asc())
            )
            .unique()
            .scalars()
            .all()
        )

        text = f"Product: {product.name}\nBrand: {product.brand.name}"

        if product.category:
            text += f"\nCategory: {product.category}"

        if quotes:
            best = quotes[0]
            text += (
                f"\nBest Price: {_money(session, best.value)} at {best.vendor.name}"
                f"\nTotal Quotes: {len(quotes)}"
            )

        try:
            pyperclip.copy(text)
            logger.info(f"Copied product '{product_name}' to clipboard")
            return text
        except Exception as e:
            raise ServiceError(f"Failed to copy to clipboard: {e}") from e

    @staticmethod
    def copy_vendor(session: Session, vendor_name: str) -> str:
        """
        Copy vendor information (including URL) to clipboard.

        Args:
            session: Database session
            vendor_name: Vendor name

        Returns:
            String that was copied to clipboard
        """
        try:
            import pyperclip
        except ImportError:
            raise ServiceError("pyperclip is not installed. Run: pip install pyperclip")

        vendor = Vendor.by_name(session, vendor_name)
        if not vendor:
            raise NotFoundError(f"Vendor '{vendor_name}' not found")

        text = f"Vendor: {vendor.name}\nCurrency: {vendor.currency}"

        if vendor.url:
            text += f"\nURL: {vendor.url}"

        if vendor.discount_code:
            text += f"\nDiscount Code: {vendor.discount_code}"

        if vendor.discount > 0:
            text += f"\nDiscount: {vendor.discount}%"

        try:
            pyperclip.copy(text)
            logger.info(f"Copied vendor '{vendor_name}' to clipboard")
            return text
        except Exception as e:
            raise ServiceError(f"Failed to copy to clipboard: {e}") from e


class VendorURLService:
    """Service for vendor URL operations."""

    @staticmethod
    def set_url(session: Session, vendor_name: str, url: str) -> Vendor:
        """
        Set or update a vendor's URL.

        Args:
            session: Database session
            vendor_name: Vendor name
            url: URL to set

        Returns:
            Updated Vendor instance
        """
        url = url.strip()
        if not url:
            raise ValidationError("URL cannot be empty")

        # Basic URL validation
        if not url.startswith(("http://", "https://")):
            raise ValidationError("URL must start with http:// or https://")

        vendor = Vendor.by_name(session, vendor_name)
        if not vendor:
            raise NotFoundError(f"Vendor '{vendor_name}' not found")

        try:
            vendor.url = url
            session.commit()
            logger.info(f"Set URL for vendor '{vendor_name}': {url}")
            return vendor
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to set vendor URL: {e}")
            raise ServiceError(f"Failed to set vendor URL: {e}") from e

    @staticmethod
    def open_url(session: Session, vendor_name: str) -> str:
        """
        Open a vendor's URL in the default browser.

        Args:
            session: Database session
            vendor_name: Vendor name

        Returns:
            The URL that was opened
        """
        import webbrowser

        vendor = Vendor.by_name(session, vendor_name)
        if not vendor:
            raise NotFoundError(f"Vendor '{vendor_name}' not found")

        if not vendor.url:
            raise ValidationError(f"Vendor '{vendor_name}' has no URL set")

        try:
            webbrowser.open(vendor.url)
            logger.info(f"Opened URL for vendor '{vendor_name}': {vendor.url}")
            return vendor.url
        except Exception as e:
            raise ServiceError(f"Failed to open URL: {e}") from e

    @staticmethod
    def clear_url(session: Session, vendor_name: str) -> Vendor:
        """
        Clear a vendor's URL.

        Args:
            session: Database session
            vendor_name: Vendor name

        Returns:
            Updated Vendor instance
        """
        vendor = Vendor.by_name(session, vendor_name)
        if not vendor:
            raise NotFoundError(f"Vendor '{vendor_name}' not found")

        try:
            vendor.url = None
            session.commit()
            logger.info(f"Cleared URL for vendor '{vendor_name}'")
            return vendor
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to clear vendor URL: {e}")
            raise ServiceError(f"Failed to clear vendor URL: {e}") from e


class ReceiptService:
    """Service for receipt attachment operations."""

    @staticmethod
    def attach(session: Session, quote_id: int, file_path: str | Path) -> Quote:
        """
        Attach a receipt file to a quote.

        Args:
            session: Database session
            quote_id: Quote ID
            file_path: Path to receipt file

        Returns:
            Updated Quote instance
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise NotFoundError(f"Receipt file not found: {file_path}")

        quote = session.get(Quote, quote_id)
        if not quote:
            raise NotFoundError(f"Quote with ID {quote_id} not found")

        try:
            quote.receipt_path = str(file_path.absolute())
            session.commit()
            logger.info(f"Attached receipt to quote {quote_id}: {file_path}")
            return quote
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to attach receipt: {e}")
            raise ServiceError(f"Failed to attach receipt: {e}") from e

    @staticmethod
    def open(session: Session, quote_id: int) -> str:
        """
        Open a quote's receipt file.

        Args:
            session: Database session
            quote_id: Quote ID

        Returns:
            Path to the receipt that was opened
        """
        import subprocess
        import platform

        quote = session.get(Quote, quote_id)
        if not quote:
            raise NotFoundError(f"Quote with ID {quote_id} not found")

        if not quote.receipt_path:
            raise ValidationError(f"Quote {quote_id} has no receipt attached")

        receipt_path = Path(quote.receipt_path)
        if not receipt_path.exists():
            raise NotFoundError(f"Receipt file not found: {receipt_path}")

        try:
            system = platform.system()
            if system == "Darwin":  # macOS
                subprocess.run(["open", str(receipt_path)], check=True)
            elif system == "Windows":
                subprocess.run(["start", "", str(receipt_path)], shell=True, check=True)
            else:  # Linux
                subprocess.run(["xdg-open", str(receipt_path)], check=True)

            logger.info(f"Opened receipt for quote {quote_id}: {receipt_path}")
            return str(receipt_path)
        except subprocess.CalledProcessError as e:
            raise ServiceError(f"Failed to open receipt: {e}") from e

    @staticmethod
    def detach(session: Session, quote_id: int) -> Quote:
        """
        Remove receipt attachment from a quote.

        Args:
            session: Database session
            quote_id: Quote ID

        Returns:
            Updated Quote instance
        """
        quote = session.get(Quote, quote_id)
        if not quote:
            raise NotFoundError(f"Quote with ID {quote_id} not found")

        try:
            quote.receipt_path = None
            session.commit()
            logger.info(f"Detached receipt from quote {quote_id}")
            return quote
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Failed to detach receipt: {e}")
            raise ServiceError(f"Failed to detach receipt: {e}") from e

    @staticmethod
    def get_quotes_with_receipts(session: Session) -> List[Quote]:
        """
        Get all quotes that have receipts attached.

        Returns:
            List of Quote instances with receipts
        """
        results = (
            session.execute(
                select(Quote)
                .options(
                    joinedload(Quote.vendor),
                    joinedload(Quote.product).joinedload(Product.brand),
                    *QUOTE_STATUS_LOAD,
                )
                .where(Quote.receipt_path.isnot(None))
                .order_by(Quote.created_at.desc())
            )
            .unique()
            .scalars()
            .all()
        )
        return list(results)


class ScraperService:
    """Service for web scraping product prices."""

    @staticmethod
    def scrape_price(url: str) -> Dict[str, Any]:
        """
        Attempt to scrape a product price from a URL.

        Args:
            url: URL to scrape

        Returns:
            Dict with scraped data (price, currency, title, etc.)
        """
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            raise ServiceError(
                "Web scraping requires requests and beautifulsoup4. "
                "Run: pip install requests beautifulsoup4"
            )

        if not url.startswith(("http://", "https://")):
            raise ValidationError("URL must start with http:// or https://")

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            }
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            raise ServiceError(f"Failed to fetch URL: {e}") from e

        soup = BeautifulSoup(response.text, "html.parser")

        result: Dict[str, Any] = {
            "url": url,
            "title": None,
            "price": None,
            # None means the page did not state a currency; callers fall back
            # to the vendor's.
            "currency": None,
            "raw_price": None,
        }

        # Try to extract title
        title_tag = soup.find("title")
        if title_tag:
            result["title"] = title_tag.text.strip()

        # Try to extract price using common patterns
        price = ScraperService._extract_price(soup)
        if price:
            result["price"] = price["value"]
            result["currency"] = price.get("currency")
            result["raw_price"] = price.get("raw")

        logger.info(f"Scraped price from {url}: {result}")
        return result

    @staticmethod
    def _extract_price(soup) -> Optional[Dict[str, Any]]:
        """
        Try to extract price from HTML using common patterns.

        Returns dict with 'value', 'currency', and 'raw' keys, or None if not found.
        """
        import re

        # Common price selectors to try
        price_selectors: list[Dict[str, Any]] = [
            # JSON-LD structured data
            {"type": "json-ld"},
            # Meta tags
            {"name": "meta", "attrs": {"property": "product:price:amount"}},
            {"name": "meta", "attrs": {"property": "og:price:amount"}},
            # Common class patterns
            {"class_": re.compile(r"price|Price|priceblock|priceToPay", re.I)},
            {"itemprop": "price"},
            {"data-price": True},
        ]

        # Try JSON-LD first
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                import json

                data = json.loads(script.string)
                if isinstance(data, dict):
                    price = ScraperService._extract_from_jsonld(data)
                    if price:
                        return price
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            price = ScraperService._extract_from_jsonld(item)
                            if price:
                                return price
            except (json.JSONDecodeError, TypeError):
                continue

        # Try meta tags. Each price meta tag has a companion currency tag;
        # assuming USD instead of reading it records foreign prices as USD.
        currency_properties = {
            "product:price:amount": "product:price:currency",
            "og:price:amount": "og:price:currency",
        }
        for selector in price_selectors[1:3]:
            tag = soup.find(**selector)
            if tag and tag.get("content"):
                try:
                    value = float(tag["content"])
                except ValueError:
                    continue
                attrs = selector.get("attrs") or {}
                currency_property = currency_properties.get(str(attrs.get("property")))
                currency_tag = (
                    soup.find("meta", attrs={"property": currency_property})
                    if currency_property
                    else None
                )
                currency = None
                if currency_tag and currency_tag.get("content"):
                    currency = str(currency_tag["content"]).strip().upper() or None
                return {
                    "value": value,
                    "currency": currency,
                    "raw": tag["content"],
                }

        # Try common class patterns
        price_pattern = re.compile(r"\$?\d+(?:,\d{3})*(?:\.\d{2})?")

        for selector in price_selectors[3:]:
            elements = soup.find_all(**selector)
            for elem in elements:
                text = elem.get_text(strip=True)
                match = price_pattern.search(text)
                if match:
                    raw = match.group()
                    # Remove currency symbols and commas
                    clean = re.sub(r"[\$\£\?,]", "", raw)
                    try:
                        value = float(clean)
                        # None means "the page did not say", so the caller can
                        # fall back to the vendor's currency rather than
                        # silently recording a foreign price as USD.
                        currency = None
                        if "\u00a3" in text or "GBP" in text:
                            currency = "GBP"
                        elif "\u20ac" in text or "EUR" in text:
                            currency = "EUR"
                        elif "$" in text or "USD" in text:
                            currency = "USD"
                        return {"value": value, "currency": currency, "raw": raw}
                    except ValueError:
                        continue

        return None

    @staticmethod
    def _extract_from_jsonld(data: dict) -> Optional[Dict[str, Any]]:
        """Extract price from JSON-LD structured data."""
        # Check for Product type
        if data.get("@type") == "Product":
            offers = data.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = offers.get("price")
            if price:
                try:
                    return {
                        "value": float(price),
                        "currency": offers.get("priceCurrency"),
                        "raw": str(price),
                    }
                except ValueError:
                    pass

        # Check for Offer type
        if data.get("@type") == "Offer":
            price = data.get("price")
            if price:
                try:
                    return {
                        "value": float(price),
                        "currency": data.get("priceCurrency"),
                        "raw": str(price),
                    }
                except ValueError:
                    pass

        return None

    @staticmethod
    def create_quote_from_scrape(
        session: Session,
        url: str,
        vendor_name: str,
        product_name: str,
        brand_name: Optional[str] = None,
    ) -> Quote:
        """
        Scrape a URL and create a quote from the result.

        Args:
            session: Database session
            url: URL to scrape
            vendor_name: Vendor name
            product_name: Product name
            brand_name: Optional brand name for new products

        Returns:
            Created Quote instance
        """
        scraped = ScraperService.scrape_price(url)

        if not scraped.get("price"):
            raise ValidationError(f"Could not extract price from {url}")

        # Use scraped price
        price = scraped["price"]

        # Get vendor
        vendor = Vendor.by_name(session, vendor_name)
        if not vendor:
            raise NotFoundError(f"Vendor '{vendor_name}' not found")

        # The page's own currency wins over the vendor's default: a vendor set
        # up as USD can still list a GBP price, and recording it as USD would
        # corrupt every comparison against it.
        source_currency = (
            scraped.get("currency") or vendor.currency or get_base_currency(session)
        ).upper()

        # Work out the vendor's base URL, but do not assign it yet: mutating
        # the vendor here would leave a dirty session behind if the quote is
        # then rejected, or get committed as a side effect of creating the
        # product below.
        vendor_base_url = None
        if not vendor.url:
            from urllib.parse import urlparse

            parsed = urlparse(url)
            vendor_base_url = f"{parsed.scheme}://{parsed.netloc}"

        # Get or create product
        product = Product.by_name(session, product_name)
        if not product:
            if not brand_name:
                raise ValidationError(
                    f"Product '{product_name}' not found and no brand name provided"
                )
            product = ProductService.create(session, product_name, brand_name)

        # Normalize to the base, keeping the scraped value and its currency
        value, original_value, original_currency = ForexService.to_base(
            session, price, source_currency
        )

        # Create quote
        quote = Quote(
            vendor=vendor,
            product=product,
            value=value,
            original_value=original_value,
            original_currency=original_currency,
            source_url=url,
        )
        with commit_or_rollback(session, f"create quote from scraped URL {url}"):
            if vendor_base_url:
                vendor.url = vendor_base_url
            session.add(quote)

        logger.info(f"Created quote from scraped URL: {url}")
        return quote


class ReportService:
    """Service for generating HTML reports."""

    # CSS styles for reports
    CSS = """
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        h1 {
            color: #333;
            border-bottom: 2px solid #2c3e50;
            padding-bottom: 10px;
        }
        h2 {
            color: #2c3e50;
            margin-top: 30px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            background-color: white;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        th, td {
            padding: 12px 15px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }
        th {
            background-color: #2c3e50;
            color: white;
            font-weight: 600;
        }
        tr:hover {
            background-color: #f5f5f5;
        }
        .best-price {
            background-color: #d4edda;
            font-weight: bold;
        }
        .status-considering {
            background-color: #fff3cd;
        }
        .status-ordered {
            background-color: #cce5ff;
        }
        .status-received {
            background-color: #d4edda;
        }
        .summary-box {
            background-color: white;
            padding: 20px;
            border-radius: 5px;
            margin: 20px 0;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .summary-item {
            display: inline-block;
            margin-right: 30px;
            padding: 10px;
        }
        .summary-label {
            color: #666;
            font-size: 0.9em;
        }
        .summary-value {
            font-size: 1.5em;
            font-weight: bold;
            color: #2c3e50;
        }
        .discount-code {
            background-color: #e7f5ff;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: monospace;
        }
        .savings {
            color: #28a745;
            font-weight: bold;
        }
        .text-muted {
            color: #6c757d;
        }
        @media print {
            body { background-color: white; }
            table { box-shadow: none; }
            .summary-box { box-shadow: none; border: 1px solid #ddd; }
        }
    """

    @staticmethod
    def _html_header(title: str) -> str:
        """Generate HTML header with CSS styles."""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>{ReportService.CSS}</style>
</head>
<body>
    <h1>{title}</h1>
    <p class="text-muted">Generated: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
"""

    @staticmethod
    def _html_footer() -> str:
        """Generate HTML footer."""
        return """
</body>
</html>
"""

    @staticmethod
    def _html_table(
        headers: List[str],
        rows: List[List[str]],
        row_classes: Optional[List[str]] = None,
    ) -> str:
        """Generate an HTML table."""
        html = "<table>\n<thead>\n<tr>"
        for h in headers:
            html += f"<th>{h}</th>"
        html += "</tr>\n</thead>\n<tbody>\n"

        for i, row in enumerate(rows):
            row_class = row_classes[i] if row_classes and i < len(row_classes) else ""
            class_attr = f' class="{row_class}"' if row_class else ""
            html += f"<tr{class_attr}>"
            for cell in row:
                html += f"<td>{cell}</td>"
            html += "</tr>\n"

        html += "</tbody>\n</table>\n"
        return html

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special characters."""
        if text is None:
            return ""
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    @staticmethod
    def price_comparison_report(
        session: Session, filter_term: Optional[str] = None
    ) -> str:
        """
        Generate a price comparison report.

        Args:
            session: Database session
            filter_term: Optional filter term for products

        Returns:
            HTML string of the report
        """
        # Get products with quotes
        if filter_term:
            products = (
                session.execute(
                    select(Product)
                    .options(
                        joinedload(Product.brand),
                        joinedload(Product.quotes).joinedload(Quote.vendor),
                    )
                    .where(Product.name.ilike(f"%{filter_term}%"))
                    .order_by(Product.name)
                )
                .unique()
                .scalars()
                .all()
            )
        else:
            products = (
                session.execute(
                    select(Product)
                    .options(
                        joinedload(Product.brand),
                        joinedload(Product.quotes).joinedload(Quote.vendor),
                    )
                    .order_by(Product.name)
                )
                .unique()
                .scalars()
                .all()
            )

        title = "Price Comparison Report"
        if filter_term:
            title += f" - '{ReportService._escape_html(filter_term)}'"

        html = ReportService._html_header(title)

        # Summary
        total_products = len([p for p in products if p.quotes])
        total_quotes = sum(len(p.quotes) for p in products)
        html += f"""
    <div class="summary-box">
        <div class="summary-item">
            <div class="summary-label">Products</div>
            <div class="summary-value">{total_products}</div>
        </div>
        <div class="summary-item">
            <div class="summary-label">Total Quotes</div>
            <div class="summary-value">{total_quotes}</div>
        </div>
    </div>
"""

        # Product comparisons
        for product in products:
            if not product.quotes:
                continue

            quotes = sorted(product.quotes, key=lambda q: q.value)
            best_price = quotes[0].value if quotes else None
            worst_price = quotes[-1].value if quotes else None

            html += f"<h2>{ReportService._escape_html(product.brand.name)} {ReportService._escape_html(product.name)}</h2>\n"

            if product.category:
                html += f"<p class='text-muted'>Category: {ReportService._escape_html(product.category)}</p>\n"

            headers = [
                "Vendor",
                f"Price ({get_base_currency(session)})",
                "As Entered",
                "Total Cost",
                "Discount Code",
                "Savings vs Worst",
            ]
            rows = []
            row_classes = []

            for q in quotes:
                discount_code = ""
                if q.vendor.discount_code:
                    discount_code = f"<span class='discount-code'>{ReportService._escape_html(q.vendor.discount_code)}</span> ({q.vendor.discount}%)"

                savings = ""
                if worst_price and best_price and worst_price > best_price:
                    savings_amount = worst_price - q.value
                    if savings_amount > 0:
                        savings = f"<span class='savings'>{_money(session, savings_amount)}</span>"

                rows.append(
                    [
                        ReportService._escape_html(q.vendor.name),
                        f"{_money(session, q.value)}",
                        ReportService._escape_html(q.source_price),
                        f"{_money(session, q.total_cost)}",
                        discount_code or "-",
                        savings or "-",
                    ]
                )
                row_classes.append("best-price" if q.value == best_price else "")

            html += ReportService._html_table(headers, rows, row_classes)

            # Price spread summary
            if best_price and worst_price:
                spread = worst_price - best_price
                html += f"""
    <p>
        Best: <strong>{_money(session, best_price)}</strong> |
        Worst: <strong>{_money(session, worst_price)}</strong> |
        Potential Savings: <span class="savings">{_money(session, spread)}</span>
    </p>
"""

        html += ReportService._html_footer()
        return html

    @staticmethod
    def purchase_summary_report(session: Session) -> str:
        """
        Generate a purchase summary report grouped by status.

        Args:
            session: Database session

        Returns:
            HTML string of the report
        """
        html = ReportService._html_header("Purchase Summary Report")

        status_totals: Dict[str, Dict[str, Any]] = {}
        all_quotes: List[Quote] = []

        for status in QUOTE_STATUSES:
            quotes = (
                session.execute(
                    select(Quote)
                    .options(
                        joinedload(Quote.vendor),
                        joinedload(Quote.product).joinedload(Product.brand),
                    )
                    .where(Quote.status == status)
                    .order_by(Quote.created_at.desc())
                )
                .unique()
                .scalars()
                .all()
            )
            status_totals[status] = {
                "count": len(quotes),
                "total": sum(q.total_cost for q in quotes),
                "quotes": list(quotes),
            }
            all_quotes.extend(quotes)

        # Summary box
        total_considering: float = status_totals["considering"]["total"]
        total_ordered: float = status_totals["ordered"]["total"]
        total_received: float = status_totals["received"]["total"]
        total_all = total_considering + total_ordered + total_received

        html += f"""
    <div class="summary-box">
        <div class="summary-item">
            <div class="summary-label">Considering</div>
            <div class="summary-value">{_money(session, total_considering)}</div>
        </div>
        <div class="summary-item">
            <div class="summary-label">Ordered</div>
            <div class="summary-value">{_money(session, total_ordered)}</div>
        </div>
        <div class="summary-item">
            <div class="summary-label">Received</div>
            <div class="summary-value">{_money(session, total_received)}</div>
        </div>
        <div class="summary-item">
            <div class="summary-label">Grand Total</div>
            <div class="summary-value">{_money(session, total_all)}</div>
        </div>
    </div>
"""

        # Status sections
        status_labels = {
            "considering": "Under Consideration",
            "ordered": "Ordered",
            "received": "Received",
        }

        for status in QUOTE_STATUSES:
            data = status_totals[status]
            quotes_list: List[Quote] = data["quotes"]
            if not quotes_list:
                continue

            html += f"<h2>{status_labels[status]} ({data['count']} items - {_money(session, data['total'])})</h2>\n"

            headers = ["Product", "Vendor", "Price", "Total Cost", "Date"]
            rows = []
            row_classes = []

            for q in quotes_list:
                rows.append(
                    [
                        f"{ReportService._escape_html(q.product.brand.name)} {ReportService._escape_html(q.product.name)}",
                        ReportService._escape_html(q.vendor.name),
                        f"{_money(session, q.value)}",
                        f"{_money(session, q.total_cost)}",
                        q.created_at.strftime("%Y-%m-%d") if q.created_at else "-",
                    ]
                )
                row_classes.append(f"status-{status}")

            html += ReportService._html_table(headers, rows, row_classes)

        html += ReportService._html_footer()
        return html

    @staticmethod
    def vendor_analysis_report(session: Session) -> str:
        """
        Generate a vendor analysis report.

        Args:
            session: Database session

        Returns:
            HTML string of the report
        """
        vendors = (
            session.execute(
                select(Vendor).options(joinedload(Vendor.quotes)).order_by(Vendor.name)
            )
            .unique()
            .scalars()
            .all()
        )

        html = ReportService._html_header("Vendor Analysis Report")

        # Summary
        total_vendors = len(vendors)
        vendors_with_quotes = len([v for v in vendors if v.quotes])
        total_quotes = sum(len(v.quotes) for v in vendors)

        html += f"""
    <div class="summary-box">
        <div class="summary-item">
            <div class="summary-label">Total Vendors</div>
            <div class="summary-value">{total_vendors}</div>
        </div>
        <div class="summary-item">
            <div class="summary-label">With Quotes</div>
            <div class="summary-value">{vendors_with_quotes}</div>
        </div>
        <div class="summary-item">
            <div class="summary-label">Total Quotes</div>
            <div class="summary-value">{total_quotes}</div>
        </div>
    </div>
"""

        # Vendor table
        html += "<h2>Vendor Details</h2>\n"
        headers = [
            "Vendor",
            "Currency",
            "Quotes",
            "Avg Price",
            "Total Value",
            "Discount Code",
        ]
        rows = []

        for v in vendors:
            quote_count = len(v.quotes)
            if quote_count > 0:
                avg_price = sum(q.value for q in v.quotes) / quote_count
                total_value = sum(q.total_cost for q in v.quotes)
            else:
                avg_price = 0
                total_value = 0

            discount_info = ""
            if v.discount_code:
                discount_info = f"<span class='discount-code'>{ReportService._escape_html(v.discount_code)}</span> ({v.discount}%)"

            rows.append(
                [
                    ReportService._escape_html(v.name),
                    v.currency,
                    str(quote_count),
                    f"{_money(session, avg_price)}" if quote_count > 0 else "-",
                    f"{_money(session, total_value)}" if quote_count > 0 else "-",
                    discount_info or "-",
                ]
            )

        html += ReportService._html_table(headers, rows)

        # Currency breakdown
        currency_stats: Dict[str, Dict[str, Any]] = {}
        for v in vendors:
            if v.currency not in currency_stats:
                currency_stats[v.currency] = {"vendors": 0, "quotes": 0, "total": 0.0}
            currency_stats[v.currency]["vendors"] += 1
            currency_stats[v.currency]["quotes"] += len(v.quotes)
            currency_stats[v.currency]["total"] += sum(q.total_cost for q in v.quotes)

        html += "<h2>Currency Breakdown</h2>\n"
        headers = [
            "Currency",
            "Vendors",
            "Quotes",
            f"Total Value ({get_base_currency(session)})",
        ]
        rows = []
        for currency, stats in sorted(currency_stats.items()):
            rows.append(
                [
                    currency,
                    str(stats["vendors"]),
                    str(stats["quotes"]),
                    f"{_money(session, stats['total'])}",
                ]
            )
        html += ReportService._html_table(headers, rows)

        html += ReportService._html_footer()
        return html

    @staticmethod
    def spend_analysis_report(
        session: Session,
        start: Optional[datetime.date] = None,
        end: Optional[datetime.date] = None,
    ) -> str:
        """
        Report on money actually committed, from purchase orders.

        Args:
            session: Database session
            start: Optional start date
            end: Optional end date

        Returns:
            Self-contained HTML string
        """
        totals = SpendAnalyticsService.summary(session, start, end)

        html = ReportService._html_header("Spend Analysis")
        html += f"""
    <div class="summary">
        <div class="summary-item">
            <div class="summary-label">Purchase Orders</div>
            <div class="summary-value">{totals["orders"]}</div>
        </div>
        <div class="summary-item">
            <div class="summary-label">Total Spend ({get_base_currency(session)})</div>
            <div class="summary-value">{_money(session, totals["total"])}</div>
        </div>
        <div class="summary-item">
            <div class="summary-label">Average Order</div>
            <div class="summary-value">{_money(session, totals["average"])}</div>
        </div>
    </div>
"""

        if not totals["orders"]:
            html += "<p class='text-muted'>No purchase orders in this period.</p>\n"
            return html + ReportService._html_footer()

        html += "<h2>By month</h2>\n"
        html += ReportService._html_table(
            ["Month", "Orders", f"Total ({get_base_currency(session)})"],
            [
                [row["period"], str(row["orders"]), f"{_money(session, row['total'])}"]
                for row in totals["by_month"]
            ],
        )

        html += "<h2>By vendor</h2>\n"
        html += ReportService._html_table(
            ["Vendor", "Orders", f"Total ({get_base_currency(session)})"],
            [
                [
                    ReportService._escape_html(row["vendor"]),
                    str(row["orders"]),
                    f"{_money(session, row['total'])}",
                ]
                for row in totals["by_vendor"]
            ],
        )

        html += "<h2>By category</h2>\n"
        html += ReportService._html_table(
            ["Category", "Orders", f"Total ({get_base_currency(session)})"],
            [
                [
                    ReportService._escape_html(row["category"]),
                    str(row["orders"]),
                    f"{_money(session, row['total'])}",
                ]
                for row in totals["by_category"]
            ],
        )

        if totals["variance"]:
            html += "<h2>Paid vs. best recorded price</h2>\n"
            html += (
                "<p class='text-muted'>A positive delta means the order cost more "
                "than the cheapest quote on file for that product.</p>\n"
            )
            html += ReportService._html_table(
                ["PO", "Product", "Vendor", "Unit Paid", "Best Recorded", "Delta"],
                [
                    [
                        ReportService._escape_html(row["po_number"]),
                        ReportService._escape_html(row["product"]),
                        ReportService._escape_html(row["vendor"]),
                        f"{_money(session, row['unit_paid'])}",
                        f"{_money(session, row['best_price'])} "
                        f"({ReportService._escape_html(row['best_vendor'])})",
                        f"{row['delta']:+,.2f} ({row['delta_pct']:+.1f}%)",
                    ]
                    for row in totals["variance"]
                ],
                ["savings" if row["delta"] <= 0 else "" for row in totals["variance"]],
            )

        return html + ReportService._html_footer()

    @staticmethod
    def generate_report(
        session: Session, preset: str, output_file: Optional[str] = None, **kwargs
    ) -> str:
        """
        Generate a report using the specified preset.

        Args:
            session: Database session
            preset: Report preset ('price-comparison', 'purchase-summary', 'vendor-analysis')
            output_file: Optional output file path
            **kwargs: Additional arguments for specific reports

        Returns:
            HTML string (or file path if output_file specified)
        """
        if preset == "spend-analysis":
            html = ReportService.spend_analysis_report(
                session, start=kwargs.get("start"), end=kwargs.get("end")
            )
        elif preset == "price-comparison":
            html = ReportService.price_comparison_report(
                session, filter_term=kwargs.get("filter_term")
            )
        elif preset == "purchase-summary":
            html = ReportService.purchase_summary_report(session)
        elif preset == "vendor-analysis":
            html = ReportService.vendor_analysis_report(session)
        else:
            raise ValidationError(
                f"Unknown report preset: {preset}. Valid options: "
                "price-comparison, purchase-summary, vendor-analysis, spend-analysis"
            )

        if output_file:
            output_path = Path(output_file)
            output_path.write_text(html, encoding="utf-8")
            logger.info(f"Generated {preset} report: {output_file}")
            return str(output_path)

        return html


class ScrapeRefreshService:
    """
    Re-fetch prices for quotes that record where they came from.

    Quotes created by ``buylog quote add --url`` remember their ``source_url``.
    Re-scraping them on a schedule keeps price history, trends, sparklines and
    alerts current without any manual data entry.
    """

    @staticmethod
    def tracked_quotes(session: Session) -> List[Quote]:
        """Every quote that carries a source URL."""
        return list(
            session.execute(
                select(Quote)
                .options(
                    joinedload(Quote.vendor),
                    joinedload(Quote.product).joinedload(Product.brand),
                    *QUOTE_STATUS_LOAD,
                )
                .where(Quote.source_url.is_not(None))
                .order_by(Quote.id)
            )
            .unique()
            .scalars()
            .all()
        )

    @staticmethod
    def refresh(
        session: Session, quote_ids: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """
        Re-scrape tracked quotes and record any price change.

        A quote whose page is unreachable or whose price cannot be parsed is
        reported in ``errors`` and left untouched, so one dead link does not
        stop the rest.

        Args:
            session: Database session
            quote_ids: Only refresh these quote IDs; None refreshes all tracked

        Returns:
            Dict with ``checked``, ``changed`` (list of change dicts),
            ``unchanged`` and ``errors``
        """
        quotes = ScrapeRefreshService.tracked_quotes(session)
        if quote_ids:
            wanted = set(quote_ids)
            quotes = [q for q in quotes if q.id in wanted]

        changed: List[Dict[str, Any]] = []
        unchanged = 0
        errors: List[str] = []

        for quote in quotes:
            url = quote.source_url
            if not url:  # pragma: no cover - filtered by the query above
                continue
            try:
                scraped = ScraperService.scrape_price(url)
            except ServiceError as e:
                errors.append(f"Quote {quote.id} ({url}): {e}")
                continue

            price = scraped.get("price")
            if price is None:
                errors.append(f"Quote {quote.id} ({url}): no price found")
                continue

            currency = (
                scraped.get("currency")
                or quote.original_currency
                or get_base_currency(session)
            ).upper()
            try:
                new_value, _, _ = ForexService.to_base(session, float(price), currency)
            except ValidationError as e:
                errors.append(f"Quote {quote.id} ({url}): {e}")
                continue

            old_value = quote.value
            if new_value == old_value:
                unchanged += 1
                continue

            # update() normalizes and records the price-history entry, which is
            # what drives trends and alerts.
            QuoteService.update(
                session, quote.id, price=float(price), price_currency=currency
            )
            changed.append(
                {
                    "quote_id": quote.id,
                    "product": quote.product.name,
                    "vendor": quote.vendor.name,
                    "old_value": old_value,
                    "new_value": new_value,
                    "url": url,
                }
            )

        logger.info(
            f"Scrape refresh: checked {len(quotes)}, changed {len(changed)}, "
            f"errors {len(errors)}"
        )
        return {
            "checked": len(quotes),
            "changed": changed,
            "unchanged": unchanged,
            "errors": errors,
        }


class ForexRefreshService:
    """Fetch current forex rates from a public API."""

    #: Frankfurter is free, needs no API key, and serves ECB reference rates.
    API_URL = "https://api.frankfurter.dev/v1/latest"

    @staticmethod
    def fetch_rates(
        codes: Optional[List[str]] = None,
        api_url: Optional[str] = None,
        base: str = "USD",
    ) -> Dict[str, float]:
        """
        Fetch rates for the given currencies against ``base``.

        Args:
            codes: Currency codes to fetch; None fetches every currency offered
            api_url: Override the API endpoint (used by tests)
            base: The database's base currency

        Returns:
            Dict mapping currency code to units of ``base`` per unit

        Raises:
            ServiceError: If the API is unreachable or returns an unusable body
        """
        import requests

        wanted = [c.strip().upper() for c in (codes or []) if c.strip()]
        # The API quotes "how much of X is one base unit"; the reciprocal is
        # the base_per_unit we store.
        base = base.upper()
        params: Dict[str, str] = {"base": base}
        if wanted:
            params["symbols"] = ",".join(sorted(set(wanted) - {base}))
            if params["symbols"] == "":
                return {}

        try:
            response = requests.get(
                api_url or ForexRefreshService.API_URL, params=params, timeout=30
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as e:
            raise ServiceError(f"Failed to fetch forex rates: {e}") from e
        except ValueError as e:
            raise ServiceError(f"Forex API returned invalid JSON: {e}") from e

        rates = payload.get("rates")
        if not isinstance(rates, dict) or not rates:
            raise ServiceError("Forex API returned no rates")

        converted: Dict[str, float] = {}
        for code, units_per_base in rates.items():
            try:
                units = float(units_per_base)
            except (TypeError, ValueError):
                logger.warning(f"Ignoring unusable rate for {code}: {units_per_base!r}")
                continue
            if units <= 0:
                logger.warning(f"Ignoring non-positive rate for {code}: {units}")
                continue
            converted[code.upper()] = 1.0 / units

        return converted

    @staticmethod
    def refresh(
        session: Session,
        codes: Optional[List[str]] = None,
        date: Optional[datetime.date] = None,
        api_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fetch rates and store them for a date.

        With no ``codes``, refreshes every currency already used by a vendor,
        so a user's own set of currencies stays current without listing them.

        Args:
            session: Database session
            codes: Currency codes to refresh
            date: Date to record the rates under (default: today)
            api_url: Override the API endpoint (used by tests)

        Returns:
            Dict with ``updated``, ``skipped`` and ``rates`` keys
        """
        date = date or datetime.date.today()
        base = get_base_currency(session)

        if not codes:
            codes = [
                row
                for row in session.execute(select(Vendor.currency).distinct()).scalars()
                if row and row.upper() != base
            ]
            if not codes:
                logger.info(f"No vendor currencies other than {base} to refresh")
                return {"updated": [], "skipped": [], "rates": {}}

        rates = ForexRefreshService.fetch_rates(codes, api_url=api_url, base=base)

        updated: List[str] = []
        skipped: List[str] = []
        for code in sorted({c.strip().upper() for c in codes if c.strip()}):
            if code == base:
                continue
            if code not in rates:
                logger.warning(f"Forex API did not return a rate for {code}")
                skipped.append(code)
                continue
            try:
                ForexService.create(session, code, rates[code], date=date)
                updated.append(code)
            except DuplicateError:
                # A rate for this code and date is already on file.
                skipped.append(code)

        logger.info(
            f"Forex refresh for {date}: updated {len(updated)}, skipped {len(skipped)}"
        )
        return {"updated": updated, "skipped": skipped, "rates": rates}


class MonitorService:
    """
    Check tracked prices and report what needs attention.

    This is the engine behind ``buylog check``: it fires price targets against
    current best prices and returns what fired, so the caller can notify the
    user without opening the app.
    """

    @staticmethod
    def check(session: Session) -> Dict[str, Any]:
        """
        Fire price targets against current best prices.

        Returns:
            Dict with ``targets`` (targets that fired now, each paired with the
            best quote that met it) and ``messages`` (one line per target).
        """
        best_prices = QuoteService.get_best_prices_by_product(session)

        fired: List[Dict[str, Any]] = []
        for product_id, quote in best_prices.items():
            product = session.get(Product, product_id)
            if product is None:
                continue
            for target in PriceTargetService.check(session, product, quote.value):
                fired.append({"target": target, "quote": quote})

        messages = [
            f"{item['quote'].product.name} is "
            f"{_money(session, item['quote'].value)} at "
            f"{item['quote'].vendor.name} (target {item['target'].label})"
            for item in fired
        ]
        return {"targets": fired, "messages": messages}


class SpendAnalyticsService:
    """
    Report on money actually committed, from purchase orders.

    Quotes record what things cost; purchase orders record what was bought.
    These reports answer "what did I spend, where, and did I buy well".
    """

    @staticmethod
    def _po_total(po: PurchaseOrder) -> float:
        """A purchase order's grand total in the base currency."""
        if po.grand_total_base is not None:
            return po.grand_total_base
        # Orders written before grand_total_base existed.
        return po.grand_total

    #: Statuses excluded from spend by default - money that was never committed.
    EXCLUDED_STATUSES = (PO_STATUS_CANCELLED,)

    @staticmethod
    def _effective_date(po: PurchaseOrder) -> Optional[datetime.date]:
        """
        The date an order counts against.

        ``order_date`` is optional, so fall back to when the record was
        created. Filtering on the raw column instead would silently drop
        undated orders from every date-bounded report while still counting
        them in the unbounded one.
        """
        if po.order_date:
            return po.order_date
        return po.created_at.date() if po.created_at else None

    @staticmethod
    def _orders(
        session: Session,
        start: Optional[datetime.date] = None,
        end: Optional[datetime.date] = None,
        statuses: Optional[List[str]] = None,
        include_cancelled: bool = False,
    ) -> List[PurchaseOrder]:
        """
        Purchase orders in a date range, oldest first.

        Args:
            session: Database session
            start: Optional inclusive start date
            end: Optional inclusive end date
            statuses: Only these statuses; None means all but the excluded ones
            include_cancelled: Count cancelled orders as spend

        Returns:
            Matching PurchaseOrder instances
        """
        query = select(PurchaseOrder).options(
            joinedload(PurchaseOrder.vendor),
            joinedload(PurchaseOrder.product).joinedload(Product.brand),
            joinedload(PurchaseOrder.product).joinedload(Product.specification),
            joinedload(PurchaseOrder.quote),
        )
        if statuses:
            query = query.where(PurchaseOrder.status.in_(statuses))
        elif not include_cancelled:
            query = query.where(
                PurchaseOrder.status.not_in(SpendAnalyticsService.EXCLUDED_STATUSES)
            )

        orders = list(session.execute(query).unique().scalars().all())

        # Filter on the effective date in Python, so orders with no order_date
        # are bucketed and filtered consistently.
        if start or end:
            orders = [
                po
                for po in orders
                if (date := SpendAnalyticsService._effective_date(po)) is not None
                and (start is None or date >= start)
                and (end is None or date <= end)
            ]

        return sorted(
            orders,
            key=lambda po: (
                SpendAnalyticsService._effective_date(po) or datetime.date.min,
                po.id,
            ),
        )

    @staticmethod
    def _period(po: PurchaseOrder) -> str:
        """The YYYY-MM bucket an order falls in."""
        date = SpendAnalyticsService._effective_date(po)
        return date.strftime("%Y-%m") if date else "unknown"

    @staticmethod
    def spend_by_month(
        session: Session,
        start: Optional[datetime.date] = None,
        end: Optional[datetime.date] = None,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Total spend per calendar month, in the base currency.

        Returns:
            List of ``{"period", "total", "orders"}`` dicts, oldest first
        """
        buckets: Dict[str, Dict[str, Any]] = {}
        for po in SpendAnalyticsService._orders(session, start, end, statuses):
            bucket = buckets.setdefault(
                SpendAnalyticsService._period(po),
                {
                    "period": SpendAnalyticsService._period(po),
                    "total": 0.0,
                    "orders": 0,
                },
            )
            bucket["total"] += SpendAnalyticsService._po_total(po)
            bucket["orders"] += 1
        return [buckets[key] for key in sorted(buckets)]

    @staticmethod
    def spend_by_vendor(
        session: Session,
        start: Optional[datetime.date] = None,
        end: Optional[datetime.date] = None,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Total spend per vendor in the base currency, highest first."""
        buckets: Dict[str, Dict[str, Any]] = {}
        for po in SpendAnalyticsService._orders(session, start, end, statuses):
            bucket = buckets.setdefault(
                po.vendor.name,
                {"vendor": po.vendor.name, "total": 0.0, "orders": 0},
            )
            bucket["total"] += SpendAnalyticsService._po_total(po)
            bucket["orders"] += 1
        return sorted(buckets.values(), key=lambda b: b["total"], reverse=True)

    @staticmethod
    def spend_by_category(
        session: Session,
        start: Optional[datetime.date] = None,
        end: Optional[datetime.date] = None,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Total spend per classification in the base currency, highest first."""
        tags = TagService.product_tag_names(session)
        buckets: Dict[str, Dict[str, Any]] = {}
        for po in SpendAnalyticsService._orders(session, start, end, statuses):
            key = (
                ProductService.report_classification(
                    po.product, tags.get(po.product_id, [])
                )
                or "uncategorized"
            )
            bucket = buckets.setdefault(
                key, {"category": key, "total": 0.0, "orders": 0}
            )
            bucket["total"] += SpendAnalyticsService._po_total(po)
            bucket["orders"] += 1
        return sorted(buckets.values(), key=lambda b: b["total"], reverse=True)

    @staticmethod
    def price_variance(
        session: Session,
        start: Optional[datetime.date] = None,
        end: Optional[datetime.date] = None,
    ) -> List[Dict[str, Any]]:
        """
        Compare what each order cost against the best price on record.

        Answers "did I buy at the best recorded price". ``delta`` is base currency paid
        above the best recorded unit price; negative means the order beat every
        quote on file.

        Returns:
            List of dicts, worst overpayment first
        """
        best_prices = QuoteService.get_best_prices_by_product(session)

        rows: List[Dict[str, Any]] = []
        for po in SpendAnalyticsService._orders(session, start, end):
            best = best_prices.get(po.product_id)
            if best is None:
                continue
            unit_paid = SpendAnalyticsService._po_total(po) / max(po.quantity, 1)
            delta = unit_paid - best.value
            rows.append(
                {
                    "po_number": po.po_number,
                    "product": po.product.name,
                    "vendor": po.vendor.name,
                    "unit_paid": unit_paid,
                    "best_price": best.value,
                    "best_vendor": best.vendor.name,
                    "delta": delta,
                    "delta_pct": (delta / best.value * 100) if best.value else 0.0,
                }
            )

        return sorted(rows, key=lambda r: r["delta"], reverse=True)

    @staticmethod
    def summary(
        session: Session,
        start: Optional[datetime.date] = None,
        end: Optional[datetime.date] = None,
    ) -> Dict[str, Any]:
        """Everything above in one call, plus the headline totals."""
        orders = SpendAnalyticsService._orders(session, start, end)
        total = sum(SpendAnalyticsService._po_total(po) for po in orders)
        return {
            "orders": len(orders),
            "total": total,
            "average": total / len(orders) if orders else 0.0,
            "by_month": SpendAnalyticsService.spend_by_month(session, start, end),
            "by_vendor": SpendAnalyticsService.spend_by_vendor(session, start, end),
            "by_category": SpendAnalyticsService.spend_by_category(session, start, end),
            "variance": SpendAnalyticsService.price_variance(session, start, end),
        }
