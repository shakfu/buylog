#!/usr/bin/env python3
"""
Simple database migration for buylog.

Compares SQLAlchemy models to actual database schema and applies changes.
New tables and columns are added. Two removals copy data before dropping:

- ``vendor.website`` goes into ``vendor.url``, or a vendor note.
- ``price_alert`` and ``watchlist`` go into ``price_target``.
- ``quote.status`` goes into purchase orders and the Considering list.
- ``quote.currency``, always USD, is dropped; any other value becomes a note.

Columns in ``RENAMED_COLUMNS`` are renamed in place, so their data is kept.
"""

import logging
from typing import Any

from sqlalchemy import UniqueConstraint, inspect, text
from sqlalchemy.engine import Engine

from .models import Base

logger = logging.getLogger("buylog")


class MigrationError(Exception):
    """Raised when one or more migration statements could not be applied."""


# (table, old name, new name). The amounts were always relative to the base
# currency; the old names said USD.
RENAMED_COLUMNS = [
    ("forex", "usd_per_unit", "base_per_unit"),
    ("purchase_order", "grand_total_usd", "grand_total_base"),
]


def rename_statements(engine: Engine) -> list[str]:
    """Rename columns whose old name is still in the database."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    statements = []
    for table, old, new in RENAMED_COLUMNS:
        if table not in tables:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if old in columns and new not in columns:
            statements.append(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
    return statements


def _unique_constraints(table: Any) -> list[dict[str, Any]]:
    """
    Every uniqueness rule a model table declares.

    Covers both table-level ``UniqueConstraint`` and column-level
    ``unique=True``, which SQLAlchemy models express interchangeably.
    """
    constraints: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()

    for constraint in table.constraints:
        if not isinstance(constraint, UniqueConstraint) or not constraint.columns:
            continue
        names = [c.name for c in constraint.columns]
        key = tuple(sorted(names))
        if key in seen:
            continue
        seen.add(key)
        constraints.append(
            {
                "name": constraint.name or f"uq_{table.name}_{'_'.join(names)}",
                "columns": names,
            }
        )

    for column in table.columns:
        if not column.unique:
            continue
        key = (column.name,)
        if key in seen:
            continue
        seen.add(key)
        constraints.append(
            {"name": f"uq_{table.name}_{column.name}", "columns": [column.name]}
        )

    return constraints


def get_model_tables() -> dict[str, dict[str, Any]]:
    """Get table definitions from SQLAlchemy models."""
    tables = {}
    for table_name, table in Base.metadata.tables.items():
        columns = {}
        for col in table.columns:
            col_type = str(col.type)
            # Normalize types for SQLite
            if "VARCHAR" in col_type or "TEXT" in col_type:
                col_type = "TEXT"
            elif "INTEGER" in col_type:
                col_type = "INTEGER"
            elif "FLOAT" in col_type or "REAL" in col_type:
                col_type = "REAL"
            elif "DATE" in col_type and "DATETIME" not in col_type:
                col_type = "DATE"
            elif "DATETIME" in col_type:
                col_type = "DATETIME"
            elif "BOOLEAN" in col_type:
                col_type = "INTEGER"  # SQLite stores booleans as integers

            columns[col.name] = {
                "type": col_type,
                "nullable": col.nullable,
                "primary_key": col.primary_key,
                "foreign_key": [fk.target_fullname for fk in col.foreign_keys]
                if col.foreign_keys
                else None,
            }
        tables[table_name] = {
            "columns": columns,
            "primary_key": [c.name for c in table.primary_key.columns],
            "unique_constraints": _unique_constraints(table),
        }
    return tables


def get_db_tables(engine: Engine) -> dict[str, dict[str, Any]]:
    """Get table definitions from actual database."""
    inspector = inspect(engine)
    tables = {}

    for table_name in inspector.get_table_names():
        columns = {}
        for col in inspector.get_columns(table_name):
            col_type = str(col["type"])
            # Normalize
            if "VARCHAR" in col_type or "TEXT" in col_type:
                col_type = "TEXT"
            elif "INTEGER" in col_type:
                col_type = "INTEGER"
            elif "FLOAT" in col_type or "REAL" in col_type:
                col_type = "REAL"

            columns[col["name"]] = {
                "type": col_type,
                "nullable": col.get("nullable", True),
            }

        unique_column_sets: list[tuple[str, ...]] = []
        for uc in inspector.get_unique_constraints(table_name):
            names = [n for n in (uc.get("column_names") or []) if n]
            if names:
                unique_column_sets.append(tuple(sorted(names)))
        for index in inspector.get_indexes(table_name):
            if not index.get("unique"):
                continue
            names = [n for n in (index.get("column_names") or []) if n]
            if names:
                unique_column_sets.append(tuple(sorted(names)))
        tables[table_name] = {
            "columns": columns,
            "unique_column_sets": unique_column_sets,
        }

    return tables


def get_sqlite_type(model_type: str) -> str:
    """Convert model type to SQLite type."""
    model_type = model_type.upper()
    if "INT" in model_type:
        return "INTEGER"
    elif "FLOAT" in model_type or "REAL" in model_type:
        return "REAL"
    elif "DATE" in model_type and "TIME" not in model_type:
        return "DATE"
    elif "DATETIME" in model_type or "TIMESTAMP" in model_type:
        return "DATETIME"
    else:
        return "TEXT"


def _unique_index_statements(
    table_name: str, table_def: dict[str, Any], existing: set[tuple[str, ...]]
) -> list[str]:
    """
    DDL for uniqueness rules the database does not enforce yet.

    SQLite cannot add a table constraint after the fact, and this migrator's
    ``CREATE TABLE`` does not emit inline UNIQUE, so uniqueness is expressed as
    a unique index in both cases - it enforces exactly the same rule.
    """
    statements = []
    for constraint in table_def.get("unique_constraints", []):
        if tuple(sorted(constraint["columns"])) in existing:
            continue
        columns_sql = ", ".join(constraint["columns"])
        statements.append(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {constraint['name']} "
            f"ON {table_name} ({columns_sql})"
        )
    return statements


def generate_migrations(engine: Engine) -> list[str]:
    """Generate SQL statements needed to sync database with models."""
    model_tables = get_model_tables()
    db_tables = get_db_tables(engine)

    migrations = []

    for table_name, table_def in model_tables.items():
        if table_name not in db_tables:
            # Create new table
            primary_key = table_def.get("primary_key") or []
            # SQLite only accepts an inline PRIMARY KEY on a single column;
            # composite keys (the vendor_brand / purchase_list_quote join
            # tables) must be declared as a table-level constraint.
            inline_pk = len(primary_key) == 1

            cols = []
            for col_name, col_def in table_def["columns"].items():
                col_sql = f"{col_name} {get_sqlite_type(col_def['type'])}"
                if col_def.get("primary_key") and inline_pk:
                    col_sql += " PRIMARY KEY"
                if not col_def.get("nullable", True) and not col_def.get("primary_key"):
                    col_sql += " NOT NULL"
                if col_def.get("foreign_key"):
                    # Extract table.column from foreign key
                    fk = col_def["foreign_key"][0]
                    col_sql += f" REFERENCES {fk.replace('.', '(')}"
                    col_sql += ")"
                cols.append(col_sql)

            if primary_key and not inline_pk:
                cols.append(f"PRIMARY KEY ({', '.join(primary_key)})")

            create_sql = f"CREATE TABLE IF NOT EXISTS {table_name} (\n    "
            create_sql += ",\n    ".join(cols)
            create_sql += "\n)"
            migrations.append(create_sql)
            migrations.extend(
                _unique_index_statements(table_name, table_def, existing=set())
            )
        else:
            # Check for missing columns
            db_columns = db_tables[table_name]["columns"]
            renamed_to = {
                new
                for table, old, new in RENAMED_COLUMNS
                if table == table_name and old in db_columns
            }
            for col_name, col_def in table_def["columns"].items():
                if col_name not in db_columns and col_name not in renamed_to:
                    sqlite_type = get_sqlite_type(col_def["type"])
                    alter_sql = (
                        f"ALTER TABLE {table_name} ADD COLUMN {col_name} {sqlite_type}"
                    )
                    if col_def.get("foreign_key"):
                        fk = col_def["foreign_key"][0]
                        alter_sql += f" REFERENCES {fk.replace('.', '(')}"
                        alter_sql += ")"
                    migrations.append(alter_sql)

            migrations.extend(
                _unique_index_statements(
                    table_name,
                    table_def,
                    existing=set(db_tables[table_name].get("unique_column_sets", [])),
                )
            )

    return migrations


def vendor_website_statements(engine: Engine) -> list[str]:
    """
    Copy ``vendor.website`` into ``url`` and drop the column.

    ``url`` wins when both are set. A blank ``url`` takes the website value.
    A website that differs from ``url`` is kept as a vendor note.
    Databases created from the current models never had the column.
    """
    inspector = inspect(engine)
    if "vendor" not in inspector.get_table_names():
        return []
    columns = {column["name"] for column in inspector.get_columns("vendor")}
    if "website" not in columns:
        return []
    return [
        "UPDATE vendor SET url = website "
        "WHERE (url IS NULL OR trim(url) = '') "
        "AND website IS NOT NULL AND trim(website) != ''",
        "INSERT INTO note (entity_type, entity_id, content, created_at) "
        "SELECT 'vendor', id, 'Website before migration: ' || trim(website), "
        "CURRENT_TIMESTAMP FROM vendor "
        "WHERE website IS NOT NULL AND trim(website) != '' "
        "AND trim(website) != trim(url)",
        "ALTER TABLE vendor DROP COLUMN website",
    ]


def price_target_statements(engine: Engine) -> list[str]:
    """
    Copy ``price_alert`` and ``watchlist`` into ``price_target``, then drop them.

    A watchlist row with notes keeps them as a product note. A row with no
    target was only a flag that the product might be bought, so its quotes
    go on the Considering purchase list. Copied watchlist targets start
    armed: they used to fire on every check, so none has been "used up".
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    statements: list[str] = []
    # Rows for a deleted product would fail the foreign key; they are dropped.
    live = "product_id IN (SELECT id FROM product)"

    if "price_alert" in tables:
        columns = {c["name"] for c in inspector.get_columns("price_alert")}
        currency = (
            "upper(coalesce(threshold_currency, 'USD'))"
            if "threshold_currency" in columns
            else "'USD'"
        )
        statements += [
            "INSERT INTO price_target "
            "(product_id, threshold, currency, active, created_at, triggered_at) "
            f"SELECT product_id, threshold_value, {currency}, coalesce(active, 1), "
            "coalesce(created_at, CURRENT_TIMESTAMP), triggered_at FROM price_alert "
            f"WHERE threshold_value IS NOT NULL AND {live}",
            "DROP TABLE price_alert",
        ]

    if "watchlist" in tables:
        columns = {c["name"] for c in inspector.get_columns("watchlist")}
        # target_currency arrived after 0.2.0; a missing value meant USD.
        currency = (
            "upper(coalesce(target_currency, 'USD'))"
            if "target_currency" in columns
            else "'USD'"
        )
        flagged = (
            "SELECT q.id FROM quote q JOIN watchlist w ON w.product_id = q.product_id "
            "WHERE w.target_price IS NULL AND coalesce(w.active, 1) = 1"
        )
        statements += [
            "INSERT INTO price_target "
            "(product_id, threshold, currency, active, created_at) "
            f"SELECT product_id, target_price, {currency}, coalesce(active, 1), "
            "coalesce(created_at, CURRENT_TIMESTAMP) FROM watchlist "
            f"WHERE target_price IS NOT NULL AND {live}",
            "INSERT INTO note (entity_type, entity_id, content, created_at) "
            "SELECT 'product', product_id, 'Watchlist note: ' || trim(notes), "
            "coalesce(created_at, CURRENT_TIMESTAMP) FROM watchlist "
            f"WHERE notes IS NOT NULL AND trim(notes) != '' AND {live}",
            *_considering_statements(flagged),
            "DROP TABLE watchlist",
        ]

    return statements


