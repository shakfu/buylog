"""Tests for schema creation and migration correctness.

These cover the modules (models.init_db, migrate) that own the on-disk
schema; a mismatch here breaks the application at runtime rather than in
the service layer.
"""

import pytest
from sqlalchemy import create_engine, inspect

from buylog import models
from buylog.migrate import check_migrations, run_migrations
from buylog.models import Base


@pytest.fixture
def fresh_engine(tmp_path):
    """An engine pointing at an empty on-disk database."""
    return create_engine(f"sqlite:///{tmp_path / 'schema.db'}")


def test_init_db_creates_audit_log(fresh_engine):
    """init_db must create audit_log, which is declared in audit.py."""
    models.init_db(fresh_engine)
    assert "audit_log" in inspect(fresh_engine).get_table_names()


def test_init_db_creates_every_registered_table(fresh_engine):
    models.init_db(fresh_engine)
    created = set(inspect(fresh_engine).get_table_names())
    assert set(Base.metadata.tables) <= created


def test_migrations_create_every_table(fresh_engine):
    """A migrated database must match Base.metadata, join tables included."""
    run_migrations(fresh_engine)
    created = set(inspect(fresh_engine).get_table_names())
    missing = set(Base.metadata.tables) - created
    assert missing == set(), f"migrate did not create: {sorted(missing)}"


def test_migrations_create_composite_key_join_tables(fresh_engine):
    """Regression: composite-PK join tables were dropped by the migrator."""
    run_migrations(fresh_engine)
    created = set(inspect(fresh_engine).get_table_names())
    assert "vendor_brand" in created
    assert "purchase_list_quote" in created


def test_migrations_include_all_columns(fresh_engine):
    """Every model column must exist in the migrated database."""
    run_migrations(fresh_engine)
    inspector = inspect(fresh_engine)
    for name, table in Base.metadata.tables.items():
        db_columns = {c["name"] for c in inspector.get_columns(name)}
        expected = {c.name for c in table.columns}
        assert expected <= db_columns, f"{name} missing {sorted(expected - db_columns)}"


def test_migrations_idempotent(fresh_engine):
    """Running migrations twice must leave nothing further to apply."""
    run_migrations(fresh_engine)
    assert check_migrations(fresh_engine) == []


def test_migrations_dry_run_changes_nothing(fresh_engine):
    statements = run_migrations(fresh_engine, dry_run=True)
    assert statements
    assert inspect(fresh_engine).get_table_names() == []


def test_migrations_enforce_unique_constraints(fresh_engine):
    """A migrated database must reject duplicates, not just create the columns."""
    import datetime

    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    run_migrations(fresh_engine)

    with Session(fresh_engine) as session:
        session.add(
            models.Forex(code="EUR", date=datetime.date(2026, 1, 1), base_per_unit=1.1)
        )
        session.commit()
        session.add(
            models.Forex(code="EUR", date=datetime.date(2026, 1, 1), base_per_unit=1.2)
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_migrations_add_a_unique_constraint_to_an_existing_table(fresh_engine):
    """A model can gain a UniqueConstraint after a table already exists."""
    from sqlalchemy import text

    with fresh_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE forex (id INTEGER PRIMARY KEY, date DATE, "
                "code TEXT, usd_per_unit REAL)"
            )
        )

    run_migrations(fresh_engine)

    unique_indexes = {
        tuple(sorted(index["column_names"]))
        for index in inspect(fresh_engine).get_indexes("forex")
        if index["unique"]
    }
    assert ("code", "date") in unique_indexes


