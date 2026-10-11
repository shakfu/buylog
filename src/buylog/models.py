#!/usr/bin/env python3
"""model.py: models a purchasing support tool"""

import datetime
from typing import List

from sqlalchemy import (
    Column,
    insert,
    Date,
    case,
    exists,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy import select
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import object_session, relationship, sessionmaker, mapped_column
from sqlalchemy.orm import DeclarativeBase, Mapped
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Object:
    """A mixin class for sqlalcheny to save some typing"""

    @declared_attr.directive
    def __tablename__(cls):
        return cls.__name__.lower()

    @classmethod
    def by_name(cls, session, name):
        """query table by name"""
        stmt = select(cls).where(cls.name == name)
        return session.execute(stmt).scalar_one_or_none()

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)

    def __repr__(self):
        return f"<{self.__class__.__name__}(name='{self.name}')>"


DEFAULT_BASE_CURRENCY = "USD"
BASE_CURRENCY_KEY = "base_currency"


class Setting(Base):
    """
    Facts about the whole database, one row per key.

    ``base_currency`` is the currency every stored amount is normalized to.
    It is set when the database is created and fixed once amounts exist.
    """

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)


def get_base_currency(session) -> str:
    """The database's base currency, cached on the session."""
    cached = session.info.get(BASE_CURRENCY_KEY)
    if cached:
        return cached
    row = session.get(Setting, BASE_CURRENCY_KEY)
    code = row.value if row else DEFAULT_BASE_CURRENCY
    session.info[BASE_CURRENCY_KEY] = code
    return code


def format_money(amount: float, code: str) -> str:
    """``"$1,234.50"`` for USD, ``"1,234.50 JPY"`` for any other code."""
    if code.upper() == "USD":
        return f"${amount:,.2f}"
    return f"{amount:,.2f} {code.upper()}"