def _considering_statements(quote_ids: str) -> list[str]:
    """Put the quotes that ``quote_ids`` (a SELECT of ids) names on Considering."""
    return [
        "INSERT INTO purchase_list (name, description, created_at) "
        "SELECT 'Considering', 'Quotes you might buy', CURRENT_TIMESTAMP "
        f"WHERE EXISTS ({quote_ids}) "
        "AND NOT EXISTS (SELECT 1 FROM purchase_list WHERE name = 'Considering')",
        "INSERT OR IGNORE INTO purchase_list_quote (purchase_list_id, quote_id) "
        "SELECT pl.id, f.id FROM purchase_list pl "
        f"JOIN ({quote_ids}) f WHERE pl.name = 'Considering'",
    ]


def quote_status_statements(engine: Engine) -> list[str]:
    """
    Turn a stored ``quote.status`` into the records it is derived from, then drop it.

    ``ordered``/``received`` with no purchase order at all gets one, priced
    the way ``PurchaseOrderService.ensure_for_quote`` prices it. A quote whose
    only orders are cancelled gets none: the cancel was the later decision.
    ``considering`` on no list goes on the Considering list.
    """
    inspector = inspect(engine)
    if "quote" not in inspector.get_table_names():
        return []
    if "status" not in {c["name"] for c in inspector.get_columns("quote")}:
        return []

    unit = "q.value * (1 - coalesce(q.discount, 0) / 100.0)"
    shipping = "coalesce(q.shipping_cost, 0)"
    tax = (
        f"CASE WHEN coalesce(q.tax_rate, 0) > 0 "
        f"THEN ({unit} + {shipping}) * q.tax_rate / 100.0 END"
    )
    total = f"{unit} + {shipping} + coalesce({tax}, 0)"
    po_number = (
        "CASE WHEN EXISTS (SELECT 1 FROM purchase_order x "
        "WHERE x.po_number = 'quote-' || q.id) "
        "THEN 'quote-' || q.id || '-migrated' ELSE 'quote-' || q.id END"
    )
    unlisted = (
        "SELECT q.id FROM quote q WHERE q.status = 'considering' "
        "AND NOT EXISTS (SELECT 1 FROM purchase_list_quote x WHERE x.quote_id = q.id)"
    )
    return [
        "INSERT INTO purchase_order (quote_id, vendor_id, product_id, po_number, "
        "status, quantity, unit_price, currency, total_amount, shipping_cost, tax, "
        "grand_total, grand_total_base, notes, created_at) "
        f"SELECT q.id, q.vendor_id, q.product_id, {po_number}, q.status, 1, "
        f"{unit}, 'USD', {unit}, q.shipping_cost, {tax}, {total}, {total}, "
        "'Recorded by migration from the quote status', "
        "coalesce(q.created_at, CURRENT_TIMESTAMP) FROM quote q "
        "WHERE q.status IN ('ordered', 'received') "
        "AND NOT EXISTS (SELECT 1 FROM purchase_order x WHERE x.quote_id = q.id)",
        *_considering_statements(unlisted),
        *_drop_column_statements(inspector, "quote", "status"),
    ]