def test_migrations_fold_vendor_website_into_url(fresh_engine):
    """An older vendor row keeps its link, and the website column goes away."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with fresh_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE vendor ("
                "id INTEGER PRIMARY KEY, name TEXT, currency TEXT, "
                "discount_code TEXT, discount REAL, url TEXT, "
                "contact_person TEXT, email TEXT, phone TEXT, website TEXT, "
                "address_line1 TEXT, address_line2 TEXT, city TEXT, state TEXT, "
                "postal_code TEXT, country TEXT, tax_id TEXT, payment_terms TEXT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO vendor (name, currency, url, website) VALUES "
                "('Only site', 'USD', NULL, 'https://only.example'), "
                "('Both', 'USD', 'https://keep.example', 'https://drop.example')"
            )
        )

    run_migrations(fresh_engine)

    columns = {column["name"] for column in inspect(fresh_engine).get_columns("vendor")}
    assert "website" not in columns
    with Session(fresh_engine) as session:
        only_site = session.execute(
            text("SELECT url FROM vendor WHERE name = 'Only site'")
        ).scalar_one()
        both = session.execute(
            text("SELECT url FROM vendor WHERE name = 'Both'")
        ).scalar_one()
        notes = (
            session.execute(
                text("SELECT content FROM note WHERE entity_type = 'vendor'")
            )
            .scalars()
            .all()
        )
    assert only_site == "https://only.example"
    assert both == "https://keep.example"
    # The dropped website survives as a note; the copied one needs none.
    assert notes == ["Website before migration: https://drop.example"]
    assert check_migrations(fresh_engine) == []


def test_migrations_upgrade_an_older_schema(fresh_engine):
    """An existing database gains new tables, columns and constraints."""
    from sqlalchemy import text

    with fresh_engine.begin() as conn:
        conn.execute(text("CREATE TABLE brand (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(
            text(
                "CREATE TABLE quote (id INTEGER PRIMARY KEY, product_id INTEGER, "
                "vendor_id INTEGER, currency TEXT, value REAL)"
            )
        )

    run_migrations(fresh_engine)

    inspector = inspect(fresh_engine)
    quote_columns = {c["name"] for c in inspector.get_columns("quote")}
    assert "source_url" in quote_columns
    assert "original_currency" in quote_columns
    assert "grand_total_base" in {
        c["name"] for c in inspector.get_columns("purchase_order")
    }
    assert check_migrations(fresh_engine) == []


def test_migrations_fold_alerts_and_watchlist_into_price_target(fresh_engine):
    """0.2.0 rows survive: thresholds, watchlist notes, and target-less flags."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    models.init_db(fresh_engine)
    with fresh_engine.begin() as conn:
        # The 0.2.0 shapes. watchlist had no target_currency yet.
        conn.execute(
            text(
                "CREATE TABLE price_alert (id INTEGER PRIMARY KEY, "
                "product_id INTEGER, threshold_value REAL, threshold_currency TEXT, "
                "active INTEGER, created_at DATETIME, triggered_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE watchlist (id INTEGER PRIMARY KEY, product_id INTEGER, "
                "target_price REAL, notes TEXT, active INTEGER, created_at DATETIME)"
            )
        )
        conn.execute(text("INSERT INTO brand (id, name) VALUES (1, 'Apple')"))
        conn.execute(
            text(
                "INSERT INTO product (id, name, brand_id) VALUES "
                "(1, 'iPhone 15', 1), (2, 'iPad', 1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO vendor (id, name, currency, discount) "
                "VALUES (1, 'Amazon', 'USD', 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO quote (id, product_id, vendor_id, value, "
                "discount, created_at) "
                "VALUES (1, 2, 1, 500, 0, '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO price_alert (product_id, threshold_value, "
                "threshold_currency, active, triggered_at) VALUES "
                "(1, 900, 'eur', 1, '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO watchlist (product_id, target_price, notes, active) "
                "VALUES (1, 800, 'wait for sale', 1), (2, NULL, NULL, 1)"
            )
        )

    run_migrations(fresh_engine)

    tables = set(inspect(fresh_engine).get_table_names())
    assert "price_alert" not in tables
    assert "watchlist" not in tables
    with Session(fresh_engine) as session:
        targets = session.execute(
            text(
                "SELECT threshold, currency, triggered_at IS NOT NULL "
                "FROM price_target ORDER BY threshold"
            )
        ).all()
        notes = (
            session.execute(text("SELECT content FROM note WHERE entity_id = 1"))
            .scalars()
            .all()
        )
        considering = session.execute(
            text(
                "SELECT plq.quote_id FROM purchase_list_quote plq "
                "JOIN purchase_list pl ON pl.id = plq.purchase_list_id "
                "WHERE pl.name = 'Considering'"
            )
        ).all()
    # The alert keeps its fired state; the watchlist target starts armed.
    assert [tuple(row) for row in targets] == [(800.0, "USD", 0), (900.0, "EUR", 1)]
    assert notes == ["Watchlist note: wait for sale"]
    assert [tuple(row) for row in considering] == [(1,)]
    assert check_migrations(fresh_engine) == []


def test_a_failed_copy_keeps_the_old_table(fresh_engine):
    """The drop must not run when the copy before it failed."""
    import pytest
    from sqlalchemy import text

    from buylog.migrate import MigrationError

    models.init_db(fresh_engine)
    with fresh_engine.begin() as conn:
        # No threshold_value column, so the copy's SELECT fails.
        conn.execute(
            text(
                "CREATE TABLE price_alert (id INTEGER PRIMARY KEY, product_id INTEGER)"
            )
        )

    with pytest.raises(MigrationError, match="skipped"):
        run_migrations(fresh_engine)

    assert "price_alert" in inspect(fresh_engine).get_table_names()