class Forex(Base):
    """
    Exchange rates over time.

    ``base_per_unit`` is how many units of the database's base currency one
    unit of ``code`` buys.
    """

    __tablename__ = "forex"
    __table_args__ = (
        # One rate per currency per day; without this, code-only lookups can
        # match several rows.
        UniqueConstraint("code", "date", name="uq_forex_code_date"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[datetime.date] = mapped_column(
        Date, default=func.current_date(), index=True
    )
    code: Mapped[str] = mapped_column(
        String(3), index=True
    )  # ISO 4217 currency codes are 3 chars
    base_per_unit: Mapped[float] = mapped_column(Float)

    def __repr__(self):
        session = object_session(self)
        base = get_base_currency(session) if session else DEFAULT_BASE_CURRENCY
        return f"<Forex(date={self.date}, {self.code} -> {base}: {self.base_per_unit})>"

    @property
    def units_per_base(self) -> float:
        """Units of ``code`` per one unit of the base currency."""
        return 1.0 / self.base_per_unit if self.base_per_unit != 0 else 0.0


VendorBrand = Table(
    "vendor_brand",
    Base.metadata,
    Column("vendor_id", ForeignKey("vendor.id"), primary_key=True),
    Column("brand_id", ForeignKey("brand.id"), primary_key=True),
)


class Vendor(Object, Base):
    """Selling Entity"""

    # country = mapped_column(String)
    currency: Mapped[str] = mapped_column(String)
    discount_code: Mapped[str | None] = mapped_column(String)
    discount: Mapped[float] = mapped_column(Float, default=0.0)
    # The vendor's site. Older databases also had a website column; migrate
    # copies that into url and drops it.
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    # Contact information
    contact_person: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    # Address fields
    address_line1: Mapped[str | None] = mapped_column(String, nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String, nullable=True)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    # Business information
    tax_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String, nullable=True)
    brands: Mapped[List["Brand"]] = relationship(
        secondary=VendorBrand, back_populates="vendors"
    )
    quotes: Mapped[List["Quote"]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )

    def add_product(self, session, brand_name, product_name, price, discount=0.0):
        _brand = session.execute(
            select(Brand).where(Brand.name == brand_name)
        ).scalar_one_or_none()
        _product = session.execute(
            select(Product).where(Product.name == product_name)
        ).scalar_one_or_none()

        if not _brand:
            _brand = Brand(name=brand_name)
            self.brands.append(_brand)
            session.add(_brand)

        if not _product:
            _product = Product(name=product_name, brand=_brand)
            session.add(_product)

        if _brand and _product:
            _quote = Quote(
                product=_product,
                vendor=self,
                value=price,
                discount=discount,
            )
            session.add(_quote)


class Brand(Object, Base):
    """Manufacturing Entity"""

    vendors: Mapped[List["Vendor"]] = relationship(
        "Vendor", secondary=VendorBrand, back_populates="brands"
    )
    # Deliberately NOT cascading: a brand's products are independently
    # meaningful records, and cascading would chain on through Product.quotes
    # and wipe the catalogue. BrandService.delete refuses instead.
    products: Mapped[List["Product"]] = relationship("Product", back_populates="brand")


# Specification data type constants
SPEC_DATA_TYPES = ["text", "number", "boolean"]


class Specification(Object, Base):
    """Structured specification template for products"""

    description: Mapped[str | None] = mapped_column(String, nullable=True)
    features: Mapped[List["SpecificationFeature"]] = relationship(
        "SpecificationFeature",
        back_populates="specification",
        cascade="all, delete-orphan",
    )
    products: Mapped[List["Product"]] = relationship(
        "Product", back_populates="specification"
    )


class SpecificationFeature(Base):
    """Individual feature definition within a specification"""

    __tablename__ = "specification_feature"

    id: Mapped[int] = mapped_column(primary_key=True)
    specification_id: Mapped[int] = mapped_column(
        ForeignKey("specification.id"), index=True
    )
    specification: Mapped["Specification"] = relationship(
        "Specification", back_populates="features"
    )
    name: Mapped[str] = mapped_column(String)
    data_type: Mapped[str] = mapped_column(
        String, default="text"
    )  # text, number, boolean
    unit: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g., "mm", "kg"
    is_required: Mapped[int] = mapped_column(Integer, default=0)  # SQLite bool
    min_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    def __repr__(self):
        return f"<SpecificationFeature(name='{self.name}', type={self.data_type})>"


class ProductFeature(Base):
    """Actual feature value for a product"""

    __tablename__ = "product_feature"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"), index=True)
    product: Mapped["Product"] = relationship("Product", back_populates="features")
    specification_feature_id: Mapped[int] = mapped_column(
        ForeignKey("specification_feature.id"), index=True
    )
    specification_feature: Mapped["SpecificationFeature"] = relationship(
        "SpecificationFeature"
    )
    value_text: Mapped[str | None] = mapped_column(String, nullable=True)
    value_number: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_boolean: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # SQLite bool

    def __repr__(self):
        return f"<ProductFeature(feature='{self.specification_feature.name}')>"

    @property
    def value(self):
        """Get the appropriate value based on data type"""
        if self.specification_feature.data_type == "text":
            return self.value_text
        elif self.specification_feature.data_type == "number":
            return self.value_number
        elif self.specification_feature.data_type == "boolean":
            return bool(self.value_boolean) if self.value_boolean is not None else None
        return None


class Product(Object, Base):
    """Item sold by brand via vendor"""

    brand_id: Mapped[int] = mapped_column(Integer, ForeignKey("brand.id"))
    brand: Mapped["Brand"] = relationship("Brand", back_populates="products")
    category: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    specification_id: Mapped[int | None] = mapped_column(
        ForeignKey("specification.id"), nullable=True
    )
    specification: Mapped["Specification | None"] = relationship(
        "Specification", back_populates="products"
    )
    quotes: Mapped[List["Quote"]] = relationship(
        "Quote", back_populates="product", cascade="all, delete-orphan"
    )
    features: Mapped[List["ProductFeature"]] = relationship(
        "ProductFeature", back_populates="product", cascade="all, delete-orphan"
    )
    price_targets: Mapped[List["PriceTarget"]] = relationship(
        "PriceTarget", back_populates="product", cascade="all, delete-orphan"
    )


# Quote status constants
QUOTE_STATUS_CONSIDERING = "considering"
QUOTE_STATUS_ORDERED = "ordered"
QUOTE_STATUS_RECEIVED = "received"
QUOTE_STATUSES = [QUOTE_STATUS_CONSIDERING, QUOTE_STATUS_ORDERED, QUOTE_STATUS_RECEIVED]


class Quote(Base):
    """Quoted Quote of Item from a vendor"""

    __tablename__ = "quote"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("product.id"))
    product: Mapped["Product"] = relationship("Product", back_populates="quotes")
    history: Mapped[List["QuoteHistory"]] = relationship(
        "QuoteHistory",
        back_populates="quote",
        cascade="all, delete-orphan",
    )
    vendor_id: Mapped[int] = mapped_column(Integer, ForeignKey("vendor.id"))
    vendor: Mapped["Vendor"] = relationship("Vendor", back_populates="quotes")
    # In the base currency. The amount as entered is original_value /
    # original_currency, which are null when it was entered in the base.
    value: Mapped[float] = mapped_column(Float)
    original_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_currency: Mapped[str | None] = mapped_column(String, nullable=True)
    discount: Mapped[float] = mapped_column(Float, default=0.0)
    shipping_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    tax_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now())
    receipt_path: Mapped[str | None] = mapped_column(String, nullable=True)
    # The product page this quote's price came from, so it can be re-scraped.
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    purchase_orders: Mapped[List["PurchaseOrder"]] = relationship(
        "PurchaseOrder", back_populates="quote"
    )
    purchase_lists: Mapped[List["PurchaseList"]] = relationship(
        "PurchaseList", secondary="purchase_list_quote", back_populates="quotes"
    )

    @hybrid_property
    def status(self) -> str | None:
        """
        Derived from the quote's purchase orders and purchase lists.

        ``received`` or ``ordered`` when a purchase order that is not
        cancelled exists, ``considering`` when the quote is on a purchase
        list, else None. Nothing stores it, so it cannot disagree with them.
        Set it with ``QuoteService.set_status``.
        """
        active = [
            po.status for po in self.purchase_orders if po.status != PO_STATUS_CANCELLED
        ]
        if active:
            if PO_STATUS_RECEIVED in active:
                return QUOTE_STATUS_RECEIVED
            return QUOTE_STATUS_ORDERED
        if self.purchase_lists:
            return QUOTE_STATUS_CONSIDERING
        return None

    @status.inplace.expression
    @classmethod
    def _status_expression(cls):
        order = PurchaseOrder.__table__
        received = exists().where(
            order.c.quote_id == cls.id, order.c.status == PO_STATUS_RECEIVED
        )
        ordered = exists().where(
            order.c.quote_id == cls.id, order.c.status != PO_STATUS_CANCELLED
        )
        listed = exists().where(PurchaseListQuote.c.quote_id == cls.id)
        return case(
            (received, QUOTE_STATUS_RECEIVED),
            (ordered, QUOTE_STATUS_ORDERED),
            (listed, QUOTE_STATUS_CONSIDERING),
            else_=None,
        )

    @property
    def base_currency(self) -> str:
        """The currency of ``value``: the database's base currency."""
        session = object_session(self)
        return get_base_currency(session) if session else DEFAULT_BASE_CURRENCY

    @property
    def source_price(self) -> str:
        """
        The price as the user entered it, e.g. ``"999.00 EUR"``.

        Quotes are normalized to the base currency at entry, but a user who
        thinks in EUR needs to see what they actually typed.
        """
        if self.original_value is None or not self.original_currency:
            return f"{self.value:,.2f} {self.base_currency}"
        return f"{self.original_value:,.2f} {self.original_currency}"

    @property
    def display_price(self) -> str:
        """
        The price with its source currency, e.g. ``"999.00 EUR (= $1,085.00)"``.

        Just the base amount when the quote was entered in the base currency.
        """
        normalized = format_money(self.value, self.base_currency)
        if self.original_value is None or not self.original_currency:
            return normalized
        return f"{self.original_value:,.2f} {self.original_currency} (= {normalized})"

    @property
    def total_cost(self) -> float:
        """Calculate total cost including discount, shipping, and tax."""
        base = self.value * (1 - (self.discount / 100))
        shipping = self.shipping_cost or 0.0
        tax_multiplier = 1 + ((self.tax_rate or 0.0) / 100)
        return (base + shipping) * tax_multiplier

    def __repr__(self):
        return f"<Quote({self.vendor.name} / {self.product.brand.name} {self.product.name} / {self.value} {self.base_currency})>"


