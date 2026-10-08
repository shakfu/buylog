#!/usr/bin/env python3
"""
Simple database migration for buylog.

Compares SQLAlchemy models to actual database schema and applies changes.
New tables and columns are added. The one removal is ``vendor.website``,
which is copied into ``vendor.url`` (or a vendor note) and then dropped.
"""

import logging
from typing import Any

from sqlalchemy import UniqueConstraint, inspect, text
from sqlalchemy.engine import Engine

from .models import Base

logger = logging.getLogger("buylog")


class MigrationError(Exception):
    """Raised when one or more migration statements could not be applied."""


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
            for col_name, col_def in table_def["columns"].items():
                if col_name not in db_columns:
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


def pending_migrations(engine: Engine) -> list[str]:
    """Schema changes the database still needs, including the website fold."""
    return generate_migrations(engine) + vendor_website_statements(engine)


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
