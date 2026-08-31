"""Tests for integration features: clipboard, vendor URL, receipt, and web scraping."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from buylog.models import Brand, Product, Vendor, Quote
from buylog.services import (
    ClipboardService,
    VendorURLService,
    ReceiptService,
    ScraperService,
    NotFoundError,
    ValidationError,
    ServiceError,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def brand(dbsession):
    """Create a test brand."""
    brand = Brand(name="TestBrand")
    dbsession.add(brand)
    dbsession.commit()
    return brand


@pytest.fixture
def product(dbsession, brand):
    """Create a test product."""
    product = Product(name="TestProduct", brand=brand, category="Electronics")
    dbsession.add(product)
    dbsession.commit()
    return product


@pytest.fixture
def vendor(dbsession):
    """Create a test vendor."""
    vendor = Vendor(name="TestVendor", currency="USD", url="https://example.com")
    dbsession.add(vendor)
    dbsession.commit()
    return vendor


@pytest.fixture
def vendor_no_url(dbsession):
    """Create a test vendor without URL."""
    vendor = Vendor(name="NoURLVendor", currency="USD")
    dbsession.add(vendor)
    dbsession.commit()
    return vendor


@pytest.fixture
def quote(dbsession, product, vendor):
    """Create a test quote."""
    quote = Quote(
        product=product,
        vendor=vendor,
        currency="USD",
        value=100.0,
    )
    dbsession.add(quote)
    dbsession.commit()
    return quote


@pytest.fixture
def temp_receipt_file():
    """Create a temporary receipt file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pdf", delete=False) as f:
        f.write("Receipt content")
        f.flush()
        yield f.name
    os.unlink(f.name)


# =============================================================================
# Clipboard Service Tests
# =============================================================================


class TestClipboardService:
    """Tests for ClipboardService."""

    @patch("pyperclip.copy")
    def test_copy_quote(self, mock_copy, dbsession, quote):
        """Test copying quote to clipboard."""
        text = ClipboardService.copy_quote(dbsession, quote.id)

        assert "TestProduct" in text
        assert "TestBrand" in text
        assert "TestVendor" in text
        assert "$100.00" in text
        mock_copy.assert_called_once_with(text)

    @patch("pyperclip.copy")
    def test_copy_quote_includes_vendor_url(self, mock_copy, dbsession, quote):
        """Test that vendor URL is included when available."""
        text = ClipboardService.copy_quote(dbsession, quote.id)

        assert "https://example.com" in text

    def test_copy_quote_not_found(self, dbsession):
        """Test copying nonexistent quote raises error."""
        with pytest.raises(NotFoundError):
            ClipboardService.copy_quote(dbsession, 99999)

    @patch("pyperclip.copy")
    def test_copy_product(self, mock_copy, dbsession, product, quote):
        """Test copying product to clipboard."""
        text = ClipboardService.copy_product(dbsession, product.name)

        assert "TestProduct" in text
        assert "TestBrand" in text
        assert "Electronics" in text
        mock_copy.assert_called_once()

    def test_copy_product_not_found(self, dbsession):
        """Test copying nonexistent product raises error."""
        with pytest.raises(NotFoundError):
            ClipboardService.copy_product(dbsession, "NonexistentProduct")

    @patch("pyperclip.copy")
    def test_copy_vendor(self, mock_copy, dbsession, vendor):
        """Test copying vendor to clipboard."""
        text = ClipboardService.copy_vendor(dbsession, vendor.name)

        assert "TestVendor" in text
        assert "USD" in text
        assert "https://example.com" in text
        mock_copy.assert_called_once()

    def test_copy_vendor_not_found(self, dbsession):
        """Test copying nonexistent vendor raises error."""
        with pytest.raises(NotFoundError):
            ClipboardService.copy_vendor(dbsession, "NonexistentVendor")


# =============================================================================
# Vendor URL Service Tests
# =============================================================================