class QuoteHistory(Base):
    """History of price changes for a quote"""

    __tablename__ = "quote_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quote.id"), index=True)
    quote: Mapped["Quote"] = relationship("Quote", back_populates="history")
    old_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_value: Mapped[float] = mapped_column(Float)
    changed_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now())
    change_type: Mapped[str] = mapped_column(
        String, default="update"
    )  # "create" or "update"

    def __repr__(self):
        return f"<QuoteHistory(quote_id={self.quote_id}, {self.old_value} -> {self.new_value})>"


# PurchaseOrder status constants
PO_STATUS_PENDING = "pending"
PO_STATUS_APPROVED = "approved"
PO_STATUS_ORDERED = "ordered"
PO_STATUS_SHIPPED = "shipped"
PO_STATUS_RECEIVED = "received"
PO_STATUS_CANCELLED = "cancelled"
PO_STATUSES = [
    PO_STATUS_PENDING,
    PO_STATUS_APPROVED,
    PO_STATUS_ORDERED,
    PO_STATUS_SHIPPED,
    PO_STATUS_RECEIVED,
    PO_STATUS_CANCELLED,
]


class PurchaseOrder(Base):
    """Purchase order representing a committed purchase"""

    __tablename__ = "purchase_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int | None] = mapped_column(
        ForeignKey("quote.id"), nullable=True, index=True
    )
    quote: Mapped["Quote | None"] = relationship(
        "Quote", back_populates="purchase_orders"
    )
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendor.id"), index=True)
    vendor: Mapped["Vendor"] = relationship("Vendor")
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"), index=True)
    product: Mapped["Product"] = relationship("Product")

    po_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    status: Mapped[str] = mapped_column(String, default=PO_STATUS_PENDING, index=True)

    order_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    expected_delivery: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    actual_delivery: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)

    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String, default="USD")
    total_amount: Mapped[float] = mapped_column(Float)  # quantity * unit_price
    shipping_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    tax: Mapped[float | None] = mapped_column(Float, nullable=True)
    grand_total: Mapped[float] = mapped_column(Float)  # total + shipping + tax
    # grand_total in the base currency, so orders in different vendor
    # currencies can be compared and summed.
    grand_total_base: Mapped[float | None] = mapped_column(Float, nullable=True)

    invoice_number: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now())

    def __repr__(self):
        return f"<PurchaseOrder(po_number='{self.po_number}', status='{self.status}')>"


