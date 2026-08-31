"""Tests for workflow features: purchase lists, status, notes, tags, and watchlist."""

import pytest

from buylog.models import (
    Brand,
    Product,
    Vendor,
    Quote,
    Tag,
    QUOTE_STATUS_CONSIDERING,
    QUOTE_STATUS_ORDERED,
    QUOTE_STATUS_RECEIVED,
)
from buylog.services import (
    QuoteService,
    PurchaseListService,
    NoteService,
    TagService,
    WatchlistService,
    NotFoundError,
    DuplicateError,
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
        currency="USD",
        value=100.0,
    )
    dbsession.add(quote)
    dbsession.commit()
    return quote


@pytest.fixture
def quote2(dbsession, product, vendor):
    """Create a second test quote."""
    quote = Quote(
        product=product,
        vendor=vendor,
        currency="USD",
        value=150.0,
    )
    dbsession.add(quote)
    dbsession.commit()
    return quote


# =============================================================================
# Purchase List Tests
# =============================================================================


class TestPurchaseListService:
    """Tests for PurchaseListService."""

    def test_create_purchase_list(self, dbsession):
        """Test creating a purchase list."""
        plist = PurchaseListService.create(
            dbsession, "My Shopping List", "Weekend purchases"
        )
        assert plist.name == "My Shopping List"
        assert plist.description == "Weekend purchases"
        assert plist.id is not None

    def test_create_duplicate_list_raises(self, dbsession):
        """Test that creating duplicate list raises error."""
        PurchaseListService.create(dbsession, "Unique List")
        with pytest.raises(DuplicateError):
            PurchaseListService.create(dbsession, "Unique List")

    def test_create_empty_name_raises(self, dbsession):
        """Test that empty name raises error."""
        with pytest.raises(ValidationError):
            PurchaseListService.create(dbsession, "")

    def test_get_by_name(self, dbsession):
        """Test getting purchase list by name."""
        PurchaseListService.create(dbsession, "FindMe")
        found = PurchaseListService.get_by_name(dbsession, "FindMe")
        assert found is not None
        assert found.name == "FindMe"

    def test_get_all(self, dbsession):
        """Test getting all purchase lists."""
        PurchaseListService.create(dbsession, "List1")
        PurchaseListService.create(dbsession, "List2")
        all_lists = PurchaseListService.get_all(dbsession)
        assert len(all_lists) >= 2

    def test_add_quote_to_list(self, dbsession, quote):
        """Test adding a quote to a list."""
        plist = PurchaseListService.create(dbsession, "QuoteList")
        updated = PurchaseListService.add_quote(dbsession, "QuoteList", quote.id)
        assert updated.id == plist.id
        assert quote in updated.quotes
        assert len(updated.quotes) == 1

    def test_add_quote_duplicate_raises(self, dbsession, quote):
        """Test adding duplicate quote raises error."""
        PurchaseListService.create(dbsession, "DupList")
        PurchaseListService.add_quote(dbsession, "DupList", quote.id)
        with pytest.raises(DuplicateError):
            PurchaseListService.add_quote(dbsession, "DupList", quote.id)

    def test_remove_quote_from_list(self, dbsession, quote):
        """Test removing a quote from a list."""
        PurchaseListService.create(dbsession, "RemoveList")
        PurchaseListService.add_quote(dbsession, "RemoveList", quote.id)
        updated = PurchaseListService.remove_quote(dbsession, "RemoveList", quote.id)
        assert quote not in updated.quotes

    def test_remove_nonexistent_quote_raises(self, dbsession, quote):
        """Test removing quote not in list raises error."""
        PurchaseListService.create(dbsession, "EmptyList")
        with pytest.raises(NotFoundError):
            PurchaseListService.remove_quote(dbsession, "EmptyList", quote.id)

    def test_total_value(self, dbsession, quote, quote2):
        """Test total value calculation."""
        plist = PurchaseListService.create(dbsession, "TotalList")
        PurchaseListService.add_quote(dbsession, "TotalList", quote.id)
        PurchaseListService.add_quote(dbsession, "TotalList", quote2.id)
        plist = PurchaseListService.get_by_name(dbsession, "TotalList")
        assert plist.total_value == 250.0  # 100 + 150

    def test_delete_list(self, dbsession):
        """Test deleting a purchase list."""
        PurchaseListService.create(dbsession, "DeleteMe")
        PurchaseListService.delete(dbsession, "DeleteMe")
        assert PurchaseListService.get_by_name(dbsession, "DeleteMe") is None