def test_orphaned_alert_rows_do_not_block_the_fold(fresh_engine):
    from sqlalchemy import text

    models.init_db(fresh_engine)
    with fresh_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE price_alert (id INTEGER PRIMARY KEY, "
                "product_id INTEGER, threshold_value REAL, threshold_currency TEXT, "
                "active INTEGER, created_at DATETIME, triggered_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO price_alert (product_id, threshold_value) VALUES (42, 900)"
            )
        )

    run_migrations(fresh_engine)

    assert "price_alert" not in inspect(fresh_engine).get_table_names()


def test_migrations_derive_quote_status(fresh_engine):
    """A stored status becomes the order or list entry it is derived from."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    models.init_db(fresh_engine)
    with fresh_engine.begin() as conn:
        conn.execute(text("ALTER TABLE quote ADD COLUMN status VARCHAR"))
        conn.execute(text("CREATE INDEX ix_quote_status ON quote (status)"))
        conn.execute(text("INSERT INTO brand (id, name) VALUES (1, 'Apple')"))
        conn.execute(
            text("INSERT INTO product (id, name, brand_id) VALUES (1, 'iPhone', 1)")
        )
        conn.execute(
            text(
                "INSERT INTO vendor (id, name, currency, discount) "
                "VALUES (1, 'Amazon', 'USD', 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO quote (id, product_id, vendor_id, value, "
                "discount, shipping_cost, tax_rate, created_at, status) VALUES "
                # 90 after 10% off, + 10 shipping, + 10% tax on both: 110.0
                "(1, 1, 1, 100, 10, 10, 10, '2026-01-01', 'ordered'), "
                "(2, 1, 1, 200, 0, NULL, NULL, '2026-01-01', 'considering'), "
                # Stored 'ordered', but its only order was cancelled.
                "(3, 1, 1, 300, 0, NULL, NULL, '2026-01-01', 'ordered'), "
                "(4, 1, 1, 400, 0, NULL, NULL, '2026-01-01', 'received')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO purchase_order (quote_id, vendor_id, product_id, "
                "po_number, status, quantity, unit_price, currency, total_amount, "
                "grand_total, created_at) VALUES "
                "(3, 1, 1, 'quote-3', 'cancelled', 1, 300, 'USD', 300, 300, "
                "'2026-01-02')"
            )
        )

    run_migrations(fresh_engine)

    assert "status" not in {
        c["name"] for c in inspect(fresh_engine).get_columns("quote")
    }
    with Session(fresh_engine) as session:
        orders = session.execute(
            text(
                "SELECT quote_id, po_number, status, round(grand_total, 2) "
                "FROM purchase_order ORDER BY quote_id"
            )
        ).all()
        statuses = dict(
            session.execute(models.select(models.Quote.id, models.Quote.status))
            .tuples()
            .all()
        )
    assert [tuple(row) for row in orders] == [
        (1, "quote-1", "ordered", 110.0),
        (3, "quote-3", "cancelled", 300.0),
        (4, "quote-4", "received", 400.0),
    ]
    assert statuses == {1: "ordered", 2: "considering", 3: None, 4: "received"}
    assert check_migrations(fresh_engine) == []


def test_migrations_drop_quote_currency(fresh_engine):
    """The column goes; a value other than USD survives as a quote note."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    models.init_db(fresh_engine)
    with fresh_engine.begin() as conn:
        conn.execute(text("ALTER TABLE quote ADD COLUMN currency VARCHAR"))
        conn.execute(text("INSERT INTO brand (id, name) VALUES (1, 'Apple')"))
        conn.execute(
            text("INSERT INTO product (id, name, brand_id) VALUES (1, 'iPhone', 1)")
        )
        conn.execute(
            text(
                "INSERT INTO vendor (id, name, currency, discount) "
                "VALUES (1, 'Amazon', 'GBP', 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO quote (id, product_id, vendor_id, currency, value, "
                "discount, created_at) VALUES "
                "(1, 1, 1, 'USD', 100, 0, '2026-01-01'), "
                "(2, 1, 1, 'GBP', 200, 0, '2026-01-01')"
            )
        )

    run_migrations(fresh_engine)

    assert "currency" not in {
        c["name"] for c in inspect(fresh_engine).get_columns("quote")
    }
    with Session(fresh_engine) as session:
        notes = session.execute(
            text("SELECT entity_id, content FROM note WHERE entity_type = 'quote'")
        ).all()
    assert [tuple(row) for row in notes] == [(2, "Currency before migration: GBP")]
    assert check_migrations(fresh_engine) == []