class PriceTarget(Base):
    """
    A price at or below which the user wants to hear about a product.

    Replaces ``price_alert`` and ``watchlist``, which stored the same
    threshold twice. It fires once: ``triggered_at`` is set when it is met,
    and editing the threshold clears it.
    """

    __tablename__ = "price_target"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"), index=True)
    product: Mapped["Product"] = relationship("Product", back_populates="price_targets")
    threshold: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    active: Mapped[int] = mapped_column(Integer, default=1)  # SQLite bool
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now())
    triggered_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    @property
    def label(self) -> str:
        """Threshold as the user set it, e.g. ``"$800.00"`` or ``"800.00 EUR"``."""
        return format_money(self.threshold, self.currency)

    def __repr__(self):
        status = "active" if self.active else "inactive"
        return f"<PriceTarget(product_id={self.product_id}, threshold={self.label}, {status})>"


# Junction table for PurchaseList to Quote many-to-many
PurchaseListQuote = Table(
    "purchase_list_quote",
    Base.metadata,
    Column("purchase_list_id", ForeignKey("purchase_list.id"), primary_key=True),
    Column("quote_id", ForeignKey("quote.id"), primary_key=True),
)


class PurchaseList(Base):
    """Named shopping list grouping quotes"""

    __tablename__ = "purchase_list"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now())
    quotes: Mapped[List["Quote"]] = relationship(
        "Quote", secondary=PurchaseListQuote, back_populates="purchase_lists"
    )

    @classmethod
    def by_name(cls, session, name):
        """Query by name"""
        stmt = select(cls).where(cls.name == name)
        return session.execute(stmt).scalar_one_or_none()

    @property
    def total_value(self) -> float:
        """Calculate total value of all quotes in list"""
        return sum(q.total_cost for q in self.quotes)

    def __repr__(self):
        return f"<PurchaseList(name='{self.name}', quotes={len(self.quotes)})>"