# =============================================================================
# Quote Status Tests
# =============================================================================


class TestQuoteStatus:
    """Tests for quote status functionality."""

    def test_set_status_considering(self, dbsession, quote):
        """Test setting status to considering."""
        updated = QuoteService.set_status(dbsession, quote.id, "considering")
        assert updated.status == QUOTE_STATUS_CONSIDERING

    def test_set_status_ordered(self, dbsession, quote):
        """Test setting status to ordered."""
        updated = QuoteService.set_status(dbsession, quote.id, "ordered")
        assert updated.status == QUOTE_STATUS_ORDERED

    def test_set_status_received(self, dbsession, quote):
        """Test setting status to received."""
        updated = QuoteService.set_status(dbsession, quote.id, "received")
        assert updated.status == QUOTE_STATUS_RECEIVED

    def test_set_invalid_status_raises(self, dbsession, quote):
        """Test setting invalid status raises error."""
        with pytest.raises(ValidationError):
            QuoteService.set_status(dbsession, quote.id, "invalid")

    def test_set_status_nonexistent_quote_raises(self, dbsession):
        """Test setting status on nonexistent quote raises error."""
        with pytest.raises(NotFoundError):
            QuoteService.set_status(dbsession, 99999, "ordered")

    def test_get_by_status(self, dbsession, quote, quote2):
        """Test getting quotes by status."""
        QuoteService.set_status(dbsession, quote.id, "ordered")
        QuoteService.set_status(dbsession, quote2.id, "considering")

        ordered = QuoteService.get_by_status(dbsession, "ordered")
        considering = QuoteService.get_by_status(dbsession, "considering")

        assert len(ordered) >= 1
        assert len(considering) >= 1
        assert all(q.status == "ordered" for q in ordered)
        assert all(q.status == "considering" for q in considering)


# =============================================================================
# Note Tests
# =============================================================================


class TestNoteService:
    """Tests for NoteService."""

    def test_create_note(self, dbsession, product):
        """Test creating a note."""
        note = NoteService.create(
            dbsession, "product", product.id, "This is a great product!"
        )
        assert note.entity_type == "product"
        assert note.entity_id == product.id
        assert note.content == "This is a great product!"

    def test_create_note_empty_content_raises(self, dbsession, product):
        """Test that empty content raises error."""
        with pytest.raises(ValidationError):
            NoteService.create(dbsession, "product", product.id, "")

    def test_create_note_invalid_type_raises(self, dbsession, product):
        """Test that invalid entity type raises error."""
        with pytest.raises(ValidationError):
            NoteService.create(dbsession, "invalid_type", product.id, "Note")

    def test_get_notes_for_entity(self, dbsession, product):
        """Test getting notes for an entity."""
        NoteService.create(dbsession, "product", product.id, "Note 1")
        NoteService.create(dbsession, "product", product.id, "Note 2")

        notes = NoteService.get_for_entity(dbsession, "product", product.id)
        assert len(notes) == 2

    def test_update_note(self, dbsession, product):
        """Test updating a note."""
        note = NoteService.create(dbsession, "product", product.id, "Original")
        updated = NoteService.update(dbsession, note.id, "Updated content")
        assert updated.content == "Updated content"
        assert updated.updated_at is not None

    def test_delete_note(self, dbsession, product):
        """Test deleting a note."""
        note = NoteService.create(dbsession, "product", product.id, "To delete")
        NoteService.delete(dbsession, note.id)
        notes = NoteService.get_for_entity(dbsession, "product", product.id)
        assert len(notes) == 0


# =============================================================================
# Tag Tests
# =============================================================================