def quote_currency_statements(engine: Engine) -> list[str]:
    """
    Drop ``quote.currency``. ``quote.value`` is USD; the entered currency is
    ``original_currency``. Every service path wrote USD. A row that says
    otherwise came from ``Vendor.add_product``, and its value is kept as a
    quote note before the column goes.
    """
    inspector = inspect(engine)
    if "quote" not in inspector.get_table_names():
        return []
    if "currency" not in {c["name"] for c in inspector.get_columns("quote")}:
        return []
    return [
        "INSERT INTO note (entity_type, entity_id, content, created_at) "
        "SELECT 'quote', id, 'Currency before migration: ' || currency, "
        "CURRENT_TIMESTAMP FROM quote "
        "WHERE currency IS NOT NULL AND upper(currency) != 'USD'",
        *_drop_column_statements(inspector, "quote", "currency"),
    ]


def _drop_column_statements(inspector: Any, table: str, column: str) -> list[str]:
    """Drop ``column``'s single-column indexes, then the column itself.

    SQLite refuses ``DROP COLUMN`` on an indexed column.
    """
    indexes = [
        index["name"]
        for index in inspector.get_indexes(table)
        if index["column_names"] == [column] and index["name"]
    ]
    return [
        *(f"DROP INDEX {name}" for name in indexes),
        f"ALTER TABLE {table} DROP COLUMN {column}",
    ]


