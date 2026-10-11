"""Tests for data management features: import, export, backup, and deduplication."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from buylog.models import Brand, Product, Vendor, Quote
from buylog.services import (
    ImportService,
    ExportService,
    BackupService,
    DeduplicationService,
    NotFoundError,
    ValidationError,
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
    product = Product(name="TestProduct", brand=brand)
    dbsession.add(product)
    dbsession.commit()
    return product


@pytest.fixture
def vendor(dbsession):
    """Create a test vendor."""
    vendor = Vendor(name="TestVendor", currency="USD")
    dbsession.add(vendor)
    dbsession.commit()
    return vendor


@pytest.fixture
def quote(dbsession, product, vendor):
    """Create a test quote."""
    quote = Quote(
        product=product,
        vendor=vendor,
        value=100.0,
    )
    dbsession.add(quote)
    dbsession.commit()
    return quote


@pytest.fixture
def temp_csv_file():
    """Create a temporary CSV file for import testing."""
    content = """vendor,product,brand,price,currency,shipping,tax_rate
TestVendor1,TestProduct1,TestBrand1,99.99,USD,5.00,8.5
TestVendor2,TestProduct2,TestBrand2,149.99,USD,,
TestVendor1,TestProduct3,TestBrand1,49.99,USD,2.50,
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(content)
        f.flush()
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def temp_json_file():
    """Create a temporary JSON file for import testing."""
    data = [
        {
            "vendor": "JsonVendor1",
            "product": "JsonProduct1",
            "brand": "JsonBrand1",
            "price": 199.99,
        },
        {
            "vendor": "JsonVendor2",
            "product": "JsonProduct2",
            "brand": "JsonBrand2",
            "price": 299.99,
            "shipping": 10.00,
        },
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        f.flush()
        yield f.name
    os.unlink(f.name)


# =============================================================================
# Import Service Tests
# =============================================================================


class TestImportService:
    """Tests for ImportService."""

    def test_import_csv_creates_entities(self, dbsession, temp_csv_file):
        """Test that CSV import creates vendors, products, and quotes."""
        stats = ImportService.import_quotes_csv(dbsession, temp_csv_file)

        assert stats["imported"] == 3
        assert stats["skipped"] == 0
        assert "TestVendor1" in stats["created_vendors"]
        assert "TestProduct1" in stats["created_products"]

    def test_import_csv_without_create(self, dbsession, temp_csv_file, vendor, product):
        """Test that import without create_missing raises errors."""
        stats = ImportService.import_quotes_csv(
            dbsession, temp_csv_file, create_missing=False
        )

        # Should have errors because vendors/products don't exist
        assert stats["skipped"] > 0
        assert len(stats["errors"]) > 0

    def test_import_csv_file_not_found(self, dbsession):
        """Test that importing non-existent file raises error."""
        with pytest.raises(NotFoundError):
            ImportService.import_quotes_csv(dbsession, "/nonexistent/file.csv")

    def test_import_json_creates_entities(self, dbsession, temp_json_file):
        """Test that JSON import creates entities."""
        stats = ImportService.import_quotes_json(dbsession, temp_json_file)

        assert stats["imported"] == 2
        assert "JsonVendor1" in stats["created_vendors"]

    def test_import_json_file_not_found(self, dbsession):
        """Test that importing non-existent JSON raises error."""
        with pytest.raises(NotFoundError):
            ImportService.import_quotes_json(dbsession, "/nonexistent/file.json")

    def test_import_invalid_json(self, dbsession):
        """Test that importing invalid JSON raises error."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json")
            f.flush()
            try:
                with pytest.raises(ValidationError):
                    ImportService.import_quotes_json(dbsession, f.name)
            finally:
                os.unlink(f.name)


# =============================================================================
# Export Service Tests
# =============================================================================


class TestExportService:
    """Tests for ExportService."""

    def test_export_quotes_csv_to_string(self, dbsession, quote):
        """Test exporting quotes to CSV string."""
        csv_content = ExportService.export_quotes_csv(dbsession)

        assert "id,vendor,product,brand,price" in csv_content
        assert "TestVendor" in csv_content
        assert "TestProduct" in csv_content

    def test_export_quotes_csv_to_file(self, dbsession, quote):
        """Test exporting quotes to CSV file."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            file_path = f.name

        try:
            result = ExportService.export_quotes_csv(dbsession, file_path)
            assert result == file_path
            assert Path(file_path).exists()

            with open(file_path) as f:
                content = f.read()
                assert "TestVendor" in content
        finally:
            os.unlink(file_path)

    def test_export_quotes_markdown(self, dbsession, quote):
        """Test exporting quotes to Markdown."""
        md_content = ExportService.export_quotes_markdown(
            dbsession, title="Test Report"
        )

        assert "# Test Report" in md_content
        assert "| ID |" in md_content
        assert "TestVendor" in md_content

    def test_export_quotes_with_filter(self, dbsession, quote):
        """Test exporting quotes with filter."""
        csv_content = ExportService.export_quotes_csv(
            dbsession, filter_by="TestProduct"
        )
        assert "TestProduct" in csv_content

        csv_content = ExportService.export_quotes_csv(
            dbsession, filter_by="NonexistentProduct"
        )
        lines = csv_content.strip().split("\n")
        assert len(lines) == 1  # Only header

    def test_export_products_csv(self, dbsession, product):
        """Test exporting products to CSV."""
        csv_content = ExportService.export_products_csv(dbsession)

        assert "id,name,brand,category" in csv_content
        assert "TestProduct" in csv_content

    def test_export_vendors_csv(self, dbsession, vendor):
        """Test exporting vendors to CSV."""
        csv_content = ExportService.export_vendors_csv(dbsession)

        assert "id,name,currency" in csv_content
        assert "TestVendor" in csv_content


# =============================================================================
# Backup Service Tests
# =============================================================================


class TestBackupService:
    """Tests for BackupService."""

    def test_backup_creates_file(self):
        """Test that backup creates a file."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            # Write some content to simulate a database
            f.write(b"test database content")
            f.flush()
            db_path = f.name

        try:
            backup_path = BackupService.backup(db_path)
            assert backup_path.exists()
            assert "backup" in str(backup_path)
            os.unlink(backup_path)
        finally:
            os.unlink(db_path)

    def test_backup_custom_path(self):
        """Test backup to custom path."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
            db_f.write(b"test content")
            db_f.flush()
            db_path = db_f.name

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as backup_f:
            backup_path = backup_f.name

        try:
            result = BackupService.backup(db_path, backup_path)
            assert result == Path(backup_path)
            assert Path(backup_path).exists()
        finally:
            os.unlink(db_path)
            os.unlink(backup_path)

    def test_backup_nonexistent_raises(self):
        """Test that backing up nonexistent file raises error."""
        with pytest.raises(NotFoundError):
            BackupService.backup("/nonexistent/database.db")

    def test_restore_from_backup(self):
        """Test restoring from backup."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as backup_f:
            backup_f.write(b"backup content")
            backup_f.flush()
            backup_path = backup_f.name

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
            db_f.write(b"original content")
            db_f.flush()
            db_path = db_f.name

        try:
            BackupService.restore(backup_path, db_path, create_backup=False)

            with open(db_path, "rb") as f:
                content = f.read()
                assert content == b"backup content"
        finally:
            os.unlink(backup_path)
            os.unlink(db_path)

    def test_restore_nonexistent_backup_raises(self):
        """Test that restoring nonexistent backup raises error."""
        with pytest.raises(NotFoundError):
            BackupService.restore("/nonexistent/backup.db", "/tmp/test.db")

    def test_list_backups(self):
        """Test listing backups."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            db_path.write_text("original")

            # Create some backups
            backup1 = Path(tmpdir) / "test_backup_20240101_120000.db"
            backup2 = Path(tmpdir) / "test_backup_20240102_120000.db"
            backup1.write_text("backup1")
            backup2.write_text("backup2")

            backups = BackupService.list_backups(db_path)

            assert len(backups) == 2
            assert all("backup" in b["name"] for b in backups)


# =============================================================================
# Deduplication Service Tests
# =============================================================================


class TestDeduplicationService:
    """Tests for DeduplicationService."""

    def test_find_similar_vendors(self, dbsession):
        """Test finding similar vendors."""
        # Create similar vendors
        v1 = Vendor(name="Amazon Store US", currency="USD")
        v2 = Vendor(name="Amazon Store UK", currency="GBP")
        v3 = Vendor(name="Best Buy Electronics", currency="USD")
        dbsession.add_all([v1, v2, v3])
        dbsession.commit()

        # Jaccard similarity of "Amazon Store US" vs "Amazon Store UK" is:
        # tokens: {"amazon", "store", "us"} vs {"amazon", "store", "uk"}
        # intersection: {"amazon", "store"} = 2
        # union: {"amazon", "store", "us", "uk"} = 4
        # similarity = 2/4 = 0.5
        groups = DeduplicationService.find_similar_vendors(dbsession, threshold=0.5)

        # Amazon Store US and Amazon Store UK should be grouped together
        assert len(groups) >= 1
        amazon_group = None
        for group in groups:
            names = [v.name for v in group]
            if "Amazon Store US" in names and "Amazon Store UK" in names:
                amazon_group = group
                break
        assert amazon_group is not None

    def test_find_similar_products(self, dbsession, brand):
        """Test finding similar products."""
        p1 = Product(name="iPhone 15 Pro", brand=brand)
        p2 = Product(name="iPhone 15 Pro Max", brand=brand)
        p3 = Product(name="Galaxy S24", brand=brand)
        dbsession.add_all([p1, p2, p3])
        dbsession.commit()

        groups = DeduplicationService.find_similar_products(dbsession, threshold=0.5)

        # iPhone products should be grouped together
        assert len(groups) >= 1

    def test_name_similarity(self):
        """Test name similarity calculation."""
        # Identical names
        assert DeduplicationService._name_similarity("Apple", "Apple") == 1.0

        # Similar names
        sim = DeduplicationService._name_similarity("Amazon US", "Amazon UK")
        assert 0.3 < sim < 1.0

        # Different names
        sim = DeduplicationService._name_similarity("Apple", "Microsoft")
        assert sim < 0.5

    def test_merge_vendors(self, dbsession, brand, product):
        """Test merging vendors."""
        v1 = Vendor(name="Vendor A", currency="USD")
        v2 = Vendor(name="Vendor A copy", currency="USD")
        dbsession.add_all([v1, v2])
        dbsession.flush()

        q1 = Quote(product=product, vendor=v1, value=100.0)
        q2 = Quote(product=product, vendor=v2, value=110.0)
        dbsession.add_all([q1, q2])
        dbsession.commit()

        # Merge v2 into v1
        result = DeduplicationService.merge_vendors(dbsession, v1.id, [v2.id])

        assert result.id == v1.id
        # Both quotes should now be under v1
        assert len(result.quotes) == 2

        # v2 should be deleted
        assert dbsession.get(Vendor, v2.id) is None

    def test_merge_products(self, dbsession, brand, vendor):
        """Test merging products."""
        p1 = Product(name="Product A", brand=brand)
        p2 = Product(name="Product A copy", brand=brand)
        dbsession.add_all([p1, p2])
        dbsession.flush()

        q1 = Quote(product=p1, vendor=vendor, value=100.0)
        q2 = Quote(product=p2, vendor=vendor, value=110.0)
        dbsession.add_all([q1, q2])
        dbsession.commit()

        # Merge p2 into p1
        result = DeduplicationService.merge_products(dbsession, p1.id, [p2.id])

        assert result.id == p1.id
        # Both quotes should now be under p1
        assert len(result.quotes) == 2

        # p2 should be deleted
        assert dbsession.get(Product, p2.id) is None

    def test_merge_nonexistent_vendor_raises(self, dbsession):
        """Test that merging nonexistent vendor raises error."""
        with pytest.raises(NotFoundError):
            DeduplicationService.merge_vendors(dbsession, 99999, [1, 2])

    def test_merge_nonexistent_product_raises(self, dbsession):
        """Test that merging nonexistent product raises error."""
        with pytest.raises(NotFoundError):
            DeduplicationService.merge_products(dbsession, 99999, [1, 2])
