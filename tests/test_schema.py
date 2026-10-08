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
            models.Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.1)
        )
        session.commit()
        session.add(
            models.Forex(code="EUR", date=datetime.date(2026, 1, 1), usd_per_unit=1.2)
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
    assert "grand_total_usd" in {
        c["name"] for c in inspector.get_columns("purchase_order")
    }
    assert check_migrations(fresh_engine) == []