def _is_destructive(sql: str) -> bool:
    upper = sql.upper()
    return upper.lstrip().startswith("DROP ") or " DROP COLUMN " in upper


def pending_migrations(engine: Engine) -> list[str]:
    """Schema changes the database still needs, including the data folds."""
    return (
        rename_statements(engine)
        + generate_migrations(engine)
        + vendor_website_statements(engine)
        + price_target_statements(engine)
        + quote_status_statements(engine)
        + quote_currency_statements(engine)
    )


def run_migrations(engine: Engine, dry_run: bool = False) -> list[str]:
    """
    Run database migrations.

    Args:
        engine: SQLAlchemy engine
        dry_run: If True, only return SQL without executing

    Returns:
        List of SQL statements that were executed (or, for a dry run, would be)

    Raises:
        MigrationError: If any statement failed to apply
    """
    migrations = pending_migrations(engine)

    if not migrations:
        logger.info("Database schema is up to date")
        return []

    if dry_run:
        return migrations

    applied: list[str] = []
    failures: list[tuple[str, Exception]] = []

    with engine.connect() as conn:
        for sql in migrations:
            # A drop after a failed copy would delete the only copy of the data.
            if failures and _is_destructive(sql):
                logger.error(f"Skipped after an earlier failure: {sql[:80]}")
                failures.append(
                    (sql, MigrationError("skipped because an earlier statement failed"))
                )
                continue
            logger.info(f"Executing: {sql[:80]}...")
            try:
                conn.execute(text(sql))
            except Exception as e:
                # Some statements may fail if already applied (e.g., duplicate
                # column). Keep going so one bad statement does not block the
                # rest, but record it - reporting success for a schema that was
                # not actually created leaves the database silently broken.
                logger.error(f"Migration failed: {sql[:80]}... ({e})")
                failures.append((sql, e))
            else:
                applied.append(sql)
        conn.commit()

    logger.info(f"Applied {len(applied)} of {len(migrations)} migration(s)")

    if failures:
        summary = "; ".join(f"{sql.splitlines()[0]}: {e}" for sql, e in failures)
        raise MigrationError(
            f"{len(failures)} of {len(migrations)} migration(s) failed: {summary}"
        )

    return applied


def check_migrations(engine: Engine) -> list[str]:
    """Check what migrations are needed without applying them."""
    return pending_migrations(engine)