class Note(Base):
    """Freeform notes attachable to any entity"""

    __tablename__ = "note"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(
        String, index=True
    )  # 'product', 'vendor', 'quote', etc.
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    content: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    def __repr__(self):
        preview = self.content[:30] + "..." if len(self.content) > 30 else self.content
        return f"<Note({self.entity_type}:{self.entity_id}, '{preview}')>"


class Tag(Base):
    """Tags for categorizing entities"""

    __tablename__ = "tag"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    color: Mapped[str | None] = mapped_column(String, nullable=True)  # hex color for UI

    @classmethod
    def by_name(cls, session, name):
        """Query by name"""
        stmt = select(cls).where(cls.name == name)
        return session.execute(stmt).scalar_one_or_none()

    def __repr__(self):
        return f"<Tag(name='{self.name}')>"


class EntityTag(Base):
    """Junction table for tagging any entity"""

    __tablename__ = "entity_tag"

    id: Mapped[int] = mapped_column(primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id"), index=True)
    tag: Mapped["Tag"] = relationship("Tag")
    entity_type: Mapped[str] = mapped_column(String, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)

    def __repr__(self):
        return f"<EntityTag(tag={self.tag.name}, {self.entity_type}:{self.entity_id})>"


def init_db(engine) -> None:
    """
    Create every table registered on ``Base.metadata``.

    Models defined outside this module (``audit.AuditLog``) only register
    themselves on import, so import them here before creating tables.
    Callers must use this instead of ``Base.metadata.create_all`` directly,
    otherwise those tables are silently skipped.
    """
    from . import audit  # noqa: F401  (registers AuditLog on Base.metadata)

    Base.metadata.create_all(engine)
    # Record the base currency the moment the database exists, so data is
    # never normalized to a currency the database does not name.
    with engine.begin() as conn:
        conn.execute(
            insert(Setting)
            .prefix_with("OR IGNORE")
            .values(key=BASE_CURRENCY_KEY, value=DEFAULT_BASE_CURRENCY)
        )


if __name__ == "__main__":
    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine)
    session = Session()

    Base.metadata.create_all(engine)

    # fx
    eur = Forex(code="EUR", base_per_unit=1.085)
    gbp = Forex(code="GBP", base_per_unit=1.292)

    # brands
    apple = Brand(name="Apple")
    session.add(apple)

    # products
    iphone_15 = Product(name="iPhone 15", brand=apple)
    session.add(iphone_15)
    iphone_14 = Product(name="iPhone 14", brand=apple)
    session.add(iphone_14)

    # vendors
    apple_shop_nyc = Vendor(name="Apple Shop NYC", currency="USD")
    session.add(apple_shop_nyc)

    # quotes
    q1 = Quote(product=iphone_15, vendor=apple_shop_nyc, value=1200)
    session.add(q1)
    q2 = Quote(product=iphone_14, vendor=apple_shop_nyc, value=1000)
    session.add(q2)

    # add products
    apple_shop_nyc.add_product(session, "Apple", "iPhone 13", 100)

    session.commit()

    print("brands:", session.query(Brand).all())
    print("products:", session.query(Product).all())
    print("vendors:", session.query(Vendor).all())
    print("quotes:", session.query(Quote).all())