class TestTagService:
    """Tests for TagService."""

    def test_create_tag(self, dbsession):
        """Test creating a tag."""
        tag = TagService.create(dbsession, "Important", "#ff0000")
        assert tag.name == "important"  # lowercase
        assert tag.color == "#ff0000"

    def test_create_duplicate_tag_raises(self, dbsession):
        """Test that creating duplicate tag raises error."""
        TagService.create(dbsession, "Unique")
        with pytest.raises(DuplicateError):
            TagService.create(dbsession, "UNIQUE")  # case-insensitive

    def test_add_tag_to_entity(self, dbsession, product):
        """Test adding a tag to an entity."""
        entity_tag = TagService.add_to_entity(dbsession, "sale", "product", product.id)
        assert entity_tag.entity_type == "product"
        assert entity_tag.entity_id == product.id

    def test_add_tag_auto_creates(self, dbsession, product):
        """Test that adding tag auto-creates it if it doesn't exist."""
        entity_tag = TagService.add_to_entity(
            dbsession, "newautotag", "product", product.id
        )
        assert entity_tag is not None
        tag = Tag.by_name(dbsession, "newautotag")
        assert tag is not None

    def test_add_duplicate_tag_raises(self, dbsession, product):
        """Test adding duplicate tag to entity raises error."""
        TagService.add_to_entity(dbsession, "dup", "product", product.id)
        with pytest.raises(DuplicateError):
            TagService.add_to_entity(dbsession, "dup", "product", product.id)

    def test_remove_tag_from_entity(self, dbsession, product):
        """Test removing a tag from an entity."""
        TagService.add_to_entity(dbsession, "removeme", "product", product.id)
        TagService.remove_from_entity(dbsession, "removeme", "product", product.id)
        tags = TagService.get_for_entity(dbsession, "product", product.id)
        assert not any(t.name == "removeme" for t in tags)

    def test_get_tags_for_entity(self, dbsession, product):
        """Test getting all tags for an entity."""
        TagService.add_to_entity(dbsession, "tag1", "product", product.id)
        TagService.add_to_entity(dbsession, "tag2", "product", product.id)
        tags = TagService.get_for_entity(dbsession, "product", product.id)
        assert len(tags) == 2

    def test_get_all_tags(self, dbsession):
        """Test getting all tags."""
        TagService.create(dbsession, "globalTag1")
        TagService.create(dbsession, "globalTag2")
        tags = TagService.get_all(dbsession)
        assert len(tags) >= 2

    def test_get_entities_by_tag(self, dbsession, product, vendor):
        """Test getting all entities with a specific tag."""
        TagService.add_to_entity(dbsession, "shared", "product", product.id)
        TagService.add_to_entity(dbsession, "shared", "vendor", vendor.id)

        entities = TagService.get_entities_by_tag(dbsession, "shared")
        assert len(entities) == 2

    def test_get_entities_by_tag_filtered(self, dbsession, product, vendor):
        """Test getting entities by tag filtered by type."""
        TagService.add_to_entity(dbsession, "filtered", "product", product.id)
        TagService.add_to_entity(dbsession, "filtered", "vendor", vendor.id)

        entities = TagService.get_entities_by_tag(dbsession, "filtered", "product")
        assert len(entities) == 1
        assert entities[0].entity_type == "product"


# =============================================================================
# Watchlist Tests
# =============================================================================


class TestWatchlistService:
    """Tests for WatchlistService."""

    def test_add_to_watchlist(self, dbsession, product):
        """Test adding a product to watchlist."""
        watchlist = WatchlistService.create(
            dbsession, product.name, target_price=50.0, notes="Wait for sale"
        )
        assert watchlist.product_id == product.id
        assert watchlist.target_price == 50.0
        assert watchlist.notes == "Wait for sale"
        assert watchlist.active == 1

    def test_add_duplicate_to_watchlist_raises(self, dbsession, product):
        """Test adding duplicate product raises error."""
        WatchlistService.create(dbsession, product.name)
        with pytest.raises(DuplicateError):
            WatchlistService.create(dbsession, product.name)

    def test_add_nonexistent_product_raises(self, dbsession):
        """Test adding nonexistent product raises error."""
        with pytest.raises(NotFoundError):
            WatchlistService.create(dbsession, "NonexistentProduct")

    def test_get_active_watchlist(self, dbsession, product):
        """Test getting active watchlist items."""
        WatchlistService.create(dbsession, product.name)
        active = WatchlistService.get_active(dbsession)
        assert len(active) >= 1

    def test_update_watchlist(self, dbsession, product):
        """Test updating watchlist entry."""
        watchlist = WatchlistService.create(dbsession, product.name, target_price=100.0)
        updated = WatchlistService.update(
            dbsession, watchlist.id, target_price=75.0, notes="New notes"
        )
        assert updated.target_price == 75.0
        assert updated.notes == "New notes"

    def test_deactivate_watchlist(self, dbsession, product):
        """Test deactivating watchlist entry."""
        watchlist = WatchlistService.create(dbsession, product.name)
        deactivated = WatchlistService.deactivate(dbsession, watchlist.id)
        assert deactivated.active == 0

    def test_delete_watchlist(self, dbsession, product):
        """Test deleting watchlist entry."""
        watchlist = WatchlistService.create(dbsession, product.name)
        WatchlistService.delete(dbsession, watchlist.id)
        all_items = WatchlistService.get_all(dbsession)
        assert not any(w.id == watchlist.id for w in all_items)