class TestVendorURLService:
    """Tests for VendorURLService."""

    def test_set_url(self, dbsession, vendor_no_url):
        """Test setting vendor URL."""
        result = VendorURLService.set_url(
            dbsession, vendor_no_url.name, "https://newurl.com"
        )

        assert result.url == "https://newurl.com"

    def test_set_url_empty_raises(self, dbsession, vendor):
        """Test that empty URL raises error."""
        with pytest.raises(ValidationError):
            VendorURLService.set_url(dbsession, vendor.name, "")

    def test_set_url_invalid_raises(self, dbsession, vendor):
        """Test that invalid URL raises error."""
        with pytest.raises(ValidationError):
            VendorURLService.set_url(dbsession, vendor.name, "not-a-url")

    def test_set_url_vendor_not_found(self, dbsession):
        """Test setting URL for nonexistent vendor raises error."""
        with pytest.raises(NotFoundError):
            VendorURLService.set_url(
                dbsession, "NonexistentVendor", "https://example.com"
            )

    @patch("webbrowser.open")
    def test_open_url(self, mock_open, dbsession, vendor):
        """Test opening vendor URL."""
        url = VendorURLService.open_url(dbsession, vendor.name)

        assert url == "https://example.com"
        mock_open.assert_called_once_with("https://example.com")

    def test_open_url_no_url_raises(self, dbsession, vendor_no_url):
        """Test opening URL for vendor without URL raises error."""
        with pytest.raises(ValidationError):
            VendorURLService.open_url(dbsession, vendor_no_url.name)

    def test_open_url_vendor_not_found(self, dbsession):
        """Test opening URL for nonexistent vendor raises error."""
        with pytest.raises(NotFoundError):
            VendorURLService.open_url(dbsession, "NonexistentVendor")

    def test_clear_url(self, dbsession, vendor):
        """Test clearing vendor URL."""
        result = VendorURLService.clear_url(dbsession, vendor.name)

        assert result.url is None

    def test_clear_url_vendor_not_found(self, dbsession):
        """Test clearing URL for nonexistent vendor raises error."""
        with pytest.raises(NotFoundError):
            VendorURLService.clear_url(dbsession, "NonexistentVendor")


# =============================================================================
# Receipt Service Tests
# =============================================================================


class TestReceiptService:
    """Tests for ReceiptService."""

    def test_attach_receipt(self, dbsession, quote, temp_receipt_file):
        """Test attaching receipt to quote."""
        result = ReceiptService.attach(dbsession, quote.id, temp_receipt_file)

        assert result.receipt_path == str(Path(temp_receipt_file).absolute())

    def test_attach_receipt_file_not_found(self, dbsession, quote):
        """Test attaching nonexistent receipt raises error."""
        with pytest.raises(NotFoundError):
            ReceiptService.attach(dbsession, quote.id, "/nonexistent/receipt.pdf")

    def test_attach_receipt_quote_not_found(self, dbsession, temp_receipt_file):
        """Test attaching receipt to nonexistent quote raises error."""
        with pytest.raises(NotFoundError):
            ReceiptService.attach(dbsession, 99999, temp_receipt_file)

    def test_detach_receipt(self, dbsession, quote, temp_receipt_file):
        """Test detaching receipt from quote."""
        ReceiptService.attach(dbsession, quote.id, temp_receipt_file)
        result = ReceiptService.detach(dbsession, quote.id)

        assert result.receipt_path is None

    def test_detach_receipt_quote_not_found(self, dbsession):
        """Test detaching receipt from nonexistent quote raises error."""
        with pytest.raises(NotFoundError):
            ReceiptService.detach(dbsession, 99999)

    def test_get_quotes_with_receipts(self, dbsession, quote, temp_receipt_file):
        """Test listing quotes with receipts."""
        ReceiptService.attach(dbsession, quote.id, temp_receipt_file)

        quotes = ReceiptService.get_quotes_with_receipts(dbsession)

        assert len(quotes) == 1
        assert quotes[0].id == quote.id

    def test_get_quotes_with_receipts_empty(self, dbsession, quote):
        """Test listing quotes with receipts when none attached."""
        quotes = ReceiptService.get_quotes_with_receipts(dbsession)

        assert len(quotes) == 0

    def test_open_receipt_no_receipt_raises(self, dbsession, quote):
        """Test opening receipt when none attached raises error."""
        with pytest.raises(ValidationError):
            ReceiptService.open(dbsession, quote.id)

    def test_open_receipt_quote_not_found(self, dbsession):
        """Test opening receipt for nonexistent quote raises error."""
        with pytest.raises(NotFoundError):
            ReceiptService.open(dbsession, 99999)


# =============================================================================
# Scraper Service Tests
# =============================================================================


class TestScraperService:
    """Tests for ScraperService."""

    def test_scrape_price_invalid_url(self):
        """Test scraping invalid URL raises error."""
        with pytest.raises(ValidationError):
            ScraperService.scrape_price("not-a-url")

    @patch("requests.get")
    def test_scrape_price_extracts_from_jsonld(self, mock_get):
        """Test extracting price from JSON-LD structured data."""
        mock_response = MagicMock()
        mock_response.text = """
        <html>
        <head><title>Test Product</title></head>
        <body>
        <script type="application/ld+json">
        {
            "@type": "Product",
            "name": "Test Product",
            "offers": {
                "@type": "Offer",
                "price": "99.99",
                "priceCurrency": "USD"
            }
        }
        </script>
        </body>
        </html>
        """
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = ScraperService.scrape_price("https://example.com/product")

        assert result["title"] == "Test Product"
        assert result["price"] == 99.99
        assert result["currency"] == "USD"

    @patch("requests.get")
    def test_scrape_price_extracts_from_meta(self, mock_get):
        """Test extracting price from meta tags."""
        mock_response = MagicMock()
        mock_response.text = """
        <html>
        <head>
            <title>Test Product</title>
            <meta property="product:price:amount" content="149.99">
        </head>
        <body></body>
        </html>
        """
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = ScraperService.scrape_price("https://example.com/product")

        assert result["price"] == 149.99

    @patch("requests.get")
    def test_scrape_price_no_price_found(self, mock_get):
        """Test scraping page with no price returns None."""
        mock_response = MagicMock()
        mock_response.text = """
        <html>
        <head><title>Test Page</title></head>
        <body><p>No price here</p></body>
        </html>
        """
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = ScraperService.scrape_price("https://example.com/page")

        assert result["title"] == "Test Page"
        assert result["price"] is None

    @patch("requests.get")
    def test_scrape_price_request_error(self, mock_get):
        """Test handling request errors."""
        import requests

        mock_get.side_effect = requests.RequestException("Connection failed")

        with pytest.raises(ServiceError):
            ScraperService.scrape_price("https://example.com/product")

    def test_create_quote_from_scrape_product_not_found(self, dbsession, vendor):
        """Test creating quote with nonexistent product and no brand raises error."""
        with patch.object(ScraperService, "scrape_price") as mock_scrape:
            mock_scrape.return_value = {"price": 99.99, "currency": "USD"}

            with pytest.raises(ValidationError):
                ScraperService.create_quote_from_scrape(
                    dbsession,
                    "https://example.com",
                    vendor.name,
                    "NonexistentProduct",
                )

    def test_create_quote_from_scrape_vendor_not_found(self, dbsession, product):
        """Test creating quote with nonexistent vendor raises error."""
        with patch.object(ScraperService, "scrape_price") as mock_scrape:
            mock_scrape.return_value = {"price": 99.99, "currency": "USD"}

            with pytest.raises(NotFoundError):
                ScraperService.create_quote_from_scrape(
                    dbsession,
                    "https://example.com",
                    "NonexistentVendor",
                    product.name,
                )

    def test_create_quote_from_scrape_no_price(self, dbsession, vendor, product):
        """Test creating quote when no price scraped raises error."""
        with patch.object(ScraperService, "scrape_price") as mock_scrape:
            mock_scrape.return_value = {"price": None, "currency": "USD"}

            with pytest.raises(ValidationError):
                ScraperService.create_quote_from_scrape(
                    dbsession,
                    "https://example.com",
                    vendor.name,
                    product.name,
                )

    @patch.object(ScraperService, "scrape_price")
    def test_create_quote_from_scrape_success(
        self, mock_scrape, dbsession, vendor, product
    ):
        """Test successfully creating quote from scraped URL."""
        mock_scrape.return_value = {"price": 199.99, "currency": "USD"}

        quote = ScraperService.create_quote_from_scrape(
            dbsession,
            "https://example.com/product",
            vendor.name,
            product.name,
        )

        assert quote.value == 199.99
        assert quote.vendor.name == vendor.name
        assert quote.product.name == product.name


# =============================================================================
# Model Field Tests
# =============================================================================


class TestModelFields:
    """Tests for new model fields."""

    def test_vendor_url_field(self, dbsession):
        """Test Vendor.url field."""
        vendor = Vendor(name="URLVendor", currency="USD", url="https://test.com")
        dbsession.add(vendor)
        dbsession.commit()

        loaded = dbsession.get(Vendor, vendor.id)
        assert loaded.url == "https://test.com"

    def test_vendor_url_nullable(self, dbsession):
        """Test Vendor.url can be null."""
        vendor = Vendor(name="NoURLVendor2", currency="USD")
        dbsession.add(vendor)
        dbsession.commit()

        loaded = dbsession.get(Vendor, vendor.id)
        assert loaded.url is None

    def test_quote_receipt_path_field(self, dbsession, product, vendor):
        """Test Quote.receipt_path field."""
        quote = Quote(
            product=product,
            vendor=vendor,
            currency="USD",
            value=100.0,
            receipt_path="/path/to/receipt.pdf",
        )
        dbsession.add(quote)
        dbsession.commit()

        loaded = dbsession.get(Quote, quote.id)
        assert loaded.receipt_path == "/path/to/receipt.pdf"

    def test_quote_receipt_path_nullable(self, dbsession, product, vendor):
        """Test Quote.receipt_path can be null."""
        quote = Quote(
            product=product,
            vendor=vendor,
            currency="USD",
            value=100.0,
        )
        dbsession.add(quote)
        dbsession.commit()

        loaded = dbsession.get(Quote, quote.id)
        assert loaded.receipt_path is None
